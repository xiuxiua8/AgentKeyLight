"""Local web palette: tune each state's look with a live preview on the keyboard.

The server listens on 127.0.0.1 only. It rejects requests addressed to any
other host name, so a web page cannot reach it through DNS rebinding, and it
requires a custom header on every change, which browsers never send across
origins without a CORS approval this server does not give.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from . import config as settings
from .config import SOURCE_LABELS, SOURCES, STATE_LABELS, STATES, Config, ConfigError
from .effects import EFFECTS
from .layout import HEIGHT, KEYS, WIDTH

MAX_BODY = 64 * 1024
HEADER = "X-AgentKeyLight"


class PaletteServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, bridge, watcher, clock: Callable[[], float]) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.bridge = bridge
        self.watcher = watcher
        self.clock = clock
        bound = self.server_address[1]
        self.origins = {f"http://127.0.0.1:{bound}", f"http://localhost:{bound}"}


def start_server(
    bridge, watcher, port: int, clock: Callable[[], float] = time.monotonic
) -> PaletteServer | None:
    from .daemon import log

    try:
        server = PaletteServer(port, bridge, watcher, clock)
    except OSError as exc:
        log(f"palette unavailable on port {port}: {exc}")
        return None
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, name="palette", daemon=True
    ).start()
    log(f"palette at http://127.0.0.1:{server.server_address[1]}/")
    return server


def info(watcher) -> dict:
    return {
        "config": watcher.current.to_dict(),
        "defaults": Config().to_dict(),
        "configPath": str(watcher.path),
        "effects": [
            {"key": key, "label": effect.label, "description": effect.description}
            for key, effect in EFFECTS.items()
        ],
        "states": [{"key": state, "label": STATE_LABELS[state]} for state in STATES],
        "sources": [{"key": source, "label": SOURCE_LABELS[source]} for source in SOURCES],
        "layout": {
            "width": WIDTH,
            "height": HEIGHT,
            "keys": [
                {"index": key.index, "label": key.label, "x": key.x, "y": key.y, "w": key.w}
                for key in KEYS
            ],
        },
    }


class Handler(BaseHTTPRequestHandler):
    server: PaletteServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _allowed_host(self) -> bool:
        return f"http://{self.headers.get('Host', '')}" in self.server.origins

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def do_GET(self) -> None:
        if not self._allowed_host():
            self._error(HTTPStatus.FORBIDDEN, "只接受本机访问")
            return
        if self.path == "/":
            page = files("agent_keylight").joinpath("palette.html").read_bytes()
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        elif self.path == "/api/info":
            self._json(info(self.server.watcher))
        elif self.path == "/api/live":
            self._json(asdict(self.server.bridge.want_mirror(self.server.clock())))
        else:
            self._error(HTTPStatus.NOT_FOUND, "没有这个地址")

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        if (
            not self._allowed_host()
            or (origin is not None and origin not in self.server.origins)
            or self.headers.get(HEADER) != "1"
            or not self.headers.get("Content-Type", "").startswith("application/json")
        ):
            self._error(HTTPStatus.FORBIDDEN, "只接受调色板页面的请求")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BODY:
                raise ValueError
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "请求内容不是有效的 JSON")
            return
        try:
            self._route(body)
        except ConfigError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _route(self, body: object) -> None:
        bridge = self.server.bridge
        if self.path == "/api/preview":
            if not isinstance(body, dict) or body.get("state") not in STATES:
                raise ConfigError("不认识要预览的状态")
            source = body.get("source", "claude")
            if source not in SOURCES:
                raise ConfigError("不认识的来源")
            config = Config.from_dict(body.get("config", {}))
            bridge.start_preview(body["state"], config, source, self.server.clock())
            self._json({"ok": True})
        elif self.path == "/api/preview/stop":
            bridge.stop_preview()
            self._json({"ok": True})
        elif self.path == "/api/config":
            config = Config.from_dict(body)
            settings.save(config, self.server.watcher.path)
            self._json({"config": config.to_dict()})
        else:
            self._error(HTTPStatus.NOT_FOUND, "没有这个地址")
