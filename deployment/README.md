# Kubernetes deployment of proTES

- [Kubernetes deployment for proTES](#kubernetes-deployment-for-protes)
    - [Usage](#usage)
    - [Tweaks](#tweaks)
      - [Using the Helm CLI](#helmclitweaks)
    - [Deployment](#deployment)

The files under this directory can be used to deploy proTES on Kubernetes. 
The deployment method is based on Helm (v3). The directory structure is as 
follows:

- templates: YAML files used in Kubernetes clusters where this is deployed
  - mongodb: YAML files for deploying MongoDB.
  - rabbitmq: YAML files for deploying RabbitMQ.
  - protes: YAML files for deploying the proTES server and Celery worker.
  - flower: YAML files for deploying flower (for montoring rabbitmq).
- values.yaml: contains the configuration variables for the Helm chart.
- Chart.yaml: the Helm chart metadata.

## Usage

First you must create a namespace in Kubernetes in which to deploy proTES. The
commands below assume that everything is created in the context of this
namespace. How the namespace is created depends on the cluster, so we won't
document it here.

Make sure you have both the Kubernetes client (kubectl) and Helm v3 installed.
(See https://helm.sh/docs/intro/install/)

Clone this repository:

```bash
git clone https://github.com/elixir-europe/proTES/
```

## Tweaks

Update the configuration by modifying the `values.yaml` file:

```bash
cd proTES/deployment
vim values.yaml
```

NOTE: update the variable clusterType in values.yaml depending on your target cluster:
  - For OpenShift clusters set the value to: `openshift`
  - For plain Kubernetes clusters set the value to: `kubernetes`

### Using the Helm CLI

Optionally, for CI/CD use cases for example, you could override the values in 
values.yaml when creating the Helm chart. For example:

```bash 
cd deployment
helm install . --generate-name --set protes.appName=proxyT
```

where proxyT will be the name of the proTES deployment and associated objects.

## Deployment

After this you can deploy proTES using Helm:

```bash 
cd deployment
helm install . --generate-name
```

Once proTES is deployed, you can access it via the url endpoint which you can 
query by running:

```bash 
# In vanilla kubernetes clusters
kubectl get ingress

# In OpenShift clusters
kubectl get routes
```

