##### BASE IMAGE #####
## Pinned by digest, so that rebuilds (e.g., of a release) use the same base
## image; Dependabot updates the tag and digest together
## (`.github/dependabot.yml`)
## TEMPORARY: FOCA's development image built from the commit that
## `pyproject.toml` pins (#214), replaced by the release image before the
## next release (`scripts/check_release_pins.py`); dated tags move with later
## commits of the day, so only the digest identifies the image
## FOCA commit: 29af8a64a2b0aae21845fa37a4a54076811f62bf
FROM elixircloud/foca:20261023-py3.13@sha256:1e1275f77be3a256449dc8739410f32b0d0376534b824d8b746d5316d27cdb1f

##### METADATA #####
LABEL software="proTES"
LABEL software.description="Proxy for GA4GH Task Execution Service (TES) instances by ELIXIR On-Cloud"
LABEL software.website="https://github.com/elixir-cloud-aai/proTES"
LABEL software.license="https://spdx.org/licenses/Apache-2.0"
LABEL maintainer="alexander.kanitz@alumni.ethz.ch"
LABEL maintainer.organisation="ELIXIR On-Cloud"

## Install dependencies (`project.dependencies` of `pyproject.toml`) first,
## so that code changes do not reinstall them; the FOCA image runs as non-root
## user. A direct reference to a FOCA commit (temporary) is left out, as the
## image has no Git: the build fails unless the base image has FOCA of that
## commit (`scripts/image_requirements.py`)
USER root
COPY pyproject.toml scripts/image_requirements.py /tmp/src/
RUN python /tmp/src/image_requirements.py /tmp/src/pyproject.toml > /tmp/src/requirements.txt \
  && pip install --no-cache-dir -r /tmp/src/requirements.txt

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
## (`api.specs.tes`). Containers of the Celery worker (same image, other
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
