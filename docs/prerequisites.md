# Prerequisites and boundary

> The environment chart implements cloud capabilities through the Crossplane platform package. See [ADR 0001](adr/0001-implement-cloud-capabilities-with-crossplane.md) and [ADR 0002](adr/0002-separate-environment-chart.md).

Mitis charts are opinionated. Adopting them means adopting a defined platform: a set of controllers, CRDs and infrastructure conventions that must already exist in the cluster and the cloud account. Service and delivery charts use that platform; the environment chart requests the resources its services share through Crossplane. Charts do not install their prerequisite controllers.

Read this page before your first deployment. The [integration guide](integrations.md) goes deeper into each contract.

## Where the charts stop

| The charts render | Your platform provides |
| --- | --- |
| Deployments, Rollouts, Jobs, CronJobs | Controllers and CRDs: External Secrets, Reloader, AWS Load Balancer Controller, external-dns, Gateway API, Argo Rollouts, Understudy, Argo CD, Kargo |
| Services, Ingresses, HTTPRoutes | IngressClasses and IngressClassParams, Gateways and listeners, load balancers, certificates, DNS zones, WAF |
| ExternalSecrets, ConfigMaps, generated Secrets | The ClusterSecretStore, the secret backend and its contents, and read access to it |
| Shared-filesystem PersistentVolumeClaims | Storage classes, CSI drivers, filesystems, access points and pre-existing PersistentVolumes |
| ServiceAccounts, Roles and RoleBindings; environment storage and workload identity composites | Crossplane providers, provider permissions, cluster identity and shared external storage facts |
| Scheduling constraints: placement, zones, architecture | Node pools with matching labels and taints, spread across zones |
| Argo CD ApplicationSets and AppProjects; Kargo Projects, Warehouses, Stages and PromotionTasks (`delivery`) | Argo CD and Kargo installations, registered clusters, namespaces, Git/OCI/image credentials |

Rules that follow from this:

- **Ownership follows lifecycle.** Environment composites manage environment-owned databases, generated values, static sites, object storage and workload identities. Shared clusters, filesystems, DNS zones, secret backends and resources with external consumers remain with the platform's infrastructure owner.
- **No controller lifecycle.** The charts have no Helm dependencies on any controller chart. They don't install, configure or upgrade controllers or CRDs, even when a feature needs one.
- **Namespace-scoped by design.** The service chart renders no cluster-scoped objects; its permissions are namespace-scoped. The delivery chart's Kargo `Project` and the environment chart's `Environment` are the only cluster-scoped objects the charts render. Cluster-wide grants, IngressClasses, storage classes and secret stores belong to the platform.
- **Existing names are references.** Values such as `secretStore`, `sharedStorage`, `placement`, `account` and the ingress class naming convention point at objects that must already exist. A wrong name renders fine and fails at runtime.

## Always required

| Requirement | Why |
| --- | --- |
| Kubernetes **1.35 or newer** | Both charts declare `kubeVersion: >=1.35.0-0`; Helm refuses older clusters |
| Helm 4.3 (tested) | Rendering and strict values validation are tested with this version |

## Required by feature

The service chart's defaults switch some of these on. A **default** in the "Triggered by" column means a service needs the dependency unless it explicitly opts out.

