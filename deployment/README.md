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
