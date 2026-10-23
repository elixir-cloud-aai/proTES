# Operations

## Components

A deployment of proTES consists of:

- the app (the TES API), served by Gunicorn;
- one or more Celery workers, which track forwarded tasks at the TES
  instances (scale them up for many concurrent tasks: each worker process
  tracks one task at a time);
- RabbitMQ, the broker between the app and the workers;
- MongoDB, which stores the tasks (database `taskStore`, collection
  `tasks`);
- optionally Flower, a web UI to monitor the Celery workers.

The Docker Compose setup and the [Helm chart](deployment.md) run all of them.

## Logs

The app and the workers log to standard error (section `log` of the
config): each request to the API, the middlewares applied, the TES instances
that a task is forwarded to, and errors of the TES instances. Read them with
`docker compose logs` or `kubectl logs`.

## Task states

The state of a task is the state last tracked from the TES instance that runs
it (see [Tasks](api.md#tasks)). Tracking is not resumed after the workers or
RabbitMQ restart: tasks that were being tracked keep their last recorded
state.

## Task owners

Only the owner of a task can read and cancel it through the API, not admins
(see [Task ownership](access-control.md#task-ownership)). Operators work on
the database instead (database `taskStore`, or the one in `MONGO_DBNAME`),
e.g., with `mongosh`; the owner of a task is in `user_id` of its document in
the collection `tasks`, and as owner roles (`g2` rules) in the collection
`policy_rules`:

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
tasks), but are loaded on each request; delete them together with the
documents, via the permissions API, or in the database
(`db.policy_rules.deleteMany({ ptype: "g2", v2: { $in: [...] } })`, with the
paths of the deleted tasks, and those ending in `:cancel`).

## Abusive tasks

Admins can't cancel the tasks of others through the API. To stop a task,
cancel it at the TES instance that runs it: its URL is in
`tes_endpoint.host` of the task document, and its identifier there in
`tes_endpoint.task_id`; the operators of the TES instance can also cancel
it on their side. If a worker still tracks the task, proTES records its new
state; otherwise, set `task.state` of the document to `CANCELED`. To keep a
user from creating more tasks, remove them from the groups with the role
`user` at the identity provider; to keep proTES from sending tasks to a TES
instance, remove it from `custom.tes.service_list`.

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
gh attestation verify oci://docker.io/elixircloud/protes:X.Y.Z \
    --repo elixir-cloud-aai/proTES \
    --signer-workflow elixir-cloud-aai/proTES/.github/workflows/release.yaml
cosign verify docker.io/elixircloud/protes:X.Y.Z \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity https://github.com/elixir-cloud-aai/proTES/.github/workflows/release.yaml@refs/tags/vX.Y.Z
```

Replace `X.Y.Z` with the version to verify. Images of commits on `dev`
(`dev`, `sha-<commit>`) are signed and attested by the CI workflow instead:
use `--signer-workflow
elixir-cloud-aai/proTES/.github/workflows/checks.yaml` and
`--certificate-identity
https://github.com/elixir-cloud-aai/proTES/.github/workflows/checks.yaml@refs/heads/dev`.
To show an image's SBOM or provenance, run:

```bash
docker buildx imagetools inspect docker.io/elixircloud/protes:X.Y.Z \
    --format '{{ json .SBOM }}'
docker buildx imagetools inspect docker.io/elixircloud/protes:X.Y.Z \
    --format '{{ json .Provenance }}'
```

The Helm chart attached to a release (`protes-X.Y.Z.tgz`) has a GitHub
artifact attestation of its provenance and a Sigstore bundle with its
signature (`protes-X.Y.Z.tgz.sigstore.json`, attached too):

```bash
gh release download vX.Y.Z --repo elixir-cloud-aai/proTES \
    --pattern 'protes-X.Y.Z.tgz*'
gh attestation verify protes-X.Y.Z.tgz \
    --repo elixir-cloud-aai/proTES \
    --signer-workflow elixir-cloud-aai/proTES/.github/workflows/release.yaml
cosign verify-blob protes-X.Y.Z.tgz \
    --bundle protes-X.Y.Z.tgz.sigstore.json \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity https://github.com/elixir-cloud-aai/proTES/.github/workflows/release.yaml@refs/tags/vX.Y.Z
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
