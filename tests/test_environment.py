import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from test_contract import ROOT, documents, render, resource


CROSSPLANE = ROOT / "crossplane"
FIXTURES = ROOT / "tests/crossplane"


def compose(xr, composition):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "xr.yaml"
        path.write_text(yaml.safe_dump(xr))
        result = subprocess.run(
            [
                "crossplane", "render", str(path), str(composition),
                str(FIXTURES / "functions.yaml"),
                "--required-resources", str(FIXTURES / "platform-aws.yaml"),
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return documents(result.stdout)[1:]


class EnvironmentContract(unittest.TestCase):
    chart = ROOT / "charts/environment"
    example = ROOT / "examples/environment/values.yaml"

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

    def test_environment_outlives_its_release(self):
        xr = resource(render(self.chart, self.example), "Environment")
        self.assertEqual(xr["metadata"]["name"], "example")
        self.assertEqual(xr["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertEqual(
            xr["metadata"]["annotations"]["argocd.argoproj.io/sync-options"], "Delete=false"
        )

    def test_maintenance_needs_hostnames_and_public_exposure(self):
        render(self.chart, {"namespace": "example", "maintenance": {"enabled": True}}, success=False)
        render(
            self.chart,
            {
                "namespace": "example",
                "exposures": ["private"],
                "maintenance": {"enabled": True, "hostnames": ["api.example.invalid"]},
            },
            success=False,
        )


class AwsEnvironmentComposition(unittest.TestCase):
    chart = ROOT / "charts/environment"
    composition = CROSSPLANE / "environment/aws.yaml"

    def composed(self, *values):
        xr = resource(render(self.chart, ROOT / "examples/environment/values.yaml", *values), "Environment")
        return compose(xr, self.composition)

    def test_exposure_classes_follow_the_service_contract(self):
        docs = self.composed()
        self.assertEqual(resource(docs, "Namespace")["metadata"]["name"], "example")
        for exposure, group in [("public", "example-alb"), ("private", "example-alb-private")]:
            params = resource(docs, "IngressClassParams", f"example-{exposure}")
            self.assertEqual(params["spec"]["group"]["name"], group)
            self.assertNotIn("certificateArn", params["spec"])
            self.assertEqual(
                params["spec"]["namespaceSelector"]["matchLabels"],
                {"kubernetes.io/metadata.name": "example"},
            )
            ingress_class = resource(docs, "IngressClass", f"example-{exposure}")
            self.assertEqual(ingress_class["spec"]["parameters"]["name"], f"example-{exposure}")
        self.assertEqual(
            resource(docs, "IngressClassParams", "example-private")["spec"]["inboundCIDRs"],
            ["192.0.2.0/24"],
        )
        self.assertEqual(
            resource(docs, "IngressClassParams", "example-public")["spec"]["loadBalancerAttributes"],
            [{"key": "routing.http.xff_header_processing.mode", "value": "remove"}],
        )

    def test_isolation_allows_only_declared_private_ports(self):
        policy = resource(self.composed(), "NetworkPolicy", "environment-isolation")
        self.assertEqual(policy["spec"]["policyTypes"], ["Egress"])
        internet = policy["spec"]["egress"][2]["to"][0]["ipBlock"]
        self.assertEqual(internet["cidr"], "0.0.0.0/0")
        self.assertIn("169.254.0.0/16", internet["except"])
        private = policy["spec"]["egress"][3]
        self.assertEqual(private["to"], [{"ipBlock": {"cidr": "198.51.100.0/24"}}])
        self.assertEqual(private["ports"], [{"protocol": "TCP", "port": 5432}])
        docs = self.composed({"isolation": {"enabled": False}})
        self.assertFalse([d for d in docs if d["kind"] == "NetworkPolicy"])

    def test_maintenance_ingress_exists_only_while_enabled(self):
        self.assertFalse([d for d in self.composed() if d["kind"] == "Ingress"])
        ingress = resource(self.composed({"maintenance": {"enabled": True}}), "Ingress")
        self.assertEqual(ingress["spec"]["ingressClassName"], "example-public")
        self.assertEqual([r["host"] for r in ingress["spec"]["rules"]], ["api.example.invalid"])
        action = json.loads(ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/actions.maintenance"])
        self.assertEqual(action["FixedResponseConfig"]["StatusCode"], "503")
        self.assertEqual(action["FixedResponseConfig"]["MessageBody"], "<p>Back soon.</p>")
        self.assertNotIn("external-dns.alpha.kubernetes.io/hostname", ingress["metadata"]["annotations"])

    def test_package_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [
                    "crossplane", "xpkg", "build",
                    "--package-root", str(CROSSPLANE),
                    "--examples-root", str(ROOT / "examples/crossplane"),
                    "-o", str(Path(directory) / "platform.xpkg"),
                ],
                check=True,
                capture_output=True,
            )


if __name__ == "__main__":
    unittest.main()
