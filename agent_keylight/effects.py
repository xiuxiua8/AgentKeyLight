"""Per-key animations for the Air60 HE.

An effect is a pure function of the time since its state began to show. It
returns one RGB color per LED as floats in 0..1, so every effect can be tested
and previewed without a keyboard.
"""

from __future__ import annotations

import colorsys
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from .layout import HEIGHT, LED_POSITIONS, WIDTH

Color = tuple[float, float, float]
Frame = list[Color]
CENTER = (WIDTH / 2, HEIGHT / 2)


@dataclass(frozen=True)
class Look:
    """How one agent state looks: an effect, its two colors, and a speed factor."""

    effect: str
    color: str
    background: str
    speed: float = 1.0

    def validate(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"unknown effect: {self.effect}")
        for value in (self.color, self.background):
            if re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
                raise ValueError(f"invalid color: {value}")
        if not 0.2 <= self.speed <= 4:
            raise ValueError("speed must be between 0.2 and 4")


def parse_color(value: str) -> Color:
    raw = bytes.fromhex(value[1:])
    return (raw[0] / 255, raw[1] / 255, raw[2] / 255)


def to_light(value: float) -> float:
    """A channel as seen on screen (sRGB) -> the linear light an LED must emit to match."""
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def to_screen(light: float) -> float:
    """Linear LED light -> the sRGB channel that looks the same on screen."""
    if light <= 0.0031308:
        return light * 12.92
    return 1.055 * light ** (1 / 2.4) - 0.055


def mix(low: Color, high: Color, amount: float) -> Color:
    t = min(1.0, max(0.0, amount))
    return (
        low[0] + (high[0] - low[0]) * t,
        low[1] + (high[1] - low[1]) * t,
        low[2] + (high[2] - low[2]) * t,
    )


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def _pulse(t: float, start: float, length: float, fade: float = 0.03) -> float:
    """1 inside [start, start + length) with short soft edges, else 0."""
    if t < start - fade or t > start + length + fade:
        return 0.0
    if t < start:
        return (t - start + fade) / fade
    if t > start + length:
        return (start + length + fade - t) / fade
    return 1.0


def _levels(levels: list[float], look: Look) -> Frame:
    low, high = parse_color(look.background), parse_color(look.color)
    return [mix(low, high, level) for level in levels]


def solid(look: Look, elapsed: float, count: int) -> Frame:
    return _levels([1.0] * len(LED_POSITIONS), look)


def breathe(look: Look, elapsed: float, count: int) -> Frame:
    level = 0.5 + 0.5 * math.cos(2 * math.pi * elapsed * look.speed / 2.6)
    return _levels([level] * len(LED_POSITIONS), look)


def flash(look: Look, elapsed: float, count: int) -> Frame:
    """Two quick flashes and a pause, like an urgent knock."""
    t = (elapsed * look.speed) % 1.1
    level = max(_pulse(t, 0.0, 0.12), _pulse(t, 0.26, 0.12))
    return _levels([level] * len(LED_POSITIONS), look)


def alarm(look: Look, elapsed: float, count: int) -> Frame:
    """Three strobes, then two slow swells, repeating."""
    t = (elapsed * look.speed) % 2.4
    if t < 0.5:
        level = max(_pulse(t, 0.0, 0.08), _pulse(t, 0.17, 0.08), _pulse(t, 0.34, 0.08))
    else:
        level = 0.25 + 0.4 * (0.5 - 0.5 * math.cos(2 * math.pi * (t - 0.5) / 0.95))
    return _levels([level] * len(LED_POSITIONS), look)


def _comet_body(behind: float) -> float:
    """A solid head about a key wide, a long soft tail behind it, and a crisp front."""
    if behind < 0:
        return math.exp(behind / 0.4)
    return 1.0 if behind <= 0.6 else math.exp(-(behind - 0.6) / 2.2)


def comet(look: Look, elapsed: float, count: int) -> Frame:
    """Bright bands sweep left to right with a fading tail, one per running task."""
    lead, span = 0.8, WIDTH + 4.0
    comets = max(1, min(3, count))
    phase = elapsed * look.speed / 1.6
    heads = [((phase + k / comets) % 1.0) * span - lead for k in range(comets)]
    levels = []
    for x, y in LED_POSITIONS:
        slanted = x + 0.35 * (y - HEIGHT / 2)
        levels.append(max(_comet_body(head - slanted) for head in heads))
    return _levels(levels, look)


