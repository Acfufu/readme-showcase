from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from skill.scripts.audit_readme import audit_svg_bytes
from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from skill.scripts.readme_showcase.visual_kernel.security import (
    MAX_VISUAL_SPEC_BYTES,
    validate_visual_security,
    validate_visual_svg_bytes,
    read_visual_bytes,
)

from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec


class VisualSecurityTests(unittest.TestCase):
    def _artifacts(self) -> dict[str, object]:
        payload = _spec("flow")
        payload["edges"] = [payload["edges"][0]]  # type: ignore[index]
        spec = validate_visual_spec(payload, EVIDENCE)
        plan = normalize_visual_spec(payload, EVIDENCE)
        return {
            "spec": spec,
            "scene": _build("flow"),
            "theme": resolve_theme(),
            "timeline": derive_timeline(plan),
            "interaction": derive_interaction(plan),
        }

    def test_compiler_outputs_pass_and_are_canonical(self) -> None:
        artifacts = self._artifacts()
        artifacts["svg"] = serialize_svg(artifacts["scene"], artifacts["theme"])
        result = validate_visual_security(**artifacts)
        self.assertEqual(set(result), {"spec", "scene", "theme", "timeline", "interaction", "svg"})
        with self.assertRaises(TypeError):
            result["scene"] = b"mutated"  # type: ignore[index]
        self.assertEqual(
            set(validate_visual_security(spec=artifacts["spec"], evidence_graph=EVIDENCE)),
            {"spec"},
        )
        for name, raw in result.items():
            self.assertIsInstance(raw, bytes)
            if name != "svg":
                self.assertEqual(raw, canonical_json_bytes(json.loads(raw)))

    def test_oversized_and_noncanonical_data_fail_without_returned_bytes(self) -> None:
        artifacts = self._artifacts()
        oversized = dict(artifacts["spec"].as_dict())
        oversized["intent"] = dict(oversized["intent"])
        oversized["intent"]["label"] = "x" * MAX_VISUAL_SPEC_BYTES
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(oversized)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_SIZE")

        raw = artifacts["scene"].canonical_bytes()
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(scene=raw[:-1])
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(scene=b"{")
        self.assertIsInstance(raised.exception, ContractError)
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(
                timeline={
                    "schema_version": 1,
                    "targets": [],
                    "duration_ms": 0,
                    "operations": [],
                }
            )
        self.assertEqual(raised.exception.code, "E_SCHEMA_MISSING_FIELD")
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(
                gate={
                    "schema_version": 1,
                    "status": "pass",
                    "spec_sha256": "",
                    "scene_sha256": "",
                    "svg_sha256": "",
                    "diagnostics": [{}],
                }
            )
        self.assertEqual(raised.exception.code, "E_SCHEMA_MISSING_FIELD")

    def test_svg_uses_authoritative_audit_and_rejects_malicious_corpus(self) -> None:
        valid = serialize_svg(_build("flow"), resolve_theme())
        self.assertEqual(audit_svg_bytes(valid), [])
        variants = (
            valid.replace(b"<svg ", b"<!DOCTYPE svg><svg "),
            valid.replace(b"<svg ", b'<svg onload="alert(1)" '),
            valid.replace(b"<text", b"<script/><text"),
            valid.replace(b"<text", b'<image href="https://example.invalid/x"/><text'),
            valid.replace(b"<text", b"<foreignObject/><text"),
            valid.replace(b"<text", b"<style>@import url(https://example.invalid/x)</style><text"),
            valid.replace(b"<g ", b"<g href=\"https://example.invalid/x\" "),
        )
        for candidate in variants:
            with self.subTest(candidate=candidate[:40]):
                with self.assertRaises(ContractError) as raised:
                    validate_visual_svg_bytes(candidate)
                self.assertEqual(raised.exception.code, "E_VISUAL_SVG_SECURITY")

    def test_svg_resource_bounds_fail_closed(self) -> None:
        valid = serialize_svg(_build("flow"), resolve_theme())
        too_many = valid.replace(b"</svg>", b"<path d=\"M0 0\"/>" * 2001 + b"</svg>")
        with self.assertRaises(ContractError) as raised:
            validate_visual_svg_bytes(too_many)
        self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")
        oversize = valid + b" " * (2 * 1024 * 1024)
        with self.assertRaises(ContractError) as raised:
            validate_visual_svg_bytes(oversize)
        self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")
        deep = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1" role="img">'
            + b"<title>x</title>"
            + b"<g>" * 65
            + b"<path d=\"M0 0\"/>"
            + b"</g>" * 65
            + b"</svg>"
        )
        with self.assertRaises(ContractError) as raised:
            validate_visual_svg_bytes(deep)
        self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")
        self.assertGreater(len(valid), 0)

    def test_scene_resource_bound_fails_before_promotion(self) -> None:
        scene = _build("flow").as_dict()
        text = next(item for item in scene["primitives"] if item["kind"] == "text")
        text["lines"] = ["x" * (2 * 1024 * 1024)]
        with self.assertRaises(ContractError) as raised:
            validate_visual_security(scene=scene)
        self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")

    def test_safe_reader_rejects_escape_symlink_and_special_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            outside = Path(temporary) / "outside"
            root.mkdir()
            outside.write_bytes(b"secret")
            (root / "ok.json").write_bytes(b"{}")
            self.assertEqual(read_visual_bytes(root, "ok.json", maximum=16), b"{}")
            with self.assertRaises(ContractError) as raised:
                read_visual_bytes(root, "ok.json", maximum=MAX_VISUAL_SPEC_BYTES * 100)
            self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")
            for relative in ("../outside", "/etc/passwd", "https://example.invalid/x", "a\\b"):
                with self.subTest(relative=relative):
                    with self.assertRaises(ContractError) as raised:
                        read_visual_bytes(root, relative, maximum=16)
                    self.assertEqual(raised.exception.code, "E_VISUAL_PATH")
                    self.assertNotIn(str(root), str(raised.exception))
            (root / "link.json").symlink_to(outside)
            with self.assertRaises(ContractError) as raised:
                read_visual_bytes(root, "link.json", maximum=16)
            self.assertEqual(raised.exception.code, "E_VISUAL_PATH")
            (root / "nested").mkdir()
            (root / "nested" / "link.json").symlink_to(outside)
            with self.assertRaises(ContractError):
                read_visual_bytes(root, "nested/link.json", maximum=16)
            (root / "ancestor-link").symlink_to(root / "nested", target_is_directory=True)
            with self.assertRaises(ContractError) as raised:
                read_visual_bytes(root, "ancestor-link/link.json", maximum=16)
            self.assertEqual(raised.exception.code, "E_VISUAL_PATH")
            with self.assertRaises(ContractError) as raised:
                read_visual_bytes(root, "missing.json", maximum=16)
            self.assertEqual(raised.exception.code, "E_VISUAL_PATH")
            fifo = root / "pipe"
            os.mkfifo(fifo)
            with self.assertRaises(ContractError) as raised:
                read_visual_bytes(root, "pipe", maximum=16)
            self.assertEqual(raised.exception.code, "E_VISUAL_PATH")


if __name__ == "__main__":
    unittest.main()
