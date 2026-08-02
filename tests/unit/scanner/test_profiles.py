from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.pipeline_core import scan_repository
from skill.scripts.readme_showcase.scanner import service
from skill.scripts.readme_showcase.scanner.policies import PROFILE_LIMITS, load_scanner_policy, posix_glob_matches


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "scanner"


class ScannerProfileTests(unittest.TestCase):
    def write_config(self, root: Path, payload: object) -> None:
        (root / ".readme-showcase.json").write_text(json.dumps(payload), encoding="utf-8")

    def make_repository(self, base: Path) -> Path:
        root = base / "target"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "scanner@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Scanner Test"], check=True)
        for path, text in (("src/main.py", "print('safe')\n"), ("docs/guide.md", "# Guide\n"), ("tests/test_main.py", "assert True\n")):
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        return root

    def test_profile_constants_match_documented_bounded_caps(self) -> None:
        self.assertEqual(
            {name: limits.as_dict() for name, limits in PROFILE_LIMITS.items()},
            {
                "fast": {"max_content_files": 50, "max_indexed_files": 5_000, "max_seconds": 5, "max_total_bytes": 2 * 1024 * 1024},
                "balanced": {"max_content_files": 250, "max_indexed_files": 20_000, "max_seconds": 20, "max_total_bytes": 16 * 1024 * 1024},
                "deep": {"max_content_files": 1_000, "max_indexed_files": 100_000, "max_seconds": 60, "max_total_bytes": 64 * 1024 * 1024},
            },
        )

    def test_valid_fixture_loads_to_the_canonical_balanced_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".readme-showcase.json").write_bytes((FIXTURES / "config-valid.json").read_bytes())

            policy = load_scanner_policy(root)

            self.assertIsNotNone(policy)
            self.assertEqual(policy.profile, "balanced")
            self.assertEqual(policy.include, ("src/**", "docs/**"))
            self.assertEqual(policy.exclude, ("docs/generated/**",))

    def test_posix_globs_are_anchored_and_cross_only_with_double_star(self) -> None:
        cases = (
            ("*.py", "main.py", True),
            ("*.py", "src/main.py", False),
            ("src/*.py", "src/main.py", True),
            ("src/*.py", "src/lib/main.py", False),
            ("src/**", "src/lib/main.py", True),
            ("**/*.py", "main.py", True),
            ("**/*.py", "src/main.py", True),
            ("docs/**", "src/docs/readme.md", False),
        )
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(posix_glob_matches(pattern, path), expected)

    def test_strict_config_rejects_unknown_duplicate_windows_and_hard_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".readme-showcase.json"
            config.write_bytes((FIXTURES / "config-invalid.json").read_bytes())
            with self.assertRaisesRegex(ContractError, "unknown") as error:
                load_scanner_policy(root)
            self.assertEqual(error.exception.code, "E_SCANNER_CONFIG")

            config.write_text('{"scanner":{"profile":"fast","profile":"deep"}}', encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "duplicate") as error:
                load_scanner_policy(root)
            self.assertEqual(error.exception.code, "E_SCANNER_CONFIG")

            self.write_config(root, {"scanner": {"include": ["src\\**"]}})
            with self.assertRaisesRegex(ContractError, "POSIX") as error:
                load_scanner_policy(root)
            self.assertEqual(error.exception.code, "E_SCANNER_CONFIG")

            self.write_config(root, {"scanner": {"limits": {"max_indexed_files": 100_001}}})
            with self.assertRaisesRegex(ContractError, "hard maximum") as error:
                load_scanner_policy(root)
            self.assertEqual(error.exception.code, "E_SCANNER_CONFIG")

    def test_invalid_config_fails_before_any_repository_body_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("TOKEN=body-sentinel", encoding="utf-8")
            self.write_config(root, {"scanner": {"include": [".git/**"]}})
            with mock.patch.object(service, "_read", side_effect=AssertionError("body read")) as read:
                with self.assertRaisesRegex(ContractError, "fixed safety") as error:
                    service.scan_repository_v1(root)
            self.assertEqual(error.exception.code, "E_SCANNER_CONFIG")
            read.assert_not_called()

    def test_config_filters_tracked_paths_and_never_reads_secret_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_repository(Path(temporary))
            (root / ".env").write_text("TOKEN=body-sentinel", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".env"], check=True)
            self.write_config(root, {"scanner": {"profile": "balanced", "include": ["src/**", ".env"], "secret_policy": "redact"}})

            with mock.patch.object(service, "_read", wraps=service._read) as read:
                result = service.scan_repository_v1(root)

            self.assertEqual([item["path"] for item in result["files"]], ["src/main.py"])
            self.assertIn({"code": "W_SCAN_SECRET", "path": ".env"}, result["warnings"])
            self.assertNotIn("body-sentinel", json.dumps(result))
            self.assertEqual([call.args[2] for call in read.call_args_list], ["src/main.py"])

    def test_explicit_untracked_policy_scans_untracked_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_repository(Path(temporary))
            (root / "notes.md").write_text("untracked\n", encoding="utf-8")
            self.write_config(root, {"scanner": {"tracked_only": False, "include": ["notes.md"]}})

            result = service.scan_repository_v1(root)

            self.assertEqual([item["path"] for item in result["files"]], ["notes.md"])

    def test_profile_replaces_the_v1_wrapper_limits_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            self.write_config(root, {"scanner": {"profile": "fast"}})

            result = scan_repository(root)

            self.assertEqual(result["scan_limits"]["max_files"], 5_000)
            self.assertEqual(result["scan_limits"]["max_total_bytes"], 2 * 1024 * 1024)
            self.assertEqual(result["scan_limits"]["max_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
