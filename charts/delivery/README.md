# Delivery chart

> [!IMPORTANT]
> This chart requires Argo CD, Kargo and the infrastructure they orchestrate, none of which it installs. Read [Prerequisites and boundary](../../docs/prerequisites.md) before activating it.

The delivery chart declares native Argo CD and Kargo resources for named environments and independent release units. A release unit is one service with its helpers, or an explicitly coordinated group. Sharing an image does not group services automatically.

See [the complete example](../../examples/delivery/platform.yaml) and [values.schema.json](values.schema.json). The example deliberately uses generic names, not an inferred stage/tenant model.

## Inputs

| Field | Meaning |
| --- | --- |
| `project` | Required Kargo project identity and Argo project/application prefix. |
| `argoNamespace` | Argo CD control-plane namespace; default `argocd`. |
| `git.repository` | Required HTTPS URL containing canonical service and environment YAML. |
| `git.revision` | Authoritative configuration branch; default `main`. |
| `git.services` | Canonical service directory, default `services`. |
| `git.environments` | Environment directory, default `environments`. |
| `charts` | Named chart definitions: `repository`, chart `name`, and optional `serviceScopes`. |
| `environments` | Named deployment targets with cluster, namespace, chart pins, and explicit promotion relationships. |
| `releaseUnits` | Named groups of services with chart selection, promoted image paths, and explicit delegated-capacity targets. |
| `stageColors` | Ordered `{match, color}` rules; the first `match` regular expression matching an environment name colors its Stage in the Kargo UI. `color` is a Kargo palette name (e.g. `green`, `yellow`, `red`, `violet`) or a `#rrggbb` hex value. |
| `discovery.interval` | Warehouse refresh interval, default `5m`. |
| `discovery.allowTags` | Allowed image-tag regular expression; default semantic release tags, optionally prefixed `v`. |
| `discovery.limit` | Discovery history limit, default 20. |

The default `charts.service` points at `ghcr.io/mitis-cloud/charts`, chart `service`, and root scope `['']`; the default `charts.environment` points at the [environment chart](../environment/README.md). For an OCI Helm source, Argo's chart `repoURL` omits `oci://`; register it with Argo as an OCI-enabled Helm repository. Chart versions come from each environment, independently of images and the delivery chart's version.

`serviceScopes` identifies where a source's values reach the service chart. Environment layers and `defaults.yaml` reach only sources whose scopes include the root (`''`): other charts get just their service file and environment file. The default `['']` covers a direct `service` source. Use an empty list for a direct vendor chart. A product chart that embeds `service` as a dependency lists its aliases, e.g. `[helpers]`. Delivery sets only the finite `render.engine`, `render.part`, and `render.routing` parameters at those scopes. Ordinary first-party values stay at the root.

### Environments

Each environment requires:

- `cluster`: an existing Argo destination cluster name.
- `namespace`: the existing application namespace.
- `charts`: a version pin for every chart used there.
- `units`: an explicit map from release unit names to nonempty service lists.

Optional `layers` lists repository-relative values files, such as `stages/test.yaml`, that every service in the environment receives after its definition and before the environment's own files, in order. Use them for values shared by several environments.

Optional `environment` holds [environment chart](../environment/README.md) values: exposures, isolation and the maintenance switch. The chart then renders an Argo CD Application for the environment, synchronized automatically because it is configuration rather than a promoted release, and the environment needs a `charts.environment` pin. Its namespace is the environment's `namespace`. The AppProject then also permits the cluster-scoped `Environment`.

Optional `externallyScaled: true` declares that replica counts change outside Git, for example through scheduled downscaling: Argo ignores `spec.replicas` on Deployments, StatefulSets and Rollouts there, so automatic synchronization does not fight the scaler, and service-chart sources get `render.capacity: external` so their Understudy baseline doesn't either (requires service chart 0.3.1 or later).

Every environment selects its services explicitly:

```yaml
units:
  backend: [api, migrations]
  site: [portal, assets]
```

Omitted units have no Application or Stage slot in that environment. Omitted services have no Helm source, capacity delegation, or promotion file operations. Adding a service to the shared catalog does not deploy it anywhere until selected. Service lists do not determine deployment order; each service's `order` does. Version 0.9.0 requires this map; the previous unit list and implicit selection of all units are no longer supported.

Optional `vars` are promotion variables for `gitValues` (e.g. `{stage: test, tenant: example}`). `upstream` is a list of predecessor environment names. An empty list means direct discovery from the units' Warehouses. `promotion` is `manual` (default) or `automatic`. `routing` is `gateway` (default) or `ingress`.

Names do not infer edges, create missing environments, or impose tenant/stage dimensions. Unknown units or services, empty or duplicate selections, missing pins for selected services, promotion cycles, and upstreams that lack a required unit fail rendering. Two upstreams are eligible sources, not a requirement that both separately approve the same Freight. Upstreams may select different services from a unit; verification there only exercises the services actually deployed.

### Release units

