"""User commands and hook entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time

from .daemon import load_config, run
from .hook import run as run_hook
from .installer import install_hooks, install_launch_agent
from .protocol import DeviceError, matching_interfaces, send_light
from .state import choose_status


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-keylight")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check the exact keyboard control interface")
    commands.add_parser("status", help="show the light selected by current agent sessions")
    commands.add_parser("serve", help="run the background light controller")
    commands.add_parser("install", help="install hooks and a macOS login agent")
    demo = commands.add_parser("demo", help="show each state and restore the idle light")
    demo.add_argument("--seconds", type=float, default=2)
    emit = commands.add_parser("emit", help="receive one Codex or Claude Code hook event")
    emit.add_argument("source", choices=("codex", "claude"))
    args = parser.parse_args()

    if args.command == "emit":
        run_hook(args.source)
        return
    if args.command == "serve":
        run()
        return
    if args.command == "install":
        for path, changed in install_hooks():
            print(f"{'updated' if changed else 'already configured'}: {path}")
        print(f"launch agent: {install_launch_agent()}")
        print("Codex hooks need review and trust in Codex's /hooks menu.")
        return
    if args.command == "status":
        config = load_config()
        status, source = choose_status(
            completed_hold=config["completed_hold_seconds"],
            error_hold=config["error_hold_seconds"],
            stale_after=config["stale_session_seconds"],
        )
        print(json.dumps({"status": status, "source": source}, ensure_ascii=False))
        return
    if args.command == "doctor":
        import hid

        from .protocol import PID, VID

        matches = matching_interfaces(hid.enumerate(VID, PID))
        print(f"Air60 HE control interfaces: {len(matches)}")
        for item in matches:
            print(
                f"VID=0x{item['vendor_id']:04x} PID=0x{item['product_id']:04x} "
                f"usage_page={item['usage_page']} usage={item['usage']}"
            )
        if len(matches) != 1:
            raise SystemExit(1)
        return
    if args.command == "demo":
        if args.seconds <= 0:
            parser.error("--seconds must be positive")
        config = load_config()
        try:
            for status in ("working", "waiting", "completed", "error"):
                send_light(config[status])
                print(status, flush=True)
                time.sleep(args.seconds)
        except (DeviceError, OSError) as exc:
            print(f"device error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        finally:
            send_light(config["idle"])
            print("idle restored", flush=True)


if __name__ == "__main__":
    main()
