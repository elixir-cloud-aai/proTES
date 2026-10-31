# Plugins

Plugins process each new task (`POST /tasks`): they may check or change the
task, choose and order the TES instances that it is forwarded to, and change
the request to each TES instance and the response to the client. proTES runs
them with the plugin engine of FOCA (`foca.plugins`), which other gateways
share: plugins are installed Python packages, enabled by the admin, and
configured in the top-level section `plugins` of the config. How to write
plugins is described in [FOCA's
documentation](https://foca.readthedocs.io/en/latest/plugins/); this page is
the operator's view.

## How a task is processed

After authentication, access control and proTES's own checks of the task
(credentials in URLs, inline contents, validation; see
[Tasks](api.md#tasks)), proTES runs the pipeline of the request in four
phases:

1. `request`: plugins may check or change the task, or reject it. After each
   plugin that changed the task, proTES removes credentials from the URLs of
   inputs and outputs, checks the inline contents of the inputs
   (`413 Content Too Large` if too large) and validates the task; a plugin
   whose output is invalid counts as failed.
2. `route`: plugins choose the TES instances to try, and their order, from
   the candidates: the TES instances of `custom.tes.backends`, including
   those discovered in service registries (see [TES
   instances](configuration.md#tes-instances)). Plugins can't add TES
   instances: proTES has credentials for configured and discovered ones
   only. The task is then stored.
3. `forward`, for each TES instance in turn: plugins may change the request
   to that TES instance (its query, its headers and the task), and drop the
   credentials of the TES instance (`auth: none`); each TES instance gets
   its own copy. proTES then adds the credentials of the TES instance
   (plugins never see them, and can't choose others), removes credentials
   from the URLs of inputs and outputs, and sends the task. Requests whose
   method or path a plugin changed (tasks are created with
   `POST /ga4gh/tes/v1/tasks`), whose task a plugin made invalid, or whose
   inline contents it made too large, are not sent, and the next TES
   instance is tried. Headers of the response that FOCA doesn't accept
   (e.g., with NUL characters) are left out.
   The next TES instance is also tried if a TES instance certainly did not
   accept the task, but never if it may have (see [Tasks](api.md#tasks)).
4. `response`: once a TES instance accepted the task, plugins may change the
   response to the client (its body and headers), in reverse order. Steps
   whose plugins fail or reject the response are skipped: the task runs at
   the TES instance, so it is kept and tracked.

Plugins see the task, the request path, query and headers (without
`Authorization`, `Cookie` and `Proxy-Authorization`) and the caller (its
identity, roles, token claims and its validated access token), never the
credentials of TES instances. Enable only plugins that you trust with the
tokens of callers. Plugins can't set the headers `Host`, `Connection`,
`Content-Length`, `Transfer-Encoding`, `TE`, `Trailer`, `Upgrade` and
`Proxy-Authorization` of requests to TES instances, which would change
where or how they are sent.
Plugins run with time limits (`plugins.defaults`): a plugin that doesn't
return in time counts as failed, and if no TES instance accepted the task
after `forward_timeout` seconds (default 60), the request fails with
`503 Service Unavailable` (the timeout of connecting to a configured TES
instance is shortened to the time left, that of discovered ones not yet, as
FOCA's guarded HTTP client has no such option; requests already sent are
not cut short, so that their outcome is known). Requests that already passed proTES (a `Via`
header with the host of `server.public_url`, or `plugins.gateway_id`) are
refused with `508 Loop Detected`; requests to TES instances carry a `Via`
header with it.

## Pipelines

A pipeline is a list of steps, each with alternatives (plugins with
parameters), tried in order: the first alternative that succeeds is used,
and the changes of failed ones are discarded. If all alternatives of a step
fail, the task fails with `500 Internal Server Error` (unless the step has
`on_failure: skip`); a plugin may also reject the task with a status of its
own (e.g., `403 Forbidden`), and then no other alternative is tried. Tasks
that fail or are rejected before they are forwarded are not kept.

The default pipeline is FOCA's ordered failover (`foca.routing.ordered`):
the TES instances in the order of `custom.tes.backends`, those of each
registry shuffled, the next one if a TES instance didn't accept the task.
No third-party services are called. The base config sets it explicitly:

```yaml
plugins:
  available: {}
  pipeline:
    - id: route
      alternatives:
        - plugin: foca.routing.ordered
```

## Installing and enabling plugins

Plugins are Python packages that register their plugin classes as entry
points of the group `foca.plugins`; install them next to proTES, e.g., in a
custom image based on the proTES image. Only plugins listed in
`plugins.available`, by their identifier (the entry point name), are loaded;
FOCA's built-in plugins (`foca.*`) are always available. Entries may pin the
distribution and version, set the plugin's admin settings (`config`), and
limit it to roles (`roles`). The app refuses to start if a plugin of the
list or of the pipeline can't be loaded, e.g., because it isn't installed
or doesn't support the TES API.

For example, with the routing plugins of
[foca-plugins](https://github.com/elixir-cloud-aai/foca-plugins) (once they
are published), tasks go to the TES instances closest to their input data,
with random order as the fallback, as with the former middlewares of
proTES:

```yaml
plugins:
  available:
    tes.routing.distance: {}
    generic.routing.random: {}
  pipeline:
    - id: route
      alternatives:
        - plugin: tes.routing.distance
        - plugin: generic.routing.random
```

## Choices of clients

Authenticated clients may choose among the TES instances of a task with the
tag `gateway.backends`, a JSON object such as
`{"type": "allow", "backends": ["https://tes.example.org"]}` (types
`order`, `allow` and `forbid`; `plugins.client_backends`), and, if the
admin enables it (`plugins.client_pipelines`, off by default), their own
pipeline or a preset of the admin (`plugins.presets`) with the tag
`gateway.pipeline`. Other tags under `gateway.` are reserved for proTES:
tasks that set them are rejected with `400 Bad Request`. The stored task
keeps the client's tags; TES instances get no tags under `gateway.`.

All settings of the section `plugins` (e.g., `defaults`, `presets`,
`client_pipelines`, `client_backends`, `tags` and `gateway_id`) are
described in FOCA's documentation.
