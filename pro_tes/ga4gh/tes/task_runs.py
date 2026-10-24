"""Class implementing TES API-server-side controller methods."""

from copy import deepcopy
import logging
import re
from typing import Any, Literal

from bson.errors import InvalidId
from bson.objectid import ObjectId
from celery import uuid
from flask import request
from foca import get_collection
from foca.utils.misc import generate_id
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
import requests
import tes
from tes.models import Task

from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import (
    BackendAuthError,
    BackendOutcomeUnknown,
    BadRequest,
    InlineContentTooLarge,
    MiddlewareException,
    NoTesInstancesAvailable,
    TaskNotCancelable,
    TaskNotFound,
)
from pro_tes.ga4gh.tes.backends import create_client
from pro_tes.ga4gh.tes.models import (
    DbDocument,
    Metadata,
    TesEndpoint,
    TesForward,
    TesState,
    TesTask,
    TesTaskLog,
)
from pro_tes.ga4gh.tes.ownership import (
    add_task_owner,
    get_owner,
    owned_tasks_filter,
    remove_task_owner,
    task_has_roles,
)
from pro_tes.ga4gh.tes.states import States
from pro_tes.middleware.middleware_handler import MiddlewareHandler
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import TASK_COLLECTION, TASK_DB, DbDocumentConnector
from pro_tes.utils.misc import (
    get_custom_config,
    has_credentials,
    strip_auth,
    utc_now,
)
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)

