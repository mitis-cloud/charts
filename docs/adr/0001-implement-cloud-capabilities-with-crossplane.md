# Implement cloud capabilities with Crossplane provider compositions

Mitis charts expose one opinionated, cloud-neutral interface. Until now, adopters had to create every cloud resource that interface relied on and pass its identifiers in, which moved the complexity to them instead of hiding it. Mitis now implements each capability itself. Crossplane v2 is a required platform prerequisite. Mitis publishes one Crossplane Configuration package containing neutral composite resource definitions and one set of Compositions per cloud provider, written with function-go-templating. The charts render only neutral composite resources.

## Decisions

- **Use Crossplane as-is.** Mitis writes no controllers or provider code. Each cloud's implementation is a Composition built on the maintained Crossplane providers.
- **The cluster selects the provider.** A cluster installs only its own provider's Compositions. No service or environment value names a cloud, so the same definitions deploy unchanged on any supported provider.
- **Offer a provider only at full parity.** A provider is offered only when it implements every neutral capability and has its own integration evidence. Requesting an unsupported capability or provider fails validation instead of failing at reconcile time. AWS is the first provider.
- **Draw ownership by lifecycle.** Mitis owns resources that live and die with a service or an environment. Shared, durable platform resources such as clusters, filesystems, DNS zones, firewalls and secret backends stay with the platform and are referenced by name. A resource that is also consumed outside Mitis stays with its existing owner.
- **Adopt, never recreate.** Mitis adopts an existing resource by its external name, observing it first and taking control only after the observed state matches. Durable resources are never destroyed and rebuilt to change ownership.
- **Remove a need before abstracting it.** A service that only needs cloud identity to persist a value uses a service output pushed through the secret store instead. Workload identity stays unimplemented until a service needs it for something else.
- **Leave native capabilities native.** Shared storage keeps using the cluster's shared-filesystem storage class with deterministic per-claim directories. Crossplane adds nothing there.

## Considered options

- **Per-cloud operators** (AWS Controllers for Kubernetes, Azure Service Operator, Config Connector) rendered from chart template branches. Rejected because the adapters end up inside Helm templates and each cloud needs a different controller.
- **Chart-owned Kubernetes objects only**, with cloud resources created outside. Rejected because it keeps consumers passing cloud identifiers, which is the problem this decision solves.
- **Building our own controller.** Rejected because Crossplane's composition model already is the provider-adapter seam.
