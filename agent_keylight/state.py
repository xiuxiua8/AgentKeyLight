"""Store hook events per session and choose one status for the keyboard."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

STATE_DIR = Path.home() / "Library" / "Application Support" / "AgentKeyLight" / "sessions"
STATES = {"working", "waiting", "completed", "error"}
PRIORITY = {"waiting": 4, "error": 3, "working": 2, "completed": 1}


def session_file(source: str, session_id: str, directory: Path = STATE_DIR) -> Path:
    digest = hashlib.sha256(f"{source}\0{session_id}".encode()).hexdigest()[:32]
    return directory / f"{source}-{digest}.json"


def write_event(
    source: str,
    session_id: str,
    status: str | None,
    directory: Path = STATE_DIR,
    now: float | None = None,
) -> None:
    if source not in {"codex", "claude"} or not session_id:
        raise ValueError("invalid source or session id")
    path = session_file(source, session_id, directory)
    if status is None:
        path.unlink(missing_ok=True)
        return
    if status not in STATES:
        raise ValueError(f"invalid status: {status}")
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "source": source,
        "session_id": session_id,
        "status": status,
        "at": now if now is not None else time.time(),
    }
    temp = directory / f".{path.name}.{os.getpid()}.tmp"
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def hook_status(source: str, event: dict) -> str | None | bool:
    """False means ignore. None removes a session from the active set."""
    name = event.get("hook_event_name")
    if name == "SessionEnd":
        return None
    if name == "SessionStart":
        return None
    if name == "UserPromptSubmit":
        return "working"
    if name in {"PermissionRequest", "Elicitation"}:
        return "waiting"
    if name in {"PostToolUse", "PostToolUseFailure"}:
        return "working"
    if name == "ElicitationResult" and source == "claude":
        return "working"
    if name == "Notification" and source == "claude":
        if event.get("notification_type") == "permission_prompt":
            return "waiting"
        return False
    if name == "Stop":
        return "completed"
    if name in {"StopFailure", "Interrupt"}:
        return "error"
    return False


def choose_status(
    directory: Path = STATE_DIR,
    *,
    now: float | None = None,
    completed_hold: float = 12,
    error_hold: float = 20,
    stale_after: float = 21600,
) -> tuple[str, str | None]:
    clock = now if now is not None else time.time()
    candidates: list[dict] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            age = max(0, clock - float(data["at"]))
            status = data["status"]
            if status not in STATES or age > stale_after:
                continue
            if status == "completed" and age > completed_hold:
                continue
            if status == "error" and age > error_hold:
                continue
            candidates.append(data)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        return "idle", None
    winner = max(candidates, key=lambda x: (PRIORITY[x["status"]], float(x["at"])))
    return winner["status"], winner["source"]
