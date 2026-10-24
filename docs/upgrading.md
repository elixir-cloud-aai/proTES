# Upgrading

The [release notes](changelog.md) list the breaking changes of each release
and how to upgrade. This page describes how existing setups and data are
affected by changes since the last deployed version (images tagged by date,
FOCA 0.12).

## Configuration

proTES runs on FOCA 0.17 and Python 3.13. Custom configs must apply the
config changes of FOCA 0.14 to 0.17 (see the FOCA release notes and
migration guides), e.g., `exceptions.mapping` instead of the old exception
members, and no `validation_methods`, `validation_checks`, `environment` or
`use_reloader`.
The service info is configured in `service_info.service` (see [Service
info](configuration.md#service-info)); it is no longer stored in the
database, so the `service_info` collection can be dropped.

Unknown keys in `custom` are errors: the app and the Celery worker refuse
to start with them, naming them, instead of ignoring them (e.g., a
misspelled `service_list`, which left the list of TES instances empty).
Remove the keys that had no effect from config overlays: all of
`custom.celery` (`custom.celery.monitor.timeout` and
`custom.celery.message_maxsize`) and of `custom.controllers.celery`
(`custom.controllers.celery.monitor.timeout` and
`custom.controllers.celery.message_maxsize`).

API specifications (`api.specs`) and access control rules
(`security.access_control.policies`) of the base config are named entries
(the spec `tes`; rules `users-create-list-tasks`,
`owners-get-cancel-tasks`, `admins-list-add-permissions` and
`admins-manage-permissions`), which overlays change by name (see
[Roles](access-control.md#roles)); overlays that give lists still replace
them. The configured rules replace the stored ones atomically on startup,
and stored configured rules are removed if an overlay sets no rules. During
a rolling update, instances of earlier versions don't get this guarantee:
update all instances before changing the configured rules, and don't start
earlier versions again afterwards (see FOCA's [migration guide to
0.17](https://foca.readthedocs.io/en/latest/migration-0.17/), also for
downgrades).

## Authentication

The TES API requires access tokens of trusted issuers, and roles from groups
of the identity provider (see [Access control](access-control.md)); before,
anyone could create, list, read and cancel all tasks. Deployments must set
the trusted issuers (`security.auth.issuers`), the audience
(`security.auth.audience`) and the groups of the roles
(`security.access_control.claim_groups` and `group_mapping`, at least one
group of the role `admin`): proTES doesn't start without them (unless
`custom.access_control.require_admin` is false). Clients send
`Authorization: Bearer <token>` with each request. HTTP Basic credentials of
requests are no longer passed through to TES instances: proTES uses
credentials of its own for them (see [TES instances and their
credentials](#tes-instances-and-their-credentials)). The Helm chart sets the
config overlay from the value `config`.

Only access tokens as per RFC 9068 (`typ` header `at+jwt`) are accepted;
other tokens of the trusted issuers, such as ID tokens, are rejected with
`401` (see [Authentication](access-control.md#authentication) for identity
providers that set another `typ` header, or none). The challenge of `401`
responses for invalid tokens has an `error_description`, too. The new
option `security.auth.clients` limits the accepted tokens to those of
certain clients (by default, clients are not restricted).

## Task ownership

Users see, read and cancel only their own tasks, and admins no longer may do
everything: they can't see the tasks of others, and only manage the access
control rules (see [Task ownership](access-control.md#task-ownership)).
Tasks of others and unknown tasks get `403 Forbidden` (was `404 Not Found`
for unknown tasks). The rule `users-get-cancel-tasks` is replaced by
`owners-get-cancel-tasks`, and the access control model
(`pro_tes/access_control/model.conf`) has owner roles (`g2`) and no longer
lets admins do everything: overlays naming the old rule, or custom models,
must be updated. Rules added via the permissions API are kept: check that
none of them gives users or admins access to all tasks (e.g., `p, user,
/ga4gh/tes/v1/tasks/:id, GET`).

proTES refuses to start if access control is not enabled and enforced, or
if service clients are configured (`security.auth.service_clients`,
`on_behalf_of_header`, `security.access_control.client_mapping`), which it
doesn't support yet.

Tasks stored by earlier versions have no owner, so nobody can see them
through the API; operators can give them to their users, or purge them (see
[Task owners](operations.md#task-owners)). Each new task adds two rules to
the access control database.

## Task identifiers

Task identifiers (`task.id`) are unique in the database: on startup,
proTES creates a unique index on them (and an index on `worker_id`), and
drops the index of earlier versions (`task_id_1_worker_id_1`), which never rejected a task identifier
that was used already. While stored tasks share identifiers, the index
can't be created, and proTES (the API and the Celery workers) refuses to
start: the error names the shared identifiers (up to 10), and how many
tasks have each. proTES doesn't change these tasks; resolve the duplicates in the
database of the tasks (`taskStore`, unless `MONGO_DBNAME` names another),
e.g., with `mongosh` (`use <database>`), and start proTES again:

1. Back up the tasks, e.g., with
   `mongodump --db <database> --collection tasks`.
2. Find the shared identifiers (`_id: null` stands for tasks without
   identifier, which the API can't serve anyway):

    ```js
    db.tasks.aggregate([
      { $group: { _id: "$task.id", n: { $sum: 1 } } },
      { $match: { n: { $gt: 1 } } },
    ])
    ```

3. List the tasks of each shared identifier:

    ```js
    db.tasks.find(
      { "task.id": "<id>" },
      { "task.id": 1, "task.state": 1, "task.creation_time": 1, user_id: 1 },
    )
    ```

4. Keep one task under the identifier, and either delete each of the
   others, or give it an identifier that is used neither by a task nor by
   owner roles (and tell its owner the new identifier, as the old one now
   leads to the task kept):

    ```js
    db.tasks.deleteOne({ _id: ObjectId("<_id>") })
    db.tasks.updateOne(
      { _id: ObjectId("<_id>") },
      { $set: { "task.id": "<new id>" } },
    )
    ```

5. If the tasks have owners (`user_id`), check the owner roles on the
   identifier: they gave each owner access to all tasks of the identifier.
   Delete those of the owners of the other tasks, and give the owners of
   renamed tasks roles on the new identifiers (see [Task
   owners](operations.md#task-owners)):

    ```js
    const paths = ["/ga4gh/tes/v1/tasks/<id>", "/ga4gh/tes/v1/tasks/<id>:cancel"]
    db.policy_rules.find({ ptype: "g2", v2: { $in: paths } })
    // the roles of a user who doesn't own the task kept
    db.policy_rules.deleteMany({ ptype: "g2", v0: "user:<sub>", v2: { $in: paths } })
    ```

## TES instances and their credentials

The entries of `custom.tes.service_list` are objects with the URL of the TES
instance (`url`) and how proTES authenticates at it (`auth`), instead of
URLs; `auth` defaults to `client_credentials`, an access token of proTES
itself (see [Credentials of TES
instances](access-control.md#credentials-of-tes-instances)). Replace each
URL with an entry, e.g., `- url: https://tes.example.org`, and choose its
`auth`:

- `client_credentials` for TES instances that accept tokens of proTES' own
  client: register a client for the client credentials grant at the
  identity provider, set its issuer (or token endpoint) in
  `custom.tes.client_credentials`, and its ID and secret in the environment
  variables `PROTES_CLIENT_ID` and `PROTES_CLIENT_SECRET` of the app and the
  Celery worker;
- `basic` for TES instances that require HTTP Basic credentials, with the
  names of the environment variables that hold them (`basic.username_env`
  and `basic.password_env`), set for the app and the Celery worker;
- `none` for TES instances that need no credentials.

proTES creates, gets and cancels tasks with a single request each, to
`<url>/ga4gh/tes/v1` only (earlier versions also tried `<url>/v1` and
`<url>`, and the next path or TES instance after timeouts and server
errors, so that TES instances could get a task more than once). TES
instances must serve the API at `/ga4gh/tes/v1` of their configured URL;
requests to create and cancel tasks don't follow redirects.
Tasks that a TES instance may have accepted without confirming it fail
with `SYSTEM_ERROR` and `502 Bad Gateway`, instead of being sent to the next
TES instance (see [TES instances](configuration.md#tes-instances)).
Requests to TES instances time out after `custom.tes.timeout` seconds
(default: 15, instead of 5); `custom.controllers.post_task.timeout`
(`post`, `poll` and `job`), which had no effect, was removed: remove it
from config overlays.

The base config no longer lists public TES instances; deployments list
their own. URLs of TES instances must use `https`, unless
`custom.tes.url_schemes` allows `http`, and must not have credentials;
duplicate entries are rejected. Tasks are only forwarded to configured TES
instances, also if middlewares add others (`tes_urls`). Custom middlewares
that read `custom.tes.service_list` get these entries instead of URLs.

Tasks with credentials in the URLs of their inputs or outputs
(`https://user:password@host/...`) are rejected with `400 Bad Request`, for
all TES instances (Funnel instances got them before); set
`custom.controllers.post_task.url_credentials` to `strip` to remove the
credentials instead.

On startup, proTES removes the HTTP Basic credentials of callers that
earlier versions stored with each task (`basic_auth`), and credentials in
the URLs of inputs and outputs of stored tasks (also of the outputs that
TES instances logged). The Celery worker refuses to start, too, if
credentials of TES instances are missing. Stored tasks are still tracked
and canceled with the credentials now configured for their TES instance.
The Celery task that tracks tasks no longer takes credentials
(`user`, `password`), so the new worker rejects tracking jobs that earlier
versions queued but no worker started yet: upgrade while no tasks are being
submitted, so that the queue is empty.

## Errors

Errors are [problem details](api.md#errors) (`title`, `status`, `detail`)
instead of objects with `message` and `code`.

## Stored tasks

`logs[].metadata.forwarded_to` of tasks is a list of forwards (`id`, `url`,
`forward_time`), in the order of forwarding, instead of a nested
`tesNextTes` object. Tasks stored by earlier versions are still read, and
served with their forwards as lists; they can be tracked and canceled as
before.

Earlier versions kept polling tasks that their backend reported as
`PREEMPTED`, which is a final state. These tasks are stored as `PREEMPTED`
already, but their final logs are not. The upgrade ends this polling, as
it ends all tracking by the old Celery worker; the logs of these tasks
aren't fetched again.

## Task tracking

Tasks are tracked by short polls that schedule the next one, instead of one
Celery task per task that held a worker process until the task finished (see
[Task tracking](operations.md#task-tracking)), so a few worker processes
track many tasks. Polls are acknowledged after they ran, and workers resume
the tracking of stored unfinished tasks when they start: restarts and
upgrades of the workers no longer stop the tracking of tasks. Only tasks
with a remote identifier in `tes_endpoint.task_id` (see [Task times and
remote identifiers](#task-times-and-remote-identifiers)) are resumed:
unfinished tasks stored by earlier versions keep their last state.

Failed polls no longer make a task fail: after
`custom.controllers.post_task.polling.attempts` consecutive failed polls
(not failed polls over the task's whole life), its state is `UNKNOWN` until
the TES instance answers again, and polls back off up to
`polling.max_wait` seconds (new, default 600); the first poll is retried,
too, and so are polls of tasks whose TES instance is not (or no longer)
configured or discovered. `polling.wait` and `polling.attempts` must be
positive, and `wait` and `max_wait` below 1800. The workers no longer set
`INITIALIZING` when they start tracking a task: the state is the one
reported by its TES instance.

## Task times and remote identifiers

Times of tasks (`creation_time`, and `start_time` and `end_time` of the
logs) are in UTC, in RFC 3339 format (e.g.,
`2026-10-22T12:34:56.789012+00:00`), instead of local-time strings such as
`10-22-2026 14:34:56`; times of tasks stored by earlier versions are served
as stored. proTES sets `creation_time`, and rejects tasks that set it
(`400`), as the TES specification marks it read-only.

The identifier of a
task at its TES instance is also stored in `tes_endpoint.task_id` (not
served), so that tasks can be canceled with
`custom.storeLogs.execution_trace: False` too.

New tasks are stored with the state that their TES instance reports when
they are forwarded (e.g., `QUEUED`), instead of `UNKNOWN` until the Celery
worker tracks them. Tasks in state `UNKNOWN` are canceled at their TES
instance too (canceling them returned `200` without effect); tasks that
have not finished, but whose identifier at their TES instance is unknown,
can't be canceled (`409 Conflict`; was `500`, or `200` without effect
for tasks in state `UNKNOWN`).

## Task listing

`GET /tasks` returns an empty `next_page_token` on the last page (it
returned the last task's identifier, so clients kept paging), and an empty
page instead of an error if no task matches; invalid page tokens and page
sizes are rejected (`400`). Pages have 256 tasks by default (was 5) and at
most 2047 (`custom.controllers.list_tasks`, see
[Tasks](configuration.md#tasks)). `name_prefix` is taken literally, not as a
regular expression. proTES no longer starts if
`custom.controllers.list_tasks.default_page_size` exceeds `max_page_size`.

## Distance-based task distribution

The distance-based middleware still looks up the places of IP addresses with
DB-IP's API, now over HTTPS (was HTTP), and geocodes the place names with
Nominatim (see [Built-in middlewares](middlewares.md#built-in-middlewares)),
but no longer with the unmaintained package `ip2geotools`, which doesn't
install on Python 3.13. Requests time out after 10 seconds (was 62). It no
longer locates addresses that are not global (e.g., private ones), which
aren't sent to the services anymore, nor places without city or country:
it falls back to the next middleware for them, instead of distributing
tasks by an arbitrary location.

## Middleware management API

The middleware management API is no longer registered, as its controllers
don't exist yet (see [Middleware management
API](middlewares.md#middleware-management-api)).

## Image

The image installs proTES as a package: it serves the app with `gunicorn
--config python:pro_tes.gunicorn_config 'pro_tes.app:init_app()'` (its
default command), and runs the Celery worker with `celery --app
pro_tes.celery_worker worker`. `/app/pro_tes`, `gunicorn.py` and `wsgi.py` are
gone, and the environment variables `GUNICORN_PROCESSES` and
`GUNICORN_THREADS` are replaced by `GUNICORN_CMD_ARGS` (see
[Server](configuration.md#server)). The Helm chart of this version needs
images of this version, and the legacy chart (versions 2 and 3.0.0) needs
earlier images. Released charts have the version of their release (e.g.,
0.3.0), so chart versions restart below 3.0.0.

Images are tagged `dev` and `sha-<commit>` for commits on `dev`, and `X.Y.Z`
for releases; `latest` is the highest stable release, no longer the latest
commit on `dev`, and date tags are no longer pushed (see [Versioning and
releases](development.md#versioning-and-releases)). The Helm chart takes
the image tag from `protes.tag`, which defaults to the chart's `appVersion`:
`dev` in the repository, the release version in released charts (see
[Image](deployment.md#image)). `protes.image` may still include a tag (e.g.,
`elixircloud/protes:0.3.0`); the Celery worker runs the app's image.

## Helm chart

The Helm chart is replaced by a chart on FOCA's library chart `foca-common`
(see [Deployment](deployment.md)): other values (camelCase, e.g., `hostName`
instead of `applicationDomain`), resource names and selectors, Secrets of
your own for MongoDB and the broker instead of credentials in the values
(the broker's user was `guest:guest`), Flower off by default and with
credentials from a Secret (was always on, with `admin:admin`),
NetworkPolicies, pinned images, and an Ingress for all paths (was
`/ga4gh/tes/v1` only), or a Gateway API HTTPRoute. The chart has no defaults
for a particular ingress controller: to enable the app's NetworkPolicy
(`protes.networkPolicy.enabled`), set its peers
(`protes.networkPolicy.ingressFrom`, none by default), and with
ingress-nginx, raise its body size limit by an annotation if you raise
`config.server.max_content_length` above 1 MiB (see [Exposing the
app](deployment.md#exposing-the-app)). Releases of the legacy
chart can't be upgraded: uninstall them and install the new chart, keeping
the data as described in [From the legacy
chart](deployment.md#from-the-legacy-chart).

## Docker Compose

Docker Compose runs MongoDB 7.0 (was 3.6, which current PyMongo releases
can't connect to), and keeps the data in named volumes instead of
`PROTES_DATA_DIR` (default `../data/pro_tes`), whose data is not migrated.
MongoDB can't upgrade data files from 3.6 to 7.0 directly: dump the data with
the old version (`mongodump`) and restore it into the new one
(`mongorestore`), or start with an empty database. Only proTES (and Flower,
now opt-in) are published, on `127.0.0.1`.
