{{- define "mitis.service.name" -}}
{{- default .Release.Name .Values.name -}}
{{- end -}}

{{- define "mitis.service.labels" -}}
app.kubernetes.io/name: {{ include "mitis.service.name" . }}
app.kubernetes.io/instance: {{ include "mitis.service.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "mitis.service.selector" -}}
app.kubernetes.io/name: {{ include "mitis.service.name" . }}
app.kubernetes.io/instance: {{ include "mitis.service.name" . }}
{{- end -}}

{{- define "mitis.service.image" -}}
{{- $image := . -}}
{{- $repo := required "image.repository is required for every running container" $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repo $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo (required "supply image.tag or image.digest" $image.tag) -}}
{{- end -}}
{{- end -}}

{{- define "mitis.service.seconds" -}}
{{- $text := toString . -}}
{{- if not (regexMatch "^[1-9][0-9]*(s|m|h)$" $text) -}}
{{- fail "duration must be a positive whole number followed by s, m, or h" -}}
{{- end -}}
{{- $amount := regexFind "^[0-9]+" $text | int -}}
{{- if hasSuffix "h" $text -}}{{ mul $amount 3600 }}
{{- else if hasSuffix "m" $text -}}{{ mul $amount 60 }}
{{- else -}}{{ $amount }}{{- end -}}
{{- end -}}

{{- define "mitis.service.value" -}}
{{- $v := .value -}}
{{- if not (kindIs "map" $v) -}}
{{- toString $v -}}
{{- else if hasKey $v "url" -}}
{{- $host := $v.url.hostname | default "" -}}
{{- if not $host -}}
{{- $domain := required "a url reference needs domain or hostname" .root.Values.domain -}}
{{- $host = ternary (printf "%s.%s" ($v.url.subdomain | default "") $domain) $domain (not (empty $v.url.subdomain)) -}}
{{- end -}}
{{- printf "%s://%s%s" ($v.url.scheme | default "https") $host ($v.url.path | default "") -}}
{{- else if hasKey $v "endpoint" -}}
{{- printf "%s://%s%s%s" ($v.endpoint.scheme | default "http") $v.endpoint.service (ternary (printf ":%v" $v.endpoint.port) "" (hasKey $v.endpoint "port")) ($v.endpoint.path | default "") -}}
{{- else if hasKey $v "fact" -}}
{{- $facts := .root.Values.facts | default dict -}}
{{- if not (hasKey $facts $v.fact) -}}{{ fail (printf "environment fact %s is missing" $v.fact) }}{{- end -}}
{{- index $facts $v.fact | toString -}}
{{- else -}}{{ fail "unsupported literal/reference value" }}{{- end -}}
{{- end -}}

{{- define "mitis.service.containerName" -}}
{{- if eq .id "main" -}}{{ include "mitis.service.name" .root }}
{{- else -}}{{ printf "%s-%s" (include "mitis.service.name" .root) .id }}{{- end -}}
{{- end -}}

{{- define "mitis.service.containers" -}}
{{- $items := dict -}}
{{- if ne .Values.workload "resources" -}}{{ $_ := set $items "main" .Values }}{{- end -}}
{{- range $id, $c := .Values.init -}}{{ $_ := set $items (printf "init-%s" $id) $c }}{{- end -}}
{{- range $id, $c := .Values.sidecars -}}{{ $_ := set $items (printf "sidecar-%s" $id) $c }}{{- end -}}
{{- range $id, $job := .Values.jobs -}}
{{- $_ := set $items (printf "job-%s" $id) $job -}}
{{- range $init, $c := $job.init -}}{{ $_ := set $items (printf "job-%s-init-%s" $id $init) $c }}{{- end -}}
{{- end -}}
{{- with .Values.updates.smoke -}}
{{- $_ := set $items "smoke" . -}}
{{- range $id, $c := .init -}}{{ $_ := set $items (printf "smoke-init-%s" $id) $c }}{{- end -}}
{{- end -}}
{{- toJson $items -}}
{{- end -}}

{{- define "mitis.service.configuration" -}}
{{- $config := dict -}}
{{- range $id, $c := (include "mitis.service.containers" . | fromJson) -}}
{{- if or (eq $id "main") (hasPrefix "init-" $id) (hasPrefix "sidecar-" $id) -}}
{{- $_ := set $config $id (dict "environment" ($c.environment | default dict) "files" ($c.files | default dict)) -}}
{{- end -}}
{{- end -}}
{{- toJson $config -}}
{{- end -}}

{{- define "mitis.service.wave" -}}
{{- if eq ((.root.Values.render | default dict).engine | default "helm") "argo" -}}
argocd.argoproj.io/sync-wave: {{ add (mul 100 .root.Values.render.order) .wave | quote }}
{{- end -}}
{{- end -}}

{{- define "mitis.service.outputsName" -}}
{{- printf "%s-outputs" (include "mitis.service.name" .) -}}
{{- end -}}

{{- define "mitis.service.ownsAccount" -}}
{{- if or .Values.permissions .Values.outputs -}}true{{- end -}}
{{- end -}}
