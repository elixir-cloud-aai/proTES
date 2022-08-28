import random
import requests
from typing import List, Any

from foca.models.config import Config
from flask import (
    current_app,
)


def task_distribution() -> List[Any]:
    """Adds a simple task distribution logic over the list of given
    TES instance.
    Returns : The best TES instance on which TES task can be submitted
    """
    tes_uri = []

    # update tes_uri list with given TES instance
    foca_config: Config = current_app.config.foca
    for tes_endpoint in foca_config.tes['service_list']:
        tes_uri.append(tes_endpoint)

    while len(tes_uri) != 0:
        # pick random TES instance from the provided list
        random_tes_uri = random.choice(tes_uri)

        # check if TES instance is online
        response = requests.get(url=random_tes_uri)
        if response.status_code == 200:
            tes_uri.clear()
            tes_uri.insert(0, random_tes_uri)
            return tes_uri
        # if not online delete the current TES instance from the \
        # list and try other instances
        else:
            tes_uri.remove(random_tes_uri)
