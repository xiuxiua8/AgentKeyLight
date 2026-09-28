import http.client
import json
import tempfile
import unittest
from pathlib import Path

from agent_keylight.config import Config, load
from agent_keylight.daemon import Bridge, SettingsWatcher
from agent_keylight.palette import PaletteServer, start_server


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.toml"
        self.bridge = Bridge()
        self.watcher = SettingsWatcher(self.path)
        self.watcher.refresh(0.0)
        self.server: PaletteServer = start_server(self.bridge, self.watcher, 0, clock=lambda: 50.0)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        default = {"Host": host or f"127.0.0.1:{self.port}"}
        if body is not None:
            default |= {"Content-Type": "application/json", "X-AgentKeyLight": "1"}
        default |= headers or {}
        data = None if body is None else json.dumps(body)
        connection.request(method, path, body=data, headers=default)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def test_serves_the_page_and_the_editor_data(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("AgentKeyLight 调色板".encode(), page)
        status, body = self.request("GET", "/api/info")
        info = json.loads(body)
        self.assertEqual(info["config"], Config().to_dict())
        self.assertEqual(
            [s["key"] for s in info["states"]], ["working", "waiting", "completed", "error"]
        )
        self.assertEqual(len(info["layout"]["keys"]), 61)
        self.assertIn("comet", [e["key"] for e in info["effects"]])

    def test_live_view_asks_the_service_for_the_mirror(self):
        self.bridge.publish(connected=True, mode="Mac", brightness=100, leds=["#000000"] * 72)
        status, body = self.request("GET", "/api/live")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "Mac")
        self.assertTrue(self.bridge.mirror_wanted(51.0))

    def test_preview_reaches_the_service(self):
        draft = Config().to_dict()
        draft["waiting"] |= {"color": "#FFAD00", "speed": 1.5}
        draft["general"]["brightness"] = 0.4
        status, _ = self.request(
            "POST", "/api/preview", {"state": "waiting", "config": draft, "source": "codex"}
        )
        self.assertEqual(status, 200)
        display, shown = self.bridge.preview(51.0)
        self.assertEqual((display.status, display.source), ("waiting", "codex"))
        self.assertEqual((shown.looks["waiting"].color, shown.brightness), ("#ffad00", 0.4))
        self.request("POST", "/api/preview/stop", {})
        self.assertIsNone(self.bridge.preview(51.0))

    def test_invalid_input_is_explained(self):
        status, body = self.request(
            "POST", "/api/preview", {"state": "waiting", "config": {"waiting": {"effect": "x"}}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "等待你操作：不认识的动画 x")
        status, body = self.request("POST", "/api/config", {"general": {"fps": 500}})
        self.assertEqual(json.loads(body), {"error": "帧率需要在 10 到 60 之间"})

    def test_saving_writes_the_settings_file(self):
        config = Config().to_dict()
        config["waiting"]["color"] = "#aa00ff"
        status, body = self.request("POST", "/api/config", config)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["config"]["waiting"]["color"], "#aa00ff")
        self.assertEqual(load(self.path).looks["waiting"].color, "#aa00ff")

    def test_rejects_requests_from_other_sites(self):
        preview = {"state": "waiting", "config": Config().to_dict()}
        cases = [
            ("rebinding host", {"host": "evil.example:80"}),
            ("foreign origin", {"headers": {"Origin": "https://evil.example"}}),
            ("no custom header", {"headers": {"X-AgentKeyLight": "0"}}),
            ("form post", {"headers": {"Content-Type": "text/plain"}}),
        ]
        for name, options in cases:
            with self.subTest(name):
                status, _ = self.request("POST", "/api/preview", preview, **options)
                self.assertEqual(status, 403)
        self.assertIsNone(self.bridge.preview(51.0))
        status, _ = self.request("GET", "/api/info", host="evil.example")
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST", "/api/preview", preview, headers={"Origin": f"http://localhost:{self.port}"}
        )
        self.assertEqual(status, 200)

    def test_busy_port_does_not_stop_the_service(self):
        self.assertIsNone(start_server(self.bridge, self.watcher, self.port))


if __name__ == "__main__":
    unittest.main()
