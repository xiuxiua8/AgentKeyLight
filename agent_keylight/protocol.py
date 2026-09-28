"""NuPhy Air60 HE lighting packets, verified against its NuPhyIO WebHID traffic."""

from __future__ import annotations

import re
from dataclasses import dataclass

VID = 0x19F5
PID = 0xFEE0
PRODUCT = "NuPhy Air60 HE"
REPORT_SIZE = 64
EFFECT_IDS = {"static": 3, "breath": 4, "piano": 17}


class DeviceError(RuntimeError):
    """The exact keyboard or its lighting interface is unavailable."""


@dataclass(frozen=True)
class Light:
    effect: str
    color: str
    brightness: int
    speed: int

    @classmethod
    def from_dict(cls, value: dict) -> Light:
        light = cls(
            effect=str(value["effect"]),
            color=str(value["color"]),
            brightness=int(value["brightness"]),
            speed=int(value["speed"]),
        )
        light.validate()
        return light

    def validate(self) -> None:
        if self.effect not in EFFECT_IDS:
            raise ValueError(f"unsupported light effect: {self.effect}")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", self.color) is None:
            raise ValueError(f"invalid color: {self.color}")
        if not 0 <= self.brightness <= 100:
            raise ValueError("brightness must be 0..100")
        if not 0 <= self.speed <= 5:
            raise ValueError("speed must be 0..5")


def lighting_report(light: Light) -> bytes:
    """Build the 64 byte report sent by NuPhyIO's Air60 HE lighting page.

    Byte 3 is the wrapping sum of bytes 4..63. Only the main backlight block
    is addressed; no key map, rapid-trigger, or side-light fields are written.
    """
    light.validate()
    packet = bytearray(REPORT_SIZE)
    packet[:8] = bytes((0x55, 0x06, 0x00, 0x00, 0x09, 0x88, 0x00, 0x00))
    packet[8] = EFFECT_IDS[light.effect]
    packet[9] = light.brightness
    packet[10] = light.speed
    packet[11] = 1  # Direction, as sent by NuPhyIO for this keyboard.
    packet[12] = 0  # Custom RGB color mode.
    packet[13] = 0  # Palette index is unused for custom RGB.
    packet[14:17] = bytes.fromhex(light.color[1:])
    packet[3] = sum(packet[4:]) & 0xFF
    return bytes(packet)


def matching_interfaces(devices: list[dict]) -> list[dict]:
    """Select only the vendor control interface observed in NuPhyIO."""
    return [
        item
        for item in devices
        if item.get("vendor_id") == VID
        and item.get("product_id") == PID
        and item.get("product_string") == PRODUCT
        and item.get("usage_page") == 1
        and item.get("usage") == 0
    ]


def control_interface_path() -> bytes:
    """Return the one observed lighting interface, or report its absence."""
    import hid

    matches = matching_interfaces(hid.enumerate(VID, PID))
    if len(matches) != 1:
        raise DeviceError(f"expected one Air60 HE control interface, found {len(matches)}")
    return matches[0]["path"]


def send_light(light: Light) -> int:
    """Send one lighting update. A zero report ID precedes the 64 byte report."""
    import hid

    path = control_interface_path()
    device = hid.device()
    try:
        device.open_path(path)
        written = device.write(b"\x00" + lighting_report(light))
        if written != REPORT_SIZE + 1:
            raise DeviceError(f"short HID write: {written} bytes")
        return written
    except OSError as exc:
        raise DeviceError(f"Air60 HE HID write failed: {exc}") from exc
    finally:
        device.close()