# view of `GET /tasks` and `GET /tasks/{id}` if none is given, as in the spec
DEFAULT_VIEW = "MINIMAL"


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
            TASK_DB, TASK_COLLECTION
        )
        self.store_logs = self.custom_config.storeLogs.execution_trace

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        """Start task.

        The user of the request becomes the owner of the task (cf.
        :py:mod:`pro_tes.ga4gh.tes.ownership`). Tasks that fail validation,
        and tasks that no TES instance accepts, are not kept: they are
        deleted, with the owner's roles on them. Tasks that a TES instance
        may have accepted are kept (as `SYSTEM_ERROR`).

        Tasks are sent to the TES instances in turn, until one accepts the
        task. They are sent to the next one only if a TES instance certainly
        did not accept the task, never if it may have (cf.
        :py:class:`pro_tes.ga4gh.tes.backends.TesClient`).

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.

        Raises:
            pro_tes.exceptions.BackendOutcomeUnknown: A TES instance may
                have accepted the task, but did not confirm it; the task is
                stored with the state `SYSTEM_ERROR`, and with the TES
                instance, without remote identifier.
            pro_tes.exceptions.BadRequest: The request has no JSON body.
            pro_tes.exceptions.MiddlewareException: The middlewares removed
                the JSON body.
            pro_tes.exceptions.NoTesInstancesAvailable: No TES instance
                accepted the task.
            pro_tes.exceptions.Unauthorized: The user of the request could
                not be identified.
        """
        # create task document
        start_time = utc_now()
        db_document: DbDocument = DbDocument()
        if request.json is None:
            raise BadRequest("Request body is missing")
        owner = get_owner()
        # request path of the tasks, before middlewares
        collection = request.path
        # credentials in URLs are neither stored nor forwarded; removed from
        # the request itself, so that middlewares don't get them either
        self._handle_url_credentials(
            payload=request.json,
            mode=self.custom_config.controllers.post_task.url_credentials,
        )
        max_inline_content = self.custom_config.controllers.post_task.max_inline_content
        self._check_inline_content(payload=request.json, limit=max_inline_content)
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
        # also credentials and contents that middlewares added
        self._handle_url_credentials(payload=payload, mode="strip")
        self._check_inline_content(payload=payload, limit=max_inline_content)
        tes_urls = deepcopy(payload["tes_urls"])
        del payload["tes_urls"]
        # set by the server (TES), not by clients
        payload["creation_time"] = start_time
        db_document.task = TesTask(**payload)

        # create database document
        db_document = self._update_task(
            db_document=db_document,
            start_time=start_time,
            owner=owner,
            collection=collection,
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

        # validate request; the TES instance sets its own creation time and
        # logs (TES servers may reject tasks that set them, as proTES does);
        # clients can't set them, but middlewares could
        for field in ("creation_time", "logs"):
            payload.pop(field, None)
        payload = self._sanitize_request(payload=payload)
        try:
            payload_marshalled = tes.Task(**payload)
        except TypeError as exc:
            self._discard_task(
                db_document=db_document, owner=owner, collection=collection
            )
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
                cli = create_client(
                    host=db_document.tes_endpoint.host,
                    base_path=db_document.tes_endpoint.base_path,
                )
            except ValueError as exc:
                # also backends that are not configured (e.g., added by
                # middlewares): proTES has no credentials for them
                logger.warning(
                    "Task '%s' could not be sent to TES endpoint hosted at: %s."
                    " Invalid or unknown TES endpoint URL. Original error"
                    " message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
                )
                continue

            task_relayed = self._relayed_task(task=payload_marshalled)
            try:
                remote_task_id = cli.create_task(task_relayed)
            except BackendOutcomeUnknown as exc:
                # the backend may run the task, so it is not sent elsewhere
                logger.error(
                    "Task '%s' may or may not have been accepted by TES endpoint"
                    " hosted at: %s; not sent to other TES endpoints. Original"
                    " error message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
                )
                db_connector.upsert_fields_in_root_object(
                    root="tes_endpoint", host=tes_url, base_path=""
                )
                db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
                raise
            except (requests.RequestException, BackendAuthError) as exc:
                # the backend did not accept the task (cf. `TesClient`)
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
            # the task is stored with its backend first, so that it is
            # cancelable even if the rest fails (e.g., the instance stops)
            db_document = self._update_doc_in_db(
                db_connector=db_connector,
                tes_url=tes_url,
                remote_task_id=remote_task_id,
            )
            try:
                task: Task = cli.get_task(remote_task_id)
                task_model_converter = TaskModelConverter(task=task)
                task_converted: TesTask = task_model_converter.convert_task()
                state = TesState(task_converted.state)
                # unless a request to cancel the task changed it meanwhile
                if db_connector.update_task_state(
                    state=state.value, if_state=TesState.UNKNOWN.value
                ):
                    db_document.task.state = state
            except Exception as exc:
                # the task is tracked anyway, also if its backend serves it
                # invalid (e.g., with an unknown state)
                logger.error(
                    "Task '%s' info could not be retrieved from TES endpoint"
                    " hosted at: %s. Original error message: '%s: %s'",
                    db_document.task.id,
                    url,
                    type(exc).__name__,
                    exc,
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
            return {"id": db_document.task.id}

        self._discard_task(db_document=db_document, owner=owner, collection=collection)
        raise NoTesInstancesAvailable(
            "Could not forward the task request to any TES instance; the task"
            " was not kept."
        )

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        """Return list of the tasks of the user of the request.

        Args:
            **kwargs: Keyword arguments passed along with request.

        Returns:
            Response object according to TES API schema . Cf.
            https://github.com/ga4gh/task-execution-schemas/blob/9e9c5aa2648d683d5574f9dbd63a025b4aea285d/openapi/task_execution_service.openapi.yaml;
            only the tasks that the user owns are listed (also for admins),
            newest first, and `next_page_token` is empty on the last page.

        Raises:
            pro_tes.exceptions.BadRequest: The page size is not positive, or
                the page token is invalid.
        """
        view = kwargs.get("view", DEFAULT_VIEW)
        page_size = self._page_size(page_size=kwargs.get("page_size"), view=view)
        filter_dict: dict[str, Any] = {**owned_tasks_filter(collection=request.path)}

        # an empty token (of the last page) is taken as no token
        page_token = kwargs.get("page_token")
        if page_token:
            try:
                filter_dict["_id"] = {"$lt": ObjectId(page_token)}
            except (InvalidId, TypeError) as exc:
                raise BadRequest(f"Invalid page token: {page_token!r}") from exc
        projection = self._set_projection(view=view)

        # matched literally, as a prefix
        name_prefix = kwargs.get("name_prefix")
        if name_prefix is not None:
            # MongoDB rejects regular expressions with null characters
            if "\x00" in name_prefix:
                raise BadRequest("Invalid name prefix: null character")
            pattern = "^" + re.escape(name_prefix)
            filter_dict["task_original.name"] = {"$regex": pattern}

        # one more task than the page size tells whether there is a next
        # page; documents are processed one by one, as the cursor yields them
        cursor = (
            self.db_client.find(filter=filter_dict, projection=projection)
            .sort("_id", -1)
            .limit(page_size + 1)
        )
        tasks: list[dict[str, Any]] = []
        next_page_token = ""
        last_id: Any = None
        for document in cursor:
            if len(tasks) == page_size:
                next_page_token = str(last_id)
                break
            last_id = document["_id"]
            if view == "MINIMAL":
                tasks.append(
                    {"id": document["task"]["id"], "state": document["task"]["state"]}
                )
            else:
                tasks.append(flatten_forwards(document["task"]))
        cursor.close()
        logger.debug("Listed %s tasks", len(tasks))
        return {"next_page_token": next_page_token, "tasks": tasks}

    def get_task(self, id: str, **kwargs: Any) -> dict[str, Any]:
        """Return detailed information about a task.

        Only owners of the task may get it (enforced by the policies).

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
        projection = self._set_projection(view=kwargs.get("view", DEFAULT_VIEW))
        document = self.db_client.find_one(
            filter={"task.id": id}, projection=projection
        )
        if document is None:
            logger.error("Task '%s' not found.", id)
            raise TaskNotFound
        return flatten_forwards(document["task"])

    def cancel_task(self, id: str, **kwargs: Any) -> dict[str, Any]:
        """Cancel task.

        Only owners of the task may cancel it (enforced by the policies).
        Tasks that have not finished (also those in state `UNKNOWN`, e.g.,
        not tracked yet) are canceled at their TES instance; finished tasks
        and tasks being canceled are left as they are.

        Args:
            id: Task identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Task identifier.

        Raises:
            pro_tes.exceptions.BackendAuthError: No credentials for the TES
                instance of the task are available.
            pro_tes.exceptions.BackendNotConfigured: The TES instance of the
                task is no longer configured.
            pro_tes.exceptions.BackendOutcomeUnknown: The TES instance may
                have canceled the task, but did not confirm it.
            pro_tes.exceptions.TaskNotCancelable: The task has not
                finished, but its identifier at its TES instance is unknown
                (e.g., it was not forwarded yet).
            pro_tes.exceptions.TaskNotFound: The requested task is not
                available.
            requests.RequestException: The TES instance was not reached, or
                rejected the request.
        """
        document, db_document = self._find_task(id=id)
        task_id = self._remote_task_id(document=document, db_document=db_document)
        if db_document.task.state in States.TO_CANCEL and task_id is None:
            # forwarding may have stored it since
            document, db_document = self._find_task(id=id)
            task_id = self._remote_task_id(document=document, db_document=db_document)

        if db_document.task.state in States.TO_CANCEL:
            db_connector = DbDocumentConnector(
                collection=self.db_client,
                worker_id=db_document.worker_id,
            )
            url: str = (
                f"{db_document.tes_endpoint.host.rstrip('/')}/"
                f"{db_document.tes_endpoint.base_path.strip('/')}"
            )

            if task_id is None:
                # e.g., tasks that are being forwarded, or that a TES
                # instance may have accepted without confirming it
                raise TaskNotCancelable(
                    f"Task '{id}' can't be canceled: its identifier at its TES"
                    " instance is unknown"
                )

            logger.info(
                "Trying to cancel task with task identifier '%s' and worker job"
                " identifier '%s' running at TES endpoint hosted at: %s",
                task_id,
                db_document.worker_id,
                url,
            )
            cli = create_client(
                host=db_document.tes_endpoint.host,
                base_path=db_document.tes_endpoint.base_path,
            )
            cli.cancel_task(task_id=task_id)
            db_connector.update_task_state(
                state="CANCELED",
            )
            logger.info(
                "Task '%s' with worker ID '%s' canceled.", id, db_document.worker_id
            )
        return {}

    def _find_task(self, id: str) -> tuple[dict[str, Any], DbDocument]:
        """Get a stored task.

        Args:
            id: Task identifier.

        Returns:
            The task as stored, and as model.

        Raises:
            pro_tes.exceptions.TaskNotFound: The task is not available.
        """
        document = self.db_client.find_one(
            filter={"task.id": id},
            projection={"_id": False},
        )
        if document is None:
            logger.error("Task '%s' not found.", id)
            raise TaskNotFound
        return document, DbDocument(**document)

    def _write_doc_to_db(
        self,
        document: DbDocument,
        owner: str,
        collection: str,
    ) -> tuple[str, str]:
        """Create database entry for task, and make the owner its owner.

        Identifiers of stored tasks, and identifiers that roles are granted
        on (e.g., left by deleted tasks), are not used again, so that nobody
        gets access to the task of someone else. Concurrent requests may
        still draw the same unused identifier: the unique index on task
        identifiers (cf. `db` in `config.yaml`) lets only one of them store
        its task, and the others try again with another identifier. The
        owner is granted the role only once the task is stored, so that
        requests losing the race never get a role on the task of someone
        else; if the role can't be granted, the task is deleted, and roles
        granted partly are revoked.

        Args:
            document: Document to be written to database.
            owner: Owner of the task (cf.
                :py:func:`pro_tes.ga4gh.tes.ownership.get_owner`).
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`).

        Returns:
            Tuple of task id and worker id.

        Raises:
            pymongo.errors.DuplicateKeyError: No unused task identifier was
                found.
        """
        controller_config = self.custom_config.controllers.post_task
        charset = controller_config.task_id.charset
        length = controller_config.task_id.length

        # try inserting until unused task id found
        for _ in range(controller_config.db.insert_attempts):
            task_id = generate_id(
                charset=charset,
                length=length,
            )
            if self.db_client.count_documents(
                {"task.id": task_id}, limit=1
            ) or task_has_roles(collection=collection, task_id=task_id):
                continue
            document.task.id = task_id
            document.worker_id = uuid()
            try:
                self.db_client.insert_one(
                    document.model_dump(mode="json", exclude_none=True)
                )
            except DuplicateKeyError:
                # another request stored a task with the identifier first
                continue
            try:
                add_task_owner(collection=collection, task_id=task_id, owner=owner)
            except Exception:
                # deleted first: roles left on deleted tasks give no access
                self.db_client.delete_one({"worker_id": document.worker_id})
                remove_task_owner(collection=collection, task_id=task_id, owner=owner)
                raise
            return task_id, document.worker_id
        raise DuplicateKeyError("Could not find an unused task identifier")

    def _discard_task(
        self, db_document: DbDocument, owner: str, collection: str
    ) -> None:
        """Delete a stored task that was not forwarded, with its owner roles.

        The task is deleted first, as roles left on deleted tasks give no
        access (cf. :py:meth:`_write_doc_to_db`).

        Args:
            db_document: The stored task.
            owner: Owner of the task.
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`).
        """
        self.db_client.delete_one({"worker_id": db_document.worker_id})
        remove_task_owner(
            collection=collection, task_id=str(db_document.task.id), owner=owner
        )
        logger.info("Task '%s' not kept: it was not forwarded", db_document.task.id)

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
        db_document: DbDocument,
        start_time: str,
        owner: str,
        collection: str,
    ) -> DbDocument:
        """Update the task object, store it, and make the owner its owner.

        Args:
            db_document: The document in the database to be updated.
            start_time: The starting time of the incoming TES request.
            owner: Owner of the task (cf.
                :py:func:`pro_tes.ga4gh.tes.ownership.get_owner`).
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`).

        Returns:
            DbDocument: The updated database document.
        """
        logs = self._set_logs(start_time=start_time)
        db_document.task.logs = [TesTaskLog(**log) for log in logs]
        db_document.task.state = TesState.UNKNOWN
        db_document.user_id = owner

        (task_id, worker_id) = self._write_doc_to_db(
            document=db_document, owner=owner, collection=collection
        )
        db_document.task.id = task_id
        db_document.worker_id = worker_id
        return db_document

    @staticmethod
    def _set_logs(start_time: str) -> list[dict[str, Any]]:
        """Create the logs of a task (`TesTask.logs`).

        Args:
            start_time: The starting time of the incoming TES request.

        Returns:
            Task logs with start time set.
        """
        return [
            {
                "logs": [],
                "metadata": {},
                "start_time": start_time,
                "end_time": None,
                "outputs": [],
                "system_logs": [],
            }
        ]

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

        # without the state, which a request to cancel the task may have
        # changed in the meantime
        task = db_document.model_dump(mode="json", exclude_none=True)["task"]
        task.pop("state", None)
        db_document = db_connector.upsert_fields_in_root_object(root="task", **task)
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

    def _page_size(self, page_size: int | None, view: str = DEFAULT_VIEW) -> int:
        """Get the size of a page of tasks.

        Args:
            page_size: Page size of the request, if any.
            view: View of the tasks.

        Returns:
            The page size of the request, or the default page size of the
            config if none is requested; at most the maximum page size of
            the config, of the view `FULL` for that view.

        Raises:
            pro_tes.exceptions.BadRequest: The page size is not positive.
        """
        config = self.custom_config.controllers.list_tasks
        maximum = config.max_page_size
        if view == "FULL":
            maximum = min(maximum, config.max_page_size_full)
        if page_size is None:
            return min(config.default_page_size, maximum)
        if page_size < 1:
            raise BadRequest(f"Page size must be positive: {page_size}")
        return min(page_size, maximum)

    @staticmethod
    def _handle_url_credentials(
        payload: dict[str, Any], mode: Literal["reject", "strip"]
    ) -> None:
        """Reject or remove credentials in the URLs of inputs and outputs.

        Credentials are user names and passwords (user information, e.g.,
        `https://user:password@host/path`). Inputs and outputs are kept
        otherwise, also inputs without URL (with `content`); malformed ones
        are left to the validation of the task.

        Args:
            payload: Task (JSON); changed in place.
            mode: Whether tasks with credentials are rejected (`reject`), or
                the credentials are removed (`strip`).

        Raises:
            pro_tes.exceptions.BadRequest: A URL has credentials, and `mode`
                is `reject`; or a URL is malformed.
        """
        for field in ("inputs", "outputs"):
            items = payload.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                url = item.get("url") if isinstance(item, dict) else None
                if not isinstance(url, str):
                    continue
                try:
                    found = has_credentials(url)
                except ValueError as exc:
                    raise BadRequest(f"Malformed URL in `{field}`") from exc
                if not found:
                    continue
                if mode == "reject":
                    raise BadRequest(
                        f"URLs in `{field}` must not have credentials (user"
                        " names or passwords)"
                    )
                item["url"] = strip_auth(url)

    @staticmethod
    def _check_inline_content(payload: dict[str, Any], limit: int) -> None:
        """Check the size of the inline contents of the inputs of a task.

        Args:
            payload: Task (JSON).
            limit: Maximum size of the contents (`inputs[].content`),
                together, in bytes (UTF-8).

        Raises:
            pro_tes.exceptions.InlineContentTooLarge: The contents are
                larger.
        """
        inputs = payload.get("inputs")
        if not isinstance(inputs, list):
            return
        size = sum(
            len(item["content"].encode("utf-8", errors="surrogatepass"))
            for item in inputs
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        )
        if size > limit:
            raise InlineContentTooLarge(
                f"Inline contents of inputs (`inputs[].content`) exceed {limit}"
                f" bytes: {size}"
            )

    @staticmethod
    def _relayed_task(task: tes.Task) -> tes.Task:
        """Get a task as it is relayed to a TES backend.

        Each backend gets a copy of the task as validated, without changes
        made for other backends. Changes of tasks for their backends belong
        here, e.g., tags with the identity of the user (planned, but not
        decided yet).

        Args:
            task: Task, as validated.

        Returns:
            The task to be relayed.
        """
        return deepcopy(task)

    @staticmethod
    def _remote_task_id(
        document: dict[str, Any], db_document: DbDocument
    ) -> str | None:
        """Get the identifier of a task at the TES backend it was relayed to.

        Tasks stored by earlier versions have it only in their logs: in the
        last forward with an execution trace, else as `remote_task_id`.

        Args:
            document: Stored task, as stored.
            db_document: Stored task, as model.

        Returns:
            The identifier of the task at its TES backend, or `None` if the
            task was not forwarded.
        """
        if db_document.tes_endpoint.task_id is not None:
            return db_document.tes_endpoint.task_id
        logs = db_document.task.logs or []
        metadata = logs[0].metadata if logs else None
        if metadata is not None and metadata.forwarded_to:
            # the TES server that proTES forwarded the task to
            return metadata.forwarded_to[-1].id
        # the metadata model drops `remote_task_id`, so it is read as stored
        stored_logs = document.get("task", {}).get("logs") or [{}]
        stored_metadata = stored_logs[0].get("metadata") or {}
        remote_task_id = stored_metadata.get("remote_task_id")
        return remote_task_id if isinstance(remote_task_id, str) else None
