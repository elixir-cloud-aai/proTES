##### BASE IMAGE #####
FROM python:3.6-slim-stretch

##### METADATA #####
LABEL base.image="python:3.6-slim-stretch"
LABEL version="0.1.0"
LABEL software="proTES"
LABEL software.version="0.1.0"
LABEL software.description="Flask microservice implementing the Global Alliance for Genomics and Health (GA4GH) Task Execution Service (TES) API specification as a proxy for task distribution."
LABEL software.website="https://github.com/elixir-europe/proTES"
LABEL software.documentation="https://github.com/elixir-europe/proTES"
LABEL software.license="https://github.com/elixir-europe/proTES/blob/master/LICENSE"
LABEL software.tags="General"
LABEL maintainer="alexander.kanitz@alumni.ethz.ch"
LABEL maintainer.organisation="Biozentrum, University of Basel"
LABEL maintainer.location="Klingelbergstrasse 50/70, CH-4056 Basel, Switzerland"
LABEL maintainer.lab="Zavolan Lab"
LABEL maintainer.license="https://spdx.org/licenses/Apache-2.0"

# Python UserID workaround for OpenShift/K8S
ENV LOGNAME=ipython
ENV USER=ipython

RUN apt-get update && apt-get install -y nodejs openssl git build-essential python3-dev

## Copy app files
COPY ./ /app

## Install dependencies
RUN cd /app \
  && pip install -r requirements.txt \
  && python setup.py develop \
  && cd /app/src/py-tes \
  && python setup.py develop \
  && cd /app/pro_tes

ENTRYPOINT cd /app/pro_tes; gunicorn -c config.py wsgi:app
