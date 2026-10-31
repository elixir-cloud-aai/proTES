# check=skip=InvalidDefaultArgInFrom
## (The base image has no default: builds name the release image explicitly)
## Image with plugins, derived from a proTES release image (see
## `docs/plugin-images.md`). The build context holds `plugins.txt`, the
## distributions to install, each pinned with its hash, and `wheels/`, their
## files, downloaded and verified before the build; the build installs these
## files only (no index, no other dependencies), as root, then checks that
## the dependencies of all installed distributions are met. Build it with
## the release image, by digest:
##   docker build --file Dockerfile \
##     --build-arg PROTES_IMAGE=docker.io/elixircloud/protes:<version>@sha256:<digest> \
##     --tag <registry>/protes-plugins:<tag> <context>
## The smoke test builds it in CI with a test plugin (`tests/smoke/plugins.sh`)
ARG PROTES_IMAGE
FROM ${PROTES_IMAGE}

USER root
RUN --mount=type=bind,source=plugins.txt,target=/tmp/plugins/plugins.txt \
  --mount=type=bind,source=wheels,target=/tmp/plugins/wheels \
  pip install --no-cache-dir --no-index --no-deps --require-hashes \
    --find-links /tmp/plugins/wheels --requirement /tmp/plugins/plugins.txt \
  && pip check
USER 1000
