"""Idempotent user-level hook and launch-agent installation."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .daemon import PROJECT_DIR

LABEL = "com.zilong.agent-keylight"
CODEX_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "Interrupt",
)
CLAUDE_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Elicitation",
    "ElicitationResult",
    "Stop",
    "StopFailure",
)


def _read_json(path: Path) -> dict:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object: {path}")
        return value
    return {}


def _write_json_safely(path: Path, value: dict) -> bool:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.agent-keylight-backup-{stamp}")
        shutil.copy2(path, backup)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        temp.chmod(stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def _hook_command(source: str) -> str:
    exe = PROJECT_DIR / ".venv" / "bin" / "agent-keylight"
    if not exe.exists():
        raise FileNotFoundError(f"create the project environment first: {exe}")
    return f"{exe} emit {source}"


def _add_hooks(path: Path, source: str, events: tuple[str, ...]) -> bool:
    value = _read_json(path)
    hooks = value.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise TypeError(f"hooks must be a JSON object: {path}")
    command = _hook_command(source)
    changed = False
    for event in events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise TypeError(f"hook event must be a list: {event}")
        exists = any(
            handler.get("type") == "command" and handler.get("command") == command
            for group in groups
            if isinstance(group, dict)
            for handler in group.get("hooks", [])
            if isinstance(handler, dict)
        )
        if not exists:
            timeout = 3 if source == "codex" and event in {"SessionEnd", "Interrupt"} else 5
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
            changed = True
    return _write_json_safely(path, value) if changed else False


def install_hooks() -> list[tuple[Path, bool]]:
    return [
        (
            Path.home() / ".codex" / "hooks.json",
            _add_hooks(Path.home() / ".codex" / "hooks.json", "codex", CODEX_EVENTS),
        ),
        (
            Path.home() / ".claude" / "settings.json",
            _add_hooks(Path.home() / ".claude" / "settings.json", "claude", CLAUDE_EVENTS),
        ),
    ]


def install_launch_agent() -> Path:
    exe = PROJECT_DIR / ".venv" / "bin" / "agent-keylight"
    if not exe.exists():
        raise FileNotFoundError(exe)
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log_dir = Path.home() / "Library" / "Logs"
    path.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    definition = {
        "Label": LABEL,
        "ProgramArguments": [str(exe), "serve"],
        "WorkingDirectory": str(PROJECT_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / "AgentKeyLight.log"),
        "StandardErrorPath": str(log_dir / "AgentKeyLight.log"),
    }
    if path.exists():
        current = plistlib.loads(path.read_bytes())
        if current != definition:
            raise FileExistsError(f"existing launch agent differs: {path}")
    else:
        path.write_bytes(plistlib.dumps(definition))
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    probe = subprocess.run(["launchctl", "print", target], capture_output=True, check=False)
    if probe.returncode != 0:
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", target], check=True)
    return path
