"""Validate examples against pinned upstream APIs and Kargo step schemas."""

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

import jsonschema
from referencing import Registry, Resource
import yaml

from test_contract import ROOT, render


CACHE = ROOT / ".cache/schemas"
RAW = "https://raw.githubusercontent.com"
CRDS = (
    [
        f"{RAW}/argoproj/argo-rollouts/v1.10.1/manifests/crds/{name}-crd.yaml"
        for name in ["rollout", "analysis-template"]
    ]
    + [
        f"{RAW}/argoproj/argo-cd/v3.5.5/manifests/crds/{name}-crd.yaml"
        for name in ["application", "applicationset", "appproject"]
    ]
    + [
        f"{RAW}/akuity/kargo/v1.12.4/charts/kargo/resources/crds/kargo.akuity.io_{name}.yaml"
        for name in [
            "projects",
            "projectconfigs",
            "warehouses",
            "stages",
            "promotiontasks",
        ]
    ]
    + [
        f"{RAW}/kubernetes-sigs/gateway-api/v1.6.3/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml",
        f"{RAW}/external-secrets/external-secrets/helm-chart-2.2.0/deploy/crds/bundle.yaml",
        f"{RAW}/mitis-cloud/understudy/v0.4.4/charts/understudy/crds/apps.understudy.sh_understudies.yaml",
    ]
)
STEP_BASE = f"{RAW}/akuity/kargo/v1.12.4/pkg/promotion/runner/builtin/schemas/"
STEPS = [
    "git-clone",
    "yaml-update",
    "json-parse",
    "yaml-parse",
    "file-write",
    "git-commit",
    "git-push",
    "argocd-update",
    "argocd-wait",
]


def merge_patch(target, patch):
    """Apply a template patch the way the ApplicationSet controller does: maps merge, lists replace."""
    if not isinstance(patch, dict) or not isinstance(target, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target)
    for key, value in patch.items():
        result[key] = merge_patch(result.get(key), value) if key in result else copy.deepcopy(value)
    return result


def download(url):
    path = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if not path.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read()
        path.write_bytes(content)
    return path.read_text()


def strict_schema(value):
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: strict_schema(item) for key, item in value.items()}
    if value.get("x-kubernetes-int-or-string"):
        result["type"] = ["integer", "string"]
    if value.get("properties") and not value.get(
        "x-kubernetes-preserve-unknown-fields"
    ):
        result.setdefault("additionalProperties", False)
    return result


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        manifests = list(pool.map(download, CRDS))
    schemas = {}
    for manifest in manifests:
        for crd in yaml.safe_load_all(manifest):
            if not crd or crd.get("kind") != "CustomResourceDefinition":
                continue
            for version in crd["spec"]["versions"]:
                schemas[
                    (
                        f"{crd['spec']['group']}/{version['name']}",
                        crd["spec"]["names"]["kind"],
                    )
                ] = strict_schema(version["schema"]["openAPIV3Schema"])

    registry = Registry()
    step_schemas = {}
    for filename in [f"{step}-config.json" for step in STEPS] + [
        "common.json",
        "argocd-common.json",
    ]:
        url = STEP_BASE + filename
        schema = json.loads(download(url))
        schema["$id"] = url
        step_schemas[filename] = schema
        registry = registry.with_resource(url, Resource.from_contents(schema))

    native = []
    applications = []
    count = 0
    for chart in ["service", "delivery", "webapp"]:
        for example in sorted((ROOT / "examples" / chart).glob("*.yaml")):
            for doc in render(ROOT / "charts" / chart, example):
                key = (doc["apiVersion"], doc["kind"])
                if key in schemas:
                    jsonschema.Draft7Validator(schemas[key]).validate(doc)
                elif "/" not in key[0] or key[0].split("/")[0] in [
                    "apps",
                    "batch",
                    "networking.k8s.io",
                    "rbac.authorization.k8s.io",
                ]:
                    native.append(doc)
                else:
                    raise AssertionError(f"No validation schema for {key}")
                if doc["kind"] == "Stage":
                    for step in doc["spec"]["promotionTemplate"]["spec"]["steps"]:
                        schema = step_schemas[f"{step['uses']}-config.json"]
                        jsonschema.Draft202012Validator(
                            schema, registry=registry
                        ).validate(step["config"])
                if doc["kind"] == "ApplicationSet":
                    spec = doc["spec"]
                    expanded = subprocess.run(
                        ["go", "run", str(ROOT / "tests/expand_templates.go")],
                        input=json.dumps(
                            {
                                "template": {
                                    "app": spec["template"],
                                    "patch": spec.get("templatePatch", ""),
                                },
                                "elements": spec["generators"][0]["list"]["elements"],
                            }
                        ),
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    for result in json.loads(expanded.stdout):
                        app = merge_patch(
                            result["app"], yaml.safe_load(result["patch"]) or {}
                        )
                        app.update(
                            apiVersion="argoproj.io/v1alpha1", kind="Application"
                        )
                        jsonschema.Draft7Validator(
                            schemas[(app["apiVersion"], app["kind"])]
                        ).validate(app)
                        assert "{{" not in json.dumps(app), (
                            "unresolved ApplicationSet template"
                        )
                        for source in app["spec"]["sources"]:
                            if "helm" in source:
                                files = source["helm"]["valueFiles"]
                                assert len(files) >= 2 and len(set(files)) == len(files)
                                assert all("//" not in file for file in files)
                        applications.append(app)
                if doc["kind"] == "AnalysisTemplate":
                    for metric in doc["spec"]["metrics"]:
                        native.append(
                            {
                                "apiVersion": "batch/v1",
                                "kind": "Job",
                                "metadata": {"name": "smoke-validation"},
                                **copy.deepcopy(metric["provider"]["job"]),
                            }
                        )
                count += 1
    subprocess.run(
        ["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.35.0"],
        input=yaml.safe_dump_all(native),
        text=True,
        check=True,
    )
    names = [app["metadata"]["name"] for app in applications]
    assert len(names) == len(set(names)), "duplicate generated Application names"
    assert set(names) == {
        "example-backend-test",
        "example-backend-production",
        "example-worker-test",
        "example-worker-production",
        "example-edge-test",
        "example-edge-production",
        "example-site-test",
        "example-site-production",
    }
    print(
        f"Expanded {len(applications)} Application templates using native Go text/template; generated Application schemas valid."
    )
    print(
        f"Validated {count} rendered resources against pinned APIs; native Kargo step configurations validated separately."
    )


if __name__ == "__main__":
    main()
