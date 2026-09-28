import tempfile
import unittest
from pathlib import Path

from agent_keylight.state import choose_status, hook_status, write_event


class StateTests(unittest.TestCase):
    def test_waiting_beats_working_across_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_event("codex", "c1", "working", directory, now=100)
            write_event("claude", "a1", "waiting", directory, now=101)
            self.assertEqual(
                choose_status(directory, now=102, stale_after=1000), ("waiting", "claude")
            )
            write_event("claude", "a1", None, directory)
            self.assertEqual(
                choose_status(directory, now=102, stale_after=1000), ("working", "codex")
            )

    def test_completed_expires_to_idle(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_event("codex", "c1", "completed", directory, now=100)
            self.assertEqual(choose_status(directory, now=110, completed_hold=12)[0], "completed")
            self.assertEqual(choose_status(directory, now=113, completed_hold=12)[0], "idle")

    def test_hook_mapping(self):
        self.assertEqual(hook_status("codex", {"hook_event_name": "UserPromptSubmit"}), "working")
        self.assertEqual(hook_status("codex", {"hook_event_name": "PermissionRequest"}), "waiting")
        self.assertEqual(hook_status("codex", {"hook_event_name": "PostToolUse"}), "working")
        self.assertEqual(hook_status("claude", {"hook_event_name": "StopFailure"}), "error")
        self.assertEqual(hook_status("claude", {"hook_event_name": "ElicitationResult"}), "working")
        self.assertIsNone(hook_status("claude", {"hook_event_name": "SessionEnd"}))


if __name__ == "__main__":
    unittest.main()
