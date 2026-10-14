# Supported integration contracts

These charts render application resources. Infrastructure installs and configures controllers, CRDs, storage, route listeners, certificates, secrets access, and application health customizations.

## Reference versions and evidence

| Integration | Reference | Evidence in this repository |
| --- | --- | --- |
| Helm | 4.3.0 | Packaged rendering and strict values validation |
| Kubernetes | 1.35 API | Native resource schema validation |
| Argo CD | 3.5.5 | ApplicationSet/AppProject CRD shape; runtime reconciliation needs environment validation |
| Kargo | 1.12.4 | CRD shape and native step-configuration schemas |
| Argo Rollouts | 1.10.1 | Rollout/AnalysisTemplate schemas and progression assertions |
| Gateway API | 1.6.3 | HTTPRoute v1 schema |
| Gateway traffic plugin | 0.17.0 | Maintained HTTPRoute integration configuration; runtime traffic needs validation |
| External Secrets | 2.2.0 | Existing-consumer API baseline; ExternalSecret v1 schema |
| Understudy | 0.4.4 | Target/baseline schema and published upstream ordinary-Argo integration contract |

The new tool/controller references were checked against current stable releases on 2026-10-14. External Secrets deliberately uses the existing consumer baseline instead of silently upgrading the cluster to the latest 2.12.0. The service chart has no dependency on these controllers' Helm charts and does not install or upgrade them.

## Ingress mode

`render.routing: ingress` targets the AWS Load Balancer Controller's native facilities. Install an IngressClass named `<namespace>-public` and/or `<namespace>-private`, with IngressClassParams retaining that environment's actual group, scheme, WAF, and load-balancer settings. Certificate discovery uses the declared hosts and suitable issued ACM certificates; HTTPS listeners are explicitly requested by the chart. The class parameters' listener attributes alone do not create a listener.

Domains and group identities remain infrastructure-owned. Never reconstruct an environment-specific ALB group from an assumed cluster naming rule. Before adoption, compare effective route ordering, host/path routing, TLS, certificate coverage, WAF, and attributes to the existing resources. Controller readiness at this boundary is not proven by a rendered Ingress. Fixed `response` services use ALB-native actions; they do not provision a dummy backend and cannot be selected in Gateway mode.

## Gateway mode and canary

`render.routing: gateway` attaches HTTPRoute to a same-namespace Gateway named `public` or `private`, listener `https`. The platform supplies the appropriate implementation, listeners, certificates, and DNS. Only HTTP routing is supported; no TCP/TLS-route support is inferred.

Canary additionally requires Argo Rollouts and the maintained [Gateway API traffic plugin](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io/en/latest/installation/), including its controller configuration and RBAC. Rollouts owns selector hashes and traffic weights. The delivery chart delegates Service selector hashes and HTTPRoute weights with `RespectIgnoreDifferences=true`.

The application supplies a smoke command against the candidate endpoint. Success advances automatically; a failed test aborts the canary. Native inconclusive analysis pauses it. Initial deployments need the separate after-ready smoke job because native Rollouts skips its canary steps initially. A failing initial smoke has no prior stable version to restore. Actual route health, abort behavior, and controller-error behavior require runtime evidence.

## Planned-drain handover

Understudy 0.4.4 supports Deployments and Rollouts. It requires overlap-safe processes and exactly one effective-capacity writer. HPA/KEDA or another Understudy resource must not manage the same replicas. Protection applies to bounded planned eviction; it does not guarantee abrupt-failure, network-draining, or application-level availability.

For Argo, service capacity renders both initial replicas and `Understudy.spec.baselineReplicas`. The delivery unit's `capacityTargets` scopes delegation to the exact workload kind/name/namespace:

- `/spec/replicas`
- `/metadata/annotations/understudy.sh~1base-replicas`
- `/metadata/annotations/understudy.sh~1target-revision`

The baseline remains Git-managed. Unprotected workloads do not delegate replicas. Check these target declarations against rendered service resources whenever protection changes. Retire protection and its delegation together, after ending any active handover. Force/Replace synchronization is excluded; upstream evidence covers ordinary Argo apply.

Existing installations need the **0.4.4 CRD upgrade explicitly**; Helm does not upgrade schemas in `crds/`. The upstream [GitOps capacity guide](https://github.com/mitis-cloud/understudy/blob/v0.4.4/docs/gitops-capacity.md) describes the verified operator lifecycle. That evidence does not establish simultaneous Helmfile upgrades during a handover or certify this chart's live installation.

## Storage and secrets

`sharedStorage` selects an existing ReadWriteMany filesystem class, without changing the cluster default. Its driver, backing retention, and access-point policies remain infrastructure concerns. Existing bindings retain their single owner. Read the [storage guide](storage.md) before provisioning or adopting durable volumes.

`secretStore` identifies an existing ClusterSecretStore (default `aws-ssm`); `secretPrefix` carries the authoritative remote-key scope. ESO must be authorized to read it. Its asynchronous synchronization supplies required Secret keys. Reloader must be installed with support for the selected workload kind if `updates.reload` is enabled. Native checksums handle ordinary declared file changes; external secret values are never read by Helm.

## Availability zones

`placement` refers to an existing infrastructure-owned pool through its `env` node label and matching NoSchedule taint. Custom pool names are supported; the chart does not create pools. `architecture` can additionally constrain single-platform images. Infrastructure also owns accounts selected with `account`, their cloud permissions, and any required workload-identity admission/token injection. The chart does not manufacture IAM bindings or cloud credentials.

`availability.zones` generates strict native topology spreading with `maxSkew: 1`, `minDomains`, and `DoNotSchedule`. Revision labels distinguish rolling/canary generations. Canary keeps a full candidate replica pool while the stable pool serves, so additional capacity is required.

Omission adds no zone requirement, including for one instance. If an eligible zone is lost, the policy does not automatically relax into same-zone placement. Scheduling constraints do not continuously rebalance existing pods or prove application-level availability. Node provisioning and storage constraints must permit the requested spread.
