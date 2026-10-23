# Helm chart

The files under this directory deploy proTES on Kubernetes (or OpenShift)
with [Helm](https://helm.sh/docs/intro/install/) (version 3):

- `templates/`: the resources of the deployment:
    - `mongodb/`: MongoDB, the database of proTES;
    - `rabbitmq/`: RabbitMQ, the broker between proTES and its Celery workers;
    - `protes/`: the proTES server and the Celery worker;
    - `flower/`: Flower, to monitor the Celery workers;
- `values.yaml`: the configuration of the chart;
- `Chart.yaml`: the metadata of the chart.

The app itself is configured by
[`pro_tes/config.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pro_tes/config.yaml)
(see [Configuration](https://protes.readthedocs.io/en/latest/configuration/)).

## Usage

First create a namespace in Kubernetes in which to deploy proTES. The
commands below assume that everything is created in the context of this
namespace. How the namespace is created depends on the cluster, so we won't
document it here.

Make sure you have both the Kubernetes client (`kubectl`) and Helm 3
installed, and clone the repository:

```bash
git clone https://github.com/elixir-cloud-aai/proTES.git
```

## Configuration

Update the configuration by modifying the `values.yaml` file:

```bash
cd proTES/deployment
vim values.yaml
```

Set `clusterType` according to your target cluster: `openshift` for
OpenShift clusters, `kubernetes` for plain Kubernetes clusters. Change the
default credentials of Flower (`flower.basicAuth`, `admin:admin`). Set the
app config in `config` (a config overlay): at least the trusted token
issuers, the audience and the groups of the roles, without which the app
doesn't start (see [Access
control](https://protes.readthedocs.io/en/latest/access-control/)).

Alternatively, for CI/CD use cases for example, override values when
installing the chart, e.g.:

```bash
cd deployment
helm install . --generate-name --set protes.appName=proxyT
```

where `proxyT` will be the name of the proTES deployment and associated
objects.

## Image

The app image is `protes.image` (default `elixircloud/protes`) with tag
`protes.tag`, which defaults to the chart's `appVersion`; the Celery worker
runs the same image (unless `celeryWorker.image` is set). Charts attached to
[GitHub releases](https://github.com/elixir-cloud-aai/proTES/releases) have
the release version as `version` and `appVersion`, so they deploy the image
of their release (`X.Y.Z`). In the repository, both are placeholders, and
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

## Deployment

Deploy proTES with Helm:

```bash
cd deployment
helm install . --generate-name
```

Once proTES is deployed, find its URL with:

```bash
# In plain Kubernetes clusters
kubectl get ingress

# In OpenShift clusters
kubectl get routes
```
