# Mitis charts

Small, opinionated Helm charts for describing **services** and **delivery** without authoring Kubernetes manifests. Apache-2.0 licensed; each chart has its own version.

| Chart | Responsibility | OCI package |
| --- | --- | --- |
| [service](charts/service/README.md) | A continuous service, its inputs, storage, helpers, and jobs; or a resource-only service | `ghcr.io/mitis-cloud/charts/service` |
| [delivery](charts/delivery/README.md) | Argo CD applications and independent Kargo promotion units across explicit environments | `ghcr.io/mitis-cloud/charts/delivery` |

**Released:** `service:0.1.0` and `delivery:0.1.0` are public OCI packages. Both are published from tested Git tags through CI and can be pulled anonymously. Render validation does not certify controller/runtime behavior or a project's migration.

## Start with one service

```yaml
image:
  repository: traefik/whoami
  tag: v1.12.0
capacity:
  instances: 1
ports:
  http: 80
health:
  readiness: {http: /, port: http}
```

Render the working example from this checkout:

```sh
helm template api charts/service -f examples/service/api.yaml
```

Or pull the released chart:

```sh
helm pull oci://ghcr.io/mitis-cloud/charts/service --version 0.1.0
helm pull oci://ghcr.io/mitis-cloud/charts/delivery --version 0.1.0
```

Use named ports for internal access. Add `http` for external exposure, `environment` for configuration, and `storage`/`mounts` for files. Dedicated init containers, sidecars, and jobs stay in the same service file. An independent-purpose process gets its own file even when it uses the same image.

Values are direct and typed. Unknown fields fail validation. Strings are literal: the charts never run Helm `tpl` over application input. There is no raw-manifest or upstream-values escape hatch.

## Read the guides

- [Complete service reference](charts/service/README.md)
- [Storage: shared, existing, temporary, ownership and retention](docs/storage.md)
- [Helmfile, Argo CD, jobs and native layering](docs/deployment.md)
- [Infrastructure conventions and controller prerequisites](docs/integrations.md)
- [Delivery reference](charts/delivery/README.md)
- [Tracked roadmap](ROADMAP.md)

Use chart defaults → canonical service YAML → environment defaults → service/environment overrides. All first-party layers use the same vocabulary. Maps merge; lists replace. Set an environment-variable, storage, mount, or managed-secret value entry to `null` to omit it. Files supplied through Argo must be plain YAML; Argo does not evaluate Helmfile `.gotmpl` files.

Consume native vendor charts directly with their own values. A companion workload can use `service` separately where needed; using this toolkit does not require wrapping vendor products or combining their values into one file. The reusable service chart has no vendor or branding namespace.

## Develop and verify

Use Helm 4.3.0, kubeconform 0.8.0, Go 1.27.2, and Python 3.14 (the implementation's existing test interpreter). These tool pins and the current stable integration references were checked on 2026-10-14. Go supplies native template-expansion checks and builds kubeconform. Existing consumer/controller versions are compatibility constraints, not automatically upgraded by these charts.

```sh
python3 -m pip install -r requirements-test.txt
make check package
pre-commit run --all-files
```

`make check` exercises packaged examples, negative contracts, image inheritance, data ownership, engine-specific hooks, replica ownership, and promotion isolation. It validates Kubernetes 1.35 resources, selected pinned upstream CRD schemas, and Kargo's native step-configuration schemas. Upstream schemas are downloaded into ignored `.cache/`; no cluster or cloud credentials are needed.

Runtime readiness, traffic transitions, reloader behavior, ApplicationSet reconciliation, and live data adoption need separate environment evidence. The examples using `example.invalid` are explicit rendering fixtures with application-owned commands to replace before deployment; the whoami example uses a real pinned public image.

## Release

After required checks and review, increment only the affected chart's version, then tag the tested commit `service-v<version>` or `delivery-v<version>`. The tag workflow validates and packages the chart, rejects an existing version, pushes with GitHub Actions' package-scoped token, and pulls back the exact archive. Never overwrite a released tag/package; corrections need a new version.

The GHCR packages must be public for anonymous consumers. GitHub initially creates packages as private even for public source repositories: the repository/package owner sets visibility once, then an anonymous pull verifies it. Chart pins and application image pins are independent.
