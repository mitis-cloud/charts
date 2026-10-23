# Roadmap

## Current release direction

`service:0.2.0` introduces consumption-first remote inputs, restricted formatting,
structured YAML/JSON files, explicit helper sharing and nullable optional jobs.
Product-owned vendor integrations use native Helm composition and the shared input
helpers; vendor-specific settings remain outside the reusable chart.

`service:0.3.0` adds service outputs: values a service writes once and publishes to the
secret store through External Secrets, read back with `{output: key}`.

`delivery:0.1.0` retains the independent Argo CD/Kargo definition. Controller-runtime
validation for promotion, canary traffic and planned-drain handover remains an
adopter integration step; schema validation alone does not establish it.

## Cloud provider profiles

Status: decided; the environment capability is implemented for AWS. See [ADR 0001](docs/adr/0001-implement-cloud-capabilities-with-crossplane.md) and [ADR 0002](docs/adr/0002-separate-environment-chart.md). No values below are accepted by the current schema yet.

Today the chart assumes AWS by default (AWS Load Balancer Controller ingress, ACM, Route53 private zones, an `aws-ssm` secret store) and leaves every cloud resource to separate infrastructure tooling. See [Prerequisites and boundary](docs/prerequisites.md#how-cloud-specific-the-charts-are-today).

Direction: keep the service interface cloud-neutral and implement each capability per provider inside Mitis, as Crossplane Compositions shipped in one Configuration package. The cluster selects the provider by installing only that provider's Compositions; no chart value names a cloud.

| Neutral capability | AWS | GCP | Azure |
| --- | --- | --- | --- |
| Public/private HTTP exposure | ALB via AWS Load Balancer Controller | GKE Gateway / Ingress | Application Gateway for Containers |
| Certificates | ACM | Certificate Manager | Key Vault certificates |
| DNS | Route53 (public and private zones) | Cloud DNS | Azure DNS |
| Remote inputs and service outputs | SSM Parameter Store / Secrets Manager | Secret Manager | Key Vault |
| Workload identity (deferred until a consumer exists) | EKS Pod Identity | GKE Workload Identity | Microsoft Entra Workload ID |
| Shared filesystem | EFS storage class | Filestore storage class | Azure Files storage class |

Resolved decisions:

- **Who creates cloud resources.** Crossplane, through Mitis Compositions. Per-cloud operators and chart-owned objects only were rejected.
- **Ownership and lifecycle.** Mitis owns service- and environment-lifecycle resources. Shared durable platform resources are referenced. Existing resources are adopted by external name, observed first, never recreated.
- **Parity scope.** AWS first. A provider is offered only when it implements every capability, and unsupported combinations fail validation.
- **Migration.** Adopting installations import their existing resources. Durable data keeps its backing.

Planned work:

- Remaining capabilities: certificates, DNS and service outputs through compositions. The `environment` chart and its AWS composition (namespace, exposure profiles, isolation, maintenance) are implemented ([environment chart](charts/environment/README.md)).
- Neutral naming for remote input references, which are currently written as `ssm` keys.

Each provider needs its own validated integration evidence. Rendering against schemas does not establish controller behavior.

The following proposed storage entries are **not supported chart values**. The
current schema rejects them. Add capabilities only with a concrete consumer and a
verified native lifecycle.

## Native StatefulSet services

Proposed direction:

```yaml
workload: stateful
storage:
  data:
    perInstance: true
    size: 20Gi
```

Deferred because the initial first-party Deployment inventory has no StatefulSet consumer. Vendor charts retain their own stateful support.

Before implementation, establish stable member/DNS identities, explicit private versus shared storage, retained per-member claims, startup ordering and peer discovery, readiness-aware rolling updates, and a supported growth/recovery procedure. An ordinary StatefulSet upgrade cannot change `volumeClaimTemplates`. Stable members do not implement replication or quorum.

Understudy 0.4.4 and Argo Rollouts do not manage StatefulSets. Do not imply their handover/canary behavior applies to this future mode.

## Exclusive block-backed Deployment storage

Proposed direction:

```yaml
capacity:
  instances: 1
storage:
  data:
    access: exclusive
    size: 20Gi
mounts:
  data: /var/lib/app
```

Deferred because the initial first-party durable Deployments use shared filesystems. Vendor products may already use EBS through their own charts.

This means a block-backed **mounted filesystem**, not a raw device. Establish native single-Pod access (RWOP, not RWO's single-node restriction), non-overlapping updates, independent-job restrictions, retained backing, AZ constraints, and native grow-only expansion. Reject surge handover, canary overlap, or multiple active instances using the same exclusive claim. A Deployment does not create a durable volume per replica.

Raw block-device access has no established requirement and is not implied by this entry.
