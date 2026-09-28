import tempfile
import tomllib
import unittest
from pathlib import Path

from agent_keylight.config import Config, ConfigError, load, render_toml, save
from agent_keylight.effects import Look


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid_and_survive_a_round_trip(self):
        config = Config()
        config.validate()
        self.assertEqual(Config.from_dict(tomllib.loads(render_toml(config))), config)
        self.assertEqual(Config.from_dict(config.to_dict()), config)

    def test_first_load_writes_a_commented_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "config.toml"
            self.assertEqual(load(path), Config())
            text = path.read_text(encoding="utf-8")
            self.assertIn("# AgentKeyLight 灯效设置", text)
            self.assertIn('effect = "comet"  # 流光', text)
            self.assertIn("color_correction = true", text)

    def test_missing_entries_keep_their_defaults(self):
        config = Config.from_dict({"waiting": {"color": "#AA00FF"}, "general": {"fps": 24}})
        self.assertEqual(config.looks["waiting"], Look("flash", "#aa00ff", "#332000", 1.0))
        self.assertEqual(config.looks["working"], Config().looks["working"])
        self.assertEqual(config.fps, 24)

    def test_invalid_values_are_explained_in_chinese(self):
        cases = {
            "等待你操作：不认识的动画 sparkles": {"waiting": {"effect": "sparkles"}},
            "完成：颜色需要写成 #RRGGBB，而不是 green": {"completed": {"color": "green"}},
            "运行中：速度需要在 0.2 到 4 之间": {"working": {"speed": 9}},
            "整体亮度需要在 0.1 到 1 之间": {"general": {"brightness": 0}},
            "enabled 只能是 true 或 false": {"source": {"enabled": "yes"}},
            "color_correction 只能是 true 或 false": {"general": {"color_correction": 1}},
            "Codex 的来源色需要写成 #RRGGBB": {"source": {"codex": "white"}},
        }
        for message, value in cases.items():
            with self.subTest(message), self.assertRaises(ConfigError) as caught:
                Config.from_dict(value)
            self.assertEqual(str(caught.exception), message)

    def test_wrong_types_raise_config_error(self):
        for value in ([], {"general": "fast"}, {"working": {"speed": "quick"}}):
            with self.subTest(value), self.assertRaises(ConfigError):
                Config.from_dict(value)

    def test_save_replaces_the_file_and_validates_first(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            save(Config(completed_seconds=30), path)
            self.assertEqual(load(path).completed_seconds, 30)
            with self.assertRaises(ConfigError):
                save(Config(fps=5), path)
            self.assertEqual(load(path).completed_seconds, 30)
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_broken_toml_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text("[working\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)


if __name__ == "__main__":
    unittest.main()
