{{/*
Settings of the app for the templates of the library chart foca-common (see
`foca-common.checkSettings` and "Helm library chart" in FOCA's docs), as
YAML; the templates are called with a dict of the root context (`root`) and
these settings (`settings`). They follow the base config in the image
(`pro_tes/config.yaml`): its size limit of request bodies
(`server.max_content_length`), its token issuers (so an audience is
required), its database (`db.dbs`) and that of access control
(`security.access_control.db_name`). Every template includes the settings,
so the app's checks (`protes.validate`) run first, before those of the
library and whichever template Helm renders first.
*/}}
{{- define "protes.settings" -}}
{{- include "protes.validate" . -}}
valuesKey: protes
maxContentLength: 1048576
requireAudience: true
mongodb:
  databases: [taskStore]
  accessControlDatabase: taskStore
{{- end -}}

{{/*
Checks of the values and the release specific to this app; renders nothing.
Included by the settings (`protes.settings`), so in every template, before
the library's checks (`foca-common.validate`); the checks of renamed and
removed values come first, so that they are reported instead of their
consequences (e.g., a missing `hostName` for `applicationDomain`).
*/}}
{{- define "protes.validate" -}}
{{- include "protes.checkLegacyValues" . -}}
{{- include "protes.checkSecurity" . -}}
{{- include "protes.checkExtraEnv" . -}}
{{- include "protes.checkFlower" . -}}
{{- end -}}

{{/*
Fails on values of the legacy chart (versions 2 and 3, before the library
chart), which the chart would otherwise ignore; renders nothing.
*/}}
{{- define "protes.checkLegacyValues" -}}
{{- $hint := "(see deployment/README.md, From the legacy chart)" -}}
{{- $removed := dict
  "applicationDomain" "set hostName to the full public host name of the app instead (it was <protes.appName>.<applicationDomain>)"
  "clusterType" "an OpenShift Route is created if the cluster serves route.openshift.io/v1 and neither an Ingress nor an HTTPRoute is enabled"
  "storageAccessMode" "set mongodb.persistence.accessModes and broker.persistence.accessModes instead"
  "celeryWorker" "the Celery worker is configured in worker (its image is that of protes)"
  "rabbitmq" "the broker is configured in broker, with its credentials in broker.existingSecret"
-}}
{{- range $key := keys $removed | sortAlpha -}}
{{- if hasKey $.Values $key -}}
{{- fail (printf "%s was removed with the chart on foca-common: %s %s" $key (get $removed $key) $hint) -}}
{{- end -}}
{{- end -}}
{{- range $key := list "protes" "mongodb" "flower" -}}
{{- $values := index $.Values $key -}}
{{- if and (kindIs "map" $values) (hasKey $values "appName") -}}
{{- fail (printf "%s.appName was removed with the chart on foca-common: resource names are derived from the release name; set fullnameOverride to choose them at installation %s" $key $hint) -}}
{{- end -}}
{{- end -}}
{{- if and (kindIs "map" .Values.mongodb) (hasKey .Values.mongodb "secret") -}}
{{- fail (printf "mongodb.secret was removed with the chart on foca-common: the chart creates no Secrets; create one with the MongoDB credentials and set mongodb.auth.existingSecret %s" $hint) -}}
{{- end -}}
{{- if and (kindIs "map" .Values.flower) (hasKey .Values.flower "basicAuth") -}}
{{- fail (printf "flower.basicAuth was removed with the chart on foca-common: create a Secret with the key basic-auth (<user>:<password>) and set flower.existingSecret %s" $hint) -}}
{{- end -}}
{{- end -}}

