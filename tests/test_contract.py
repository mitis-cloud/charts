import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def documents(text):
    return [doc for doc in yaml.load_all(text, Loader=UniqueLoader) if doc]


def render(chart, *values, success=True):
    with tempfile.TemporaryDirectory() as directory:
        command = ["helm", "template", "sample", str(chart), "--namespace", "example"]
        for i, value in enumerate(values):
            if isinstance(value, (Path, str)):
                path = value
            else:
                path = Path(directory) / f"values-{i}.json"
                path.write_text(json.dumps(value))
            command += ["-f", str(path)]
        result = subprocess.run(command, text=True, capture_output=True)
        if success:
            if result.returncode:
                raise AssertionError(result.stderr)
            docs = documents(result.stdout)
            identities = [
                (d["apiVersion"], d["kind"], d["metadata"]["name"]) for d in docs
            ]
            if len(identities) != len(set(identities)):
                raise AssertionError(f"duplicate resource identities: {identities}")
            return docs
        if not result.returncode:
            raise AssertionError("invalid values were accepted")
        if (
            "values don't meet" not in result.stderr
            and "execution error at" not in result.stderr
        ):
            raise AssertionError(
                f"failure was not contract validation: {result.stderr}"
            )
        return result.stderr


def resource(docs, kind, name=None):
    return next(
        d
        for d in docs
        if d["kind"] == kind and (not name or d["metadata"]["name"] == name)
    )


