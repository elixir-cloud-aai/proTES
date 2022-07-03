import logging
from typing import (
    Dict,
)
import tes
from bson.objectid import ObjectId
from celery import uuid
from foca.models.config import Config
from foca.utils.misc import generate_id
from flask import (
    current_app,
    request,
)
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from pro_tes.utils.db_utils import DbDocumentConnector


from pro_tes.exceptions import (
    EngineUnavailable,
    RunNotFound,
)

from pro_tes.ga4gh.tes.model import (
    DbDocument,
    TesEndpoint,
    TesState
)
import json
 

logger = logging.getLogger(__name__)

class TaskRuns:
    
    def __init__(self) -> None:
        """Class for TES API server-side controller methods.

        Attributes:
            config: App configuration.
            foca_config: FOCA configuration.
            db_client: Database collection storing task run objects.
            document: Document to be inserted into the collection. Note that
                this is iteratively built up.
        """
        self.config: Dict = current_app.config
        self.foca_config: Config = current_app.config['FOCA']
        self.db_client: Collection = (
            self.foca_config.db.dbs['runStore'].collections['tasks'].client
        )
    
    def create_task (
        self,
        **kwargs
    ) -> Dict:
        # Initialize database document
        document : DbDocument = DbDocument()
        
        # attach request
        data = json.dumps(request.json)
        document.run_log.request = data
        
        
        # get and attach suitable Tes endpoint
        document.tes_endpoint = TesEndpoint(
            host="https://csc-tesk-noauth.rahtiapp.fi",
        )
        # # get and attach task run owner
        document.user_id = kwargs.get('user_id', None)
        
        # create run environment & insert run document into run collection
        document_stored = self._create_run_environment(document=document)
        
        # instantiate database connector
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            worker_id=document_stored.worker_id,
        )
        
         # forward incoming TES request and validate response
        url = (
            f"{document_stored.tes_endpoint.host.rstrip('/')}/"
            f"{document_stored.tes_endpoint.base_path.strip('/')}"
        )
        logger.info(
            f"Sending task '{document_stored.run_log.task_id}' with "
            f"task identifier '{document_stored.worker_id}' to TES endpoint "
            f"hosted at: {url}"
        )
        
        # #TODO : Submitting of task sucessfully to TES endpoint
        # try:
        #     task = tes.Task(document.run_log.request)
        #     cli = tes.HTTPClient(url,timeout = 5)
        #     task_id = cli.create_task(task)
        # except EngineUnavailable:
        #     db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
            
        # document_stored: DbDocument = (
        #     db_connector.upsert_fields_in_root_object(
        #         root='tes_endpoint',
        #         task_id = task_id,
        #     )
        # )
        db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
        return {'id' : document_stored.run_log.task_id}
    
    def list_tasks(
        self,
        **kwargs
    )-> Dict:
        if 'page_size' in kwargs:
            page_size = kwargs['page_size']
        else:
            page_size = (
                self.foca_config.controllers['list_runs']['default_page_size']
            )
        # extract/set page token
        if 'page_token' in kwargs:
            page_token = kwargs['page_token']
        else:
            page_token = ''
        
        # initialize filter dictionary
        filter_dict = {}
        
        # add filter for user-owned runs if user ID is available
        if 'user_id' in kwargs:
            filter_dict['user_id'] = kwargs['user_id']
        
                # add pagination filter based on last object ID
        if page_token != '':
            filter_dict['_id'] = {'$lt': ObjectId(page_token)}
            
        #TODO: to set view (minimal,basic,full)
        
        # query database for task runs
        cursor = self.db_client.find(
            filter=filter_dict,
            projection={
                'run_log.task_id': True,
                'run_log.state': True,
            }
            # sort results by descending object ID (+/- newest to oldest)
            ).sort(
                '_id', -1
            # implement page size limit
            ).limit(
                page_size
            )
         # convert cursor to list
        tasks_list = list(cursor)

        # get next page token from ID of last run in cursor
        if tasks_list:
            next_page_token = str(tasks_list[-1]['_id'])
        else:
            next_page_token = ''
        
        # reshape list of runs
        for task in tasks_list:
            task['task_id'] = task['run_log']['task_id']
            task['state'] = task['run_log']['state']
            del task['run_log']
            del task['_id']
        
        # build and return response
        return {
            'next_page_token': next_page_token,
            'runs': tasks_list
        }
    
    def get_task(
        self,
        id= str,
        **kwargs
    ) -> Dict:
        #TODO: to set view (minimal,basic,full)
        # retrieve task run
        document = self.db_client.find_one(
            filter={'run_log.task_id': id},
            projection={
                'user_id': True,
                'run_log': True,
                '_id': False,
            }
        )
        # raise error if task run was not found
        if document is None:
            logger.error("Run '{id}' not found.".format(id=id))
            raise RunNotFound
    
        # # raise error trying to access task run that is not owned by user
        # # only if authorization enabled
        # self._check_access_permission(
        #     resource_id=id,
        #     owner=document.get('user_id', None),
        #     requester=kwargs.get('user_id', None),
        # )
        return document['run_log']
    
    def cancel_task(
        self,
        id : str,
        **kwargs
    ) -> Dict:
        #TODO : proper cancelling of task after it is properply submitted to TES endpoint
        document = self.db_client.find_one(
            filter={'run_log.task_id': id},
            projection={
                'user_id': True,
                'tes_endpoint.host': True,
                'tes_endpoint.base_path': True,
                'tes_endpoint.task_id': True,
                '_id': False,
            }
        )
        # ensure resource is available
        if document is None:
            logger.error("task '{id}' not found.".format(id =id ))
            raise RunNotFound
        
        url_host = document['tes_endpoint']['host']
        # url_base = document['tes_endpoint']['base_path']
        url_base = '/v1'
        url = (
            f"{url_host}{url_base}"
        )
        cli = tes.HTTPClient(url,timeout = 5)
        id = document['tes_endpoint']['task_id']
        cli.cancel_task(task_id = id)
        
        return {}
    
    def _create_run_environment(
        self,
        document: DbDocument,
    ) -> DbDocument:
       
        controller_config = self.foca_config.controllers['post_task']
        # try until unused run id was found
        attempt = 1
        while attempt <= controller_config['db']['insert_attempts']:
            attempt += 1
            task_id = generate_id(
                charset=controller_config['task_id']['charset'],
                length=controller_config['task_id']['length'],
            )
           

            # populate document
            document.run_log.task_id = task_id
            document.worker_id= uuid()

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
