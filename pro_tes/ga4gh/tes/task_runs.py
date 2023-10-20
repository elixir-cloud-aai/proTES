"""Class implementing TES API-server-side controller methods."""

from copy import deepcopy
from datetime import datetime
import logging
from typing import Optional, Sequence

from bson.objectid import ObjectId  # type: ignore
from celery import uuid
from dateutil.parser import parse as parse_time
from flask import current_app, request
from foca.models.config import Config  # type: ignore
from foca.utils.misc import generate_id  # type: ignore
from pymongo.collection import Collection  # type: ignore
from pymongo.errors import DuplicateKeyError, PyMongoError  # type: ignore
import requests
import tes  # type: ignore
from tes.models import Task  # type: ignore

from pro_tes.exceptions import (
    BadRequest,
    NoTesInstancesAvailable,
    TaskNotFound,
)
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
from pro_tes.middleware.middleware_handler import MiddlewareHandler
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.utils.misc import strip_auth
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

    def create_task(  # pylint: disable=too-many-statements,too-many-branches
        self, **kwargs
    ) -> dict:
        """Start task.

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.
        """
        # create task document
        start_time = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        db_document: DbDocument = DbDocument()
        db_document.basic_auth = self.parse_basic_auth(request.authorization)
        assert request.json is not None
        payload_original: dict = deepcopy(request.json)
        db_document.task_original = TesTask(**payload_original)

        # apply middlewares
        mw_handler = MiddlewareHandler()
        mw_handler.set_middlewares(paths=current_app.config.foca.middlewares)
        logger.debug(f"Middlewares registered: {mw_handler.middlewares}")
        request_modified = mw_handler.apply_middlewares(request=request)

        # update task document
        assert request_modified.json is not None
        payload: dict = request_modified.json
        tes_urls = deepcopy(payload["tes_urls"])
        del payload["tes_urls"]
        db_document.task = TesTask(**payload)

        # create database document
        db_document = self._update_task(
            payload=payload,
            db_document=db_document,
            start_time=start_time,
            **kwargs,
        )
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            worker_id=db_document.worker_id,
        )
        logger.info(
            "Created task record with task identifier"
            f" '{db_document.task.id}' and worker job identifier"
            f" '{db_document.worker_id}'"
        )

        # validate request
        payload = self._sanitize_request(payload=payload)
        try:
            payload_marshalled = tes.Task(**payload)
        except TypeError as exc:
            db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
            raise BadRequest(
                f"Task '{db_document.task.id}' could not be "
                f"validated. Original error message: '{type(exc).__name__}: "
                f"{exc}'"
            ) from exc

        # relay request
        logger.info(
            "Attempting to forward the task request to any of the known TES"
            f" instances, in the following order: {tes_urls}"
        )
        for tes_url in tes_urls:
            db_document.tes_endpoint = TesEndpoint(host=tes_url)
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
                logger.warning(
                    f"Task '{db_document.task.id}' could not "
                    f"be sent to TES endpoint hosted at: {url}. Invalid TES"
                    " endpoint URL. Original error message: "
                    f"'{type(exc).__name__}: {exc}'"
                )
                continue

            # fix for FTP URLs with credentials on non-Funnel services
            def strip_none_items(_seq: Sequence) -> list:
                """Remove empty items from Sequence.

                Args:
                    _seq: Sequence of items.

                Returns:
                    List of none empty items.
                """
                return [item for item in _seq if item is not None]

            def remove_auth(_list: list) -> list:
                """Forward the list to 'remove_auth_from_url'.

                Args:
                    _list: List

                Returns:
                    List of items without basic authentication information.
                """
                return [
                    strip_auth(item.url)
                    for item in _list
                    if getattr(item, "url", None) is not None
                ]

            is_funnel = False
            try:
                response = cli.get_service_info()
                if response.name == "Funnel":
                    is_funnel = True
            except requests.exceptions.HTTPError:
                pass
            if not is_funnel:
                if payload_marshalled.inputs is not None:
                    inputs = strip_none_items(payload_marshalled.inputs)
                    payload_marshalled.inputs = remove_auth(inputs)

                if payload_marshalled.outputs is not None:
                    outputs = strip_none_items(payload_marshalled.outputs)
                    payload_marshalled.outputs = remove_auth(outputs)

            try:
                remote_task_id = cli.create_task(payload_marshalled)
            except requests.HTTPError as exc:
                logger.warning(
                    f"Task '{db_document.task.id}' could not be sent to TES"
                    f" endpoint hosted at: {url}. Original error message:"
                    f" '{type(exc).__name__}: {exc}'"
                )
                continue

            logger.info(
                f"Task '{db_document.task.id}' successfully forwarded to TES"
                f" endpoint hosted at: {url}. Remote tak identifier:"
                f" {remote_task_id}"
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
                tes_url=tes_url,
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

        db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
        raise NoTesInstancesAvailable(
            "Could not forward the task request to any TES instance. Task"
            " state set to 'SYSTEM_ERROR'."
        )

    def list_tasks(self, **kwargs) -> dict:
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

        name_prefix: str = kwargs.get("name_prefix")

        if name_prefix is not None:
            filter_dict["task_original.name"] = {"$regex": f"^{name_prefix}"}

        cursor = (
            self.db_client.find(filter=filter_dict, projection=projection)
            .sort("_id", -1)
            .limit(page_size)
        )
        tasks_list = list(cursor)

        logger.debug(f"Tasks list: {tasks_list}")
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

    def get_task(self, id=str, **kwargs) -> dict:
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

    def cancel_task(self, id: str, **kwargs) -> dict:
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
                task_id = db_document.task.logs[0].metadata.forwarded_to.id
            else:
                task_id = db_document.task.logs[0].metadata["remote_task_id"]
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
    ) -> tuple[str, str]:
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

    def _sanitize_request(self, payload: dict) -> dict:
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

    def _set_projection(self, view: str) -> dict:
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

    def _set_logs(self, payloads: dict, start_time: str) -> dict:
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
        tes_url: str,
        remote_task_id: str,
    ) -> DbDocument:
        """Set end time, task metadata in `TesTask.logs`, and update document.

        Args:
            db_connector: The database connector.
            tes_url: The TES URL where the task if forwarded.
            remote_task_id: Task identifier at the remote TES instance.

        Returns:
            The updated database document.
        """
        time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        tes_endpoint_dict = {"host": tes_url, "base_path": ""}
        db_document = db_connector.upsert_fields_in_root_object(
            root="tes_endpoint",
            **tes_endpoint_dict,
        )
        # updating the end time in TesTask logs
        for logs in db_document.task.logs:
            logs.end_time = time_now

        # updating the metadata in TesTask logs
        if self.store_logs:
            db_document = self._update_task_metadata(
                db_document=db_document,
                tes_url=tes_url,
                remote_task_id=remote_task_id,
            )
        else:
            for logs in db_document.task.logs:
                logs.metadata = {"remote_task_id": remote_task_id}

        db_document = db_connector.upsert_fields_in_root_object(
            root="task",
            **db_document.dict()["task"],
        )
        logger.debug(f"Task '{db_document.task}' inserted to database ")
        return db_document

    def _update_task_metadata(
        self, db_document: DbDocument, tes_url: str, remote_task_id: str
    ) -> DbDocument:
        """Update the task metadata.

        Set TES endpoint and remote task identifier in `TesTask.logs.metadata`.

        Args:
            db_document: The document in the database to be updated.
            tes_url: The TES URL where the task if forwarded.
            remote_task_id: Task identifier at the remote TES instance.

        Returns:
            The updated database document.
        """
        for logs in db_document.task.logs:
            tesNextTes_obj = TesNextTes(id=remote_task_id, url=tes_url)
            if logs.metadata.forwarded_to is None:
                logs.metadata.forwarded_to = tesNextTes_obj
        return db_document

    @staticmethod
    def parse_basic_auth(auth: Optional[dict[str, str]]) -> BasicAuth:
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
