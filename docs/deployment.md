# Native composition and release jobs

## Values layering

Both charts use ordinary Helm merging: chart defaults → canonical service → environment defaults → service/environment overrides. Maps merge; lists replace. Service environment entries, storage entries, mounts, and individual managed-secret values can be set to null to omit them. Remove references to an omitted storage or Secret key in the same override; a managed Secret still needs at least one remote value. Helpers inherit only the main image, as a whole, when their own image object is absent.

```text
services/api.yaml
environments/test/defaults.yaml
environments/test/api.yaml
```

All three files use the service vocabulary. The environment owns facts such as domain, secret scope/store, shared-storage class, and placement. Use native infrastructure outputs to supply authoritative facts; copying cloud identifiers into service files creates conflicting ownership.

## Helmfile

For a simple continuous service or a resource-only service with after-ready jobs:

```yaml
helmDefaults:
  wait: true
  waitForJobs: true
  timeout: 900
releases:
  - name: api
    namespace: example
    chart: oci://ghcr.io/mitis-cloud/charts/service
    version: 0.1.0
    values:
      - services/api.yaml
      - environments/test/defaults.yaml
      - environments/test/api.yaml
```

This is an installation pattern for a published version, not proof that version is already released. Check the repository's tags/package first. The [local Helmfile fixture](../examples/helmfile.yaml) uses the source chart for reproducible rendering.

Helm before-hooks execute before ordinary resources, including their ConfigMaps and ExternalSecrets. Prefer a resource-only service with an `after-ready` job and a downstream Helmfile `needs` dependency for an independently purposeful migration. This creates its inputs and runs its failure-gating job in one release. A genuine before-job attached to a running service that needs chart-managed inputs requires **two stable releases of the same definition**:

```yaml
releases:
  - name: api-inputs
    chart: oci://ghcr.io/mitis-cloud/charts/service
    version: 0.1.0
    values: [services/api.yaml, environments/test/defaults.yaml, environments/test/api.yaml]
    set:
      - {name: name, value: api}
      - {name: render.part, value: inputs}
  - name: api
    chart: oci://ghcr.io/mitis-cloud/charts/service
    version: 0.1.0
    needs: [api-inputs]
    wait: true
    waitForJobs: true
    timeout: 900
    values: [services/api.yaml, environments/test/defaults.yaml, environments/test/api.yaml]
    set:
      - {name: name, value: api}
      - {name: render.part, value: runtime}
```

Always preserve the same explicit `name` across the two releases. Never change an existing release between parts: Helm would interpret omitted resources as deletions. Use splits only where the lifecycle needs them. An independent migration service can use ordinary inputs plus an `after-ready` hook, with its consumers depending on that release.

When an existing scheduled helper must retain a separate release, `application` owns inputs/workload/release jobs and `scheduled` owns only CronJobs. Both read the same complete service definition; schedule the helper release after its application with `needs`. `all` is the unsplit equivalent. These ownership modes are permanent choices for a release, not switches to alternate during upgrades.

`before` maps to pre-install/pre-upgrade hooks; `after-ready` maps to post-install/post-upgrade hooks. Helm must wait for workload readiness before the latter. Hook Jobs are recreated on upgrade. By default failed results remain for diagnosis and successful hooks are cleaned up; `cleanup` can preserve an existing on-completion or next-run policy. Scheduled jobs are ordinary CronJobs, not release hooks.

`needs` orders release commands and propagates failures. It does not prove asynchronous secret/stream reconciliation. Required Secret-key references keep pods from starting until usable inputs exist. Where the service declares controller-created resources that callers require, the owning product's maintained Helmfile composition must add bounded native checks, such as `kubectl wait --for=condition=Ready ... --timeout=...`, after installing the controller/resources. Do not introduce an unbounded watcher or treat submitted custom resources as ready.

`helmfile apply` is diff-driven: an unchanged prerequisite release may not rerun its hooks when another release changes. A full release operation that promises job execution must select the coordinated releases and use sync/upgrade semantics. Ordinary diff/render/refresh is not a release operation. Preserve an existing explicit restart token when converting a current pipeline.

## Argo CD

Set `render.engine: argo`. Argo consumes plain YAML layers through native multi-source Helm values. It does not run Helmfile or template `.gotmpl` files.

| Resource group | Argo behavior |
| --- | --- |
| Managed inputs | Ordinary Sync resources, wave -20 |
| Before jobs | Repeatable Sync hook Jobs, waves -10 through -1 |
| Runtime | Wave 0 |
| After-ready jobs | Repeatable Sync hook Jobs, waves 10 through 19 |

Argo's Sync hooks are deliberate: PreSync would run before newly created ordinary prerequisites. Resource-selective sync skips hooks and is outside the full-release contract. Waves order resources **inside one Application**, not independent Applications. Group truly coordinated services into one release unit/Application when their job ordering requires it.

Within that Application, `render.order` assigns a service an ordering slot (0–100). Its inputs, before-jobs, runtime, and after-jobs occupy waves `100 × order - 20`, `-10..-1`, `0`, and `10..19` respectively. A service in slot 1 therefore waits for slot 0's after-jobs. Delivery sets the slot from each service's `order`. Vendor dependencies also need their native resource annotations aligned to the intended slot; a helper's slot cannot change a sibling vendor chart's resources. Helmfile uses explicit `needs` between releases instead.

Argo health checks must cover selected custom resources. ExternalSecret readiness is asynchronous and does not promise a fresh provider read for each sync. Product resources with observed generations need readiness checks that require current-generation status; those checks belong to the product's deployment integration. The service chart does not install controller health customizations.

The delivery chart generates ordinary-apply Applications with `RespectIgnoreDifferences=true`. Do not add Force/Replace synchronization to protected workloads. Install the documented capacity and progressive-routing field ownership together with the corresponding service capabilities.

## Manual jobs

For a resource-only task service, explicitly set `render.task` on its normal release with `render.part: all`. Its inputs remain owned by that release, and the selected task runs as a post-install/post-upgrade hook. Clear `render.task` after the invocation; ordinary reconciliation will not run it. Dedicated service jobs should use this single-release pattern rather than creating historical input/runtime splits.

`run: manual` jobs are excluded from ordinary rendering. Execute only through a separately selected task release, after inputs exist:

```sh
helm upgrade --install api-reset oci://ghcr.io/mitis-cloud/charts/service \
  --version 0.1.0 --namespace example \
  -f services/api.yaml -f environments/test/defaults.yaml -f environments/test/api.yaml \
  --set name=api --set render.part=task --set render.task=reset \
  --wait --wait-for-jobs --timeout 15m
```

This is an explicit operation, not an action for routine CI synchronization. Destructive reset semantics belong to the application command and the caller's deliberate selection.

## Native vendor charts

Consume native vendor charts directly with their documented values. A dedicated companion using `service` has its own values and declares its image, environment and storage explicitly. No wrapper or unified vendor/helper file is required.

Helmfile can order companion releases through `needs`. With the delivery chart, set `serviceScopes: []` for a direct vendor source so service-specific render parameters are not injected into it. Argo waves order resources only within one Application; a service helper's ordering slot does not annotate a vendor chart's resources. Preserve the product's native readiness and lifecycle requirements explicitly.
