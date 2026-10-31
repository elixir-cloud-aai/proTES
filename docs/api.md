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
| `GET /tasks` | List the caller's tasks, newest first, one page at a time (`page_size`, at most `max_page_size` of the [config](configuration.md#tasks), and `max_page_size_full` for the view `FULL`; `page_token` from `next_page_token` of the previous page, which is empty on the last page), optionally only those whose name starts with `name_prefix` (taken literally). |
| `GET /tasks/{id}` | Get a task of the caller, with its state as last tracked from the TES instance. |
| `GET /tasks/{id}/provenance` | proTES extension: get the provenance of a task, for its owner and for admins (see [Provenance](#provenance)). |
| `POST /tasks/{id}:cancel` | Cancel a task of the caller that has not finished yet (also in state `UNKNOWN`, e.g., not tracked yet), at the TES instance that runs it; finished tasks are left as they are, also tasks that finish while they are canceled (their final state is kept). Unfinished tasks whose identifier at their TES instance is unknown (e.g., while they are being forwarded, or if forwarding failed unexpectedly) can't be canceled (`409 Conflict`). |

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
   proTES is configured to remove them, and tasks with inline contents of
   inputs over `max_inline_content`, with `413 Content Too Large`: see
   [Tasks](configuration.md#tasks));
2. runs the pipeline of [plugins](plugins.md), which may change the task
   and select the TES instances to try and their order (by default, those
   of `custom.tes.backends`, in order); a plugin may reject the task (with
   its status, e.g., `403 Forbidden`), a step whose plugins all fail fails
   the request (`500 Internal Server Error`), tasks without TES instances
   fail with `503 Service Unavailable`, and tasks whose inline contents
   plugins make too large with `413 Content Too Large`; none of these is
   kept;
3. forwards the task to the first of these TES instances that accepts it,
   after the plugins of the `forward` phase, and returns its own identifier
   of the task (plugins may change the response); if no TES instance
   accepts it (within `plugins.defaults.forward_timeout`), the request fails
   with `503 Service Unavailable`, and the task is not kept (it is deleted,
   with its owner's roles on it). Each TES instance gets its own copy of
   the task, without tags under `gateway.`, with the credentials
   of proTES for it (see [Credentials of TES
   instances](access-control.md#credentials-of-tes-instances)), in a single
   request; the next TES instance is tried only if a TES instance
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
(`logs[].metadata.forwarded_to`). `forwarded_to` is deprecated (an array of
objects in the string map `metadata`, which the TES specification doesn't
allow, so strict clients may fail on it): use the gateway tags below
instead; it will be removed in a later release.

## Gateway tags

proTES adds tags to each new task (FOCA's gateway tags, under the reserved
prefix `plugins.tags.prefix`, `gateway.` by default), served with the task in
the views `BASIC` and `FULL`, besides the client's own tags:

| Tag | Value | Added |
| --- | --- | --- |
| `gateway.pipeline_source` | `default`, `preset:<name>` or `client` | when the task is stored |
| `gateway.pipeline_hash` | `sha256:` hash of the resolved pipeline | when the task is stored |
| `gateway.plugins` | the plugins that ran in the `request` and `route` phases, as `id@version`, comma-separated | when the task is stored |
| `gateway.provenance` | the path of the task's provenance (`/ga4gh/tes/v1/tasks/<id>/provenance`) | when the task is stored |
| `gateway.backend` | the URL of the TES instance that accepted the task (or may have) | after forwarding |
| `gateway.backend_task_id` | the identifier of the task at that TES instance, if it accepted it | after forwarding |
| `gateway.forwarded_at` | when the task was sent to it (RFC 3339, UTC) | after forwarding |
| `gateway.attempts` | the number of TES instances tried | after forwarding |
| `gateway.route` | the last attempts as compact JSON (`url`, `outcome`, `reason`) | after forwarding |
| `gateway.route_truncated` | `true` if `gateway.route` leaves out attempts | after forwarding |

Tracking a task doesn't change its tags. If tags can't be built (an
internal error, which is logged), the task is created without them; its
provenance is stored nevertheless.

Clients can't set these tags (`400 Bad Request`; only `gateway.backends`,
and `gateway.pipeline` if enabled, are theirs, see
[Plugins](plugins.md#choices-of-clients)), and plugins can't change them.
TES instances get tasks without any tag under the prefix. Clients that
submit a fetched task again drop the tags under the prefix first, except
their own choices. Tasks created by earlier versions have none.

## Provenance

`GET /tasks/{id}/provenance` (a proTES extension) returns the provenance of
a task: the record that FOCA's plugin engine made when the task was created
and forwarded (see [Task provenance](operations.md#task-provenance)), with
the builds of the plugins that ran (`builds`, by build hash) and the
resolved pipeline (`pipeline.resolved`), e.g., shortened:

```json
{
  "engine": {"foca": "0.18.0", "plugin_api": 1,
             "app": {"name": "proTES", "version": "0.4.0"}},
  "request_id": "4f3c...",
  "pipeline": {"source": "default", "preset": null, "requested": null,
               "resolved": [{"id": "route", "alternatives": ["..."]}],
               "resolved_hash": "sha256:9a1e..."},
  "plugins": {"sha256:77b0...": "foca.routing.ordered"},
  "builds": {"sha256:77b0...": {"id": "foca.routing.ordered",
                                "distribution": "foca", "version": "0.18.0"}},
  "executions": [{"step": "route", "phase": "route", "outcome": "ok"}],
  "route": {"candidates_in": ["https://tes.example.org"],
            "candidates_out": ["https://tes.example.org"]},
  "selection": null,
  "attempts": [{"number": 1, "backend": "https://tes.example.org",
                "outcome": "accepted", "reason": null, "remote_id": "task-1",
                "started_at": "2026-10-31T09:00:00.020Z", "duration_ms": 80.1}],
  "final_reason": null
}
```

The owner of the task and admins may get it; everyone else gets
`403 Forbidden`, also for unknown tasks (see [Access
control](access-control.md#task-ownership)); admins get `404 Not Found` for
unknown tasks. Tasks without provenance (e.g.,
created by earlier versions) give `404 Not Found`. The record holds no
inputs, logs, IP addresses, tokens, header values or claims: plugins'
changes are recorded as paths and hashes only.

## Errors

Errors are [problem details](https://www.rfc-editor.org/rfc/rfc9457) (with
`title`, `status` and `detail`; errors of the plugin engine also with
`problem_type`, a URI in FOCA's documentation), sent as `application/json`,
as the GA4GH
specification declares: `400 Bad Request` for invalid requests (e.g.,
invalid tasks or query parameters, or invalid or reserved tags under
`gateway.`), `401 Unauthorized` for requests
without a valid access token (with a Bearer challenge), `403 Forbidden` for
users without the required role and for tasks of others or unknown tasks
(see [Access control](access-control.md)), `404 Not Found` for tasks of the
caller deleted from the database, `409 Conflict` for unfinished tasks
whose identifier at their TES instance is unknown, which can't be canceled,
`413 Content Too Large` for request bodies over the [size
limit](configuration.md#request-size-limit) and tasks with inline contents
of inputs over `max_inline_content` (see
[Tasks](configuration.md#tasks)), the status of a plugin that rejected a
task, `500 Internal Server Error` if all plugins of a step failed,
`502 Bad Gateway` if a TES instance did not confirm a request to create or
cancel a task, which it may have processed (see [Tasks](#tasks)),
`503 Service Unavailable` if no TES instance accepted a task (in time; it
is not kept), and `508 Loop Detected` for tasks that passed proTES before
(see [Plugins](plugins.md)). The
mapping of exceptions to problem details is
`exceptions` in
[`pro_tes.exceptions`](reference.md#pro_tes.exceptions).

The TES specification declares only the successful responses. The served
specification adds, as proTES extensions, `413 Content Too Large` to
`POST /tasks` and `409 Conflict` to `POST /tasks/{id}:cancel`, with the
schema `ProblemDetails` (cf.
[`additions.openapi.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/api/additions.openapi.yaml)),
as clients don't get these from TES servers in general; the operations are
otherwise those of the TES specification. The other errors are those of
any HTTP API, and are not declared.
