# Roadmap

## Current release direction

`service:0.2.0` introduces consumption-first remote inputs, restricted formatting,
structured YAML/JSON files, explicit helper sharing and nullable optional jobs.
Product-owned vendor integrations use native Helm composition and the shared input
helpers; vendor-specific settings remain outside the reusable chart.

`delivery:0.1.0` retains the independent Argo CD/Kargo definition. Controller-runtime
validation for promotion, canary traffic and planned-drain handover remains an
adopter integration step; schema validation alone does not establish it.

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