`releaseUnits.<unit>.services` maps canonical service filenames (without `.yaml`) to:

- `chart`: a key in `charts`.
- `releaseName`: optional Helm release identity; defaults to the service key.
- `order`: coordinated-service slot (0–100, default 0); a later service waits for earlier slots' after-jobs within the Application.
- `images`: values paths mapped to image repositories, e.g. `image.digest: ghcr.io/example/backend`. Wrapper paths may be nested. The unit's Warehouse subscribes once per unique repository across its complete service catalog. A unit where no service declares images is configuration: Kargo does not promote it, and its Applications sync automatically.
- `gitValues` (service level): top-level values filled at promotion from files in Git repositories, as `<key>: {repository, path, branch, expression}`. The unit's Warehouse follows each repository's branch (default `main`). Promotion checks the repository out at the Freight's commit, parses the JSON or YAML file at `path` (which may use `${{ vars.<name> }}`) and optionally narrows it with an expr-lang `expression` (the whole file by default). A service with `gitValues` owns its environment file: promotion rewrites `<environment>/<service>.yaml` whole with those values, then sets its image digests there, so keys removed at the source disappear. Keep anything hand-written in the service file or `defaults.yaml`, not in that environment file. A missing source file for a selected service fails promotion; it never disables that service implicitly.
- `criteria` (unit level): Freight creation criteria for the unit's Warehouse, a Kargo expression every new Freight must satisfy, for example that its builds and the Git commit their configuration comes from share a tag.
- `allowTags` (unit level, beside `services`): image-tag regular expression for this unit's images, overriding `discovery.allowTags`, for example `^[0-9a-f]{7}$` for commit tags. The newest build among matching tags is selected. An image shared by two units must use the same filter.
- `capacityTargets`: exact `{name, kind}` references for Understudy-managed Deployment/Rollout replicas. This declares field ownership for Argo, not capacity values. Match it to the service's actual protection resources and keep it absent for unprotected targets.

A service belongs to only one release unit. Each deployed unit gets one ApplicationSet whose explicit list generator selects its environments and produces one coordinated Application per environment, containing only that environment's selected services. A unit groups artifacts that are promoted together; it need not correspond to one source repository or container image.

Argo AppProjects permit namespace-scoped resources from the configured repositories at the configured destination. They do not grant cluster-scoped application resources; shared CRDs/controllers remain separately owned infrastructure.

## Native promotion

The chart emits a Kargo Project, ProjectConfig, one Warehouse per promoted unit and one Stage per environment that selects promoted units. Each Stage requests one Freight slot per selected promoted unit. Each Freight contains that unit's complete artifact set, shared across environments even when they deploy different subsets. Discovery therefore still waits for the unit's complete artifacts and creation criteria.

A promotion moves one unit's Freight. It clones the configuration branch, updates the selected services' promoted **digest** fields and Git-sourced values in one commit, pushes with native conflict handling, synchronizes that unit's Application, and waits for health. Other units are untouched. Unchanged resources synchronize as no-ops, but the selected unit's sync hooks run again.

Kargo `yaml-update` changes existing scalar keys only. Seed every promoted digest field in `environments/<environment>/<service>.yaml` before activation. Supply real immutable digests; do not point a digest field at a tag. Missing fields are promotion failures, not permission to synthesize a second generated service file.

Multiple repositories in one Warehouse do not prove images came from the same coordinated build. That coherence must be established by the project's build/release process if it is required. Concurrent promotions use native git-push integration; genuine edit conflicts fail visibly.

Applications authorize precisely their environment's Stage, `<project>:<environment>`, for Kargo. Kargo writes Git, then requests a full synchronization of the configured branch. Every environment tracks that shared branch, so health is Synced and Healthy rather than a match against the pushed commit: pinning the commit would mark every other environment unhealthy after any later commit. Argo CD's branch-resolution cache can make a sync immediately after the push apply the previous commit while the promotion still succeeds; verify the running digest where that matters. Promoted units are not synchronized automatically: Kargo requests full syncs after promotions. Configuration units synchronize automatically.

## Lifecycle and evidence

Install Kargo/Argo controllers and CRDs, Argo OCI/Git credentials, registered destinations, application namespaces, and Kargo Git/image credentials before activation. Kargo Project precedes namespaced Kargo resources. Controller bootstrap is not performed by this chart.

The service definition, any environment layers, and the environment's `defaults.yaml` and service file are all required and consumed in that order. Argo does not evaluate Helmfile templates. Jobs are failure-gating only inside their coordinated Application and with correct custom-resource health. See [deployment semantics](../../docs/deployment.md) and [controller prerequisites](../../docs/integrations.md).

CI proves chart/package rendering, CRD schemas, stage/reference isolation, native Go template expansion into valid Applications, and native Kargo step-config shape. The expansion test uses Go's standard template engine on the chart's explicit list elements. It does not run the ApplicationSet controller, evaluate live Freight expressions, prove Git credentials, or establish runtime traffic/readiness. Verify those boundaries in the adopting environment before enabling promotion.
