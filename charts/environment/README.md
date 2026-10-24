# Environment chart

> [!IMPORTANT]
> This chart renders one Crossplane composite resource. It needs Crossplane v2 and the Mitis platform package, installed and configured as described below.

An environment is one application namespace and everything its services share: the namespace itself, the exposure profiles that `http.exposure` selects, outbound isolation and the maintenance switch. Install one release per environment ([ADR 0002](../../docs/adr/0002-separate-environment-chart.md)). The chart renders a single cluster-scoped `Environment` (`mitis.cloud/v1alpha1`) named after its namespace; the platform package composes it for the cluster's provider ([ADR 0001](../../docs/adr/0001-implement-cloud-capabilities-with-crossplane.md)).

## Values

| Field | Meaning |
| --- | --- |
| `namespace` | Required. The application namespace, which the environment owns and is named after. It cannot change. |
| `exposures` | Profiles services may select: `public`, `private`, or both (default). |
| `isolation.enabled` | Restrict outbound traffic to the namespace itself, cluster DNS, the internet and `isolation.privatePorts` on platform private networks. Link-local addresses, such as instance metadata, are blocked. Inbound traffic is not restricted. |
| `isolation.privatePorts` | TCP ports reachable on platform private networks, such as a database port. |
| `maintenance.enabled` | Answer every request to `maintenance.hostnames` with the fixed response instead of reaching services. Requires the `public` exposure. |
| `maintenance.hostnames` | Public hostnames to take down. Required while enabled. |
| `maintenance.status`, `contentType`, `body` | The fixed response: 503, `text/plain` and a short message by default. `body` is limited to 1024 bytes. |

| `secrets.store`, `secrets.prefix` | The ClusterSecretStore holding the environment's values and their remote key prefix. Required with `databases`. |
| `secrets.generated` | Values by remote key suffix: `password` (default 32 characters with symbols), `string` (letters and digits, `length` required), `username` (lowercase letters), `manual` (a `-` placeholder a person replaces) or `value` (fixed content). Each is generated and published only when its remote key does not exist, so existing values are kept. |
| `webapps.<name>` | A static site at `hostname` (inside a platform DNS zone). `mode: application` (default) routes non-file paths to the single-page application and caches only `cachedPaths` (default `/assets/*`); `mode: files` serves stored files as they are, all cached, and ignores maintenance. It follows the environment's maintenance switch and body, and publishes its bucket and CDN identifiers as `<prefix><name>-website-bucket-id` and `<prefix><name>-cloudfront-website-id` for upload tooling. Requires `secrets`. |
| `databases.<key>` | A PostgreSQL database `<namespace>-<key>` on the platform's shared instance: `extensions`, `schemas` (per schema `operator` and `reader` access: `full`, `read`, `read-execute` or `none`; `existingFunctions`, `adminMaintain`) and `accounts` (name to `admin`, `operator` or `reader` group). |

See [the example](../../examples/environment/values.yaml) and [values.schema.json](values.schema.json).

Removing the release or its Argo CD Application leaves the `Environment` in place: deleting it deletes the namespace and every service in it. Delete the `Environment` itself only to retire the environment.

## Platform package

`ghcr.io/mitis-cloud/platform` is a Crossplane Configuration with the `Environment` definition and its provider compositions. It depends on `function-environment-configs`, `function-go-templating`, `function-auto-ready`, `provider-sql` and the AWS S3, CloudFront, ACM and Route53 providers. The platform then supplies:

