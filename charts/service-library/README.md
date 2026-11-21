# Service library

The templates behind the [service chart](../service/README.md), packaged as a Helm library chart. The service chart is a thin application chart over it. Use the library from a chart of your own when an application is several processes that belong in one release and one values file, or needs a few resources beside them. [ADR 0003](../../docs/adr/0003-service-templates-in-a-library-chart.md) records the decision.

```yaml
# Chart.yaml
dependencies:
  - name: service-library
    version: 0.2.0
    repository: oci://ghcr.io/mitis-cloud/charts
```

## Contract

| Template | Input | Result |
| --- | --- | --- |
| `mitis.service.render` | `(dict "root" $ "values" <service definition>)` | Every resource the service chart would render for that definition, as YAML documents. |
| `mitis.service.defaults` | none | The service chart's default values as YAML; `mitis.service.render` applies them before the definition. |
| `mitis.service.processInputs`, `mitis.service.inputResources` | see the [integration guide](../../docs/integrations.md#native-chart-composition) | Input Secrets and ConfigMaps for a workload the library does not render. |

A definition uses the [service vocabulary](../service/README.md) and passes the same semantic validation. Other named templates are internal and can change in any release.

- `name` identifies each service and defaults to the release name, so a chart rendering more than one must set it.
- The environment-supplied settings (`domain`, `facts`, `secretPrefix`, `secretStore`, `secretRefresh`, `sharedStorage`, `instanceStorage`, `placement`) and the orchestrator's `render` selectors are not inherited: pass them into every definition.
- The service chart's JSON schema does not apply to the maps a composing chart passes. That chart owns the schema of the values it exposes.
- Helm adds a `service-library` entry to the composing chart's values; a closed root schema has to allow it.

The [integration guide](../../docs/integrations.md#composing-services-in-a-product-chart) has a worked template.
