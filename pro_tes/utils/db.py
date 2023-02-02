"""Utility class for common MongoDB operations."""

import logging
from typing import Mapping, Optional
from pymongo.collection import ReturnDocument
from pymongo import collection as Collection

from pro_tes.ga4gh.tes.models import DbDocument, TesState

logger = logging.getLogger(__name__)


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
        collection: Collection,
        worker_id: str,
    ) -> None:
        """Construct object instance."""
        self.collection: Collection = collection
        self.worker_id: str = worker_id

    def get_document(
        self,
        projection: Optional[Mapping] = None,
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
            document: DbDocument = DbDocument(**document_unvalidated)
        except Exception as exc:
            raise ValueError(
                "Database document does not conform to schema: "
                f"{document_unvalidated}"
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
            {"$set": {"task_incoming.state": state}},
        )
        logger.info(f"[{self.worker_id}] {state}")

    def upsert_fields_in_root_object(
        self,
        root: str,
        projection: Optional[Mapping] = None,
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
            {
                "$set": {
                    ".".join([root, key]): value
                    for (key, value) in kwargs.items()
                }
            },
            projection=projection,
            return_document=ReturnDocument.AFTER,
        )
        try:
            document: DbDocument = DbDocument(**document_unvalidated)
        except Exception as exc:
            raise ValueError(
                "Database document does not conform to schema: "
                f"{document_unvalidated}"
            ) from exc
        return document
