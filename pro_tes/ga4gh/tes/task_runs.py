"""Class implementing TES API-server-side controller methods."""

from copy import deepcopy
from datetime import datetime
import logging
from typing import Dict, Optional, Tuple

from bson.objectid import ObjectId
from celery import uuid
from dateutil.parser import parse as parse_time
from flask import current_app, request
from foca.models.config import Config
from foca.utils.misc import generate_id
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError
import requests
import tes
from tes.models import Task

from pro_tes.exceptions import BadRequest, TaskNotFound
from pro_tes.ga4gh.tes.models import (
    BasicAuth,
    DbDocument,
    TesEndpoint,
    TesState,
    TesTask,
    TesTaskLog,
    TesNextTes,
)
from pro_tes.ga4gh.tes.states import States
from pro_tes.middleware.middleware import TaskDistributionMiddleware
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.utils.misc import remove_auth_from_url
from pro_tes.utils.models import TaskModelConverter

# pragma pylint: disable=invalid-name,redefined-builtin,unused-argument
# pragma pylint: disable=too-many-locals
# pylint: disable=unsubscriptable-object

logger = logging.getLogger(__name__)


class TaskRuns:
    """Class for TES API server-side controller methods.

    Attributes:
        foca_config: FOCA configuration.
        db_client: Database collection storing task objects.
        document: Document to be inserted into the collection. Note that it is
            built up iteratively.
    """

    def __init__(self) -> None:
        """Construct object instance."""
        self.foca_config: Config = current_app.config.foca
        self.db_client: Collection = (
            self.foca_config.db.dbs["taskStore"].collections["tasks"].client
        )
        self.store_logs = self.foca_config.storeLogs["execution_trace"]
        self.task_distributor = TaskDistributionMiddleware()

    def create_task(  # pylint: disable=too-many-statements,too-many-branches
        self, **kwargs
    ) -> Dict:
        """Start task.

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.
        """
        start_time = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        payload: Dict = deepcopy(request.json)
        db_document: DbDocument = DbDocument()

        db_document.basic_auth = self.parse_basic_auth(request.authorization)

        db_document.task_original = TesTask(**payload)

        # middleware is called after the task is created in the database
        payload = self.task_distributor.modify_request(request=request).json

        tes_uri_list = deepcopy(payload["tes_uri"])
        del payload["tes_uri"]

        db_document.task = TesTask(**payload)
        db_document = self._update_task(
            payload=payload,
            db_document=db_document,
            start_time=start_time,
            **kwargs,
        )
        logger.info(
            "Trying to forward task with task identifier "
            f"'{db_document.task.id}' and worker job identifier "
            f"'{db_document.worker_id}'"
        )
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            worker_id=db_document.worker_id,
        )
        payload = self._sanitize_request(payload=payload)

        try:
            payload_marshalled = tes.Task(**payload)
        except TypeError as exc:
            db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
            raise BadRequest(
                f"Task '{db_document.task.id}' could not be "
                f"validate. Original error message: '{type(exc).__name__}: "
                f"{exc}'"
            ) from exc

        for tes_uri in tes_uri_list:
            db_document.tes_endpoint = TesEndpoint(host=tes_uri)
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.lstrip('/')}"
            )
            try:
                cli = tes.HTTPClient(
                    url,
                    timeout=5,
                    user=db_document.basic_auth.username,
                    password=db_document.basic_auth.password,
                )
            except ValueError as exc:

                logger.info(
                    f"Task '{db_document.task.id}' could not "
                    f"be sentto TES endpoint hosted at: {url}. Invalid TES"
                    " endpoint URL. Original error message: "
                    f"'{type(exc).__name__}: {exc}'"
                )
                continue

            # fix for FTP URLs with credentials on non-Funnel services
            is_funnel = False
            try:
                response = cli.get_service_info()
                if response.name == "Funnel":
                    is_funnel = True
            except requests.exceptions.HTTPError:
                pass
            if not is_funnel:
                if payload_marshalled.inputs is not None:
                    for input in payload_marshalled.inputs:
                        input.url = remove_auth_from_url(input.url)
                if payload_marshalled.outputs is not None:
                    for output in payload_marshalled.outputs:
                        output.url = remove_auth_from_url(output.url)

            try:
                remote_task_id = cli.create_task(payload_marshalled)
            except requests.HTTPError as exc:

                logger.info(
                    f"Task '{db_document.task.id}' "
                    "could not be sent to TES endpoint hosted "
                    f"at: {url}. Task could not be created. Original "
                    f"error message: '{type(exc).__name__}: "
                    f"{exc}'"
                )
                continue

            logger.info(
                f"Task '{remote_task_id}' "
                "forwarded to TES endpoint "
                f"hosted at: {url}. proTES task identifier: "
                f"{db_document.task.id}."
            )
            try:
                task: Task = cli.get_task(remote_task_id)
                task_model_converter = TaskModelConverter(task=task)
                task_converted: TesTask = task_model_converter.convert_task()
                db_document.task.state = task_converted.state
            except requests.HTTPError as exc:
                logger.error(
                    f"Task '{db_document.task.id}' info could "
                    "not be retrieved from TES endpoint hosted at: "
                    f"{url}. Original error message: "
                    f"'{type(exc).__name__}: {exc}'"
                )
            except PyMongoError as exc:
                logger.error(
                    "Database could not be updated with task info "
                    f"retrieved for task '{db_document.task.id}'"
                    f"sent to TES endpoint hosted at: {url}. "
                    f"Original error message:'{type(exc).__name__}: {exc}'"
                )
            # update task_logs, tes_endpoint and task in db
            db_document = self._update_doc_in_db(
                db_connector=db_connector,
                tes_uri=tes_uri,
                remote_task_id=remote_task_id,
            )
            task__track_task_progress.apply_async(
                None,
                {
                    "worker_id": db_document.worker_id,
                    "remote_host": db_document.tes_endpoint.host,
                    "remote_base_path": db_document.tes_endpoint.base_path,
                    "remote_task_id": remote_task_id,
                    "user": db_document.basic_auth.username,
                    "password": db_document.basic_auth.password,
                },
            )
            return {"id": db_document.task.id}

        # set TES state to SYSTEM_ERROR as task submission failed on all the available TES instances.
        db_connector.update_task_state(
            state=TesState.SYSTEM_ERROR.value
        )

    def list_tasks(self, **kwargs) -> Dict:
        """Return list of tasks.

        Args:
            **kwargs: Keyword arguments passed along with request.

        Returns:
            Response object according to TES API schema . Cf.
            https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml
        """
        page_size = kwargs.get(
            "page_size",
            self.foca_config.controllers["list_tasks"]["default_page_size"],
        )
        page_token = kwargs.get("page_token")
        filter_dict = {}
        filter_dict["user_id"] = kwargs.get("user_id")

        if page_token is not None:
            filter_dict["_id"] = {"$lt": ObjectId(page_token)}
        view = kwargs.get("view", "BASIC")
        projection = self._set_projection(view=view)

        cursor = (
            self.db_client.find(filter=filter_dict, projection=projection)
            .sort("_id", -1)
            .limit(page_size)
        )
        tasks_list = list(cursor)

        logger.info(f"Tasks list: {tasks_list}")
        if tasks_list:
            next_page_token = str(tasks_list[-1]["_id"])
        else:
            next_page_token = ""

        tasks_lists = []
        for task in tasks_list:
            del task["_id"]
            if view == "MINIMAL":
                task["id"] = task["task"]["id"]
                task["state"] = task["task"]["state"]
                tasks_lists.append({"id": task["id"], "state": task["state"]})
            if view == "BASIC":
                tasks_lists.append(task["task"])
            if view == "FULL":
                tasks_lists.append(task["task"])

        return {"next_page_token": next_page_token, "tasks": tasks_lists}

    def get_task(self, id=str, **kwargs) -> Dict:
        """Return detailed information about a task.

        Args:
            task_id: Task identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Response object according to TES API schema . Cf.
                https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml

        Raises:
            pro_tes.exceptions.TaskNotFound: The requested task is not
                available.
        """
        projection = self._set_projection(view=kwargs.get("view", "BASIC"))
        document = self.db_client.find_one(
            filter={"task.id": id}, projection=projection
        )
        if document is None:
            logger.error(f"Task '{id}' not found.")
            raise TaskNotFound
        return document["task"]

    def cancel_task(self, id: str, **kwargs) -> Dict:
        """Cancel task.

        Args:
            id: Task identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.

        Raises:
            pro_tes.exceptions.Forbidden: The requester is not allowed
                to access the resource.
            pro_tes.exceptions.TaskNotFound: The requested task is not
                available.
        """
        document = self.db_client.find_one(
            filter={"task.id": id},
            projection={"_id": False},
        )
        if document is None:
            logger.error(f"task '{id}' not found.")
            raise TaskNotFound
        db_document = DbDocument(**document)

        if db_document.task.state in States.CANCELABLE:
            db_connector = DbDocumentConnector(
                collection=self.db_client,
                worker_id=db_document.worker_id,
            )
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.strip('/')}"
            )
            if self.store_logs:
                task_id = db_document.task.logs[
                    0
                ].metadata.forwarded_to.id
            else:
                task_id = db_document.task.logs[0].metadata[
                    "remote_task_id"
                ]
            logger.info(
                "Trying to cancel task with task identifier"
                f" '{task_id}' and worker job"
                f" identifier '{db_document.worker_id}' running at TES"
                f" endpoint hosted at: {url}"
            )
            cli = tes.HTTPClient(
                url,
                timeout=5,
                user=db_document.basic_auth.username,
                password=db_document.basic_auth.password,
            )

            cli.cancel_task(task_id=task_id)
            db_connector.update_task_state(
                state="CANCELED",
            )
            logger.info(
                f"Task '{id}' with worker ID '{db_document.worker_id}'"
                " canceled."
            )
        return {}

    def _write_doc_to_db(
        self,
        document: DbDocument,
    ) -> Tuple[str, str]:
        """Create database entry for task.

        Args:
            document: Document to be written to database.

        Returns:
            Tuple of task id and worker id.
        """
        controller_config = self.foca_config.controllers["post_task"]
        charset = controller_config["task_id"]["charset"]
        length = controller_config["task_id"]["length"]

        # try inserting until unused task id found
        for _ in range(controller_config["db"]["insert_attempts"]):
            document.task.id = generate_id(
                charset=charset,
                length=length,
            )
            document.worker_id = uuid()
            try:
                self.db_client.insert(document.dict(exclude_none=True))
            except DuplicateKeyError:
                continue
            assert document is not None
            return document.task.id, document.worker_id
        raise DuplicateKeyError("Could not insert document into database.")

    def _sanitize_request(self, payload: dict) -> Dict:
        """Sanitize request for use with py-tes.

        Args:
            payload: Request payload.

        Returns:
            Sanitized request payload.
        """
        time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        if "creation_time" not in payload:
            payload["creation_time"] = parse_time(time_now)
        if "inputs" in payload:
            payload["inputs"] = [
                tes.models.Input(**input) for input in payload["inputs"]
            ]
        if "outputs" in payload:
            payload["outputs"] = [
                tes.models.Output(**output) for output in payload["outputs"]
            ]
        if "resources" in payload:
            payload["resources"] = tes.models.Resources(**payload["resources"])
        if "executors" in payload:
            payload["executors"] = [
                tes.models.Executor(**executor)
                for executor in payload["executors"]
            ]
        if "logs" in payload:
            payload["logs"] = [
                tes.models.TaskLog(**log) for log in payload["logs"]
            ]
        return payload

    def _set_projection(self, view: str) -> Dict:
        """Set database projection for selected view.

        Args:
            view: View path parameter.

        Returns:
            Database projection for selected view.

        Raises:
            pro_tes.exceptions.BadRequest: Invalid view parameter.
        """
        if view == "MINIMAL":
            projection = {
                "task.id": True,
                "task.state": True,
            }
        elif view == "BASIC":
            projection = {
                "task.inputs.content": False,
                "task.system_logs": False,
                "task.logs.stdout": False,
                "task.logs.stderr": False,
                "tes_endpoint": False,
            }
        elif view == "FULL":
            projection = {
                "worker_id": False,
                "tes_endpoint": False,
            }
        else:
            raise BadRequest
        return projection

    def _update_task(
        self, payload: dict, db_document: DbDocument, start_time: str, **kwargs
    ) -> DbDocument:
        """Update the task object.

        Args:
            payload: A dictionary containing the payload for the update.
            db_document: The document in the database to be updated.
            start_time: The starting time of the incoming TES request.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            DbDocument: The updated database document.
        """
        logs = self._set_logs(
            payloads=deepcopy(payload), start_time=start_time
        )
        db_document.task.logs = [TesTaskLog(**logs) for logs in logs]
        db_document.task.state = TesState.UNKNOWN
        db_document.user_id = kwargs.get("user_id", None)

        (task_id, worker_id) = self._write_doc_to_db(document=db_document)
        db_document.task.id = task_id
        db_document.worker_id = worker_id
        return db_document

    def _set_logs(self, payloads: dict, start_time: str) -> Dict:
        """Create or update `TesTask.logs` and set start time.

        Args:
            payload: A dictionary containing the payload for the update.
            start_time: The starting time of the incoming TES request.

        Returns:
            Task logs with start time set.
        """
        if "logs" not in payloads.keys():
            logs = [
                {
                    "logs": [],
                    "metadata": {},
                    "start_time": start_time,
                    "end_time": None,
                    "outputs": [],
                    "system_logs": [],
                }
            ]
            payloads["logs"] = logs
        else:
            for log in payloads["logs"]:
                log["start_time"] = start_time
        return payloads["logs"]

    def _update_doc_in_db(
        self,
        db_connector,
        tes_uri: str,
        remote_task_id: str,
    ) -> DbDocument:
        """Set end time, task metadata in `TesTask.logs`, and update document.

        Args:
            db_connector: The database connector.
            tes_uri: The TES URI where the task if forwarded.
            remote_task_id: Task identifier at the remote TES instance.

        Returns:
            The updated database document.
        """
        time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        tes_endpoint_dict = {"host": tes_uri, "base_path": ""}
        db_document = db_connector.upsert_fields_in_root_object(
            root="tes_endpoint",
            **tes_endpoint_dict,
        )
        logger.info(
            f"TES endpoint: '{db_document.tes_endpoint.host}' "
            "finally to database "
        )
        # updating the end time in TesTask logs
        for logs in db_document.task.logs:
            logs.end_time = time_now

        # updating the metadata in TesTask logs
        if self.store_logs:
            db_document = self._update_task_metadata(
                db_document=db_document,
                tes_uri=tes_uri,
                remote_task_id=remote_task_id,
            )
        else:
            for logs in db_document.task.logs:
                logs.metadata = {"remote_task_id": remote_task_id}

        db_document = db_connector.upsert_fields_in_root_object(
            root="task",
            **db_document.dict()["task"],
        )
        logger.info(
            f"Task '{db_document.task}' inserted to database "
        )
        return db_document

    def _update_task_metadata(
        self, db_document: DbDocument, tes_uri: str, remote_task_id: str
    ) -> DbDocument:
        """Update the task metadata.

        Set TES endpoint and remote task identifier in `TesTask.logs.metadata`.

        Args:
            db_document: The document in the database to be updated.
            tes_uri: The TES URI where the task if forwarded.
            remote_task_id: Task identifier at the remote TES instance.

        Returns:
            The updated database document.
        """
        for logs in db_document.task.logs:
            tesNextTes_obj = TesNextTes(id=remote_task_id, url=tes_uri)
            if logs.metadata.forwarded_to is None:
                logs.metadata.forwarded_to = tesNextTes_obj
        return db_document

    @staticmethod
    def parse_basic_auth(auth: Optional[Dict[str, str]]) -> BasicAuth:
        """Parse basic auth header.

        Args:
            auth: Request authorization information.

        Returns:
            Basic authorization model instance with username and password;
                missing values are set to `None`.
        """
        if auth is None:
            return BasicAuth()
        return BasicAuth(
            username=auth.get("username"),
            password=auth.get("password"),
        )
