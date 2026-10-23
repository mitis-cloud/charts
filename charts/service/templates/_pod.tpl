{{- define "mitis.service.pod" -}}
{{- $root := .root -}}
{{- $c := .container -}}
{{- $init := .init | default dict -}}
{{- $sidecars := .sidecars | default dict -}}
{{- $containers := dict .id $c -}}
{{- range $key, $value := $init -}}{{ $_ := set $containers (printf "%sinit-%s" $.prefix $key) $value }}{{- end -}}
{{- range $key, $value := $sidecars -}}{{ $_ := set $containers (printf "sidecar-%s" $key) $value }}{{- end -}}
{{- $volumes := dict -}}
{{- range $id, $container := $containers -}}
{{- range $name, $_ := $container.mounts -}}{{ $_ := set $volumes $name (index $root.Values.storage $name) }}{{- end -}}
{{- end -}}
restartPolicy: {{ .restart }}
enableServiceLinks: {{ eq ($c.discovery | default "dns") "environment" }}
{{- if hasKey $c "apiToken" }}
automountServiceAccountToken: {{ $c.apiToken }}
{{- end }}
terminationGracePeriodSeconds: {{ $c.shutdown | default 30 }}
{{- if or (hasKey $c "fileGroup") (eq ($c.security | default "image-defaults") "restricted") }}
securityContext:
  {{- if hasKey $c "fileGroup" }}
  fsGroup: {{ $c.fileGroup }}
  {{- end }}
  {{- if eq ($c.security | default "image-defaults") "restricted" }}
  seccompProfile: {type: RuntimeDefault}
  {{- end }}
{{- end }}
{{- if $c.account }}
serviceAccountName: {{ $c.account }}
{{- else if include "mitis.service.ownsAccount" $root }}
serviceAccountName: {{ include "mitis.service.name" $root }}
{{- end }}
{{- if or (ne $root.Values.placement "any") $root.Values.architecture }}
nodeSelector:
  {{- if $root.Values.architecture }}
  kubernetes.io/arch: {{ $root.Values.architecture }}
  {{- end }}
  {{- if ne $root.Values.placement "any" }}
  env: {{ $root.Values.placement }}
  {{- end }}
{{- end }}
{{- if ne $root.Values.placement "any" }}
tolerations:
  - key: env
    operator: Equal
    value: {{ $root.Values.placement }}
    effect: NoSchedule
{{- end }}
{{- if and (eq .id "main") $root.Values.availability }}
topologySpreadConstraints:
  - maxSkew: 1
    minDomains: {{ $root.Values.availability.zones }}
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        {{- include "mitis.service.selector" $root | nindent 8 }}
    matchLabelKeys:
      - {{ ternary "rollouts-pod-template-hash" "pod-template-hash" (eq $root.Values.updates.strategy "canary") }}
{{- end }}
{{- if $init }}
initContainers:
  {{- range $id, $container := $init }}
  - {{ include "mitis.service.container" (dict "root" $root "container" $container "name" $id "id" (printf "%sinit-%s" $.prefix $id)) | nindent 4 | trim }}
  {{- end }}
{{- end }}
containers:
  - {{ include "mitis.service.container" (dict "root" $root "container" $c "name" (.name | default "main") "id" .id) | nindent 4 | trim }}
  {{- range $id, $container := $sidecars }}
  - {{ include "mitis.service.container" (dict "root" $root "container" $container "name" $id "id" (printf "sidecar-%s" $id)) | nindent 4 | trim }}
  {{- end }}
{{- $files := false -}}
{{- range $id, $container := $containers -}}{{ if or $container.files $container.tokens }}{{ $files = true }}{{ end }}{{- end }}
{{- if or $volumes $files }}
volumes:
  {{- range $name, $volume := $volumes }}
  - name: {{ $name }}
    {{- if $volume.temporary }}
    emptyDir:
      {{- if eq ($volume.medium | default "disk") "memory" }}
      medium: Memory
      {{- end }}
      {{- with $volume.sizeLimit }}
      sizeLimit: {{ . | quote }}
      {{- end }}
      {{- if not (or $volume.sizeLimit (eq ($volume.medium | default "disk") "memory")) }}
      {}
      {{- end }}
    {{- else }}
    persistentVolumeClaim:
      claimName: {{ $volume.existing | default ($volume.name | default (printf "%s-%s" (include "mitis.service.name" $root) $name)) }}
    {{- end }}
  {{- end }}
  {{- range $id, $container := $containers }}
  {{- range $name, $token := $container.tokens }}
  - name: {{ $id }}-token-{{ $name }}
    projected:
      defaultMode: 420
      sources:
        - serviceAccountToken:
            audience: {{ $token.audience | quote }}
            expirationSeconds: {{ $token.lifetime | default 3600 }}
            path: token
  {{- end }}
  {{- range $name, $file := $container.files }}
  {{- $source := include "mitis.service.file" (dict "root" $root "file" $file "id" $id "key" $name) | fromJson }}
  - name: {{ $id }}-file-{{ $name }}
    {{- if eq $source.kind "Secret" }}
    secret:
      secretName: {{ $source.name }}
    {{- else }}
    configMap:
      name: {{ $source.name }}
    {{- end }}
      defaultMode: {{ if hasKey $file "mode" }}{{ $file.mode }}{{ else }}420{{ end }}
      items:
        - key: {{ $source.key | quote }}
          path: {{ $source.key | quote }}
  {{- end }}
  {{- end }}
{{- end }}
{{- end -}}
