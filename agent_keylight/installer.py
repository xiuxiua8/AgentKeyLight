"""Idempotent user-level hook and login-agent installation, and its removal."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
EXECUTABLE = PROJECT_DIR / ".venv" / "bin" / "agent-keylight"
LABEL = "com.zilong.agent-keylight"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "AgentKeyLight.log"
CODEX_HOOKS = Path.home() / ".codex" / "hooks.json"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
# Event name -> matcher. Question tools only, so other tool calls skip the hook.
CODEX_EVENTS: dict[str, str | None] = {
    "SessionStart": None,
    "SessionEnd": None,
    "UserPromptSubmit": None,
    "PreToolUse": "^request_user_input$",
    "PermissionRequest": None,
    "PostToolUse": None,
    "Stop": None,
    "Interrupt": None,
}
CLAUDE_EVENTS: dict[str, str | None] = {
    "SessionStart": None,
    "SessionEnd": None,
    "UserPromptSubmit": None,
    "PreToolUse": "AskUserQuestion|ExitPlanMode",
    "PermissionRequest": None,
    "PostToolUse": None,
    "PostToolUseFailure": None,
    "Notification": None,
    "Elicitation": None,
    "ElicitationResult": None,
    "Stop": None,
    "StopFailure": None,
}


def hook_command(source: str) -> str:
    return f"{EXECUTABLE} emit {source}"


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
        backup = path.with_name(
            f"{path.name}.agent-keylight-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(path, backup)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        temp.chmod(stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def _ours(group: object, command: str) -> bool:
    return isinstance(group, dict) and any(
        isinstance(handler, dict) and handler.get("command") == command
        for handler in group.get("hooks", [])
    )


def _timeout(source: str, event: str) -> int:
    # Codex caps Interrupt hooks at three seconds.
    return 3 if source == "codex" and event in {"SessionEnd", "Interrupt"} else 5


def add_hooks(path: Path, source: str, events: dict[str, str | None], command: str) -> bool:
    """Add or update this program's hook groups, leaving every other entry alone."""
    value = _read_json(path)
    hooks = value.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise TypeError(f"hooks must be a JSON object: {path}")
    for event, matcher in events.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise TypeError(f"hook event must be a list: {event}")
        handler = {"type": "command", "command": command, "timeout": _timeout(source, event)}
        group = {"matcher": matcher, "hooks": [handler]} if matcher else {"hooks": [handler]}
        mine = [index for index, entry in enumerate(groups) if _ours(entry, command)]
        # Rewrite only groups this installer created: one handler, nothing else attached.
        own = [i for i in mine if len(groups[i].get("hooks", [])) == 1]
        if own:
            groups[own[0]] = group
            for index in reversed(own[1:]):
                del groups[index]
        elif not mine:
            groups.append(group)
    return _write_json_safely(path, value)


def remove_hooks(path: Path, command: str) -> bool:
    if not path.exists():
        return False
    value = _read_json(path)
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if _ours(group, command):
                handlers = [
                    h
                    for h in group.get("hooks", [])
                    if not (isinstance(h, dict) and h.get("command") == command)
                ]
                if not handlers:
                    continue
                group = {**group, "hooks": handlers}
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return _write_json_safely(path, value)


def install_hooks() -> list[tuple[Path, bool]]:
    if not EXECUTABLE.exists():
        raise FileNotFoundError(f"create the project environment first: {EXECUTABLE}")
    return [
        (CODEX_HOOKS, add_hooks(CODEX_HOOKS, "codex", CODEX_EVENTS, hook_command("codex"))),
        (
            CLAUDE_SETTINGS,
            add_hooks(CLAUDE_SETTINGS, "claude", CLAUDE_EVENTS, hook_command("claude")),
        ),
    ]


def uninstall_hooks() -> list[tuple[Path, bool]]:
    return [
        (CODEX_HOOKS, remove_hooks(CODEX_HOOKS, hook_command("codex"))),
        (CLAUDE_SETTINGS, remove_hooks(CLAUDE_SETTINGS, hook_command("claude"))),
    ]


def launch_agent_definition() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(EXECUTABLE), "serve"],
        "WorkingDirectory": str(PROJECT_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        # Animations need steady 30 fps timers; background throttling makes them stutter.
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install_launch_agent() -> Path:
    if not EXECUTABLE.exists():
        raise FileNotFoundError(EXECUTABLE)
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    definition = plistlib.dumps(launch_agent_definition())
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    changed = not LAUNCH_AGENT.exists() or LAUNCH_AGENT.read_bytes() != definition
    loaded = _launchctl("print", target, check=False).returncode == 0
    if changed:
        if loaded:
            _launchctl("bootout", target, check=False)
            loaded = False
        LAUNCH_AGENT.write_bytes(definition)
    if not loaded:
        _launchctl("bootstrap", domain, str(LAUNCH_AGENT))
    _launchctl("kickstart", "-k", target)
    return LAUNCH_AGENT


def uninstall_launch_agent() -> bool:
    target = f"gui/{os.getuid()}/{LABEL}"
    _launchctl("bootout", target, check=False)
    if LAUNCH_AGENT.exists():
        LAUNCH_AGENT.unlink()
        return True
    return False


def service_running() -> bool:
    result = _launchctl("print", f"gui/{os.getuid()}/{LABEL}", check=False)
    return result.returncode == 0 and "state = running" in result.stdout
