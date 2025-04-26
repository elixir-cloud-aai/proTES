"""Custom app config models."""

from typing import Optional
from pathlib import Path

from pydantic import BaseModel  # pylint: disable=no-name-in-module

from pro_wes.ga4gh.wes.models import ServiceInfoBase as ServiceInfo

# pragma pylint: disable=too-few-public-methods


class Controllers(BaseModel):
  """Controller configurations.

    Args:
        post_task: Settings for POST /task.
        list_tasks: Settings for GET /tasks.
        celery: Celery background task settings.

    Attributes:
        post_task: Settings for POST /task.
        list_tasks: Settings for GET /tasks.
        celery: Celery background task settings.
    """   
    post_task:
      db:
      insert_attempts: 10
    task_id:
      charset: string.ascii_uppercase + string.digits
      length: 6
        timeout:
          post: null
          poll: 2
          job: null
        polling:
          wait: 3
          attempts: 100
    list_tasks:
      default_page_size: 5
    celery:
      monitor:
      timeout: 0.1
      message_maxsize: 16777216

class Tes(BaseModel):
  """TES backend configuration.

    Args:
        service_list: List of available TES services.

    Attributes:
        service_list: List of available TES services.
    """

service_list:
    - "https://csc-tesk-noauth.rahtiapp.fi"
    - "https://funnel.cloud.e-infra.cz/"
    - "https://tesk-eu.hypatia-comp.athenarc.gr"
    - "https://tesk-na.cloud.e-infra.cz"
    - "https://vm4816.kaj.pouta.csc.fi/"

        

class StoreLogs(BaseModel):
   """Logging configuration.

    Args:
        execution_trace: Whether to store execution trace logs.

    Attributes:
        execution_trace: Whether to store execution trace logs.
    """
      execution_trace: True

class Middlewares(BaseModel):
      - - "pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance"
        - "pro_tes.plugins.middlewares.task_distribution.random.TaskDistributionRandom"


class CustomConfig(BaseModel):
  """Custom app configuration.

    Args:
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.
        service_info: Metadata about the service.

    Attributes:
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.
        service_info: Metadata about the service.
    """
    controllers: Controllers = Controllers()
    tes: Tes = Tes()
    storeLogs: StoreLogs = StoreLogs()
    middlewares: Middlewares = Middlewares()
    service_info: ServiceInfo
 