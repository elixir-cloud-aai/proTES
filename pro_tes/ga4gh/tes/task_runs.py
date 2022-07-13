import logging
from typing import (
    Dict,
)

from bson.objectid import ObjectId
from celery import uuid
from foca.models.config import Config
from foca.utils.misc import generate_id
from flask import (
    current_app,
    request,
)
from requests import HTTPError
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from werkzeug.exceptions import BadRequest

import tes
from pro_tes.exceptions import (
    TaskNotFound,
)
from pro_tes.ga4gh.tes.models import (
    DbDocument,
    TesEndpoint,
    TesState
)
from pro_tes.utils.db_utils import DbDocumentConnector

from datetime import datetime
from dateutil.parser import parse as parse_time


logger = logging.getLogger(__name__)


class TaskRuns:

    def __init__(self) -> None:
        """Class for TES API server-side controller methods.

        Attributes:
            config: App configuration.
            foca_config: FOCA configuration.
            db_client: Database collection storing task objects.
            document: Document to be inserted into the collection. Note that
                this is iteratively built up.
        """
        self.config: Dict = current_app.config
        self.foca_config: Config = current_app.config['FOCA']
        self.db_client: Collection = (
            self.foca_config.db.dbs['taskStore'].collections['tasks'].client
        )

    def create_task(
            self,
            **kwargs
    ) -> Dict:
        """Start task.

               Args:
                   **kwargs: Additional keyword arguments passed along with
                             request.
               Returns:
                   task identifier.
               """

        # storing request as payload
        payload = request.json

        # Initialize database document
        document: DbDocument = DbDocument()

        # storing data of payload into payloads so that it can be used to\
        # sanitize request to be passed to py-tes client
        payloads = dict.copy(payload)

        # store payload in Tes task model
        document_stored = self._attach_request(
            payload=payload,
            document=document
            )

        # get and attach suitable Tes endpoint
        document.tes_endpoint = TesEndpoint(
            host="https://csc-tesk-noauth.rahtiapp.fi",
        )

        url = (
            f"{document_stored.tes_endpoint.host.rstrip('/')}/"
            f"{document_stored.tes_endpoint.base_path.strip('/')}"
        )

        # get and attach task owner
        document.user_id = kwargs.get('user_id', None)

        # create run environment & insert task document into task collection
        document_stored = self._create_run_environment(
            document=document_stored
            )

        # instantiate database connector
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            worker_id=document_stored.worker_id,
        )

        logger.info(
            f"Sending task '{document_stored.task_log['id']}' with "
            f"task identifier '{document_stored.worker_id}' to TES endpoint "
            f"hosted at: {url}"
        )

        # Converting payload according to the tes-client model
        payloads = self._sanitize_request(payloads=payloads)

        try:
            task = tes.Task(**payloads)
            cli = tes.HTTPClient(url, timeout=5)
            task_id = cli.create_task(task)
            res = cli.get_task(task_id)
            document_stored.task_log['id'] = res.id

            # storing the document in database
            document_stored: DbDocument = (
                db_connector.upsert_fields_in_root_object(
                    root='tes_endpoint',
                    task_id=res.id,
                )
            )
            document_stored: DbDocument = (
                db_connector.upsert_fields_in_root_object(
                    root='task_log',
                    id=res.id,
                )
            )
        except Exception as e:
            db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
            logger.error(
                (   # noqa: F524
                    "Task '{document_stored.task_log['id']}' could not be \
                            sent to any TES instance."
                    "Task state was set to 'SYSTEM_ERROR'. Original error "
                    "message: '{type}: {msg}'"
                ).format(
                    type=type(e).__name__,
                    msg='.'.join(e.args),
                )
            )

        # Todo : Properly track task progress in background

        # track task progress in background
        # self._track_task_progress(
        #     worker_id= document_stored.worker_id,
        #     remote_host= document_stored.tes_endpoint['host'],
        #     remote_base_path= document_stored.tes_endpoint['base_path'],
        #     remote_task_id= document_stored.tes_endpoint['task_id']
        #
        # )

        return {'id': task_id}

    def list_tasks(
            self,
            **kwargs
    ) -> Dict:
        """Return list of tasks.

               Args:
                   **kwargs: Keyword arguments passed along with request.

               Returns:
                   Response object according to TES API schema . Cf.
                       https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml
               """
        if 'page_size' in kwargs:
            page_size = kwargs['page_size']
        else:
            page_size = (
                self.foca_config.controllers['list_task']['default_page_size']
            )
        # extract/set page token
        if 'page_token' in kwargs:
            page_token = kwargs['page_token']
        else:
            page_token = ''

        # initialize filter dictionary
        filter_dict = {}

        # add filter for user-owned tasks if user ID is available
        if 'user_id' in kwargs:
            filter_dict['user_id'] = kwargs['user_id']

            # add pagination filter based on last object ID
        if page_token != '':
            filter_dict['_id'] = {'$lt': ObjectId(page_token)}

        # Set projection
        projection_MINIMAL = {
            # '_id': False,
            'task_log.id': True,
            'task_log.state': True,
        }
        projection_BASIC = {
            # '_id': False,
            'task_log.inputs.content': False,
            'task_log.system_logs': False,
            'task_log.logs.stdout': False,
            'task_log.logs.stderr': False,
            'tes_endpoint': False,
            'worker_id': False
        }
        projection_FULL = {
            # '_id': False,
            'worker_id': False,
            'tes_endpoint': False,
        }

        # Check view mode
        if 'view' in kwargs:
            view = kwargs['view']
        else:
            view = "BASIC"
        if view == "MINIMAL":
            projection = projection_MINIMAL
        elif view == "BASIC":
            projection = projection_BASIC
        elif view == "FULL":
            projection = projection_FULL
        else:
            raise BadRequest

        # query database for tasks
        cursor = self.db_client.find(
            filter=filter_dict,
            projection=projection
            # sort results by descending object ID (+/- newest to oldest)
        ).sort(
            '_id', -1
            # implement page size limit
        ).limit(
            page_size
        )

        # convert cursor to list
        tasks_list = list(cursor)

        # get next page token from ID of last task in cursor
        if tasks_list:
            next_page_token = str(tasks_list[-1]['_id'])
        else:
            next_page_token = ''

        # reshape list of task
        tasks_lists = []
        for task in tasks_list:
            del task['_id']
            if projection == projection_MINIMAL:
                task['id'] = task['task_log']['id']
                task['state'] = task['task_log']['state']
                tasks_lists.append({
                    'id': task['id'],
                    'state': task['state']
                })
            if projection == projection_BASIC:
                tasks_lists.append(task['task_log'])
            if projection == projection_FULL:
                tasks_lists.append(task['task_log'])

        # build and return response
        return {
            'next_page_token': next_page_token,
            'tasks': tasks_lists
        }

    def get_task(
            self,
            id=str,
            **kwargs
    ) -> Dict:
        """Return detailed information about a task.

                Args:
                    task_id: task identifier.
                    **kwargs: Additional keyword arguments passed along with
                              request.

                Returns:
                    Response object according to TES API schema . Cf.
                       https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml

                Raises:
                    pro_tes.exceptions.Forbidden: The requester is not allowed
                        to access the resource.
                    pro_tes.exceptions.TaskNotFound: The requested task is not
                        available.
                """

        # Set projection
        projection_MINIMAL = {
            # '_id': False,
            'task_log.id': True,
            'task_log.state': True,
        }

        projection_BASIC = {
            # '_id': False,
            'task_log.inputs.content': False,
            'task_log.system_logs': False,
            'task_log.logs.stdout': False,
            'task_log.logs.stderr': False,
            'tes_endpoint': False,
        }
        projection_FULL = {
            # '_id': False,
            'worker_id': False,
            'tes_endpoint': False,
        }
        # Check view mode
        if 'view' in kwargs:
            view = kwargs['view']
        else:
            view = "BASIC"
        if view == "MINIMAL":
            projection = projection_MINIMAL
        elif view == "BASIC":
            projection = projection_BASIC
        elif view == "FULL":
            projection = projection_FULL
        else:
            raise BadRequest

        document = self.db_client.find_one(
            filter={'task_log.id': id},
            projection=projection
        )
        # raise error if task was not found
        if document is None:
            logger.error("Task '{id}' not found.".format(id=id))
            raise

        # # raise error trying to access task that is not owned by user
        # # only if authorization enabled
        # self._check_access_permission(
        #     resource_id=id,
        #     owner=document.get('user_id', None),
        #     requester=kwargs.get('user_id', None),
        # )

        return document['task_log']

    def cancel_task(
            self,
            id: str,
            **kwargs
    ) -> Dict:
        """Cancel task.

               Args:
                   id: Task identifier.
                   **kwargs: Additional keyword arguments passed along with
                             request.

               Returns:
                   Task identifier.

               Raises:
                   pro_tes.exceptions.Forbidden: The requester is not allowed
                                                 to access the resource.
                   pro_tes.exceptions.TaskNotFound: The requested task is not
                       available.
               """

        document = self.db_client.find_one(
            filter={'task_log.id': id},
            projection={
                'user_id': True,
                'tes_endpoint.host': True,
                'tes_endpoint.base_path': True,
                'tes_endpoint.task_id': True,
                'task_log.state': True,
                '_id': False,
                'worker_id': True
            }
        )
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            worker_id=document['worker_id'],
        )
        # ensure resource is available
        if document is None:
            logger.error("task '{id}' not found.".format(id=id))
            raise TaskNotFound

        url = (
            f"{document['tes_endpoint']['host'].rstrip('/')}/"
            f"{document['tes_endpoint']['base_path'].strip('/')}"
        )
        # If task is in cancelable state...
        # if 'document.task_log.state' in TesState.is_cancelable or \
        #     'document.task_log.state' in TesState.UNKNOWN:

        # Cancel remote task
        
        try:
            cli = tes.HTTPClient(url, timeout=5)
            cli.cancel_task(task_id=document['tes_endpoint']['task_id'])
        except HTTPError:
            pass

            # Write log entry
            logger.info(
                (
                    "Task '{id}' (worker ID '{worker_id}') was canceled."
                ).format(
                    id=id,
                    worker_id=document['worker_id'],
                )
            )

        # Update task state
        db_connector.update_task_state(
            state='CANCELED',
        )
        return {}

    def _create_run_environment(
            self,
            document: DbDocument,
    ) -> DbDocument:

        controller_config = self.foca_config.controllers['post_task']
        # try until unused task id was found
        attempt = 1
        while attempt <= controller_config['db']['insert_attempts']:
            attempt += 1
            task_id = generate_id(
                charset=controller_config['task_id']['charset'],
                length=controller_config['task_id']['length'],
            )
            # create 'id feild in document and asign it with task_id created
            document.task_log['id'] = task_id

            # assign initial state of the task in document
            document.task_log['state'] = TesState.UNKNOWN.value

            # create worker id for task identification
            document.worker_id = uuid()

            # insert document into database
            try:
                self.db_client.insert(
                    document.dict(
                        exclude_none=True,
                    )
                )
            except DuplicateKeyError:
                continue
            return document

    def _attach_request(
            self,
            payload: dict,
            document: DbDocument
    ) -> DbDocument:
        # attach request
        document.task_log = payload

        return document

    def _sanitize_request(
            self,
            payloads: dict
    ) -> Dict:

        # process or sanitiza request for use with py-tes
        time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        if 'creation_time' not in payloads:
            payloads['creation_time'] = parse_time(time_now)
        if 'inputs' in payloads:
            payloads['inputs'] = [
                tes.models.Input(**input) for input in payloads['inputs']
            ]
        if 'outputs' in payloads:
            payloads['outputs'] = [
                tes.models.Output(**output) for output in payloads['outputs']
            ]
        if 'resources' in payloads:
            payloads['resources'] = \
                tes.models.Resources(**payloads['resources'])

        if 'executors' in payloads:
            payloads['executors'] = [
                tes.models.Executor(**executor) for executor in
                payloads['executors']
            ]
        if 'logs' in payloads:
            for log in payloads['logs']:
                log['start_time'] = time_now
                log['end_time'] = time_now
            log['logs'] = [
                tes.models.ExecutorLog(**log) for log in log['logs']
            ]
            if 'outputs' in log:
                for output in log['outputs']:
                    output['size_bytes'] = 0
                log['outputs'] = [
                    tes.models.SystemLog(**log) for log in log['system_logs']
                ]
            if 'system_logs' in log:
                log['system_logs'] = [
                    tes.models.SystemLog(**log) for log in log['system_logs']
                ]
        return payloads

    # def _track_task_progress(
    #         self,
    #         worker_id : str,
    #         remote_host: str,
    #         remote_base_path: str,
    #         remote_task_id = str,
    #         # jwt: Optional[str] = None,
    #         timeout : Optional[int] = None,
    # ) -> None:
    #     """Asynchronously track the task request on the remote TES.
    #
    #             Args:
    #                 worker_id: Identifier for the background job.
    #                 remote_host: Host at which the TES API is served that is
    #                     processing this request; note that this should
    #                     include the path information but *not* the base path
    #                     path defined in the TES API specification; e.g.,
    #                     specify https://my.tes.com/api if the actual API is
    #                     hosted at https://my.tes.com/api/ga4gh/tes/v1.
    #                 remote_base_path: Override the default path suffix
    #                     defined in the TES API specification,
    #                     i.e., `/ga4gh/tes/v1`.
    #                 remote_task_id: Task identifier on remote WES service.
    #                 jwt: Authorization bearer token to be passed on with
    #                      task request to external engine.
    #                 timeout: Timeout for the job. Set to `None` to disable
    #                          timeout.
    #             """
        # task__track_run_progress.apply_async(
        #     None,
        #     {
        #         'jwt': jwt,
        #         'worker_id': worker_id,
        #         'remote_host': remote_host,
        #         'remote_base_path': remote_base_path,
        #         'remote_task_id': remote_task_id,
        #     },
        #     soft_time_limit=timeout,
        # )
        # return None
