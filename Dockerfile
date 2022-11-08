##### BASE IMAGE #####
FROM elixircloud/foca:20221107-py3.10

##### METADATA #####
LABEL base.image="elixircloud/foca:20221107-py3.10"
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

# Install general dependencies
RUN apt-get update && apt-get install -y nodejs openssl git build-essential python3-dev

## Set working directory
WORKDIR /app

## Copy app files
COPY ./ .

## Install app
RUN pip install -e .
