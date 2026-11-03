import unittest

import yaml

from test_contract import ROOT, render, resource


class EnvironmentChartSelection(unittest.TestCase):
    def test_selection_is_explicit_and_pinned(self):
        chart = ROOT / "charts/delivery"
        values = yaml.safe_load((ROOT / "examples/delivery/platform.yaml").read_text())
        values["charts"]["assembly"] = {
            "repository": "registry.example.invalid/charts",
            "name": "assembly",
            "serviceScopes": [],
        }
        env = values["environments"]["test"]
        env["environmentChart"] = "assembly"
        env["charts"]["assembly"] = "1.2.3"
        docs = render(chart, values)
        source = resource(docs, "Application", "example-test-environment")["spec"][
            "source"
        ]
        self.assertEqual(
            (source["repoURL"], source["chart"], source["targetRevision"]),
            ("registry.example.invalid/charts", "assembly", "1.2.3"),
        )
        env["environmentChart"] = "missing"
        render(chart, values, success=False)
        env["environmentChart"] = "assembly"
        del env["charts"]["assembly"]
        render(chart, values, success=False)
