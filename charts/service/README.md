# Service chart

The service definition is a closed interface. [values.schema.json](values.schema.json) is the exact type contract; this guide explains behavior. [Examples](../../examples/service) are rendered from packaged charts in CI.

## Process and capacity

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | Helm release name | Stable logical identity, maximum 40 characters. Keep identical across split owners. |
| `workload` | `deployment` | `deployment` or `resources`; the latter has no primary container. |
| `image.repository` | required for a running container | Image repository. |
| `image.tag`, `image.digest` | one required | SHA-256 digest wins if both are supplied. |
| `image.pullPolicy` | `IfNotPresent` | Also accepts `Always` and `Never`. |
| `command`, `args` | image defaults | Arrays passed directly to the container; shell interpretation requires an explicit shell command. |
| `workingDirectory` | image default | Absolute working directory. |
| `capacity.instances` | `1` | Baseline replicas; explicit `0` is preserved. Rollouts/handover may temporarily exceed it. |
| `capacity.cpu` | `100m` for main | Scheduling reservation, expressed in cores or millicores. Helpers do not inherit main reservations. |
| `capacity.memory` | `128Mi` | Memory reservation. |
| `capacity.memoryLimit` | `256Mi` | Memory ceiling. |
| `capacity.cpuLimit` | omitted | Optional CPU ceiling; CPU is uncapped by default. |
| `availability.zones` | omitted | Strict zone spread. Instances must cover requested zones; two zones with one unavailable means reduced capacity. |
| `placement` | `any` | An existing workload pool such as `spot`, `stable`, or `analytics`, using the environment's `env` node label/taint convention. `any` adds no pool constraint. |
| `identity.user`, `.group` | image defaults | Numeric process UID/GID, including zero. |
| `shutdown` | `30` | Graceful termination budget in seconds. |
| `fileGroup` | omitted | Group ID for writable pod volumes; also supported on jobs. |
| `architecture` | omitted | Require `amd64` or `arm64` nodes when the image is single-platform; applies to service jobs too. |
| `security` | `image-defaults` | `restricted` requires non-root, a read-only root filesystem, no privilege escalation, dropped capabilities, and runtime-default seccomp. Declare a nonzero `identity.user` or use an image with a non-root user. |
| `account` | Kubernetes default | Consume an existing infrastructure-owned service account, including its cloud identity. Incompatible with chart-owned `permissions`. |
| `apiToken` | Kubernetes default (`true`) | Whether to mount the service account's API token. |
| `discovery` | `dns` | `environment` additionally injects Kubernetes service-link environment variables for applications requiring them. |

New services roll with one surge instance and no planned unavailability. Adoption of existing services must record their actual settings instead of assuming these defaults are equivalent.

## Network and health

```yaml
ports:
  http: 8000
  peers: 7000
http:
  exposure: public
  subdomain: api
  paths: [/rest]
  healthcheck:
    path: /health/live
    successCodes: [204]
health:
  startup: {http: /health/live, port: http, delay: 5, interval: 5, timeout: 2, failures: 30}
  readiness: {http: /health/ready, port: http}
  liveness: {tcp: http}
```

`ports` names the container's TCP ports and creates internal access. Simple `http` exposes service port 80; other simple ports expose their own number. Use `{target: 8080, listen: 8080}` for an explicit listening-port exception.

`http.exposure` is `public` or `private`. `hostname` overrides `subdomain` + environment `domain`; the default subdomain is the service name. `aliases` adds hostnames. `paths` defaults to `[/]`; `port` defaults to `http`. All declared hosts must already have supported DNS/TLS infrastructure. External routes use HTTPS. [Routing prerequisites](../../docs/integrations.md) define the controller conventions.

Set `subdomain: ''` explicitly to serve the base domain. Ingress-only `priority` preserves an explicit shared-ALB rule order (-1000–1000; lower numbers match first). Gateway mode rejects `priority` and `healthcheck`, whose ALB settings have no portable Gateway equivalent.

A resource-only service can serve a fixed `response: {status: 503, contentType: text/html, body: '<h1>Maintenance</h1>'}` through `http` without a container. Status accepts 2xx, 4xx, and 5xx; content type defaults to `text/plain` and also accepts `text/css`, `text/html`, `application/javascript`, and `application/json`. Bodies are limited to 1024 UTF-8 bytes. This uses ALB fixed-response actions and therefore requires ingress routing; portable Gateway API has no equivalent response-body filter. It has no backend port or healthcheck. See the [response example](../../examples/service/response.yaml).

