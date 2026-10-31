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
`owners-get-provenance`, `admins-get-provenance`, `users-list-plugins`,
`users-get-plugins`, `admins-list-add-permissions` and
`admins-manage-permissions`): a config
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

- `GET /tasks/{id}/provenance` is allowed for the owner and for admins
  (rules `owners-get-provenance` and `admins-get-provenance`); everyone else
  gets `403 Forbidden`. The provenance holds no inputs or logs of the task,
  only which plugins and TES instances processed it (see
  [Provenance](api.md#provenance)); remove `admins-get-provenance` (`null`
  in an overlay) to keep it from admins.

- `GET /plugins` and `GET /plugins/{plugin_id}` (what users may choose per
  task, see [Choices of clients](plugins.md#choices-of-clients)) are allowed
  for the role `user` (rules `users-list-plugins` and `users-get-plugins`);
  everyone else gets `403 Forbidden`, and requests without an access token
  `401 Unauthorized`.

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
`taskStore`, or of the database in `MONGO_DBNAME` if set); the configured
policies are restored on each startup. The Helm chart's Ingress (or
HTTPRoute, or Route) routes all paths to the app, including the permissions
API; to keep it off the internet, block `/admin/` in front of the cluster
(see [Exposing the app](deployment.md#exposing-the-app)).

The permissions API can write any rule, also owner roles on tasks (`g2`
rules), so admins could make themselves owners of the tasks of others. FOCA
logs each request to it; give the role `admin` only to operators trusted
with the data of the tasks.

## Deployment settings

Which identity providers are trusted, for which audiences, and which groups
have which role is deployment configuration: the base config has no
defaults, and the app refuses to start without trusted issuers, without an
audience (unless `security.auth.verify_audience` is false, which accepts
tokens for any app, or each issuer has an audience of its own, see below)
and without a group mapped to the role `admin` (unless
`security.access_control.require_admin` is false). Set them in a [config
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

Issuers may also be maps of the issuer (`issuer`) and settings of their own
(FOCA's `IssuerConfig`): the namespace of their users' subjects
(`namespace`, see [Task owners](operations.md#task-owners)), and an
`audience`, a group claim (`claim_groups`) and `userinfo_claims` that
replace the global ones for the issuer's tokens (unless unset or null), e.g.:

```yaml
security:
  auth:
    issuers:
      - issuer: https://login.example.org/oidc/
        audience:
          - my-portal-client-id
      - issuer: https://other.example.org/
        audience:
          - my-portal-client-id
        # take no groups from this issuer's tokens
        claim_groups: ""
```

Group claim values are not namespaced: take groups only from issuers that
cannot issue each other's groups, and set `claim_groups: ""` for the others
(see [FOCA's authentication
options](https://foca.readthedocs.io/en/latest/configuration/auth/)).

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

proTES authenticates at TES instances with credentials of its own, never
with those of its callers: the access tokens of requests are not forwarded,
and no credentials of callers are stored or queued for the Celery worker.
How proTES authenticates is set for each entry of `custom.tes.backends`, by
`auth` (TES instances discovered in a service registry get the `auth` of
the registry's entry, `client_credentials` or `none`):

- `client_credentials` (the default): with an access token of proTES itself,
  which it gets from its identity provider with the OAuth 2.0 client
  credentials grant ([RFC 6749, section
  4.4](https://www.rfc-editor.org/rfc/rfc6749#section-4.4)). TES instances
  accept tokens of proTES' client (e.g., by the `client_id` claim of the
  token). proTES caches the token, and fetches a new one shortly before it
  expires (`refresh_before` seconds, at most half its lifetime), also in the
  Celery worker, which polls tasks for hours;
- `basic`: with HTTP Basic credentials from environment variables, whose
  names are set in `basic` (`username_env`, `password_env`). This is a
  transitional mode for TES instances that don't accept tokens yet, and will
  be removed once all of them do;
- `none`: without credentials, e.g., for TES instances on an internal
  network that need none.

The client ID and secret of proTES are read from the environment variables
`PROTES_CLIENT_ID` and `PROTES_CLIENT_SECRET`, and Basic credentials from
the variables named in the config, e.g., set from Kubernetes Secrets; they
are never part of the config or the database. The app refuses to start if
one of them is not set for a TES instance that needs it, and warns of each
TES instance with `basic` or `none`. Set the TES instances and the identity
provider of proTES in a [config overlay](configuration.md), e.g.:

```yaml
custom:
  tes:
    backends:
      - type: ga4gh-tes
        url: https://tes.example.org
      - type: ga4gh-tes
        url: https://tes.example.com
        auth: basic
        basic:
          username_env: TES_EXAMPLE_COM_USERNAME
          password_env: TES_EXAMPLE_COM_PASSWORD
    client_credentials:
      # The token endpoint is that of the issuer's OpenID Connect discovery
      # document (`token_endpoint` sets it instead)
      issuer: https://login.example.org/oidc/
      # Scope of the tokens, if the identity provider needs one
      scope: null
      # `client_secret_basic` (HTTP Basic) or `client_secret_post`
      auth_method: client_secret_basic
```

The Docker Compose setup passes `PROTES_CLIENT_ID` and
`PROTES_CLIENT_SECRET` of the host to the app and the Celery worker; with
the Helm chart, set them (and the variables of Basic credentials) from a
Secret with `protes.extraEnv` (see the chart's README). The Celery worker
checks the credentials on startup, too.

The client of proTES is registered at the identity provider for the client
credentials grant; it is a client of its own, not the client of the front
ends whose tokens proTES accepts (`security.auth.audience`). All TES
instances with `client_credentials` get the same token, so each of them
could use it at the others until it expires: configure only TES instances
that you trust, and keep the lifetime of the tokens short. Tasks are only
forwarded to configured TES instances, and to those discovered in the
service registries of `custom.tes.backends` (only verified ones, see
[Discovery in a service
registry](configuration.md#discovery-in-a-service-registry)); plugins
can only choose among them (see [Plugins](plugins.md)). Plugins never see
the credentials of TES instances, and can't choose other credentials for
a TES instance than its own; in the `forward` phase, they may only drop
them (`none`), e.g., to send credentials of their own. Keep TES instances in the list until their tasks have finished:
tasks of TES instances that are no longer configured can't be tracked or
canceled (their state becomes `UNKNOWN`, and is tracked again once the TES
instance is configured again).

URLs of TES instances, registries and of the identity provider must use
`https` (`url_schemes` of each entry of `custom.tes.backends`, and
`custom.tes.url_schemes` for the identity provider); allow `http` for local
tests only, as tokens and credentials are sent unencrypted then. URLs of TES
instances must not have credentials (`https://user:password@host`).

Credentials in the URLs of inputs and outputs of tasks
(`https://user:password@host/...`) are not accepted either: such tasks are
rejected with `400 Bad Request`, or, if
`custom.controllers.post_task.url_credentials` is `strip`, the credentials
are removed before the task is stored and forwarded (see
[Tasks](configuration.md#tasks)). Credentials that plugins add are
always removed. The headers `Authorization`, `Cookie` and
`Proxy-Authorization` of callers are left out of what plugins see, but
plugins get the caller's validated access token (FOCA's `Principal.token`,
e.g., for token exchange): enable only plugins that you trust with it.

proTES doesn't tell TES instances on whose behalf it forwards tasks yet:
they only see proTES. Tags with the identity of the user are planned, once
their names are agreed with other services.
