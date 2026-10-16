{{- define "mitis.service.jobSpec" -}}
{{- $job := .job -}}
backoffLimit: {{ if hasKey $job "retries" }}{{ $job.retries }}{{ else }}0{{ end }}
{{- if and (ne ($job.timeout | default "") "none") (or (not $job.schedule) $job.timeout) }}
activeDeadlineSeconds: {{ include "mitis.service.seconds" ($job.timeout | default "10m") }}
{{- end }}
{{- if $job.resultLifetime }}
ttlSecondsAfterFinished: {{ include "mitis.service.seconds" $job.resultLifetime }}
{{- else if regexMatch "^[1-9][0-9]*(s|m|h)$" ($job.cleanup | default "") }}
ttlSecondsAfterFinished: {{ include "mitis.service.seconds" $job.cleanup }}
{{- end }}
template:
  metadata:
    labels:
      mitis.cloud/job: {{ .name }}
  spec:
    {{- include "mitis.service.pod" (dict "root" .root "container" $job "name" "task" "id" .id "prefix" (printf "%s-" .id) "init" $job.init "restart" "Never") | nindent 4 }}
{{- end -}}

{{- define "mitis.service.job" -}}
{{- $job := .job -}}
{{- $root := .root -}}
{{- $name := $job.name | default (printf "%s-%s" (include "mitis.service.name" $root) .key) -}}
{{- $run := $job.run | default "after-ready" -}}
{{- $order := int ($job.order | default 0) -}}
{{- $cleanup := $job.cleanup | default "on-success" -}}
{{- $immediate := has $cleanup (list "on-success" "on-completion") -}}
{{- $wave := add 10 $order -}}{{ if eq $run "before" }}{{ $wave = add -10 $order }}{{ end }}
---
apiVersion: batch/v1
kind: {{ ternary "CronJob" "Job" (not (empty $job.schedule)) }}
metadata:
  name: {{ $name }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- if $job.schedule }}
    {{- include "mitis.service.wave" (dict "root" $root "wave" 10) | nindent 4 }}
    {{- else }}
    {{- if eq $root.Values.render.engine "argo" }}
    argocd.argoproj.io/hook: Sync
    {{- include "mitis.service.wave" (dict "root" $root "wave" $wave) | nindent 4 }}
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation{{ if $immediate }},HookSucceeded{{ end }}{{ if eq $cleanup "on-completion" }},HookFailed{{ end }}
    {{- else }}
    helm.sh/hook: {{ ternary "pre-install,pre-upgrade" "post-install,post-upgrade" (eq $run "before") }}
    helm.sh/hook-weight: {{ $wave | quote }}
    helm.sh/hook-delete-policy: before-hook-creation{{ if $immediate }},hook-succeeded{{ end }}{{ if eq $cleanup "on-completion" }},hook-failed{{ end }}
    {{- end }}
    {{- end }}
spec:
  {{- if $job.schedule }}
  schedule: {{ $job.schedule | quote }}
  {{- with $job.timeZone }}
  timeZone: {{ . | quote }}
  {{- end }}
  concurrencyPolicy: Forbid
  suspend: {{ $job.suspend | default false }}
  {{- $history := $job.history | default dict }}
  successfulJobsHistoryLimit: {{ if hasKey $history "successful" }}{{ $history.successful }}{{ else }}1{{ end }}
  failedJobsHistoryLimit: {{ if hasKey $history "failed" }}{{ $history.failed }}{{ else }}1{{ end }}
  {{- with $job.startDeadline }}
  startingDeadlineSeconds: {{ . }}
  {{- end }}
  jobTemplate:
    spec:
      {{- include "mitis.service.jobSpec" (dict "root" $root "job" $job "name" $name "id" .id) | nindent 6 }}
  {{- else }}
  {{- include "mitis.service.jobSpec" (dict "root" $root "job" $job "name" $name "id" .id) | nindent 2 }}
  {{- end }}
{{- end -}}
