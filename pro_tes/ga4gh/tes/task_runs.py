"""Class implementing TES API-server-side controller methods."""

from copy import deepcopy
from datetime import datetime
import logging
from typing import Dict, Tuple

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
    DbDocument, TesEndpoint, TesState, TesTask, TesTaskLog,
    TesNextTes
)
from pro_tes.ga4gh.tes.states import States
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import DbDocumentConnector
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

    def create_task(self, **kwargs) -> Dict:
        """Start task.

        Args:
            **kwargs: Additional keyword arguments passed along with
                             request and start_time.
        Returns:
            Task identifier.
        """
        payload: Dict = deepcopy(request.json)

        db_document: DbDocument = DbDocument()
        tes_uri_list = deepcopy(payload["tes_uri"])
        del payload["tes_uri"]

        db_document.task_outgoing = TesTask(**payload)
        db_document.task_incoming = TesTask(**payload)
        db_document = self._update_task_incoming(
            payload=payload,
            db_document=db_document,
            **kwargs
        )
        url: str = (
            f"{db_document.tes_endpoint.host.rstrip('/')}/"
            f"{db_document.tes_endpoint.base_path.lstrip('/')}"
        )
        logger.info(
            "Trying to send incoming task with task identifier "
            f"'{db_document.task_incoming.id}' and worker job identifier "
            f"'{db_document.worker_id}' to TES endpoint hosted at: {url}"
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
                f"Task '{db_document.task_incoming.id}' could not be sent "
                f"to TES endpoint hosted at: {url}. Incoming request invalid. "
                f"Original error message: '{type(exc).__name__}: "
                f"{exc}'"
            ) from exc

        for tes_uri in tes_uri_list:
            db_document.tes_endpoint = TesEndpoint(host=tes_uri)
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.lstrip('/')}"
            )
            try:
                cli = tes.HTTPClient(url, timeout=5)
            except ValueError as exc:
                db_connector.update_task_state(
                    state=TesState.SYSTEM_ERROR.value
                )
                logger.info(
                    f"Task '{db_document.task_incoming.id}' could not "
                    f"be sentto TES endpoint hosted at: {url}. Invalid TES"
                    f" endpoint URL. Original error message: "
                    f"'{type(exc).__name__}: {exc}'"
                )
                continue

            try:
                remote_task_id = cli.create_task(payload_marshalled)
            except requests.HTTPError as exc:
                db_connector.update_task_state(
                    state=TesState.SYSTEM_ERROR.value
                )
                logger.info(
                    f"Task '{db_document.task_incoming.id}' "
                    f"could not be sent to TES endpoint hosted "
                    f"at: {url}. Task could not be created. Original "
                    f"error message: '{type(exc).__name__}: "
                    f"{exc}'"
                )
                continue

            logger.info(
                f"Task '{remote_task_id}' "
                f"forwarded to TES endpoint "
                f"hosted at: {url}. proTES task identifier: "
                f"{db_document.task_incoming.id}."
            )
            try:
                task: Task = cli.get_task(remote_task_id)
                task_model_converter = TaskModelConverter(task=task)
                task_converted: TesTask = task_model_converter.convert_task()
                db_document.task_incoming.state = task_converted.state
            except requests.HTTPError as exc:
                logger.error(
                    f"Task '{db_document.task_incoming.id}' info could "
                    f"not be retrieved from TES endpoint hosted at: "
                    f"{url}. Original error message: "
                    f"'{type(exc).__name__}: {exc}'"
                )
            except PyMongoError as exc:
                logger.error(
                    "Database could not be updated with task info "
                    f"retrieved for task '{db_document.task_incoming.id}'"
                    f"sent to TES endpoint hosted at: {url}. "
                    f"Original error message:'{type(exc).__name__}: {exc}'"
                )
            # update task_logs, tes_endpoint and task_incoming in db
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
                },
            )
            return {"id": db_document.task_incoming.id}

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
                task["id"] = task["task_incoming"]["id"]
                task["state"] = task["task_incoming"]["state"]
                tasks_lists.append({"id": task["id"], "state": task["state"]})
            if view == "BASIC":
                tasks_lists.append(task["task_incoming"])
            if view == "FULL":
                tasks_lists.append(task["task_incoming"])

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
            filter={"task_incoming.id": id}, projection=projection
        )
        if document is None:
            logger.error(f"Task '{id}' not found.")
            raise TaskNotFound
        return document["task_incoming"]

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
            filter={"task_incoming.id": id},
            projection={"_id": False},
        )
        if document is None:
            logger.error(f"task '{id}' not found.")
            raise TaskNotFound
        db_document = DbDocument(**document)

        if db_document.task_incoming.state in States.CANCELABLE:
            db_connector = DbDocumentConnector(
                collection=self.db_client,
                worker_id=db_document.worker_id,
            )
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.strip('/')}"
            )
            if self.store_logs:
                task_id = db_document.task_incoming.logs[0].\
                    metadata.forwarded_to.id
            else:
                task_id = db_document.task_incoming.logs[0].metadata[
                    "remote_task_id"
                ]
            logger.warning(f"DB document: {db_document}")
            logger.info(
                "Trying cancel task with task identifier"
                f" '{task_id}' and worker job"
                f" identifier '{db_document.worker_id}' running at TES"
                f" endpoint hosted at: {url}"
            )
            cli = tes.HTTPClient(url, timeout=5)

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
            document.task_incoming.id = generate_id(
                charset=charset,
                length=length,
            )
            document.worker_id = uuid()
            try:
                self.db_client.insert(document.dict(exclude_none=True))
            except DuplicateKeyError:
                continue
            assert document is not None
            return document.task_incoming.id, document.worker_id
        raise DuplicateKeyError("Could not insert document into database.")

    def _sanitize_request(self, payload: dict) -> Dict:
        """Sanitize request for use with py-tes.

        Args:
            payloads: Request payload.

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
        """Set database projectoin for selected view.

        Args:
            view: View path parameter.

        Returns:
            Database projection for selected view.

        Raises:
            pro_tes.exceptions.BadRequest: Invalid view parameter.
        """
        if view == "MINIMAL":
            projection = {
                "task_incoming.id": True,
                "task_incoming.state": True,
            }
        elif view == "BASIC":
            projection = {
                "task_incoming.inputs.content": False,
                "task_incoming.system_logs": False,
                "task_incoming.logs.stdout": False,
                "task_incoming.logs.stderr": False,
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

    def _update_task_incoming(
        self,
        payload: dict,
        db_document: DbDocument,
        **kwargs
    ) -> DbDocument:
        """Update the task incoming object."""
        logs = self._set_logs(
            payloads=deepcopy(payload),
            start_time=kwargs["start_time"]
        )
        db_document.task_incoming.logs = [
            TesTaskLog(**logs) for logs in logs
        ]
        db_document.task_incoming.state = TesState.UNKNOWN
        db_document.user_id = kwargs.get("user_id", None)

        (task_id, worker_id) = self._write_doc_to_db(document=db_document)
        db_document.task_incoming.id = task_id
        db_document.worker_id = worker_id
        return db_document

    def _set_logs(self, payloads: dict, start_time: str) -> Dict:
        """Set up the logs for the incoming request."""
        if "logs" not in payloads.keys():
            logs = [{
                'logs': [],
                'metadata': {},
                'start_time': start_time,
                'end_time': None,
                'outputs': [],
                'system_logs': []
            }]
            payloads["logs"] = logs
        else:
            for log in payloads["logs"]:
                log["start_time"] = start_time
        return payloads['logs']

    def _update_doc_in_db(
        self,
        db_connector,
        tes_uri: str,
        remote_task_id: str,
    ) -> DbDocument:
        """Update the document in the database."""
        time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
        tes_endpoint_dict = {'host': tes_uri, 'base_path': ''}
        db_document = db_connector.upsert_fields_in_root_object(
            root="tes_endpoint",
            **tes_endpoint_dict,
        )
        logger.info(
            f"TES endpoint: '{db_document.tes_endpoint.host}' "
            f"finally to database "
        )
        # updating the end time in TesTask logs
        for logs in db_document.task_incoming.logs:
            logs.end_time = time_now

        # updating the metadata in TesTask logs
        if self.store_logs:
            db_document = self._update_task_metadata(
                db_document=db_document,
                tes_uri=tes_uri,
                remote_task_id=remote_task_id
            )
        else:
            for logs in db_document.task_incoming.logs:
                logs.metadata = {
                    "remote_task_id": remote_task_id
                }

        db_document = db_connector.upsert_fields_in_root_object(
            root="task_incoming",
            **db_document.dict()["task_incoming"],
        )
        logger.info(
            f"Task '{db_document.task_incoming}' "
            f"inserted to database "
        )
        return db_document

    def _update_task_metadata(
        self,
        db_document: DbDocument,
        tes_uri: str,
        remote_task_id: str
    ) -> DbDocument:
        """Update the task metadata."""
        for logs in db_document.task_incoming.logs:
            tesNextTes_obj = TesNextTes(id=remote_task_id, url=tes_uri)
            if logs.metadata.forwarded_to is None:
                logs.metadata.forwarded_to = tesNextTes_obj
        return db_document
