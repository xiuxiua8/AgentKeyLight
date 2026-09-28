import math
import unittest

from agent_keylight.effects import (
    EFFECTS,
    Look,
    parse_color,
    render,
    source_levels,
    to_light,
    to_screen,
)
from agent_keylight.layout import (
    BOTTOM_ROW,
    BOWL,
    EXTRA_LED,
    KEYS,
    LED_COUNT,
    LED_POSITIONS,
    RING,
    SOURCE_AREAS,
    SOURCE_PATHS,
    SPACE,
    WIDTH,
)

RED = "#ff0000"
DARK = "#100000"


def level(look: Look, color) -> float:
    """How far a rendered color sits between the look's background (0) and color (1)."""
    low, high = parse_color(look.background), parse_color(look.color)
    return (color[0] - low[0]) / (high[0] - low[0])


class LayoutTests(unittest.TestCase):
    def test_led_order_matches_the_keyboard_matrix(self):
        self.assertEqual([key.index for key in KEYS], list(range(61)))
        self.assertEqual(LED_COUNT, 62)
        self.assertEqual(len(LED_POSITIONS), LED_COUNT)
        self.assertEqual(KEYS[0].label, "esc")
        self.assertEqual(KEYS[28].label, "caps")
        self.assertEqual(KEYS[SPACE].w, 6.5)
        self.assertEqual(LED_POSITIONS[EXTRA_LED], LED_POSITIONS[SPACE])

    def test_source_areas(self):
        self.assertEqual(BOTTOM_ROW, tuple(range(53, 61)))
        labels = [KEYS[i].label for i in BOTTOM_ROW]
        self.assertEqual(labels, ["ctrl", "opt", "cmd", "", "cmd", "opt", "ctrl", "fn"])
        # The ring: the whole top and bottom rows, and three keys on each side between them.
        sides = [KEYS[i].label for i in RING if 14 <= i < 53]
        self.assertEqual(sides, ["tab", "\\", "caps", "return", "shift", "shift"])
        self.assertEqual(set(RING), set(range(14)) | set(BOTTOM_ROW) | {14, 27, 28, 40, 41, 52})
        self.assertEqual(len(RING), 28)
        # The bowl: the ring without 1 to =, so esc and delete are its rims.
        self.assertEqual(set(BOWL), set(RING) - set(range(1, 13)))
        self.assertEqual([KEYS[i].label for i in BOWL[:2]], ["esc", "delete"])
        self.assertEqual(len(BOWL), 16)
        self.assertEqual(SOURCE_AREAS["ring"], RING + (EXTRA_LED,))
        self.assertEqual(SOURCE_AREAS["bowl"], BOWL + (EXTRA_LED,))
        self.assertEqual(SOURCE_AREAS["bottom"], BOTTOM_ROW + (EXTRA_LED,))

    def test_chase_paths_walk_each_area_key_by_key(self):
        ring, closed = SOURCE_PATHS["ring"]
        self.assertTrue(closed)
        labels = [KEYS[i].label for i in ring]
        self.assertEqual(
            labels[:2] + labels[13:17], ["esc", "1", "delete", "\\", "return", "shift"]
        )
        self.assertEqual(labels[17:25], ["fn", "ctrl", "opt", "cmd", "", "cmd", "opt", "ctrl"])
        self.assertEqual(labels[25:], ["shift", "caps", "tab"])
        bowl, closed = SOURCE_PATHS["bowl"]
        self.assertFalse(closed)
        self.assertEqual(
            [KEYS[i].label for i in (bowl[0], bowl[4], bowl[-5], bowl[-1])],
            ["esc", "ctrl", "fn", "delete"],
        )
        self.assertEqual(SOURCE_PATHS["bottom"], (BOTTOM_ROW, False))
        for name, (path, _closed) in SOURCE_PATHS.items():
            self.assertEqual(len(set(path)), len(path), name)
            self.assertEqual(SOURCE_AREAS[name], tuple(sorted(path)) + (EXTRA_LED,))

    def test_rows_span_the_keyboard_width(self):
        for row in sorted({key.y for key in KEYS}):
            keys = [key for key in KEYS if key.y == row]
            self.assertAlmostEqual(sum(key.w for key in keys), 15.1, delta=0.2)


