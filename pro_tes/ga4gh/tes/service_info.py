"""Controllers for the `/service-info route."""

import logging
from typing import Dict

from bson.objectid import ObjectId
from foca.models.config import Config
from flask import current_app
from pymongo.collection import Collection
from pro_tes.exceptions import (
    NotFound,
)

logger = logging.getLogger(__name__)


class ServiceInfo:

    def __init__(self) -> None:

        """Class for TES API service info server-side controller methods.

        Creates service info upon first request, if it does not exist.

        Attributes:
            config: App configuration.
            foca_config: FOCA configuration.
            db_client_service_info: Database collection storing service info
                objects.
            db_client_tasks: Database collection storing workflow run objects.
            object_id: Database identifier for service info.
        """
        self.config: Dict = current_app.config
        self.foca_config: Config = self.config.foca
        self.db_client_service_info: Collection = (
            self.foca_config.db.dbs['taskStore']
            .collections['service_info'].client
        )
        self.object_id: str = "000000000000000000000000"
        self.service_info = self.foca_config.serviceInfo

    def get_service_info(
        self,
        **kwrags
    ) -> Dict:
        # updating service info in database
        self.db_client_service_info.replace_one(
            filter={'_id': ObjectId(self.object_id)},
            replacement=self.service_info,
            upsert=True,
        )
        ServiceInfo = self.db_client_service_info.find_one(
            {'_id': ObjectId(self.object_id)},
            {'_id': False},
        )
        if ServiceInfo is None:
            raise NotFound
        return ServiceInfo
