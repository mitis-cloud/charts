{{- define "mitis.service.container" -}}
{{- $root := .root -}}
{{- $c := .container -}}
{{- $identity := include "mitis.service.containerName" (dict "root" $root "id" .id) -}}
{{- $image := $root.Values.image -}}
{{- if hasKey $c "image" -}}{{ $image = $c.image }}{{- end -}}
name: {{ .name }}
image: {{ include "mitis.service.image" $image | quote }}
imagePullPolicy: {{ $image.pullPolicy | default "IfNotPresent" }}
{{- with $c.command }}
command: {{ toJson . }}
{{- end }}
{{- with $c.args }}
args: {{ toJson . }}
{{- end }}
{{- with $c.workingDirectory }}
workingDir: {{ . | quote }}
{{- end }}
{{- $capacity := $c.capacity | default dict }}
{{- $resources := dict -}}
{{- $requests := dict -}}
{{- $limits := dict -}}
{{- range $key := list "cpu" "memory" -}}
{{- if ne (index $capacity $key) nil -}}{{ $_ := set $requests $key (toString (index $capacity $key)) }}{{- end -}}
{{- if ne (index $capacity (printf "%sLimit" $key)) nil -}}{{ $_ := set $limits $key (toString (index $capacity (printf "%sLimit" $key))) }}{{- end -}}
{{- end -}}
{{- if $requests -}}{{ $_ := set $resources "requests" $requests }}{{- end -}}
{{- if $limits -}}{{ $_ := set $resources "limits" $limits }}{{- end }}
resources: {{ toJson $resources }}
{{- with $c.ports }}
ports:
  {{- range $name, $port := . }}
  - name: {{ $name }}
    containerPort: {{ if kindIs "map" $port }}{{ $port.target }}{{ else }}{{ $port }}{{ end }}
    protocol: TCP
  {{- end }}
{{- end }}
{{- $environment := dict -}}
{{- range $key, $value := $c.environment -}}
{{- if ne $value nil -}}{{ $_ := set $environment $key $value }}{{- end -}}
{{- end -}}
{{- with $environment }}
env:
  {{- range $key, $value := . }}
  {{- if ne $value nil }}
  - name: {{ $key | quote }}
    {{- if and (kindIs "map" $value) (hasKey $value "secret") }}
    valueFrom:
      secretKeyRef:
        name: {{ $value.secret.name }}
        key: {{ $value.secret.key }}
    {{- else if and (kindIs "map" $value) (hasKey $value "instance") }}
    valueFrom:
      fieldRef:
        fieldPath: {{ index (dict "name" "metadata.name" "namespace" "metadata.namespace" "ip" "status.podIP") $value.instance }}
    {{- else }}
    {{- $input := include "mitis.service.environmentInput" (dict "root" $root "value" $value) | fromJson }}
    {{- if $input.remotes }}
    valueFrom:
      secretKeyRef:
        name: {{ $input.name | default ($c.inputSecret | default (printf "%s-secrets" $identity)) }}
        key: {{ $input.key | default $key }}
    {{- else }}
    value: {{ $input.literal | toString | quote }}
    {{- end }}
    {{- end }}
    {{- end }}
  {{- end }}
{{- end }}
{{- range $kind, $probe := $c.health }}
{{ $kind }}Probe:
  {{- if hasKey $probe "http" }}
  httpGet:
    path: {{ $probe.http | quote }}
    port: {{ $probe.port | default "http" }}
    {{- with $probe.headers }}
    httpHeaders:
      {{- range $key, $value := . }}
      - name: {{ $key | quote }}
        value: {{ $value | quote }}
      {{- end }}
    {{- end }}
  {{- else }}
  tcpSocket:
    port: {{ $probe.tcp }}
  {{- end }}
  initialDelaySeconds: {{ $probe.delay | default 0 }}
  periodSeconds: {{ $probe.interval | default 10 }}
  timeoutSeconds: {{ $probe.timeout | default 2 }}
  failureThreshold: {{ $probe.failures | default (ternary 30 3 (eq $kind "startup")) }}
{{- end }}
{{- if or $c.mounts $c.files $c.tokens }}
volumeMounts:
  {{- range $name, $mount := $c.mounts }}
  - name: {{ $name }}
    {{- if kindIs "string" $mount }}
    mountPath: {{ $mount | quote }}
    {{- else }}
    mountPath: {{ $mount.path | quote }}
    readOnly: {{ $mount.readOnly | default false }}
    {{- with $mount.subdirectory }}
    subPath: {{ . | quote }}
    {{- end }}
    {{- end }}
  {{- end }}
  {{- range $name, $file := $c.files }}
  {{- $source := include "mitis.service.file" (dict "root" $root "file" $file "id" $.id "key" $name) | fromJson }}
  - name: {{ $.id }}-file-{{ $name }}
    mountPath: {{ $file.path | quote }}
    subPath: {{ $source.key | quote }}
    readOnly: true
  {{- end }}
  {{- range $name, $token := $c.tokens }}
  - name: {{ $.id }}-token-{{ $name }}
    mountPath: {{ $token.path | quote }}
    readOnly: true
  {{- end }}
{{- end }}
{{- if or $c.identity (eq ($c.security | default "image-defaults") "restricted") }}
securityContext:
  {{- with $c.identity }}
  {{- if hasKey . "user" }}
  runAsUser: {{ .user }}
  {{- end }}
  {{- if hasKey . "group" }}
  runAsGroup: {{ .group }}
  {{- end }}
  {{- end }}
  {{- if eq ($c.security | default "image-defaults") "restricted" }}
  runAsNonRoot: true
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
  seccompProfile: {type: RuntimeDefault}
  {{- end }}
{{- end }}
{{- end -}}
