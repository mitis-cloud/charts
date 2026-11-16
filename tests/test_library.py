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

    def test_a_composed_service_equals_the_service_chart(self):
        for path in sorted((ROOT / "examples/service").glob("*.yaml")):
            with self.subTest(example=path.name):
                definition = yaml.safe_load(path.read_text())
                direct = render(self.service, definition, {"name": definition.get("name", path.stem)})
                composed = render(self.composed, {"services": {path.stem: definition}})
                self.assertEqual(without_chart_label(direct), without_chart_label(composed))

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
        deployments = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
        self.assertEqual(set(deployments), {"server", "worker"})
        self.assertEqual(deployments["worker"]["spec"]["replicas"], 2)
        self.assertEqual([d["metadata"]["name"] for d in docs if d["kind"] == "Service"], ["server"])


if __name__ == "__main__":
    unittest.main()
