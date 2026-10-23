"""Utility class for common MongoDB operations."""

from collections.abc import Mapping
import logging
from typing import Any

from pymongo.collection import Collection, ReturnDocument

from pro_tes.ga4gh.tes.models import DbDocument, TesState
from pro_tes.utils.misc import has_credentials, strip_auth

logger = logging.getLogger(__name__)

# Fields of stored tasks with lists of inputs or outputs, which have URLs
FILE_FIELDS = (
    "task.inputs",
    "task.outputs",
    "task_original.inputs",
    "task_original.outputs",
)
# Field of stored tasks with the outputs logged by backends (in each log)
LOG_OUTPUTS_FIELD = "task.logs.outputs"
# Name of the index of the tasks of earlier versions (`task_id`, `worker_id`)
LEGACY_TASK_INDEX = "task_id_1_worker_id_1"


def _strip_urls(items: Any) -> bool:
    """Remove credentials from the URLs of stored inputs or outputs.

    Args:
        items: Stored inputs or outputs; changed in place.

    Returns:
        Whether a URL had credentials.
    """
    changed = False
    for item in items if isinstance(items, list) else []:
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str):
            continue
        try:
            found = has_credentials(url)
        except ValueError:
            # malformed, and not forwarded to a backend
            continue
        if found:
            item["url"] = strip_auth(url)
            changed = True
    return changed


def remove_stored_credentials(collection: Collection[dict[str, Any]]) -> None:
    """Remove credentials of callers from tasks stored by earlier versions.

    That is, their HTTP Basic credentials (`basic_auth`), and credentials
    in the URLs of inputs and outputs (also of the tasks as received, and
    of the outputs in their logs). Run on startup; idempotent.

    Args:
        collection: Database collection of the tasks.
    """
    result = collection.update_many(
        {"basic_auth": {"$exists": True}}, {"$unset": {"basic_auth": ""}}
    )
    if result.modified_count:
        logger.info(
            "Removed HTTP Basic credentials from %d stored tasks",
            result.modified_count,
        )
    # URLs with user information (`scheme://user:password@host...`)
    pattern = {"$regex": "^[^/?#]*//[^/?#]*@"}
    fields = [*FILE_FIELDS, LOG_OUTPUTS_FIELD]
    query = {"$or": [{f"{field}.url": pattern} for field in fields]}
    stripped = 0
    for document in collection.find(
        query, projection=["_id", *FILE_FIELDS, "task.logs"]
    ):
        update: dict[str, Any] = {}
        for field in FILE_FIELDS:
            root, name = field.split(".")
            items = (document.get(root) or {}).get(name)
            if _strip_urls(items):
                update[field] = items
        # outputs that backends logged, e.g., Funnel, which earlier versions
        # forwarded URLs with credentials to
        logs = (document.get("task") or {}).get("logs")
        for log in logs if isinstance(logs, list) else []:
            if isinstance(log, dict) and _strip_urls(log.get("outputs")):
                update["task.logs"] = logs
        if update:
            collection.update_one({"_id": document["_id"]}, {"$set": update})
            stripped += 1
    if stripped:
        logger.info("Removed credentials from the URLs of %d stored tasks", stripped)


