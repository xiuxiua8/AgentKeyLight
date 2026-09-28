import unittest

from agent_keylight.protocol import Light, lighting_report, matching_interfaces


class ProtocolTests(unittest.TestCase):
    def test_idle_packet_matches_air60_he_nuphyio_capture(self):
        report = lighting_report(Light("piano", "#ffbf00", 100, 3))
        self.assertEqual(len(report), 64)
        self.assertEqual(report[:17].hex(), "550600c809880000116403010000ffbf00")
        self.assertEqual(report[17:], bytes(47))

    def test_static_green_packet_has_checksum_and_only_main_light_bytes(self):
        report = lighting_report(Light("static", "#00d15a", 75, 3))
        self.assertEqual(report[8], 3)
        self.assertEqual(report[14:17], bytes.fromhex("00d15a"))
        self.assertEqual(report[3], sum(report[4:]) & 0xFF)
        self.assertEqual(report[17:], bytes(47))

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
            dict(base, product_id=0x3246),
        ]
        self.assertEqual(matching_interfaces(candidates), [base])

    def test_color_must_be_six_hex_digits(self):
        with self.assertRaises(ValueError):
            Light("static", "#ff ff ", 75, 3).validate()


if __name__ == "__main__":
    unittest.main()
