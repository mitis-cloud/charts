import copy
import json
import unittest
from urllib.parse import quote_plus

import yaml

from test_contract import ROOT, expand_secret, render, resource


class InputContract(unittest.TestCase):
    chart = ROOT / "charts/service"
    base = {"image": {"repository": "example.invalid/service", "tag": "fixture"}}

    def test_formats_are_scoped_encoded_and_not_executable(self):
        values = {
            "inputSecret": "existing-inputs",
            "secretPrefix": "tenant-",
            "secretRefresh": "1m",
            "facts": {"host": "database", "zero": 0, "false": False},
            "environment": {
                "URL": {
                    "format": "db://user:{ssm:password|urlquery}@{fact:host}/{fact:zero}"
                },
                "FLAG": {"format": "{fact:false}"},
                "LITERAL": '{{ fail "literal" }}',
                "WHOLE": {
                    "ssm": {"key": "/shared/token", "absolute": True},
                    "key": "token",
                },
            },
        }
        docs = render(self.chart, self.base, values)
        secret = resource(docs, "ExternalSecret", "existing-inputs")
        self.assertEqual(secret["spec"]["refreshInterval"], "1m")
        self.assertEqual(secret["spec"]["target"]["template"]["mergePolicy"], "Replace")
        password = 'a b&/+?"{{ .untrusted }}'
        self.assertEqual(
            expand_secret(
                secret, {"tenant-password": password, "/shared/token": "token"}
            ),
            {
                "URL": f"db://user:{quote_plus(password)}@database/0",
                "token": "token",
            },
        )
        env = resource(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0][
            "env"
        ]
        by_name = {item["name"]: item for item in env}
        self.assertEqual(by_name["FLAG"]["value"], "false")
        self.assertEqual(by_name["LITERAL"]["value"], '{{ fail "literal" }}')
        self.assertEqual(
            by_name["URL"]["valueFrom"]["secretKeyRef"],
            {"name": "existing-inputs", "key": "URL"},
        )
        self.assertEqual(by_name["WHOLE"]["valueFrom"]["secretKeyRef"]["key"], "token")

    def test_files_share_one_producer_and_keep_mount_identity(self):
        values = {
            "files": {
                "settings": {
                    "name": "existing-config",
                    "key": "settings.yaml",
                    "path": "/etc/settings.yaml",
                    "yaml": {
                        "password": {"ssm": "password"},
                        "enabled": False,
                        "items": ["true", True, 0],
                    },
                }
            },
            "init": {
                "prepare": {
                    "files": {
                        "config": {"from": "settings", "path": "/work/settings.yaml"}
                    }
                }
            },
            "sidecars": {"observer": {"command": ["observe"]}},
        }
        docs = render(self.chart, self.base, values)
        secrets = [d for d in docs if d["kind"] == "ExternalSecret"]
        self.assertEqual(len(secrets), 1)
        text = expand_secret(secrets[0], {"password": '"colon: value\nnext'})[
            "settings.yaml"
        ]
        self.assertEqual(
            yaml.safe_load(text),
            {
                "password": '"colon: value\nnext',
                "enabled": False,
                "items": ["true", True, 0],
            },
        )
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(
            [v["secret"]["secretName"] for v in pod["volumes"]],
            ["existing-config", "existing-config"],
        )
        self.assertEqual(
            pod["initContainers"][0]["volumeMounts"][0]["subPath"], "settings.yaml"
        )
        self.assertNotIn("volumeMounts", pod["containers"][1])
        render(
            self.chart, self.base, values, {"files": {"settings": None}}, success=False
        )

    def test_plain_structured_and_whole_remote_files(self):
        docs = render(
            self.chart,
            self.base,
            {
                "facts": {"zero": 0},
                "files": {
                    "plain": {
                        "path": "/plain.json",
                        "json": {
                            "zero": {"fact": "zero"},
                            "empty": [],
                            "literal": "{{ untouched }}",
                        },
                    },
                    "remote": {"path": "/token", "ssm": "token"},
                },
            },
        )
        config = resource(docs, "ConfigMap")
        self.assertEqual(
            json.loads(config["data"]["plain"]),
            {"zero": 0, "empty": [], "literal": "{{ untouched }}"},
        )
        self.assertEqual(
            expand_secret(resource(docs, "ExternalSecret"), {"token": "whole\nfile"}),
            {"remote": "whole\nfile"},
        )

    def test_named_target_and_nullable_job_ownership(self):
        values = {
            "environment": {
                "TOKEN": {"ssm": "token", "name": "native-inputs", "key": "token"},
                "FALSE": {"value": False},
            },
            "jobs": {
                "configure": {
                    "run": "after-ready",
                    "environment": {"ONLY_JOB": {"ssm": "job-token"}},
                }
            },
        }
        docs = render(self.chart, self.base, values)
        self.assertEqual(len([d for d in docs if d["kind"] == "ExternalSecret"]), 2)
        self.assertEqual(
            expand_secret(
                resource(docs, "ExternalSecret", "native-inputs"), {"token": "value"}
            ),
            {"token": "value"},
        )
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(
            env["TOKEN"]["valueFrom"]["secretKeyRef"],
            {"name": "native-inputs", "key": "token"},
        )
        self.assertEqual(env["FALSE"]["value"], "false")
        omitted = render(self.chart, self.base, values, {"jobs": {"configure": None}})
        self.assertFalse(any(d["kind"] == "Job" for d in omitted))
        self.assertEqual(
            [d["metadata"]["name"] for d in omitted if d["kind"] == "ExternalSecret"],
            ["native-inputs"],
        )
        render(
            self.chart,
            self.base,
            {"environment": {"LITERAL": {"value": "x", "name": "native-inputs"}}},
            success=False,
        )

    def test_invalid_and_conflicting_inputs_fail_closed(self):
        for value in [
            {"format": "{ssm:key|upper}"},
            {"format": "{{ .password }}"},
            {"format": "{unknown:key}"},
            {"format": "{ssm:key"},
            {"format": "{fact:missing}"},
            {"format": "{ssm:key}", "ssm": "key"},
            {"remoteSecret": "old"},
            {"ssm": ""},
        ]:
            with self.subTest(value=value):
                render(
                    self.chart,
                    self.base,
                    {"environment": {"VALUE": value}},
                    success=False,
                )
        render(self.chart, self.base, {"secrets": {}}, success=False)
        render(
            self.chart,
            self.base,
            {"files": {"config": {"path": "/config", "from": "missing"}}},
            success=False,
        )
        shared = {"inputSecret": "shared", "environment": {"VALUE": {"ssm": "one"}}}
        values = {
            **copy.deepcopy(shared),
            "sidecars": {"worker": copy.deepcopy(shared)},
        }
        self.assertEqual(
            len(
                [
                    d
                    for d in render(self.chart, self.base, values)
                    if d["kind"] == "ExternalSecret"
                ]
            ),
            1,
        )
        values["sidecars"]["worker"]["environment"]["VALUE"] = {"ssm": "two"}
        render(self.chart, self.base, values, success=False)


if __name__ == "__main__":
    unittest.main()
