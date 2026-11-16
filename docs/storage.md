# Storage for services and helpers

Storage is declared once by name; each container explicitly declares its own mounts. The same vocabulary applies to the main process, init containers, sidecars, and jobs.

## A service-owned shared filesystem

Environment defaults bind a supported shared-filesystem class:

```yaml
sharedStorage: efs-sc
```

The service definition stays about use:

```yaml
storage:
  uploads:
    access: shared
mounts:
  uploads: /srv/uploads
```

All Deployment instances use one claim and see the same files. The application must support concurrent access if instances overlap. One instance does not mean exclusive storage; existing singleton applications may deliberately use shared filesystems and overlapping rolling updates.

In AWS, EFS provisions an access point/directory in an existing infrastructure-owned filesystem. It does not provision a fixed-capacity filesystem per service. The optional `size` is **PVC request metadata, not an enforced EFS quota**; new declarations default internally to `1Gi`. Preserve an existing claim's request during adoption instead of changing bookkeeping. EFS access points do not reserve or cap capacity.

`name` pins the claim identity (otherwise `<service>-<storage-key>`). Never rename a durable claim as a cosmetic refactor: dynamic provisioning would create different backing and an empty directory. `retain` defaults to true; setting false is a deliberate deletion-policy change.

The binding is explicit because Kubernetes has one cluster-wide default StorageClass, not a default per access mode. Reusing an EBS default for `shared` does not work. The class must actually support ReadWriteMany filesystem access and the retention policy the platform promises. The chart does not install storage drivers or classes.

## Per-instance storage

A stateful workload gives each member its own volume:

```yaml
workload: stateful
instanceStorage: block
capacity:
  instances: 3
storage:
  data:
    access: instance
    size: 20Gi
mounts:
  data: /var/lib/app
```

`instanceStorage` is an environment default naming a class that provisions one ReadWriteOnce filesystem volume per claim; when omitted, the cluster's default class applies. Member `<service>-<ordinal>` uses claim `<storage-key>-<service>-<ordinal>`, for example `data-broker-0`. `name` on the entry replaces the storage key in that identity, which lets a service take over the claims of a workload that used another template name: a claim that already exists under the expected name is used as it is.

Claims are retained when the service is removed and when it is scaled down; a member that returns finds its volume again. They are provisioned on first start, usually in the zone where the member is scheduled, and then pin that member to the zone. Use `availability.nodes` to keep members on different nodes; a strict zone spread can conflict with volumes that already exist elsewhere.

Kubernetes does not let a running workload change its claim templates. To grow volumes, expand each claim through the storage class, then replace the workload object without deleting its members and apply the new `size`. Only the main process, its init containers and sidecars can mount per-instance storage; a job has no member identity.

## Existing or cross-service storage

```yaml
storage:
  content:
    existing: wordpress-wp-content
mounts:
  content: /var/www/html/wp-content
```

`existing` is a same-namespace claim reference, not a provisioning request. Its owner controls backing, class, access modes, sizing, retention, and backup. Consumers cannot change those properties with this declaration. Offline rendering cannot prove the existing claim supports the intended concurrency.

Declaring `access: shared` separately in two services does **not** connect them: it normally creates different claims/directories. Cross-service consumers intentionally reference the same owned binding. For storage shared with external systems such as SFTP, infrastructure owns a stable backing/access point and its binding; do not substitute a dynamic access point whose path changes.

## Temporary disk or RAM workspace

```yaml
storage:
  scratch:
    temporary: true
    sizeLimit: 1Gi
  work:
    temporary: true
    medium: memory
    sizeLimit: 64Mi
mounts:
  scratch: /tmp
  work: /work
init:
  prepare:
    command: [/app/prepare]
    mounts:
      work: /work
```

`medium` defaults to `disk`; `memory` uses RAM. Each pod gets its own workspace, shared only with colocated containers that explicitly mount it. Container restarts in that pod retain the workspace; replacing the pod discards it. An independent job has a different workspace even when its logical storage name matches.

`sizeLimit` is a limit, not a reservation. Memory-backed files count against memory usage; disk limits do not reserve node space and pressure can cause eviction. These are not durable uploads or database storage.

## Mount options

```yaml
mounts:
  content: /content
  cache:
    path: /app/.cache
    subdirectory: app/.cache
    readOnly: true
```

The string form is the normal read/write directory mount. The object form has required `path`, optional `readOnly` (default false), and optional relative `subdirectory`. Parent traversal is rejected. Read-only use is per container; it does not change the volume's shared access capability. Duplicate mount targets and unknown volume names fail validation.

Configuration and secret-backed files use `files`, not durable storage. They have their own source and restart semantics in the [service reference](../charts/service/README.md).

## Retention and lifecycle

Service-owned durable claims receive Helm `keep` and Argo `Prune=false,Delete=false` by default. Existing claims keep their owner's policy. These controls are not backups or protection from namespace deletion, explicit PVC deletion, or deletion of cloud backing. A PV reclaim policy of Retain is a separate infrastructure guarantee and may require explicit recovery/rebinding after claim deletion.

Do not change access, identity, or backing during routine adoption. Moving a binding between release owners needs a reviewed ownership-transfer operation; two active owners are invalid. No storage resizing machinery is included. EFS request metadata is not a capacity operation; existing volumes remain owner-managed.

## Explicitly deferred

[ROADMAP.md](../ROADMAP.md) records exclusive block-backed Deployment storage. The schema rejects `access: exclusive`. An EBS-backed directory is a filesystem on a block device; literal raw-device access is a separate unsupported requirement. Vendor charts retain their own storage capabilities.
