# proTES

## Synopsis

[Flask](http://flask.pocoo.org/) microservice implementing the
[Global Alliance for Genomics and Health](https://www.ga4gh.org/) (GA4GH)
[Task Execution Service](https://github.com/ga4gh/task-execution-schemas)
(TES) API specification.

## Description

proTES is an proxy-like implementation of the
[GA4GH TES OpenAPI specification](https://github.com/ga4gh/task-execution-schemas)
based on [Flask](http://flask.pocoo.org/) and [Connexion](https://github.com/zalando/connexion)
built for distributing TES tasks over different TES service instances.

proTES is part of [ELIXIR](https://www.elixir-europe.org/), a multinational effort at
establishing and implementing FAIR data sharing and promoting reproducible data analyses and
responsible data handling in the Life Sciences. Infrastructure and IT support are provided by
ELIXIR Finland at the [CSC](https://www.csc.fi/home), the [TESK](https://github.com/EMBL-EBI-TSI/TESK)
service is being developed and maintained by ELIXIR UK at the [EBI](https://www.ebi.ac.uk/) in
Hinxton, and proTES itself is being mainly developed by ELIXIR Switzerland at the
[Biozentrum](https://www.biozentrum.unibas.ch/) in Basel and the
[Swiss Institute of Bioinformatics](https://www.sib.swiss/).

## Installation

### Docker

#### Requirements (Docker)

Ensure you have the following software installed:

* Docker (18.06.1-ce, build e68fc7a)
* docker-compose (1.22.0, build f46880fe)
* Git (1.8.3.1)

Note: These are the versions used for development/testing. Other versions may or may not work.

#### Instructions (Docker)

Create data directory and required subdiretories

```bash
mkdir -p data/db data/output data/tmp
```

Clone repository

```bash
git clone https://github.com/elixir-europe/proTES.git app
```

Traverse to app directory

```bash
cd app
```

##### Optional: edit default and override app config

* Via configuration files:

```bash
vi wes_elixir/config/app_config.yaml
vi wes_elixir/config/override/app_config.dev.yaml  # for development service
vi wes_elixir/config/override/app_config.prod.yaml  # for production server
```

* Via environment variables: 

A few configuration settings can be overridden 
by environment variables.

```bash
export <ENV_VAR_NAME>=<VALUE>
```

   * List of the available environment variables:

| Variable       | Description             |
|----------------|-------------------------|
| MONGO_HOST     | MongoDB host endpoint   |
| MONGO_PORT     | MongoDB service port    |
| MONGO_DBNAME   | MongoDB database name   |
| MONGO_USERNAME | MongoDB client username |
| MONGO_PASSWORD | MongoDB client password |
| RABBIT_HOST    | RabbitMQ host endpoint  |
| RABBIT_PORT    | RabbitMQ service port   |

Build container image

```bash
docker-compose build
```

Run docker-compose services in detached/daemonized mode

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d  # for development service
docker-compose -f docker-compose.yaml -f docker-compose.prod.yaml up -d  # for production service
```

Visit Swagger UI

```bash
firefox http://localhost:7878/ui
```

### Non-dockerized

#### Requirements (non-dockerized)

Ensure you have the following software installed:

* curl (7.47.0)
* Git (2.7.4)
* MongoDB (4.0.1)
* Python3 (3.5.2)
* RabbitMQ (3.5.7)
* virtualenv (16.0.0)

Note: These are the versions used for development/testing. Other versions may or may not work.

#### Instructions (non-dockerized)

Ensure RabbitMQ is running (actual command is [OS-dependent](https://www.digitalocean.com/community/tutorials/how-to-install-and-manage-rabbitmq))

```bash
sudo service rabbitmq-server status
```

Start MongoDB daemon (actual command is [OS-dependent](https://docs.mongodb.com/manual/administration/install-community/))

```bash
sudo service mongod start
```

Clone repository

```bash
git clone https://github.com/elixir-europe/proTES.git app
```

Traverse to project directory

```bash
cd app
project_dir="$PWD"
```

Create and activate virtual environment

```bash
virtualenv -p `which python3` venv
source venv/bin/activate
```

Install required packages

```bash
pip install -r requirements.txt
```

Install editable packages

```bash
cd "${project_dir}/venv/src/py-tes"
python setup.py develop
cd "$project_dir"
```

Install app

```bash
python setup.py develop
```

Optionally, override default config by setting environment variable and pointing it to a YAML config
file. Ensure the file is accessible.

```bash
export TES_CONFIG=<path/to/override/config/file.yaml>
```

Start service

```bash
python pro_tes/app.py
```

In another terminal, load virtual environment & start Celery worker for executing background tasks

```bash
# Traverse to project directory ("app") first
source venv/bin/activate
cd pro_tes
celery worker -A celery_worker -E --loglevel=info
```

Visit Swagger UI

```bash
firefox http://localhost:8989/ui
```

Note: If you have edited `TES_CONFIG`, ensure that host and port match the values specified in the config file.

## Q&A

Coming soon...

## Contributing

**Join us at the [2018 BioHackathon in Paris](https://bh2018paris.info/), organized by [ELIXIR Europe](https://www.elixir-europe.org/) (November 12-16)!** Check out our [project description](https://github.com/elixir-europe/BioHackathon/tree/master/tools/Development%20of%20a%20GA4GH-compliant%2C%20language-agnostic%20workflow%20execution%20service).

This project is a community effort and lives off your contributions, be it in the form of bug
reports, feature requests, discussions, or fixes and other code changes. Please read [these
guidelines](CONTRIBUTING.md) if you want to contribute. And please mind the [code of
conduct](CODE_OF_CONDUCT.md) for all interactions with the community.

## Versioning

Development of the app is currently still in alpha stage, and current "versions" are for internal
use only. We are aiming to have a fully spec-compliant ("feature complete") version of the app
available by the end of 2018. The plan is to then adopt a [semantic versioning](https://semver.org/)
scheme in which we would shadow TES spec versioning for major and minor versions, and release
patched versions intermittently.

## License

This project is covered by the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) also
[shipped with this repository](LICENSE).

## Contact

The project is a collaborative effort under the umbrella of [ELIXIR
Europe](https://www.elixir-europe.org/).

Please contact the [project leader](mailto:alexander.kanitz@sib.swiss) for inquiries,
proposals, questions etc. that are not covered by the [Q&A](#Q&A) and [Contributing](#Contributing)
sections.

## References

* <https://www.elixir-europe.org/>
* <https://www.ga4gh.org/>
* <https://github.com/ga4gh/task-execution-schemas>
