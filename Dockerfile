##### BASE IMAGE #####
FROM elixircloud/foca:20221110-py3.10

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

WORKDIR /app
COPY ./requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt
# Patch py-tes timestampconv to treat empty-string timestamps as None (Funnel compat)
RUN python3 -c "import pathlib; f=pathlib.Path('/usr/local/lib/python3.10/site-packages/tes/models.py'); t=f.read_text(); t=t.replace('    if value is None:\n        return value\n', '    if not value:\n        return None\n'); f.write_text(t)"
COPY ./ .
RUN pip install -e .

## Add permissions for storing updated API specification
## (required by FOCA)
RUN chmod -R a+rwx /app/pro_tes/api
