from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.pipeline_core import validate_generated_bundle
from tests import test_bundle_contracts


class VisualContractTests(unittest.TestCase):
    builder = test_bundle_contracts.BundleContractTests()

    def update_manifest(
        self,
        root: Path,
        bundle: dict[str, Any],
        update: Callable[[dict[str, Any]], object],
    ) -> None:
        artifacts = bundle["artifacts"]
        assert isinstance(artifacts, dict)
        reference = artifacts["asset_manifest"]
        assert isinstance(reference, dict)
        manifest_path = root / str(reference["path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        update(manifest["assets"][0])
        write_canonical_json_atomic(manifest_path, manifest)
        reference["sha256"] = canonical_sha256(manifest)

    def assert_code(self, root: Path, bundle: dict[str, Any], code: str) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_generated_bundle(bundle, root)
        self.assertEqual(raised.exception.code, code)

    def test_static_hybrid_and_explicit_motion_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for production_kind in ("static", "hybrid", "motion"):
                with self.subTest(production_kind=production_kind):
                    root = base / production_kind
                    root.mkdir()
                    bundle, _ = self.builder.make_bundle(
                        root,
                        "asset-only",
                        production_kind=production_kind,
                    )
                    self.assertEqual(
                        validate_generated_bundle(bundle, root)["status"],
                        "pass",
                    )

    def test_hybrid_requires_editable_sources_and_static_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, bundle_path = self.builder.make_bundle(
                root,
                "asset-only",
                production_kind="hybrid",
            )
            before = bundle_path.read_bytes()
            self.update_manifest(root, bundle, lambda asset: asset.pop("layout"))

            self.assert_code(root, bundle, "E_SCHEMA_MISSING_FIELD")
            self.assertEqual(bundle_path.read_bytes(), before)

    def test_motion_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.builder.make_bundle(
                root,
                "asset-only",
                production_kind="motion",
            )
            self.update_manifest(
                root,
                bundle,
                lambda asset: asset.update({"motion_approved": False}),
            )

            self.assert_code(root, bundle, "E_VISUAL_MOTION_APPROVAL")


if __name__ == "__main__":
    unittest.main()
