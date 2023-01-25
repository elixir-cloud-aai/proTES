"""Model for the Access Uri Combination."""
# pylint: disable=no-name-in-module
from typing import List
from pydantic import BaseModel, AnyUrl, HttpUrl

# pragma pylint: disable=too-few-public-methods


class TesStats(BaseModel):
    """Combination of Tes stats, currently total distance."""

    total_distance: float = None


class TaskParams(BaseModel):
    """Combination of task parameters, currently input uris."""

    input_uris: List[AnyUrl]


class TesDeployment(BaseModel):
    """Combination of the tes_uri and its stats"""

    tes_uri: HttpUrl
    stats: TesStats


class AccessUriCombination(BaseModel):
    """Combination of input_uri of the TES task and the TES instances."""

    task_params: TaskParams
    tes_deployments: List[TesDeployment]