`redirectFromHttp: true` additionally accepts plain HTTP solely to redirect to HTTPS. In Gateway mode the platform must also provide the `http` listener; the chart creates a separate redirect route. Omission requests HTTPS only.

Each health check uses either `http: /path` (with optional `port` and literal `headers`) or `tcp: <port>`. Named ports must exist. `delay`, `interval`, `timeout`, and `failures` use seconds/counts, defaulting to 0, 10, 2, and 3; startup defaults to 30 failures. Startup, readiness, and liveness have distinct responsibilities. No endpoint is invented when health checks are omitted.

## Environment and secrets

`environment` maps variable names to literal strings/numbers/booleans or one typed reference:

```yaml
domain: test.example.com
secretPrefix: test-
secretStore: aws-ssm
facts:
  tenant: example
environment:
  LOG_LEVEL: info
  TENANT: {fact: tenant}
  PORTAL: {url: {subdomain: portal, path: /register}}
  API: {endpoint: {service: api, path: /rest}}
  TOKEN: {remoteSecret: application-token}
  PASSWORD: {secret: {name: shared-credentials, key: password}}
  INSTANCE: {instance: name}
```

- `url` uses `hostname` or `subdomain`/`domain`, optional `path`, and `scheme` (`https` by default).
- `endpoint` uses an internal `service`, optional numeric `port`, `path`, and `scheme` (`http` by default).
- `fact` reads a literal value from the environment's `facts` map; missing facts fail rendering.
- `remoteSecret` generates an ExternalSecret and an explicit Secret-key reference. `secretPrefix` is prepended; an object `{key: /actual/key, property: field, absolute: true}` bypasses it and optionally selects a property.
- `secret` consumes an existing same-namespace Secret/key without creating it.
- `instance` exposes `name`, `namespace`, or `ip` through the downward API.
- An entry set to `null` is omitted, allowing environment overrides to remove a canonical variable.

Shared or vendor-consumed Secrets have one intentional owner through `secrets`:

```yaml
secrets:
  credentials:
    name: shared-credentials
    refresh: 1h
    values:
      username: database-user
      password: database-password
    template:
      url: 'postgres://{{ .username }}:{{ .password }}@database/app'
```

Map keys default to target/resource names; `name` pins an existing identity. `refresh` defaults to `1h`. `values` uses the same remote-key forms. `template` contains **ESO v2** templates, never Helm expressions; templates merge with fetched keys. Remote values remain in the secret manager. Consumers wait for required Secret keys to exist, not for a guaranteed fresh provider read on every release.

When a secret-derived configuration file also needs non-secret environment context, declare `templateContext: {host: {fact: databaseHost}, database: app}` beside `template`, then reference `{{ $context.host }}` or `{{ $context.database }}` inside the ESO template. Context accepts literal strings/numbers/booleans and named `fact` references; it preserves types and requires `template`. Missing facts fail rendering. The chart safely serializes context as data for ESO v2's `fromJson`; context strings are never evaluated as Helm or ESO expressions. Fetched secrets remain accessible as ordinary ESO keys such as `{{ .password }}`.

## Files, storage and helpers

See the [storage guide](../../docs/storage.md) for the complete storage/mount reference.

`files` maps a logical name to `{path, content}` or `{path, secret: {name, key}}`. `path` is absolute. `name` can pin a literal file's ConfigMap identity; `key` names its stored/mounted filename (defaults to the logical name); `mode` is decimal Unix permissions, default 420 (`0644`). File mounts are read-only and use a subpath. Strings stay literal, including `{{ ... }}`. Secret-derived file content is produced by ESO under `secrets.template` and consumed using `files.*.secret`.

`init` and `sidecars` map helper names to the same `image`, `command`, `args`, `workingDirectory`, `capacity`, `ports`, `health`, `environment`, `files`, `mounts`, `identity`, and `security` vocabulary. A helper inherits the **whole main image** only when its image object is absent. It does not inherit environment, files, mounts, or resources; helper resource reservations/ceilings are explicit and omitted when undeclared. Helper capacity has no instance count. Init helpers run in name order; use names expressing the required order when there are several. Sidecars are ordinary colocated containers.

