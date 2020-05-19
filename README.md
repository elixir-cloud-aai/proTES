# proTES

[![Apache License](https://img.shields.io/badge/license-Apache%202.0-orange.svg?style=flat&color=important)](http://www.apache.org/licenses/LICENSE-2.0)
[![Build Status](https://travis-ci.org/elixir-europe/proTES.svg?branch=dev)](https://travis-ci.org/elixir-europe/proTES)

## Synopsis

[Flask] microservice implementing the [Global Alliance for Genomics and Health]
(GA4GH) [Task Execution Service] (TES) API specification for injecting
middleware (such as task distribution) logic into TES requests.

## Description

proTES is a proxy-like implementation of the [GA4GH TES OpenAPI specification]
based on [Flask] and [Connexion] built for distributing TES tasks over different
TES service instances and injecting other middleware into TES requests.

proTES is part of [ELIXIR Cloud & AAI], a multinational effort at establishing
and implementing FAIR data sharing and promoting reproducible data analyses and
responsible data handling in the Life Sciences.

## Installation

For production-grade [Kubernetes]-based deployment, see [separate
instructions](deployment/README.md). For testing/development purposes, you can
use the instructions described below.

### Requirements

Ensure you have the following software installed:

* [Docker] (18.06.1-ce, build e68fc7a)
* [docker-compose] (1.22.0, build f46880fe)
* [Git] (1.8.3.1)

> **Note:** These indicated versions are those that were used for
> developing/testing. Other versions may or may not work.

### Prerequisites

Create data directory and required subdiretories

```bash
export PROTES_DATA_DIR=/path/to/data/directory
mkdir -p $PROTES_DATA_DIR/db
```

> **Note:** If the `PROTES_DATA_DIR` environment variable is not set, proTES
> will require the following directory to be available:
>
> * `../data/pro_tes/db`

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
firefox http://localhost:7878/ga4gh/tes/v1/ui
```

> **Note:** Host and port may differ if you have changed the configuration or
> use an HTTP server to reroute calls to a different host.

## Contributing

This project is a community effort and lives off your contributions, be it in
the form of bug reports, feature requests, discussions, or fixes and other
code changes. Please read [these guidelines](CONTRIBUTING.md) if you want to
contribute. And please mind the [code of conduct](CODE_OF_CONDUCT.md) for all
interactions with the community.

## Versioning

Development of the app is currently still in alpha stage, and current "versions"
are for internal use only. We are aiming to have a fully spec-compliant
("feature complete") version of the app available by the end of 2018. The plan
is to then adopt a [semantic versioning] scheme in which we would shadow TES
spec versioning for major and minor versions, and release patched versions
intermittently.

## License

This project is covered by the [Apache License 2.0] also [shipped with this
repository](LICENSE).

## Contact

The project is a collaborative effort under the umbrella of [ELIXIR
Europe](https://www.elixir-europe.org/).

Please contact the [project leader](mailto:alexander.kanitz@sib.swiss) for
inquiries, proposals, questions etc. that are not covered by these docs.

## References

[![GA4GH logo](images/logo-ga4gh.png)](https://www.ga4gh.org/)
[![ELIXIR logo](images/logo-elixir.png)](https://www.elixir-europe.org/)
[![ELIXIR Cloud & AAI log](images/logo-elixir-cloud.png)](https://elixir-europe.github.io/cloud/)

[Apache License 2.0]: <https://www.apache.org/licenses/LICENSE-2.0>
[Connexion]: <https://github.com/zalando/connexion>
[Docker]: <https://www.docker.com/>
[docker-compose]: <https://docs.docker.com/compose/>
[ELIXIR Cloud & AAI]: <https://elixir-europe.github.io/cloud/>
[Flask]: <http://flask.pocoo.org/>
[GA4GH TES OpenAPI specification]: <https://github.com/ga4gh/task-execution-schemas>
[Git]: <https://git-scm.com/>
[Global Alliance for Genomics and Health]: <https://www.ga4gh.org/>
[Kubernetes]: <https://kubernetes.io/>
[semantic versioning]: <https://semver.org/>
[Task Execution Service]: <https://github.com/ga4gh/task-execution-schemas>
