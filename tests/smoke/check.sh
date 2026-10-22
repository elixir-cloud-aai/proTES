#!/usr/bin/env bash
# Smoke test of proTES started with the Compose override of this directory
# (`compose.yaml`): relays a task to the fake TES backend, which completes it
# right away, waits until the Celery worker has tracked it (via RabbitMQ) to
# the end, and checks the other endpoints. Run it from the repository root,
# with the base URL of the TES API as argument (default
# `http://localhost:8080/ga4gh/tes/v1`); needs curl, jq and docker compose
# (with `COMPOSE_FILE` set as for `up`, to query the fake backend).
set -euo pipefail

api="${1:-http://localhost:8080/ga4gh/tes/v1}"
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

# Request a path, with an optional JSON body; check the status and that the
# JSON response matches a jq filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" data="${5:-}" actual
  local args=(--silent --show-error --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json')
  if [ -n "${data}" ]; then
    args+=(--header 'Content-Type: application/json' --data "${data}")
  fi
  actual="$(curl "${args[@]}" "${api}${path}")"
  if [ "${actual}" != "${status}" ] \
      || ! jq -e "${filter}" "${body}" > /dev/null; then
    echo "::error::${method} ${path}: status ${actual}, expected ${status} and ${filter}"
    cat "${body}"
    return 1
  fi
}

check GET /service-info 200 '.type.artifact == "tes"'
check POST /tasks 200 '.id | length > 0' \
  '{"name": "smoke", "executors": [{"image": "alpine", "command": ["true"]}]}'
id="$(jq -r .id "${body}")"
state=""
for _ in $(seq 60); do
  check GET "/tasks/${id}?view=MINIMAL" 200 '.state != null'
  state="$(jq -r .state "${body}")"
  [ "${state}" = "COMPLETE" ] && break
  sleep 1
done
if [ "${state}" != "COMPLETE" ]; then
  echo "::error::Task ${id} not complete, but ${state}"
  exit 1
fi
check GET "/tasks/${id}?view=FULL" 200 \
  '.name == "smoke" and .logs[0].metadata.forwarded_to[0].url == "http://fake-tes:8080"'
check GET '/tasks?view=MINIMAL' 200 "any(.tasks[]; .id == \"${id}\")"
check POST "/tasks/${id}:cancel" 200 '. == {}'
check GET /tasks/UNKNWN 404 '.status == 404'
# The fake backend got the task (and those of earlier runs, if not restarted)
docker compose exec -T fake-tes python -c \
  "import json, urllib.request; \
  tasks = json.load(urllib.request.urlopen('http://localhost:8080/ga4gh/tes/v1/tasks'))['tasks']; \
  assert tasks and all(task['state'] == 'COMPLETE' for task in tasks), tasks"
echo "Smoke test passed"
