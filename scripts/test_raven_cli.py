"""Stdlib-only unit tests for raven_cli helpers.

Run: python3 -m unittest .agents/skills/raven/scripts/test_raven_cli.py

Covers config-file plumbing and auth-resolution precedence — the parts that
don't require a live network. The end-to-end `login` flow has API-side tests
in `api/tests/test_cli_auth_router.py` and a structural self-test via
`raven_cli.py self-test`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import raven_cli  # noqa: E402


class ConfigPathTests(unittest.TestCase):
    def test_config_path_honors_xdg(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(
                    raven_cli.config_path(), Path(tmp) / "ravn" / "config.json"
                )
            finally:
                del os.environ["XDG_CONFIG_HOME"]

    def test_config_path_default_is_home_dotconfig(self):
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.assertEqual(
            raven_cli.config_path(), Path.home() / ".config" / "ravn" / "config.json"
        )


class ConfigRoundtripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        os.environ["XDG_CONFIG_HOME"] = self._tmp

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_load_returns_empty_when_missing(self):
        self.assertEqual(raven_cli.load_config(), {})

    def test_save_then_load_round_trip(self):
        data = {"api_url": "https://r", "api_key": "rvn_abc", "user": {"id": 1}}
        path = raven_cli.save_config(data)
        self.assertTrue(path.is_file())
        # Mode 0600 (best-effort; some filesystems may not support)
        mode = path.stat().st_mode & 0o777
        if mode != 0:
            self.assertEqual(mode, 0o600)
        loaded = raven_cli.load_config()
        self.assertEqual(loaded, data)

    def test_load_returns_empty_on_corrupt_file(self):
        path = raven_cli.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(raven_cli.load_config(), {})


class AuthResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        os.environ["XDG_CONFIG_HOME"] = self._tmp
        os.environ.pop("RAVN_API_KEY", None)
        os.environ.pop("RAVN_API_URL", None)

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_cli_flag_wins_over_env_and_config(self):
        os.environ["RAVN_API_KEY"] = "rvn_env"
        raven_cli.save_config({"api_key": "rvn_config"})
        self.assertEqual(raven_cli.resolve_api_key("rvn_flag"), "rvn_flag")

    def test_env_var_wins_over_config(self):
        os.environ["RAVN_API_KEY"] = "rvn_env"
        raven_cli.save_config({"api_key": "rvn_config"})
        self.assertEqual(raven_cli.resolve_api_key(None), "rvn_env")

    def test_config_file_used_when_no_flag_or_env(self):
        raven_cli.save_config({"api_key": "rvn_from_file"})
        self.assertEqual(raven_cli.resolve_api_key(None), "rvn_from_file")

    def test_returns_none_when_nothing_configured(self):
        self.assertIsNone(raven_cli.resolve_api_key(None))

    def test_url_falls_through_to_config_when_no_flag(self):
        raven_cli.save_config({"api_url": "https://configured"})
        self.assertEqual(raven_cli.resolve_api_url(None), "https://configured")

    def test_url_keeps_user_override(self):
        raven_cli.save_config({"api_url": "https://configured"})
        self.assertEqual(
            raven_cli.resolve_api_url("http://user-supplied:9000"),
            "http://user-supplied:9000",
        )

    def test_url_env_var_beats_config(self):
        os.environ["RAVN_API_URL"] = "https://env-supplied"
        raven_cli.save_config({"api_url": "https://configured"})
        self.assertEqual(raven_cli.resolve_api_url(None), "https://env-supplied")

    def test_url_falls_back_to_default_when_nothing_configured(self):
        self.assertEqual(raven_cli.resolve_api_url(None), raven_cli.DEFAULT_API_URL)


class ParserSelfTest(unittest.TestCase):
    def test_self_test_exits_zero(self):
        rc = raven_cli.cmd_self_test(
            client=None,  # type: ignore[arg-type]
            args=type("args", (), {})(),
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
