# Service chart

> [!IMPORTANT]
> This chart depends on controllers and infrastructure it does not install or create, several of them enabled by default. Read [Prerequisites and boundary](../../docs/prerequisites.md) before deploying.

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
  databaseHost: database.example.internal
environment:
  LOG_LEVEL: info
  TENANT: {fact: tenant}
  PORTAL: {url: {subdomain: portal, path: /register}}
  API: {endpoint: {service: api, path: /rest}}
  TOKEN: {ssm: application-token}
  DATABASE_URL:
    format: "postgresql://user:{ssm:database-password|urlquery}@{fact:databaseHost}/app"
  PASSWORD: {secret: {name: shared-credentials, key: password}}
  INSTANCE: {instance: name}
```

- `url` uses `hostname` or `subdomain`/`domain`, optional `path`, and `scheme` (`https` by default).
- `endpoint` uses an internal `service`, optional numeric `port`, `path`, and `scheme` (`http` by default).
- `fact` reads a literal value from the environment's `facts` map; missing facts fail rendering.
- `ssm` generates an ExternalSecret and an explicit Secret-key reference. `secretPrefix` is prepended; an object `{key: /actual/key, property: field, absolute: true}` bypasses it and optionally selects a property.
- `format` substitutes only `{ssm:key}` and `{fact:name}`. Append `|urlquery` to URL-encode a substitution. Unknown references, operations and unmatched braces fail rendering. Other strings remain literal, including Go-template syntax.
- `secret` consumes an existing same-namespace Secret/key without creating it.
- `instance` exposes `name`, `namespace`, or `ip` through the downward API.
- An entry set to `null` is omitted, allowing environment overrides to remove a canonical variable.

Inputs are declared where consumed. The chart generates ESO v2 transformations; Helm never fetches remote values. `secretRefresh` defaults to `1h`. Environment inputs use `<process-identity>-secrets`; optional `inputSecret` on a process/helper/job pins an existing target identity. Variables use their authored names as Secret keys unless an input explicitly pins `key`, for example `PASSWORD: {ssm: application-password, key: password}`. This preserves an existing consumer-facing key without a separate generation definition. Explicitly shared targets must agree on each key's source; conflicting definitions fail rendering. Only consumed output keys enter the target Secret, not internal transformation aliases.

An individual remote-backed input may pin `name` when its existing Secret differs from the process's `inputSecret`. For example, `TOKEN: {ssm: token, name: shared-inputs, key: token}` generates and consumes exactly that target/key. `name` and `key` are identity controls, not remote-key overrides. A scalar may also be written as `{value: text}`; ordinary scalar entries remain the concise default.

Helpers declare their inputs explicitly. Consuming a shared external Secret uses `secret`; service-owned generated inputs have one intentional owner. Consumers wait for required keys to exist, not for a guaranteed fresh provider read on every release.

### Outputs

`outputs` (since 0.3.0) declares values the service produces and publishes, such as a token created during bootstrap:

```yaml
outputs:
  api-token: {ssm: bootstrap-api-token}
  org-id: {ssm: bootstrap-org-id, sensitive: false}
jobs:
  setup:
    environment:
      EXISTING_TOKEN: {output: api-token}
```

The chart creates an empty `<name>-outputs` Secret and a chart-owned service account allowed to get, update and patch only that Secret. A process writes each key there through the Kubernetes API. An External Secrets `PushSecret` publishes every key to the secret store under the same remote-key rules as inputs, encrypted unless `sensitive: false`. Remote values are never deleted with the service.

`{output: key}` reads a published value back as an optional environment variable: it is unset until first written, which lets a bootstrap step run once and become idempotent. Outputs replace cloud identities whose only purpose was writing to the secret store. They cannot be combined with `account`. The store must allow writes, and remote keys that already exist must be marked as managed by External Secrets. See the [outputs example](../../examples/service/outputs.yaml).

## Files, storage and helpers

See the [storage guide](../../docs/storage.md) for the complete storage/mount reference.

`files` maps a logical name to an absolute `path` and exactly one source:

| Source | Meaning |
| --- | --- |
| `content` | Literal text, including `{{ ... }}`. |
| `ssm` | A complete remote file; accepts the environment input's remote-key forms. |
| `format` | Text using the same restricted substitutions as environment inputs. |
| `yaml`, `json` | Structured documents; leaves accept literals, `ssm`, `fact` and `format`. Serialization escapes values and preserves literal/fact types. |
| `secret: {name, key}` | An existing external Secret key. |
| `from` | Explicitly consume one uniquely named file declared by this service or a helper. |

`name` pins a generated ConfigMap/Secret identity; `key` pins its stored and mounted filename, defaulting to the logical name. `from` reuses the source's identity/key and cannot redefine them. `mode` is decimal Unix permissions, default 420 (`0644`). Mounts are read-only and use subpaths. Files containing remote references become Secrets; other files become ConfigMaps. Resource-only services can declare shared files consumed by their jobs. Null removes a file; a dangling or ambiguous `from` fails validation.

```yaml
files:
  connection:
    path: /etc/service/connection.yaml
    yaml:
      database:
        host: {fact: databaseHost}
        password: {ssm: database-password}
