# proTES

[![license][badge-license]][badge-url-license]
[![chat][badge-chat]][badge-url-chat]
[![ci][badge-ci]][badge-url-ci]

## Synopsis

proTES is a robust and scalable [Global Alliance for Genomics and Health
(GA4GH)][res-ga4gh] [Task Execution Service (TES) API][res-ga4gh-tes] gateway
that may play a pivotal role in augmenting the capabilities of your GA4GH Cloud
ecosystem by offering flexible middleware injection for effectively federating
atomic, containerized workloads across on premise, hybrid and multi-cloud
environments composed of GA4GH TES nodes.

## Description

proTES gateway may serve as a crucial component in federated compute networks
based on the GA4GH Cloud ecosystem. Its primary purpose is to provide
centralized features to a federated network of independently operated  GA4GH TES 
instances. As such, it can serve, for example, as a compatibility layer, a load
balancer workload distribution layer, a public entry point to an enclave of
independent compute nodes, or a means of collecting telemetry.

When TES requests are received, proTES applies a configured middlewares before
forwarding the requests to appropriate TES instances in the network. A plugin
system makes it easy to write and inject middlewares tailored to specific
requirements, such as for access control, request/response processing or
validation, or the selection of suitable endpoints considering data use
restrictions and client preferences.

### Built-in middleware plugins

Currently, there are two plugins shipped with proTES that each serve as
proof-of-concept examples for different task distribution scenarios:

* **Load balancing**: The `pro_tes.middleware.task_distribution.random` plugin
  evenly (actually: randomly!) distributes workloads across a network of TES
  endpoints
* **Bringing compute to the data**: The
  `pro_tes.middleware.task_distribution.distance` plugin selects TES endpoints 
  to relay incoming requests to in such a way that the distance the (input) data
  of a task has to travel across the network of TES endpoints is minimized. 

### Implementation notes

proTES is a [Flask][res-flask] microservice that supports
[OAuth2][res-oauth2]-based authorization out of the box (bearer authentication)
and stores information about incoming and outgoing tasks in a NoSQL database
([MongoDB][res-mongodb]). Based on our [FOCA][res-foca] microservice archetype,
it is highly configurable in a declarative (YAML-based!) manner. Forwarded tasks
are tracked asynchronously via a [RabbitMQ][res-rabbitmq] broker and
[Celery][res-celery] workers that can be easily scaled up. Both a
[Helm][res-helm] chart and a [Docker Compose][res-docker-compose] configuration
are provided for easy deployment in native cloud-based production and
development environments, respectively.

![proTES-overview][image-protes-overview]

## Installation

For production-grade [Kubernetes][res-kubernetes]-based deployment, see
[separate instructions][docs-deploy]. For testing/development purposes, you can
use the instructions described below.

### Requirements

Ensure you have the following software installed:

* [Docker][res-docker] (18.06.1-ce, build e68fc7a)
* [docker-compose][res-docker-compose] (1.22.0, build f46880fe)
* [Git][res-git] (1.8.3.1)

> **Note:** These indicated versions are those that were used for
> developing/testing. Other versions may or may not work.

### Prerequisites

Create data directory and required subdiretories

```bash
export PROTES_DATA_DIR=/path/to/data/directory
mkdir -p $PROTES_DATA_DIR/{db,specs}
```

> **Note:** If the `PROTES_DATA_DIR` environment variable is not set, proTES
> will require the following default directories to be available:
>
> * `../data/pro_tes/db`
> * `../data/pro_tes/specs`

Clone repository

```bash
git clone https://github.com/elixir-europe/proTES.git
```

Traverse to app directory

```bash
cd proTES
```

### Configure (optional)

The following user-configurable files are available:

* [app configuration](pro_tes/config/app_config.yaml)
* [deployment configuration](docker-compose.yaml)

### Deploy

Build/pull and run services

```bash
docker-compose up -d --build
```

Visit Swagger UI

```bash
firefox http://localhost:8080/ga4gh/tes/v1/ui
```

> **Note:** Host and port may differ if you have changed the configuration or
> use an HTTP server to reroute calls to a different host.

