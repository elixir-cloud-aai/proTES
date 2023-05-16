# proTES

[![license][badge-license]][badge-url-license]
[![chat][badge-chat]][badge-url-chat]
[![ci][badge-ci]][badge-url-ci]

## Synopsis

[Flask][res-flask] microservice implementing the [Global Alliance for Genomics
and Health (GA4GH)][res-ga4gh] [Task Execution Service (TES)
API][res-ga4gh-tes] specification for injecting middleware (such as task
distribution logic) into TES requests.

## Description

proTES is a proxy-like implementation of the [GA4GH TES OpenAPI specification]
based on [Flask][res-flask] and [Connexion][res-connexion] built for
distributing TES tasks over different TES service instances and injecting other
middleware into TES requests.

proTES is part of [ELIXIR Cloud & AAI][res-elixir-cloud-aai], a multinational
effort at establishing and implementing FAIR data sharing and promoting
reproducible data analyses and responsible data handling in the life sciences.

![proTES-overview][image-protes-overview]
* The TES task are passed through this proTES gateway, major purpose of this gateway 
  is to inject different middleware logic into TES request.
* The continuously incoming TES requests are forwarded to the TES instances after appropriate
middleware logic is applied. Meanwhile, the state of the submitted task is asynchronously 
tracked using RabbitMQ and Celery workers. This approach eliminates the need to wait for the
task to complete, allowing us to continue working on other tasks while the previous one is
still executing on the main server.
* At the current state there are 2 kinds of the task distribution middleware available in proTES:
  * Random distributor : This distributor randomly selects the TES instance from the list of 
  available TES instances and forwards the task to that instance.
  * Distance based distributor : This distributor selects the TES instance based on the distance
  between the TES instance and the TES task input location. The distance is calculated using the IP address
  of the TES instance and input location.

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
the form of bug reports, feature requests, discussions, or fixes and other
code changes. Please read [these guidelines][docs-contributing] if you want to
contribute. And please mind the [code of conduct][docs-coc] for all
interactions with the community.

## Versioning

Development of the app is currently still in alpha stage, and current "versions"
are for internal use only. We are aiming to have a fully spec-compliant
("feature complete") version of the app available by the end of 2018. The plan
is to then adopt a [semantic versioning][res-sem-ver] scheme in which we would
shadow TES spec versioning for major and minor versions, and release patched
versions intermittently.

## License

This project is covered by the [Apache License 2.0][badge-url-license] also
[shipped with this repository][docs-license].

## Contact

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
[badge-url-chat]: <https://join.slack.com/t/elixir-cloud/shared_invite/zt-1r9z32xg5-GgRguOCqsgEHtB~dN2wfZg>
[badge-url-ci]: <https://github.com/elixir-cloud-aai/proTES/actions/workflows/checks.yaml>
[badge-url-license]: <http://www.apache.org/licenses/LICENSE-2.0>
[contact-email]: <mailto:cloud-service@elixir-europe.org>
[contact-issue-tracker]: <https://github.com/elixir-cloud-aai/landing-page/issues>
[contact-qa]: <https://github.com/elixir-cloud-aai/elixir-cloud-aai/discussions>
[docs-coc]: CODE_OF_CONDUCT.md
[docs-contributing]: CONTRIBUTING.md
[docs-deploy]: deployment/README.md
[docs-license]: LICENSE
[GA4GH TES OpenAPI specification]:<https://github.com/ga4gh/task-execution-schemas>
[image-protes-overview]: <images/overview.svg>
[res-connexion]: <https://github.com/zalando/connexion>
[res-docker]: <https://www.docker.com/>
[res-docker-compose]: <https://docs.docker.com/compose/>
[res-elixir-cloud-aai]: <https://elixir-cloud.dcc.sib.swiss/>
[res-flask]: <http://flask.pocoo.org/>
[res-ga4gh]: <https://www.ga4gh.org/>
[res-ga4gh-tes]: <https://github.com/ga4gh/task-execution-schemas>
[res-git]: <https://git-scm.com/>
[res-kubernetes]: <https://kubernetes.io/>
[res-sem-ver]: <https://semver.org/>
