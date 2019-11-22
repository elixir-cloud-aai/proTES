"""Middleware to be injected into TES requests."""
import logging
from json import dumps
import requests
from typing import (Dict, Iterable, List, Mapping)
from urllib.parse import urljoin

# Get logger instance
logger = logging.getLogger(__name__)


class TEStribute:
    """Calls external TEStribute task distribution logic service."""
    def __init__ (
        self,
        task: Mapping,
        config: Mapping,
        tes_uris: Iterable,
    )-> None:
        self.api_root = config['api_root']

        # Fall back to default execution time if not provided
        if not 'exeuction_time_sec' in task['resources']:
            task['resources']['execution_time_sec'] = \
                config['default_execution_time_sec']
        
        # Create JSON payload for POST request
        self.json_payload = dumps({
            'drs_uris': config['drs_uris'],
            'mode': config['mode'],
            'object_ids': [i['name'] for i in task['inputs']],
            'resource_requirements': task['resources'],
            'tes_uris': tes_uris,
        })


    def rank_services(
        self,
        endpoint: str = '/rank_services'
    ) -> Dict:
        """Send POST request to TEStribute '/rank_services' endpoints."""
        # TODO: Improve error handling
        try:
            return requests.post(
                urljoin(self.api_root, endpoint),
                json=self.json_payload
            )
        except Exception:
            raise
