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
| `user` | create, list, read and cancel tasks (`POST /tasks`, `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}:cancel`) |
| `admin` | do everything |

Roles are given to groups of the identity provider: the values of the claim
`security.access_control.claim_groups` (from the token, or from the user info
endpoint if listed in `security.auth.userinfo_claims`) are mapped to roles by
`security.access_control.group_mapping`. Values are matched exactly, so list
each group whose members get a role, including subgroups. Users may see and
cancel all tasks, not only their own.

The roles and their permissions are the policies of
`security.access_control.policies` in the base config (see
[FOCA's access control](https://foca.readthedocs.io/en/latest/access-control/)),
named rules (`users-create-list-tasks` and `users-get-cancel-tasks`): a config
overlay adds, changes or removes (`null`) single rules by name, e.g., a rule
that lets the role `user` only list and read tasks:

```yaml
security:
  access_control:
    policies:
      users-create-list-tasks: [p, user, /ga4gh/tes/v1/tasks, (GET)|(HEAD)]
      users-get-cancel-tasks: [p, user, "/ga4gh/tes/v1/tasks/:id", (GET)|(HEAD)]
```

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
the role `user` (only admins can use the API then), if `group_mapping` is
set without `claim_groups` (no group gets a role then), and if access control
is not enabled and enforced (`security.access_control.enabled` and
`enforce`; anyone with a valid token may do everything then). The Docker Compose setup
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
