"""Module for task distribution logic."""

from copy import deepcopy
import random
from typing import List, Optional

from flask import current_app
import requests


def task_distribution() -> Optional[str]:
    """Random task distributor.

    Randomly distribute tasks across available TES instances.

    Returns:
        A randomly selected, available TES instance.
    """
    foca_conf = current_app.config.foca
    tes_uri: List[str] = deepcopy(foca_conf.tes["service_list"])
    timeout: int = foca_conf.controllers["post_task"]["timeout"]["poll"]
    while len(tes_uri) != 0:
        random_tes_uri: str = random.choice(tes_uri)
        response = requests.get(url=random_tes_uri, timeout=timeout)
        if response.status_code == 200:
            return random_tes_uri
        tes_uri.remove(random_tes_uri)
    return None
