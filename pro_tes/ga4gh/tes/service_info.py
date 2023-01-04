"""Controller for the `/service-info route."""

import logging
from typing import Dict

from bson.objectid import ObjectId
from flask import current_app
from pymongo.collection import Collection

from pro_tes.exceptions import NotFound

logger = logging.getLogger(__name__)


class ServiceInfo:
    """Class for service info server-side controller methods.

    Creates service info upon first request, if it does not exist.

    Attributes:
        db_client: Database collection storing service info objects.
        object_id: Database identifier for service info.
    """

    def __init__(self) -> None:
        """Construct class instance."""
        self.db_client: Collection = (
            current_app.config.foca.db.dbs["taskStore"]
            .collections["service_info"]
            .client
        )
        self.object_id: str = "000000000000000000000000"

    def get_service_info(self) -> Dict:
        """Get latest service info from database.

        Returns:
            Latest service info details.

        Raises:
            NotFound: Service info was not found.
        """
        service_info = self.db_client.find_one(
            {"_id": ObjectId(self.object_id)},
            {"_id": False},
        )
        if service_info is None:
            raise NotFound
        return service_info

    def set_service_info(self, data: Dict) -> None:
        """Create or update service info.

        Arguments:
            data: Dictionary of service info values. Cf.
        """
        self.db_client.replace_one(
            filter={"_id": ObjectId(self.object_id)},
            replacement=data,
            upsert=True,
        )
        logger.info(f"Service info set: {data}")

    def init_service_info_from_config(self) -> None:
        """Initialize service info from config.

        Set service info only if it does not yet exist.
        """
        service_info_conf = current_app.config.foca.serviceInfo
        try:
            service_info_db = self.get_service_info()
        except NotFound:
            logger.info("Initializing service info.")
            self.set_service_info(data=service_info_conf)
            return
        if service_info_db != service_info_conf:
            logger.info(
                "Service info configuration changed. Updating service info."
            )
            self.set_service_info(data=service_info_conf)
            return
        logger.debug("Service info already initialized and up to date.")
