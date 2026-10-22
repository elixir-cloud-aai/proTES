"""Class implementing TES API-server-side controller methods."""

from copy import deepcopy
import logging
import re
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from celery import uuid
from flask import request
from foca import get_collection
from foca.utils.misc import generate_id
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError
import requests
import tes
from tes.models import Task
from werkzeug.datastructures import Authorization

from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import (
    BadRequest,
    MiddlewareException,
    NoTesInstancesAvailable,
    TaskNotFound,
)
from pro_tes.ga4gh.tes.models import (
    BasicAuth,
    DbDocument,
    Metadata,
    TesEndpoint,
    TesForward,
    TesState,
    TesTask,
    TesTaskLog,
)
from pro_tes.ga4gh.tes.states import States
from pro_tes.middleware.middleware_handler import MiddlewareHandler
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.utils.misc import get_custom_config, strip_auth, utc_now
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)


def flatten_forwards(task: dict[str, Any]) -> dict[str, Any]:
    """Serve forwards nested by earlier versions as lists.

    Args:
        task: Stored task (`DbDocument.task`), possibly projected.

    Returns:
        The task, with the forwards of its logs as lists (cf.
        :py:meth:`pro_tes.ga4gh.tes.models.Metadata.flatten_forwards`).
    """
    for log in task.get("logs") or []:
        metadata = log.get("metadata") if isinstance(log, dict) else None
        if isinstance(metadata, dict) and "forwarded_to" in metadata:
            metadata["forwarded_to"] = Metadata.flatten_forwards(
                metadata["forwarded_to"]
            )
    return task