{{/*
As the app on startup, refuse to render without trusted token issuers (if
access control is enabled, `security.access_control.enabled`), and, if it is
also enforced (`security.access_control.enforce`), with a group mapping but no
group claim, or without a group mapped to the role `admin` (unless admins are
explicitly not required); renders nothing. The audience is checked by the
library (`requireAudience`).
*/}}
{{- define "protes.checkSecurity" -}}
{{- $config := .Values.config | default dict -}}
{{- $mapping := dict -}}
{{- $claimGroups := "" -}}
{{- $issuers := list -}}
{{- $enabled := "true" -}}
{{- $enforce := "true" -}}
{{- $security := get $config "security" -}}
{{- if kindIs "map" $security -}}
{{- $securityAccessControl := get $security "access_control" -}}
{{- if kindIs "map" $securityAccessControl -}}
{{- if hasKey $securityAccessControl "enabled" -}}
{{- $enabled = include "protes.parseBool" (dict
  "value" (get $securityAccessControl "enabled")
  "key" "config.security.access_control.enabled"
) -}}
{{- end -}}
{{- if hasKey $securityAccessControl "enforce" -}}
{{- $enforce = include "protes.parseBool" (dict
  "value" (get $securityAccessControl "enforce")
  "key" "config.security.access_control.enforce"
) -}}
{{- end -}}
{{- /* Only if set, as `get` returns an empty string for missing keys */ -}}
{{- if hasKey $securityAccessControl "group_mapping" -}}
{{- $mapping = get $securityAccessControl "group_mapping" -}}
{{- end -}}
{{- if hasKey $securityAccessControl "claim_groups" -}}
{{- $claimGroups = get $securityAccessControl "claim_groups" -}}
{{- if kindIs "invalid" $claimGroups -}}
{{- $claimGroups = "" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $auth := get $security "auth" -}}
{{- if kindIs "map" $auth -}}
{{- $issuers = get $auth "issuers" | default list -}}
{{- end -}}
{{- end -}}
{{- /*
Issuers are compared with the `iss` claim as written, so surrounding
whitespace is refused, too
*/ -}}
{{- $issuersError := "config.security.auth.issuers must list the trusted token issuers (the iss claim of their tokens, e.g., https://login.example.org/oidc/, without surrounding whitespace): the app trusts none by default" -}}
{{- if eq $enabled "true" -}}
{{- if not (and (kindIs "slice" $issuers) $issuers) -}}
{{- fail $issuersError -}}
{{- end -}}
{{- range $issuer := $issuers -}}
{{- if not (and (kindIs "string" $issuer) (trim $issuer) (eq $issuer (trim $issuer))) -}}
{{- fail $issuersError -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not (kindIs "string" $claimGroups) -}}
{{- fail "config.security.access_control.claim_groups must be the name of a claim (a string)" -}}
{{- end -}}
{{- if ne $claimGroups (trim $claimGroups) -}}
{{- fail "config.security.access_control.claim_groups must be the name of a claim, without surrounding whitespace" -}}
{{- end -}}
{{- if kindIs "invalid" $mapping -}}
{{- $mapping = dict -}}
{{- else if not (kindIs "map" $mapping) -}}
{{- fail "config.security.access_control.group_mapping must be a map of groups to roles" -}}
{{- end -}}
{{- $enforced := and (eq $enabled "true") (eq $enforce "true") -}}
{{- if and $enforced $mapping (not $claimGroups) -}}
{{- fail "config.security.access_control.claim_groups must name the claim with the groups of users (e.g., groups), as groups are mapped to roles (group_mapping); also add it to config.security.auth.userinfo_claims if the identity provider releases it via user info only" -}}
{{- end -}}
{{- $requireAdmin := "true" -}}
{{- $custom := get $config "custom" -}}
{{- $accessControl := dict -}}
{{- if kindIs "map" $custom -}}
{{- $accessControl = get $custom "access_control" | default dict -}}
{{- end -}}
{{- if and (kindIs "map" $accessControl) (hasKey $accessControl "require_admin") -}}
{{- $requireAdmin = include "protes.parseBool" (dict
  "value" (get $accessControl "require_admin")
  "key" "config.custom.access_control.require_admin"
) -}}
{{- end -}}
{{- if and $enforced (eq $requireAdmin "true") (not (has "admin" (values $mapping))) -}}
{{- fail "config.security.access_control.group_mapping must map a group to the role admin (e.g., the admin group of the identity provider), or set config.custom.access_control.require_admin to false" -}}
{{- end -}}
{{- end -}}

{{/*
A boolean of the config, parsed as pydantic parses booleans (lax mode), as the
app does: `true` or `false`, the strings `1`, `on`, `t`, `true`, `y`, `yes`
and `0`, `off`, `f`, `false`, `n`, `no` (in any case, without whitespace),
and the numbers 1 and 0. Renders "true" or "false"; fails otherwise. Call
with a dict of the value (`value`) and its key (`key`).
*/}}
{{- define "protes.parseBool" -}}
{{- $value := .value -}}
{{- $text := lower (toString $value) -}}
{{- $isNumber := or (kindIs "float64" $value) (kindIs "int64" $value) (kindIs "int" $value) -}}
{{- if kindIs "bool" $value -}}
{{- $value -}}
{{- else if and (kindIs "string" $value) (has $text (list "1" "on" "t" "true" "y" "yes")) -}}
true
{{- else if and (kindIs "string" $value) (has $text (list "0" "off" "f" "false" "n" "no")) -}}
false
{{- else if and $isNumber (has $text (list "0" "1")) -}}
{{- eq $text "1" -}}
{{- else if kindIs "invalid" $value -}}
{{- fail (printf "%s must be true or false, not null" .key) -}}
{{- else -}}
{{- fail (printf "%s must be true or false, not %v" .key $value) -}}
{{- end -}}
{{- end -}}

{{/*
Environment variables that the chart sets in the app's and the worker's
containers, which `protes.extraEnv` must not set again.
*/}}
{{- define "protes.reservedEnv" -}}
{{- list "FOCA_CONFIG_OVERLAY" "GUNICORN_CMD_ARGS" "MONGO_USERNAME" "MONGO_PASSWORD" "CELERY_BROKER_URL" "CELERY_RESULT_BACKEND" | toJson -}}
{{- end -}}

