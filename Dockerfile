##### BUILD STAGE #####
## Pinned by digest, so that rebuilds (e.g., of a release) use the same base
## image; Dependabot updates the tag and digest together, in both stages
## (`.github/dependabot.yml`)
FROM elixircloud/foca:0.15.0-py3.12@sha256:0d21e23d67f8b84a0bf4e2ee893024ac82344aec41a09f9f4b6440d1e108e570 AS build

## Build wheels of the dependencies (`project.dependencies` of
## `pyproject.toml`, read with `tomllib` of the base image's Python, 3.11 or
## later): some have no wheels for Python 3.12 (e.g., `typed-ast`, required by
## `ip2geotools`), and the final image has no compiler
USER root
RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc libc6-dev \
  && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /tmp/src/
RUN mkdir /tmp/deps \
  && python -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/src/pyproject.toml', 'rb'))['project']['dependencies']))" \
    > /tmp/deps/requirements.txt \
  && pip wheel --no-cache-dir --wheel-dir /tmp/deps/wheels -r /tmp/deps/requirements.txt

##### BASE IMAGE #####
FROM elixircloud/foca:0.15.0-py3.12@sha256:0d21e23d67f8b84a0bf4e2ee893024ac82344aec41a09f9f4b6440d1e108e570

##### METADATA #####
LABEL software="proTES"
LABEL software.description="Proxy for GA4GH Task Execution Service (TES) instances by ELIXIR On-Cloud"
LABEL software.website="https://github.com/elixir-cloud-aai/proTES"
LABEL software.license="https://spdx.org/licenses/Apache-2.0"
LABEL maintainer="alexander.kanitz@alumni.ethz.ch"
LABEL maintainer.organisation="ELIXIR On-Cloud"

## Install dependencies from the wheels of the build stage first, so that
## code changes do not reinstall them; the FOCA image runs as non-root user
USER root
RUN --mount=type=bind,from=build,source=/tmp/deps,target=/tmp/deps \
  pip install --no-cache-dir --no-index --find-links /tmp/deps/wheels \
    -r /tmp/deps/requirements.txt

## Install app (non-editable; the base config is installed with the package,
## and deployments override it via `FOCA_CONFIG_OVERLAY`)
## (`README.md` is the package's long description; the license text is
## installed with the package metadata, e.g.,
## `site-packages/pro_tes-<version>.dist-info/licenses/LICENSE`)
## The build context has no Git metadata, so the version is passed as a build
## argument (e.g., `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=0.3.0`, as the
## workflows do); without it, the app reports version `0.0.0.dev0`
COPY pyproject.toml README.md LICENSE /tmp/src/
COPY pro_tes /tmp/src/pro_tes
ARG SETUPTOOLS_SCM_PRETEND_VERSION
RUN pip install --no-cache-dir --no-deps /tmp/src \
  && rm -rf /tmp/src
USER 1000

## Probe the Swagger UI, which needs neither the database nor the message
## broker; set `HEALTHCHECK_URL` if Gunicorn's `--bind` changes the port, or
## the config changes the API base path or the Swagger UI setting
## (`api.specs[]`). Containers of the Celery worker (same image, other
## command) need a health check of their own (see `docker-compose.yaml`)
ENV HEALTHCHECK_URL=http://localhost:8080/ga4gh/tes/v1/ui/
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(os.environ['HEALTHCHECK_URL'], timeout=4)"]

## Serve with Gunicorn (settings in `pro_tes/gunicorn_config.py`; override
## them via `GUNICORN_CMD_ARGS`, e.g., `--workers 2`); each worker creates the
## app (no preloading). The Celery worker runs
## `celery --app pro_tes.celery_worker worker`; `python -m pro_tes.app` runs
## Flask's development server instead
CMD ["gunicorn", "--config", "python:pro_tes.gunicorn_config", \
  "pro_tes.app:init_app()"]
