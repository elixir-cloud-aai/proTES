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
    in the URLs of inputs and outputs (also of the tasks as received). Run on
    startup; idempotent.

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
    query = {"$or": [{f"{field}.url": pattern} for field in FILE_FIELDS]}
    stripped = 0
    for document in collection.find(query, projection=["_id", *FILE_FIELDS]):
        update: dict[str, Any] = {}
        for field in FILE_FIELDS:
            root, name = field.split(".")
            items = (document.get(root) or {}).get(name)
            if _strip_urls(items):
                update[field] = items
        if update:
            collection.update_one({"_id": document["_id"]}, {"$set": update})
            stripped += 1
    if stripped:
        logger.info("Removed credentials from the URLs of %d stored tasks", stripped)


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
