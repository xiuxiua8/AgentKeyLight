"""Hook entry point for Codex and Claude Code. It is quick and never fails the agent."""

from __future__ import annotations

import json
import sys
import time

from .state import SESSIONS_DIR, apply, interpret

MAX_EVENT_BYTES = 32 * 1024 * 1024


def run(source: str) -> None:
    try:
        payload = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(payload) > MAX_EVENT_BYTES:
            raise ValueError("hook event is too large")
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise TypeError("hook event is not an object")
        update = interpret(source, event)
        session_id = event.get("session_id")
        if update is not None and isinstance(session_id, str) and session_id:
            agent = None
            if update.status is not None:
                from .agents import agent as find_agent

                process = find_agent()
                agent = (process.pid, process.started) if process else None
            apply(source, session_id, update, agent=agent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Status lights are advisory; record the problem and let the agent go on.
        try:
            path = SESSIONS_DIR.parent / "hook-errors.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {source}: {exc}\n")
        except OSError:
            pass
    finally:
        # Codex reads JSON from Stop and Interrupt hooks; an empty object changes nothing.
        if source == "codex":
            sys.stdout.write("{}\n")
