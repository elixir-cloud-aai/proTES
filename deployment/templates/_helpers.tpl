{{/*
Image of the app or the Celery worker: `<key>.image` with `<key>.tag`, by
default the chart's `appVersion`; an image with a tag or digest is used as it
is. Call with a dict of the root context (`root`), the values of the
component (`values`) and their key (`key`, for error messages).
*/}}
{{- define "protes.image" -}}
{{- $image := .values.image -}}
{{- $tag := .values.tag | default "" | toString -}}
{{- if or (contains "@" $image) (regexMatch ":[^/]+$" $image) -}}
{{- if $tag -}}
{{- fail (printf "%s.tag must not be set if %s.image includes a tag or digest" .key .key) -}}
{{- end -}}
{{- $image -}}
{{- else -}}
{{- printf "%s:%s" $image ($tag | default .root.Chart.AppVersion) -}}
{{- end -}}
{{- end -}}
