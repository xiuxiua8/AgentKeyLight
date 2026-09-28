"""Background service: watch agent sessions and stream the matching animation.

One thread owns the keyboard. It renders frames for the status chosen from the
hook records and sends them with LedSyncDownload. When nothing needs showing it
stops sending, and the keyboard returns to its own lighting by itself.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config as settings
from .config import Config, ConfigError
from .device import FRAME_BYTES, MODES, DeviceError, Keyboard
from .effects import (
    Color,
    Frame,
    Look,
    mix,
    parse_color,
    render,
    source_levels,
    to_light,
    to_screen,
)
from .layout import EXTRA_LED, LED_COUNT, SOURCE_PATHS, SPACE
from .state import IDLE, SESSIONS_DIR, Display, select

CROSSFADE_SECONDS = 0.3
FADE_OUT_SECONDS = 0.6
KEEPALIVE_SECONDS = 0.5
SELECT_INTERVAL = 0.2
CONFIG_CHECK_INTERVAL = 0.5
RECONNECT_SECONDS = 2.0
PREVIEW_SECONDS = 8.0
MIRROR_INTERVAL = 0.1
MIRROR_IDLE_AFTER = 2.0
INFO_INTERVAL = 3.0
LOG_PATH = Path.home() / "Library" / "Logs" / "AgentKeyLight.log"
BLACK: Color = (0.0, 0.0, 0.0)
# Source keys never go fully dark, so their color always tells the agent apart; in a
# chase this dim agent color is the background the highlight runs over.
SOURCE_FLOOR = 0.25


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", file=sys.stderr, flush=True)


def to_bytes(frame: Frame, config: Config) -> bytes:
    """LED values for a frame of screen colors, corrected and dimmed as configured."""
    out = bytearray()
    for color in frame:
        for channel in color:
            value = min(1.0, max(0.0, channel))
            if config.color_correction:
                value = to_light(value)
            out.append(round(value * config.brightness * 255))
    return bytes(out)


def screen_colors(leds: bytes) -> list[str]:
    """How LED values read back from the keyboard look, as screen colors.

    LEDs emit light in proportion to their values, so the same conversion
    applies whether the colors came from this program or from the keyboard.
    """
    return [
        "#" + "".join(f"{round(to_screen(value / 255) * 255):02x}" for value in leds[i : i + 3])
        for i in range(0, len(leds), 3)
    ]


def paint_source(frame: Frame, config: Config, source: str, elapsed: float) -> None:
    """Show the agent's color on the configured keys, in the configured style."""
    color = parse_color(config.source_colors[source])
    path, closed = SOURCE_PATHS[config.source_area]
    levels = source_levels(config.source_style, path, closed, config.source_speed, elapsed)
    for index, level in zip(path, levels, strict=True):
        frame[index] = mix(BLACK, color, SOURCE_FLOOR + (1 - SOURCE_FLOOR) * level)
    frame[EXTRA_LED] = frame[SPACE]


class Animator:
    """Turns what should be shown into frames, with crossfades and a fade to idle."""

    def __init__(self) -> None:
        self._key: tuple[Display, Look | None] = (IDLE, None)
        self._started = 0.0
        self._last: Frame | None = None
        self._fade_from: Frame | None = None
        self._fade_started = 0.0

    def frame(
        self, display: Display, look: Look | None, config: Config, now: float
    ) -> bytes | None:
        """The next frame, or None when the keyboard should show its own lighting."""
        if (display, look) != self._key:
            # Any change crossfades; the animation restarts only for a new state, so a
            # second task or a color tweak does not reset the running motion.
            if display.status != self._key[0].status:
                self._started = now
            self._key = (display, look)
            self._fade_from = self._last
            self._fade_started = now
        if display.status == "idle" or look is None:
            if self._fade_from is None:
                self._last = None
                return None
            progress = (now - self._fade_started) / FADE_OUT_SECONDS
            if progress >= 1:
                self._last = self._fade_from = None
                return None
            self._last = [mix(color, BLACK, progress) for color in self._fade_from]
            return to_bytes(self._last, config)
        frame = render(look, now - self._started, display.count)
        if config.source_enabled and display.source in config.source_colors:
            paint_source(frame, config, display.source, now - self._started)
        if self._fade_from is not None:
            progress = (now - self._fade_started) / CROSSFADE_SECONDS
            if progress < 1:
                frame = [mix(old, new, progress) for old, new in zip(self._fade_from, frame)]
            else:
                self._fade_from = None
        self._last = frame
        return to_bytes(frame, config)


