{{- define "mitis.service.input" -}}
{{- $v := .value -}}
{{- $root := .root -}}
{{- $remotes := dict -}}
{{- $literal := $v -}}
{{- $expr := mustToJson $v -}}
{{- if eq $v nil -}}{{ $expr = "(index (dict) \"null\")" }}{{- end -}}
{{- if kindIs "string" $v -}}
{{- $expr = printf "%q" $v -}}
{{- else if kindIs "map" $v -}}
{{- if hasKey $v "ssm" -}}
{{- if ne (len $v) 1 -}}{{ fail "ssm inputs accept only the ssm reference" }}{{- end -}}
{{- $remote := $v.ssm -}}
{{- if kindIs "string" $remote -}}{{ $remote = dict "key" $remote }}{{- end -}}
{{- $key := required "ssm.key must not be empty" $remote.key -}}
{{- if not ($remote.absolute | default false) -}}{{ $key = printf "%s%s" ($root.Values.secretPrefix | default "") $key }}{{- end -}}
{{- $ref := dict "key" $key -}}
{{- with $remote.property -}}{{ $_ := set $ref "property" . }}{{- end -}}
{{- $alias := printf "r%s" (sha256sum (mustToJson $ref)) -}}
{{- $_ := set $remotes $alias $ref -}}
{{- $expr = printf ".%s" $alias -}}
{{- $literal = "" -}}
{{- else if hasKey $v "fact" -}}
{{- if ne (len $v) 1 -}}{{ fail "fact inputs accept only the fact name" }}{{- end -}}
{{- if not (hasKey ($root.Values.facts | default dict) $v.fact) -}}{{ fail (printf "environment fact %s is missing" $v.fact) }}{{- end -}}
{{- $literal = index $root.Values.facts $v.fact -}}
{{- $input := include "mitis.service.input" (dict "root" $root "value" $literal) | fromJson -}}
{{- $expr = $input.expression -}}
{{- else if hasKey $v "format" -}}
{{- if ne (len $v) 1 -}}{{ fail "format inputs accept only the format string" }}{{- end -}}
{{- $text := $v.format -}}
{{- $parts := list -}}
{{- $literal = "" -}}
{{- range $token := regexFindAll "\\{[^{}]*\\}" $text -1 -}}
{{- $before := first (splitList $token $text) -}}
{{- $text = trimPrefix (printf "%s%s" $before $token) $text -}}
{{- if regexMatch "[{}]" $before -}}{{ fail "format contains an unmatched brace" }}{{- end -}}
{{- $parts = append $parts (printf "%q" $before) -}}
{{- $literal = printf "%s%s" $literal $before -}}
{{- if not (regexMatch "^\\{(ssm|fact):[A-Za-z0-9_./-]+(\\|urlquery)?\\}$" $token) -}}{{ fail (printf "unsupported format reference %s" $token) }}{{- end -}}
{{- $body := trimSuffix "}" (trimPrefix "{" $token) -}}
{{- $encoded := hasSuffix "|urlquery" $body -}}
{{- $body = trimSuffix "|urlquery" $body -}}
{{- $source := first (splitList ":" $body) -}}
{{- $key := trimPrefix (printf "%s:" $source) $body -}}
{{- $input := include "mitis.service.input" (dict "root" $root "value" (dict $source $key)) | fromJson -}}
{{- $_ := merge $remotes $input.remotes -}}
{{- $part := printf "(printf \"%%v\" %s)" $input.expression -}}
{{- $local := toString $input.literal -}}
{{- if $encoded -}}{{ $part = printf "(urlquery %s)" $part }}{{ $local = urlquery $local }}{{- end -}}
{{- $parts = append $parts $part -}}
{{- $literal = printf "%s%s" $literal $local -}}
{{- end -}}
{{- if regexMatch "[{}]" $text -}}{{ fail "format contains an unmatched brace" }}{{- end -}}
{{- $parts = append $parts (printf "%q" $text) -}}
{{- $literal = printf "%s%s" $literal $text -}}
{{- $expr = printf "(printf %q %s)" (repeat (len $parts) "%s") (join " " $parts) -}}
{{- else -}}
{{- $literal = dict -}}
{{- $parts := list -}}
{{- range $key, $value := $v -}}
{{- $input := include "mitis.service.input" (dict "root" $root "value" $value) | fromJson -}}
{{- $_ := merge $remotes $input.remotes -}}
{{- $_ := set $literal $key $input.literal -}}
{{- $parts = append $parts (printf "%q %s" $key $input.expression) -}}
{{- end -}}
{{- $expr = printf "(dict %s)" (join " " $parts) -}}
{{- end -}}
{{- else if kindIs "slice" $v -}}
{{- $literal = list -}}
{{- $parts := list -}}
{{- range $value := $v -}}
{{- $input := include "mitis.service.input" (dict "root" $root "value" $value) | fromJson -}}
{{- $_ := merge $remotes $input.remotes -}}
{{- $literal = append $literal $input.literal -}}
{{- $parts = append $parts $input.expression -}}
{{- end -}}
{{- $expr = printf "(list %s)" (join " " $parts) -}}
{{- end -}}
{{- mustToJson (dict "expression" $expr "literal" $literal "remotes" $remotes) -}}
{{- end -}}

