# Configuration

The app is configured by
[`pro_tes/config.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/config.yaml),
the base config, which documents its values. It is a
[FOCA](https://foca.readthedocs.io/) config: the sections `server`,
`security`, `db`, `api`, `log`, `jobs`, `service_info` and `exceptions` are
described in the [FOCA documentation](https://foca.readthedocs.io/en/latest/),
the app's own settings are in section `custom` (validated on startup by
[`pro_tes.config_models.CustomConfig`](reference.md#pro_tes.config_models.CustomConfig)).
To override some values, write them to a config overlay file and list it in
environment variable `FOCA_CONFIG_OVERLAY` (several files separated by `:`);
FOCA merges overlays into the config on startup (see the [FOCA deployment
guide](https://foca.readthedocs.io/en/latest/deployment/)). The Celery worker
reads the same config.

## TES instances

Tasks are forwarded to the TES instances listed in `custom.tes.service_list`:
each with its base URL (`url`, without the API path `/ga4gh/tes/v1`), and
how proTES authenticates at it (`auth`: with its own access token, the
default, with HTTP Basic credentials from environment variables, or not at
all; see [Credentials of TES
instances](access-control.md#credentials-of-tes-instances)). The base config
lists none; deployments set their own list, e.g.:

```yaml
custom:
  tes:
    service_list:
      - url: https://tes.example.org
      - url: https://tes.example.com
        auth: none
```

URLs must use one of the schemes of `custom.tes.url_schemes` (default:
`https` only). The [middlewares](middlewares.md) decide which of the TES
instances are tried, and in which order (see [Tasks](api.md#tasks)).

## Middlewares

`custom.middlewares` is the stack of middlewares that are applied to each new
task, in order: a list of lists of the import paths of middleware classes.
Of each inner list, the first class that can be applied successfully is used,
and the others are skipped; the inner list is a chain of fallbacks. If no
class of an inner list can be applied, the task is rejected. See
[Middlewares](middlewares.md) for the built-in ones and how to write your own.

```yaml
custom:
  middlewares:
    - - "pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance"
      - "pro_tes.plugins.middlewares.task_distribution.random.TaskDistributionRandom"
```

## Tasks

`custom.controllers` sets how tasks are created and listed:

- `post_task.db.insert_attempts`: how often proTES tries to store a new task
  with a newly generated identifier if the identifier exists already;
- `post_task.task_id`: the characters (`charset`) and the length (`length`)
  of task identifiers;
- `post_task.polling`: how the Celery worker tracks a forwarded task: it
  polls the TES instance for the task's state every `wait` seconds until the
  task has finished, and gives up (state `SYSTEM_ERROR`) after `attempts`
  failed requests;
- `post_task.url_credentials`: what happens to tasks with credentials in the
  URLs of their inputs or outputs (`https://user:password@host/...`): they
  are rejected with `400 Bad Request` (`reject`, the default), or the
  credentials are removed (`strip`); credentials are never stored or
  forwarded, and those that middlewares add are always removed;
- `list_tasks.default_page_size`: the number of tasks per page of
  `GET /tasks` if the request sets no `page_size` (default 256, as in the
  TES specification);
- `list_tasks.max_page_size`: the maximum number of tasks per page; larger
  `page_size`s are reduced to it (default and upper limit 2047: the TES
  specification requires fewer than 2048).

`custom.storeLogs.execution_trace` sets whether proTES records the TES
instance and the remote task identifier of each forwarded task in the task's
logs (`logs[].metadata.forwarded_to`); proTES keeps them for cancellation
either way.

## Security

The TES API requires access tokens of the trusted issuers
(`security.auth`), and roles from groups (`security.access_control`); the
issuers, the audience and the groups are deployment settings without
defaults. See [Access control](access-control.md).

## Request size limit

Request bodies are limited to `server.max_content_length` bytes (default:
1048576, i.e., 1 MiB), by FOCA: larger requests, also chunked ones, are
rejected with `413 Content Too Large` before they are authenticated and
parsed. Raise the limit if tasks with large inline contents
(`inputs[].content`) must be accepted.

## Server

The image serves the app with [Gunicorn](https://gunicorn.org/), configured by
[`pro_tes/gunicorn_config.py`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/gunicorn_config.py),
which documents its settings: it listens on port 8080, with one worker
process with four threads. Override settings with the environment variable
`GUNICORN_CMD_ARGS` (e.g., `--workers 2 --threads 8`), but not `--bind`: the
image's health check and the Helm chart expect port 8080. Behind a reverse
proxy, set its addresses in `FORWARDED_ALLOW_IPS`, so that Gunicorn trusts
its `X-Forwarded-*` headers. `server.host` and `server.port` of the config
only apply to Flask's development server (`python -m pro_tes.app`).
`server.debug` must stay off outside local development: Flask's debugger
allows executing arbitrary code.

The Celery worker runs from the same image, with the command `celery --app
pro_tes.celery_worker worker`; it connects to the broker at
`CELERY_BROKER_URL` (with the RabbitMQ credentials; see
[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/docker-compose.yaml)).

## Service info

`GET /service-info` is served by FOCA from section `service_info` of the
config, according to the GA4GH service info schema (validated on startup).
Its `version` is the version of the installed package (`service_info.package`).
The base config describes the app, not a deployment: its `id`
(`org.example.protes`) and `organization` are placeholders with reserved
example domains, which the app warns of on startup. Deployments set their
own `id`, `name`, `description`, `organization`, `contactUrl` and
`environment`, and `storage` (the storage locations their TES instances can
access), in `service_info.service`.
