import tempfile
import threading
import unittest
from pathlib import Path

from agent_keylight import config as settings
from agent_keylight.config import Config
from agent_keylight.daemon import (
    CROSSFADE_SECONDS,
    FADE_OUT_SECONDS,
    KEEPALIVE_SECONDS,
    PREVIEW_SECONDS,
    RECONNECT_SECONDS,
    Animator,
    Bridge,
    KeyboardLink,
    SettingsWatcher,
    screen_colors,
    serve,
    to_bytes,
)
from agent_keylight.device import FRAME_BYTES, DeviceError, MainLight
from agent_keylight.effects import Look, parse_color, render
from agent_keylight.layout import BOTTOM_ROW, SOURCE_LEDS, SPACE
from agent_keylight.state import IDLE, Display, Update, apply

WORKING = Display("working", "claude", 1)
SOLID = Look("solid", "#ff0000", "#000000")
# Uncorrected values keep the blending arithmetic easy to check.
RAW = Config(color_correction=False)


def led(frame: bytes, index: int) -> bytes:
    return frame[3 * index : 3 * index + 3]


def near(actual: bytes, expected: list[int]) -> bool:
    return all(abs(a - e) <= 1 for a, e in zip(actual, expected, strict=True))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += max(seconds, 0.001)


class FakeKeyboard:
    def __init__(self, clock, failures=0):
        self.clock = clock
        self.frames: list[tuple[float, bytes]] = []
        self.failures = failures
        self.closed = False

    def show_frame(self, frame: bytes) -> None:
        if self.failures:
            self.failures -= 1
            raise DeviceError("unplugged")
        assert len(frame) == FRAME_BYTES
        self.frames.append((self.clock(), frame))

    def close(self) -> None:
        self.closed = True

    def current_mode(self) -> int:
        return 2

    def main_light(self, mode: int) -> MainLight:
        return MainLight(17, 0, 3, "#ffbf00")

    def shown_colors(self) -> bytes:
        return bytes([9]) * 216


class AnimatorTests(unittest.TestCase):
    def test_idle_sends_nothing(self):
        self.assertIsNone(Animator().frame(IDLE, None, Config(), 0.0))

    def test_bottom_row_shows_the_source_color(self):
        config = Config()
        frame = Animator().frame(Display("working", "codex", 1), SOLID, config, 0.0)
        for index in SOURCE_LEDS:
            self.assertEqual(led(frame, index), b"\xff\xff\xff", index)
        # The row above, and every other key, keeps the state's animation.
        self.assertEqual(led(frame, 52), b"\xff\x00\x00")
        self.assertEqual(led(frame, 0), b"\xff\x00\x00")
        plain = Animator().frame(WORKING, SOLID, Config(source_enabled=False), 0.0)
        self.assertTrue(all(led(plain, index) == b"\xff\x00\x00" for index in SOURCE_LEDS))

    def test_screen_colors_are_converted_to_led_light(self):
        grey = [(0.5, 0.5, 0.5)]
        self.assertEqual(to_bytes(grey, Config()), bytes([55, 55, 55]))
        self.assertEqual(to_bytes(grey, RAW), bytes([128, 128, 128]))
        amber = to_bytes([parse_color("#ffad00")], Config())
        self.assertEqual(amber, bytes([255, 107, 0]))
        self.assertEqual(screen_colors(amber), ["#ffad00"])

    def test_brightness_scales_every_led(self):
        frame = Animator().frame(WORKING, SOLID, Config(brightness=0.5), 0.0)
        self.assertEqual(led(frame, 0), bytes([128, 0, 0]))

    def test_state_changes_crossfade(self):
        animator = Animator()
        waiting = Display("waiting", "claude", 1)
        blue = Look("solid", "#0000ff", "#000000")
        animator.frame(WORKING, SOLID, RAW, 0.0)
        self.assertEqual(led(animator.frame(waiting, blue, RAW, 1.0), 0), b"\xff\x00\x00")
        middle = animator.frame(waiting, blue, RAW, 1.0 + CROSSFADE_SECONDS / 2)
        self.assertTrue(near(led(middle, 0), [128, 0, 128]), led(middle, 0))
        after = animator.frame(waiting, blue, RAW, 1.0 + CROSSFADE_SECONDS + 0.01)
        self.assertEqual(led(after, 0), b"\x00\x00\xff")

    def test_going_idle_fades_out_then_stops(self):
        animator = Animator()
        animator.frame(WORKING, SOLID, RAW, 0.0)
        self.assertEqual(led(animator.frame(IDLE, None, RAW, 1.0), 0), b"\xff\x00\x00")
        half = animator.frame(IDLE, None, RAW, 1.0 + FADE_OUT_SECONDS / 2)
        self.assertTrue(near(led(half, 0), [128, 0, 0]), led(half, 0))
        self.assertIsNone(animator.frame(IDLE, None, Config(), 1.0 + FADE_OUT_SECONDS + 0.01))
        self.assertIsNone(animator.frame(IDLE, None, Config(), 3.0))

    def test_a_second_task_joins_without_restarting_the_motion(self):
        animator = Animator()
        comet = Look("comet", "#ff0000", "#000000")
        two = Display("working", "claude", 2)
        animator.frame(WORKING, comet, Config(), 0.0)
        animator.frame(WORKING, comet, Config(), 0.5)
        animator.frame(two, comet, Config(), 0.5)
        later = 0.5 + CROSSFADE_SECONDS + 0.01
        frame = animator.frame(two, comet, Config(), later)
        expected = to_bytes(render(comet, later, 2), Config())
        self.assertEqual(frame[: 3 * BOTTOM_ROW[0]], expected[: 3 * BOTTOM_ROW[0]])

    def test_new_colors_keep_the_animation_running(self):
        animator = Animator()
        animator.frame(WORKING, Look("flash", "#ff0000", "#000000"), Config(), 0.0)
        recolored = Look("flash", "#00ff00", "#000000")
        animator.frame(WORKING, recolored, Config(), 0.6)
        # 0.91 s into the flash cycle is a pause; a restarted cycle would be flashing.
        frame = animator.frame(WORKING, recolored, Config(), 0.6 + CROSSFADE_SECONDS + 0.01)
        self.assertEqual(led(frame, 0), b"\x00\x00\x00")