| Dependency | Triggered by | You provide |
| --- | --- | --- |
| [External Secrets Operator](https://external-secrets.io) (`external-secrets.io/v1`; tested at 2.2.0) | Any `ssm` reference in `environment` or `files` | A ClusterSecretStore named by `secretStore` (default `aws-ssm`), ESO read access to the keys under `secretPrefix`, and the remote values themselves |
| [Stakater Reloader](https://github.com/stakater/Reloader) | **Default.** `updates.reload` and `updates.reloadSecrets` are both `true` | Reloader watching the namespace, with support for the workload kind (Deployment or Rollout). Without it, changed external secret values don't restart pods |
| [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/) | **Default** for any `http` exposure: `render.routing: ingress` | An IngressClass named `<namespace>-public` and/or `<namespace>-private`, each with IngressClassParams carrying the ALB group, scheme, WAF, inbound CIDRs and attributes. Issued ACM certificates covering every declared host |
| [external-dns](https://kubernetes-sigs.github.io/external-dns/) | Any `http` exposure in ingress mode | external-dns managing the target zones. Private exposure is annotated for a Route53 private hosted zone |
| ALB fixed-response actions | `response` services (maintenance pages) | AWS Load Balancer Controller. Ingress mode only |
| [Gateway API](https://gateway-api.sigs.k8s.io) v1 implementation (tested schema 1.6.3) | `render.routing: gateway` | A Gateway named `public` or `private` in the service's namespace, with an `https` listener, certificates and DNS. Also an `http` listener when `redirectFromHttp` is set |
| [Argo Rollouts](https://argoproj.github.io/rollouts/) with the [Gateway API traffic plugin](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io) | `updates.strategy: canary` (requires gateway routing) | Both controllers, plugin configuration and RBAC, and spare capacity for a full candidate pool |
| [Understudy](https://github.com/mitis-cloud/understudy) 0.4.4 | `disruption.surgeBeforeEviction: true` | The controller and the 0.4.4 CRDs. Helm doesn't upgrade CRDs, so upgrade them explicitly |
| ReadWriteMany storage class | Any `storage` entry with `access: shared` | A class named by `sharedStorage` (for example EFS CSI) and its backing filesystem. Pre-existing volumes for adopted data |
| Node pools | `placement` other than `any` | Nodes labelled `env=<pool>` with a matching `env=<pool>:NoSchedule` taint |
| Zone spread | `availability.zones` | Nodes labelled `topology.kubernetes.io/zone` across at least that many zones, with room to schedule in each |
| Architecture | `architecture` | Nodes with the matching `kubernetes.io/arch` |
| Workload identity | `account` or `tokens` | A ServiceAccount bound by an environment WorkloadIdentity or the infrastructure owner, and the cloud's admission/token injection |
| [Argo CD](https://argo-cd.readthedocs.io) | `render.engine: argo` | Argo CD applying the rendered sync waves and hooks |

### Delivery chart

`delivery` renders only Argo CD and Kargo objects, including the cluster-scoped Kargo `Project`. Everything it orchestrates must already exist:

- Argo CD (tested schema 3.5.5) and Kargo (tested 1.12.4) controllers and CRDs.
- Argo CD destination clusters registered under the names used by each environment's `cluster`.
- Existing application namespaces.
- Git, OCI and image-registry credentials for Argo CD and Kargo.
- A Gateway API implementation and per-namespace Gateways. Delivery's per-environment `routing` defaults to `gateway`, so services it deploys use gateway routing unless the environment sets `routing: ingress`.
- Argo Rollouts and the Gateway API traffic plugin, if any service uses canary.
- Understudy, if any service delegates capacity through `capacityTargets`.

See the [delivery reference](../charts/delivery/README.md) for activation order.

### Environment chart

`environment` renders one `Environment` composite. It needs Crossplane v2 (tested 2.4.3), the `ghcr.io/mitis-cloud/platform` package, the `mitis-platform` EnvironmentConfig and the RBAC grant described in the [environment reference](../charts/environment/README.md#platform-package). Once installed, it replaces the platform-provided IngressClasses and IngressClassParams for its namespace.

## How cloud-specific the charts are today

The service chart is **AWS-shaped in its default configuration**. The cloud-specific parts are:

- **Ingress routing** (the default): AWS Load Balancer Controller annotations, ACM certificate discovery, ALB fixed responses, and Route53 private-zone annotations for external-dns.
- **Secret store name**: the default `secretStore` is `aws-ssm`, and inputs are written as `ssm` references. ESO itself supports other providers. The `ssm` key maps to the ExternalSecret remote key, so a store backed by another provider works if its keys fit that model.

Gateway routing, storage, scheduling, jobs and the delivery chart contain no cloud-specific conventions. Mitis tracks [neutral cloud-provider support](../ROADMAP.md#cloud-provider-profiles) to remove the AWS assumption.

## What validation covers

`make check` validates rendering against pinned schemas for every integration above. It does not prove that your controllers are installed, configured or healthy, or that the cloud resources exist. Verify those in each environment before adopting a service.
