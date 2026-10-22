##### BASE IMAGE #####
## Pinned by digest, so that rebuilds use the same base image
FROM elixircloud/foca:0.15.0-py3.12@sha256:0d21e23d67f8b84a0bf4e2ee893024ac82344aec41a09f9f4b6440d1e108e570

##### METADATA #####
LABEL version="1.2"
LABEL software="proTES"
LABEL software.description="Flask microservice implementing the Global Alliance for Genomics and Health (GA4GH) Task Execution Service (TES) API specification as a proxy for middleware injection (e.g., task distribution logic)."
LABEL software.website="https://github.com/elixir-europe/proTES"
LABEL software.documentation="https://github.com/elixir-europe/proTES"
LABEL software.license="https://spdx.org/licenses/Apache-2.0"
LABEL maintainer="cloud-service@elixir-europe.org"
LABEL maintainer.organisation="ELIXIR Cloud & AAI"

# Python UserID workaround for OpenShift/K8S
ENV LOGNAME=ipython
ENV USER=ipython

## The FOCA image runs as non-root user `foca` (UID 1000); install as root
WORKDIR /app
USER root
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY ./ .
RUN pip install --no-cache-dir -e .
USER 1000
