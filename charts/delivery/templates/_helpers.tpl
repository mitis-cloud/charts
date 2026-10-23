{{- define "mitis.delivery.validate" -}}
{{- $root := . -}}
{{- $owners := dict -}}
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
{{- range $unitName := $env.units | default (keys $root.Values.releaseUnits) -}}
{{- if not (hasKey $root.Values.releaseUnits $unitName) -}}{{ fail (printf "%s references unknown release unit %s" $envName $unitName) }}{{- end -}}
{{- if gt (len (printf "%s-%s-%s" $root.Values.project $unitName $envName)) 63 -}}{{ fail "project-unit-environment names must fit in 63 characters" }}{{- end -}}
{{- range $name, $service := (index $root.Values.releaseUnits $unitName).services -}}
{{- if not (hasKey $env.charts $service.chart) -}}{{ fail (printf "environment %s is missing chart pin %s" $envName $service.chart) }}{{- end -}}
{{- end -}}
{{- range $upstream := $env.upstream -}}
{{- if not (hasKey $root.Values.environments $upstream) -}}{{ fail (printf "unknown upstream environment %s" $upstream) }}{{- end -}}
{{- if eq $upstream $envName -}}{{ fail "an environment cannot promote from itself" }}{{- end -}}
{{- $source := index $root.Values.environments $upstream -}}
{{- if not (has $unitName ($source.units | default (keys $root.Values.releaseUnits))) -}}{{ fail (printf "%s's upstream %s does not deploy unit %s" $envName $upstream $unitName) }}{{- end -}}
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