class KeyboardLinkTests(unittest.TestCase):
    def test_unchanged_frames_are_only_resent_to_keep_them_alive(self):
        clock = FakeClock()
        keyboard = FakeKeyboard(clock)
        link = KeyboardLink(lambda: keyboard)
        frame = bytes(FRAME_BYTES)
        for t in (0.0, 0.1, 0.2, KEEPALIVE_SECONDS + 0.01):
            clock.now = t
            link.show(frame, t)
        self.assertEqual([t for t, _ in keyboard.frames], [0.0, KEEPALIVE_SECONDS + 0.01])

    def test_reconnects_after_the_keyboard_goes_away(self):
        clock = FakeClock()
        keyboard = FakeKeyboard(clock, failures=1)
        opened = []

        def open_keyboard():
            opened.append(clock.now)
            return keyboard

        link = KeyboardLink(open_keyboard)
        for t in (0.0, 1.0, RECONNECT_SECONDS + 0.1):
            clock.now = t
            link.show(bytes(FRAME_BYTES), t)
            if t == 0.0:
                self.assertTrue(keyboard.closed)
        self.assertEqual(opened, [0.0, RECONNECT_SECONDS + 0.1])
        self.assertEqual(len(keyboard.frames), 1)

    def test_missing_keyboard_is_retried_later(self):
        attempts = []

        def open_keyboard():
            attempts.append(1)
            raise DeviceError("not plugged in")

        link = KeyboardLink(open_keyboard)
        for t in (0.0, 0.5, 1.0, RECONNECT_SECONDS + 0.1):
            link.show(bytes(FRAME_BYTES), t)
        self.assertEqual(len(attempts), 2)