- An `EnvironmentConfig` named `mitis-platform` with the cluster's provider facts. For AWS:

  ```yaml
  apiVersion: apiextensions.crossplane.io/v1beta1
  kind: EnvironmentConfig
  metadata:
    name: mitis-platform
  data:
    exposure:
      public:
        group: example-alb            # ALB group
        scheme: internet-facing
        attributes: {}                # optional ALB attributes, shared by the whole group
        wafAclArn: ''                 # optional
      private:
        group: example-alb-private
        scheme: internal
        inboundCIDRs: [192.0.2.0/24]  # optional
    privateNetworks: [198.51.100.0/24]
    region: eu-west-1                 # needed for webapps
    dns:
      zones:                          # public zones webapp hostnames live in, by domain
        example.invalid: Z0000000000EXAMPLE
    postgres:                         # shared PostgreSQL instance, needed for databases
      providerConfig: postgres        # provider-sql ClusterProviderConfig with superuser access
      adminRole: postgres             # the role that provider-sql connects as
      host: postgres.example.internal
      port: 5432
  ```

- Permission for Crossplane to compose plain Kubernetes and External Secrets objects, through an aggregated ClusterRole. Packages cannot carry RBAC.

  ```yaml
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRole
  metadata:
    name: crossplane-mitis-environment
    labels:
      rbac.crossplane.io/aggregate-to-crossplane: "true"
  rules:
    - apiGroups: [""]
      resources: [namespaces, secrets]
      verbs: [get, list, watch, create, update, patch, delete]
    - apiGroups: [networking.k8s.io]
      resources: [ingressclasses, ingresses, networkpolicies]
      verbs: [get, list, watch, create, update, patch, delete]
    - apiGroups: [elbv2.k8s.aws]
      resources: [ingressclassparams]
      verbs: [get, list, watch, create, update, patch, delete]
    - apiGroups: [external-secrets.io]
      resources: [externalsecrets, pushsecrets]
      verbs: [get, list, watch, create, update, patch, delete]
    - apiGroups: [generators.external-secrets.io]
      resources: [passwords]
      verbs: [get, list, watch, create, update, patch, delete]
  ```

- For databases, a provider-sql `ClusterProviderConfig` with superuser access to the instance, and a secret store that External Secrets can write (PushSecret).
- For webapps on AWS, the S3, CloudFront, ACM and Route53 providers with a `default` `ClusterProviderConfig` allowed to manage buckets, distributions, functions, certificates and records in those zones.

The package declares every function and provider it can use. A platform may install it with `skipDependencyResolution` and install only what its clusters need.

On AWS, the composition renders the namespace, an `IngressClassParams` and `IngressClass` named `<namespace>-<exposure>` for each exposure, the isolation `NetworkPolicy` and, while maintenance is enabled, a fixed-response `Ingress` ordered before every service rule. It names no certificate: the AWS Load Balancer Controller discovers ACM certificates from each Ingress's hosts. Isolation needs a CNI that enforces NetworkPolicies, such as the VPC CNI with network policy enabled.

Each database gets `admin`, `operator` and `reader` group roles and login roles `<database>-<account>`. Admin accounts act as the admin group, which owns the database, its schemas and whatever they create; default privileges extend each group's access to future objects. Account passwords come from `<prefix>pgsql-<key>-<account>-password` (with a matching `-url`), generated only when absent, and land in a Secret `<database>-<account>` (`username`, `password`, `url`) in the namespace. Crossplane never drops or revokes anything in a database: removing a database, an account or a grant from the values leaves it in place, and deleting one is a deliberate operation.

## Adopting existing resources

Crossplane takes over an existing object only when it already carries the annotation `crossplane.io/composition-resource-name` with the composition's name for it. Before creating an `Environment` over a namespace that exists, annotate each object it will compose:

| Object | Annotation value |
| --- | --- |
| Namespace `<namespace>` | `namespace` |
| IngressClassParams and IngressClass `<namespace>-<exposure>` | `<exposure>-parameters` and `<exposure>-class` |
| A webapp's buckets, identity, function, certificate, distribution and records | Pre-create each managed resource with its external name, the `<webapp>-<id>` name the composition uses, and `crossplane.io/composition-resource-name: <id>` |

Databases, roles and grants are adopted by name with no annotation: existing passwords stay, because they are read from the existing remote keys. Then stop the previous owner from managing them without deleting them.
