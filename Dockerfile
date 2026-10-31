##### BASE IMAGE #####
## Pinned by digest, so that rebuilds (e.g., of a release) use the same base
## image; Dependabot updates the tag and digest together
## (`.github/dependabot.yml`)
FROM elixircloud/foca:0.18.0-py3.13@sha256:dd8dd927d8d5e7ccb616d441c8db7a5e2c0b24c52a3329f484f6edac082b7a26

##### METADATA #####
## OCI annotations; images built from the FOCA image inherit its own, so all
## static ones are set here. CI's `docker/metadata-action` sets the version,
## revision and creation date and overrides title, description, URLs and
## licenses with the repository's metadata
LABEL org.opencontainers.image.title="proTES"
LABEL org.opencontainers.image.description="Proxy for GA4GH Task Execution Service (TES) instances by ELIXIR On-Cloud"
LABEL org.opencontainers.image.url="https://github.com/elixir-cloud-aai/proTES"
LABEL org.opencontainers.image.source="https://github.com/elixir-cloud-aai/proTES"
LABEL org.opencontainers.image.documentation="https://protes.readthedocs.io/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.authors="ELIXIR On-Cloud <cloud-service@elixir-europe.org>"
LABEL org.opencontainers.image.vendor="ELIXIR On-Cloud"

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
## argument (e.g., `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=1.2.3`, as the
## workflows do); without it, the app reports version `0.0.0.dev0`
COPY pyproject.toml README.md LICENSE /tmp/src/
COPY pro_tes /tmp/src/pro_tes
ARG SETUPTOOLS_SCM_PRETEND_VERSION
RUN pip install --no-cache-dir --no-deps /tmp/src \
  && rm -rf /tmp/src

## Plugins for local builds (e.g., `--build-arg PLUGINS="<distribution>==<version>"`,
## pip requirement specifiers without spaces, separated by spaces; recorded
## in the image's history, so never credentials), installed with their
## dependencies from the package index, NOT pinned by hash; then pip checks
## that all dependencies are met. Empty by default, as for the published
## images; images for deployments derive from a release image instead (see
## `docs/plugin-images.md`)
ARG PLUGINS=""
RUN if [ -n "${PLUGINS}" ]; then \
    pip install --no-cache-dir ${PLUGINS} && pip check; \
  fi
USER 1000

## Probe the Swagger UI, which needs neither the database nor the message
## broker; set `HEALTHCHECK_URL` if Gunicorn's `--bind` changes the port, or
## the config changes the API base path or the Swagger UI setting
## (`api.specs.tes`). Containers of the Celery worker (same image, other
## command) need a health check of their own (see `docker-compose.yaml`)
ENV HEALTHCHECK_URL=http://localhost:8080/ga4gh/tes/v1/ui/
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(os.environ['HEALTHCHECK_URL'], timeout=4)"]

## Serve with Gunicorn (FOCA's settings for containers, `foca.gunicorn_config`;
## override them via `GUNICORN_CMD_ARGS`, e.g., `--workers 2`); each worker
## creates the app (no preloading). The Celery worker runs
## `celery --app pro_tes.celery_worker worker`; `python -m pro_tes.app` runs
## Flask's development server instead
CMD ["gunicorn", "--config", "python:foca.gunicorn_config", \
  "pro_tes.app:init_app()"]
