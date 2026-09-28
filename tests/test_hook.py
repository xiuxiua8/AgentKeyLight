import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_keylight.hook import run


class HookTests(unittest.TestCase):
    def test_codex_stop_emits_valid_noop_json(self):
        payload = json.dumps({"session_id": "test", "hook_event_name": "Stop"}).encode()
        output = io.StringIO()
        with (
            patch("agent_keylight.hook.sys.stdin", SimpleNamespace(buffer=io.BytesIO(payload))),
            patch("agent_keylight.hook.sys.stdout", output),
            patch("agent_keylight.hook.write_event") as write_event,
        ):
            run("codex")
        self.assertEqual(json.loads(output.getvalue()), {})
        write_event.assert_called_once_with("codex", "test", "completed")


if __name__ == "__main__":
    unittest.main()
