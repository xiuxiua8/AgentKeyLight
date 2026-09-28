"""Command line: the hook entry point, the service, and tools to check and tune it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

STATES = ("working", "waiting", "completed", "error")
SOURCES = ("claude", "codex")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent-keylight",
        description="用 NuPhy Air60 HE 的背光显示 Codex 与 Claude Code 的状态",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="命令")
    emit = commands.add_parser("emit", help="接收一个 hook 事件（由 hooks 调用）")
    emit.add_argument("source", choices=SOURCES)
    commands.add_parser("serve", help="运行后台服务（由登录服务启动）")
    commands.add_parser("palette", help="打开网页调色板")
    preview = commands.add_parser("preview", help="在键盘上预览一种状态的灯效")
    preview.add_argument("state", choices=STATES)
    preview.add_argument("--source", choices=SOURCES, default="claude")
    demo = commands.add_parser("demo", help="依次预览全部四种状态")
    demo.add_argument("--seconds", type=float, default=4.0)
    commands.add_parser("status", help="显示键盘当前的状态和各会话")
    commands.add_parser("doctor", help="检查键盘、后台服务、hooks 和设置")
    commands.add_parser("install", help="安装 hooks 和登录服务")
    commands.add_parser("uninstall", help="移除 hooks 和登录服务")
    args = parser.parse_args(argv)

    if args.command == "emit":
        from .hook import run as run_hook

        run_hook(args.source)
    elif args.command == "serve":
        from .daemon import run

        run()
    elif args.command == "palette":
        url = _palette_url()
        try:
            _call("/api/info")
        except OSError:
            sys.exit("后台服务没有响应，请先运行 agent-keylight doctor 检查。")
        subprocess.run(["open", url], check=False)
        print(f"已在浏览器打开 {url}")
    elif args.command == "preview":
        _preview(args.state, args.source)
        print(f"正在键盘上预览「{_label(args.state)}」，约 8 秒后恢复。")
    elif args.command == "demo":
        if not 1 <= args.seconds <= 30:
            parser.error("--seconds 需要在 1 到 30 之间")
        try:
            for state in STATES:
                _preview(state, "claude")
                print(_label(state), flush=True)
                time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                _call("/api/preview/stop", {})
            except OSError:
                pass
        print("演示结束，键盘恢复当前状态。")
    elif args.command == "status":
        _status()
    elif args.command == "doctor":
        sys.exit(0 if _doctor() else 1)
    elif args.command == "install":
        from .installer import install_hooks, install_launch_agent

        for path, changed in install_hooks():
            print(f"{'已更新' if changed else '无需更改'}：{path}")
        print(f"登录服务：{install_launch_agent()}")
        print("Codex 对新增或改动的 hook 需要你在 Codex 的 /hooks 里确认信任。")
    elif args.command == "uninstall":
        from .installer import uninstall_hooks, uninstall_launch_agent

        for path, changed in uninstall_hooks():
            print(f"{'已移除' if changed else '没有找到'}：{path}")
        print("登录服务：" + ("已移除" if uninstall_launch_agent() else "没有找到"))


def _label(state: str) -> str:
    from .config import STATE_LABELS

    return STATE_LABELS[state]


def _palette_url() -> str:
    from .config import Config, ConfigError, load

    try:
        port = load().palette_port
    except (OSError, ConfigError):
        port = Config().palette_port
    return f"http://127.0.0.1:{port}"


def _call(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(_palette_url() + path, data=data)
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.add_header("X-AgentKeyLight", "1")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(json.loads(exc.read() or b"{}").get("error", str(exc))) from exc


def _preview(state: str, source: str) -> None:
    from .config import load

    try:
        _call("/api/preview", {"state": state, "config": load().to_dict(), "source": source})
    except OSError as exc:
        raise SystemExit("后台服务没有响应，请先运行 agent-keylight doctor 检查。") from exc


def _status() -> None:
    from .agents import running
    from .config import load
    from .state import SESSIONS_DIR, effective_status, select

    config = load()
    display = select(completed_seconds=config.completed_seconds, error_seconds=config.error_seconds)
    now = time.time()
    sessions = []
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            shown = effective_status(
                record,
                now,
                completed_seconds=config.completed_seconds,
                error_seconds=config.error_seconds,
                running=running,
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
        sessions.append(
            {
                "source": record["source"],
                "session": record["session_id"],
                "recorded": record["status"],
                "showing": shown or "finished",
                "seconds_ago": round(now - float(record["at"]), 1),
            }
        )
    print(
        json.dumps({"display": vars(display), "sessions": sessions}, ensure_ascii=False, indent=2)
    )


def _check(ok: bool, text: str) -> bool:
    print(f"{'✓' if ok else '✗'} {text}")
    return ok


def _doctor() -> bool:
    from .config import CONFIG_PATH, ConfigError, load
    from .device import MODES, DeviceError, Keyboard
    from .installer import (
        CLAUDE_EVENTS,
        CLAUDE_SETTINGS,
        CODEX_EVENTS,
        CODEX_HOOKS,
        hook_command,
        service_running,
    )

    healthy = True
    try:
        with Keyboard.open() as keyboard:
            mode = keyboard.current_mode()
            light = keyboard.main_light(mode)
            name = {"Gaming": "游戏", "Windows": "Windows", "Mac": "Mac"}[MODES[mode]]
            healthy &= _check(
                True,
                f"键盘已连接：固件 {keyboard.firmware_version()}，{name} 模式，背光亮度 {light.brightness}%",
            )
            if light.brightness == 0:
                healthy &= _check(False, "当前模式背光亮度为 0，灯效会看不见；请调高亮度")
    except DeviceError as exc:
        healthy &= _check(False, f"找不到键盘的控制接口：{exc}")
    try:
        load()
        healthy &= _check(True, f"设置有效：{CONFIG_PATH}")
    except (OSError, ConfigError) as exc:
        healthy &= _check(False, f"设置有误：{exc}")
    running = service_running()
    healthy &= _check(
        running,
        "后台服务正在运行" if running else "后台服务没有运行，请运行 agent-keylight install",
    )
    try:
        _call("/api/info")
        healthy &= _check(True, f"网页调色板可用：{_palette_url()}")
    except OSError:
        healthy &= _check(False, "网页调色板没有响应")
    for path, source, events in (
        (CODEX_HOOKS, "codex", CODEX_EVENTS),
        (CLAUDE_SETTINGS, "claude", CLAUDE_EVENTS),
    ):
        try:
            hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks", {})
        except (OSError, ValueError):
            hooks = {}
        command = hook_command(source)
        missing = [
            event
            for event in events
            if not any(
                isinstance(handler, dict) and handler.get("command") == command
                for group in hooks.get(event, [])
                if isinstance(group, dict)
                for handler in group.get("hooks", [])
            )
        ]
        name = "Codex" if source == "codex" else "Claude Code"
        text = f"{name} hooks 已安装" if not missing else f"{name} 缺少 hooks：{', '.join(missing)}"
        healthy &= _check(not missing, text)
    return healthy


if __name__ == "__main__":
    main()
