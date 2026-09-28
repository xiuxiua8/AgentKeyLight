import json
import tempfile
import unittest
from pathlib import Path

from agent_keylight.state import (
    IDLE,
    PERMISSION_CONFIRM_SECONDS,
    Display,
    Update,
    apply,
    interpret,
    select,
    session_file,
)


def alive(_pid: int, _started: float) -> bool:
    return True


def event(name: str, **fields) -> dict:
    return {"hook_event_name": name, "session_id": "s", **fields}


class InterpretTests(unittest.TestCase):
    def test_turn_lifecycle(self):
        for source in ("claude", "codex"):
            self.assertEqual(interpret(source, event("UserPromptSubmit")), Update("working"))
            self.assertEqual(interpret(source, event("PostToolUse")), Update("working"))
            self.assertEqual(interpret(source, event("Stop")), Update("completed"))
            self.assertEqual(interpret(source, event("SessionEnd")), Update(None, end=True))
            self.assertEqual(
                interpret(source, event("SessionStart", source="startup")), Update(None)
            )
        self.assertEqual(
            interpret("claude", event("StopFailure", error="rate_limit")), Update("error")
        )
        self.assertEqual(interpret("codex", event("Interrupt")), Update("error"))

    def test_compaction_does_not_end_the_running_task(self):
        for source in ("claude", "codex"):
            self.assertIsNone(interpret(source, event("SessionStart", source="compact")))

    def test_permission_waits_need_confirmation_only_in_claude_code(self):
        self.assertEqual(
            interpret("claude", event("PermissionRequest", tool_name="Bash")),
            Update("waiting", confirmed=False),
        )
        self.assertEqual(
            interpret("claude", event("Notification", notification_type="permission_prompt")),
            Update("waiting"),
        )
        self.assertEqual(
            interpret("codex", event("PermissionRequest", tool_name="Bash")), Update("waiting")
        )

    def test_questions_wait_for_the_answer(self):
        for tool in ("AskUserQuestion", "ExitPlanMode"):
            self.assertEqual(
                interpret("claude", event("PreToolUse", tool_name=tool)), Update("waiting")
            )
            self.assertEqual(
                interpret("claude", event("PermissionRequest", tool_name=tool)), Update("waiting")
            )
        self.assertEqual(
            interpret("codex", event("PreToolUse", tool_name="request_user_input")),
            Update("waiting"),
        )
        self.assertIsNone(interpret("claude", event("PreToolUse", tool_name="Bash")))
        self.assertEqual(interpret("claude", event("Elicitation")), Update("waiting"))
        self.assertEqual(interpret("claude", event("ElicitationResult")), Update("working"))

    def test_idle_prompt_clears_a_turn_ended_by_esc(self):
        self.assertEqual(
            interpret("claude", event("Notification", notification_type="idle_prompt")),
            Update(None),
        )
        self.assertIsNone(
            interpret("claude", event("Notification", notification_type="auth_success"))
        )

    def test_subagent_api_error_is_not_a_failed_turn(self):
        self.assertIsNone(
            interpret("claude", event("StopFailure", agent_id="a1", error="model_not_found"))
        )

    def test_unknown_events_change_nothing(self):
        self.assertIsNone(interpret("codex", event("SubagentStop")))
        self.assertIsNone(
            interpret("codex", event("Notification", notification_type="permission_prompt"))
        )


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, source, session, update, now, agent=(4242, 1000.0)):
        apply(source, session, update, directory=self.dir, now=now, agent=agent)

    def show(self, now, running=alive, forget=False):
        return select(
            self.dir,
            now=now,
            running=running,
            forget=forget,
            completed_seconds=12,
            error_seconds=20,
        )

    def test_records_are_small_and_only_waits_carry_confirmation(self):
        self.put("claude", "a", Update("waiting", confirmed=False), 100)
        record = json.loads(session_file("claude", "a", self.dir).read_text())
        self.assertEqual(
            record,
            {
                "source": "claude",
                "session_id": "a",
                "status": "waiting",
                "at": 100,
                "confirmed": False,
                "pid": 4242,
                "started": 1000.0,
            },
        )
        self.put("claude", "a", Update("working"), 101, agent=None)
        self.assertNotIn("confirmed", json.loads(session_file("claude", "a", self.dir).read_text()))

    def test_waiting_beats_everything_and_counts_are_per_status(self):
        self.put("codex", "c1", Update("working"), 100)
        self.put("claude", "a1", Update("working"), 101)
        self.put("claude", "a2", Update("completed"), 102)
        self.assertEqual(self.show(103), Display("working", "claude", 2))
        self.put("codex", "c2", Update("waiting"), 104)
        self.assertEqual(self.show(105), Display("waiting", "codex", 1))
        self.put("codex", "c2", Update(None), 106)
        self.assertEqual(self.show(107), Display("working", "claude", 2))

    def test_unconfirmed_permission_turns_into_working(self):
        self.put("claude", "a", Update("waiting", confirmed=False), 100)
        self.assertEqual(self.show(100 + PERMISSION_CONFIRM_SECONDS - 1).status, "waiting")
        self.assertEqual(self.show(100 + PERMISSION_CONFIRM_SECONDS + 1).status, "working")
        self.put("claude", "a", Update("waiting"), 106)
        self.assertEqual(self.show(400).status, "waiting")

    def test_results_show_for_their_hold_time(self):
        self.put("codex", "c", Update("completed"), 100)
        self.assertEqual(self.show(111).status, "completed")
        self.assertEqual(self.show(113), IDLE)
        self.put("claude", "a", Update("error"), 200)
        self.assertEqual(self.show(219).status, "error")
        self.assertEqual(self.show(221), IDLE)

    def test_sessions_of_exited_agents_disappear(self):
        self.put("claude", "gone", Update("waiting"), 100, agent=(4242, 1000.0))
        self.put("codex", "done", Update("completed"), 100, agent=(4242, 1000.0))

        def running(pid, started):
            return (pid, started) != (4242, 1000.0)

        self.assertEqual(
            self.show(101, running=running, forget=True), Display("completed", "codex", 1)
        )
        self.assertFalse(session_file("claude", "gone", self.dir).exists())
        self.assertTrue(session_file("codex", "done", self.dir).exists())

    def test_session_end_keeps_a_result_flash_for_one_shot_runs(self):
        self.put("codex", "one-shot", Update("completed"), 100)
        self.put("codex", "one-shot", Update(None, end=True), 101)
        self.assertEqual(self.show(102).status, "completed")
        self.put("claude", "other", Update("working"), 120)
        self.put("claude", "other", Update(None, end=True), 121)
        self.assertEqual(self.show(122), IDLE)

    def test_forget_deletes_finished_records_but_not_fresh_rewrites(self):
        self.put("codex", "old", Update("completed"), 100)
        self.put("claude", "busy", Update("working"), 100)
        path = session_file("claude", "busy", self.dir)

        def running(pid, started):
            # A hook rewrites the session while the daemon is judging it.
            self.put("claude", "busy", Update("working"), 500)
            return False

        self.show(200, running=running, forget=True)
        self.assertFalse(session_file("codex", "old", self.dir).exists())
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text())["at"], 500)

    def test_corrupt_or_foreign_files_are_ignored(self):
        (self.dir / "junk.json").write_text("{", encoding="utf-8")
        (self.dir / "future.json").write_text(
            json.dumps({"source": "codex", "status": "thinking", "at": 1}), encoding="utf-8"
        )
        (self.dir / "other.json").write_text(
            json.dumps({"source": "x", "status": "waiting", "at": 1})
        )
        self.assertEqual(self.show(2, forget=True), IDLE)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_stale_sessions_expire(self):
        self.put("codex", "c", Update("working"), 100, agent=None)
        self.assertEqual(self.show(100 + 6 * 3600 - 1).status, "working")
        self.assertEqual(self.show(100 + 6 * 3600 + 1), IDLE)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            apply("gemini", "s", Update("working"), directory=self.dir)
        with self.assertRaises(ValueError):
            apply("codex", "", Update("working"), directory=self.dir)
        with self.assertRaises(ValueError):
            apply("codex", "s", Update("sleeping"), directory=self.dir)


if __name__ == "__main__":
    unittest.main()
