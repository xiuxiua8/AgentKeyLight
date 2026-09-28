import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_keylight.installer import _add_hooks


class InstallerTests(unittest.TestCase):
    def test_adds_only_own_handler_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
            path.write_text(json.dumps(existing), encoding="utf-8")
            with patch("agent_keylight.installer._hook_command", return_value="tool emit codex"):
                self.assertTrue(_add_hooks(path, "codex", ("Stop", "Interrupt")))
                self.assertFalse(_add_hooks(path, "codex", ("Stop", "Interrupt")))
            value = json.loads(path.read_text(encoding="utf-8"))
            handlers = [handler for group in value["hooks"]["Stop"] for handler in group["hooks"]]
            self.assertEqual([x["command"] for x in handlers], ["other", "tool emit codex"])
            self.assertIn("Interrupt", value["hooks"])

    def test_codex_interrupt_and_session_end_use_supported_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            with patch("agent_keylight.installer._hook_command", return_value="tool emit codex"):
                _add_hooks(path, "codex", ("Interrupt", "SessionEnd", "Stop"))
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(hooks["Interrupt"][0]["hooks"][0]["timeout"], 3)
            self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 3)
            self.assertEqual(hooks["Stop"][0]["hooks"][0]["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
