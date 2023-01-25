"""Module for random task distribution."""
import random
from copy import deepcopy
from typing import List, Optional

import requests
from flask import current_app


def random_task_distribution() -> Optional[List]:
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
            tes_uri.clear()
            tes_uri.insert(0, random_tes_uri)
            return tes_uri
        tes_uri.remove(random_tes_uri)
    return None
