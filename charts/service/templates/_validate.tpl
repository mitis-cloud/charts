{{- define "mitis.service.validate" -}}
{{- $v := .Values -}}
{{- $name := include "mitis.service.name" . -}}
{{- if and (eq $v.workload "resources") (or $v.init $v.sidecars $v.ports $v.health $v.environment $v.files $v.mounts) -}}
{{- fail "resource-only services declare container inputs on their jobs, not at the root" -}}{{- end -}}
{{- if or (gt (len $name) 40) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $name)) -}}
{{- fail "service name/release name must be a DNS label of at most 40 characters" -}}{{- end -}}
{{- if and $v.availability (lt (int $v.capacity.instances) (int $v.availability.zones)) -}}
{{- fail "capacity.instances must cover availability.zones; omit availability when no zone constraint is wanted" -}}{{- end -}}
{{- if and $v.disruption.handover (or (eq $v.updates.strategy "replace") (eq $v.workload "resources")) -}}
{{- fail "disruption.handover requires an overlap-safe continuous workload" -}}{{- end -}}
{{- if eq $v.updates.strategy "canary" -}}
{{- if or (ne $v.render.routing "gateway") (not $v.http) (not $v.updates.smoke) (ne $v.workload "deployment") -}}
{{- fail "canary requires a continuous workload, gateway routing, http, and an explicit smoke job" -}}{{- end -}}
{{- if lt (int $v.capacity.instances) 1 -}}{{ fail "canary needs at least one instance" }}{{- end -}}
{{- end -}}
{{- if $v.http -}}
{{- if not (hasKey $v.ports ($v.http.port | default "http")) -}}{{ fail "http.port must identify a declared port" }}{{- end -}}
{{- if and (not $v.http.hostname) (not $v.domain) -}}{{ fail "http requires hostname or domain" }}{{- end -}}
{{- end -}}
{{- $containers := include "mitis.service.containers" . | fromJson -}}
{{- range $id, $c := $containers -}}
{{- $image := $v.image -}}{{ if hasKey $c "image" }}{{ $image = $c.image }}{{ end -}}
{{- $_ := include "mitis.service.image" $image -}}
{{- $paths := dict -}}
{{- range $volume, $mount := $c.mounts -}}
{{- if not (hasKey $v.storage $volume) -}}{{ fail (printf "%s.mounts refers to unknown storage %s" $id $volume) }}{{- end -}}
{{- $path := $mount -}}{{ if kindIs "map" $mount }}{{ $path = $mount.path }}{{ end -}}
{{- if and (kindIs "map" $mount) (has ".." (splitList "/" ($mount.subdirectory | default ""))) -}}{{ fail "mount subdirectory must not contain parent traversal" }}{{- end -}}
{{- if hasKey $paths $path -}}{{ fail (printf "%s has duplicate mount path %s" $id $path) }}{{- end -}}
{{- $_ := set $paths $path true -}}
{{- end -}}
{{- range $file, $f := $c.files -}}
{{- if hasKey $paths $f.path -}}{{ fail (printf "%s has duplicate mount/file path %s" $id $f.path) }}{{- end -}}
{{- $_ := set $paths $f.path true -}}
{{- end -}}
{{- range $probe, $p := $c.health -}}
{{- $port := $p.tcp | default ($p.port | default "http") -}}
{{- if and (kindIs "string" $port) (not (hasKey ($c.ports | default dict) $port)) -}}
{{- fail (printf "%s.health.%s refers to unknown named port %s" $id $probe $port) -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $inputCount := add (len $v.secrets) (len $v.storage) -}}
{{- range $id, $c := $containers -}}
{{- $inputCount = add $inputCount (len ($c.files | default dict)) -}}
{{- range $_, $value := $c.environment -}}
{{- if and (kindIs "map" $value) (hasKey $value "remoteSecret") -}}{{ $inputCount = add $inputCount 1 }}{{- end -}}
{{- end -}}
{{- end -}}
{{- range $id, $job := $v.jobs -}}
{{- if and (not $job.run) (not $job.schedule) -}}{{ fail (printf "jobs.%s requires run or schedule" $id) }}{{- end -}}
{{- if and (eq ($job.run | default "") "before") (eq $v.render.engine "helm") (eq $v.render.part "all") (gt $inputCount 0) -}}
{{- fail "Helm before-jobs with managed inputs require stable inputs and runtime releases; see the Helmfile guide" -}}{{- end -}}
{{- end -}}
{{- if eq $v.render.part "task" -}}
{{- $task := required "render.task is required for the task part" $v.render.task -}}
{{- if not (hasKey $v.jobs $task) -}}{{ fail "render.task must identify a declared manual job" }}{{- end -}}
{{- if ne (index $v.jobs $task).run "manual" -}}{{ fail "render.task must identify a manual job" }}{{- end -}}
{{- else if $v.render.task -}}{{ fail "render.task is only allowed with render.part=task" }}{{- end -}}
{{- end -}}
