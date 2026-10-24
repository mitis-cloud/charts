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

    def test_databases_and_generated_values_keep_existing_remote_values(self):
        docs = self.composed()
        database = resource(docs, "Database", "backend")
        self.assertEqual(database["metadata"]["namespace"], "example")
        self.assertEqual(database["spec"]["name"], "example-backend")
        self.assertEqual(database["spec"]["secrets"], {"store": "aws-ssm", "key": "example-pgsql-backend"})
        pushes = {d["metadata"]["name"]: d for d in docs if d["kind"] == "PushSecret"}
        for key in ["nats-password", "mcp-api-key", "nats-username", "openai-api-key"]:
            push = pushes[f"generated-{key}"]
            self.assertEqual(push["spec"]["updatePolicy"], "IfNotExists")
            self.assertEqual(push["spec"]["data"][0]["match"]["remoteRef"]["remoteKey"], f"example-{key}")
        generators = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "Password"}
        self.assertEqual(generators["generated-mcp-api-key"]["symbols"], 0)
        self.assertEqual(generators["generated-mcp-api-key"]["length"], 32)
        self.assertTrue(generators["generated-nats-username"]["noUpper"])
        self.assertIn("symbolCharacters", generators["generated-nats-password"])
        manual = resource(docs, "Secret", "generated-openai-api-key")
        self.assertEqual(manual["stringData"], {"value": "-"})

    def test_webapps_follow_the_environment_maintenance_switch(self):
        webapp = resource(self.composed({"maintenance": {"enabled": True}}), "Webapp", "portal")
        self.assertEqual(webapp["spec"]["hostname"], "portal.app.example.invalid")
        self.assertTrue(webapp["spec"]["maintenance"]["enabled"])
        self.assertEqual(webapp["spec"]["maintenance"]["body"], "<p>Back soon.</p>")
        self.assertEqual(webapp["spec"]["outputs"], {"store": "aws-ssm", "key": "example-portal"})

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



class PostgresDatabaseComposition(unittest.TestCase):
    composition = CROSSPLANE / "database/postgres.yaml"

    def composed(self):
        xr = yaml.safe_load((ROOT / "examples/crossplane/database.yaml").read_text())
        return compose(xr, self.composition)

    def by_name(self, docs):
        return {d["metadata"]["name"]: d for d in docs}

    def test_durable_objects_are_never_dropped(self):
        # Revoking in teardown order is impossible once memberships go, and dropped roles would orphan the database.
        for doc in self.composed():
            if doc["apiVersion"].startswith("postgresql."):
                self.assertNotIn("Delete", doc["spec"]["managementPolicies"])

    def test_groups_accounts_and_ownership(self):
        docs = self.by_name(self.composed())
        migrator = docs["example-backend-account-migrator"]["spec"]["forProvider"]
        self.assertEqual(migrator["configurationParameters"], [{"name": "role", "value": "example-backend-group-admin"}])
        api = docs["example-backend-account-api"]["spec"]["forProvider"]
        self.assertNotIn("configurationParameters", api)
        self.assertEqual(api["passwordSecretRef"], {"name": "example-backend-api", "key": "password"})
        self.assertEqual(
            docs["example-backend-account-api-membership"]["spec"]["forProvider"]["memberOf"],
            "example-backend-group-operator",
        )
        self.assertEqual(docs["example-backend-database"]["spec"]["forProvider"]["owner"], "example-backend-group-admin")
        self.assertTrue(docs["example-backend-database-admin"]["spec"]["forProvider"]["revokePublicOnDb"])
        self.assertIn("MAINTAIN", docs["example-backend-public-admin-table"]["spec"]["forProvider"]["privileges"])
        self.assertNotIn("MAINTAIN", docs["example-backend-reporting-admin-table"]["spec"]["forProvider"]["privileges"])
        self.assertEqual(docs["example-backend-reporting-operator-table"]["spec"]["forProvider"]["privileges"], ["SELECT"])
        self.assertNotIn("example-backend-reporting-operator-function", docs)
        targets = {
            d["spec"]["forProvider"]["targetRole"]
            for d in docs.values()
            if d["kind"] == "DefaultPrivileges"
        }
        self.assertEqual(targets, {"example-backend-group-admin", "example-backend-migrator"})
        self.assertNotIn("example-backend-schema-public", docs)
        self.assertEqual(
            docs["example-backend-provider-membership"]["spec"]["forProvider"],
            {"role": "platform_admin", "memberOf": "example-backend-group-admin"},
        )
        self.assertEqual(
            docs["example-backend-provider-membership-migrator"]["spec"]["forProvider"]["memberOf"],
            "example-backend-migrator",
        )
        self.assertNotIn("example-backend-provider-membership-api", docs)

    def test_passwords_are_published_only_when_absent(self):
        docs = self.by_name(self.composed())

        pushes = [d for d in docs.values() if d["kind"] == "PushSecret"]
        self.assertEqual(len(pushes), 2)
        for push in pushes:
            self.assertEqual(push["spec"]["updatePolicy"], "IfNotExists")
            keys = [d["match"]["remoteRef"]["remoteKey"] for d in push["spec"]["data"]]
            account = push["metadata"]["name"].removeprefix("example-backend-")
            self.assertEqual(keys, [f"example-pgsql-backend-{account}-password", f"example-pgsql-backend-{account}-url"])
        generated = [d for d in docs.values() if d["kind"] == "ExternalSecret" and d["metadata"]["name"].endswith("-generated")]
        self.assertTrue(all(d["spec"]["refreshPolicy"] == "CreatedOnce" for d in generated))



