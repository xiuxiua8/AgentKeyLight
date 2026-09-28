"""Physical layout of the Air60 HE key LEDs.

LED i lights the key at matrix index i. The order and geometry come from
NuPhyIO's Air60 HE layout and were checked against the default key matrix read
from the keyboard (GetDefaultKeyMatrix): rows from the top, left to right.
Coordinates are in key units. LED 61 has no key of its own; it mirrors the
space bar so that any light it gives matches.
"""

from __future__ import annotations

from dataclasses import dataclass

WIDTH = 15.25
HEIGHT = 5.16
ROW_Y = (0.0, 1.04, 2.08, 3.12, 4.16)


@dataclass(frozen=True)
class Key:
    index: int
    label: str
    x: float
    y: float
    w: float = 1.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + 0.5


def _row(row: int, start: int, keys: list[tuple[str, float]]) -> list[Key]:
    out, x = [], 0.0
    for offset, (label, width) in enumerate(keys):
        out.append(Key(start + offset, label, round(x, 2), ROW_Y[row], width))
        x += width
    return out


KEYS: tuple[Key, ...] = tuple(
    _row(0, 0, [("esc", 1)] + [(c, 1) for c in "1234567890-="] + [("delete", 2)])
    + _row(1, 14, [("tab", 1.5)] + [(c, 1) for c in "QWERTYUIOP[]"] + [("\\", 1.5)])
    + _row(2, 28, [("caps", 1.8)] + [(c, 1) for c in "ASDFGHJKL;'"] + [("return", 2.25)])
    + _row(3, 41, [("shift", 2.3)] + [(c, 1) for c in "ZXCVBNM,./"] + [("shift", 2.8)])
    + _row(
        4,
        53,
        [("ctrl", 1.25), ("opt", 1.25), ("cmd", 1.25), ("", 6.5)]
        + [("cmd", 1.25), ("opt", 1.25), ("ctrl", 1.25), ("fn", 1.25)],
    )
)
SPACE = 56
EXTRA_LED = 61
LED_COUNT = 62
LED_POSITIONS: tuple[tuple[float, float], ...] = tuple((key.cx, key.cy) for key in KEYS) + (
    (KEYS[SPACE].cx, KEYS[SPACE].cy),
)
BOTTOM_ROW: tuple[int, ...] = tuple(key.index for key in KEYS if key.y == ROW_Y[-1])
# The bottom row, plus the LED that mirrors the space bar, shows which agent a status is for.
SOURCE_LEDS: tuple[int, ...] = BOTTOM_ROW + (EXTRA_LED,)
