"""NuPhy Air60 HE vendor HID protocol, as used by NuPhyIO (drive.nuphy.io).

Every request and reply is a 64 byte report on the usage page 1 / usage 0
interface:

    [0] 0x55 request, 0xAA reply   [4] payload length
    [1] command                    [5] address, low byte
    [2] XOR key, always 0 here     [6] address, high byte
    [3] sum of bytes 4..63         [7] reserved, 0
    [8..63] payload, at most 56 bytes

Only reads and LedSyncDownload are sent. LedSyncDownload shows one frame of
key colors from RAM: the firmware keeps it for about 1.6 s and then returns to
the lighting saved in the keyboard, so nothing stored is ever changed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Self

VID = 0x19F5
PID = 0xFEE0
PRODUCT = "NuPhy Air60 HE"
REPORT_SIZE = 64
MAX_PAYLOAD = 56
KEY_LEDS = 62
SIDE_LEDS = 10
FRAME_BYTES = 3 * KEY_LEDS
MODES = ("Gaming", "Windows", "Mac")
MODE_FUNC_STRIDE = 64
MAIN_LIGHT_OFFSET = 8
REPLY_TIMEOUT = 0.25


class Command(IntEnum):
    GET_INFO = 0x03
    GET_BASE = 0x04
    GET_FUNC = 0x05
    LED_SYNC_DOWNLOAD = 0xDD
    LED_SYNC_UPLOAD = 0xDE


class DeviceError(RuntimeError):
    """The exact keyboard or its control interface is unavailable."""


@dataclass(frozen=True)
class MainLight:
    """The backlight settings stored for one keyboard mode."""

    effect: int
    brightness: int
    speed: int
    color: str


def packet(
    command: Command, address: int = 0, data: bytes = b"", length: int | None = None
) -> bytes:
    if len(data) > MAX_PAYLOAD or not 0 <= address <= 0xFFFF:
        raise ValueError("payload or address out of range")
    report = bytearray(REPORT_SIZE)
    report[0] = 0x55
    report[1] = command
    report[4] = len(data) if length is None else length
    report[5] = address & 0xFF
    report[6] = address >> 8
    report[8 : 8 + len(data)] = data
    report[3] = sum(report[4:]) & 0xFF
    return bytes(report)


def matching_interfaces(devices: list[dict]) -> list[dict]:
    """Select only the vendor control interface NuPhyIO talks to."""
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
    import hid

    matches = matching_interfaces(hid.enumerate(VID, PID))
    if len(matches) != 1:
        raise DeviceError(f"expected one Air60 HE control interface, found {len(matches)}")
    return matches[0]["path"]


def _allow_shared_access() -> None:
    """Open devices without seizing them, so NuPhyIO and other tools keep working.

    hidapi seizes devices on macOS by default and resets that choice when it
    first initializes, which enumerating has already done by the time this runs.
    The Python binding does not wrap the switch, but its library exports it.
    """
    import ctypes

    import hid

    ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)


class Keyboard:
    """An open Air60 HE control interface."""

    def __init__(self, handle) -> None:
        self._handle = handle

    @classmethod
    def open(cls) -> Keyboard:
        import hid

        path = control_interface_path()
        _allow_shared_access()
        handle = hid.device()
        try:
            handle.open_path(path)
        except OSError as exc:
            raise DeviceError(f"cannot open the Air60 HE: {exc}") from exc
        return cls(handle)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def transact(
        self, command: Command, address: int = 0, data: bytes = b"", length: int | None = None
    ) -> bytes:
        """Send one request and return the reply that echoes its command and address."""
        request = packet(command, address, data, length)
        try:
            written = self._handle.write(b"\x00" + request)
            if written != REPORT_SIZE + 1:
                raise DeviceError(f"short HID write: {written} bytes")
            deadline = time.monotonic() + REPLY_TIMEOUT
            while (left := deadline - time.monotonic()) > 0:
                reply = bytes(self._handle.read(REPORT_SIZE, max(1, int(left * 1000))))
                # Unsolicited reports (mode or light changes) do not start with 0xAA,
                # and a late reply to an earlier request names another address.
                if (
                    len(reply) >= 8
                    and reply[0] == 0xAA
                    and reply[1] == command
                    and reply[5:7] == request[5:7]
                ):
                    return reply
        except (OSError, ValueError) as exc:
            raise DeviceError(f"Air60 HE HID transfer failed: {exc}") from exc
        raise DeviceError(f"no reply to command 0x{command:02x}")

    def read(self, command: Command, address: int, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            size = min(MAX_PAYLOAD, length - len(out))
            reply = self.transact(command, address + len(out), length=size)
            out += reply[8 : 8 + size]
        return bytes(out)

    def firmware_version(self) -> str:
        reply = self.transact(Command.GET_INFO)
        return format(reply[9] << 8 | reply[8], "x")

    def current_mode(self) -> int:
        """Index into MODES. Firmware 1.10 and later keep it at offset 0."""
        mode = self.read(Command.GET_BASE, 0, 16)[0]
        if mode >= len(MODES):
            raise DeviceError(f"unexpected keyboard mode {mode}")
        return mode

    def main_light(self, mode: int) -> MainLight:
        data = self.read(Command.GET_FUNC, mode * MODE_FUNC_STRIDE + MAIN_LIGHT_OFFSET, 9)
        return MainLight(
            effect=data[0], brightness=data[1], speed=data[2], color="#" + data[6:9].hex()
        )

    def show_frame(self, frame: bytes) -> None:
        """Show one frame of key colors, 3 bytes per LED, for about 1.6 seconds."""
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"a frame is {FRAME_BYTES} bytes")
        for offset in range(0, FRAME_BYTES, MAX_PAYLOAD):
            self.transact(Command.LED_SYNC_DOWNLOAD, offset, frame[offset : offset + MAX_PAYLOAD])

    def shown_colors(self) -> bytes:
        """What the LEDs show right now, already scaled by the keyboard's brightness."""
        return self.read(Command.LED_SYNC_UPLOAD, 0, 3 * (KEY_LEDS + SIDE_LEDS))
