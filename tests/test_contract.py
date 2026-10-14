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
            "secrets": {
                "credentials": {
                    "values": {"required": "required", "optional": "optional"}
                }
            },
            "environment": {
                "OPTIONAL": {"secret": {"name": "credentials", "key": "optional"}}
            },
        }
        override = {
            "storage": {"assets": None},
            "mounts": {"assets": None},
            "secrets": {"credentials": {"values": {"optional": None}}},
            "environment": {"OPTIONAL": None},
        }
        docs = render(self.chart, self.base, service, override)
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertNotIn("volumes", pod)
        self.assertNotIn("volumeMounts", pod["containers"][0])
        self.assertNotIn("env", pod["containers"][0])
        self.assertEqual(
            resource(docs, "ExternalSecret")["spec"]["data"],
            [
                {"secretKey": "required", "remoteRef": {"key": "required"}},
            ],
        )
        render(
            self.chart, self.base, service, {"storage": {"assets": None}}, success=False
        )
        render(
            self.chart,
            self.base,
            service,
            override,
            {"secrets": {"credentials": {"values": {"required": None}}}},
            success=False,
        )

    def test_explicit_manual_task_can_share_its_resource_only_release(self):
        service = {
            "workload": "resources",
            "secrets": {"inputs": {"values": {"key": "key"}}},
            "jobs": {
                "reset": {"run": "manual", "command": ["reset"]},
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

    def test_secret_context_is_typed_data_not_template_code(self):
        literal = '"{{ fail "not code" }}"\n\\'
        values = {
            "workload": "resources",
            "facts": {"enabled": False, "count": 0},
            "secrets": {
                "config": {
                    "values": {"password": "password"},
                    "templateContext": {
                        "enabled": {"fact": "enabled"},
                        "count": {"fact": "count"},
                        "literal": literal,
                    },
                    "template": {
                        "file": "{{ if $context.enabled }}on{{ else }}off{{ end }}|{{ $context.count }}|{{ $context.literal }}|{{ .password }}"
                    },
                }
            },
        }
        secret = resource(render(self.chart, values), "ExternalSecret")
        output = subprocess.run(
            ["go", "run", str(ROOT / "tests/expand_templates.go")],
            input=json.dumps(
                {
                    "template": secret["spec"]["target"]["template"]["data"],
                    "elements": [{"password": "fixture"}],
                }
            ),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            json.loads(output.stdout), [{"file": f"off|0|{literal}|fixture"}]
        )
        render(self.chart, values, {"facts": {"enabled": None}}, success=False)
        render(
            self.chart,
            values,
            {"secrets": {"config": {"template": None}}},
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
                "environment": {"PRIVATE": {"remoteSecret": "private"}},
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

    def test_isolated_promotion_boundaries(self):
        docs = render(self.chart, self.example)
        stages = [d for d in docs if d["kind"] == "Stage"]
        self.assertEqual(4, len(stages))
        self.assertEqual(
            {"backend-test", "backend-production", "worker-test", "worker-production"},
            {d["metadata"]["name"] for d in stages},
        )
        for stage in stages:
            self.assertEqual(1, len(stage["spec"]["requestedFreight"]))
            self.assertEqual(
                1, len(stage["spec"]["promotionTemplate"]["spec"]["steps"])
            )
        worker = resource(docs, "ApplicationSet", "example-worker")
        ignores = worker["spec"]["template"]["spec"]["ignoreDifferences"]
        self.assertFalse(
            any("/spec/replicas" in rule.get("jsonPointers", []) for rule in ignores)
        )
        task = resource(docs, "PromotionTask", "backend")
        push = next(s for s in task["spec"]["steps"] if s["uses"] == "argocd-update")
        self.assertEqual(
            "${{ task.outputs.push.commit }}",
            push["config"]["apps"][0]["sources"][0]["desiredRevision"],
        )

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
        docs = render(
            self.chart,
            self.example,
            {"environments": {"production": {"units": ["backend"]}}},
        )
        self.assertEqual(3, sum(d["kind"] == "Stage" for d in docs))

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
