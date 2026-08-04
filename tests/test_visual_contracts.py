from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.readme_showcase.contracts import plan as plan_contract
from skill.scripts.pipeline_core import validate_generated_bundle
from tests import test_bundle_contracts


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURES = REPO_ROOT / "tests/fixtures/contracts"


class VisualContractTests(unittest.TestCase):
    builder = test_bundle_contracts.BundleContractTests()

    def test_legacy_plan_canonical_bytes_and_fixture_hashes_are_pinned(self) -> None:
        expected_fixture_hashes = {
            "readme-plan-v1.valid.json": "ecfd65f67dabb6ea688ddd99db9b472863ffb3a338c39e786a94cbf85648a3e6",
            "readme-plan-v1.invalid.json": "4cd8a573d310e4dd0e521e24d2ab9a5d866b3f1fa9ba8c3b8d0787f3c573b10d",
            "readme-plan-v2.valid.json": "0505e851996c2343590afdee622ab5468e382a1ff5f7aba401d12d8dc0a0993c",
            "readme-plan-v2.invalid.json": "6fb138e0ff1348f88673321104a84cf33784a56ccf347776e8636adaafe92474",
        }
        for name, expected in expected_fixture_hashes.items():
            with self.subTest(fixture=name):
                raw = (CONTRACT_FIXTURES / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

        v1 = json.loads(
            (CONTRACT_FIXTURES / "readme-plan-v1.valid.json").read_text(
                encoding="utf-8"
            )
        )
        v1_bytes = plan_contract.canonical_readme_plan_bytes(v1, version=1)
        v1_fixture_bytes = (CONTRACT_FIXTURES / "readme-plan-v1.valid.json").read_bytes()
        self.assertEqual(v1_bytes, v1_fixture_bytes)
        self.assertEqual(
            hashlib.sha256(v1_bytes).hexdigest(),
            "ecfd65f67dabb6ea688ddd99db9b472863ffb3a338c39e786a94cbf85648a3e6",
        )

        v2 = json.loads(
            (CONTRACT_FIXTURES / "readme-plan-v2.valid.json").read_text(
                encoding="utf-8"
            )
        )
        # The fixture's FACT_ID token is intentionally schema-neutral; bind it to
        # a normative Evidence v2 ID before exercising the v2 canonical producer.
        v2["evidence_ids"] = ["file:" + "a" * 64]
        expected_v2 = (
            '{"commands":[],"diagram_route":"static","evidence_ids":["file:'
            + "a" * 64
            + '"],"locales":[{"readme_path":"docs/primary.md","tag":"en"},'
            '{"readme_path":"localized/guide.md","tag":"zh-Hans"},'
            '{"readme_path":"notes/release.md","tag":"ja"}],"mode":"readme",'
            '"schema_version":2,"sections":["overview"],"visual_intent":"hero"}\n'
        ).encode("utf-8")
        v2_bytes = plan_contract.canonical_readme_plan_bytes(v2, version=2)
        self.assertEqual(v2_bytes, expected_v2)
        self.assertEqual(
            hashlib.sha256(v2_bytes).hexdigest(),
            "86a4973990ea0e5c6b198c235e1fdf7a496358a8d681b2a231cb45710bc2d694",
        )

    def test_legacy_plan_regression_detects_one_byte_serializer_drift(self) -> None:
        payload = json.loads(
            (CONTRACT_FIXTURES / "readme-plan-v1.valid.json").read_text(
                encoding="utf-8"
            )
        )
        expected = plan_contract.canonical_readme_plan_bytes(payload, version=1)
        expected_sha = hashlib.sha256(expected).hexdigest()
        original = plan_contract.canonical_readme_plan_bytes

        def mutated(*args: Any, **kwargs: Any) -> bytes:
            raw = original(*args, **kwargs)
            # Flip one canonical payload byte in the temporary test double only.
            index = raw.index(b"project-structure")
            return raw[:index] + bytes([raw[index] ^ 1]) + raw[index + 1 :]

        with mock.patch.object(plan_contract, "canonical_readme_plan_bytes", side_effect=mutated):
            with self.assertRaises(AssertionError):
                self.assertEqual(
                    hashlib.sha256(
                        plan_contract.canonical_readme_plan_bytes(payload, version=1)
                    ).hexdigest(),
                    expected_sha,
                )

        # The patch is restored and the real fixture/serializer bytes remain fixed.
        self.assertEqual(plan_contract.canonical_readme_plan_bytes(payload, version=1), expected)
        self.assertEqual(hashlib.sha256(expected).hexdigest(), expected_sha)

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