{{- define "mitis.service.environmentInput" -}}
{{- $value := .value -}}
{{- $key := "" -}}
{{- $name := "" -}}
{{- if kindIs "map" $value -}}{{ $key = $value.key | default "" }}{{ $name = $value.name | default "" }}{{ $value = omit $value "key" "name" }}{{- end -}}
{{- if and (kindIs "map" $value) (hasKey $value "value") -}}{{ $value = $value.value }}{{- end -}}
{{- if and (kindIs "map" $value) (or (hasKey $value "url") (hasKey $value "endpoint")) -}}
{{- $value = include "mitis.service.value" . -}}
{{- end -}}
{{- $input := include "mitis.service.input" (dict "root" .root "value" $value) | fromJson -}}
{{- if and (or $key $name) (not $input.remotes) (not .generateAll) -}}{{ fail "an explicit input identity requires a remote-backed value" }}{{- end -}}
{{- $_ := set $input "key" $key -}}
{{- $_ := set $input "name" $name -}}
{{- mustToJson $input -}}
{{- end -}}

{{- define "mitis.service.inputContainers" -}}
{{- $items := include "mitis.service.containers" . | fromJson -}}
{{- if and (eq .Values.workload "resources") .Values.files -}}{{ $_ := set $items "main" (dict "files" .Values.files) }}{{- end -}}
{{- mustToJson $items -}}
{{- end -}}

{{- define "mitis.service.file" -}}
{{- $root := .root -}}
{{- $file := .file -}}
{{- $id := .id -}}
{{- $key := .key -}}
{{- $consumer := $file -}}
{{- if $file.from -}}
{{- $matches := list -}}
{{- range $sourceID, $c := (include "mitis.service.inputContainers" $root | fromJson) -}}
{{- range $sourceKey, $source := $c.files -}}
{{- if and $source (eq $sourceKey $file.from) (not $source.from) -}}{{ $matches = append $matches (dict "id" $sourceID "key" $sourceKey "file" $source) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- if ne (len $matches) 1 -}}{{ fail (printf "file.from %s must identify exactly one declared file" $file.from) }}{{- end -}}
{{- $source := first $matches -}}
{{- $file = $source.file -}}{{ $id = $source.id -}}{{ $key = $source.key -}}
{{- end -}}
{{- $input := dict "literal" "" "remotes" dict -}}
{{- $kind := "ConfigMap" -}}
{{- $name := $file.name | default (printf "%s-%s-file-%s" (include "mitis.service.name" $root) $id $key) -}}
{{- $dataKey := $file.key | default $key -}}
{{- if $file.secret -}}
{{- $kind = "Secret" -}}{{ $name = $file.secret.name -}}{{ $dataKey = $file.secret.key -}}
{{- else -}}
{{- $value := $file.content -}}
{{- $serialize := "" -}}
{{- range $type := list "ssm" "format" "yaml" "json" -}}
{{- if hasKey $file $type -}}
{{- if has $type (list "yaml" "json") -}}{{ $value = index $file $type }}{{ $serialize = $type }}
{{- else -}}{{ $value = dict $type (index $file $type) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- $input = include "mitis.service.input" (dict "root" $root "value" $value) | fromJson -}}
{{- if eq $serialize "json" -}}
{{- $_ := set $input "expression" (printf "(toJson %s)" $input.expression) -}}
{{- $_ := set $input "literal" (mustToJson $input.literal) -}}
{{- else if eq $serialize "yaml" -}}
{{- $_ := set $input "expression" (printf "(toYaml %s)" $input.expression) -}}
{{- $_ := set $input "literal" (printf "%s\n" (toYaml $input.literal)) -}}
{{- end -}}
{{- if $input.remotes -}}{{ $kind = "Secret" }}{{- end -}}
{{- end -}}
{{- mustToJson (dict "kind" $kind "name" $name "key" $dataKey "input" $input "generated" (not $file.secret) "producer" (not $consumer.from)) -}}
{{- end -}}

