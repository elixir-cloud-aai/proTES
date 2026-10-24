# API

proTES implements the [GA4GH Task Execution Service (TES)
API](https://github.com/ga4gh/task-execution-schemas), version 1.0
([specification](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/api/9e9c5aa.task_execution_service.openapi.yaml)),
at `/ga4gh/tes/v1/`. A deployment's Swagger UI (`/ga4gh/tes/v1/ui/`) shows
the endpoints and lets you try them; the specification is served at
`/ga4gh/tes/v1/openapi.json`. All endpoints except `GET /service-info`, the
specification and the Swagger UI require an access token (see [Access
control](access-control.md)).

| Endpoint | Description |
| --- | --- |
| `GET /service-info` | The service info of the deployment (see [Service info](configuration.md#service-info)). |
| `POST /tasks` | Create a task: proTES forwards it to a TES instance (see [Tasks](#tasks)) and returns its own identifier of the task. |
| `GET /tasks` | List the caller's tasks, newest first, one page at a time (`page_size`, at most `max_page_size` of the [config](configuration.md#tasks); `page_token` from `next_page_token` of the previous page, which is empty on the last page), optionally only those whose name starts with `name_prefix` (taken literally). |
| `GET /tasks/{id}` | Get a task of the caller, with its state as last tracked from the TES instance. |
| `POST /tasks/{id}:cancel` | Cancel a task of the caller that has not finished yet (also in state `UNKNOWN`, e.g., not tracked yet), at the TES instance that runs it; finished tasks are left as they are. Unfinished tasks whose identifier at their TES instance is unknown (e.g., while they are being forwarded, or if forwarding failed unexpectedly) can't be canceled (`409 Conflict`). |

Only the owner of a task, the user who created it, can read and cancel it
(see [Task ownership](access-control.md#task-ownership)). Admins can manage
the access control rules at runtime through FOCA's permissions API at
`/admin/access-control/permissions` (see [Access
control](access-control.md#permissions-api)).

`GET /tasks` and `GET /tasks/{id}` return the views `MINIMAL` (the default;
identifier and state), `BASIC` (without the contents of inputs, the system
logs and the executors' standard output and error) and `FULL` (`view`), as
the TES specification describes. Times (`creation_time`, and `start_time`
and `end_time` of proTES's log of a task) are in UTC, in RFC 3339 format.

## Tasks

For `POST /tasks`, proTES:

1. stores the task as received, with a new identifier
   (see [Tasks](configuration.md#tasks) for its format) and its
   `creation_time` (tasks that set `creation_time`, which the TES
   specification marks read-only, are rejected with `400 Bad Request`, as
   are tasks with credentials in the URLs of inputs or outputs, unless
   proTES is configured to remove them: see [Tasks](configuration.md#tasks));
2. applies the [middlewares](middlewares.md), which select the TES instances
   to try and their order (and may change the task);
3. forwards the task to the first of these TES instances that accepts it,
   and returns its own identifier of the task; if no TES instance accepts
   it, the task's state is set to `SYSTEM_ERROR`, and the request fails with
   `500 Internal Server Error`. Each TES instance gets its own copy of the
   validated task, with the credentials of proTES for it (see [Credentials
   of TES instances](access-control.md#credentials-of-tes-instances)), in a
   single request; the next TES instance is tried only if a TES instance
   certainly did not accept the task (see [TES
   instances](configuration.md#tes-instances)). If a TES instance may have
   accepted it without confirming it (e.g., the response timed out), the
   task is not sent to other TES instances, its state is set to
   `SYSTEM_ERROR`, and the request fails with `502 Bad Gateway`: the task
   may run at that TES instance, but proTES can neither track nor cancel
   it. Once a TES instance accepted the task, proTES stores the task with
   its TES instance and the remote identifier, and then with the state that
   the TES instance reports for it (it stays `UNKNOWN` if the state can't
   be got);
4. tracks the task at the TES instance in the background (a Celery worker
   polls its state until it has finished; see [Tasks](configuration.md#tasks)),
   and updates its state and logs.

proTES keeps the task as received (`task_original`) as well as the task as
forwarded, and, if `custom.storeLogs.execution_trace` is set, records the TES
instance and the remote identifier of the task in its logs
(`logs[].metadata.forwarded_to`).

## Errors

Errors are [problem details](https://www.rfc-editor.org/rfc/rfc9457) (with
`title`, `status` and `detail`), sent as `application/json`, as the GA4GH
specification declares: `400 Bad Request` for invalid requests (e.g.,
invalid tasks or query parameters), `401 Unauthorized` for requests
without a valid access token (with a Bearer challenge), `403 Forbidden` for
users without the required role and for tasks of others or unknown tasks
(see [Access control](access-control.md)), `404 Not Found` for tasks of the
caller deleted from the database, `409 Conflict` for unfinished tasks
whose identifier at their TES instance is unknown, which can't be canceled,
`413 Content Too Large` for request bodies over the [size
limit](configuration.md#request-size-limit), `500 Internal Server Error`
if a task could not be forwarded or no middleware could be applied, and
`502 Bad Gateway` if a TES instance did not confirm a request to create or
cancel a task, which it may have processed (see [Tasks](#tasks)). The
mapping of exceptions to problem details is
`exceptions` in
[`pro_tes.exceptions`](reference.md#pro_tes.exceptions).

## Middleware management API

The [middleware management API](middlewares.md#middleware-management-api) is
specified, but not served yet.
