import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_keylight import hook
from agent_keylight.state import Update


def run_hook(source: str, payload: bytes) -> str:
    output = io.StringIO()
    with (
        patch("agent_keylight.hook.sys.stdin", SimpleNamespace(buffer=io.BytesIO(payload))),
        patch("agent_keylight.hook.sys.stdout", output),
    ):
        hook.run(source)
    return output.getvalue()


class HookTests(unittest.TestCase):
    def test_codex_stop_records_completion_and_prints_empty_json(self):
        payload = json.dumps({"session_id": "t", "hook_event_name": "Stop"}).encode()
        with patch("agent_keylight.hook.apply") as apply:
            output = run_hook("codex", payload)
        self.assertEqual(json.loads(output), {})
        source, session, update = apply.call_args.args
        self.assertEqual((source, session, update), ("codex", "t", Update("completed")))
        pid, started = apply.call_args.kwargs["agent"]
        self.assertGreater(pid, 1)
        self.assertGreater(started, 0)

    def test_claude_hooks_print_nothing(self):
        payload = json.dumps({"session_id": "t", "hook_event_name": "Stop"}).encode()
        with patch("agent_keylight.hook.apply"):
            self.assertEqual(run_hook("claude", payload), "")

    def test_large_tool_output_is_still_recorded(self):
        event = {
            "session_id": "t",
            "hook_event_name": "PostToolUse",
            "tool_response": "x" * 3_000_000,
        }
        with patch("agent_keylight.hook.apply") as apply:
            run_hook("claude", json.dumps(event).encode())
        self.assertEqual(apply.call_args.args[2], Update("working"))

    def test_events_without_meaning_are_not_written(self):
        payload = json.dumps({"session_id": "t", "hook_event_name": "SubagentStop"}).encode()
        with patch("agent_keylight.hook.apply") as apply:
            run_hook("claude", payload)
        apply.assert_not_called()

    def test_bad_input_is_logged_and_codex_still_gets_json(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / "sessions"
            with patch("agent_keylight.hook.SESSIONS_DIR", sessions):
                output = run_hook("codex", b"not json")
            self.assertEqual(json.loads(output), {})
            self.assertIn("codex:", (Path(temp) / "hook-errors.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
