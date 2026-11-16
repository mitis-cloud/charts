{{- /* The service chart's own defaults, for callers that pass a values map instead of chart values. */ -}}
{{- define "mitis.service.defaults" -}}
name: ''
workload: deployment
image: {}
capacity:
  instances: 1
  cpu: 100m
  memory: 128Mi
  memoryLimit: 256Mi
ports: {}
environment: {}
files: {}
mounts: {}
storage: {}
init: {}
sidecars: {}
jobs: {}
domain: ''
secretPrefix: ''
secretStore: aws-ssm
secretRefresh: 1h
placement: any
updates:
  strategy: rolling
  reload: true
  reloadSecrets: true
disruption:
  surgeBeforeEviction: false
render:
  engine: helm
  part: all
  routing: ingress
  order: 0
  task: ''
  capacity: git
{{- end -}}

{{- /* Renders one service from (dict "root" $ "values" <service definition>); a composing chart calls it once per service. */ -}}
{{- define "mitis.service.render" -}}
{{- $values := mergeOverwrite (include "mitis.service.defaults" . | fromYaml) (deepCopy .values) -}}
{{- $root := dict "Values" $values "Release" .root.Release "Chart" .root.Chart "Capabilities" .root.Capabilities "Template" .root.Template -}}
{{- range $part := list "inputs" "workload" "network" "response" "jobs" "canary" }}
{{- with include (printf "mitis.service.render.%s" $part) $root | trim }}
---
{{ . }}
{{- end }}
{{- end }}
{{- end -}}
