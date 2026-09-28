"""User settings: how each agent state looks and how long results stay visible."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .effects import EFFECTS, Look

APP_DIR = Path.home() / "Library" / "Application Support" / "AgentKeyLight"
CONFIG_PATH = APP_DIR / "config.toml"
STATES = ("working", "waiting", "completed", "error")
STATE_LABELS = {
    "working": "运行中",
    "waiting": "等待你操作",
    "completed": "完成",
    "error": "出错或中断",
}
SOURCES = ("claude", "codex")
SOURCE_LABELS = {"claude": "Claude Code", "codex": "Codex"}
SOURCE_STYLES = {"solid": "静态", "breathe": "呼吸", "chase": "滚动"}
SOURCE_AREAS = {"ring": "外圈", "bowl": "碗状", "bottom": "底行"}


class ConfigError(ValueError):
    """The settings file or a submitted setting is invalid."""


@dataclass(frozen=True)
class Config:
    looks: dict[str, Look] = field(
        default_factory=lambda: {
            "working": Look("comet", "#2878ff", "#0f2350", 1.0),
            "waiting": Look("flash", "#ffad00", "#332000", 1.0),
            "completed": Look("bloom", "#00d15a", "#00301a", 1.0),
            "error": Look("alarm", "#ff3048", "#3a0a0e", 1.0),
        }
    )
    completed_seconds: float = 12.0
    error_seconds: float = 20.0
    brightness: float = 1.0
    # Colors are chosen as they look on screen; LEDs shine linearly, so convert.
    color_correction: bool = True
    fps: int = 30
    palette_port: int = 47631
    source_enabled: bool = True
    source_area: str = "bowl"
    source_style: str = "breathe"
    source_speed: float = 1.0
    source_colors: dict[str, str] = field(
        default_factory=lambda: {"claude": "#ff7a1a", "codex": "#ffffff"}
    )

    def validate(self) -> None:
        if set(self.looks) != set(STATES):
            raise ConfigError("需要为四种状态各设置一种灯效")
        for state, look in self.looks.items():
            try:
                look.validate()
            except ValueError as exc:
                raise ConfigError(f"{STATE_LABELS[state]}：{_explain(exc)}") from exc
        if not 1 <= self.completed_seconds <= 600 or not 1 <= self.error_seconds <= 600:
            raise ConfigError("保持时间需要在 1 到 600 秒之间")
        if not 0.1 <= self.brightness <= 1:
            raise ConfigError("整体亮度需要在 0.1 到 1 之间")
        if not 10 <= self.fps <= 60:
            raise ConfigError("帧率需要在 10 到 60 之间")
        if not 1024 <= self.palette_port <= 65535:
            raise ConfigError("调色板端口需要在 1024 到 65535 之间")
        if self.source_area not in SOURCE_AREAS:
            raise ConfigError("来源色位置只能是 ring（外圈）、bowl（碗状）或 bottom（底行）")
        if self.source_style not in SOURCE_STYLES:
            raise ConfigError("来源色样式只能是 solid（静态）、breathe（呼吸）或 chase（滚动）")
        if not 0.2 <= self.source_speed <= 4:
            raise ConfigError("来源色速度需要在 0.2 到 4 之间")
        if set(self.source_colors) != set(SOURCES):
            raise ConfigError("需要为 Claude Code 和 Codex 各设置一种来源色")
        for name, value in self.source_colors.items():
            if re.fullmatch(r"#[0-9a-f]{6}", value) is None:
                raise ConfigError(f"{SOURCE_LABELS[name]} 的来源色需要写成 #RRGGBB")

    def to_dict(self) -> dict:
        return {
            "general": {
                "completed_seconds": self.completed_seconds,
                "error_seconds": self.error_seconds,
                "brightness": self.brightness,
                "color_correction": self.color_correction,
                "fps": self.fps,
                "palette_port": self.palette_port,
            },
            "source": {
                "enabled": self.source_enabled,
                "area": self.source_area,
                "style": self.source_style,
                "speed": self.source_speed,
                **self.source_colors,
            },
            **{
                state: {
                    "effect": look.effect,
                    "color": look.color,
                    "background": look.background,
                    "speed": look.speed,
                }
                for state, look in self.looks.items()
            },
        }

    @classmethod
    def from_dict(cls, value: dict) -> Config:
        """Build settings from parsed TOML or JSON; missing entries keep their defaults."""
        if not isinstance(value, dict):
            raise ConfigError("设置必须是一个对象")
        default = cls()
        try:
            general = value.get("general", {})
            source = value.get("source", {})
            looks = {}
            for state in STATES:
                raw = value.get(state, {})
                base = default.looks[state]
                looks[state] = Look(
                    effect=str(raw.get("effect", base.effect)),
                    color=str(raw.get("color", base.color)).lower(),
                    background=str(raw.get("background", base.background)).lower(),
                    speed=float(raw.get("speed", base.speed)),
                )
            config = replace(
                default,
                looks=looks,
                completed_seconds=float(
                    general.get("completed_seconds", default.completed_seconds)
                ),
                error_seconds=float(general.get("error_seconds", default.error_seconds)),
                brightness=float(general.get("brightness", default.brightness)),
                color_correction=_boolean(
                    general.get("color_correction", default.color_correction), "color_correction"
                ),
                fps=int(general.get("fps", default.fps)),
                palette_port=int(general.get("palette_port", default.palette_port)),
                source_enabled=_boolean(source.get("enabled", default.source_enabled), "enabled"),
                source_area=str(source.get("area", default.source_area)),
                source_style=str(source.get("style", default.source_style)),
                source_speed=float(source.get("speed", default.source_speed)),
                source_colors={
                    name: str(source.get(name, default.source_colors[name])).lower()
                    for name in SOURCES
                },
            )
        except (AttributeError, TypeError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"设置格式不正确：{exc}") from exc
        config.validate()
        return config


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} 只能是 true 或 false")
    return value


def _explain(exc: ValueError) -> str:
    text = str(exc)
    if text.startswith("unknown effect"):
        return "不认识的动画 " + text.split(": ", 1)[1]
    if text.startswith("invalid color"):
        return "颜色需要写成 #RRGGBB，而不是 " + text.split(": ", 1)[1]
    if text.startswith("speed"):
        return "速度需要在 0.2 到 4 之间"
    return text


def load(path: Path = CONFIG_PATH) -> Config:
    """Read the settings file, creating it with defaults on first use."""
    if not path.exists():
        save(Config(), path)
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} 不是有效的 TOML：{exc}") from exc
    return Config.from_dict(value)


def _boolean_toml(value: bool) -> str:
    return "true" if value else "false"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(round(float(value), 3))


def render_toml(config: Config) -> str:
    names = "、".join(f"{key} {effect.label}" for key, effect in EFFECTS.items())
    lines = [
        "# AgentKeyLight 灯效设置",
        "#",
        "# 推荐用网页调色板修改：在终端运行 agent-keylight palette",
        "# 也可以直接编辑本文件。保存后后台服务会自动载入；",
        "# 如果内容有误，会继续使用上一次的设置，并在日志里说明原因。",
        "#",
        '# 颜色写成 "#RRGGBB"。speed 是速度倍率，0.2 到 4，1 为默认。',
        f"# effect 可选：{names}",
        "",
        "[general]",
        f"completed_seconds = {_number(config.completed_seconds)}  # 完成灯效保持的秒数",
        f"error_seconds = {_number(config.error_seconds)}  # 出错或中断灯效保持的秒数",
        f"brightness = {_number(config.brightness)}  # 整体亮度 0.1 到 1，还会乘以键盘自身的背光亮度",
        f"color_correction = {_boolean_toml(config.color_correction)}  # 颜色校正：接近屏幕上的颜色",
        f"fps = {config.fps}  # 动画帧率 10 到 60",
        f"palette_port = {config.palette_port}  # 网页调色板端口，只在本机开放",
        "",
        "[source]",
        "# 显示灯效时，用一组按键的颜色表示发起任务的工具",
        f"enabled = {_boolean_toml(config.source_enabled)}",
        f'area = "{config.source_area}"  # 位置：ring 外圈、bowl 碗状（外圈去掉 1 到 =）、bottom 底行',
        f'style = "{config.source_style}"  # 样式：solid 静态、breathe 呼吸、chase 滚动',
        f"speed = {_number(config.source_speed)}  # 呼吸或滚动的速度倍率，0.2 到 4",
    ]
    for name in SOURCES:
        lines.append(f'{name} = "{config.source_colors[name]}"  # {SOURCE_LABELS[name]}')
    for state in STATES:
        look = config.looks[state]
        lines += [
            "",
            f"[{state}]  # {STATE_LABELS[state]}",
            f'effect = "{look.effect}"  # {EFFECTS[look.effect].label}',
            f'color = "{look.color}"  # 主色',
            f'background = "{look.background}"  # 背景色，即动画的暗部',
            f"speed = {_number(look.speed)}",
        ]
    return "\n".join(lines) + "\n"


def save(config: Config, path: Path = CONFIG_PATH) -> None:
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(render_toml(config), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
