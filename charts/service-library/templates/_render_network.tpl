{{- /* One external route to the service: the primary http entry or an http.also entry, sharing the backend port and health check. */ -}}
{{- define "mitis.service.route" -}}
{{- $root := .root -}}
{{- $service := include "mitis.service.name" $root -}}
{{- $name := .name -}}
{{- $route := .route -}}
{{- $portName := .portName -}}
{{- $listen := .listen -}}
{{- $canary := not (empty (include "mitis.service.rollout" $root)) -}}
{{- $subdomain := .subdomain | default "" -}}
{{- if hasKey $route "subdomain" -}}{{ $subdomain = $route.subdomain }}{{- end -}}
{{- $hostname := include "mitis.service.value" (dict "root" $root "value" (dict "url" (dict "hostname" ($route.hostname | default "") "subdomain" $subdomain))) | trimPrefix "https://" -}}
{{- $hosts := prepend ($route.aliases | default list) $hostname -}}
{{- if eq $root.Values.render.routing "gateway" -}}
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {{ $name }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" 0) | nindent 4 }}
spec:
  parentRefs:
    - name: {{ $route.exposure }}
      sectionName: https
  hostnames: {{ toJson $hosts }}
  rules:
    - matches:
        {{- range $route.paths | default (list "/") }}
        - path: {type: PathPrefix, value: {{ . | quote }}}
        {{- end }}
      backendRefs:
        - name: {{ $service }}
          port: {{ $listen }}
          weight: 100
        {{- if $canary }}
        - name: {{ $service }}-candidate
          port: {{ $listen }}
          weight: 0
        {{- end }}
{{- if $route.redirectFromHttp }}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {{ $name }}-http
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" 0) | nindent 4 }}
spec:
  parentRefs:
    - name: {{ $route.exposure }}
      sectionName: http
  hostnames: {{ toJson $hosts }}
  rules:
    - filters:
        - type: RequestRedirect
          requestRedirect:
            scheme: https
            statusCode: 301
{{- end }}
{{- else -}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ $name }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" 0) | nindent 4 }}
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: {{ ternary "[{\"HTTP\":80},{\"HTTPS\":443}]" "[{\"HTTPS\":443}]" ($route.redirectFromHttp | default false) | quote }}
    alb.ingress.kubernetes.io/ssl-redirect: '443'
    {{- if hasKey $route "priority" }}
    alb.ingress.kubernetes.io/group.order: {{ $route.priority | quote }}
    {{- end }}
    {{- with $root.Values.http.healthcheck }}
    {{- with .path }}
    alb.ingress.kubernetes.io/healthcheck-path: {{ . | quote }}
    {{- end }}
    {{- with .successCodes }}
    alb.ingress.kubernetes.io/success-codes: {{ join "," . | quote }}
    {{- end }}
    {{- with .port }}
    {{- $check := index $root.Values.ports . }}
    alb.ingress.kubernetes.io/healthcheck-port: {{ if kindIs "map" $check }}{{ $check.target | quote }}{{ else }}{{ $check | quote }}{{ end }}
    {{- end }}
    {{- end }}
    external-dns.alpha.kubernetes.io/hostname: {{ join "," $hosts | quote }}
    {{- if eq $route.exposure "private" }}
    external-dns.alpha.kubernetes.io/route53-private-zone: 'true'
    {{- end }}
spec:
  ingressClassName: {{ $root.Release.Namespace }}-{{ $route.exposure }}
  rules:
    {{- range $host := $hosts }}
    - host: {{ $host | quote }}
      http:
        paths:
          {{- range $route.paths | default (list "/") }}
          - path: {{ . | quote }}
            pathType: Prefix
            backend:
              service:
                name: {{ $service }}
                {{- /* By name in mesh canaries, which keeps an existing ALB target group across the move to this chart. */}}
                port: {{ if include "mitis.service.meshCanary" $root }}{name: {{ $portName }}}{{ else }}{number: {{ $listen }}}{{ end }}
          {{- end }}
    {{- end }}
{{- end }}
{{- end -}}

{{- define "mitis.service.render.network" -}}
{{- include "mitis.service.validate" . -}}
{{- if and (has .Values.render.part (list "all" "runtime" "application")) (has .Values.workload (list "deployment" "stateful")) -}}
{{- $root := . -}}
{{- $name := include "mitis.service.name" . -}}
{{- if eq .Values.workload "stateful" }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}-peers
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" 0) | nindent 4 }}
spec:
  type: ClusterIP
  clusterIP: None
  {{- /* Members must resolve each other before they are ready. */}}
  publishNotReadyAddresses: true
  selector:
    {{- include "mitis.service.selector" $root | nindent 4 }}
  {{- with .Values.ports }}
  ports:
    {{- range $portName, $port := . }}
    - name: {{ $portName }}
      port: {{ if kindIs "map" $port }}{{ $port.target }}{{ else }}{{ $port }}{{ end }}
      targetPort: {{ $portName }}
      protocol: TCP
    {{- end }}
  {{- end }}
{{- end }}
{{- $canary := not (empty (include "mitis.service.rollout" $)) -}}
{{- /* A mesh canary's controller owns its Service: Argo applying one could point traffic at pods that aren't ready. */}}
{{- $services := ternary (list) (list $name) (not (empty (include "mitis.service.meshCanary" .))) -}}
{{- if $canary -}}{{ $services = append $services (printf "%s-candidate" $name) }}{{- end -}}
{{- if .Values.ports -}}
{{- range $service := $services }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $service }}
  labels:
    {{- include "mitis.service.labels" $root | nindent 4 }}
  annotations:
    {{- include "mitis.service.wave" (dict "root" $root "wave" 0) | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "mitis.service.selector" $root | nindent 4 }}
  ports:
    {{- range $portName, $port := $root.Values.ports }}
    - name: {{ $portName }}
      port: {{ if kindIs "map" $port }}{{ $port.listen }}{{ else if eq $portName "http" }}80{{ else }}{{ $port }}{{ end }}
      targetPort: {{ $portName }}
      protocol: TCP
      {{- if eq $portName "grpc" }}
      appProtocol: kubernetes.io/h2c
      {{- end }}
    {{- end }}
{{- end }}
{{- end }}
{{- with .Values.http }}
{{- $portName := .port | default "http" -}}
{{- $port := index $root.Values.ports $portName -}}
{{- $listen := $port -}}
{{- if kindIs "map" $port -}}{{ $listen = $port.listen }}{{- else if eq $portName "http" -}}{{ $listen = 80 }}{{- end }}
---
{{ include "mitis.service.route" (dict "root" $root "name" $name "subdomain" $name "route" . "portName" $portName "listen" $listen) }}
{{- range $key, $route := .also }}
{{- if $route }}
---
{{ include "mitis.service.route" (dict "root" $root "name" (printf "%s-%s" $name $key) "route" $route "portName" $portName "listen" $listen) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