def find_duplicate_task_ids(
    collection: Collection[dict[str, Any]],
) -> dict[str | None, int]:
    """Find identifiers that stored tasks share.

    Such tasks prevent the unique index on task identifiers (`task.id`, cf.
    `db` in `config.yaml`); earlier versions didn't have it.

    Args:
        collection: Database collection of the tasks.

    Returns:
        Number of tasks per shared identifier, in the order of the
        identifiers; `None` for tasks without identifier (more than one
        prevents the index, too).
    """
    pipeline: list[dict[str, Any]] = [
        {"$group": {"_id": "$task.id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
    ]
    duplicates = {
        group["_id"]: group["count"] for group in collection.aggregate(pipeline)
    }
    return dict(sorted(duplicates.items(), key=lambda item: str(item[0])))


def drop_legacy_task_index(collection: Collection[dict[str, Any]]) -> None:
    """Drop the index of earlier versions on `task_id` and `worker_id`.

    The fields of the index didn't identify tasks (task identifiers are
    stored as `task.id`), so it never rejected a duplicate identifier; it is
    replaced by a unique index on `task.id` and an index on `worker_id`
    (cf. `db` in `config.yaml`). Run on startup; idempotent.

    Args:
        collection: Database collection of the tasks.
    """
    if LEGACY_TASK_INDEX in collection.index_information():
        collection.drop_index(LEGACY_TASK_INDEX)
        logger.info(
            "Dropped index '%s' of collection '%s' of earlier versions",
            LEGACY_TASK_INDEX,
            collection.name,
        )


class DbDocumentConnector:
    """MongoDB connector to a given proTES database document.

    Args:
        collection: Database collection.
        worker_id: Celery task identifier.

    Attributes:
        collection: Database collection.
        worker_id: Celery task identifier.
    """

    def __init__(
        self,
        collection: Collection[dict[str, Any]],
        worker_id: str,
    ) -> None:
        """Construct object instance."""
        self.collection: Collection[dict[str, Any]] = collection
        self.worker_id: str = worker_id

    def get_document(
        self,
        projection: Mapping[str, Any] | None = None,
    ) -> DbDocument:
        """Get document associated with task.

        Args:
            projection: A projection object indicating which fields of the
                document to return. By default, all fields except the MongoDB
                identifier `_id` are returned.

        Returns:
            Instance of `pro_wes.ga4gh.wes.models.DbDocument` associated with
                the task.

        Raise:
            ValueError: Returned document does not conform to schema.
        """
        if projection is None:
            projection = {"_id": False}
        document_unvalidated = self.collection.find_one(
            filter={"worker_id": self.worker_id},
            projection=projection,
        )
        try:
            document: DbDocument = DbDocument(**(document_unvalidated or {}))
        except Exception as exc:
            raise ValueError(
                f"Database document does not conform to schema: {document_unvalidated}"
            ) from exc
        return document

    def update_task_state(
        self,
        state: str = "UNKNOWN",
    ) -> None:
        """Update task status.

        Args:
            state: New task status; one of `pro_wes.ga4gh.wes.models.State`.

        Raises:
            ValueError: Invalid state passed.
        """
        try:
            TesState(state)
        except Exception as exc:
            raise ValueError(f"Unknown state: {state}") from exc
        self.collection.find_one_and_update(
            {"worker_id": self.worker_id},
            {"$set": {"task.state": state}},
        )
        logger.info("[%s] %s", self.worker_id, state)

    def upsert_fields_in_root_object(
        self,
        root: str,
        projection: Mapping[str, Any] | None = None,
        **kwargs: object,
    ) -> DbDocument:
        """Insert or update fields in(to) the same root (object) field.

        Args:
            root: Root field name.
            projection: A projection object indicating which fields of the
                document to return. By default, all fields except the MongoDB
                identifier `_id` are returned.
            **kwargs: Key-value pairs of fields to insert/update.

        Returns:
            Inserted/updated document, or `None` if database operation failed.
        """
        if projection is None:
            projection = {"_id": False}
        document_unvalidated = self.collection.find_one_and_update(
            {"worker_id": self.worker_id},
            {"$set": {".".join([root, key]): value for (key, value) in kwargs.items()}},
            projection=projection,
            return_document=ReturnDocument.AFTER,
        )
        if document_unvalidated is None:
            raise ValueError(f"No database document with worker ID '{self.worker_id}'")
        try:
            document: DbDocument = DbDocument(**document_unvalidated)
        except Exception as exc:
            raise ValueError(
                f"Database document does not conform to schema: {document_unvalidated}"
            ) from exc
        return document
