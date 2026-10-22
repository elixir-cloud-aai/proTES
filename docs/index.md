[![ELIXIR On-Cloud](images/logo_elixir_on_cloud.png)](https://elixir-on.cloud/)

# proTES

proTES is a gateway for the [GA4GH Task Execution Service (TES)
API](https://github.com/ga4gh/task-execution-schemas), brought to you by
[ELIXIR On-Cloud](https://elixir-on.cloud/) (see the
[README](https://github.com/elixir-cloud-aai/proTES/blob/dev/README.md) for
an introduction and a quick start).

## Architecture

proTES provides central features to a federated network of independently
operated TES instances: it can serve, for example, as a compatibility layer,
a load balancer, a public entry point to an enclave of independent compute
nodes, or a means of collecting telemetry.

When proTES receives a task, it applies the configured middlewares before
forwarding the task to a TES instance of the network. A plugin system makes
it easy to write middlewares for specific requirements, such as access
control, request or response processing or validation, or the selection of
suitable TES instances considering data use restrictions and client
preferences. Two middlewares are shipped with proTES, as proofs of concept
for different ways of distributing tasks:

- **Load balancing:**
  `pro_tes.plugins.middlewares.task_distribution.random.TaskDistributionRandom`
  distributes tasks randomly across the TES instances.
- **Bringing compute to the data:**
  `pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance`
  selects the TES instances such that the distance that the task's input
  data has to travel across the network is minimized.

proTES is a [Flask](https://flask.palletsprojects.com/) app built with
[FOCA](https://github.com/elixir-cloud-aai/foca), configured declaratively in
YAML. It stores incoming and forwarded tasks in a
[MongoDB](https://www.mongodb.com/) database. Forwarded tasks are tracked
asynchronously by [Celery](https://docs.celeryq.dev/) workers, which can be
scaled up, via a [RabbitMQ](https://www.rabbitmq.com/) broker. A
[Helm](https://helm.sh/) chart and a [Docker
Compose](https://docs.docker.com/compose/) setup are provided for
deployments on Kubernetes and for development, respectively.

![proTES overview](images/overview.svg)

## License

This project is covered by the [Apache License
2.0](https://www.apache.org/licenses/LICENSE-2.0) also [shipped with this
repository](https://github.com/elixir-cloud-aai/proTES/blob/dev/LICENSE).

## Contact

The project is a collaborative effort under the umbrella of [ELIXIR
On-Cloud](https://elixir-on.cloud/). Follow the link to get in touch with us
via chat or email. Please mention the name of this service for any inquiry,
proposal, question etc.
