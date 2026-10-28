{{- define "mitis.delivery.validate" -}}
{{- $root := . -}}
{{- $owners := dict -}}
{{- $filters := dict -}}
{{- range $unitName, $unit := .Values.releaseUnits -}}
{{- $filter := $unit.allowTags | default $root.Values.discovery.allowTags -}}
{{- range $service := $unit.services -}}{{- range $image := $service.images -}}
{{- if and (hasKey $filters $image) (ne (index $filters $image) $filter) -}}{{ fail (printf "image %s is discovered with two different tag filters" $image) }}{{- end -}}
{{- $_ := set $filters $image $filter -}}
{{- end -}}{{- end -}}
{{- end -}}
{{- range $unitName, $unit := .Values.releaseUnits -}}
{{- range $name, $service := $unit.services -}}
{{- if hasKey $owners $name -}}{{ fail (printf "service %s belongs to more than one release unit" $name) }}{{- end -}}
{{- $_ := set $owners $name $unitName -}}
{{- if not (hasKey $root.Values.charts $service.chart) -}}{{ fail (printf "service %s references unknown chart %s" $name $service.chart) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- range $envName, $env := .Values.environments -}}
{{- if and (hasKey $env "environment") (not (hasKey $env.charts "environment")) -}}{{ fail (printf "environment %s is missing chart pin environment" $envName) }}{{- end -}}
{{- if and (hasKey $env "environment") (not (hasKey $root.Values.charts "environment")) -}}{{ fail "environments with environment values need the environment chart" }}{{- end -}}
{{- range $unitName, $services := $env.units -}}
{{- if not (hasKey $root.Values.releaseUnits $unitName) -}}{{ fail (printf "%s references unknown release unit %s" $envName $unitName) }}{{- end -}}
{{- if gt (len (printf "%s-%s-%s" $root.Values.project $unitName $envName)) 63 -}}{{ fail "project-unit-environment names must fit in 63 characters" }}{{- end -}}
{{- $unit := index $root.Values.releaseUnits $unitName -}}
{{- range $name := $services -}}
{{- if not (hasKey $unit.services $name) -}}{{ fail (printf "environment %s selects unknown service %s in unit %s" $envName $name $unitName) }}{{- end -}}
{{- $service := index $unit.services $name -}}
{{- if not (hasKey $env.charts $service.chart) -}}{{ fail (printf "environment %s is missing chart pin %s" $envName $service.chart) }}{{- end -}}
{{- end -}}
{{- range $upstream := $env.upstream -}}
{{- if not (hasKey $root.Values.environments $upstream) -}}{{ fail (printf "unknown upstream environment %s" $upstream) }}{{- end -}}
{{- if eq $upstream $envName -}}{{ fail "an environment cannot promote from itself" }}{{- end -}}
{{- $source := index $root.Values.environments $upstream -}}
{{- if not (hasKey $source.units $unitName) -}}{{ fail (printf "%s's upstream %s does not deploy unit %s" $envName $upstream $unitName) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- $seen := dict $envName true -}}
{{- $pending := $env.upstream | default list -}}
{{- range $_ := until (len $root.Values.environments) -}}
{{- $next := list -}}
{{- range $ancestor := $pending -}}
{{- if eq $ancestor $envName -}}{{ fail (printf "promotion cycle involving %s" $envName) }}{{- end -}}
{{- if not (hasKey $seen $ancestor) -}}
{{- $_ := set $seen $ancestor true -}}
{{- $next = concat $next ((index $root.Values.environments $ancestor).upstream | default list) -}}
{{- end -}}
{{- end -}}
{{- $pending = $next -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* A unit is promoted by Kargo when any of its services declares an image; otherwise it is configuration that syncs automatically. */}}
{{- define "mitis.delivery.promoted" -}}
{{- range $service := .services -}}{{- if $service.images -}}true{{- end -}}{{- end -}}
{{- end -}}

{{- /* Release units an environment deploys, in name order. */}}
{{- define "mitis.delivery.units" -}}
{{- toJson (keys .env.units | sortAlpha) -}}
{{- end -}}

{{- /* Resolve membership once for Argo sources and Kargo promotion steps. */}}
{{- define "mitis.delivery.services" -}}
{{- $services := dict -}}
{{- $unit := index .root.Values.releaseUnits .unit -}}
{{- range $name := index .env.units .unit -}}
{{- $_ := set $services $name (index $unit.services $name) -}}
{{- end -}}
{{- toJson $services -}}
{{- end -}}

{{- /* Each promoted unit gets its own Freight slot in the environment's Stage. */}}
{{- define "mitis.delivery.promotedUnits" -}}
{{- $root := .root -}}
{{- $out := list -}}
{{- range $unitName := include "mitis.delivery.units" . | fromJsonArray -}}
{{- if include "mitis.delivery.promoted" (index $root.Values.releaseUnits $unitName) -}}{{ $out = append $out $unitName }}{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{- /* Renders a nested map whose leaves are Kargo expressions as an expr-lang map literal. */}}
{{- define "mitis.delivery.exprMap" -}}
{{- $parts := list -}}
{{- range $key := keys . | sortAlpha -}}
{{- $value := index $ $key -}}
{{- if kindIs "map" $value -}}
{{- $parts = append $parts (printf "%q: %s" $key (include "mitis.delivery.exprMap" $value)) -}}
{{- else -}}
{{- $parts = append $parts (printf "%q: %s" $key $value) -}}
{{- end -}}
{{- end -}}
{{- printf "{%s}" (join ", " $parts) -}}
{{- end -}}
{{- define "mitis.delivery.imageExpression" -}}
{{- $image := printf "imageFrom(%q, warehouse(%q))" .repository .unit -}}
{{- if hasSuffix "tag" .path -}}
{{- printf "%s.Tag + \"@\" + %s.Digest" $image $image -}}
{{- else -}}
{{- printf "%s.Digest" $image -}}
{{- end -}}
{{- end -}}
