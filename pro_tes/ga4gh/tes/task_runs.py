"""Class implementing TES API-server-side controller methods."""

from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
import logging
import re
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from celery import uuid
from foca import get_collection, get_custom_config
from foca.plugins import (
    Attempt,
    BackendResponse,
    GatewayError,
    InboundRequest,
    NoBackendAvailable,
    Principal,
)
from foca.plugins.runtime import get_host
from foca.utils.misc import generate_id
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
import requests
from tes.models import Task

from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import (
    BackendAuthError,
    BackendOutcomeUnknown,
    BadRequest,
    TaskNotCancelable,
    TaskNotFound,
)
from pro_tes.ga4gh.tes.backends import (
    TesClient,
    client_for,
    create_client,
    forward_reason,
    get_backends,
)
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
from pro_tes.gateway import (
    check_inline_content,
    handle_url_credentials,
    relayed_task,
    validate_task,
)
from pro_tes.tasks.track_task_progress import task__track_task_progress
from pro_tes.utils.db import TASK_COLLECTION, TASK_DB, DbDocumentConnector
from pro_tes.utils.misc import utc_now
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
        self.custom_config: CustomConfig = get_custom_config(CustomConfig)
        self.db_client: Collection[dict[str, Any]] = get_collection(
            TASK_DB, TASK_COLLECTION
        )
        self.store_logs = self.custom_config.storeLogs.execution_trace

    def create_task(
        self,
        task: dict[str, Any] | None,
        collection: str,
        inbound: InboundRequest,
        principal: Principal,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> tuple[Any, dict[str, str]]:
        """Start task.

        The user of the request becomes the owner of the task (cf.
        :py:mod:`pro_tes.ga4gh.tes.ownership`). The task is checked, then
        the plugin pipeline of the request runs (cf.
        :py:mod:`pro_tes.gateway`): its `request` phase may change the task,
        and its `route` phase chooses the TES backends, in order, from
        `custom.tes.backends`. Tasks that fail validation, are rejected by a
        plugin, or have no backend are not stored.

        The task is then stored, and relayed to the backends in turn, after
        the `forward` phase of each attempt, until one accepts the task. It
        is relayed to the next one only if a backend certainly did not
        accept it, never if it may have (cf.
        :py:class:`pro_tes.ga4gh.tes.backends.TesClient`). Tasks that no
        backend accepts are not kept: they are deleted, with the owner's
        roles on them. Tasks that a backend may have accepted are kept (as
        `SYSTEM_ERROR`).

        Args:
            task: Task of the request (its JSON body); not changed.
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`), the resource of the access control
                policies of the task's owner.
            inbound: Inbound request (cf.
                :py:func:`foca.plugins.adapter.inbound_from_request`); its
                payload is replaced by the checked task.
            principal: Caller of the request (cf.
                :py:func:`foca.plugins.adapter.current_principal`).
            request_id: Identifier of the request, for logs.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Response to the client: by default, the task identifier; and its
            headers (none by default; plugins may change both in the
            `response` phase).

        Raises:
            foca.plugins.GatewayError: The pipeline run failed (e.g., a
                plugin rejected the task, a step failed, the tags choose an
                invalid pipeline or backends, the request passed proTES
                before), or no backend accepted the task (in time); in the
                latter case, the task is not kept.
            pro_tes.exceptions.BackendOutcomeUnknown: A TES instance may
                have accepted the task, but did not confirm it; the task is
                stored with the state `SYSTEM_ERROR`, and with the TES
                instance, without remote identifier.
            pro_tes.exceptions.BadRequest: The request has no JSON body,
                or the task is invalid.
            pro_tes.exceptions.InlineContentTooLarge: The inline contents of
                the inputs of the task exceed the limit, also after a plugin
                changed the task.
            pro_tes.exceptions.Unauthorized: The user of the request could
                not be identified.
        """
        # create task document
        start_time = utc_now()
        db_document: DbDocument = DbDocument()
        if task is None:
            raise BadRequest("Request body is missing")
        owner = get_owner()
        payload_original: dict[str, Any] = deepcopy(task)
        # credentials in URLs are neither stored nor forwarded; removed before
        # the plugins, so that they don't get them either
        handle_url_credentials(
            payload=payload_original,
            mode=self.custom_config.controllers.post_task.url_credentials,
        )
        max_inline_content = self.custom_config.controllers.post_task.max_inline_content
        check_inline_content(payload=payload_original, limit=max_inline_content)
        db_document.task_original = TesTask(**payload_original)
        try:
            validate_task(payload_original)
        except (TypeError, ValueError) as exc:
            raise BadRequest(
                f"Task could not be validated. Original error message:"
                f" '{type(exc).__name__}: {exc}'"
            ) from exc

        # run the plugins of the request and route phases; the engine
        # sanitizes and validates the task after each plugin that changed it
        host = get_host()
        run = host.start(
            replace(inbound, payload=deepcopy(payload_original)),
            principal,
            request_id=request_id,
        )
        payload: dict[str, Any] = run.request().payload
        candidates = run.route(get_backends())
        # before the task is stored: no task is left
        if not candidates:
            run.final_reason = "no_backend"
            raise NoBackendAvailable("No TES backend available", run=run)

        # update task document; set by the server (TES), not by clients
        payload = deepcopy(payload)
        payload["creation_time"] = start_time
        db_document.task = TesTask(**payload)

        # create database document
        db_document = self._update_task(
            db_document=db_document,
            start_time=start_time,
            owner=owner,
            collection=collection,
        )
        run.task_id = db_document.task.id
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

        # relay request
        logger.info(
            "Attempting to forward task '%s' to the TES instances, in the"
            " following order: %s",
            db_document.task.id,
            [backend.id for backend in candidates],
        )
        prefix = host.conf.tags.prefix
        accepted: tuple[Attempt, TesClient, BackendResponse, str] | None = None
        try:
            for attempt in run.attempts(candidates):
                tes_url = attempt.backend.url
                db_document.tes_endpoint = TesEndpoint(host=tes_url)
                try:
                    cli = client_for(attempt.backend)
                    remote_task_id, response = cli.forward(
                        attempt.outbound,
                        task=relayed_task(attempt.outbound.payload, prefix=prefix),
                        connect_timeout=attempt.remaining(),
                    )
                except BackendOutcomeUnknown as exc:
                    # the backend may run the task, so it is not sent elsewhere
                    attempt.unknown(exc.reason)
                    logger.error(
                        "Task '%s' may or may not have been accepted by TES"
                        " endpoint hosted at: %s; not sent to other TES"
                        " endpoints. Original error message: '%s: %s'",
                        db_document.task.id,
                        tes_url,
                        type(exc).__name__,
                        exc,
                    )
                    db_connector.upsert_fields_in_root_object(
                        root="tes_endpoint", host=tes_url, base_path=""
                    )
                    db_connector.update_task_state(state=TesState.SYSTEM_ERROR.value)
                    raise
                except (
                    requests.RequestException,
                    BackendAuthError,
                    ValueError,
                ) as exc:
                    # the backend did not accept the task (cf. `TesClient`),
                    # or has no client (e.g., it is no longer configured,
                    # `BackendNotConfigured`)
                    attempt.failed(forward_reason(exc))
                    logger.warning(
                        "Task '%s' could not be sent to TES endpoint hosted at:"
                        " %s. Original error message: '%s: %s'",
                        db_document.task.id,
                        tes_url,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                accepted = (attempt, cli, response, remote_task_id)
                break
        except GatewayError:
            # no backend accepted the task (in time), or a plugin of the
            # `forward` phase rejected it
            self._discard_task(
                db_document=db_document, owner=owner, collection=collection
            )
            raise
        if accepted is None:
            # the attempts end with a success, or raise
            raise RuntimeError("Forwarding ended without outcome")
        attempt, cli, response, remote_task_id = accepted
        return self._forwarded(
            attempt=attempt,
            cli=cli,
            response=response,
            db_document=db_document,
            db_connector=db_connector,
            remote_task_id=remote_task_id,
        )

    def _forwarded(
        self,
        attempt: Attempt,
        cli: TesClient,
        response: BackendResponse,
        db_document: DbDocument,
        db_connector: DbDocumentConnector,
        remote_task_id: str,
    ) -> tuple[Any, dict[str, str]]:
        """Store and track a task that a backend accepted.

        Args:
            attempt: The attempt of the backend.
            cli: Client of the backend.
            response: Response of the backend.
            db_document: The stored task.
            db_connector: Connector of the stored task.
            remote_task_id: Identifier of the task at the backend.

        Returns:
            Response to the client and its headers, after the `response`
            phase.
        """
        tes_url = attempt.backend.url
        logger.info(
            "Task '%s' successfully forwarded to TES endpoint hosted at:"
            " %s. Remote task identifier: %s",
            db_document.task.id,
            tes_url,
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
            remote_task: Task = cli.get_task(remote_task_id)
            task_model_converter = TaskModelConverter(task=remote_task)
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
                tes_url,
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
        response.client_payload = {"id": db_document.task.id}
        response = attempt.succeeded(response, remote_id=remote_task_id)
        return response.client_payload, dict(response.client_headers)

    def list_tasks(self, collection: str, **kwargs: Any) -> dict[str, Any]:
        """Return list of the tasks of the user of the request.

        Args:
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`), the resource of the access control
                policies of their owners.
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
        filter_dict: dict[str, Any] = {**owned_tasks_filter(collection=collection)}

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
        and tasks being canceled are left as they are, also if their state
        changed while they were canceled (e.g., a final state stored by
        their tracking).

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
            # unless tracking stored a final state meanwhile: the backend
            # may accept cancels of finished tasks (as no-ops); a stored
            # `CANCELING` (the backend confirmed the cancel) becomes
            # `CANCELED`, which tracking keeps until the task is final
            if db_connector.update_task_state(
                state="CANCELED",
                if_state=States.TO_CANCEL + States.CANCELING,
            ):
                logger.info(
                    "Task '%s' with worker ID '%s' canceled.",
                    id,
                    db_document.worker_id,
                )
            else:
                logger.info(
                    "Task '%s' with worker ID '%s' canceled at its TES"
                    " instance; its stored state is kept, as it changed"
                    " meanwhile.",
                    id,
                    db_document.worker_id,
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
        access (cf. :py:meth:`_write_doc_to_db`). Errors are logged, not
        raised, so that the error of the request is raised; tasks that
        could not be deleted are marked as `SYSTEM_ERROR`, if possible.

        Args:
            db_document: The stored task.
            owner: Owner of the task.
            collection: Request path of the tasks (e.g.,
                `/ga4gh/tes/v1/tasks`).
        """
        try:
            self.db_client.delete_one({"worker_id": db_document.worker_id})
            remove_task_owner(
                collection=collection, task_id=str(db_document.task.id), owner=owner
            )
        except Exception:
            logger.exception("Task '%s' could not be discarded", db_document.task.id)
            with suppress(Exception):
                self.db_client.update_one(
                    {"worker_id": db_document.worker_id},
                    {"$set": {"task.state": TesState.SYSTEM_ERROR.value}},
                )
            return
        logger.info("Task '%s' not kept: it was not forwarded", db_document.task.id)

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
                "task_original": False,
                "tes_endpoint": False,
            }
        elif view == "FULL":
            projection = {
                "task_original": False,
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
