"""Module for random task distribution."""

import random
from copy import deepcopy
from typing import List

from flask import current_app


def task_distribution() -> List[str]:
    """Randomize list of TES instances.

    Returns:
        Randomly shuffled list of TES instances.
    """
    foca_conf = current_app.config.foca
    tes_uri: List[str] = deepcopy(foca_conf.tes["service_list"])
    random.shuffle(tes_uri)
    return tes_uri
