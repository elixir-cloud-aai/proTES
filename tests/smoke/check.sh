#!/usr/bin/env bash
# Smoke test of proTES started with the Compose override of this directory
# (`compose.yaml`): relays a task to the fake TES backend, which completes it
# right away, waits until the Celery worker has tracked it (via RabbitMQ) to
# the end, and checks the other endpoints and access control, with access
# tokens of the fake identity provider. Run it from the repository root,
# with the base URL of the TES API as argument (default
# `http://localhost:8080/ga4gh/tes/v1`); needs curl, jq and docker compose
# (with `COMPOSE_FILE` set as for `up`, to query the fake backend).
set -euo pipefail

api="${1:-http://localhost:8080/ga4gh/tes/v1}"
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

# Get an access token of a user from the fake identity provider
token() {
  docker compose exec -T fake-tes python -c \
    "import json, urllib.request; \
    print(json.load(urllib.request.urlopen( \
    'http://localhost:8080/oidc/token?sub=$1'))['access_token'])"
}
# Access token of the requests of `check` (none if empty)
auth="$(token alice)"

# Request a path, with an optional JSON body; check the status and that the
# JSON response matches a jq filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" data="${5:-}" actual
  local args=(--silent --show-error --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json')
  if [ -n "${auth}" ]; then
    args+=(--header "Authorization: Bearer ${auth}")
  fi
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
# Unknown tasks are forbidden, as tasks of others are
check GET /tasks/UNKNWN 403 '.status == 403'
# Access control: no token (but the service info is public), no role, other
# users and admins (only owners see and cancel tasks)
auth=""
check GET /service-info 200 '.type.artifact == "tes"'
check GET /tasks 401 '.status == 401'
auth="$(token eve)"
check GET /tasks 403 '.status == 403'
auth="$(token bob)"
check GET '/tasks?view=MINIMAL' 200 "all(.tasks[]; .id != \"${id}\")"
check GET "/tasks/${id}" 403 '.status == 403'
check POST "/tasks/${id}:cancel" 403 '.status == 403'
auth="$(token admin)"
check GET "/tasks/${id}" 403 '.status == 403'
# The fake backend got the task (and those of earlier runs, if not
# restarted), with the access token of proTES (which it requires); listed
# with a token of proTES' client
docker compose exec -T fake-tes python -c \
  "import base64, json, sys, urllib.request; \
  sys.path.insert(0, '/smoke'); \
  from fake_tes import CLIENT_ID, CLIENT_SECRET; \
  basic = base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode(); \
  request = urllib.request.Request('http://localhost:8080/oidc/token', \
    data=b'grant_type=client_credentials', headers={'Authorization': f'Basic {basic}'}); \
  token = json.load(urllib.request.urlopen(request))['access_token']; \
  request = urllib.request.Request('http://localhost:8080/ga4gh/tes/v1/tasks', \
    headers={'Authorization': f'Bearer {token}'}); \
  tasks = json.load(urllib.request.urlopen(request))['tasks']; \
  assert tasks and all(task['state'] == 'COMPLETE' for task in tasks), tasks"
echo "Smoke test passed"
