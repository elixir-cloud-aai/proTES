# Operations

## Components

A deployment of proTES consists of:

- the app (the TES API), served by Gunicorn;
- one or more Celery workers, which track forwarded tasks at the TES
  instances (see [Task tracking](#task-tracking));
- RabbitMQ, the broker between the app and the workers;
- MongoDB, which stores the tasks (database `taskStore`, collection
  `tasks`);
- optionally Flower, a web UI to monitor the Celery workers.

The Docker Compose setup and the [Helm chart](deployment.md) run all of them.

## Logs

The app and the workers log to standard error (section `log` of the
config): each request to the API, the plugins loaded and failed, the TES
instances that a task is forwarded to, and errors of the TES instances. Read them with
`docker compose logs` or `kubectl logs`.

## Task tracking

The state of a task is the state last tracked from the TES instance that runs
it (see [Tasks](api.md#tasks)). The Celery workers poll each unfinished task
at its TES instance every `polling.wait` seconds (see
[Tasks](configuration.md#tasks)): each poll is a short Celery task, which
schedules the next one, so no worker process is held for the runtime of a
task, and long tasks don't keep the others from being tracked. Once a task has
finished, its logs and outputs are stored, and tracking ends; a canceled
task stays `CANCELED` until its TES instance has finished it. After
`polling.attempts` consecutive failed polls (e.g., while the TES instance or
the identity provider is down), the state of the task is `UNKNOWN` until the
TES instance answers again, and polls back off to every `polling.max_wait`
seconds; such tasks are not marked as failed, as they may still be running.

The workers need little capacity per task: a worker process is busy only
for the duration of a poll (one request to the TES instance). As a rule of
thumb, one process tracks about as many tasks as polls fit into
`polling.wait`; e.g., with polls of 0.1 seconds and the default `wait` of 3
seconds, about 30 tasks. Raise `--concurrency` (processes per worker) or the
number of workers for more unfinished tasks, and watch the queue length in
Flower or RabbitMQ: if polls wait in the queue, tasks are polled less often
than configured, but are still tracked.

Polls are acknowledged after they ran (`acks_late`), so the broker delivers
the poll of a worker that is lost (e.g., killed after its grace period)
again, and polls that are scheduled but not yet due go back to the broker
when a worker shuts down. Polls that fail because MongoDB is unavailable
are retried, with backoff up to `polling.max_wait` seconds, and those that
fail because RabbitMQ is go back to it. Each
task is tracked by one chain of polls (the stored task holds the identifier
of its current poll and when it ran, `tracking`). When a worker starts, and
then every `polling.max_wait` seconds, it resumes the tracking of stored
unfinished tasks that were forwarded to a TES instance but haven't been
polled for three times `polling.max_wait` seconds (or ever), e.g., because
RabbitMQ lost its data. Upgrades and restarts of the workers therefore don't
stop the tracking of tasks; they only delay their next polls. Canceled
tasks are not resumed: if their tracking is lost before their TES instance
has finished them, they stay `CANCELED`, without their final logs.

Tasks whose creation was cut off (e.g., if a Gunicorn worker was stopped
while it forwarded them, during an upgrade) stay in state `UNKNOWN`
without identifier at a TES instance: they can be neither tracked nor
canceled. The workers set them to `SYSTEM_ERROR` when they resume the
tracking of tasks, once the tasks are older than
`post_task.max_create_time` seconds (default 3600; see
[Tasks](configuration.md#tasks)). As for tasks that a TES instance did not
confirm, the task may run at a TES instance: it is kept, and the logs of
the API name the TES instances that were tried.

## Task owners

Only the owner of a task can read and cancel it through the API, not admins
(see [Task ownership](access-control.md#task-ownership)). Operators work on
the database instead (database `taskStore`, or the one in `MONGO_DBNAME`),
e.g., with `mongosh`; the owner of a task is in `user_id` of its document in
the collection `tasks`, and as owner roles (`g2` rules) in the collection
`policy_rules`. Owners are subjects (`sub` claims) of the users' access
tokens; with several trusted issuers, they are namespaced by issuer
(`<issuer>|<sub>`, or the issuer's `namespace`; bare for an issuer with
`namespace: ""`), so `<sub>` below stands for that form:

```js
// tasks of a user
db.tasks.find({ user_id: "<sub>" }, { "task.id": 1, "task.state": 1 })
// tasks without owner, e.g., created by earlier versions
db.tasks.find({ user_id: { $exists: false } }, { "task.id": 1 })
```

Tasks created before owners were recorded have no owner, so nobody can see
them through the API. To give such a task to a user, add the two owner roles
via the [permissions API](access-control.md#permissions-api), as an admin:

```bash
for resource in "/ga4gh/tes/v1/tasks/<id>" "/ga4gh/tes/v1/tasks/<id>:cancel"; do
  curl -X POST "http://localhost:8080/admin/access-control/permissions" \
    -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
    -d "{\"policy_type\": \"g2\", \"rule\": {\"v0\": \"user:<sub>\", \"v1\": \"owner\", \"v2\": \"${resource}\"}}"
done
```

and set `user_id` of the task to the user's `sub` in the database. To purge
tasks, delete their documents and their owner roles, e.g., those without
owner:

```js
db.tasks.deleteMany({ user_id: { $exists: false } })
```

Owner roles left by deleted tasks don't give access to other tasks (their
owners get `404 Not Found`, and their identifiers are not used for new
tasks), but are kept in memory by each instance of proTES; delete them
together with the documents, via the permissions API, or in the database
(`db.policy_rules.deleteMany({ ptype: "g2", v2: { $in: [...] } })`, with the
paths of the deleted tasks, and those ending in `:cancel`).

Rules of access control changed in the database directly (e.g., with
`mongosh`) apply only once proTES is restarted (all instances; e.g.,
`kubectl rollout restart deployment/<app>` with the Helm chart, `<app>` as in its README, Names and labels), as
proTES keeps the rules in memory and notices only the changes made through
FOCA, e.g., via the permissions API (see FOCA's [migration guide to
0.18](https://foca.readthedocs.io/en/latest/migration-0.18/#rules-changed-in-the-database-directly)).

## Abusive tasks

Admins can't cancel the tasks of others through the API. To stop a task,
cancel it at the TES instance that runs it: its URL is in
`tes_endpoint.host` of the task document, and its identifier there in
`tes_endpoint.task_id`; the operators of the TES instance can also cancel
it on their side. If a worker still tracks the task, proTES records its new
state; otherwise, set `task.state` of the document to `CANCELED`. To keep a
user from creating more tasks, remove them from the groups with the role
`user` at the identity provider; to keep proTES from sending tasks to a TES
instance, remove it from `custom.tes.backends`, or, if it was discovered
in a service registry, have a registry admin remove its verification (it is
no longer used once the registry has been read again, see `refresh_interval`
in [Discovery in a service
registry](configuration.md#discovery-in-a-service-registry)).

## Verifying images and charts

Images on Docker Hub (`elixircloud/protes`) of releases (`X.Y.Z`, from
version tags) and of commits on `dev` (`dev`, `sha-<commit>`) are built by
this repository's workflows on GitHub Actions, with an SBOM and the build
provenance attached as attestations, a [GitHub artifact
attestation](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
of their provenance, and a keyless [Sigstore](https://www.sigstore.dev/)
signature (images tagged by date, of earlier deployments, are not). To check
that an image was built by the release workflow, run (with the [GitHub
CLI](https://cli.github.com/) or
[cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3 or
later, which reads the signatures in the Sigstore bundle format):

```bash
gh attestation verify oci://docker.io/elixircloud/protes:{{ version }} \
    --repo elixir-cloud-aai/proTES \
    --signer-workflow elixir-cloud-aai/proTES/.github/workflows/release.yaml \
    --source-ref refs/tags/v{{ version }}
cosign verify docker.io/elixircloud/protes:{{ version }} \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity https://github.com/elixir-cloud-aai/proTES/.github/workflows/release.yaml@refs/tags/v{{ version }}
```

These are the commands for release `{{ version }}`: replace it with the
version to verify. `--source-ref` and the ref in
`--certificate-identity` check that the workflow ran for the release tag,
not for another branch. Images of commits on `dev` (`dev`, `sha-<commit>`)
are signed and attested by the CI workflow instead: use `--signer-workflow
elixir-cloud-aai/proTES/.github/workflows/ci.yml`,
`--source-ref refs/heads/dev` and `--certificate-identity
https://github.com/elixir-cloud-aai/proTES/.github/workflows/ci.yml@refs/heads/dev`.
The workflow files were renamed after 0.3.0: releases after 0.3.0 are
signed by `release.yml` instead of `release.yaml`, and images of commits on
`dev` built before the rename by `checks.yaml` instead of `ci.yml`.
To show an image's SBOM or provenance, run:

```bash
docker buildx imagetools inspect docker.io/elixircloud/protes:{{ version }} \
    --format '{{ json .SBOM }}'
docker buildx imagetools inspect docker.io/elixircloud/protes:{{ version }} \
    --format '{{ json .Provenance }}'
```

The Helm chart attached to a release (`protes-{{ chart_version }}.tgz`) has a GitHub
artifact attestation of its provenance and a Sigstore bundle with its
signature (`protes-{{ chart_version }}.tgz.sigstore.json`, attached too):

```bash
gh release download v{{ version }} --repo elixir-cloud-aai/proTES \
    --pattern 'protes-{{ chart_version }}.tgz*'
gh attestation verify protes-{{ chart_version }}.tgz \
    --repo elixir-cloud-aai/proTES \
    --signer-workflow elixir-cloud-aai/proTES/.github/workflows/release.yaml \
    --source-ref refs/tags/v{{ version }}
cosign verify-blob protes-{{ chart_version }}.tgz \
    --bundle protes-{{ chart_version }}.tgz.sigstore.json \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity https://github.com/elixir-cloud-aai/proTES/.github/workflows/release.yaml@refs/tags/v{{ version }}
```

Images are scanned for known vulnerabilities with [Trivy](https://trivy.dev/)
in CI and again before a release is published (both fail on fixable critical
and high vulnerabilities), and the `latest` image weekly. The repository's
code scanning alerts show the findings that have a fix, which the maintainers
can act on; findings without a fix (often OS packages of the base image)
appear there once a fix is released. The weekly scan also keeps a full report,
those findings included, as a workflow artifact of each run. The published
images (releases and `dev`) are the scanned ones: the workflows push each
image by digest, untagged, scan exactly that digest, and only if the scan
passes (no fixable critical or high vulnerabilities, except accepted ones)
sign and tag it.