`permissions` provides namespace-scoped API access for a controller/helper: each entry has `group`, `resources`, `actions`, and optional resource `names`. It creates one service account, Role, and RoleBinding. Allowed actions are get/list/watch/create/update/patch/delete. It does not grant cluster permissions.

## Jobs and updates

`jobs` maps names to the container fields above (excluding ports/health), plus dedicated `init` helpers and:

| Field | Meaning |
| --- | --- |
| `run` | `before`, `after-ready`, or `manual`; mutually exclusive with schedule. |
| `schedule` | Cron schedule; jobs use `Forbid` concurrency. |
| `name` | Explicit Kubernetes job/CronJob name when identity continuity requires it. |
| `order` | 0–9 within the before/after group; default 0. |
| `timeout` | Positive whole `s`, `m`, or `h`; release jobs default to `10m`, scheduled jobs have no deadline unless declared. Explicit `none` preserves an existing job without a controller deadline; canary smoke must remain bounded. Orchestrator timeouts still apply. |
| `retries` | Job backoff limit, default 0. |
| `suspend` | Suspend a scheduled job, default false. |
| `history.successful`, `.failed` | Scheduled result-history counts; each defaults to 1, and zero is preserved. |
| `startDeadline` | Optional scheduled job late-start budget, in seconds. |
| `timeZone` | Optional IANA time zone for a scheduled job, such as `Etc/UTC`; omission uses the controller's zone. |
| `cleanup` | Release result retention: `on-success` (default), `on-completion`, `next-run`, or a duration such as `5m` after completion. Durations use the native Job TTL controller. |

Jobs also accept `account`, `apiToken`, `discovery`, and `security` with the process semantics above; declare them on each job. They do not inherit the main process's identity or token settings.

Release jobs are repeatable and failure-gating on full release operations, not exactly-once tasks. Rendering/refreshing does not execute them. Manual tasks are excluded until explicitly selected; see [engine sequencing](../../docs/deployment.md).

`updates.strategy` is `rolling` (default), `replace` (non-overlapping upgrade), or `canary`. `updates.reload` defaults to true: ordinary file/configuration changes update the pod checksum, and the installed Reloader handles referenced ConfigMap/Secret changes. `updates.reloadSecrets` also defaults to true; explicitly disable it to retain configuration-only replacement without activating secret-triggered rollouts. Disable `reload` entirely for self-reloading applications. `updates.restart` is an optional explicit deployment-operation token, never a template clock.

Canary requires Gateway routing, HTTP exposure, and an explicit `updates.smoke` job with application-owned command, timeout, inputs, and resources. It keeps a baseline-sized candidate pool, tests at zero traffic, then tests at 10% and 50% before full promotion. Failed application jobs fail analysis (`failureLimit: 0`); native inconclusive analysis pauses progression. A repeatable after-ready smoke job covers the initial deployment, when Rollouts skips canary analysis. Initial failure marks the release unsuccessful but has no previous stable revision to restore. Controller/infrastructure error handling and actual traffic behavior require runtime verification.

`disruption.handover: true` creates Understudy protection for an overlap-safe Deployment/Rollout. `maxWait` defaults to `10m`; `name` can preserve its resource identity. It is incompatible with `replace` and resource-only services. Argo mode supplies initial workload replicas and Understudy baseline from the same `capacity.instances`; Helm mode retains reactive protection. See [capacity ownership](../../docs/integrations.md).

## Orchestration selectors

`render.engine` is `helm` or `argo`; `render.part` is `all`, `inputs`, `runtime`, `application`, `scheduled`, or `task`; `render.routing` is `ingress` or `gateway`. The optional application/scheduled split preserves a separate scheduled-helper release: `application` owns inputs, continuous workload, and release jobs; `scheduled` owns only CronJobs. `render.order` is a coordinated-service slot from 0 to 100, default 0, preserving input/job/runtime ordering within each slot. `render.task` is required only for the `task` part and must name a declared manual job. These are finite rendering selections for maintained orchestration, not a second service specification. Their defaults are helm/all/ingress. Keep stable release owners; never alternate parts on an existing release.