def expand_inputs(data, parameters):
    result = subprocess.run(
        ["go", "run", "."],
        cwd=ROOT / "tests/eso",
        input=json.dumps({"data": data, "parameters": parameters}),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def expand_secret(secret, remote):
    parameters = {
        item["secretKey"]: remote[item["remoteRef"]["key"]]
        for item in secret["spec"]["data"]
    }
    return expand_inputs(secret["spec"]["target"]["template"]["data"], parameters)


class ServiceContract(unittest.TestCase):
    chart = ROOT / "charts/service"
    base = {"image": {"repository": "example.invalid/service", "tag": "fixture"}}

    def test_published_package_shape_and_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                ["helm", "package", str(self.chart), "-d", directory],
                check=True,
                capture_output=True,
            )
            package = next(Path(directory).glob("*.tgz"))
            for path in sorted((ROOT / "examples/service").glob("*.yaml")):
                with self.subTest(example=path.name):
                    source = render(self.chart, path)
                    packaged = render(package, path)
                    self.assertEqual(source, packaged)
                    subprocess.run(
                        ["helm", "lint", "--strict", str(package), "-f", str(path)],
                        check=True,
                        capture_output=True,
                    )

    def test_success_cleanup_and_failed_result_ttl_are_independent(self):
        values = {
            "workload": "resources",
            "jobs": {
                "setup": {
                    "run": "after-ready",
                    "command": ["setup"],
                    "cleanup": "on-success",
                    "resultLifetime": "5m",
                }
            },
        }
        job = resource(render(self.chart, self.base, values), "Job")
        self.assertEqual(job["spec"]["ttlSecondsAfterFinished"], 300)
        self.assertEqual(
            job["metadata"]["annotations"]["helm.sh/hook-delete-policy"],
            "before-hook-creation,hook-succeeded",
        )
        render(
            self.chart,
            self.base,
            values,
            {"jobs": {"setup": {"cleanup": "10m"}}},
            success=False,
        )

    def test_private_ingress_uses_private_dns_zone(self):
        base = {
            "ports": {"http": 8080},
            "http": {"hostname": "internal.example.org", "exposure": "private"},
        }
        ingress = resource(render(self.chart, self.base, base), "Ingress")
        self.assertEqual(
            ingress["metadata"]["annotations"][
                "external-dns.alpha.kubernetes.io/route53-private-zone"
            ],
            "true",
        )

    def test_unknown_and_future_fields_fail(self):
        for invalid in [
            {"mitis": {}},
            {"rawResources": {}},
            {"workload": "stateful"},
            {"capacity": {"instnaces": 2}},
            {"storage": {"disk": {"access": "exclusive", "size": "20Gi"}}},
            {"storage": {"disk": {"existing": "other", "size": "20Gi"}}},
            {"storage": {"disk": {"access": "shared", "perInstance": True}}},
        ]:
            with self.subTest(invalid=invalid):
                render(self.chart, self.base, invalid, success=False)

    def test_environment_layers_can_remove_optional_storage_and_secret_keys(self):
        service = {
            "storage": {"assets": {"existing": "shared-assets"}},
            "mounts": {"assets": "/srv"},
            "environment": {
                "REQUIRED": {"ssm": "required"},
                "OPTIONAL": {"ssm": "optional"},
            },
        }
        override = {
            "storage": {"assets": None},
            "mounts": {"assets": None},
            "environment": {"OPTIONAL": None},
        }
        docs = render(self.chart, self.base, service, override)
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertNotIn("volumes", pod)
        self.assertNotIn("volumeMounts", pod["containers"][0])
        self.assertEqual([e["name"] for e in pod["containers"][0]["env"]], ["REQUIRED"])
        self.assertEqual(
            [
                d["remoteRef"]["key"]
                for d in resource(docs, "ExternalSecret")["spec"]["data"]
            ],
            ["required"],
        )
        render(
            self.chart, self.base, service, {"storage": {"assets": None}}, success=False
        )
        empty = render(
            self.chart,
            self.base,
            service,
            override,
            {"environment": {"REQUIRED": None}},
        )
        self.assertFalse(any(d["kind"] == "ExternalSecret" for d in empty))
        self.assertNotIn(
            "env",
            resource(empty, "Deployment")["spec"]["template"]["spec"]["containers"][0],
        )

    def test_explicit_manual_task_can_share_its_resource_only_release(self):
        service = {
            "workload": "resources",
            "jobs": {
                "reset": {
                    "run": "manual",
                    "command": ["reset"],
                    "environment": {"TOKEN": {"ssm": "token"}},
                },
                "other": {"run": "manual", "command": ["other"]},
            },
        }
        ordinary = render(self.chart, self.base, service)
        self.assertEqual([d["kind"] for d in ordinary], ["ExternalSecret"])
        selected = render(self.chart, self.base, service, {"render": {"task": "reset"}})
        self.assertEqual([d["kind"] for d in selected], ["ExternalSecret", "Job"])
        self.assertEqual(resource(selected, "Job")["metadata"]["name"], "sample-reset")
        self.assertEqual(
            resource(selected, "Job")["metadata"]["annotations"]["helm.sh/hook"],
            "post-install,post-upgrade",
        )
        render(
            self.chart,
            self.base,
            service,
            {"render": {"task": "missing"}},
            success=False,
        )

    def test_projected_identity_tokens_rotate_and_are_explicit(self):
        values = {
            "account": "identity-client",
            "apiToken": False,
            "tokens": {
                "federation": {
                    "audience": "sts.amazonaws.com",
                    "path": "/var/run/identity",
                    "lifetime": 86400,
                }
            },
            "sidecars": {"observer": {"command": ["observe"]}},
            "jobs": {
                "verify": {
                    "run": "after-ready",
                    "account": "verifier",
                    "tokens": {
                        "check": {"audience": "verification", "path": "/var/run/check"}
                    },
                }
            },
        }
        docs = render(self.chart, self.base, values)
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "identity-client")
        self.assertFalse(pod["automountServiceAccountToken"])
        projection = pod["volumes"][0]["projected"]["sources"][0]["serviceAccountToken"]
        self.assertEqual(
            projection,
            {
                "audience": "sts.amazonaws.com",
                "expirationSeconds": 86400,
                "path": "token",
            },
        )
        mount = pod["containers"][0]["volumeMounts"][0]
        self.assertTrue(mount["readOnly"])
        self.assertNotIn("subPath", mount)
        self.assertNotIn("volumeMounts", pod["containers"][1])
        job = resource(docs, "Job")["spec"]["template"]["spec"]
        self.assertEqual(job["serviceAccountName"], "verifier")
        self.assertEqual(
            job["volumes"][0]["projected"]["sources"][0]["serviceAccountToken"][
                "expirationSeconds"
            ],
            3600,
        )
        for override in [
            {"tokens": {"federation": {"path": "/"}}},
            {"tokens": {"federation": {"path": "/var/../identity"}}},
            {"tokens": {"federation": {"lifetime": 599}}},
            {"tokens": {"federation": {"audience": ""}}},
            {
                "files": {
                    "overlap": {
                        "path": "/var/run/identity/config",
                        "content": "literal",
                    }
                }
            },
            {"workload": "resources"},
        ]:
            with self.subTest(override=override):
                render(self.chart, self.base, values, override, success=False)

    def test_fixed_response_is_bounded_and_ingress_only(self):
        values = ROOT / "examples/service/response.yaml"
        docs = render(self.chart, values)
        self.assertEqual([d["kind"] for d in docs], ["Ingress"])
        ingress = docs[0]
        action = json.loads(
            ingress["metadata"]["annotations"][
                "alb.ingress.kubernetes.io/actions.maintenance"
            ]
        )
        self.assertEqual(action["FixedResponseConfig"]["StatusCode"], "503")
        self.assertEqual(
            ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"],
            {"name": "maintenance", "port": {"name": "use-annotation"}},
        )
        for invalid in [
            {"response": {"status": 302}},
            {"response": {"body": "é" * 600}},
            {"render": {"routing": "gateway"}},
            {"http": {"port": "http"}},
            {"workload": "deployment", **self.base},
        ]:
            with self.subTest(invalid=invalid):
                render(self.chart, values, invalid, success=False)

    def test_structured_inputs_preserve_types_and_treat_strings_as_data(self):
        literal = '"{{ fail "not code" }}"\n\\'
        values = {
            "workload": "resources",
            "facts": {"enabled": False, "count": 0},
            "files": {
                "config": {
                    "path": "/config.json",
                    "json": {
                        "enabled": {"fact": "enabled"},
                        "count": {"fact": "count"},
                        "literal": literal,
                        "password": {"ssm": "password"},
                        "items": [None, True, 7, "7"],
                    },
                }
            },
        }
        secret = resource(render(self.chart, values), "ExternalSecret")
        password = '"newline\n{{ .unsafe }}\\'
        output = expand_secret(secret, {"password": password})
        self.assertEqual(
            json.loads(output["config"]),
            {
                "enabled": False,
                "count": 0,
                "literal": literal,
                "password": password,
                "items": [None, True, 7, "7"],
            },
        )
        render(self.chart, values, {"facts": {"enabled": None}}, success=False)
        render(
            self.chart,
            values,
            {
                "files": {
                    "config": {"json": {"password": {"ssm": "password", "extra": True}}}
                }
            },
            success=False,
        )

    def test_explicit_job_security_and_retention(self):
        docs = render(self.chart, ROOT / "examples/service/scheduled-connector.yaml")
        cron = resource(docs, "CronJob")
        self.assertEqual(cron["spec"]["timeZone"], "Etc/UTC")
        job = cron["spec"]["jobTemplate"]["spec"]
        self.assertEqual(job["activeDeadlineSeconds"], 240)
        pod = job["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"kubernetes.io/arch": "amd64"})
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(
            pod["securityContext"],
            {"fsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
        )
        self.assertEqual(
            pod["containers"][0]["securityContext"],
            {
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "runAsNonRoot": True,
                "readOnlyRootFilesystem": True,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        )
        values = {
            "workload": "resources",
            "jobs": {
                "configure": {
                    "image": self.base["image"],
                    "run": "after-ready",
                    "timeout": "none",
                    "cleanup": "5m",
                    "discovery": "environment",
                    "account": "cloud-worker",
                }
            },
        }
        job = resource(render(self.chart, values), "Job")
        self.assertNotIn("activeDeadlineSeconds", job["spec"])
        self.assertEqual(job["spec"]["ttlSecondsAfterFinished"], 300)
        self.assertEqual(
            job["metadata"]["annotations"]["helm.sh/hook-delete-policy"],
            "before-hook-creation",
        )
        self.assertTrue(job["spec"]["template"]["spec"]["enableServiceLinks"])
        self.assertEqual(
            job["spec"]["template"]["spec"]["serviceAccountName"], "cloud-worker"
        )
        render(
            self.chart,
            self.base,
            {"security": "restricted", "identity": {"user": 0}},
            success=False,
        )
        render(
            self.chart,
            self.base,
            {
                "account": "existing",
                "permissions": [
                    {"group": "", "resources": ["pods"], "actions": ["get"]}
                ],
            },
            success=False,
        )

    def test_zero_and_optional_availability(self):
        for replicas in [0, 1, 3]:
            deployment = resource(
                render(self.chart, self.base, {"capacity": {"instances": replicas}}),
                "Deployment",
            )
            self.assertEqual(replicas, deployment["spec"]["replicas"])
            pod = deployment["spec"]["template"]["spec"]
            self.assertNotIn("topologySpreadConstraints", pod)
            self.assertNotIn("cpu", pod["containers"][0]["resources"]["limits"])
        render(self.chart, self.base, {"availability": {"zones": 2}}, success=False)

    def test_inputs_helpers_and_whole_image_inheritance(self):
        values = copy.deepcopy(self.base)
        values.update(
            {
                "environment": {"PRIVATE": {"ssm": "private"}},
                "secretPrefix": "test-",
                "storage": {"work": {"temporary": True}},
                "mounts": {"work": "/work"},
                "init": {"prepare": {"command": ["prepare"]}},
                "sidecars": {
                    "read": {
                        "image": {
                            "repository": "example.invalid/reader",
                            "tag": "independent",
                        }
                    }
                },
            }
        )
        values["image"]["digest"] = "sha256:" + "a" * 64
        docs = render(self.chart, values)
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(
            pod["containers"][0]["image"], pod["initContainers"][0]["image"]
        )
        self.assertEqual(
            "example.invalid/reader:independent", pod["containers"][1]["image"]
        )
        self.assertNotIn("env", pod["initContainers"][0])
        self.assertNotIn("volumeMounts", pod["initContainers"][0])
        self.assertEqual(
            "test-private",
            resource(docs, "ExternalSecret")["spec"]["data"][0]["remoteRef"]["key"],
        )
        values["init"]["prepare"]["image"] = {"tag": "not-a-partial-override"}
        render(self.chart, values, success=False)

    def test_storage_identity_and_literal_files(self):
        docs = render(self.chart, ROOT / "examples/service/storage.yaml")
        claim = resource(docs, "PersistentVolumeClaim")
        self.assertEqual("efs-sc", claim["spec"]["storageClassName"])
        self.assertEqual(["ReadWriteMany"], claim["spec"]["accessModes"])
        self.assertEqual(
            "keep", claim["metadata"]["annotations"]["helm.sh/resource-policy"]
        )
        self.assertIn(
            "{{ do_not_evaluate_this }}",
            next(iter(resource(docs, "ConfigMap")["data"].values())),
        )
        self.assertEqual(1, sum(d["kind"] == "PersistentVolumeClaim" for d in docs))

    def test_false_facts_and_apex_routes(self):
        docs = render(
            self.chart,
            self.base,
            {
                "facts": {"flag": False, "zero": 0},
                "environment": {"FLAG": {"fact": "flag"}, "ZERO": {"fact": "zero"}},
                "domain": "example.com",
                "ports": {"http": 80},
                "http": {
                    "exposure": "public",
                    "subdomain": "",
                    "priority": 200,
                    "redirectFromHttp": True,
                },
            },
        )
        env = resource(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0][
            "env"
        ]
        self.assertEqual(
            {"FLAG": "false", "ZERO": "0"}, {e["name"]: e["value"] for e in env}
        )
        ingress = resource(docs, "Ingress")
        self.assertEqual("example.com", ingress["spec"]["rules"][0]["host"])
        self.assertEqual(
            "200",
            ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/group.order"],
        )
        self.assertEqual(
            [{"HTTP": 80}, {"HTTPS": 443}],
            json.loads(
                ingress["metadata"]["annotations"][
                    "alb.ingress.kubernetes.io/listen-ports"
                ]
            ),
        )

    def test_coordinated_jobs_run_after_prior_services(self):
        docs = render(
            self.chart,
            ROOT / "examples/service/jobs.yaml",
            {"render": {"engine": "argo", "order": 1}},
        )
        for doc in docs:
            wave = int(doc["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
            self.assertGreater(wave, 19)
            if doc["kind"] == "CronJob":
                self.assertEqual(110, wave)

    def test_invalid_references_and_overlap(self):
        for invalid in [
            {"mounts": {"missing": "/data"}},
            {
                "storage": {"work": {"temporary": True}},
                "mounts": {"work": {"path": "/data", "subdirectory": "a/../b"}},
            },
            {"updates": {"strategy": "replace"}, "disruption": {"handover": True}},
            {"updates": {"strategy": "canary"}},
            {"health": {"readiness": {"tcp": "missing"}}},
            {"workload": "resources", "command": ["silently-ignored"]},
            {"capacity": {"cpu": "banana"}},
            {"capacity": {"cpu": -1}},
            {"jobs": {"cron": {"schedule": "* * * * *", "cleanup": "next-run"}}},
            {"jobs": {"once": {"run": "before", "history": {"failed": 1}}}},
        ]:
            with self.subTest(invalid=invalid):
                render(self.chart, self.base, invalid, success=False)

    def test_native_layering_replaces_lists_and_removes_nulls(self):
        docs = render(
            self.chart,
            self.base,
            {"args": ["one", "two"], "environment": {"KEEP": "a", "DROP": "b"}},
            {"args": ["three"], "environment": {"DROP": None}},
        )
        container = resource(docs, "Deployment")["spec"]["template"]["spec"][
            "containers"
        ][0]
        self.assertEqual(["three"], container["args"])
        self.assertEqual([{"name": "KEEP", "value": "a"}], container["env"])

    def test_stable_split_has_no_duplicate_owners(self):
        path = ROOT / "examples/service/storage.yaml"
        all_docs = render(self.chart, path)
        inputs = render(self.chart, path, {"render": {"part": "inputs"}})
        runtime = render(self.chart, path, {"render": {"part": "runtime"}})
        identity = lambda d: (d["kind"], d["metadata"]["name"])
        self.assertFalse(set(map(identity, inputs)) & set(map(identity, runtime)))
        self.assertEqual(
            set(map(identity, all_docs)), set(map(identity, inputs + runtime))
        )

    def test_scheduled_helpers_keep_a_separate_release_owner(self):
        path = ROOT / "examples/service/jobs.yaml"
        application = render(self.chart, path, {"render": {"part": "application"}})
        scheduled = render(self.chart, path, {"render": {"part": "scheduled"}})
        self.assertEqual(["CronJob"], [doc["kind"] for doc in scheduled])
        combined = application + scheduled
        identity = lambda doc: (doc["kind"], doc["metadata"]["name"])
        self.assertEqual(
            sorted(map(identity, render(self.chart, path))),
            sorted(map(identity, combined)),
        )

    def test_job_gates_and_manual_exclusion(self):
        path = ROOT / "examples/service/jobs.yaml"
        helm = render(self.chart, path)
        self.assertFalse(any(d["metadata"]["name"] == "migrations-reset" for d in helm))
        job = resource(helm, "Job")
        self.assertEqual(
            "post-install,post-upgrade", job["metadata"]["annotations"]["helm.sh/hook"]
        )
        argo = render(self.chart, path, {"render": {"engine": "argo"}})
        self.assertEqual(
            "Sync",
            resource(argo, "Job")["metadata"]["annotations"]["argocd.argoproj.io/hook"],
        )
        task = render(self.chart, path, {"render": {"part": "task", "task": "reset"}})
        self.assertEqual(["migrations-reset"], [d["metadata"]["name"] for d in task])
        before = {"jobs": {"migrate": {"run": "before"}}}
        render(self.chart, path, before, success=False)
        render(self.chart, path, before, {"render": {"part": "runtime"}})

    def test_external_capacity_leaves_the_understudy_baseline_to_the_scaler(self):
        canary = ROOT / "examples/service/canary.yaml"
        pinned = render(self.chart, canary, {"render": {"engine": "argo"}})
        self.assertIn("baselineReplicas", resource(pinned, "Understudy")["spec"])
        external = render(self.chart, canary, {"render": {"engine": "argo", "capacity": "external"}})
        self.assertNotIn("baselineReplicas", resource(external, "Understudy")["spec"])

    def test_canary_has_initial_smoke_and_scoped_capacity(self):
        docs = render(self.chart, ROOT / "examples/service/canary.yaml")
        rollout = resource(docs, "Rollout")
        steps = rollout["spec"]["strategy"]["canary"]["steps"]
        self.assertEqual(
            [0, 10, 50, 100], [s["setWeight"] for s in steps if "setWeight" in s]
        )
        self.assertEqual(3, sum("analysis" in s for s in steps))
        self.assertEqual(
            0, resource(docs, "AnalysisTemplate")["spec"]["metrics"][0]["failureLimit"]
        )
        self.assertEqual(
            rollout["spec"]["replicas"],
            resource(docs, "Understudy")["spec"]["baselineReplicas"],
        )
        self.assertEqual(
            "10",
            resource(docs, "Job")["metadata"]["annotations"][
                "argocd.argoproj.io/sync-wave"
            ],
        )


class DeliveryContract(unittest.TestCase):
    chart = ROOT / "charts/delivery"
    example = ROOT / "examples/delivery/platform.yaml"

    def test_one_warehouse_per_unit_and_one_stage_per_environment(self):
        docs = render(self.chart, self.example)
        warehouses = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "Warehouse"}
        self.assertEqual({"backend", "site", "worker"}, set(warehouses))
        images = lambda unit: [s["image"]["repoURL"] for s in warehouses[unit]["subscriptions"] if "image" in s]
        self.assertEqual(images("backend"), ["ghcr.io/example/backend", "ghcr.io/example/database"])
        self.assertEqual(images("site"), ["ghcr.io/example/assets", "ghcr.io/example/portal"])
        stages = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Stage"}
        self.assertEqual({"test", "production"}, set(stages))
        # One slot per unit the environment runs, each from its own Warehouse.
        slots = stages["production"]["spec"]["requestedFreight"]
        self.assertEqual([r["origin"]["name"] for r in slots], ["backend", "site", "worker"])
        self.assertTrue(all(r["sources"] == {"stages": ["test"]} for r in slots))
        values = yaml.safe_load(self.example.read_text())
        values["environments"]["production"]["units"] = {"backend": ["api", "migrations"]}
        only_backend = render(self.chart, values)
        self.assertEqual([r["origin"]["name"] for r in resource(only_backend, "Stage", "production")["spec"]["requestedFreight"]], ["backend"])
        # A promotion touches only the unit whose Freight it moves.
        steps = stages["production"]["spec"]["promotionTemplate"]["spec"]["steps"]
        gate = lambda unit: '${{ ctx.targetFreight.origin.name == "%s" }}' % unit
        updates = {s["config"]["path"]: s.get("if") for s in steps if s["uses"] == "yaml-update"}
        self.assertEqual(updates, {
            "./repo/environments/production/api.yaml": gate("backend"),
            "./repo/environments/production/migrations.yaml": gate("backend"),
            "./repo/environments/production/worker.yaml": gate("worker"),
        })
        api = next(s for s in steps if s["uses"] == "yaml-update" and s["config"]["path"].endswith("/api.yaml"))
        self.assertEqual(api["config"]["updates"][0]["value"], '${{ imageFrom("ghcr.io/example/backend", warehouse("backend")).Digest }}')
        syncs = [(s["config"]["apps"][0]["name"], s["if"]) for s in steps if s["uses"] == "argocd-update"]
        self.assertEqual(syncs, [("example-%s-production" % u, gate(u)) for u in ["backend", "site", "worker"]])
        self.assertTrue(all("if" not in s for s in steps if s["uses"] in ("git-commit", "git-push")))
        # Health must not pin the shared branch to one promotion's commit.
        self.assertTrue(all("sources" not in a for s in steps if s["uses"] == "argocd-update" for a in s["config"]["apps"]))
        self.assertFalse([d for d in docs if d["kind"] == "PromotionTask"])
        app = resource(docs, "ApplicationSet", "example-backend")["spec"]["template"]
        self.assertEqual(app["metadata"]["annotations"]["kargo.akuity.io/authorized-stage"], "example:{{ .environment }}")
        ignores = self.expanded_spec(docs, "worker", "production")["ignoreDifferences"]
        self.assertFalse(any("/spec/replicas" in rule.get("jsonPointers", []) for rule in ignores))

    def test_stage_colors_follow_the_first_matching_rule(self):
        rules = {"stageColors": [{"match": "^prod", "color": "red"}, {"match": "^(test|prod)", "color": "green"}]}
        stages = {d["metadata"]["name"]: d for d in render(self.chart, self.example, rules) if d["kind"] == "Stage"}
        color = lambda s: s["metadata"].get("annotations", {}).get("kargo.akuity.io/color")
        self.assertEqual(color(stages["test"]), "green")
        self.assertEqual(color(stages["production"]), "red")
        unmatched = render(self.chart, self.example, {"stageColors": [{"match": "^staging", "color": "yellow"}]})
        self.assertTrue(all("annotations" not in d["metadata"] for d in unmatched if d["kind"] == "Stage"))

    def test_git_values_promote_files_at_the_freight_commit(self):
        docs = render(self.chart, self.example)
        warehouse = resource(docs, "Warehouse", "site")["spec"]
        self.assertIn(
            {"git": {"repoURL": "https://github.com/example/web.git", "branch": "main", "commitSelectionStrategy": "NewestFromBranch", "discoveryLimit": 20}},
            warehouse["subscriptions"],
        )
        self.assertEqual(
            warehouse["freightCreationCriteria"]["expression"],
            "imageFrom('ghcr.io/example/portal').Tag == commitFrom('https://github.com/example/web.git').ID[0:7]",
        )
        stage = resource(docs, "Stage", "test")["spec"]["promotionTemplate"]["spec"]
        self.assertEqual(stage["vars"], [{"name": "stage", "value": "test"}])
        steps = stage["steps"]
        clone = next(s for s in steps if s["uses"] == "git-clone" and s["config"]["repoURL"] == "https://github.com/example/web.git")
        self.assertEqual(clone["if"], '${{ ctx.targetFreight.origin.name == "site" }}')
        self.assertEqual(clone["config"]["checkout"], [{"commit": '${{ commitFrom("https://github.com/example/web.git", warehouse("site")).ID }}', "path": "./sources/site-0"}])
        parse = next(s for s in steps if s.get("as") == "read-portal-config")
        self.assertEqual(parse["uses"], "json-parse")
        self.assertEqual(parse["config"]["path"], "./sources/site-0/configs/${{ vars.stage }}/portal.json")
        self.assertEqual(parse["config"]["outputs"], [{"name": "value", "fromExpression": "$env"}])
        theme = next(s for s in steps if s.get("as") == "read-assets-source")
        self.assertEqual(theme["config"]["outputs"][0]["fromExpression"], "theme")
        writes = {s["config"]["path"]: s["config"]["contents"] for s in steps if s["uses"] == "file-write"}
        # One write carries the configuration and the digests: yaml-update cannot create keys a rewrite removed.
        self.assertEqual(
            writes["./repo/environments/test/portal.yaml"],
            '${{ asYAML({"artifact": {"digest": imageFrom("ghcr.io/example/portal", warehouse("site")).Digest}, '
            '"config": outputs["read-portal-config"].value}) }}',
        )
        owned = [s["uses"] for s in steps if s["config"].get("path") == "./repo/environments/test/portal.yaml"]
        self.assertEqual(owned, ["file-write"])
        files = self.expanded_sources(docs, "site", "test")[0]["helm"]["valueFiles"]
        self.assertEqual(files[-1], "$values/environments/test/assets.yaml")
        self.assertFalse([f for f in files if "promoted" in f])

    def test_warehouse_uses_supported_tag_selection(self):
        # Kargo >= 1.11 rejects the deprecated allowTags field at discovery time.
        docs = render(self.chart, self.example)
        subscriptions = [s for d in docs if d["kind"] == "Warehouse" for s in d["spec"]["subscriptions"]]
        filters = {s["image"]["repoURL"]: s["image"]["allowTagsRegexes"] for s in subscriptions if "image" in s}
        self.assertTrue(all("allowTags" not in s.get("image", {}) for s in subscriptions))
        self.assertEqual(filters["ghcr.io/example/backend"], [r"^v?[0-9]+\.[0-9]+\.[0-9]+$"])
        self.assertEqual(filters["ghcr.io/example/worker"], [r"^[0-9a-f]{7}$"])
        render(
            self.chart,
            self.example,
            {"releaseUnits": {"worker": {"services": {"worker": {"images": {"image.digest": "ghcr.io/example/backend"}}}}}},
            success=False,
        )

    def test_vendor_tag_fields_remain_digest_pinned_in_both_promotion_paths(self):
        values = yaml.safe_load(self.example.read_text())
        service = values["releaseUnits"]["backend"]["services"]["api"]
        service["images"] = {"plugin.image.tag": "ghcr.io/example/backend"}
        expression = 'imageFrom("ghcr.io/example/backend", warehouse("backend"))'
        pinned = f'{expression}.Tag + "@" + {expression}.Digest'
        steps = resource(render(self.chart, values), "Stage", "test")["spec"]["promotionTemplate"]["spec"]["steps"]
        update = next(s for s in steps if s["uses"] == "yaml-update" and s["config"]["path"].endswith("/api.yaml"))
        self.assertEqual(update["config"]["updates"], [{"key": "plugin.image.tag", "value": "${{ " + pinned + " }}"}])
        self.assertEqual(update["if"], '${{ ctx.targetFreight.origin.name == "backend" }}')
        service["gitValues"] = {"config": {"repository": "https://github.com/example/backend.git", "path": "config.yaml"}}
        steps = resource(render(self.chart, values), "Stage", "test")["spec"]["promotionTemplate"]["spec"]["steps"]
        write = next(s for s in steps if s["uses"] == "file-write" and s["config"]["path"].endswith("/api.yaml"))
        self.assertIn('"plugin": {"image": {"tag": ' + pinned + '}}', write["config"]["contents"])
        self.assertEqual(write["if"], update["if"])

    def test_unknown_references_cycles_and_exclusions(self):
        for invalid in [
            {"environments": {"production": {"upstream": ["missing"]}}},
            {"environments": {"test": {"upstream": ["production"]}}},
            {"environments": {"production": {"charts": {"service": None}}}},
            {
                "releaseUnits": {
                    "worker": {"services": {"worker": {"chart": "missing"}}}
                }
            },
        ]:
            with self.subTest(invalid=invalid):
                render(self.chart, self.example, invalid, success=False)
        values = yaml.safe_load(self.example.read_text())
        values["environments"]["production"]["units"] = {"backend": ["api", "migrations"]}
        docs = render(self.chart, values)
        production = resource(docs, "Stage", "production")["spec"]["promotionTemplate"]["spec"]["steps"]
        self.assertEqual(
            [a["name"] for a in next(s for s in production if s["uses"] == "argocd-update")["config"]["apps"]],
            ["example-backend-production"],
        )

    def test_service_membership_filters_sources_settings_and_promotion(self):
        values = yaml.safe_load(self.example.read_text())
        values["environments"]["production"]["units"] = {
            "backend": ["migrations"], "site": ["assets"],
        }
        values["releaseUnits"]["site"]["services"]["portal"]["gitValues"]["config"]["repository"] = "https://github.com/example/portal.git"
        docs = render(self.chart, values)
        sources = self.expanded_sources(docs, "backend", "production")
        self.assertEqual([s["helm"]["releaseName"] for s in sources if "helm" in s], ["migrations"])
        self.assertFalse(any(r.get("name") == "api" for r in self.expanded_spec(docs, "backend", "production")["ignoreDifferences"]))
        sources = self.expanded_sources(docs, "site", "production")
        self.assertEqual([s["helm"]["releaseName"] for s in sources if "helm" in s], ["assets"])
        stage = resource(docs, "Stage", "production")["spec"]
        self.assertEqual([r["origin"]["name"] for r in stage["requestedFreight"]], ["backend", "site"])
        steps = stage["promotionTemplate"]["spec"]["steps"]
        writes = [s["config"]["path"] for s in steps if s["uses"] in ("yaml-update", "file-write")]
        self.assertEqual(writes, ["./repo/environments/production/migrations.yaml", "./repo/environments/production/assets.yaml"])
        clones = [s["config"]["repoURL"] for s in steps if s["uses"] == "git-clone"]
        self.assertEqual(clones, ["https://github.com/example/platform.git", "https://github.com/example/web.git"])
        self.assertFalse(any(s.get("as") == "read-portal-config" for s in steps))
        self.assertEqual(len([s for s in steps if s["uses"] == "git-commit"]), 1)
        self.assertEqual(len([s for s in steps if s["uses"] == "argocd-update"]), 2)
        test_sources = self.expanded_sources(docs, "site", "test")
        self.assertEqual([s["helm"]["releaseName"] for s in test_sources if "helm" in s], ["assets", "portal"])
        warehouse = resource(docs, "Warehouse", "site")["spec"]["subscriptions"]
        self.assertEqual(len([s for s in warehouse if "image" in s]), 2)
        self.assertIn("https://github.com/example/portal.git", [s["git"]["repoURL"] for s in warehouse if "git" in s])

    def test_membership_is_explicit_and_validated(self):
        for units in [None, [], ["backend"], {}, {"missing": ["api"]},
                      {"backend": []}, {"backend": ["api", "api"]},
                      {"backend": ["missing"]}, {"backend": ["portal"]}]:
            with self.subTest(units=units):
                values = yaml.safe_load(self.example.read_text())
                values["environments"]["production"]["units"] = units
                render(self.chart, values, success=False)
        values = yaml.safe_load(self.example.read_text())
        del values["environments"]["production"]["units"]
        render(self.chart, values, success=False)
        values = yaml.safe_load(self.example.read_text())
        del values["environments"]["test"]["units"]["worker"]
        error = render(self.chart, values, success=False)
        self.assertIn("upstream test does not deploy unit worker", error)

    def test_unselected_catalog_entries_do_not_change_installations(self):
        values = yaml.safe_load(self.example.read_text())
        before = render(self.chart, values)
        values["releaseUnits"]["backend"]["services"]["optional"] = {
            "chart": "proxy", "images": {"image.digest": "ghcr.io/example/backend"},
            "capacityTargets": [{"name": "optional", "kind": "Deployment"}],
        }
        after = render(self.chart, values)
        for env in values["environments"]:
            self.assertEqual(self.expanded_spec(before, "backend", env), self.expanded_spec(after, "backend", env))
            self.assertEqual(resource(before, "Stage", env), resource(after, "Stage", env))
        for env in values["environments"].values():
            env["units"] = {"backend": ["api", "migrations"]}
            del env["charts"]["proxy"]
            del env["charts"]["webapp"]
        docs = render(self.chart, values)
        self.assertEqual([d["metadata"]["name"] for d in docs if d["kind"] == "ApplicationSet"], ["example-backend"])
        self.assertEqual([s["helm"]["releaseName"] for s in self.expanded_sources(docs, "backend", "test") if "helm" in s], ["api", "migrations"])

    def expanded_spec(self, docs, unit, environment):
        appset = resource(docs, "ApplicationSet", f"example-{unit}")
        element = next(
            e for e in appset["spec"]["generators"][0]["list"]["elements"]
            if e["environment"] == environment
        )
        result = subprocess.run(
            ["go", "run", str(ROOT / "tests/expand_templates.go")],
            input=json.dumps({"template": {"patch": appset["spec"]["templatePatch"]}, "elements": [element]}),
            text=True,
            capture_output=True,
            check=True,
        )
        return yaml.safe_load(json.loads(result.stdout)[0]["patch"])["spec"]

    def test_externally_scaled_environments_ignore_replicas(self):
        docs = render(self.chart, self.example)
        replicas = lambda spec: [r for r in spec["ignoreDifferences"] if r.get("jsonPointers") == ["/spec/replicas"] and "name" not in r]
        test = self.expanded_spec(docs, "backend", "test")
        self.assertEqual({r["kind"] for r in replicas(test)}, {"Deployment", "StatefulSet", "Rollout"})
        self.assertTrue(any(r.get("name") == "api" for r in test["ignoreDifferences"]))
        self.assertFalse(replicas(self.expanded_spec(docs, "backend", "production")))
        capacity = lambda env: [p["value"] for p in self.expanded_sources(docs, "backend", env)[0]["helm"]["parameters"] if p["name"] == "render.capacity"]
        self.assertEqual(capacity("test"), ["external"])
        self.assertEqual(capacity("production"), [])
        project = resource(docs, "AppProject", "example-test")
        self.assertEqual(project["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"], "-10")

    def expanded_sources(self, docs, unit, environment):
        appset = resource(docs, "ApplicationSet", f"example-{unit}")
        element = next(
            e for e in appset["spec"]["generators"][0]["list"]["elements"]
            if e["environment"] == environment
        )
        result = subprocess.run(
            ["go", "run", str(ROOT / "tests/expand_templates.go")],
            input=json.dumps({"template": {"patch": appset["spec"]["templatePatch"]}, "elements": [element]}),
            text=True,
            capture_output=True,
            check=True,
        )
        return yaml.safe_load(json.loads(result.stdout)[0]["patch"])["spec"]["sources"]

    def test_layers_sit_between_service_and_environment_files(self):
        docs = render(self.chart, self.example)
        api = self.expanded_sources(docs, "backend", "test")[0]
        self.assertEqual(api["targetRevision"], "0.1.0")
        self.assertEqual(
            api["helm"]["valueFiles"],
            [
                "$values/services/api.yaml",
                "$values/stages/test.yaml",
                "$values/environments/test/defaults.yaml",
                "$values/environments/test/api.yaml",
            ],
        )
        production = self.expanded_sources(docs, "backend", "production")[0]
        self.assertEqual(len(production["helm"]["valueFiles"]), 3)
        render(
            self.chart,
            self.example,
            {"environments": {"test": {"layers": ["../outside.yaml"]}}},
            success=False,
        )

    def test_units_without_images_are_configuration(self):
        docs = render(self.chart, self.example)
        self.assertFalse(
            [s for s in resource(docs, "Stage", "test")["spec"]["promotionTemplate"]["spec"]["steps"] if "edge" in json.dumps(s)]
        )
        edge = resource(docs, "ApplicationSet", "example-edge")["spec"]["template"]
        self.assertTrue(edge["spec"]["syncPolicy"]["automated"]["selfHeal"])
        self.assertNotIn("annotations", edge["metadata"])
        backend = resource(docs, "ApplicationSet", "example-backend")["spec"]["template"]
        self.assertNotIn("automated", backend["spec"]["syncPolicy"])

    def test_shared_values_reach_only_service_chart_sources(self):
        docs = render(self.chart, self.example)
        proxy = self.expanded_sources(docs, "edge", "test")[0]
        self.assertEqual(
            proxy["helm"]["valueFiles"],
            ["$values/services/edge-proxy.yaml", "$values/environments/test/edge-proxy.yaml"],
        )
        self.assertNotIn("parameters", proxy["helm"])

    def test_environment_values_deploy_the_environment_chart(self):
        docs = render(self.chart, self.example)
        app = resource(docs, "Application", "example-test-environment")
        self.assertEqual(app["spec"]["source"]["chart"], "environment")
        self.assertEqual(app["spec"]["source"]["targetRevision"], "0.1.0")
        self.assertEqual(app["spec"]["source"]["helm"]["valuesObject"]["namespace"], "example-test")
        self.assertTrue(app["spec"]["syncPolicy"]["automated"]["selfHeal"])
        self.assertEqual(
            resource(docs, "AppProject", "example-test")["spec"]["clusterResourceWhitelist"],
            [{"group": "mitis.cloud", "kind": "Environment"}],
        )
        self.assertEqual(
            resource(docs, "AppProject", "example-production")["spec"]["clusterResourceWhitelist"], []
        )
        self.assertFalse(
            [d for d in docs if d["kind"] == "Application" and d["metadata"]["name"] == "example-production-environment"]
        )
        render(
            self.chart,
            self.example,
            {"environments": {"test": {"charts": {"environment": None}}}},
            success=False,
        )

    def test_operator_permissions_are_explicit_and_environment_scoped(self):
        grants = [
            {"group": "apiextensions.k8s.io", "kind": "CustomResourceDefinition"},
            {"group": "rbac.authorization.k8s.io", "kind": "ClusterRole"},
        ]
        docs = render(self.chart, self.example, {"environments": {"test": {"clusterResources": grants}}})
        self.assertEqual(
            resource(docs, "AppProject", "example-test")["spec"]["clusterResourceWhitelist"],
            [{"group": "mitis.cloud", "kind": "Environment"}] + grants,
        )
        self.assertEqual(resource(docs, "AppProject", "example-production")["spec"]["clusterResourceWhitelist"], [])
        for grant in [{"group": "*", "kind": "ClusterRole"}, {"group": "rbac.authorization.k8s.io", "kind": "*"}]:
            render(self.chart, self.example, {"environments": {"test": {"clusterResources": [grant]}}}, success=False)

    def test_package_and_lint(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                ["helm", "package", str(self.chart), "-d", directory],
                check=True,
                capture_output=True,
            )
            package = next(Path(directory).glob("*.tgz"))
            self.assertEqual(
                render(self.chart, self.example), render(package, self.example)
            )
            subprocess.run(
                ["helm", "lint", "--strict", str(package), "-f", str(self.example)],
                check=True,
                capture_output=True,
            )


if __name__ == "__main__":
    unittest.main()