class SettingsWatcherTests(unittest.TestCase):
    def test_keeps_the_last_good_settings_when_the_file_breaks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            watcher = SettingsWatcher(path)
            self.assertEqual(watcher.refresh(0.0), Config())
            settings.save(Config(completed_seconds=30), path)
            self.assertEqual(watcher.refresh(1.0).completed_seconds, 30)
            path.write_text('[working]\neffect = "nope"\n', encoding="utf-8")
            self.assertEqual(watcher.refresh(2.0).completed_seconds, 30)


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config_path = root / "config.toml"
        self.sessions = root / "sessions"
        self.clock = FakeClock()
        self.keyboard = FakeKeyboard(self.clock)
        self.bridge = Bridge()
        self.stop = threading.Event()
        self.events: dict[float, callable] = {}

    def tearDown(self):
        self.temp.cleanup()

    def wall(self) -> float:
        return 1000.0 + self.clock.now

    def wait(self, seconds: float) -> None:
        self.clock.advance(seconds)
        for at in sorted(self.events):
            if self.clock.now >= at:
                self.events.pop(at)()

    def run_until(self, end: float) -> list[float]:
        self.events[end] = self.stop.set
        serve(
            self.config_path,
            self.sessions,
            open_keyboard=lambda: self.keyboard,
            bridge=self.bridge,
            stop=self.stop,
            clock=self.clock,
            wall=self.wall,
            wait=self.wait,
            sleep=self.clock.advance,
            palette=False,
        )
        return [t for t, _ in self.keyboard.frames]

    def record(self, status: str) -> None:
        apply("codex", "c1", Update(status), directory=self.sessions, now=self.wall())

    def test_streams_while_working_then_shows_the_result_and_lets_go(self):
        self.record("working")
        self.events[2.0] = lambda: self.record("completed")
        times = self.run_until(20.0)
        working = [t for t in times if t < 2.0]
        self.assertGreater(len(working), 50, "about 30 frames a second")
        # Completed shows for 12 s after it was recorded, then fades out and stops.
        last = times[-1]
        self.assertGreater(last, 2.0 + 12.0)
        self.assertLess(last, 2.0 + 12.0 + 0.3 + FADE_OUT_SECONDS + 0.1)

    def test_preview_shows_over_idle_and_ends_by_itself(self):
        waiting = Look("flash", "#ffad00", "#1c1000")
        draft = Config(looks={**Config().looks, "waiting": waiting})
        self.events[1.0] = lambda: self.bridge.start_preview(
            "waiting", draft, "codex", self.clock.now
        )
        times = self.run_until(15.0)
        self.assertTrue(all(t >= 1.0 for t in times))
        self.assertLess(times[-1], 1.0 + PREVIEW_SECONDS + FADE_OUT_SECONDS + 0.2)
        shown = [frame for t, frame in self.keyboard.frames if 1.5 < t < 1.0 + PREVIEW_SECONDS]
        amber = to_bytes([parse_color("#ffad00")], Config())
        self.assertTrue(any(led(frame, 0) == amber for frame in shown))
        self.assertTrue(
            all(led(frame, i) == b"\xff\xff\xff" for frame in shown for i in SOURCE_LEDS)
        )

    def test_preview_uses_unsaved_general_settings(self):
        draft = Config(brightness=0.5, source_colors={"claude": "#ff7a1a", "codex": "#00ff00"})
        self.events[1.0] = lambda: self.bridge.start_preview(
            "working", draft, "codex", self.clock.now
        )
        self.run_until(3.0)
        frame = next(frame for t, frame in self.keyboard.frames if t > 2.0)
        self.assertEqual(led(frame, SPACE), bytes([0, 128, 0]))

    def test_shutdown_fades_the_keyboard_out(self):
        self.record("working")
        times = self.run_until(1.0)
        final = self.keyboard.frames[-1][1]
        self.assertGreater(times[-1], 1.0)
        self.assertLess(max(final), 60)

    def test_palette_mirror_reports_the_keyboard(self):
        self.events[0.5] = lambda: self.bridge.want_mirror(self.clock.now)
        self.run_until(1.0)
        live = self.bridge.want_mirror(self.clock.now)
        self.assertTrue(live.connected)
        self.assertEqual((live.mode, live.brightness), ("Mac", 0))
        self.assertEqual(len(live.leds), 72)
        self.assertEqual(live.leds[0], screen_colors(bytes([9, 9, 9]))[0])
        self.assertEqual(live.leds[0], "#353535")


if __name__ == "__main__":
    unittest.main()
