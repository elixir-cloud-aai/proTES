import random
import requests
from typing import Dict, List, Any

from foca.models.config import Config
from flask import (
    current_app,
)


def task_distribution() -> List[Any]:
    tes_uri = []
    foca_config: Config = current_app.config.foca
    for tes_endpoint in foca_config.tes['service_list']:
        tes_uri.append(tes_endpoint)
    while len(tes_uri) != 0:
        # pick random tes_uri from the provided list
        random_tes_uri = random.choice(tes_uri)
        # check if it is online
        response = requests.get(url=random_tes_uri)
        if response.status_code == 200:
            tes_uri.clear()
            tes_uri.insert(0, random_tes_uri)
            return tes_uri
        # if not online delete the current tes_uri from the \
        # list and try other uri's
        else:
            tes_uri.remove(random_tes_uri)
