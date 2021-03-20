"""Utility functions for GET /v1/tasks/service-info endpoint."""

from copy import deepcopy
import logging
from typing import (Any, Mapping)

import foca.database.db_utils as db_utils


# Get logger instance
logger = logging.getLogger(__name__)


# Helper function GET /service-info
def get_service_info(
    config: Mapping,
    silent: bool = False,
    *args: Any,
    **kwarg: Any
):
    """Returns readily formatted service info or `None` (in silent mode);
    creates service info database document if it does not exist."""
    collection_service_info = config['database']['collections']['service_info']
    service_info = deepcopy(config['service_info'])

    # Write current service info to database if absent or different from latest
    if not service_info == db_utils.find_one_latest(collection_service_info):
        collection_service_info.insert(service_info)
        logger.info('Updated service info: {service_info}'.format(
            service_info=service_info,
        ))
    else:
        logger.debug('No change in service info. Not updated.')

    # Return None when called in silent mode:
    if silent:
        return None

    return service_info