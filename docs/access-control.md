# Access control

## Authentication

The TES API requires an access token of a trusted identity provider (OpenID
Connect), sent as a bearer token, for all operations on tasks:

```bash
curl -X POST "http://localhost:8080/ga4gh/tes/v1/tasks" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d @task.json
```

`GET /service-info`, the specification (`/ga4gh/tes/v1/openapi.json`) and
the Swagger UI are public. Requests without a valid
token are rejected with `401 Unauthorized` and a Bearer challenge
(`WWW-Authenticate: Bearer`, RFC 6750; with `error="invalid_token"` for
invalid tokens); requests with a valid token, but without the required role,
with `403 Forbidden` (both as [problem details](api.md#errors)).

proTES accepts only tokens that:

- are access tokens as per [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068)
  (`typ` header `at+jwt`), not, e.g., ID tokens;
- are issued by one of the trusted issuers (`security.auth.issuers`, the
  `iss` claim), and verified with the issuer's keys (from its OpenID Connect
  discovery document);
- are for one of the configured audiences (`security.auth.audience`, the
  `aud` claim), i.e., were issued to one of the apps that call proTES (e.g.,
  its front ends, by their client IDs);
- if `security.auth.clients` is set, were obtained by one of these clients
  (the `client_id` claim, or `azp` if there is none);
- have not expired.

If your identity provider sets another `typ` header on its access tokens,
list it in `security.auth.token_types`; if it sets none, disable the check
with `security.auth.verify_token_type: false` (see [FOCA's authentication
options](https://foca.readthedocs.io/en/latest/configuration/auth/)).

## Roles

| Role | May |
| --- | --- |
| `user` | create tasks, and list their own (`POST /tasks`, `GET /tasks`) |
| `owner` (of a task) | read and cancel the task (`GET /tasks/{id}`, `POST /tasks/{id}:cancel`) |
| `admin` | manage the access control rules ([permissions API](#permissions-api)); not the tasks of others |

Roles are given to groups of the identity provider: the values of the claim
`security.access_control.claim_groups` (from the token, or from the user info
endpoint if listed in `security.auth.userinfo_claims`) are mapped to the
roles `user` and `admin` by `security.access_control.group_mapping`. Values
are matched exactly, so list each group whose members get a role, including
subgroups. The role `owner` is not mapped from groups: users get it on the
tasks they create (see [Task ownership](#task-ownership)).

The roles and their permissions are the policies of
`security.access_control.policies` in the base config (see
[FOCA's access control](https://foca.readthedocs.io/en/latest/access-control/)),
named rules (`users-create-list-tasks`, `owners-get-cancel-tasks`,
`admins-list-add-permissions` and `admins-manage-permissions`): a config
overlay adds, changes or removes (`null`) single rules by name, e.g., rules
that let users only list their tasks, and owners only read them:

```yaml
security:
  access_control:
    policies:
      users-create-list-tasks: [p, user, /ga4gh/tes/v1/tasks, (GET)|(HEAD)]
      owners-get-cancel-tasks: [p, owner, "/ga4gh/tes/v1/tasks/:id", (GET)|(HEAD)]
```

The access control model of proTES (`pro_tes/access_control/model.conf`)
differs from FOCA's reference model in that the role `admin` may not do
everything: admins only have the rights that rules give them.

## Task ownership

The user who creates a task (the `sub` claim of the access token, or the
claim set in `security.access_control.claim_identity`) becomes its owner:

- `GET /tasks` lists only the tasks of the caller, also for admins;
- `GET /tasks/{id}` and `POST /tasks/{id}:cancel` are allowed for the owner
  only; everyone else gets `403 Forbidden`, also admins and for unknown
  tasks, so that responses don't tell which tasks exist. The owner gets
  `404 Not Found` for a task that was deleted from the database.

Tasks are not shared with other users, and admins can't read or cancel the
tasks of others through the API: tasks and their logs may hold sensitive
data, and users' entitlements to access data can't be passed on to others.
Owners keep access to their tasks if they lose the role `user`, but can't
create or list tasks then.

Owners are stored as resource-scoped roles of FOCA (`g2` rules in the
collection `policy_rules`) on the task and on its cancellation, e.g., for
task `ABC123`:

```text
g2, user:<sub>, owner, /ga4gh/tes/v1/tasks/ABC123
g2, user:<sub>, owner, /ga4gh/tes/v1/tasks/ABC123:cancel
```

The task document records its owner, too (`user_id`), e.g., for operators
looking for the tasks of a user (see [Operations](operations.md#task-owners)).
Each task adds two rules, and FOCA loads all rules on each request (and
creating or listing tasks loads them a few more times), so the time of each
request grows with the number of tasks (see FOCA's notes on
[scaling](https://foca.readthedocs.io/en/latest/access-control/#scaling)).

Ownership is taken from the caller's own token only: proTES doesn't support
service clients, also acting for users, yet, and refuses to start if
`security.auth.service_clients`, `security.auth.on_behalf_of_header` or
`security.access_control.client_mapping` is set.

New tasks never get the identifier of a stored task, or of a task that
owner roles are left on (e.g., of a deleted task), so that nobody gets
access to the task of someone else with the same identifier.

A list replaces all rules of the base config. The configured rules replace
the stored ones atomically on each startup; if an overlay leaves no rules,
the stored ones are removed.
The role `admin` comes only from groups in `group_mapping`: the startup
check doesn't count `g` rules of the policies or of the permissions API.

## Permissions API

Admins can list, add, change and delete access control rules at runtime
through [FOCA's permissions
API](https://foca.readthedocs.io/en/latest/access-control/) at
`/admin/access-control/permissions`; everyone else gets `401` (no token) or
`403`. The rules are stored in the database (collection `policy_rules` of
`taskStore`, or of the database in `MONGO_DBNAME` if set); the configured policies are restored on each startup. The Helm
chart's ingress routes only the TES API, so the permissions API is reachable
only from within the cluster (e.g., with `kubectl port-forward`).

The permissions API can write any rule, also owner roles on tasks (`g2`
rules), so admins could make themselves owners of the tasks of others. FOCA
logs each request to it; give the role `admin` only to operators trusted
with the data of the tasks.

## Deployment settings

Which identity providers are trusted, for which audiences, and which groups
have which role is deployment configuration: the base config has no
defaults, and the app refuses to start without trusted issuers, without an
audience (unless `security.auth.verify_audience` is false, which accepts
tokens for any app) and without a group mapped to the role `admin` (unless
`custom.access_control.require_admin` is false). Set them in a [config
overlay](configuration.md), e.g.:

```yaml
security:
  auth:
    issuers:
      - https://login.example.org/oidc/
    audience:
      - my-portal-client-id
    # if the identity provider releases groups via user info only
    userinfo_claims:
      - groups
  access_control:
    claim_groups: groups
    group_mapping:
      compute-users: user
      compute-admins: admin
```

The app logs the groups of each role on startup, and warns if no group has
the role `user` (nobody can create tasks then) and if `group_mapping` is
set without `claim_groups` (no group gets a role then). It refuses to start
if access control is not enabled and enforced
(`security.access_control.enabled` and `enforce`), as users could access
the tasks of others then. The Docker Compose setup
uses `config.dev.yaml`, which accepts tokens for any audience and requires no
admins; set your issuer and groups there. For local tests without an
identity provider, the [smoke test](development.md) brings a fake one.

## Credentials of TES instances

proTES forwards tasks to TES instances without credentials of its own: the
`Authorization` header of requests carries the access token, so HTTP Basic
credentials can no longer be passed through to TES instances (TES instances
that require them can't be used for now; credentials of proTES for TES
instances are planned,
[#213](https://github.com/elixir-cloud-aai/proTES/issues/213)). Credentials
in the URLs of inputs and outputs (`https://user:password@host/...`) are
removed before the task is forwarded, unless the TES instance is a
[Funnel](https://ohsu-comp-bio.github.io/funnel/) instance.
