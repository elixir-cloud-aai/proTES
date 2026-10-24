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

## Local overrides

Docker Compose mounts
[`config.dev.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/config.dev.yaml),
an overlay for local development (see [Getting
started](getting-started.md#usage)), into the app and the Celery worker. To
override values locally without changing it, put them in
`config.local.yaml` and mount it in a `docker-compose.override.yaml` (both
are ignored by Git), for both services:

```yaml
services:
    protes:
        environment:
            FOCA_CONFIG_OVERLAY: /etc/protes/config.dev.yaml:/etc/protes/config.local.yaml
        volumes:
            - ./config.local.yaml:/etc/protes/config.local.yaml:ro
    protes-worker:
        environment:
            FOCA_CONFIG_OVERLAY: /etc/protes/config.dev.yaml:/etc/protes/config.local.yaml
        volumes:
            - ./config.local.yaml:/etc/protes/config.local.yaml:ro
```

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
    # Identity provider of proTES, for the TES instances with
    # `auth: client_credentials` (the default)
    client_credentials:
      issuer: https://login.example.org/oidc/
```

URLs must use one of the schemes of `custom.tes.url_schemes` (default:
`https` only). The [middlewares](middlewares.md) decide which of the TES
instances are tried, and in which order (see [Tasks](api.md#tasks)).

### Discovery in a service registry

proTES can also take TES instances from a service registry implementing the
[GA4GH Service Registry
API](https://github.com/ga4gh-discovery/ga4gh-service-registry) with a list
of verified services, such as the [Cloud
Registry](https://github.com/elixir-cloud-aai/cloud-registry)
(`GET /verifications`), so that new TES instances are used once they are
registered and verified, without config change. Discovery is off unless
`custom.tes.discovery.registry_url` is set to the base URL of the registry's
API; there is no default registry:

```yaml
custom:
  tes:
    discovery:
      registry_url: https://registry.example.org/ga4gh/registry/v1
      # Seconds between reads of the registry
      refresh_interval: 300
      # Timeout of each request to the registry, in seconds
      timeout: 10
      # Accepted TES versions, as prefixes (`1.1` accepts `1.1.0`)
      versions: ["1.0", "1.1"]
      # Accepted environments of services (`environment`); any if empty
      environments: [prod]
      # How proTES authenticates at discovered TES instances:
      # `client_credentials` (the default) or `none`
      auth: client_credentials
      # For local setups only
      allow_private_addresses: false
```

Anyone who can register services in the registry could point an entry at a
malicious or internal endpoint, which would receive tasks, their input
locations and proTES' access token. So the registry, set by the deployment,
is the trust anchor, and a registered service is only used if all of the
following hold:

- the registry lists it as verified by an admin, with its current
  identifier, URL and API type (`type.group`, `type.artifact`);
- it is a TES service (`org.ga4gh` `tes`) of one of the `versions`, and, if
  `environments` is set, of one of the `environments`;
- its URL is a valid `https` URL without user info (`url_schemes` applies
  to `service_list`, the registry and the identity provider only); the URL
  is normalized (e.g., lowercase host, without default port and trailing
  slashes), and the API path `/ga4gh/tes/v1`, which registries list as part
  of the URL, is removed;
- its host has public addresses only: hosts with private, loopback,
  link-local or other special-purpose addresses (e.g., `10.0.0.1`,
  `localhost`, `169.254.169.254`) are rejected, unless
  `allow_private_addresses` is set. Hosts are checked on discovery and
  before each request to the TES instance, and requests to discovered TES
  instances don't follow redirects.

The registry is read without access token (its reads are public), over a
URL with a scheme of `custom.tes.url_schemes`; redirects are not followed.
Each process (each Gunicorn worker and Celery worker) reads it every
`refresh_interval` seconds, and keeps the last good list while the
registry is unreachable or returns invalid responses, retrying after 30
seconds at most; TES instances whose hosts can't be resolved for the time
being are kept for one more read, too. A process that doesn't know the
TES instance of a task (e.g., the Celery worker, for a TES instance that
the app's process discovered first) reads the registry again, at most every
10 seconds; polls of a task whose TES instance is unknown fail like other
polls, so its tracking goes on (see `post_task.polling` in
[Tasks](#tasks)). A service whose verification is removed is no longer used
once the registry has been read again. Changes of the list and rejected verified
services are logged. `timeout` applies to each request to the registry; the
hosts of the services are resolved with the timeouts of the system's
resolver. TES instances of `custom.tes.service_list` are used, too, and come
first; their settings take precedence over those of discovered ones with the
same URL. Discovered TES instances get the credentials of `auth` (see
[Credentials of TES
instances](access-control.md#credentials-of-tes-instances)); tasks of a TES
instance that is no longer discovered can't be tracked or canceled (their
state becomes `UNKNOWN` until it is discovered again).

The host check doesn't protect against DNS rebinding (a host whose
addresses change between the check and the request); restrict the egress
of proTES to the networks of the TES instances (e.g., with a Kubernetes
NetworkPolicy) where internal services must not be reachable.

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
- `post_task.polling`: how the Celery workers track a forwarded task: they
  poll the TES instance for the task's state every `wait` seconds (default
  3) until the task has finished. After `attempts` consecutive failed polls
  (default 100; a successful poll resets the count), the task's state is
  `UNKNOWN`, and the polls back off, doubling the interval up to `max_wait`
  seconds (default 600; keep it below RabbitMQ's `consumer_timeout`, 30
  minutes by default), until the TES instance answers again: failed polls
  never make a task fail, as it may still be running (see [Task
  tracking](operations.md#task-tracking));
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
access), in `service_info.service`. The service info also lists the URLs
of the TES instances that tasks may be forwarded to, configured and
discovered (`backends`, from `service_info.runtime_fields`); set
`runtime_fields` to `null` in an overlay to hide them.
