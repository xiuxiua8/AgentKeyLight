"""Agent sessions reported by hooks, and the one status the keyboard shows.

Each session is one small JSON file written atomically by the hook. The daemon
reads them all, drops sessions whose agent process is gone, and picks the most
important status: waiting > error > working > completed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR

SESSIONS_DIR = APP_DIR / "sessions"
SOURCES = ("claude", "codex")
PRIORITY = {"waiting": 4, "error": 3, "working": 2, "completed": 1}
# Claude Code sends a permission_prompt notification about six seconds after a
# permission prompt that is still unanswered. Without one, the user answered.
PERMISSION_CONFIRM_SECONDS = 10.0
STALE_SECONDS = 6 * 3600.0
QUESTION_TOOLS = {
    "claude": {"AskUserQuestion", "ExitPlanMode"},
    "codex": {"request_user_input"},
}


@dataclass(frozen=True)
class Update:
    """What one hook event means for its session. status None means idle."""

    status: str | None
    end: bool = False
    confirmed: bool = True


def interpret(source: str, event: dict) -> Update | None:
    """Map a hook event to a session update; None means the event changes nothing."""
    name = event.get("hook_event_name")
    tool = event.get("tool_name")
    if name == "SessionStart":
        # Compaction restarts the context mid-turn; the task keeps running.
        return None if event.get("source") == "compact" else Update(None)
    if name == "SessionEnd":
        return Update(None, end=True)
    if name == "UserPromptSubmit":
        return Update("working")
    if name == "PreToolUse":
        return Update("waiting") if tool in QUESTION_TOOLS[source] else None
    if name == "PermissionRequest":
        if tool in QUESTION_TOOLS[source]:
            return Update("waiting")
        return Update("waiting", confirmed=source != "claude")
    if name in {"PostToolUse", "PostToolUseFailure", "ElicitationResult"}:
        return Update("working")
    if name == "Elicitation":
        return Update("waiting")
    if name == "Notification" and source == "claude":
        kind = event.get("notification_type")
        if kind in {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog"}:
            return Update("waiting")
        if kind == "idle_prompt":
            # Sent a minute after Claude stopped responding, including after Esc,
            # which never fires Stop.
            return Update(None)
        return None
    if name == "Stop":
        return Update("completed")
    if name == "StopFailure":
        # An API error inside a subagent does not end the main turn.
        return None if event.get("agent_id") else Update("error")
    if name == "Interrupt":
        return Update("error")
    return None


def session_file(source: str, session_id: str, directory: Path = SESSIONS_DIR) -> Path:
    digest = hashlib.sha256(f"{source}\0{session_id}".encode()).hexdigest()[:32]
    return directory / f"{source}-{digest}.json"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def apply(
    source: str,
    session_id: str,
    update: Update,
    *,
    directory: Path = SESSIONS_DIR,
    now: float | None = None,
    agent: tuple[int, float] | None = None,
) -> None:
    if source not in SOURCES or not session_id:
        raise ValueError("invalid source or session id")
    path = session_file(source, session_id, directory)
    if update.end:
        # Keep a result that is still showing, so a one-shot run can end at once.
        record = _read(path)
        if record is None or record.get("status") not in {"completed", "error"}:
            path.unlink(missing_ok=True)
        return
    if update.status is None:
        path.unlink(missing_ok=True)
        return
    if update.status not in PRIORITY:
        raise ValueError(f"invalid status: {update.status}")
    record = {
        "source": source,
        "session_id": session_id,
        "status": update.status,
        "at": time.time() if now is None else now,
    }
    if update.status == "waiting":
        record["confirmed"] = update.confirmed
    if agent is not None:
        record["pid"], record["started"] = agent
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{path.name}.{os.getpid()}.tmp"
    try:
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass(frozen=True)
class Display:
    """What the keyboard should show."""

    status: str
    source: str | None = None
    count: int = 0


IDLE = Display("idle")


def effective_status(
    record: dict,
    now: float,
    *,
    completed_seconds: float,
    error_seconds: float,
    running: Callable[[int, float], bool],
) -> str | None:
    """The status a record shows now, or None once it has run its course."""
    status = record["status"]
    if status not in PRIORITY:
        raise ValueError(f"unknown status: {status}")
    age = max(0.0, now - float(record["at"]))
    if status == "completed":
        return status if age <= completed_seconds else None
    if status == "error":
        return status if age <= error_seconds else None
    if age > STALE_SECONDS:
        return None
    if "pid" in record and not running(int(record["pid"]), float(record["started"])):
        return None
    if status == "waiting" and not record.get("confirmed", True):
        return "working" if age > PERMISSION_CONFIRM_SECONDS else "waiting"
    return status


def select(
    directory: Path = SESSIONS_DIR,
    *,
    now: float | None = None,
    completed_seconds: float = 12,
    error_seconds: float = 20,
    running: Callable[[int, float], bool] | None = None,
    forget: bool = False,
) -> Display:
    """Pick the status to show. With forget, delete records that no longer show."""
    if running is None:
        from .agents import running as process_running

        running = process_running
    clock = time.time() if now is None else now
    shown: list[tuple[str, dict]] = []
    for path in directory.glob("*.json"):
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        record = _read(path)
        try:
            if record is None or record.get("source") not in SOURCES:
                raise ValueError
            status = effective_status(
                record,
                clock,
                completed_seconds=completed_seconds,
                error_seconds=error_seconds,
                running=running,
            )
        except (KeyError, TypeError, ValueError):
            status = None
        if status is not None:
            shown.append((status, record))
        elif forget:
            # A hook may have rewritten the file since it was read; keep that.
            try:
                if path.stat().st_mtime_ns == modified:
                    path.unlink()
            except OSError:
                pass
    if not shown:
        return IDLE
    status, winner = max(shown, key=lambda item: (PRIORITY[item[0]], float(item[1]["at"])))
    return Display(status, winner["source"], sum(1 for item in shown if item[0] == status))
