import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from agent_keylight.installer import (
    CLAUDE_EVENTS,
    CODEX_EVENTS,
    add_hooks,
    launch_agent_definition,
    remove_hooks,
)

COMMAND = "/tool emit codex"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "hooks.json"

    def tearDown(self):
        self.temp.cleanup()

    def hooks(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))["hooks"]

    def test_adds_only_own_groups_and_is_idempotent(self):
        other = {"matcher": "", "hooks": [{"type": "command", "command": "other"}]}
        self.path.write_text(
            json.dumps({"hooks": {"Stop": [other]}, "theme": "dark"}), encoding="utf-8"
        )
        self.assertTrue(add_hooks(self.path, "codex", CODEX_EVENTS, COMMAND))
        self.assertFalse(add_hooks(self.path, "codex", CODEX_EVENTS, COMMAND))
        value = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(value["theme"], "dark")
        self.assertEqual(value["hooks"]["Stop"][0], other)
        self.assertEqual(set(value["hooks"]), set(CODEX_EVENTS))
        backups = list(Path(self.temp.name).glob("hooks.json.agent-keylight-backup-*"))
        self.assertEqual(len(backups), 1)

    def test_question_hooks_use_matchers_and_codex_timeouts_fit_its_limits(self):
        add_hooks(self.path, "codex", CODEX_EVENTS, COMMAND)
        hooks = self.hooks()
        self.assertEqual(
            hooks["PreToolUse"],
            [
                {
                    "matcher": "^request_user_input$",
                    "hooks": [{"type": "command", "command": COMMAND, "timeout": 5}],
                }
            ],
        )
        self.assertEqual(hooks["Interrupt"][0]["hooks"][0]["timeout"], 3)
        self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        self.assertNotIn("matcher", hooks["Stop"][0])
        self.assertEqual(CLAUDE_EVENTS["PreToolUse"], "AskUserQuestion|ExitPlanMode")

    def test_upgrades_an_older_own_group_in_place(self):
        old = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": COMMAND, "timeout": 5}]}]
            }
        }
        self.path.write_text(json.dumps(old), encoding="utf-8")
        add_hooks(self.path, "codex", {"PreToolUse": "^request_user_input$"}, COMMAND)
        self.assertEqual(self.hooks()["PreToolUse"][0]["matcher"], "^request_user_input$")
        self.assertEqual(len(self.hooks()["PreToolUse"]), 1)

    def test_leaves_a_group_shared_with_other_handlers_alone(self):
        shared = {
            "hooks": [
                {"type": "command", "command": "other"},
                {"type": "command", "command": COMMAND},
            ]
        }
        self.path.write_text(json.dumps({"hooks": {"Stop": [shared]}}), encoding="utf-8")
        add_hooks(self.path, "codex", {"Stop": None}, COMMAND)
        self.assertEqual(self.hooks()["Stop"], [shared])

    def test_remove_takes_out_only_this_program(self):
        other = {"hooks": [{"type": "command", "command": "other"}]}
        self.path.write_text(json.dumps({"hooks": {"Stop": [other]}}), encoding="utf-8")
        add_hooks(self.path, "codex", CODEX_EVENTS, COMMAND)
        self.assertTrue(remove_hooks(self.path, COMMAND))
        self.assertEqual(self.hooks(), {"Stop": [other]})
        self.assertFalse(remove_hooks(self.path, COMMAND))

    def test_login_agent_runs_the_service_without_throttling(self):
        definition = launch_agent_definition()
        self.assertEqual(definition["ProgramArguments"][1:], ["serve"])
        self.assertEqual(definition["ProcessType"], "Interactive")
        self.assertTrue(definition["KeepAlive"])
        plistlib.dumps(definition)


if __name__ == "__main__":
    unittest.main()
