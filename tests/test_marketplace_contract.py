from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = REPO_ROOT / ".claude-plugin/marketplace.json"
PACKAGE_JSON = REPO_ROOT / "package.json"

# Claude Code marketplace manifest: closed field set, nothing else.
CLOSED_FIELDS = {"name", "version", "description", "path"}
# Honest-description gate: these words overclaim capabilities of the
# single-source skill package and must never appear in `description`.
FORBIDDEN_DESCRIPTION_WORDS = ("pip install", "publish", "live", "browser", "production")


class MarketplaceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    def load_manifest(self) -> dict:
        return json.loads(MARKETPLACE.read_text(encoding="utf-8"))

    def test_marketplace_manifest_file_exists(self) -> None:
        self.assertTrue(MARKETPLACE.is_file(), f"missing: {MARKETPLACE}")

    def test_marketplace_manifest_is_valid_json(self) -> None:
        data = self.load_manifest()
        self.assertIsInstance(data, dict)

    def test_marketplace_manifest_has_closed_field_set_with_nonempty_values(self) -> None:
        data = self.load_manifest()
        self.assertEqual(set(data.keys()), CLOSED_FIELDS)
        for field in sorted(CLOSED_FIELDS):
            self.assertIsInstance(
                data[field], str, f"marketplace field {field!r} must be a string"
            )
            self.assertTrue(
                data[field].strip(),
                f"marketplace field {field!r} must be non-empty",
            )

    def test_marketplace_version_matches_package_json(self) -> None:
        data = self.load_manifest()
        self.assertEqual(data["version"], self.package["version"])

    def test_marketplace_description_avoids_forbidden_overclaims(self) -> None:
        data = self.load_manifest()
        description = str(data["description"]).lower()
        for word in FORBIDDEN_DESCRIPTION_WORDS:
            self.assertNotIn(word, description, f"description overclaims: contains {word!r}")

    def test_marketplace_manifest_not_shipped_in_npm_package(self) -> None:
        """deepsec#10: .claude-plugin/ must not ride along in the npm tarball.

        package.json `files` whitelist omits .claude-plugin/; assert the
        packed file list never contains marketplace.json.
        """
        try:
            result = subprocess.run(
                ["npm", "pack", "--dry-run", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except FileNotFoundError:
            self.skipTest("npm not available on PATH")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        # npm >= 7 emits an array; npm 6 emits a single object. Normalize both.
        packages = payload if isinstance(payload, list) else [payload]
        packed_paths = [entry["path"] for pkg in packages for entry in pkg.get("files", [])]
        # npm packs path-prefixed entry names (".claude-plugin/marketplace.json",
        # never bare "marketplace.json"): exact membership would false-pass on the
        # real violation, so assert the suffix AND that the directory is absent.
        self.assertFalse(any(p.endswith("marketplace.json") for p in packed_paths))
        self.assertNotIn(".claude-plugin", packed_paths)


if __name__ == "__main__":
    unittest.main()