{{- define "mitis.service.addInput" -}}
{{- $target := index .targets .name | default (dict "data" dict "remotes" dict) -}}
{{- if and (hasKey $target.data .key) (ne (index $target.data .key) .body) -}}{{ fail (printf "conflicting input data for %s/%s" .name .key) }}{{- end -}}
{{- $_ := set $target.data .key .body -}}
{{- $_ := merge $target.remotes (.remotes | default dict) -}}
{{- $_ := set .targets .name $target -}}
{{- end -}}

{{- define "mitis.service.inputs" -}}
{{- include "mitis.service.processInputs" (dict "root" . "containers" (include "mitis.service.inputContainers" . | fromJson)) -}}
{{- end -}}

{{- define "mitis.service.processInputs" -}}
{{- $root := .root -}}
{{- $generateAll := .generateAll | default false -}}
{{- $secrets := dict -}}{{- $configs := dict -}}
{{- range $id, $c := .containers -}}
{{- $name := $c.inputSecret | default (printf "%s-secrets" (include "mitis.service.containerName" (dict "root" $root "id" $id))) -}}
{{- range $key, $value := $c.environment -}}
{{- if and (ne $value nil) (not (and (kindIs "map" $value) (or (hasKey $value "secret") (hasKey $value "instance") (hasKey $value "output")))) -}}
{{- $input := include "mitis.service.environmentInput" (dict "root" $root "value" $value "generateAll" $generateAll) | fromJson -}}
{{- if or $input.remotes $generateAll -}}{{ include "mitis.service.addInput" (dict "targets" $secrets "name" ($input.name | default $name) "key" ($input.key | default $key) "body" (printf "{{ %s }}" $input.expression) "remotes" $input.remotes) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- range $key, $file := $c.files -}}
{{- if $file -}}
{{- $source := include "mitis.service.file" (dict "root" $root "file" $file "id" $id "key" $key) | fromJson -}}
{{- if and $source.generated $source.producer -}}
{{- $input := $source.input -}}
{{- if eq $source.kind "Secret" -}}
{{- include "mitis.service.addInput" (dict "targets" $secrets "name" $source.name "key" $source.key "body" (printf "{{ %s }}" $input.expression) "remotes" $input.remotes) -}}
{{- else -}}
{{- include "mitis.service.addInput" (dict "targets" $configs "name" $source.name "key" $source.key "body" (toString $input.literal)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- mustToJson (dict "secrets" $secrets "configs" $configs) -}}
{{- end -}}

{{- define "mitis.service.inputResources" -}}
{{- $root := .root -}}
{{- range $id, $config := .inputs.configs }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $id }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" -20) | nindent 4 }}
data:
  {{- toYaml $config.data | nindent 2 }}
{{- end -}}
{{- range $id, $s := .inputs.secrets -}}
{{- if not $s.remotes -}}{{ fail (printf "generated Secret %s needs at least one remote-backed input" $id) }}{{- end }}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ $id }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" -20) | nindent 4 }}
spec:
  refreshInterval: {{ $root.Values.secretRefresh }}
  secretStoreRef:
    kind: ClusterSecretStore
    name: {{ $root.Values.secretStore }}
  target:
    name: {{ $id }}
    creationPolicy: Owner
    template:
      engineVersion: v2
      mergePolicy: Replace
      data:
        {{- toYaml $s.data | nindent 8 }}
  data:
    {{- range $key, $remote := $s.remotes }}
    - secretKey: {{ $key | quote }}
      remoteRef:
        {{- toYaml $remote | nindent 8 }}
    {{- end }}
{{- end -}}
{{- end -}}
