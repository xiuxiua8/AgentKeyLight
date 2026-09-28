"""Watch local agent hook state and apply the highest priority light."""

from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

from .protocol import DeviceError, Light, control_interface_path, send_light
from .state import STATE_DIR, choose_status

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for name in ("idle", "working", "waiting", "completed", "error"):
        config[name] = Light.from_dict(config[name])
    for name in (
        "poll_interval_seconds",
        "completed_hold_seconds",
        "error_hold_seconds",
        "stale_session_seconds",
    ):
        value = float(config[name])
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        config[name] = value
    return config


def run(config_path: Path = CONFIG_PATH, state_dir: Path = STATE_DIR) -> None:
    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    previous: Light | None = None
    previous_config_mtime: int | None = None
    config: dict | None = None
    next_device_attempt = 0.0
    next_device_probe = 0.0
    previous_device_path: bytes | None = None
    try:
        while not stop:
            try:
                now = time.monotonic()
                if now >= next_device_probe:
                    next_device_probe = now + 2
                    try:
                        device_path = control_interface_path()
                    except (DeviceError, OSError):
                        device_path = None
                    if device_path != previous_device_path:
                        previous_device_path = device_path
                        previous = None
                mtime = config_path.stat().st_mtime_ns
                if mtime != previous_config_mtime:
                    config = load_config(config_path)
                    previous_config_mtime = mtime
                    previous = None
                    print("configuration loaded", file=sys.stderr, flush=True)
                assert config is not None
                status, source = choose_status(
                    state_dir,
                    completed_hold=config["completed_hold_seconds"],
                    error_hold=config["error_hold_seconds"],
                    stale_after=config["stale_session_seconds"],
                )
                desired = config[status]
                if desired != previous and now >= next_device_attempt:
                    try:
                        send_light(desired)
                        previous = desired
                        print(f"light={status} source={source or '-'}", file=sys.stderr, flush=True)
                    except (DeviceError, OSError) as exc:
                        print(f"device unavailable: {exc}", file=sys.stderr, flush=True)
                        next_device_attempt = time.monotonic() + 3
                time.sleep(config["poll_interval_seconds"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                print(f"configuration or state error: {exc}", file=sys.stderr, flush=True)
                time.sleep(2)
    finally:
        try:
            restored = load_config(config_path)["idle"]
            send_light(restored)
            print("idle light restored", file=sys.stderr, flush=True)
        except (OSError, ValueError, TypeError, KeyError, DeviceError) as exc:
            print(f"could not restore idle light: {exc}", file=sys.stderr, flush=True)