init:
  prepare:
    command: [prepare, /work/connection.yaml]
    files:
      connection: {from: connection, path: /work/connection.yaml}
```

See the [complete file example](../../examples/service/input-files.yaml). Document keys `ssm`, `fact` and `format` denote typed references; they are reserved at a reference leaf.

`init` and `sidecars` map helper names to the same `image`, `command`, `args`, `workingDirectory`, `capacity`, `ports`, `health`, `environment`, `files`, `tokens`, `mounts`, `identity`, and `security` vocabulary. A helper inherits the **whole main image** only when its image object is absent. It does not inherit environment, files, tokens, mounts, or resources; helper resource reservations/ceilings are explicit and omitted when undeclared. Helper capacity has no instance count. Init helpers run in name order; use names expressing the required order when there are several. Sidecars are ordinary colocated containers.

`permissions` provides namespace-scoped API access for a controller/helper: each entry has `group`, `resources`, `actions`, and optional resource `names`. It creates one service account, Role, and RoleBinding. Allowed actions are get/list/watch/create/update/patch/delete. It does not grant cluster permissions.

`tokens` (since 0.1.1) maps a logical name to `{audience, path, lifetime}` on any process, helper, or job. It projects a native service-account token into a dedicated read-only directory at `path`, with filename `token`. The optional `lifetime` requests 600–86400 seconds (default 3600); the API server can adjust it. Each process explicitly opts in. These tokens use the pod's service account, independently of the default Kubernetes API token controlled by `apiToken`. They grant no permissions by themselves: the recipient's trust policy and the existing `account` remain infrastructure-owned. Direct directory mounts preserve kubelet token rotation; no subpath is used. Token directories must not overlap other mounts/files. See the [identity example](../../examples/service/identity.yaml).

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
| `resultLifetime` | Optional native TTL for results retained by `on-success`, `on-completion`, or `next-run`. For example, `cleanup: on-success` plus `resultLifetime: 5m` removes successful hooks immediately and retains failed results for up to five minutes after completion. Cannot combine with duration-valued cleanup or scheduled jobs. |

Jobs also accept `account`, `apiToken`, `discovery`, and `security` with the process semantics above; declare them on each job. They do not inherit the main process's identity or token settings.

Release jobs are repeatable and failure-gating on full release operations, not exactly-once tasks. Rendering/refreshing does not execute them. Manual tasks are excluded until explicitly selected; see [engine sequencing](../../docs/deployment.md).

`updates.strategy` is `rolling` (default), `replace` (non-overlapping upgrade), or `canary`. `updates.reload` defaults to true: ordinary file/configuration changes update the pod checksum, and the installed Reloader handles referenced ConfigMap/Secret changes. `updates.reloadSecrets` also defaults to true; explicitly disable it to retain configuration-only replacement without activating secret-triggered rollouts. Disable `reload` entirely for self-reloading applications. `updates.restart` is an optional explicit deployment-operation token, never a template clock.

Canary requires Gateway routing, HTTP exposure, and an explicit `updates.smoke` job with application-owned command, timeout, inputs, and resources. It keeps a baseline-sized candidate pool, tests at zero traffic, then tests at 10% and 50% before full promotion. Failed application jobs fail analysis (`failureLimit: 0`); native inconclusive analysis pauses progression. A repeatable after-ready smoke job covers the initial deployment, when Rollouts skips canary analysis. Initial failure marks the release unsuccessful but has no previous stable revision to restore. Controller/infrastructure error handling and actual traffic behavior require runtime verification.

`disruption.handover: true` creates Understudy protection for an overlap-safe Deployment/Rollout. `maxWait` defaults to `10m`; `name` can preserve its resource identity. It is incompatible with `replace` and resource-only services. Argo mode supplies initial workload replicas and Understudy baseline from the same `capacity.instances`; Helm mode retains reactive protection. See [capacity ownership](../../docs/integrations.md).

## Orchestration selectors

`render.engine` is `helm` or `argo`; `render.part` is `all`, `inputs`, `runtime`, `application`, `scheduled`, or `task`; `render.routing` is `ingress` or `gateway`. Ordinary services and their dedicated helpers use `all` in one release. The optional application/scheduled split supports an explicitly independent scheduled-helper owner: `application` owns inputs, continuous workload, and release jobs; `scheduled` owns only CronJobs. `render.order` is a coordinated-service slot from 0 to 100, default 0, preserving input/job/runtime ordering within each slot. `render.task` is required for the `task` part and must name a declared manual job. Since 0.1.2, it can also select a manual job with `part: all`, keeping the job and its inputs in one release. Omitting it excludes manual jobs. These are finite rendering selections for maintained orchestration, not a second service specification. `render.capacity` is `git` (default) or `external`: in Argo mode, `external` leaves the Understudy baseline unpinned so an external scaler (for example scheduled downscaling) is the only replica writer. Their defaults are helm/all/ingress/git. Keep stable release owners; never alternate parts on an existing release.

An environment layer may set an individual `environment`, `files`, `jobs`, `storage` or `mounts` entry to null to remove it. Omit matching references too; dangling storage mounts and file references fail validation. Omitting a job also omits inputs produced only for that job. Empty generated input resources are omitted.