{{/*
Fails unless `protes.extraEnv` is a list of environment variables (each with
a `name`, set once) that the chart does not set itself; renders nothing.
*/}}
{{- define "protes.checkExtraEnv" -}}
{{- $env := dig "extraEnv" nil (.Values.protes | default dict) -}}
{{- if not (kindIs "invalid" $env) -}}
{{- if not (kindIs "slice" $env) -}}
{{- fail "protes.extraEnv must be a list of environment variables (name and value or valueFrom)" -}}
{{- end -}}
{{- $reserved := include "protes.reservedEnv" . | fromJsonArray -}}
{{- $names := list -}}
{{- range $var := $env -}}
{{- $name := and (kindIs "map" $var) (get $var "name") -}}
{{- if not (and (kindIs "string" $name) $name) -}}
{{- fail "protes.extraEnv must be a list of environment variables, each with a name" -}}
{{- end -}}
{{- if has $name $reserved -}}
{{- fail (printf "protes.extraEnv must not set %s, which the chart sets (%s)" $name (join ", " $reserved)) -}}
{{- end -}}
{{- if has $name $names -}}
{{- fail (printf "protes.extraEnv sets %s more than once" $name) -}}
{{- end -}}
{{- $names = append $names $name -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
A Deployment rendered by the library (`manifest`, YAML; empty if disabled),
with the environment variables of `protes.extraEnv` added to its containers,
e.g., credentials from Secrets for TES backends; unchanged without them.
Call with a dict of the root context (`root`) and the manifest.
*/}}
{{- define "protes.withExtraEnv" -}}
{{- $env := dig "extraEnv" nil (.root.Values.protes | default dict) | default list -}}
{{- if and $env (trim .manifest) -}}
{{- $manifest := .manifest | fromYaml -}}
{{- if hasKey $manifest "Error" -}}
{{- fail (printf "protes: could not add protes.extraEnv to a Deployment of the library chart: %s" $manifest.Error) -}}
{{- end -}}
{{- range $container := $manifest.spec.template.spec.containers -}}
{{- $_ := set $container "env" (concat ($container.env | default list) $env) -}}
{{- end -}}
{{- toYaml $manifest -}}
{{- else -}}
{{- .manifest -}}
{{- end -}}
{{- end -}}

{{/*
Name of Flower's resources; truncated like the library's names of the other
components (e.g., `foca-common.workerFullname`).
*/}}
{{- define "protes.flowerFullname" -}}
{{- printf "%s-flower" (include "foca-common.fullname" . | trunc 56 | trimSuffix "-") -}}
{{- end -}}

{{/*
Fails unless Flower, if enabled, has its credentials (`flower.existingSecret`)
and the broker's (`broker.existingSecret`), and a name of its own; renders
nothing.
*/}}
{{- define "protes.checkFlower" -}}
{{- if dig "enabled" false (.Values.flower | default dict) -}}
{{- if not .Values.flower.existingSecret -}}
{{- fail "flower.existingSecret must be set if flower.enabled: a Secret with the key basic-auth (<user>:<password>), as Flower shows the arguments of tasks" -}}
{{- end -}}
{{- if not (dig "existingSecret" "" (.Values.broker | default dict)) -}}
{{- fail "broker.existingSecret must be set if flower.enabled: Flower connects to the broker with its URL (broker-url)" -}}
{{- end -}}
{{- if eq (include "foca-common.fullname" .) (include "protes.flowerFullname" .) -}}
{{- fail (printf "the app and Flower resources would both be named %s: choose a fullnameOverride that does not end in -flower" (include "foca-common.fullname" .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Fails on upgrades of releases of the legacy chart (versions 2 and 3, before
the library chart), which would delete their volumes with the MongoDB data,
as their names changed; renders nothing. Releases are recognized by their
Deployments (`protes.failIfLegacy`), which, unlike claims of volumes, do not
outlast an uninstallation. Included once (in the app's ConfigMap template),
as it looks up resources in the cluster. `lookup` finds nothing in
`helm template` and client-side dry runs.
*/}}
{{- define "protes.checkUpgrade" -}}
{{- if .Release.IsUpgrade -}}
{{- $deployments := lookup "apps/v1" "Deployment" .Release.Namespace "" | default dict -}}
{{- range $deployment := (get $deployments "items" | default list) -}}
{{- include "protes.failIfLegacy" (dict "root" $ "resource" $deployment) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Fails if a Deployment (looked up in the cluster) belongs to this release, but
was created by the legacy chart (version 2 or 3): without the label
`app.kubernetes.io/instance`. Call with a dict of the root context (`root`)
and the Deployment (`resource`).
*/}}
{{- define "protes.failIfLegacy" -}}
{{- $owner := dig "metadata" "annotations" "meta.helm.sh/release-name" "" .resource -}}
{{- $labels := dig "metadata" "labels" dict .resource -}}
{{- if and (eq $owner .root.Release.Name) (not (hasKey $labels "app.kubernetes.io/instance")) -}}
{{- fail (printf "release %s was installed with the legacy chart (version 2 or 3), and upgrading it would delete its volume with the MongoDB data (Deployment %s): uninstall it and install the current chart instead, keeping the data as described in deployment/README.md (From the legacy chart)" .root.Release.Name (dig "metadata" "name" "" .resource)) -}}
{{- end -}}
{{- end -}}