class TaskRuns:
    """Class for TES API server-side controller methods.

    Attributes:
        custom_config: Custom configuration of the app.
        db_client: Database collection storing task objects.
        document: Document to be inserted into the collection. Note that it is
            built up iteratively.
    """

    def __init__(self) -> None:
        """Construct object instance."""
        self.custom_config: CustomConfig = get_custom_config()
        self.db_client: Collection[dict[str, Any]] = get_collection(
            "taskStore", "tasks"
        )
        self.store_logs = self.custom_config.storeLogs.execution_trace

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        """Start task.

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.

        Raises:
            pro_tes.exceptions.BadRequest: The request has no JSON body.
            pro_tes.exceptions.MiddlewareException: The middlewares removed
                the JSON body.
        """
        # create task document
        start_time = utc_now()
        db_document: DbDocument = DbDocument()
        db_document.basic_auth = self.parse_basic_auth(request.authorization)
        if request.json is None:
            raise BadRequest("Request body is missing")
        payload_original: dict[str, Any] = deepcopy(request.json)
        db_document.task_original = TesTask(**payload_original)

        # apply middlewares
        mw_handler = MiddlewareHandler()
        mw_handler.set_middlewares(paths=self.custom_config.middlewares.root)
        logger.debug("Middlewares registered: %s", mw_handler.middlewares)
        request_modified = mw_handler.apply_middlewares(request=request)

        # update task document
        if request_modified.json is None:
            raise MiddlewareException("Middlewares removed the request body")
        payload: dict[str, Any] = request_modified.json
        tes_urls = deepcopy(payload["tes_urls"])
        del payload["tes_urls"]
        # set by the server (TES), not by clients
        payload["creation_time"] = start_time
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
            "Created task record with task identifier '%s' and worker job"
            " identifier '%s'",
            db_document.task.id,
            db_document.worker_id,
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
            " instances, in the following order: %s",
            tes_urls,
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
                    "Task '%s' could not be sent to TES endpoint hosted at: %s."
                    " Invalid TES endpoint URL. Original error message:"
                    " '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
                )
                continue

            is_funnel = False
            try:
                response = cli.get_service_info()
                if response.name == "Funnel":
                    is_funnel = True
            except requests.exceptions.HTTPError:
                pass
            # each backend gets the task as validated, without changes made
            # for other backends
            task_relayed = deepcopy(payload_marshalled)
            if not is_funnel:
                self._strip_credentials(task=task_relayed)

            try:
                remote_task_id = cli.create_task(task_relayed)
            except requests.HTTPError as exc:
                logger.warning(
                    "Task '%s' could not be sent to TES endpoint hosted at: %s."
                    " Original error message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
                )
                continue

            logger.info(
                "Task '%s' successfully forwarded to TES endpoint hosted at:"
                " %s. Remote task identifier: %s",
                db_document.task.id,
                url,
                remote_task_id,
            )
            try:
                task: Task = cli.get_task(remote_task_id)
                task_model_converter = TaskModelConverter(task=task)
                task_converted: TesTask = task_model_converter.convert_task()
                db_document.task.state = task_converted.state
            except requests.HTTPError as exc:
                logger.error(
                    "Task '%s' info could not be retrieved from TES endpoint"
                    " hosted at: %s. Original error message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
                )
            except PyMongoError as exc:
                logger.error(
                    "Database could not be updated with task info retrieved"
                    " for task '%s' sent to TES endpoint hosted at: %s."
                    " Original error message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
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

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        """Return list of tasks.

        Args:
            **kwargs: Keyword arguments passed along with request.

        Returns:
            Response object according to TES API schema . Cf.
            https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml;
            tasks are listed newest first, and `next_page_token` is empty on
            the last page.

        Raises:
            pro_tes.exceptions.BadRequest: The page size is not positive, or
                the page token is invalid.
        """
        page_size = self._page_size(page_size=kwargs.get("page_size"))
        filter_dict: dict[str, Any] = {}

        user_id = kwargs.get("user_id")
        if user_id is not None:
            filter_dict["user_id"] = user_id

        # an empty token (of the last page) is taken as no token
        page_token = kwargs.get("page_token")
        if page_token:
            try:
                filter_dict["_id"] = {"$lt": ObjectId(page_token)}
            except (InvalidId, TypeError) as exc:
                raise BadRequest(f"Invalid page token: {page_token!r}") from exc
        view = kwargs.get("view", "BASIC")
        projection = self._set_projection(view=view)

        # matched literally, as a prefix
        name_prefix = kwargs.get("name_prefix")
        if name_prefix is not None:
            filter_dict["task_original.name"] = {"$regex": f"^{re.escape(name_prefix)}"}

        cursor = (
            self.db_client.find(filter=filter_dict, projection=projection)
            .sort("_id", -1)
            .limit(page_size + 1)
        )
        tasks_list = list(cursor)

        logger.debug("Tasks list: %s", tasks_list)
        next_page_token = ""
        if len(tasks_list) > page_size:
            tasks_list = tasks_list[:page_size]
            next_page_token = str(tasks_list[-1]["_id"])

        tasks_lists = []
        for task in tasks_list:
            del task["_id"]
            if view == "MINIMAL":
                task["id"] = task["task"]["id"]
                task["state"] = task["task"]["state"]
                tasks_lists.append({"id": task["id"], "state": task["state"]})
            if view == "BASIC":
                tasks_lists.append(flatten_forwards(task["task"]))
            if view == "FULL":
                tasks_lists.append(flatten_forwards(task["task"]))

        return {"next_page_token": next_page_token, "tasks": tasks_lists}

    def get_task(self, id: str, **kwargs: Any) -> dict[str, Any]:
        """Return detailed information about a task.

        Args:
            id: Task identifier.
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
            logger.error("Task '%s' not found.", id)
            raise TaskNotFound
        return flatten_forwards(document["task"])

    def cancel_task(self, id: str, **kwargs: Any) -> dict[str, Any]:
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
            logger.error("Task '%s' not found.", id)
            raise TaskNotFound
        db_document: DbDocument = DbDocument(**document)

        if db_document.task.state in States.CANCELABLE:
            db_connector = DbDocumentConnector(
                collection=self.db_client,
                worker_id=db_document.worker_id,
            )
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.strip('/')}"
            )

            task_id = self._remote_task_id(db_document=db_document)

            logger.info(
                "Trying to cancel task with task identifier '%s' and worker job"
                " identifier '%s' running at TES endpoint hosted at: %s",
                task_id,
                db_document.worker_id,
                url,
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
                "Task '%s' with worker ID '%s' canceled.", id, db_document.worker_id
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
        controller_config = self.custom_config.controllers.post_task
        charset = controller_config.task_id.charset
        length = controller_config.task_id.length

        # try inserting until unused task id found
        for _ in range(controller_config.db.insert_attempts):
            document.task.id = generate_id(
                charset=charset,
                length=length,
            )
            document.worker_id = uuid()
            try:
                self.db_client.insert_one(
                    document.model_dump(mode="json", exclude_none=True)
                )
            except DuplicateKeyError:
                continue
            return document.task.id, document.worker_id
        raise DuplicateKeyError("Could not insert document into database.")

    def _sanitize_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sanitize request for use with py-tes.

        Args:
            payload: Request payload.

        Returns:
            Sanitized request payload.
        """
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
                tes.models.Executor(**executor) for executor in payload["executors"]
            ]
        if "logs" in payload:
            payload["logs"] = [tes.models.TaskLog(**log) for log in payload["logs"]]
        return payload

    def _set_projection(self, view: str) -> dict[str, bool]:
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
                "task.logs.system_logs": False,
                "task.logs.logs.stdout": False,
                "task.logs.logs.stderr": False,
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
        self,
        payload: dict[str, Any],
        db_document: DbDocument,
        start_time: str,
        **kwargs: Any,
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
        logs = self._set_logs(payloads=deepcopy(payload), start_time=start_time)
        db_document.task.logs = [TesTaskLog(**logs) for logs in logs]
        db_document.task.state = TesState.UNKNOWN
        db_document.user_id = kwargs.get("user_id")

        (task_id, worker_id) = self._write_doc_to_db(document=db_document)
        db_document.task.id = task_id
        db_document.worker_id = worker_id
        return db_document

    def _set_logs(
        self, payloads: dict[str, Any], start_time: str
    ) -> list[dict[str, Any]]:
        """Create or update `TesTask.logs` and set start time.

        Args:
            payloads: A dictionary containing the payload for the update.
            start_time: The starting time of the incoming TES request.

        Returns:
            Task logs with start time set.
        """
        if "logs" not in payloads:
            logs: list[dict[str, Any]] = [
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
        logs = payloads["logs"]
        return logs

    def _update_doc_in_db(
        self,
        db_connector: DbDocumentConnector,
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

        Raises:
            ValueError: The task has no logs.
        """
        time_now = utc_now()
        tes_endpoint_dict: dict[str, Any] = {
            "host": tes_url,
            "base_path": "",
            "task_id": remote_task_id,
        }
        db_document = db_connector.upsert_fields_in_root_object(
            root="tes_endpoint",
            **tes_endpoint_dict,
        )
        if db_document.task.logs is None:
            raise ValueError(f"Task '{db_document.task.id}' has no logs")
        task_logs = db_document.task.logs
        # updating the end time in TesTask logs
        for logs in task_logs:
            logs.end_time = time_now

        # updating the metadata in TesTask logs
        if self.store_logs:
            db_document = self._update_task_metadata(
                db_document=db_document,
                tes_url=tes_url,
                remote_task_id=remote_task_id,
            )

        db_document = db_connector.upsert_fields_in_root_object(
            root="task",
            **db_document.model_dump(mode="json", exclude_none=True)["task"],
        )
        logger.debug("Task '%s' inserted to database", db_document.task)
        return db_document

    def _update_task_metadata(
        self, db_document: DbDocument, tes_url: str, remote_task_id: str
    ) -> DbDocument:
        """Update the task metadata.

        Append the TES endpoint, the remote task identifier and the time of
        forwarding to the forwards in `TesTask.logs.metadata.forwarded_to`.

        Args:
            db_document: The document in the database to be updated.
            tes_url: The TES URL where the task if forwarded.
            remote_task_id: Task identifier at the remote TES instance.

        Returns:
            The updated database document.

        Raises:
            ValueError: The task has no logs.
        """
        if db_document.task.logs is None:
            raise ValueError(f"Task '{db_document.task.id}' has no logs")
        logs: list[TesTaskLog] = db_document.task.logs
        forward = TesForward(
            id=remote_task_id,
            url=tes_url,
            forward_time=utc_now(),
        )
        for log in logs:
            if log.metadata is None:
                log.metadata = Metadata()
            if log.metadata.forwarded_to is None:
                log.metadata.forwarded_to = []
            log.metadata.forwarded_to.append(forward)
        return db_document

    def _page_size(self, page_size: int | None) -> int:
        """Get the size of a page of tasks.

        Args:
            page_size: Page size of the request, if any.

        Returns:
            The page size of the request, at most the maximum page size of
            the config; the default page size of the config if none is
            requested.

        Raises:
            pro_tes.exceptions.BadRequest: The page size is not positive.
        """
        config = self.custom_config.controllers.list_tasks
        if page_size is None:
            return config.default_page_size
        if page_size < 1:
            raise BadRequest(f"Page size must be positive: {page_size}")
        return min(page_size, config.max_page_size)

    @staticmethod
    def _strip_credentials(task: tes.Task) -> None:
        """Remove user names and passwords from the URLs of inputs and outputs.

        Inputs and outputs are kept otherwise, also inputs without URL
        (with `content`).

        Args:
            task: Task to be relayed; changed in place.
        """
        for item in [*(task.inputs or []), *(task.outputs or [])]:
            if item is not None and item.url is not None:
                item.url = strip_auth(item.url)

    @staticmethod
    def _remote_task_id(db_document: DbDocument) -> str:
        """Get the identifier of a task at the TES backend it was relayed to.

        Tasks stored by earlier versions have it only in the last forward of
        their logs.

        Args:
            db_document: Stored task.

        Returns:
            The identifier of the task at its TES backend.

        Raises:
            ValueError: The task has no such identifier.
        """
        if db_document.tes_endpoint.task_id is not None:
            return db_document.tes_endpoint.task_id
        logs = db_document.task.logs or []
        metadata = logs[0].metadata if logs else None
        if metadata is None or not metadata.forwarded_to:
            raise ValueError(f"Task '{db_document.task.id}' was not forwarded")
        # the TES server that proTES forwarded the task to
        return metadata.forwarded_to[-1].id

    @staticmethod
    def parse_basic_auth(auth: Authorization | None) -> BasicAuth:
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
