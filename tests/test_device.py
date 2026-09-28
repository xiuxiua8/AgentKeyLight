import unittest

from agent_keylight.device import (
    FRAME_BYTES,
    Command,
    DeviceError,
    Keyboard,
    matching_interfaces,
    packet,
)


def reply(request: bytes, data: bytes = b"") -> list[int]:
    """What the keyboard answers: the request echoed with 0xAA and the data read."""
    out = bytearray(request)
    out[0] = 0xAA
    out[8 : 8 + len(data)] = data
    return list(out)


class FakeHandle:
    """A HID handle that answers like the Air60 HE, optionally with noise first."""

    def __init__(self, answer=None, noise=()):
        self.written: list[bytes] = []
        self.pending: list[list[int]] = []
        self.answer = answer or (lambda request: reply(request))
        self.noise = list(noise)

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        request = bytes(data[1:])
        self.pending += [list(n) for n in self.noise]
        result = self.answer(request)
        if result is not None:
            self.pending.append(result)
        return len(data)

    def read(self, size: int, timeout_ms: int) -> list[int]:
        return self.pending.pop(0) if self.pending else []

    def close(self) -> None:
        pass


class PacketTests(unittest.TestCase):
    def test_led_sync_packet_layout_and_checksum(self):
        report = packet(Command.LED_SYNC_DOWNLOAD, 168, bytes([1, 2, 3]))
        self.assertEqual(len(report), 64)
        self.assertEqual(report[:3], bytes([0x55, 0xDD, 0x00]))
        self.assertEqual(report[4:8], bytes([3, 168, 0, 0]))
        self.assertEqual(report[8:11], bytes([1, 2, 3]))
        self.assertEqual(report[3], sum(report[4:]) & 0xFF)

    def test_read_request_names_a_length_without_payload(self):
        report = packet(Command.GET_FUNC, 2 * 64 + 8, length=9)
        self.assertEqual(report[4:7], bytes([9, 0x88, 0]))
        self.assertEqual(report[8:], bytes(56))

    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            packet(Command.LED_SYNC_DOWNLOAD, 0, bytes(57))

    def test_requires_exact_model_and_control_interface(self):
        base = {
            "vendor_id": 0x19F5,
            "product_id": 0xFEE0,
            "product_string": "NuPhy Air60 HE",
            "usage_page": 1,
            "usage": 0,
        }
        candidates = [
            dict(base),
            dict(base, product_string="NuPhy Air60 V2"),
            dict(base, usage=6),
            dict(base, product_id=0x6120),
        ]
        self.assertEqual(matching_interfaces(candidates), [base])


class KeyboardTests(unittest.TestCase):
    def test_frame_is_sent_in_four_chunks_by_offset(self):
        handle = FakeHandle()
        Keyboard(handle).show_frame(bytes(range(FRAME_BYTES)))
        sent = [w[1:] for w in handle.written]
        self.assertEqual(
            [(s[1], s[4], s[5]) for s in sent],
            [(0xDD, 56, 0), (0xDD, 56, 56), (0xDD, 56, 112), (0xDD, 18, 168)],
        )
        self.assertEqual(b"".join(s[8 : 8 + s[4]] for s in sent), bytes(range(FRAME_BYTES)))
        self.assertTrue(all(w[0] == 0 for w in handle.written), "report id 0 precedes every report")

    def test_rejects_frames_of_the_wrong_size(self):
        with self.assertRaises(ValueError):
            Keyboard(FakeHandle()).show_frame(bytes(FRAME_BYTES - 3))

    def test_skips_unsolicited_reports_and_stale_addresses(self):
        light_report = [0x03] + [0] * 63
        handle = FakeHandle(noise=[light_report])
        stale = reply(packet(Command.GET_FUNC, 0, length=9))
        handle.pending.append(stale)

        def answer(request):
            return reply(request, bytes([0x11, 0x64, 3, 1, 0, 0, 0xFF, 0xBF, 0x00]))

        handle.answer = answer
        light = Keyboard(handle).main_light(2)
        self.assertEqual(
            (light.effect, light.brightness, light.speed, light.color), (17, 100, 3, "#ffbf00")
        )

    def test_missing_reply_raises_device_error(self):
        with self.assertRaises(DeviceError):
            Keyboard(FakeHandle(answer=lambda request: None)).transact(Command.GET_BASE, length=16)

    def test_reads_mode_and_firmware_from_captured_replies(self):
        # Replies captured from an Air60 HE on firmware 1.12 in Mac mode.
        info = bytes.fromhex("aa03009b160000001201") + b"Nov 12 2024"
        base = bytes.fromhex("aa04001310000000 0204aabb".replace(" ", ""))

        def answer(request):
            source = info if request[1] == Command.GET_INFO else base
            out = bytearray(64)
            out[: len(source)] = source
            out[5:7] = request[5:7]
            return list(out)

        keyboard = Keyboard(FakeHandle(answer=answer))
        self.assertEqual(keyboard.firmware_version(), "112")
        self.assertEqual(keyboard.current_mode(), 2)


if __name__ == "__main__":
    unittest.main()