def scanner(look: Look, elapsed: float, count: int) -> Frame:
    """One band that sweeps back and forth."""
    phase = (elapsed * look.speed / 2.4) % 1.0
    head = (0.5 - 0.5 * math.cos(2 * math.pi * phase)) * WIDTH
    return _levels([math.exp(-((x - head) ** 2) / 2.2) for x, _ in LED_POSITIONS], look)


def wave(look: Look, elapsed: float, count: int) -> Frame:
    phase = elapsed * look.speed / 2.0
    return _levels(
        [0.5 + 0.5 * math.sin(2 * math.pi * (x / WIDTH - phase)) for x, _ in LED_POSITIONS], look
    )


def _ring(distance: float, radius: float) -> float:
    if radius <= 0:
        return 0.0
    fade = max(0.0, 1 - radius / 13)
    return fade * math.exp(-((distance - radius) ** 2) / 1.3)


def ripple(look: Look, elapsed: float, count: int) -> Frame:
    """Rings keep spreading from the middle of the keyboard."""
    t = (elapsed * look.speed) % 1.6
    levels = []
    for x, y in LED_POSITIONS:
        d = math.hypot(x - CENTER[0], (y - CENTER[1]) * 1.3)
        levels.append(max(_ring(d, t * 9), _ring(d, (t + 0.8) * 9)))
    return _levels(levels, look)


def bloom(look: Look, elapsed: float, count: int) -> Frame:
    """Two rings spread from the middle, then the whole keyboard glows and softly breathes."""
    t = elapsed * look.speed
    glow = _smoothstep(0.35, 1.6, t) * (0.82 + 0.12 * math.cos(2 * math.pi * (t - 1.6) / 3.2))
    levels = []
    for x, y in LED_POSITIONS:
        d = math.hypot(x - CENTER[0], (y - CENTER[1]) * 1.3)
        levels.append(max(glow, _ring(d, t * 10), _ring(d, (t - 0.45) * 10)))
    return _levels(levels, look)


def _hash(index: int) -> float:
    return (math.sin(index * 12.9898 + 78.233) * 43758.5453) % 1.0


def sparkle(look: Look, elapsed: float, count: int) -> Frame:
    """Keys twinkle one by one over the background."""
    levels = []
    for index in range(len(LED_POSITIONS)):
        rate = 0.35 + 0.5 * _hash(index)
        wave_ = math.sin(2 * math.pi * (elapsed * look.speed * rate + _hash(index + 97)))
        levels.append(max(0.0, wave_) ** 6)
    return _levels(levels, look)


def rainbow(look: Look, elapsed: float, count: int) -> Frame:
    """Hues flow across the keyboard; the two colors are not used."""
    phase = elapsed * look.speed / 3.0
    return [
        colorsys.hsv_to_rgb((x / WIDTH * 0.8 - phase) % 1.0, 1.0, 1.0) for x, _ in LED_POSITIONS
    ]


@dataclass(frozen=True)
class Effect:
    label: str
    description: str
    render: Callable[[Look, float, int], Frame]


EFFECTS: dict[str, Effect] = {
    "comet": Effect("流光", "亮带从左到右扫过键盘，每个运行中的任务一道（最多 3 道）", comet),
    "flash": Effect("急促双闪", "两次快速闪烁后短暂停顿", flash),
    "bloom": Effect("涟漪后常亮", "两圈涟漪从中心扩散，随后柔和常亮", bloom),
    "alarm": Effect("警报", "三连爆闪后缓慢脉动，循环", alarm),
    "breathe": Effect("呼吸", "整体平滑地明暗变化", breathe),
    "wave": Effect("波浪", "明暗波纹从左向右流动", wave),
    "ripple": Effect("涟漪", "圆环不断从中心向外扩散", ripple),
    "scanner": Effect("扫描", "亮带左右来回扫动", scanner),
    "sparkle": Effect("星光", "按键此起彼伏地闪烁", sparkle),
    "rainbow": Effect("彩虹", "彩色从左向右流动（不使用上面两种颜色）", rainbow),
    "solid": Effect("常亮", "只显示主色", solid),
}


def render(look: Look, elapsed: float, count: int = 1) -> Frame:
    return EFFECTS[look.effect].render(look, max(0.0, elapsed), count)