class KeyboardLink:
    """Keeps the keyboard open and sends frames, resending before the firmware lets go."""

    def __init__(self, open_keyboard: Callable[[], Keyboard]) -> None:
        self._open = open_keyboard
        self.keyboard: Keyboard | None = None
        self._retry_at = 0.0
        self._last: bytes | None = None
        self._sent_at = 0.0
        self._reported = False

    def connect(self, now: float) -> Keyboard | None:
        if self.keyboard is None and now >= self._retry_at:
            try:
                self.keyboard = self._open()
                self._reported = False
                log("keyboard connected")
            except DeviceError as exc:
                self._retry_at = now + RECONNECT_SECONDS
                if not self._reported:
                    log(f"keyboard unavailable: {exc}")
                    self._reported = True
        return self.keyboard

    def drop(self, now: float, reason: Exception) -> None:
        log(f"keyboard lost: {reason}")
        if self.keyboard is not None:
            try:
                self.keyboard.close()
            except OSError:
                pass
        self.keyboard = None
        self._last = None
        self._retry_at = now + RECONNECT_SECONDS
        self._reported = True

    def show(self, frame: bytes | None, now: float) -> None:
        if frame is None:
            self._last = None
            return
        if frame == self._last and now - self._sent_at < KEEPALIVE_SECONDS:
            return
        keyboard = self.connect(now)
        if keyboard is None:
            return
        try:
            keyboard.show_frame(frame)
        except DeviceError as exc:
            self.drop(now, exc)
            return
        self._last = frame
        self._sent_at = now

    def close(self) -> None:
        if self.keyboard is not None:
            self.keyboard.close()
            self.keyboard = None


@dataclass
class Live:
    """What the palette page shows about the keyboard. connected is None until checked."""

    connected: bool | None = None
    mode: str | None = None
    brightness: int | None = None
    leds: list[str] = field(default_factory=list)
    status: str = "idle"
    source: str | None = None
    preview: str | None = None


class Bridge:
    """Thread-safe exchange between the palette web server and the service loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._preview: tuple[str, Config, str, float] | None = None
        self._live = Live()
        self._mirror_wanted_at = float("-inf")

    def start_preview(self, state: str, config: Config, source: str, now: float) -> None:
        """Show one state with unsaved settings, including brightness and source colors."""
        with self._lock:
            self._preview = (state, config, source, now + PREVIEW_SECONDS)

    def stop_preview(self) -> None:
        with self._lock:
            self._preview = None

    def preview(self, now: float) -> tuple[Display, Config] | None:
        with self._lock:
            if self._preview is None:
                return None
            state, config, source, until = self._preview
            if now >= until:
                self._preview = None
                return None
            return Display(state, source, 1), config

    def want_mirror(self, now: float) -> Live:
        with self._lock:
            self._mirror_wanted_at = now
            return Live(**vars(self._live))

    def mirror_wanted(self, now: float) -> bool:
        with self._lock:
            return now - self._mirror_wanted_at < MIRROR_IDLE_AFTER

    def publish(self, **changes: object) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._live, name, value)


class SettingsWatcher:
    """Reloads the settings file when it changes, keeping the last good settings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._modified: int | None = None
        self._checked = float("-inf")
        self.current = Config()

    def refresh(self, now: float) -> Config:
        if now - self._checked < CONFIG_CHECK_INTERVAL:
            return self.current
        self._checked = now
        try:
            if not self.path.exists():
                settings.save(Config(), self.path)
            modified = self.path.stat().st_mtime_ns
            if modified != self._modified:
                self._modified = modified
                self.current = settings.load(self.path)
                log(f"settings loaded from {self.path}")
        except (OSError, ConfigError) as exc:
            log(f"settings not applied, keeping the previous ones: {exc}")
        return self.current


