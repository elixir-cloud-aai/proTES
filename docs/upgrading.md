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

API specifications (`api.specs`) and access control rules
(`security.access_control.policies`) of the base config are named entries
(the spec `tes`; rules `users-create-list-tasks` and
`users-get-cancel-tasks`), which overlays change by name (see
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
credentials of TES instances are missing. Stored tasks are still
tracked and canceled with the credentials now configured for their TES
instance. The Celery task that tracks tasks no longer takes credentials
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
[Server](configuration.md#server)). The Helm chart 3.0.0 needs images of this
version, and earlier charts need earlier images.

## Docker Compose

Docker Compose runs MongoDB 7.0 (was 3.6, which current PyMongo releases
can't connect to), and keeps the data in named volumes instead of
`PROTES_DATA_DIR` (default `../data/pro_tes`), whose data is not migrated.
MongoDB can't upgrade data files from 3.6 to 7.0 directly: dump the data with
the old version (`mongodump`) and restore it into the new one
(`mongorestore`), or start with an empty database. Only proTES (and Flower,
now opt-in) are published, on `127.0.0.1`.
