# Upgrading

The [release notes](changelog.md) list the breaking changes of each release
and how to upgrade. This page describes how existing setups and data are
affected by changes since the last deployed version (images tagged by date,
FOCA 0.12).

## Configuration

Since the upgrade to FOCA 0.15, custom configs must apply the config changes
of FOCA 0.14 and 0.15 (see the FOCA release notes and migration guides),
e.g., `exceptions.mapping` instead of the old exception members, and no
`validation_methods`, `validation_checks`, `environment` or `use_reloader`.
The service info is configured in `service_info.service` (see [Service
info](configuration.md#service-info)); it is no longer stored in the
database, so the `service_info` collection can be dropped.

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