def serve(
    config_path: Path = settings.CONFIG_PATH,
    sessions_dir: Path = SESSIONS_DIR,
    *,
    open_keyboard: Callable[[], Keyboard] = Keyboard.open,
    bridge: Bridge | None = None,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
    wait: Callable[[float], object] | None = None,
    sleep: Callable[[float], object] = time.sleep,
    palette: bool = True,
) -> None:
    """Run until stop is set. wait paces frames and returns early on stop; sleep
    paces the final fade, which runs after stop is already set."""
    stop = stop or threading.Event()
    wait = wait or stop.wait
    bridge = bridge or Bridge()
    watcher = SettingsWatcher(config_path)
    animator = Animator()
    link = KeyboardLink(open_keyboard)
    config = watcher.refresh(clock())
    server = None
    if palette:
        from .palette import start_server

        server = start_server(bridge, watcher, config.palette_port)
    display = IDLE
    shown: tuple[Display, bool] = (IDLE, False)
    next_select = next_mirror = next_info = 0.0
    try:
        while not stop.is_set():
            now = clock()
            config = watcher.refresh(now)
            if now >= next_select:
                next_select = now + SELECT_INTERVAL
                display = select(
                    sessions_dir,
                    now=wall(),
                    completed_seconds=config.completed_seconds,
                    error_seconds=config.error_seconds,
                    forget=True,
                )
            preview = bridge.preview(now)
            target, shown_config = preview or (display, config)
            look = shown_config.looks.get(target.status)
            if (target, preview is not None) != shown:
                shown = (target, preview is not None)
                label = f"preview {target.status}" if preview else target.status
                log(f"show {label} source={target.source or '-'} sessions={target.count}")
            bridge.publish(
                status=display.status,
                source=display.source,
                preview=target.status if preview else None,
            )
            frame = animator.frame(target, look, shown_config, now)
            link.show(frame, now)
            mirror = bridge.mirror_wanted(now)
            if mirror and now >= next_info:
                next_info = now + INFO_INTERVAL
                _publish_info(link, bridge, now)
            if mirror and now >= next_mirror:
                next_mirror = now + MIRROR_INTERVAL
                _publish_mirror(link, bridge, now)
            # Idle: nothing to animate, so only wake up to look for new sessions.
            interval = 1 / config.fps if frame is not None else SELECT_INTERVAL
            if mirror:
                interval = min(interval, MIRROR_INTERVAL)
            wait(max(0.0, interval - (clock() - now)))
        _fade_out(animator, link, config, clock, sleep)
    finally:
        if server is not None:
            server.shutdown()
        link.close()
        log("stopped")


def _fade_out(
    animator: Animator,
    link: KeyboardLink,
    config: Config,
    clock: Callable[[], float],
    sleep: Callable[[float], object],
) -> None:
    """On shutdown, dim the last frame instead of leaving it frozen for 1.6 seconds."""
    deadline = clock() + FADE_OUT_SECONDS + 0.2
    while clock() < deadline:
        frame = animator.frame(IDLE, None, config, clock())
        if frame is None:
            return
        link.show(frame, clock())
        sleep(1 / config.fps)


def _publish_info(link: KeyboardLink, bridge: Bridge, now: float) -> None:
    keyboard = link.connect(now)
    if keyboard is None:
        bridge.publish(connected=False, mode=None, brightness=None, leds=[])
        return
    try:
        mode = keyboard.current_mode()
        light = keyboard.main_light(mode)
    except DeviceError as exc:
        link.drop(now, exc)
        return
    bridge.publish(connected=True, mode=MODES[mode], brightness=light.brightness)


def _publish_mirror(link: KeyboardLink, bridge: Bridge, now: float) -> None:
    keyboard = link.connect(now)
    if keyboard is None:
        bridge.publish(connected=False, leds=[])
        return
    try:
        colors = keyboard.shown_colors()
    except DeviceError as exc:
        link.drop(now, exc)
        return
    bridge.publish(connected=True, leds=screen_colors(colors))


def run() -> None:
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    _trim_log()
    log("starting")
    serve(stop=stop)


def _trim_log(limit: int = 10 * 1024 * 1024) -> None:
    try:
        if LOG_PATH.stat().st_size > limit:
            LOG_PATH.write_text("", encoding="utf-8")
    except OSError:
        pass


assert FRAME_BYTES == 3 * LED_COUNT
