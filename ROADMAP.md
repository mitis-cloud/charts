# Roadmap

## Current release direction

`service:0.2.0` introduces consumption-first remote inputs, restricted formatting,
structured YAML/JSON files, explicit helper sharing and nullable optional jobs.
Product-owned vendor integrations use native Helm composition and the shared input
helpers; vendor-specific settings remain outside the reusable chart.

`delivery:0.1.0` retains the independent Argo CD/Kargo definition. Controller-runtime
validation for promotion, canary traffic and planned-drain handover remains an
adopter integration step; schema validation alone does not establish it.

## Cloud provider profiles

Status: direction, not implemented. No values below are accepted by the current schema.

Today the chart assumes AWS by default (AWS Load Balancer Controller ingress, ACM, Route53 private zones, an `aws-ssm` secret store) and leaves every cloud resource to separate infrastructure tooling. See [Prerequisites and boundary](docs/prerequisites.md#how-cloud-specific-the-charts-are-today).

Direction: keep the service interface cloud-neutral and implement each capability per provider inside Mitis, selected by one platform setting, instead of deferring cloud-specific work to another tool.

```yaml
platform:
  provider: aws   # aws | gcp | azure
```

| Neutral capability | AWS | GCP | Azure |
| --- | --- | --- | --- |
| Public/private HTTP exposure | ALB via AWS Load Balancer Controller | GKE Gateway / Ingress | Application Gateway for Containers |
| Certificates | ACM | Certificate Manager | Key Vault certificates |
| DNS | Route53 (public and private zones) | Cloud DNS | Azure DNS |
| Remote inputs | SSM Parameter Store / Secrets Manager | Secret Manager | Key Vault |
| Workload identity | IRSA / EKS Pod Identity | GKE Workload Identity | Microsoft Entra Workload ID |
| Shared filesystem | EFS | Filestore | Azure Files |

Decisions required before implementation:

- **Who creates cloud resources.** Options are Kubernetes-native provider operators (AWS Controllers for Kubernetes, Config Connector, Azure Service Operator), a single cross-cloud control plane such as Crossplane, or chart-owned Kubernetes objects only (IngressClassParams, Gateways, SecretStores) with cloud resources still provisioned outside. The first two make the charts depend on another controller per cloud; the last keeps the current boundary.
- **Ownership and lifecycle.** Durable resources (certificates, DNS zones, filesystems, identities) must survive a Helm uninstall and must not be adopted from another owner implicitly.
- **Parity scope.** Which capabilities each provider must support before it is offered, and how unsupported combinations fail validation.
- **Migration.** Existing AWS installations must keep their current rendered resources when `platform.provider: aws` becomes explicit.

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
