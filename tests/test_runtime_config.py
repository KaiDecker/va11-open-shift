from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_shift.cli import main
from open_shift.byok import ThinkingMode
from open_shift.runtime_config import RuntimeConfigError, load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def write(self, root: Path, text: str) -> Path:
        path = root / "open-shift.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_valid_provider_config_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_runtime_config(
                self.write(
                    Path(temp_dir),
                    """
[provider]
base_url = "https://api.example.test"
model = "deepseek-v4-flash"
api_key_env = "OPEN_SHIFT_API_KEY"
timeout_seconds = 12
max_calls = 20
thinking = "disabled"

[world]
prefetch_days = 1
""",
                )
            )
            self.assertEqual(config.to_byok_config().model, "deepseek-v4-flash")
            self.assertIs(config.provider_thinking, ThinkingMode.DISABLED)
            rendered = config.redacted_dict()
            self.assertNotIn("do-not-store", str(rendered).lower())
            self.assertEqual(rendered["world"]["prefetch_days"], 1)

    def test_loads_gui_supported_thinking_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_runtime_config(
                self.write(
                    Path(temp_dir),
                    """
[provider]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
thinking = "enabled"

[world]
prefetch_days = 1
""",
                )
            )
            self.assertIs(config.provider_thinking, ThinkingMode.ENABLED)

    def test_rejects_secret_values_and_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeConfigError, "secret-bearing"):
                load_runtime_config(
                    self.write(
                        Path(temp_dir),
                        """
[provider]
base_url = "https://api.example.test"
model = "model"
api_key = "do-not-store"
""",
                    )
                )
            with self.assertRaisesRegex(RuntimeConfigError, "unknown fields"):
                load_runtime_config(
                    self.write(
                        Path(temp_dir),
                        """
[world]
prefetch_depth = 4
""",
                    )
                )

    def test_rejects_more_than_one_prefetched_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeConfigError, "prefetch_days"):
                load_runtime_config(
                    self.write(
                        Path(temp_dir),
                        """
[world]
prefetch_days = 2
""",
                    )
                )

    def test_launch_maps_validated_config_to_bridge_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write(
                root,
                """
[provider]
base_url = "https://api.example.test"
model = "model-1"
max_calls = 25

[world]
prefetch_days = 0
""",
            )
            with patch("open_shift.cli.RuntimeSession") as session:
                session.return_value.run.return_value = 0
                exit_code = main(
                    [
                        "launch",
                        "--config",
                        str(config_path),
                        "--db",
                        str(root / "world.sqlite3"),
                        "--runtime-file",
                        str(root / "open-shift-runtime.ini"),
                        "--game-cwd",
                        str(root),
                        "--game-command",
                        "game.exe",
                    ]
                )
            self.assertEqual(exit_code, 0)
            launch = session.call_args.args[0]
            arguments = list(launch.bridge_extra_args)
            self.assertEqual(arguments[arguments.index("--provider-model") + 1], "model-1")
            self.assertEqual(arguments[arguments.index("--provider-max-calls") + 1], "25")
            self.assertEqual(arguments[arguments.index("--prefetch-days") + 1], "0")


if __name__ == "__main__":
    unittest.main()