class EffectTests(unittest.TestCase):
    def test_every_effect_returns_one_valid_color_per_led(self):
        for name in EFFECTS:
            look = Look(name, RED, DARK, 1.3)
            for elapsed in (0, 0.13, 0.9, 3.7, 61.2):
                frame = render(look, elapsed, count=2)
                self.assertEqual(len(frame), LED_COUNT, name)
                for color in frame:
                    self.assertTrue(all(0 <= channel <= 1 for channel in color), (name, color))

    def test_flash_knocks_twice_then_rests(self):
        look = Look("flash", RED, DARK)
        timeline = {t: level(look, render(look, t)[0]) for t in (0.05, 0.19, 0.32, 0.6, 1.0, 1.15)}
        self.assertEqual(timeline, {0.05: 1.0, 0.19: 0.0, 0.32: 1.0, 0.6: 0.0, 1.0: 0.0, 1.15: 1.0})

    def test_speed_scales_time(self):
        slow, fast = Look("flash", RED, DARK, 0.5), Look("flash", RED, DARK, 2.0)
        self.assertEqual(render(slow, 0.64), render(fast, 0.16))

    def test_alarm_strobes_three_times_then_swells(self):
        look = Look("alarm", RED, DARK)
        strobes = [level(look, render(look, t)[0]) for t in (0.04, 0.12, 0.21, 0.29, 0.38)]
        self.assertEqual(strobes, [1.0, 0.0, 1.0, 0.0, 1.0])
        swell = [level(look, render(look, t)[0]) for t in (0.6, 0.97, 1.4)]
        self.assertLess(swell[0], swell[1])
        self.assertGreater(swell[1], swell[2])

    def test_comet_moves_left_to_right(self):
        look = Look("comet", RED, DARK)

        def brightest(elapsed):
            frame = render(look, elapsed)
            return max(range(61), key=lambda i: (level(look, frame[i]), -KEYS[i].cx))

        positions = [KEYS[brightest(t)].cx for t in (0.3, 0.7, 1.1)]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(positions[-1] - positions[0], WIDTH / 3)

    def test_each_running_task_adds_a_comet_up_to_three(self):
        look = Look("comet", RED, DARK)

        def lit(count):
            # At 0.18 s every comet of a group of three is on the keyboard.
            return sum(level(look, color) > 0.5 for color in render(look, 0.18, count)[:61])

        self.assertLess(lit(1), lit(2))
        self.assertLess(lit(2), lit(3))
        self.assertEqual(lit(3), lit(9))

    def test_bloom_spreads_from_the_middle_then_glows(self):
        look = Look("bloom", RED, DARK)
        early = render(look, 0.05)
        middle = min(KEYS, key=lambda key: math.hypot(key.cx - 7.6, key.cy - 2.6))
        corner = KEYS[0]
        self.assertGreater(level(look, early[middle.index]), 0.5)
        self.assertLess(level(look, early[corner.index]), 0.1)
        settled = [level(look, color) for color in render(look, 5.0)]
        self.assertGreater(min(settled), 0.65)

    def test_rainbow_ignores_the_configured_colors(self):
        a = render(Look("rainbow", RED, DARK), 1.0)
        b = render(Look("rainbow", "#00ff00", "#000010"), 1.0)
        self.assertEqual(a, b)

    def test_source_styles(self):
        path, closed = SOURCE_PATHS["bowl"]
        self.assertEqual(source_levels("solid", path, closed, 1, 3.3), [1.0] * 16)
        breath = [source_levels("breathe", path, closed, 1, t)[5] for t in (0, 1.3, 2.6)]
        self.assertEqual([round(v, 6) for v in breath], [1.0, 0.0, 1.0])

    def test_chase_moves_a_highlight_with_a_trail(self):
        path, closed = SOURCE_PATHS["bowl"]
        # Only the first key is lit when the state begins: no trail from before.
        self.assertEqual(source_levels("chase", path, closed, 1, 0.0), [1.0] + [0.0] * 15)
        # A second later the highlight is 7 keys along, with a fading trail behind it.
        levels = source_levels("chase", path, closed, 1, 1.0)
        self.assertEqual(max(range(16), key=levels.__getitem__), 7)
        self.assertTrue(levels[7] > levels[6] > levels[5] > levels[4])
        self.assertEqual(levels[9:], [0.0] * 7)
        # An open path bounces: after reaching delete the highlight heads back.
        back = source_levels("chase", path, closed, 1, 20 / 7)
        self.assertEqual(max(range(16), key=back.__getitem__), 10)
        # A closed path wraps around; faster speed covers the path sooner.
        ring, closed = SOURCE_PATHS["ring"]
        around = source_levels("chase", ring, closed, 2, 30 / 14)
        self.assertEqual(max(range(28), key=around.__getitem__), 2)
        self.assertGreater(around[27], 0.1)

    def test_screen_and_light_conversions_invert_each_other(self):
        for value in (0.0, 0.02, 0.2, 0.5, 0.73, 1.0):
            self.assertAlmostEqual(to_screen(to_light(value)), value, places=9)
        self.assertAlmostEqual(to_light(0.5), 0.214, places=3)
        self.assertEqual((to_light(0.0), to_light(1.0)), (0.0, 1.0))

    def test_look_validation(self):
        for look in (
            Look("sparkles", RED, DARK),
            Look("flash", "red", DARK),
            Look("flash", RED, "#12345"),
            Look("flash", RED, DARK, 0.1),
            Look("flash", RED, DARK, 5),
        ):
            with self.assertRaises(ValueError, msg=look):
                look.validate()
        Look("flash", "#ABCDEF", DARK, 4).validate()


if __name__ == "__main__":
    unittest.main()
