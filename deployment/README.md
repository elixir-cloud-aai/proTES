# Helm chart

The chart deploys proTES on Kubernetes (and OpenShift): the app, its Celery
worker, which relays tasks to the TES instances and tracks their states, a
MongoDB database and a RabbitMQ broker, and optionally Flower, the monitoring
UI of the worker. It is a thin chart over FOCA's library chart `foca-common`
(the version in `Chart.yaml`), which renders most resources from the values
described here and in `values.yaml` (see the [library chart's
documentation](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.2.0/docs/helm-chart.md)).
The chart in the repository downloads the library chart with `helm
dependency build` (see [Installation](#installation)).

## Configuration

The image contains the app's complete base config
([`pro_tes/config.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/config.yaml)).
The chart renders a config overlay into the `<app>-config` ConfigMap (see
[Names and labels](#names-and-labels)), which FOCA merges into the base
config on startup (via `FOCA_CONFIG_OVERLAY`), for the app and the worker:

- `db.host` is set to the release's MongoDB Service (`<app>-mongodb`),
  unless MongoDB is external (see [External MongoDB](#external-mongodb));
- `config` overrides keys of the base config; it takes precedence over the
  generated values. Mappings are merged recursively, all other values
  (including lists) replace those of the base config (see
  [Configuration](https://protes.readthedocs.io/en/latest/configuration/)).
  `server.host` and `server.port` have no effect, as they apply to the
  development server only (see [Server](#server)); the broker is set via its
  Secret, not `jobs` (see [Celery worker and
  broker](#celery-worker-and-broker)).

The identity provider is deployment configuration, without defaults (see
[Access control](https://protes.readthedocs.io/en/latest/access-control/)).
Rendering fails, as the app would refuse to start, unless each deployment
sets:

- access control, enabled and enforced (as by default:
  `config.security.access_control.enabled` and `enforce`), so that only the
  owners of tasks can access them;
- the trusted token issuers (`config.security.auth.issuers`);
- the audience (`config.security.auth.audience`), unless
  `config.security.auth.verify_audience` is `false`, which accepts tokens for
  any audience;
- an admin group mapped to the role `admin`
  (`config.security.access_control.group_mapping`), unless
  `config.custom.access_control.require_admin` is `false`;
- the claim with the groups of users
  (`config.security.access_control.claim_groups`), if groups are mapped.

These booleans are checked as the app parses them (e.g., `"no"` is
`false`); rendering fails on other values.

Keep the values of each deployment in its own values file outside this
repository (e.g., `values-prod.yaml` in the deployment's infrastructure
repository), for example:

```yaml
hostName: tes.example.org
mongodb:
  auth:
    existingSecret: protes-mongodb-credentials
broker:
  existingSecret: protes-celery
config:
  security:
    auth:
      # Trusted token issuers (`iss` claim)
      issuers: ["https://login.example.org/oidc/"]
      # Client IDs of the apps calling proTES
      audience: ["my-portal-client-id"]
      # Claims released via user info only, not in access tokens
      userinfo_claims: ["groups"]
    access_control:
      # Claim with the groups of users
      claim_groups: groups
      group_mapping:
        compute-users: user
        compute-admins: admin
  custom:
    tes:
      # TES instances that tasks are distributed to; proTES authenticates
      # at them with its own access token (`auth: client_credentials`, the
      # default), unless `auth` is `basic` or `none`
      service_list:
        - url: "https://tes.example.org"
      # Identity provider of the client of proTES (credentials from
      # `PROTES_CLIENT_ID` and `PROTES_CLIENT_SECRET`, see Secrets)
      client_credentials:
        issuer: "https://login.example.org/oidc/"
```

Set the service info (`config.service_info.service`, see [Service
info](https://protes.readthedocs.io/en/latest/configuration/#service-info))
for the deployment, too. Pods are restarted when the ConfigMap changes. Do
not edit the ConfigMap with `kubectl edit`: Helm overwrites it on the next
upgrade. As `config` is stored in a ConfigMap, it must not contain secrets
(see [Secrets](#secrets)).

## Image

The app image is `protes.image` (default `elixircloud/protes`) with tag
`protes.tag`, which defaults to the chart's `appVersion`; the worker runs the
same image. Charts attached to [GitHub
releases](https://github.com/elixir-cloud-aai/proTES/releases) have the
release version as `version` and `appVersion`, so they deploy the image of
their release (`X.Y.Z`). In the repository, both are placeholders, and
`appVersion` is `dev`: the chart deploys the latest image of branch `dev`,
which changes with every commit. Deployments from the repository should set
the tag of a release (e.g., `0.3.0`) or of a commit on `dev`
(`sha-<commit>`, the full commit SHA) in the values file:

```yaml
protes:
  tag: sha-0123456789abcdef0123456789abcdef01234567
```

`protes.image` may also include a tag or digest (e.g.,
`elixircloud/protes:0.3.0`, as in earlier chart versions); then `protes.tag`
must be empty. See [Versioning and
releases](https://protes.readthedocs.io/en/latest/development/#versioning-and-releases)
for the image tags.

The image is pulled on every start of a pod (`protes.imagePullPolicy:
Always`), so that moving tags such as `dev` deploy the latest image; set
`IfNotPresent` for fixed tags or digests, or `Never` for images loaded into
the nodes (e.g., with `kind load`). The images of MongoDB, RabbitMQ and
Flower are pinned to patch versions and pulled only if missing.

The chart needs images built with FOCA 0.17 or later (installed as a
package, with the worker entry point `pro_tes.celery_worker`; see
[Image](https://protes.readthedocs.io/en/latest/upgrading/#image)).

## Installation

Create the Secrets with the MongoDB credentials (see
[Authentication](#authentication)) and the broker URL (see [Celery worker
and broker](#celery-worker-and-broker)), and set their names in the values
file (`mongodb.auth.existingSecret`, `broker.existingSecret`). Then install
the chart of a [release](https://github.com/elixir-cloud-aai/proTES/releases)
(`protes-X.Y.Z.tgz`, attached to the release `vX.Y.Z`):

```bash
helm install protes \
  https://github.com/elixir-cloud-aai/proTES/releases/download/vX.Y.Z/protes-X.Y.Z.tgz \
  -n <namespace> -f values-prod.yaml
```

To install the chart from the `deployment` folder of the repository instead
(e.g., to test changes of the chart), download the library chart (into
`charts/`, the version locked in `Chart.lock`), and set `protes.tag` (see
[Image](#image)), as the chart deploys the moving `dev` image otherwise:

```bash
helm dependency build deployment
helm install protes deployment -n <namespace> -f values-prod.yaml
```

`<namespace>` is the namespace to deploy to; `protes` is the name of the
release, which is referenced when upgrading or uninstalling. Helm prints the
app's URL and a test request after installing (`helm get notes protes -n
<namespace>` shows them again).

## Secrets

The chart creates no Secrets and has no default credentials: create the
Secrets before installing, and set their names in the values. Credentials
never appear in values files or ConfigMaps.

| Secret (value with its name) | Keys | Used by |
| --- | --- | --- |
| `mongodb.auth.existingSecret` (required, unless `mongodb.auth.enabled` is false) | `root-username`, `root-password` (bundled MongoDB only), `username`, `password` | MongoDB; the app and the worker (`MONGO_USERNAME`, `MONGO_PASSWORD`) |
| `broker.existingSecret` (required) | `broker-url`, optionally `result-backend`; `username`, `password` (bundled RabbitMQ only) | RabbitMQ; the app, the worker and Flower (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`) |
| `flower.existingSecret` (if `flower.enabled`) | `basic-auth` (`<user>:<password>`) | Flower (`FLOWER_BASIC_AUTH`) |
| any, via `protes.extraEnv` | any | the app and the worker |

`protes.extraEnv` adds environment variables to the app's and the worker's
containers (Kubernetes `EnvVar`s), e.g., credentials of proTES for the TES
instances from a Secret of your own; rendering fails for the variables that
the chart sets itself. Credentials of proTES for TES instances (client
credentials of an identity provider, or HTTP Basic credentials, see
[Credentials of TES
instances](https://github.com/elixir-cloud-aai/proTES/blob/dev/docs/access-control.md#credentials-of-tes-instances))
are read from environment variables: `PROTES_CLIENT_ID` and
`PROTES_CLIENT_SECRET` for TES instances with `auth: client_credentials`,
and those named in `basic.username_env` and `basic.password_env` for TES
instances with `auth: basic`, e.g.:

```yaml
protes:
  extraEnv:
  - name: PROTES_CLIENT_ID
    valueFrom:
      secretKeyRef:
        name: protes-tes-credentials
        key: client-id
  - name: PROTES_CLIENT_SECRET
    valueFrom:
      secretKeyRef:
        name: protes-tes-credentials
        key: client-secret
```

Helm does not restart pods when a Secret changes (only when the config
changes), as the chart does not manage the Secrets: restart them after
changing one (`kubectl rollout restart deploy/protes deploy/protes-worker -n
<namespace>`).

## Names and labels

Resource names are derived from the release name, so that several releases
can be installed in one namespace:

- **App** (Deployment, Service, Ingress, HTTPRoute or Route, NetworkPolicy;
  ConfigMap `<app>-config`): `<app>` is `<release>-protes`, or the release
  name if it contains `protes` (e.g., `protes` for the release `protes`,
  `prod-protes` for the release `prod`);
- **Worker** (Deployment): `<app>-worker`;
- **MongoDB** (Deployment, Service, PersistentVolumeClaim, NetworkPolicy;
  ConfigMap `<app>-mongodb-init-script`): `<app>-mongodb`; the app connects
  to this Service;
- **Broker** (StatefulSet, Service, PersistentVolumeClaim, NetworkPolicy;
  ConfigMap `<app>-broker-config`): `<app>-broker`, the host of the broker
  URL;
- **Flower** (Deployment, Service, NetworkPolicies `<app>-flower` and
  `<app>-broker-from-flower`): `<app>-flower`.

`nameOverride` replaces `protes` in the names and the
`app.kubernetes.io/name` label, `fullnameOverride` replaces `<app>`
(camelCase, as in other Helm charts); set `fullnameOverride` to know the
broker's host before installing. Set them when installing only: changing
them later changes the pod selectors of the Deployments, which cannot be
changed, so the upgrade fails. Names are truncated to 63 characters. The
examples in this document use the release `protes`.

All resources have the [standard
labels](https://helm.sh/docs/chart_best_practices/labels/)
(`app.kubernetes.io/name`, `instance`, `version` and `managed-by`, and
`helm.sh/chart`); pods are selected by name, instance and
`app.kubernetes.io/component` (`app`, `worker`, `mongodb`, `broker` or
`flower`), e.g.:

```bash
kubectl get pods -n <namespace> -l app.kubernetes.io/instance=protes
```

## Exposing the app

The chart exposes the app for `hostName` in one of three ways (see
[Exposing the
app](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.2.0/docs/helm-chart.md#exposing-the-app) in
the library chart's documentation for TLS, body size and rate limits,
NetworkPolicy peers and keep-alive settings of ingress-nginx, Traefik, the
Gateway API, OpenShift and cloud load balancers):

- an Ingress (`protes.ingress`, enabled by default), for any ingress
  controller; `className` sets its class, `annotations` its annotations
  (e.g., for the ingress controller; they take precedence over those set
  by the chart for `https`, `null` removes one), and `https.enabled`
  requests a certificate from cert-manager (via the ClusterIssuer
  `https.issuer`);
- a Gateway API HTTPRoute (`protes.httpRoute`), attached to an existing
  Gateway (`parentRefs`), optionally with a second HTTPRoute that redirects
  HTTP to HTTPS (`httpsRedirect`); set `protes.ingress.enabled: false` to use
  it, e.g.:

    ```yaml
    protes:
      ingress:
        enabled: false
      httpRoute:
        enabled: true
        parentRefs:
        - name: gateway
          namespace: gateway-system
          sectionName: https
        httpsRedirect:
          enabled: true
          parentRefs:
          - name: gateway
            namespace: gateway-system
            sectionName: http
    ```

- an OpenShift Route, with TLS termination at the router, if neither is
  enabled, the cluster serves Routes (`route.openshift.io/v1`) and
  `protes.route.enabled` is true (the default); otherwise the app is only
  reachable within the cluster, and Helm prints a warning after
  installing.

The chart sets no defaults for a particular ingress controller or Gateway
implementation: their annotations, policies and NetworkPolicy peers go
into the values (see the library chart's documentation, linked above, for
examples).

All of them route all paths (`/`) to the app, including FOCA's permissions
API (`/admin/access-control/`, for admins only; see [Permissions
API](https://protes.readthedocs.io/en/latest/access-control/#permissions-api)).
To keep that API off the internet, block `/admin/` in the proxy in front of
the cluster, or disable the chart's Ingress (and, on OpenShift, its Route:
`protes.route.enabled: false`) and create one of your own that routes only
`/ga4gh/tes/v1/` to the app's Service (port `http`).

Clients send bearer tokens, so serve the app over HTTPS only: by default, the
Ingress has no TLS (`https.enabled: false`) and serves plain HTTP, so TLS
must be terminated in front of the cluster (e.g., by a load balancer); Helm's
notes after installing say so.

The app rejects request bodies over `config.server.max_content_length`
bytes (default: 1 MiB) in every setup; the chart sets no limit at the
proxy. ingress-nginx limits request bodies to 1 MiB by default, the
app's default: if you raise the app's limit, e.g., to 8 MiB, raise that
of ingress-nginx with an annotation, too (other ingress controllers
ignore it):

```yaml
config:
  server:
    max_content_length: 8388608
protes:
  ingress:
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: 8m
```

## Celery worker and broker

The worker (`worker`, enabled by default) runs the app's image with
`worker.command` (`celery --app pro_tes.celery_worker worker`), with the
app's config overlay, MongoDB credentials, `protes.extraEnv` and security
contexts. It polls each unfinished task every few seconds, one short poll
per process at a time (`--concurrency=2` by default), so a few processes
track many tasks: raise the concurrency, `worker.replicas` and
`worker.resources` if polls wait in the queue (see [Task
tracking](https://protes.readthedocs.io/en/latest/operations/#task-tracking)).
Restarts and upgrades of the worker don't stop the tracking: polls are
acknowledged after they ran, so the broker delivers those of a killed worker
again, and workers resume the tracking of all unfinished tasks when they
start. Without a worker (e.g., one
run elsewhere, connected to the same broker and MongoDB), the states of
tasks are not tracked, and Helm's notes warn.

The broker (`broker`, enabled by default) is RabbitMQ, with one pod, and its
data (queued tasks) in a PersistentVolumeClaim (`broker.persistence`). Both
the app and the worker connect to it with the URL in the Secret
`broker.existingSecret` (key `broker-url`), which also has the credentials of
RabbitMQ's user (`username`, `password`). For the release `protes`
(broker host `protes-broker`):

```bash
password="$(openssl rand -hex 16)"
kubectl create secret generic protes-celery -n <namespace> \
  --from-literal=username=protes \
  --from-literal=password="${password}" \
  --from-literal=broker-url="pyamqp://protes:${password}@protes-broker:5672//"
```

URL-encode reserved characters of the credentials in `broker-url`. RabbitMQ
creates its user from the Secret only when it starts with empty data: to
change the password later, change it in the Secret and in RabbitMQ
(`printf '%s' "${password}" | kubectl exec -i statefulset/protes-broker -n
<namespace> -- rabbitmqctl change_password protes`), then restart the app,
the worker and Flower. For an external broker, set `broker.enabled: false`
and only `broker.existingSecret` (with `broker-url`). See [Deployment
notes](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.2.0/docs/helm-chart.md#deployment-notes)
of the library chart for the persistence of queued tasks and the result
backend (key `result-backend`; by default `jobs.backend` of the config).

## Flower

[Flower](https://flower.readthedocs.io/) shows the worker's tasks, including
their arguments, so it is off by default (`flower.enabled: false`) and
requires basic authentication when enabled: create a Secret with the key
`basic-auth` (`<user>:<password>`) and set its name in `flower.existingSecret`:

```bash
kubectl create secret generic protes-flower -n <namespace> \
  --from-literal=basic-auth="admin:$(openssl rand -hex 16)"
```

```yaml
flower:
  enabled: true
  existingSecret: protes-flower
```

Flower connects to the broker with `broker-url` of `broker.existingSecret`;
the worker sends the events it shows (`--task-events` in `worker.command`).
The chart creates no Ingress for it: its NetworkPolicy
(`flower.networkPolicy`) admits no one by default, and Flower is reached via
a port forward, which NetworkPolicies don't affect:

```bash
kubectl port-forward svc/protes-flower 5555:5555 -n <namespace>
```

To expose it, add the pods of your proxy to
`flower.networkPolicy.ingressFrom`, and create an Ingress of your own for the
Service `<app>-flower` (port `http`), served over HTTPS only.

## Security context

All containers run as non-root users, without privilege escalation or
capabilities, with the runtime's default seccomp profile and a read-only root
file system; they write to `emptyDir` volumes (`/tmp`) and MongoDB and
RabbitMQ to their data volumes. The app image (and so the worker) runs as
user 1000, Flower's as user 1000 (`flower`); the MongoDB and RabbitMQ images
use user 999, which the chart sets (`runAsUser`, `runAsGroup`), and
`fsGroup` makes their data volumes writable. No pod gets a service account
token (`automountServiceAccountToken: false`), as none calls the Kubernetes
API.

The `/tmp` volumes are limited to 64 MiB each by `tmpSizeLimit` of `protes`,
`worker`, `mongodb`, `broker` and `flower` (a positive Kubernetes quantity,
e.g., `64Mi`, or a number of bytes; `null` removes the limit); the kubelet
evicts pods that exceed it.

On OpenShift (detected by the API `security.openshift.io/v1`), the chart
omits `runAsUser`, `runAsGroup` and `fsGroup`, as OpenShift assigns them from
the namespace's range. The contexts are set in the values
(`podSecurityContext` and `securityContext` of `protes`, `mongodb`, `broker`
and `flower`; the worker uses those of `protes`); mappings are merged with
the defaults, and `null` removes a default.

## Network policies

The chart creates
[NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
that restrict which pods may connect to its pods (ingress); connections from
the pods, e.g., of the app and the worker to the TES instances and the token
issuers, are not restricted. They only take effect if the cluster's network
plugin enforces NetworkPolicies (e.g., Calico or Cilium). NetworkPolicies add
up: a pod that several policies select accepts the connections that any of
them allows.

- **MongoDB** (`mongodb.networkPolicy.enabled`, default `true`): only the
  app's and the worker's pods of the same release may connect.
- **Broker** (`broker.networkPolicy.enabled`, default `true`): only the
  app's and the worker's pods of the same release, and Flower's, if enabled
  (`<app>-broker-from-flower`).
- **Flower** (`flower.networkPolicy.enabled`, default `true`): only the peers
  in `flower.networkPolicy.ingressFrom` (none by default).
- **App** (`protes.networkPolicy.enabled`, default `false`): only the
  NetworkPolicy peers in `protes.networkPolicy.ingressFrom` may connect.
  There are none by default, as they depend on the cluster: when enabling
  the policy, set them to the pods of the cluster's ingress controller,
  Gateway or OpenShift router (see [NetworkPolicy
  peers](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.2.0/docs/helm-chart.md#networkpolicy-peers)
  in the library chart's documentation), and add other clients in the
  cluster that call the app's Service directly; otherwise, the app is
  unreachable through the Ingress (or Route). Rendering fails if the
  policy is enabled without peers (which would allow all sources), e.g.:

    ```yaml
    protes:
      networkPolicy:
        enabled: true
        ingressFrom:
        # ingress-nginx: its pods in the namespace `ingress-nginx`
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
          podSelector:
            matchLabels:
              app.kubernetes.io/name: ingress-nginx
        # A client in the release's namespace
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: my-portal
    ```

`kubectl exec` and `kubectl port-forward` don't go through the pod network
and are not affected. The policies don't block the probes: connections from
the pod's own node, where the kubelet runs them, are allowed.

## Server

The image serves the app with Gunicorn on port 8080 (see
[Server](https://protes.readthedocs.io/en/latest/configuration/#server)). The
chart passes the settings in `protes.gunicorn` to it (via
`GUNICORN_CMD_ARGS`); the defaults are the image's (one worker process with
4 threads, a timeout of 30 seconds, a graceful timeout of 20 seconds, and a
keep-alive of 100 seconds, longer than the upstream keep-alive timeout of
common ingress controllers: 60 seconds for ingress-nginx, 90 seconds for
Traefik). Rendering fails if `extraArgs` set
`--bind`/`-b`, `--preload` or `--graceful-timeout`. Each Gunicorn worker
loads the whole app, so raise `protes.resources` before raising `workers`.
On shutdown, the app keeps serving for `protes.preStopSleepSeconds` (default
5) before Gunicorn stops, so that the ingress controller stops sending
requests to the pod first. Gunicorn trusts `X-Forwarded-*` headers from the
local host only; to trust those of the ingress controller, set its pods'
addresses in `FORWARDED_ALLOW_IPS` via `protes.extraEnv` (never `*` if
clients can reach the app's pods directly).

## Probes

- **App:** ready once it serves its Swagger UI (`/ga4gh/tes/v1/ui/`; it
  needs neither authentication nor the database; set `protes.probePath` if
  the config disables the Swagger UI); up to 5 minutes are allowed for the
  start.
- **Worker:** no probes by default (`worker.readinessProbe`,
  `worker.livenessProbe`), e.g., an `exec` probe with `celery --app
  pro_tes.celery_worker inspect ping`, with long periods and timeouts, as it
  starts Python.
- **MongoDB** and **RabbitMQ:** ready once they accept connections.
- **Flower:** ready once it accepts connections.

## MongoDB

### Version

The chart pins MongoDB (`mongodb.image`, default `mongo:7.0.45`; MongoDB 8.0
refuses to start on Linux kernels 6.19 to 7.0.13, cf.
[SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)). MongoDB only
starts on data whose feature compatibility version (FCV) it supports, so
check the running version and the FCV before changing the image of an
existing database:

```bash
kubectl exec deploy/protes-mongodb -n <namespace> -- sh -c 'mongosh --quiet \
  -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --eval "printjson({version: db.version(), fcv: db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1}).featureCompatibilityVersion})"'
```

Upgrades go one major version at a time (set the image of the next version,
wait for the rollout, then set the FCV to that version); downgrades of the
MongoDB binary are not supported: dump the database (`mongodump`) and restore
it (`mongorestore`) into a new, empty volume with the older version instead.

### Authentication

MongoDB requires authentication by default. Before installing the chart,
create a Secret with the credentials of the MongoDB root user and the app
user:

```bash
kubectl create secret generic protes-mongodb-credentials -n <namespace> \
  --from-literal=root-username=root \
  --from-literal=root-password="$(openssl rand -hex 24)" \
  --from-literal=username=protes \
  --from-literal=password="$(openssl rand -hex 24)"
```

and set its name in `mongodb.auth.existingSecret`. To run MongoDB without
authentication, set `mongodb.auth.enabled: false` explicitly; any pod that
can reach the MongoDB service can then read and modify the data.

MongoDB creates both users when it starts with an empty data directory, with
an init script (ConfigMap `<app>-mongodb-init-script`); the app and the
worker connect as the app user, which may only access the app's database,
`taskStore`, which also holds the rules of access control. If the config
sets another database for them (`config.security.access_control.db_name`),
the app user gets access to it too, for new databases; for existing ones,
grant it with `mongosh` as the root user. Changing the Secret does not change
the users: change a password in MongoDB first, then in the Secret, and
restart the app and the worker. See [Deployment
notes](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.2.0/docs/helm-chart.md#deployment-notes)
of the library chart for creating the users of an existing database.

### Persistence

MongoDB keeps its data in a PersistentVolumeClaim (`<app>-mongodb`;
`mongodb.persistence`: `size`, `accessModes`, `storageClass`, or an
`existingClaim`). Helm keeps the claim, and with it the database, when the
release is uninstalled (`mongodb.persistence.resourcePolicy: keep`); a new
release of the same name in the same namespace uses it again. To remove the
data, delete the claim after uninstalling the release (`kubectl delete pvc
protes-mongodb -n <namespace>`). With `mongodb.persistence.enabled: false`
(e.g., for tests), the data is lost when MongoDB's pod is replaced.

### External MongoDB

To use a MongoDB outside the release (e.g., a managed database), set
`mongodb.enabled: false` and its host (and port) in the config; rendering
fails without `config.db.host`:

```yaml
mongodb:
  enabled: false
  auth:
    # Secret with the keys `username` and `password` of the app user
    existingSecret: protes-mongodb-credentials
config:
  db:
    host: mongodb.example.org
    port: 27017
```

The app authenticates as the app user against its database (`taskStore`),
so create that user there, with the `readWrite` role on `taskStore`; allow
the app's and the worker's pods to connect to the database.

## Upgrades

Change the values file (e.g., `config` for the config, `protes.tag` for the
image of a chart from the repository) and upgrade the release, with the
chart of a release (or `deployment`, as when installing):

```bash
helm upgrade protes \
  https://github.com/elixir-cloud-aai/proTES/releases/download/vX.Y.Z/protes-X.Y.Z.tgz \
  -n <namespace> -f values-prod.yaml
```

Upgrade with the values file (`-f`), not with `--reuse-values`, which would
keep values that the chart no longer has. The app's and the worker's pods
are replaced when the config changes.

### From the legacy chart

The legacy chart (versions 2.x and 3.0.0, before the library chart) used
other values, resource names and selectors. The current chart renames the
resources (see [Names and labels](#names-and-labels); e.g., `mongodb`
becomes `protes-mongodb`) and changes the selectors of the Deployments,
which cannot be changed in place, so **releases of the legacy chart cannot be
upgraded: uninstall them and install the current chart.** An upgrade would
delete the volume with the MongoDB data (`mongodb-volume`), so `helm upgrade`
fails for releases with Deployments or claims of the legacy chart (only
when Helm can query the cluster: not in `helm template` or client-side dry
runs). Uninstalling also deletes the claim and, usually, the data with it.

Rendering fails for the values of the legacy chart, which the current chart
would ignore:

| Legacy values | Now |
| --- | --- |
| `applicationDomain` (host `<protes.appName>.<applicationDomain>`) | `hostName` (the full host name) |
| `clusterType` | none: a Route is created on OpenShift without an Ingress or HTTPRoute |
| `storageAccessMode` | `mongodb.persistence.accessModes`, `broker.persistence.accessModes` |
| `protes.appName`, `mongodb.appName`, `flower.appName` | `fullnameOverride` (see [Names and labels](#names-and-labels)) |
| `celeryWorker` | `worker` (with the image of `protes`) |
| `rabbitmq` (user `guest:guest`) | `broker`, with `broker.existingSecret` |
| `mongodb.secret` (a Secret created by the chart) | `mongodb.auth.existingSecret` (a Secret of your own) |
| `flower.basicAuth` (default `admin:admin`; Flower was always deployed) | `flower.existingSecret`; Flower is off by default |

To keep the tasks, dump the database before uninstalling and restore it into
the new release. The app is down from the first to the last step. The
commands are for the release `protes` and the legacy chart's default names
(`protes`, `celery-worker` and `mongodb`, from `protes.appName`,
`celeryWorker.appName` and `mongodb.appName`):

1. Stop the app and the worker, so that the data does not change during the
   dump:

    ```bash
    kubectl scale deploy/protes deploy/celery-worker -n <namespace> --replicas=0
    ```

2. Dump the app's database (`taskStore`, or `mongodb.secret.databaseName` of
   the legacy values, if set) to a local file, and check that MongoDB can
   read the dump; continue only if `Dump OK` is printed:

    ```bash
    kubectl exec deploy/mongodb -n <namespace> -- sh -c 'mongodump --quiet \
      -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
      --authenticationDatabase admin --db taskStore --archive' \
      > taskStore.archive \
    && kubectl exec -i deploy/mongodb -n <namespace> -- sh -c 'mongorestore \
      --dryRun -u "$MONGO_INITDB_ROOT_USERNAME" \
      -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin \
      --archive' < taskStore.archive \
    && echo "Dump OK"
    ```

3. Uninstall the release (`helm uninstall protes -n <namespace>`), update the
   values file (see the table above) and create the Secrets (see
   [Secrets](#secrets)). The legacy chart ran `mongo:noble` (MongoDB 8), whose
   dumps may not restore into the chart's default version (see
   [Version](#version)); set `mongodb.image` to the dumped version if needed.

4. Install the current chart (see [Installation](#installation)), stop its
   app and worker again, so that they don't write during the restore, and
   wait for MongoDB:

    ```bash
    kubectl scale deploy/protes deploy/protes-worker -n <namespace> --replicas=0
    kubectl rollout status deploy/protes-mongodb -n <namespace>
    ```

5. Restore the dump into `taskStore` (with `--nsFrom '<database>.*' --nsTo
   'taskStore.*'` if the legacy database had another name), replacing the
   collections the app has created, then start the app and the worker:

    ```bash
    kubectl exec -i deploy/protes-mongodb -n <namespace> -- sh -c \
      'mongorestore --quiet --drop --archive \
      -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
      --authenticationDatabase admin' < taskStore.archive
    kubectl scale deploy/protes deploy/protes-worker -n <namespace> --replicas=1
    ```

Tasks queued in the legacy RabbitMQ are not migrated: tasks that were still
being tracked keep their last stored state. See
[Upgrading](https://protes.readthedocs.io/en/latest/upgrading/) for the
changes of the app since the last deployed version.
