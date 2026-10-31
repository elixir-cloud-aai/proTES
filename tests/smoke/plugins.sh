#!/usr/bin/env bash
# Smoke test of the derived-image recipe for plugins (`docs/plugin-images.md`,
# `examples/plugin-image/Dockerfile`), after `check.sh`: builds the wheel of
# the test plugin (`plugin/`), pins it with the recipe's `resolve.sh` (and
# checks that a wrong hash fails the build), builds the image derived from
# the app's image with the recipe, restarts proTES and the Celery worker with
# it and the plugin enabled (`plugins.yaml`), and checks that a new task gets
# the plugin's tag. Run it from the repository
# root, with `COMPOSE_FILE` set as for `check.sh` and the base URL of the TES
# API as argument (default `http://localhost:8080/ga4gh/tes/v1`); the base
# image is `PROTES_IMAGE` (default `protes:local`, built by Compose), the
# derived one `PLUGIN_IMAGE` (default `protes:plugins`), and the wheel is
# built with `PYTHON` (default `python3`, with pip). Needs curl, jq and
# docker compose.
set -euo pipefail

api="${1:-http://localhost:8080/ga4gh/tes/v1}"
export PLUGIN_IMAGE="${PLUGIN_IMAGE:-protes:plugins}"
context="$(mktemp -d)"
trap 'rm -rf "${context}"' EXIT

base="${PROTES_IMAGE:-protes:local}"
# The build context of the recipe: the wheel, and `plugins.txt`, which
# `resolve.sh` pins against the base image, from the wheel's directory
# instead of the package index
"${PYTHON:-python3}" -m pip wheel --quiet --no-deps \
  --wheel-dir "${context}/wheels" tests/smoke/plugin
wheels=("${context}"/wheels/*.whl)
hash="$(sha256sum "${wheels[0]}" | cut -d ' ' -f 1)"
pin="protes-smoke-plugin==0.1.0 --hash=sha256:${hash}"
echo "protes-smoke-plugin==0.1.0" > "${context}/plugins.in"
examples/plugin-image/resolve.sh "${base}" "${context}/plugins.in" \
  "${context}/plugins.txt" --env PIP_NO_INDEX=1 \
  --env PIP_FIND_LINKS=/wheels --volume "${context}/wheels:/wheels:ro"
if [ "$(cat "${context}/plugins.txt")" != "${pin}" ]; then
  echo "::error::resolve.sh pinned '$(cat "${context}/plugins.txt")', expected '${pin}'"
  exit 1
fi
build() {
  docker build --file examples/plugin-image/Dockerfile \
    --build-arg PROTES_IMAGE="${base}" "$@" "${context}"
}
# The build fails if a hash differs (pip's message)
cp "${context}/plugins.txt" "${context}/plugins.txt.pinned"
sed -i "s/${hash}/$(printf '0%.0s' {1..64})/" "${context}/plugins.txt"
if output="$(build --progress plain 2>&1)"; then
  echo "::error::Image built despite a wrong hash in plugins.txt"
  exit 1
fi
if ! grep -q "DO NOT MATCH THE HASHES" <<< "${output}"; then
  echo "::error::Build with a wrong hash failed for another reason: ${output}"
  exit 1
fi
mv "${context}/plugins.txt.pinned" "${context}/plugins.txt"
build --tag "${PLUGIN_IMAGE}"

# The app refuses to start if it can't load an enabled plugin
COMPOSE_FILE="${COMPOSE_FILE}:tests/smoke/plugins.yaml" \
  docker compose up -d --no-build --wait --wait-timeout 300 \
  protes protes-worker

token="$(docker compose exec -T fake-tes python -c \
  "import json, urllib.request; \
  print(json.load(urllib.request.urlopen( \
  'http://localhost:8080/oidc/token?sub=alice'))['access_token'])")"
request() {
  curl --fail --silent --show-error --header 'Accept: application/json' \
    --header "Authorization: Bearer ${token}" "$@"
}
id="$(request --header 'Content-Type: application/json' --data \
  '{"name": "plugin", "executors": [{"image": "alpine", "command": ["true"]}]}' \
  "${api}/tasks" | jq -r .id)"
task="$(request "${api}/tasks/${id}?view=FULL")"
if ! jq -e '.tags["smoke.plugin"] == "ran"' <<< "${task}" > /dev/null; then
  echo "::error::Task ${id} has no tag of the test plugin: ${task}"
  exit 1
fi
echo "Plugin smoke test passed"
