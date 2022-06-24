##### BASE IMAGE #####
# FROM python:3.7-slim-stretch
FROM elixircloud/foca:20220524-py3.7

##### METADATA #####
# LABEL base.image="python:3.6-slim-stretch"
# LABEL version="1.1"
LABEL base.image="elixircloud/foca:20220524-py3.7"
LABEL version="2.0"
LABEL software="proTES"
# LABEL software.version="0.1.0"
LABEL software.version="0.2.0"
LABEL software.description="Flask microservice implementing the Global Alliance for Genomics and Health (GA4GH) Task Execution Service (TES) API specification as a proxy for task distribution."
LABEL software.website="https://github.com/elixir-europe/proTES"
LABEL software.documentation="https://github.com/elixir-europe/proTES"
LABEL software.license="https://github.com/elixir-europe/proTES/blob/master/LICENSE"
LABEL software.tags="General"
LABEL maintainer="alexander.kanitz@alumni.ethz.ch"
LABEL maintainer.organisation="Biozentrum, University of Basel"
LABEL maintainer.location="Klingelbergstrasse 50/70, CH-4056 Basel, Switzerland"
LABEL maintainer.lab="ELIXIR Cloud & AAI"
LABEL maintainer.license="https://spdx.org/licenses/Apache-2.0"

# Python UserID workaround for OpenShift/K8S
ENV LOGNAME=ipython
ENV USER=ipython

# Install general dependencies
RUN apt-get update && apt-get install -y nodejs openssl git build-essential python3-dev

## Set working directory
WORKDIR /app

## Copy Python requirements
COPY ./requirements.txt /app/requirements.txt
COPY ./requirements_dev.txt /app/requirements_dev.txt

## Install Python dependencies
RUN cd /app \
  && pip install -r requirements.txt \
#  && cd /app/src/testribute \
#  && python setup.py develop \
  && cd /

## Copy remaining app files
COPY ./ /app

## Install app
RUN cd /app \
  && python setup.py develop \
  && cd /

