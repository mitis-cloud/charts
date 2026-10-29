{{- define "mitis.service.validate" -}}
{{- $v := .Values -}}
{{- range $key, $value := $v.jobs -}}
{{- if eq (kindOf $value) "invalid" -}}{{ $_ := unset $v.jobs $key }}{{- end -}}
{{- end -}}
{{- range $key, $value := $v.storage -}}
{{- if eq (kindOf $value) "invalid" -}}{{ $_ := unset $v.storage $key }}{{- end -}}
{{- end -}}
{{- $processes := list $v -}}
{{- range $c := $v.init -}}{{ $processes = append $processes $c }}{{- end -}}
{{- range $c := $v.sidecars -}}{{ $processes = append $processes $c }}{{- end -}}
{{- range $job := $v.jobs -}}
{{- $processes = append $processes $job -}}
{{- range $c := $job.init -}}{{ $processes = append $processes $c }}{{- end -}}
{{- end -}}
{{- if $v.updates.smoke -}}{{ $processes = append $processes $v.updates.smoke }}{{- end -}}
{{- range $c := $processes -}}
{{- range $key, $value := $c.mounts -}}
{{- if eq (kindOf $value) "invalid" -}}{{ $_ := unset $c.mounts $key }}{{- end -}}
{{- end -}}
{{- range $key, $value := $c.files -}}
{{- if eq (kindOf $value) "invalid" -}}{{ $_ := unset $c.files $key }}{{- end -}}
{{- end -}}
{{- end -}}
{{- $name := include "mitis.service.name" . -}}
{{- $identities := dict -}}
{{- if and (eq $v.workload "resources") (or $v.init $v.sidecars $v.ports $v.health $v.environment $v.tokens $v.mounts (and $v.http (not $v.response)) $v.availability $v.command $v.args $v.identity $v.fileGroup $v.shutdown $v.workingDirectory $v.account $v.security $v.discovery) -}}
{{- fail "resource-only services declare container inputs on their jobs, not at the root" -}}{{- end -}}
{{- if or (gt (len $name) 40) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $name)) -}}
{{- fail "service name/release name must be a DNS label of at most 40 characters" -}}{{- end -}}
{{- if and $v.availability (lt (int $v.capacity.instances) (int $v.availability.zones)) -}}
{{- fail "capacity.instances must cover availability.zones; omit availability when no zone constraint is wanted" -}}{{- end -}}
{{- if and $v.disruption.surgeBeforeEviction (or (eq $v.updates.strategy "replace") (eq $v.workload "resources")) -}}
{{- fail "disruption.surgeBeforeEviction requires an overlap-safe continuous workload" -}}{{- end -}}
{{- if eq $v.updates.strategy "canary" -}}
{{- if or (ne $v.render.routing "gateway") (not $v.http) (not $v.updates.smoke) (ne $v.workload "deployment") -}}
{{- fail "canary requires a continuous workload, gateway routing, http, and an explicit smoke job" -}}{{- end -}}
{{- if lt (int $v.capacity.instances) 1 -}}{{ fail "canary needs at least one instance" }}{{- end -}}
{{- else if $v.updates.smoke -}}{{ fail "updates.smoke requires canary strategy" }}
{{- end -}}
{{- if $v.http -}}
{{- if and (not $v.response) (not (hasKey $v.ports ($v.http.port | default "http"))) -}}{{ fail "http.port must identify a declared port" }}{{- end -}}
{{- if and (not $v.http.hostname) (not $v.domain) -}}{{ fail "http requires hostname or domain" }}{{- end -}}
{{- if and (eq $v.render.routing "gateway") (or $v.http.healthcheck (hasKey $v.http "priority")) -}}{{ fail "http.healthcheck and http.priority require ingress routing; Gateway implementations own their health policy and route precedence" }}{{- end -}}
{{- end -}}
{{- if $v.response -}}
{{- if or (ne $v.workload "resources") (ne $v.render.routing "ingress") (not $v.http) -}}{{ fail "response requires workload=resources, http, and ingress routing" }}{{- end -}}
{{- if or $v.http.port $v.http.healthcheck -}}{{ fail "a fixed response has no backend port or healthcheck" }}{{- end -}}
{{- if gt (len $v.response.body) 1024 -}}{{ fail "response.body exceeds the ALB limit of 1024 bytes" }}{{- end -}}
{{- end -}}
{{- $containers := include "mitis.service.containers" . | fromJson -}}
{{- range $id, $c := $containers -}}
{{- if and $c.account $v.permissions -}}{{ fail "account consumes an existing service account and cannot be combined with chart-owned permissions" }}{{- end -}}
{{- if and $c.account $v.outputs -}}{{ fail "outputs are written with the chart-owned service account and cannot be combined with account" }}{{- end -}}
{{- range $key, $value := $c.environment -}}
{{- if and (kindIs "map" $value) (hasKey $value "output") (not (hasKey ($v.outputs | default dict) $value.output)) -}}{{ fail (printf "environment %s reads undeclared output %s" $key $value.output) }}{{- end -}}
{{- end -}}
{{- if and (eq ($c.security | default "image-defaults") "restricted") (hasKey ($c.identity | default dict) "user") (eq (int $c.identity.user) 0) -}}{{ fail "restricted security cannot run as user 0" }}{{- end -}}
{{- if gt (len $id) 63 -}}{{ fail (printf "container/helper identity %s exceeds 63 characters" $id) }}{{- end -}}
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
{{- if gt (len (printf "%s-file-%s" $id $file)) 63 -}}{{ fail "generated file volume names must fit in 63 characters" }}{{- end -}}
{{- if hasKey $paths $f.path -}}{{ fail (printf "%s has duplicate mount/file path %s" $id $f.path) }}{{- end -}}
{{- $_ := set $paths $f.path true -}}
{{- end -}}
{{- range $token, $t := $c.tokens -}}
{{- if gt (len (printf "%s-token-%s" $id $token)) 63 -}}{{ fail "generated token volume names must fit in 63 characters" }}{{- end -}}
{{- if or (has ".." (splitList "/" $t.path)) (ne (clean $t.path) $t.path) -}}{{ fail "token paths must be canonical absolute directories" }}{{- end -}}
{{- range $path, $_ := $paths -}}
{{- if or (eq $path $t.path) (hasPrefix (printf "%s/" $path) $t.path) (hasPrefix (printf "%s/" $t.path) $path) -}}{{ fail "token directories must not overlap another mount or file" }}{{- end -}}
{{- end -}}
{{- $_ := set $paths $t.path true -}}
{{- end -}}
{{- range $probe, $p := $c.health -}}
{{- $port := $p.tcp | default ($p.port | default "http") -}}
{{- if and (kindIs "string" $port) (not (hasKey ($c.ports | default dict) $port)) -}}
{{- fail (printf "%s.health.%s refers to unknown named port %s" $id $probe $port) -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $inputs := include "mitis.service.inputs" . | fromJson -}}
{{- range $id, $_ := $inputs.secrets -}}
{{- include "mitis.service.reserve" (dict "identities" $identities "kind" "Secret" "name" $id) -}}
{{- end -}}
{{- range $id, $_ := $inputs.configs -}}
{{- include "mitis.service.reserve" (dict "identities" $identities "kind" "ConfigMap" "name" $id) -}}
{{- end -}}
{{- range $id, $volume := $v.storage -}}
{{- if eq ($volume.access | default "") "shared" -}}
{{- include "mitis.service.reserve" (dict "identities" $identities "kind" "PersistentVolumeClaim" "name" ($volume.name | default (printf "%s-%s" $name $id))) -}}
{{- end -}}
{{- end -}}
{{- $inputCount := add (len $inputs.secrets) (len $inputs.configs) (len $v.storage) -}}
{{- range $id, $job := $v.jobs -}}
{{- if and $job.resultLifetime (or $job.schedule (regexMatch "^[1-9][0-9]*(s|m|h)$" ($job.cleanup | default ""))) -}}{{ fail "resultLifetime requires a release job with an immediate or next-run cleanup policy" }}{{- end -}}
{{- include "mitis.service.reserve" (dict "identities" $identities "kind" "Task" "name" ($job.name | default (printf "%s-%s" $name $id))) -}}
{{- if and (not $job.run) (not $job.schedule) -}}{{ fail (printf "jobs.%s requires run or schedule" $id) }}{{- end -}}
{{- if and $job.schedule (or (hasKey $job "order") (hasKey $job "cleanup")) -}}{{ fail "scheduled jobs do not accept release order or cleanup; use history for result retention" }}{{- end -}}
{{- if and (not $job.schedule) (or (hasKey $job "history") (hasKey $job "startDeadline") (hasKey $job "suspend") (hasKey $job "timeZone")) -}}{{ fail "history, startDeadline, suspend, and timeZone require a scheduled job" }}{{- end -}}
{{- if and (eq ($job.run | default "") "before") (eq $v.render.engine "helm") (has $v.render.part (list "all" "application")) (gt $inputCount 0) -}}
{{- fail "Helm before-jobs with managed inputs require stable inputs and runtime releases; see the Helmfile guide" -}}{{- end -}}
{{- end -}}
{{- if and $v.updates.smoke (eq ($v.updates.smoke.timeout | default "") "none") -}}{{ fail "canary smoke requires a bounded timeout" }}{{- end -}}

{{- if or (eq $v.render.part "task") (and (eq $v.render.part "all") $v.render.task) -}}
{{- $task := required "render.task is required for the task part" $v.render.task -}}
{{- if not (hasKey $v.jobs $task) -}}{{ fail "render.task must identify a declared manual job" }}{{- end -}}
{{- if ne (index $v.jobs $task).run "manual" -}}{{ fail "render.task must identify a manual job" }}{{- end -}}
{{- else if $v.render.task -}}{{ fail "render.task is only allowed with render.part=all or task" }}{{- end -}}
{{- end -}}

{{- define "mitis.service.reserve" -}}
{{- if gt (len .name) 63 -}}{{ fail (printf "generated %s name %s exceeds 63 characters; shorten its logical identity" .kind .name) }}{{- end -}}
{{- $key := printf "%s/%s" .kind .name -}}
{{- if hasKey .identities $key -}}{{ fail (printf "duplicate resource identity %s" $key) }}{{- end -}}
{{- $_ := set .identities $key true -}}
{{- end -}}
