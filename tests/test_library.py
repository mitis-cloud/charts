import copy
import unittest

import yaml

from test_contract import ROOT, render


def without_chart_label(docs):
    docs = copy.deepcopy(docs)
    for doc in docs:
        doc["metadata"].get("labels", {}).pop("helm.sh/chart", None)
        template = doc.get("spec", {}).get("template", {})
        template.get("metadata", {}).get("labels", {}).pop("helm.sh/chart", None)
    return sorted(docs, key=lambda d: (d["kind"], d["metadata"]["name"]))


class ServiceLibrary(unittest.TestCase):
    service = ROOT / "charts/service"
    composed = ROOT / "tests/fixtures/composed"

    def test_an_existing_configmap_is_consumed_without_a_second_owner(self):
        definition = {
            "image": {"repository": "example.invalid/service", "tag": "fixture"},
            "files": {
                "settings": {
                    "path": "/etc/app/settings.yaml",
                    "configMap": {"name": "shared-settings", "key": "settings.yaml"},
                }
            },
        }
        docs = render(self.service, definition)
        self.assertFalse(
            any(d["kind"] in ("ConfigMap", "Secret", "ExternalSecret") for d in docs)
        )
        pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"][
            "spec"
        ]
        self.assertEqual(pod["volumes"][0]["configMap"]["name"], "shared-settings")
        self.assertEqual(
            pod["volumes"][0]["configMap"]["items"],
            [{"key": "settings.yaml", "path": "settings.yaml"}],
        )
        render(
            self.service,
            definition,
            {"files": {"settings": {"content": "duplicate"}}},
            success=False,
        )
        render(
            self.service,
            definition,
            {"files": {"settings": {"configMap": {"key": ""}}}},
            success=False,
        )

    def test_a_composed_service_equals_the_service_chart(self):
        for path in sorted((ROOT / "examples/service").glob("*.yaml")):
            with self.subTest(example=path.name):
                definition = yaml.safe_load(path.read_text())
                direct = render(
                    self.service,
                    definition,
                    {"name": definition.get("name", path.stem)},
                )
                composed = render(self.composed, {"services": {path.stem: definition}})
                self.assertEqual(
                    without_chart_label(direct), without_chart_label(composed)
                )

    def test_one_release_composes_several_services(self):
        image = {"repository": "example.invalid/service", "tag": "fixture"}
        docs = render(
            self.composed,
            {
                "services": {
                    "server": {"image": image, "ports": {"http": 8080}},
                    "worker": {"image": image, "capacity": {"instances": 2}},
                }
            },
        )
        deployments = {
            d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"
        }
        self.assertEqual(set(deployments), {"server", "worker"})
        self.assertEqual(deployments["worker"]["spec"]["replicas"], 2)
        self.assertEqual(
            [d["metadata"]["name"] for d in docs if d["kind"] == "Service"], ["server"]
        )

    def test_external_scaling_omits_replicas_only_on_the_owned_workload(self):
        image = {"repository": "example.invalid/service", "tag": "fixture"}
        worker = {"image": image, "capacity": {"instances": "external"}}
        docs = render(
            self.composed, {"services": {"server": {"image": image}, "worker": worker}}
        )
        deployments = {
            d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"
        }
        self.assertEqual(deployments["server"]["spec"]["replicas"], 1)
        self.assertNotIn("replicas", deployments["worker"]["spec"])
        direct = render(self.service, worker)
        self.assertNotIn(
            "replicas", next(d for d in direct if d["kind"] == "Deployment")["spec"]
        )
        for incompatible in (
            {"workload": "stateful"},
            {"updates": {"strategy": "canary"}},
            {"disruption": {"surgeBeforeEviction": True}},
            {"availability": {"nodes": True}},
        ):
            with self.subTest(incompatible=incompatible):
                render(self.service, worker, incompatible, success=False)

    def test_environment_settings_and_ordering_reach_every_service(self):
        image = {"repository": "example.invalid/service", "tag": "fixture"}
        docs = render(
            self.composed,
            {
                "secretPrefix": "test-",
                "instanceStorage": "block",
                "placement": "stable",
                "render": {"engine": "argo", "order": 2},
                "services": {
                    "broker": {
                        "image": image,
                        "workload": "stateful",
                        "capacity": {"instances": 3},
                        "ports": {"client": 4222},
                        "storage": {"data": {"access": "instance", "size": "1Gi"}},
                        "mounts": {"data": "/data"},
                    },
                    "controller": {
                        "image": image,
                        "environment": {"TOKEN": {"ssm": "token"}},
                        "placement": "any",
                    },
                },
            },
        )
        kinds = {(d["kind"], d["metadata"]["name"]) for d in docs}
        self.assertLessEqual(
            {
                ("StatefulSet", "broker"),
                ("Service", "broker"),
                ("Service", "broker-peers"),
                ("Deployment", "controller"),
                ("ExternalSecret", "controller-secrets"),
            },
            kinds,
        )
        broker = next(d for d in docs if d["kind"] == "StatefulSet")
        self.assertEqual(
            broker["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"],
            "block",
        )
        self.assertEqual(
            broker["spec"]["template"]["spec"]["nodeSelector"], {"env": "stable"}
        )
        controller = next(d for d in docs if d["kind"] == "Deployment")
        self.assertNotIn("nodeSelector", controller["spec"]["template"]["spec"])
        secret = next(d for d in docs if d["kind"] == "ExternalSecret")
        self.assertEqual(secret["spec"]["data"][0]["remoteRef"]["key"], "test-token")
        for doc in (broker, controller):
            self.assertEqual(
                doc["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"], "200"
            )


if __name__ == "__main__":
    unittest.main()
