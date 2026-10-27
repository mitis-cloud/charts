from pathlib import Path
import subprocess
import tempfile
import unittest

from test_contract import ROOT, render, resource


class WebappContract(unittest.TestCase):
    chart = ROOT / "charts/webapp"
    example = ROOT / "examples/webapp/values.yaml"

    def test_package_and_lint(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["helm", "package", str(self.chart), "-d", directory], check=True, capture_output=True)
            package = next(Path(directory).glob("*.tgz"))
            self.assertEqual(render(self.chart, self.example), render(package, self.example))
            subprocess.run(["helm", "lint", "--strict", str(package), "-f", str(self.example)], check=True, capture_output=True)

    def test_publishes_as_the_webapp_identity(self):
        docs = render(self.chart, self.example)
        pod = resource(docs, "Job", "sample-publish")["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "sample-publisher")
        self.assertFalse(pod["automountServiceAccountToken"])
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["BUCKET"]["valueFrom"]["secretKeyRef"], {"name": "sample-outputs", "key": "bucket"})
        self.assertEqual(env["DISTRIBUTION"]["valueFrom"]["secretKeyRef"], {"name": "sample-outputs", "key": "distribution"})
        self.assertEqual(pod["nodeSelector"], {"env": "spot"})
        fetch = {e["name"]: e["value"] for e in pod["initContainers"][0]["env"]}
        self.assertEqual(
            (fetch["REGISTRY"], fetch["AWS_REGION"], fetch["REPOSITORY"]),
            ("000000000000", "eu-west-1", "portal"),
        )

    def test_config_is_published_only_when_given(self):
        data = resource(render(self.chart, self.example), "ConfigMap", "sample-publish")["data"]
        self.assertEqual(data["config.json"], '{"backendUrl":"https://api.example.invalid","features":{"chats":{"enabled":true}}}')
        bare = render(self.chart, self.example, {"config": None, "name": "statics", "source": "dbridge", "prefix": "web", "replace": True})
        self.assertNotIn("config.json", resource(bare, "ConfigMap", "statics-publish")["data"])
        upload = {e["name"]: e.get("value") for e in resource(bare, "Job", "statics-publish")["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual((upload["PREFIX"], upload["REPLACE"]), ("web", "true"))

    def test_runs_on_every_sync(self):
        job = resource(render(self.chart, self.example), "Job", "sample-publish")
        self.assertEqual(job["metadata"]["annotations"]["argocd.argoproj.io/hook"], "Sync")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 600)

    def test_rejects_artifacts_outside_ecr_and_unsafe_paths(self):
        render(self.chart, self.example, {"artifact": {"repository": "ghcr.io/example/portal"}}, success=False)
        render(self.chart, self.example, {"source": "../etc"}, success=False)
