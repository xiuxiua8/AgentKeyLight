"""Small, non-blocking hook entry point for Codex and Claude Code."""

from __future__ import annotations

import json
import sys
import time

from .state import STATE_DIR, end_session, hook_status, write_event


def run(source: str) -> None:
    try:
        payload = sys.stdin.buffer.read(262145)
        if len(payload) > 262144:
            raise ValueError("hook event is too large")
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise TypeError("hook event is not an object")
        status = hook_status(source, event)
        session_id = event.get("session_id")
        if status is not False and isinstance(session_id, str) and session_id:
            if event.get("hook_event_name") == "SessionEnd":
                end_session(source, session_id)
            else:
                write_event(source, session_id, status)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Hook status is advisory. Never fail or interrupt the coding agent.
        try:
            path = STATE_DIR.parent / "hook-errors.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {source}: {exc}\n")
        except OSError:
            pass
    finally:
        # Codex requires JSON stdout for Stop; an empty object is a no-op.
        if source == "codex":
            sys.stdout.write("{}\n")
