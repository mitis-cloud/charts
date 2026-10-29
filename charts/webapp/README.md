# Webapp chart

Publishes one static site build to a `Webapp` of the [environment chart](../environment/README.md): it fetches the build, adds the environment's configuration, uploads it to the site's bucket and invalidates its CDN cache. Install one release per webapp, named after it. The chart creates no cloud resources and no permissions; the Webapp composite provides both.

## Contract with the Webapp

The release runs a Job as service account `<name>-publisher` and reads `bucket` and `distribution` from Secret `<name>-outputs`. The Webapp composite creates both and allows that account to upload to its bucket, invalidate its distribution and read the platform's artifact repositories, nothing else.

## Artifact

The build is a zip pushed to ECR as an OCI artifact, for example with `oras push <repository>:<tag> site.zip:application/zip`. The Job fetches its first layer through the ECR API, verifies its digest and unpacks it, so the pod needs neither a registry client nor registry credentials.

## Values

| Field | Meaning |
| --- | --- |
| `name` | The Webapp; defaults to the release name. |
| `artifact.repository`, `artifact.digest` | The zip artifact, as `<account>.dkr.ecr.<region>.amazonaws.com/<repository>` and its manifest digest. Promotion tooling updates the digest. |
| `source` | Folder inside the zip to publish, for example one theme of a shared assets zip; empty publishes the whole zip. |
| `prefix` | Key prefix inside the bucket; empty publishes at the root. |
| `replace` | Delete objects under `prefix` that the build no longer contains. Default `false`: application buckets keep older chunks for sessions still running the previous build. |
| `config` | An object published as `config.json` next to the build, or `null` for none. |
| `publishJob.placement` | Workload pool for the publish Job, using the `env` node label and taint as in the service chart. Default `spot`; `any` adds no constraint. The site itself is served from the bucket, not the cluster. |
| `timeout` | Job deadline, default `10m`. |
| `tools.aws`, `tools.unpack` | Pinned AWS CLI and BusyBox images. |
| `resources` | Container resources for each step. |

The Job is an Argo CD `Sync` hook and a Helm post-install/upgrade hook, so every sync republishes the current build and configuration. See [the example](../../examples/webapp/values.yaml).
