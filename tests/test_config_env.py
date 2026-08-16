"""Contract tests for centralized environment resolution (config_env).

T3 A8: env resolution for the visual-review track is centralized in
`skill/scripts/readme_showcase/config_env.py`:

- process environment wins over the `.env` file;
- the `.env` file is only consulted for keys matching the configured prefix;
- the `required` contract raises ``E_CONFIG_MISSING_KEY`` (never echoing the
  key's value) and applies to the actually-resolved key name;
- every test isolates the process environment (mock.patch.dict auto-restores),
  clearing VISION_REVIEW_* keys plus HOME/CODEX_HOME.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keys the isolation context must clear from the process environment.
_CONFIG_PREFIX = "VISION_REVIEW_"
_ISOLATED_NAMES = ("HOME", "CODEX_HOME")


def _config_env() -> ModuleType:
    """Import config_env lazily so the grep-assert test still runs during the
    RED phase (module does not exist yet)."""
    import importlib

    return importlib.import_module("skill.scripts.readme_showcase.config_env")


class _EnvIsolation:
    """Clears VISION_REVIEW_* keys and HOME/CODEX_HOME from the process
    environment, restoring the original environment on exit. mock.patch.dict
    is the auto-restoring mechanism."""

    _patcher: mock._patch

    def __enter__(self) -> "_EnvIsolation":
        self._patcher = mock.patch.dict(os.environ)
        self._patcher.start()
        for key in list(os.environ):
            if key.startswith(_CONFIG_PREFIX) or key in _ISOLATED_NAMES:
                del os.environ[key]
        return self

    def __exit__(self, *_exc: object) -> None:
        self._patcher.stop()


class ConfigEnvTests(unittest.TestCase):
    def test_env_wins_over_dotenv(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_API_KEY=file-key\n", encoding="utf-8")
                os.environ["VISION_REVIEW_API_KEY"] = "process-key"
                resolved = _config_env().resolve_config(env_path=env_file)
                self.assertEqual(resolved["VISION_REVIEW_API_KEY"], "process-key")

    def test_dotenv_fallback_when_env_unset(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_API_BASE=https://example.test/v1\n",
                    encoding="utf-8")
                resolved = _config_env().resolve_config(env_path=env_file)
                self.assertEqual(
                    resolved.get("VISION_REVIEW_API_BASE"),
                    "https://example.test/v1")

    def test_codex_home_defaults_to_home_dot_codex(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as home:
                os.environ["HOME"] = home
                self.assertEqual(
                    str(_config_env().codex_home()), str(Path(home) / ".codex"))
                os.environ["CODEX_HOME"] = "/custom/codex-home"
                self.assertEqual(str(_config_env().codex_home()), "/custom/codex-home")

    def test_prefix_filter_limits_dotenv_reads(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_MODEL=gpt-4o\nUNRELATED_KEY=leak\n",
                    encoding="utf-8")
                os.environ["ALSO_UNPREFIXED"] = "x"
                resolved = _config_env().resolve_config(env_path=env_file)
                self.assertIn("VISION_REVIEW_MODEL", resolved)
                self.assertNotIn("UNRELATED_KEY", resolved)
                self.assertNotIn("ALSO_UNPREFIXED", resolved)

    def test_non_prefixed_keys_never_written_to_dotenv(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                _config_env().write_env_file(
                    {"VISION_REVIEW_MODEL": "gpt-4o", "LEAKY_SECRET": "hunter2"},
                    env_path=env_file)
                content = env_file.read_text(encoding="utf-8")
                self.assertIn("VISION_REVIEW_MODEL=gpt-4o", content)
                self.assertNotIn("LEAKY_SECRET", content)
                self.assertNotIn("hunter2", content)

    def test_empty_required_never_raises(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_MODEL=gpt-4o\n", encoding="utf-8")
                resolved = _config_env().resolve_config(
                    required=(), env_path=env_file)
                self.assertIn("VISION_REVIEW_MODEL", resolved)

    def test_required_missing_raises_config_missing_key_without_value(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_API_KEY=sk-REAL-SECRET\n"
                    "VISION_REVIEW_API_BASE=https://example.test/v1\n",
                    encoding="utf-8")
                cfg = _config_env()
                with self.assertRaises(ContractError) as ctx:
                    cfg.resolve_config(required=["VISION_REVIEW_MODEL"], env_path=env_file)
                self.assertEqual(ctx.exception.code, "E_CONFIG_MISSING_KEY")
                self.assertIn("VISION_REVIEW_MODEL", str(ctx.exception))
                self.assertNotIn("sk-REAL-SECRET", str(ctx.exception))
                self.assertNotIn("https://example.test/v1", str(ctx.exception))

    def test_required_applies_to_actually_resolved_dynamic_name(self) -> None:
        """deepsec#5: the required check follows the resolved key name.

        When the static name exists in .env but the dynamic name is missing,
        the check must not falsely pass and must not silently use the wrong
        key's value.
        """
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "VISION_REVIEW_API_KEY=sk-STATIC-IN-ENVFILE\n", encoding="utf-8")
                cfg = _config_env()
                # dynamic name set in the process environment → required passes.
                os.environ["CUSTOM_API_KEY_ENV"] = "sk-dynamic"
                cfg.resolve_config(required=["CUSTOM_API_KEY_ENV"], env_path=env_file)
                # dynamic name missing → required must raise (no false pass),
                # and the static .env value must never surface under the
                # dynamic name.
                os.environ.pop("CUSTOM_API_KEY_ENV", None)
                with self.assertRaises(ContractError) as ctx:
                    cfg.resolve_config(required=["CUSTOM_API_KEY_ENV"], env_path=env_file)
                self.assertEqual(ctx.exception.code, "E_CONFIG_MISSING_KEY")
                self.assertIn("CUSTOM_API_KEY_ENV", str(ctx.exception))
                self.assertNotIn("sk-STATIC-IN-ENVFILE", str(ctx.exception))
                self.assertIsNone(cfg.config_get("CUSTOM_API_KEY_ENV", env_path=env_file))

    def test_dotenv_comments_and_blank_lines_are_ignored(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                env_file.write_text(
                    "# leading comment\n"
                    "\n"
                    'VISION_REVIEW_MODEL="gpt-4o"\n'
                    "   \n"
                    "# another comment\n"
                    "VISION_REVIEW_API_BASE='https://example.test/v1'\n",
                    encoding="utf-8")
                resolved = _config_env().resolve_config(env_path=env_file)
                self.assertEqual(resolved["VISION_REVIEW_MODEL"], "gpt-4o")
                self.assertEqual(
                    resolved["VISION_REVIEW_API_BASE"], "https://example.test/v1")

    def test_env_file_created_with_mode_0600(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / "brand-new.env"
                _config_env().write_env_file(
                    {"VISION_REVIEW_MODEL": "gpt-4o"}, env_path=env_file)
                self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

    def test_secret_values_never_appear_in_outputs_or_exceptions(self) -> None:
        with _EnvIsolation():
            with tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / ".env"
                secret = "sk-ULTRA-SECRET-28347"
                env_file.write_text(
                    f"VISION_REVIEW_API_KEY={secret}\n", encoding="utf-8")
                cfg = _config_env()
                # The resolved value is the API contract; the secret must
                # resolve correctly ...
                resolved = cfg.resolve_config(env_path=env_file)
                self.assertEqual(resolved["VISION_REVIEW_API_KEY"], secret)
                self.assertEqual(
                    cfg.config_get("VISION_REVIEW_API_KEY", env_path=env_file), secret)
                # ... but never appear in a raised exception (str or repr).
                with self.assertRaises(ContractError) as ctx:
                    cfg.resolve_config(required=["VISION_REVIEW_MODEL"], env_path=env_file)
                self.assertNotIn(secret, str(ctx.exception))
                self.assertNotIn(secret, repr(ctx.exception))

    def test_env_isolation_clears_prefix_and_home_then_restores(self) -> None:
        os.environ["VISION_REVIEW_MODEL"] = "should-vanish"
        os.environ["HOME"] = "/tmp/whatever-home"
        os.environ["CODEX_HOME"] = "/tmp/whatever-codex"
        with _EnvIsolation():
            self.assertNotIn("VISION_REVIEW_MODEL", os.environ)
            self.assertNotIn("HOME", os.environ)
            self.assertNotIn("CODEX_HOME", os.environ)
        self.assertEqual(os.environ.get("VISION_REVIEW_MODEL"), "should-vanish")
        self.assertEqual(os.environ.get("HOME"), "/tmp/whatever-home")
        self.assertEqual(os.environ.get("CODEX_HOME"), "/tmp/whatever-codex")

    def test_no_direct_vision_env_reads_outside_config_env(self) -> None:
        """os.environ/os.getenv reads of VISION_* keys are centralized.

        The three direct read points (review.py: VISION_REVIEW_MODEL x2 and
        VISION_REVIEW_API_BASE) must flow through config_env. Session-model
        probes and infra env passthrough read non-VISION keys and are
        intentionally out of scope.
        """
        scripts = REPO_ROOT / "skill" / "scripts"
        hits = []
        for path in sorted(scripts.rglob("*.py")):
            if path.name == "config_env.py":
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if "VISION_REVIEW" in line and (
                        "os.environ" in line or "os.getenv" in line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        self.assertEqual(
            hits, [],
            f"direct VISION_* env reads must live in config_env.py: {hits}")


if __name__ == "__main__":
    unittest.main()