## Contributing

This project is a community effort and lives off your contributions, be it in
the form of bug reports, feature requests, discussions, ideas, fixes, or other
code changes. Please read [these guidelines][docs-contributing] if you want to
contribute. And please mind the [code of conduct][docs-coc] for all
interactions with the community.

## Versioning

The project adopts the [semantic versioning][semver] scheme for versioning.
Currently the service is in beta stage, so the API may change and even break
without further notice. However, once we deem the service stable and "feature
complete", the major, minor and patch version will shadow the supported TES
version, with the build version representing proTES-internal updates.

## License

This project is covered by the [Apache License 2.0][badge-url-license] also
[shipped with this repository][docs-license].

## Contact

proTES is part of [ELIXIR Cloud & AAI][res-elixir-cloud-aai], a multinational
effort at establishing and implementing FAIR data sharing and promoting
reproducible data analyses and responsible data handling in the life sciences.

If you have suggestions for or find issue with this app, please use the
[issue tracker][contact-issue-tracker]. If you would like to reach out to us
for anything else, you can join our [Slack board][badge-url-chat], start a
thread in our [Q&A forum][contact-qa], or send us an [email][contact-email].

[![GA4GH logo](images/logo-ga4gh.png)](https://www.ga4gh.org/)
[![ELIXIR logo](images/logo-elixir.png)](https://www.elixir-europe.org/)
[![ELIXIR Cloud & AAI logo](images/logo-elixir-cloud.png)](https://elixir-europe.github.io/cloud/)

[badge-chat]: <https://img.shields.io/static/v1?label=chat&message=Slack&color=ff6994>
[badge-ci]: <https://github.com/elixir-cloud-aai/proTES/actions/workflows/checks.yaml/badge.svg>
[badge-license]: <https://img.shields.io/badge/license-Apache%202.0-blue.svg>
[badge-url-chat]: <https://join.slack.com/t/elixir-cloud/shared_invite/zt-1uvebyx1e-NcC0Hof2guT9df~haBdQaw>
[badge-url-ci]: <https://github.com/elixir-cloud-aai/proTES/actions/workflows/checks.yaml>
[badge-url-license]: <http://www.apache.org/licenses/LICENSE-2.0>
[contact-email]: <mailto:cloud-service@elixir-europe.org>
[contact-issue-tracker]: <https://github.com/elixir-cloud-aai/landing-page/issues>
[contact-qa]: <https://github.com/elixir-cloud-aai/elixir-cloud-aai/discussions>
[docs-coc]: <https://github.com/elixir-cloud-aai/elixir-cloud-aai/blob/dev/CODE_OF_CONDUCT.md>
[docs-contributing]: <https://elixir-cloud-aai.github.io/guides/guide-contributor/>
[docs-deploy]: deployment/README.md
[docs-license]: LICENSE
[GA4GH TES OpenAPI specification]:<https://github.com/ga4gh/task-execution-schemas>
[image-protes-overview]: <images/overview.svg>
[res-celery]: <http://www.celeryproject.org/>
[res-connexion]: <https://github.com/zalando/connexion>
[res-docker]: <https://www.docker.com/>
[res-docker-compose]: <https://docs.docker.com/compose/>
[res-elixir-cloud-aai]: <https://elixir-cloud.dcc.sib.swiss/>
[res-flask]: <http://flask.pocoo.org/>
[res-foca]: <https://github.com/elixir-cloud-aai/foca>
[res-ga4gh]: <https://www.ga4gh.org/>
[res-ga4gh-cloud]: <https://www.ga4gh.org/work_stream/cloud/>
[res-ga4gh-tes]: <https://github.com/ga4gh/task-execution-schemas>
[res-git]: <https://git-scm.com/>
[res-helm]: <https://helm.sh/>
[res-kubernetes]: <https://kubernetes.io/>
[res-mondodb]: <https://www.mongodb.com/>
[res-ouath2]: <https://oauth.net/2/>
[res-rabbitmq]: <https://www.rabbitmq.com/>
[res-sem-ver]: <https://semver.org/>
