[![ELIXIR On-Cloud][logo]][org]

# proTES

[![License][badge-license]][badge-url-license]
[![Build_status][badge-build-status]][badge-url-build-status]
[![Docs][badge-docs]][badge-url-docs]
[![Coverage][badge-coverage]][badge-url-coverage]
[![GitHub_tag][badge-github-tag]][badge-url-github-tag]
[![Docker_image][badge-docker]][badge-url-docker]
[![DOI][badge-doi]][badge-url-doi]

<!-- --8<-- [start:synopsis] -->
A gateway for the [GA4GH Task Execution Service (TES)
API](https://github.com/ga4gh/task-execution-schemas) that federates your TES
instances, brought to you by [ELIXIR On-Cloud](https://elixir-on.cloud/).
Clients send their tasks to proTES as to any TES instance; proTES passes each
task through a configurable stack of middlewares, which pick the TES instance
to run it on (or change the request), forwards it there and tracks its state.
Built-in middlewares distribute tasks randomly, for load balancing, or to the
TES instance closest to the task's input data, to bring compute to the data;
write your own for access control, request processing or validation, or the
selection of instances by data use restrictions and client preferences. Run it
with Docker Compose or deploy it to Kubernetes with its Helm chart.
<!-- --8<-- [end:synopsis] -->

**Read the [documentation][docs]** for the architecture, the configuration,
the API, the middlewares, the deployment with the Helm chart in
[`deployment/`][deployment], operations, upgrades and development.

## Known deployments

<!-- --8<-- [start:deployments] -->
None listed yet.

Running proTES yourself? Open a pull request that adds your deployment
(URL, operator and notes) to this section of
[`README.md`](https://github.com/elixir-cloud-aai/proTES/blob/dev/README.md).
<!-- --8<-- [end:deployments] -->

## Quick start

<!-- --8<-- [start:quick-start] -->
With [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) and
[Docker](https://docs.docker.com/get-docker/) with the [Compose
plugin](https://docs.docker.com/compose/install/) (version 2) installed, clone
the repository and start proTES with its MongoDB database, RabbitMQ broker and
Celery worker:

```bash
git clone https://github.com/elixir-cloud-aai/proTES.git
cd proTES
docker compose up --build -d
```

The TES API is then served at `http://localhost:8080/ga4gh/tes/v1/`, with a
Swagger UI at
[http://localhost:8080/ga4gh/tes/v1/ui](http://localhost:8080/ga4gh/tes/v1/ui):

```bash
curl -X GET "http://localhost:8080/ga4gh/tes/v1/service-info" -H "accept: application/json"
```

Tasks are forwarded to the TES instances listed in `custom.tes.service_list`
of the config, with the credentials of proTES for each; list yours in
[`config.dev.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/config.dev.yaml)
(see [Credentials of TES
instances](https://protes.readthedocs.io/en/latest/access-control/#credentials-of-tes-instances)).
All endpoints except `GET /service-info`, the specification and the Swagger
UI require access tokens of the identity provider set in
[`config.dev.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/config.dev.yaml):
replace its placeholder issuer and groups with those of your identity
provider, or try the API with the Compose setup of the smoke test, which
brings a fake one (see [Access
control](https://protes.readthedocs.io/en/latest/access-control/)).
<!-- --8<-- [end:quick-start] -->

## Citation

<!-- --8<-- [start:citation] -->
If you use proTES, please cite it with its DOI
[10.5281/zenodo.23619646](https://doi.org/10.5281/zenodo.23619646), which
always resolves to the latest version on [Zenodo](https://zenodo.org/); the
Zenodo record lists the DOI of each version, too. The citation metadata is in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/proTES/blob/dev/CITATION.cff):
on GitHub, "Cite this repository" in the sidebar of the repository formats it
as APA or BibTeX.
<!-- --8<-- [end:citation] -->

## Contributing

Contributions are welcome! See [Contributing][docs-contributing] in the
documentation and the organisation's [contributing guide][contributing], and
mind our [code of conduct][code-of-conduct].

[badge-build-status]: <https://github.com/elixir-cloud-aai/proTES/actions/workflows/ci.yml/badge.svg?branch=dev>
[badge-coverage]: <https://codecov.io/gh/elixir-cloud-aai/proTES/branch/dev/graph/badge.svg?branch=dev>
[badge-docker]: <https://img.shields.io/docker/v/elixircloud/protes?sort=semver&label=image&color=C39BD3>
[badge-doi]: <https://zenodo.org/badge/DOI/10.5281/zenodo.23619646.svg>
[badge-docs]: <https://img.shields.io/readthedocs/protes>
[badge-github-tag]: <https://img.shields.io/github/v/tag/elixir-cloud-aai/proTES?color=C39BD3>
[badge-license]: <https://img.shields.io/badge/license-Apache%202.0-blue.svg>
[badge-url-build-status]: <https://github.com/elixir-cloud-aai/proTES/actions/workflows/ci.yml?query=branch%3Adev>
[badge-url-coverage]: <https://codecov.io/gh/elixir-cloud-aai/proTES?branch=dev>
[badge-url-docker]: <https://hub.docker.com/r/elixircloud/protes/tags>
[badge-url-doi]: <https://doi.org/10.5281/zenodo.23619646>
[badge-url-docs]: <https://protes.readthedocs.io/en/latest/>
[badge-url-github-tag]: <https://github.com/elixir-cloud-aai/proTES/releases>
[badge-url-license]: <https://www.apache.org/licenses/LICENSE-2.0>
[code-of-conduct]: <https://github.com/elixir-cloud-aai/.github/blob/main/CODE_OF_CONDUCT.md>
[contributing]: <https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md>
[deployment]: deployment/
[docs]: <https://protes.readthedocs.io/en/latest/>
[docs-contributing]: <https://protes.readthedocs.io/en/latest/contributing/>
[logo]: docs/images/logo_elixir_on_cloud.png
[org]: <https://elixir-on.cloud/>