class AwsWebappComposition(unittest.TestCase):
    composition = CROSSPLANE / "webapp/aws.yaml"

    def composed(self, observed=None):
        xr = yaml.safe_load((ROOT / "examples/crossplane/webapp.yaml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xr.yaml"
            path.write_text(yaml.safe_dump(xr))
            command = [
                "crossplane", "render", str(path), str(self.composition), str(FIXTURES / "functions.yaml"),
                "--required-resources", str(FIXTURES / "platform-aws.yaml"),
            ]
            if observed:
                command += ["--observed-resources", str(FIXTURES / observed)]
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(result.stderr)
            return {
                d["metadata"]["annotations"]["crossplane.io/composition-resource-name"]: d
                for d in documents(result.stdout)[1:]
            }

    def test_dependent_resources_wait_for_their_inputs(self):
        first = self.composed()
        for waiting in ["distribution", "website-policy", "validation", "record-a", "outputs-publish"]:
            self.assertNotIn(waiting, first)
        self.assertEqual(first["website-bucket"]["metadata"]["annotations"]["crossplane.io/external-name"], "portal.app.example.invalid")
        self.assertEqual(first["logs-bucket"]["metadata"]["annotations"]["crossplane.io/external-name"], "example-portal-logs")
        self.assertEqual(first["certificate"]["spec"]["forProvider"]["region"], "us-east-1")
        self.assertEqual(first["certificate"]["spec"]["forProvider"]["domainName"], "*.portal.app.example.invalid")

    def test_adopted_names_are_kept_and_the_distribution_is_wired(self):
        docs = self.composed("webapp-observed.yaml")
        self.assertEqual(docs["logs-bucket"]["metadata"]["annotations"]["crossplane.io/external-name"], "legacy-portal-logs-abc123")
        self.assertEqual(docs["function"]["metadata"]["annotations"]["crossplane.io/external-name"], "legacy-portal-function")
        distribution = docs["distribution"]["spec"]["forProvider"]
        self.assertEqual(docs["distribution"]["metadata"]["annotations"]["crossplane.io/external-name"], "E1EXAMPLEDIST")
        self.assertEqual(distribution["origin"][0]["s3OriginConfig"]["originAccessIdentity"], "origin-access-identity/cloudfront/E2EXAMPLEOAI")
        self.assertEqual(distribution["origin"][0]["domainName"], "portal.app.example.invalid.s3.eu-west-1.amazonaws.com")
        self.assertEqual(distribution["loggingConfig"]["bucket"], "legacy-portal-logs-abc123.s3.amazonaws.com")
        self.assertEqual([b["pathPattern"] for b in distribution["orderedCacheBehavior"]], ["/assets/*"])
        self.assertEqual(distribution["defaultCacheBehavior"]["functionAssociation"][0]["functionArn"], "arn:aws:cloudfront::000000000000:function/legacy-portal-function")
        self.assertEqual(docs["record-a"]["spec"]["forProvider"]["zoneId"], "Z0000000000NESTED")
        self.assertEqual(docs["validation"]["spec"]["forProvider"]["name"], "_abc.portal.app.example.invalid.")
        self.assertIn("maintenanceEnabled = false", docs["function-code"]["stringData"]["code"])


if __name__ == "__main__":
    unittest.main()
