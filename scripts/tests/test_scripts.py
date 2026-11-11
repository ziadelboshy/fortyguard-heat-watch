"""Tests for the setup, export and check scripts (standard library only).

Run from the repo root: python3 -m unittest discover -s scripts -t scripts
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import check_workflows
import export_workflows
import setup_n8n

MAIN = json.loads(setup_n8n.WORKFLOWS[1][1].read_text(encoding="utf-8"))


def env_file(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as handle:
        handle.write(text)
    return Path(handle.name)


class ReadEnvFile(unittest.TestCase):
    def test_parses_quotes_exports_and_comments(self):
        path = env_file(
            "# comment\n"
            "export FORTYGUARD_API_KEY='abc def'\n"
            'TELEGRAM_BOT_TOKEN="1:x # not a comment"\n'
            "TELEGRAM_CHAT_ID=12345  # my chat\n"
            "EMPTY=\n"
        )
        try:
            values = setup_n8n.read_env_file(path)
        finally:
            path.unlink()
        self.assertEqual(values["FORTYGUARD_API_KEY"], "abc def")
        self.assertEqual(values["TELEGRAM_BOT_TOKEN"], "1:x # not a comment")
        self.assertEqual(values["TELEGRAM_CHAT_ID"], "12345")
        self.assertNotIn("EMPTY", values)  # empty entries must not mask real environment values


class LoadSecrets(unittest.TestCase):
    BASE: ClassVar[dict[str, str]] = {"FORTYGUARD_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "t"}

    def test_accepts_numeric_and_channel_chat_ids(self):
        for chat in ("123456", "-1001234567890", "@heat_alerts"):
            with self.subTest(chat=chat):
                self.assertEqual(
                    setup_n8n.load_secrets({**self.BASE, "TELEGRAM_CHAT_ID": chat}, "TELEGRAM_CHAT_ID").chat_id, chat
                )

    def test_rejects_missing_values_and_odd_chat_ids(self):
        with self.assertRaises(SystemExit):
            setup_n8n.load_secrets(self.BASE, "TELEGRAM_CHAT_ID")
        for chat in ("12 34", "chat; rm -rf /", "@x"):
            with self.subTest(chat=chat), self.assertRaises(SystemExit):
                setup_n8n.load_secrets({**self.BASE, "TELEGRAM_CHAT_ID": chat}, "TELEGRAM_CHAT_ID")


class Placeholders(unittest.TestCase):
    def test_fills_only_exact_placeholder_values(self):
        doc = {"a": "YOUR_TELEGRAM_CHAT_ID", "b": ["x YOUR_TELEGRAM_CHAT_ID", {"c": "YOUR_TELEGRAM_CHAT_ID"}]}
        self.assertEqual(
            setup_n8n.fill_placeholders(doc, "42"), {"a": "42", "b": ["x YOUR_TELEGRAM_CHAT_ID", {"c": "42"}]}
        )


class CheckWorkflows(unittest.TestCase):
    def test_repository_workflows_pass(self):
        self.assertEqual(check_workflows.check_workflow(MAIN), [])

    def test_catches_leaks_an_export_could_introduce(self):
        leaked = setup_n8n.fill_placeholders(copy.deepcopy(MAIN), "123456789")
        leaked["pinData"] = {"Settings": [{"json": {}}]}
        # Built at runtime so secret scanners don't flag this fake token in the source.
        leaked["nodes"][0]["notes"] = "token " + "1234567890:" + "AAbb" * 9
        problems = " | ".join(check_workflows.check_workflow(leaked))
        self.assertIn("telegram_chat_id must be", problems)
        self.assertIn("pinData", problems)
        self.assertIn("Telegram bot token", problems)


class ExportSanitize(unittest.TestCase):
    def test_export_is_returned_to_a_safe_repository_copy(self):
        exported = setup_n8n.fill_placeholders(copy.deepcopy(MAIN), "123456789")
        exported.update(active=True, versionId="v1", shared=[{"role": "owner"}], pinData={"x": [1]})
        for node in exported["nodes"]:
            if "credentials" in node:
                for ref in node["credentials"].values():
                    ref["data"] = "should never be kept"
        clean = export_workflows.sanitize(exported)
        self.assertEqual(check_workflows.check_workflow(clean), [])
        self.assertNotIn("versionId", clean)
        self.assertNotIn("shared", clean)
        self.assertFalse(clean["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
