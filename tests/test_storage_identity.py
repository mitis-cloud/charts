import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
import jsonschema

from test_contract import ROOT, documents, render, resource
from validate_resources import CACHE, download, strict_schema


def compose(kind, patch=None, observed=None, storage=True):
    xr = yaml.safe_load((ROOT / f"examples/crossplane/{kind}.yaml").read_text())
    xr["spec"].update(patch or {})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "xr.yaml"
        path.write_text(yaml.safe_dump(xr))
        args = [
            "crossplane", "render", str(path), str(ROOT / f"crossplane/{kind}/aws.yaml"),
            str(ROOT / "tests/crossplane/functions.yaml"),
            "--required-resources", str(ROOT / "tests/crossplane/platform-aws.yaml"),
        ]
        if storage and kind == "workloadidentity":
            args += ["--required-resources", str(ROOT / "tests/crossplane/storage-ready.yaml")]
        if observed:
            args += ["--observed-resources", str(ROOT / f"tests/crossplane/{observed}.yaml")]
        return subprocess.run(args, text=True, capture_output=True)


class StorageIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage_result = compose("objectstorage", {"versioning": False, "expireAfterDays": 90})
        cls.identity_result = compose("workloadidentity", observed="identity-observed")
        for result in [cls.storage_result, cls.identity_result]:
            if result.returncode:
                raise AssertionError(result.stderr)
        cls.storage = documents(cls.storage_result.stdout)
        cls.identity = documents(cls.identity_result.stdout)

    def test_private_storage_preserves_data_and_expiry(self):
        self.assertFalse(resource(self.storage, "Bucket")["spec"]["forProvider"]["forceDestroy"])
        public = resource(self.storage, "BucketPublicAccessBlock")["spec"]["forProvider"]
        self.assertTrue(all(public[k] for k in ["blockPublicAcls", "blockPublicPolicy", "ignorePublicAcls", "restrictPublicBuckets"]))
        rule = resource(self.storage, "BucketServerSideEncryptionConfiguration")["spec"]["forProvider"]["rule"][0]
        self.assertEqual(rule["blockedEncryptionTypes"], ["SSE-C"])
        self.assertEqual(rule["applyServerSideEncryptionByDefault"]["sseAlgorithm"], "AES256")
        self.assertEqual(resource(self.storage, "BucketVersioning")["spec"]["forProvider"]["versioningConfiguration"]["status"], "Suspended")
        expiry = resource(self.storage, "BucketLifecycleConfiguration")["spec"]["forProvider"]["rule"][0]
        self.assertEqual(expiry["expiration"][0]["days"], 90)

    def test_identity_preserves_adopted_names_and_uses_observed_storage(self):
        role = resource(self.identity, "Role")
        self.assertEqual(role["metadata"]["annotations"]["crossplane.io/external-name"], "existing_engine")
        statements = json.loads(resource(self.identity, "Policy")["spec"]["forProvider"]["policy"])["Statement"]
        self.assertEqual(statements[0]["Resource"], ["arn:aws:s3:::example-lakehouse", "arn:aws:s3:::example-lakehouse/*"])
        self.assertIn("s3:PutObject", statements[0]["Action"])
        self.assertEqual(statements[1]["Action"], ["s3:GetObject", "s3:ListBucket"])
        self.assertEqual(statements[1]["Resource"], ["arn:aws:s3:::shared-input", "arn:aws:s3:::shared-input/*"])
        self.assertEqual(statements[2]["Action"], ["s3:ListAllMyBuckets"])

    def test_workload_and_node_trust_are_scoped(self):
        trust = json.loads(resource(self.identity, "Role")["spec"]["forProvider"]["assumeRolePolicy"])["Statement"]
        conditions = trust[0]["Condition"]["StringEquals"]
        self.assertIn(["system:serviceaccount:example:engine"], conditions.values())
        self.assertEqual(trust[1]["Principal"]["AWS"], ["arn:aws:iam::000000000000:role/example-workers"])
        node = resource(self.identity, "RolePolicy")
        policy = json.loads(node["spec"]["forProvider"]["policy"])["Statement"][0]
        self.assertEqual(policy["Resource"], "arn:aws:iam::000000000000:role/existing_engine")
        account = resource(self.identity, "ServiceAccount")
        self.assertEqual(account["metadata"]["annotations"]["eks.amazonaws.com/role-arn"], policy["Resource"])

    def test_missing_storage_fails_without_replacing_permissions(self):
        result = compose("workloadidentity", observed="identity-observed", storage=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has no observed identifier", result.stderr)

    def test_managed_resources_match_the_installed_provider_api(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        for doc in self.storage + self.identity:
            group = doc["apiVersion"].split("/")[0]
            if not group.endswith(".aws.m.upbound.io"):
                continue
            plural = doc["kind"].lower() + "s"
            if doc["kind"].endswith("Policy"):
                plural = doc["kind"].lower()[:-1] + "ies"
            url = f"https://raw.githubusercontent.com/crossplane-contrib/provider-upjet-aws/v2.9.0/package/crds/{group}_{plural}.yaml"
            crd = yaml.safe_load(download(url))
            version = next(v for v in crd["spec"]["versions"] if v["name"] == doc["apiVersion"].split("/")[1])
            jsonschema.Draft7Validator(strict_schema(version["schema"]["openAPIV3Schema"])).validate(doc)

    def test_environment_keeps_neutral_capability_inputs(self):
        storage = yaml.safe_load((ROOT / "examples/crossplane/objectstorage.yaml").read_text())["spec"]
        identity = yaml.safe_load((ROOT / "examples/crossplane/workloadidentity.yaml").read_text())["spec"]
        values = {"namespace": "example", "objectStorage": {"lakehouse": storage}, "identities": {"engine": identity}}
        env = resource(render(ROOT / "charts/environment", values), "Environment")
        self.assertEqual(env["spec"]["objectStorage"]["lakehouse"], storage)
        self.assertEqual(env["spec"]["identities"]["engine"], identity)
        invalid = copy.deepcopy(values)
        invalid["identities"]["engine"]["serviceAccounts"] = ["*"]
        render(ROOT / "charts/environment", invalid, success=False)
