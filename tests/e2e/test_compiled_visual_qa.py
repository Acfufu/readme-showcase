from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase import visual_kernel as facade
from skill.scripts.readme_showcase.contracts.evidence import validate_evidence_graph
from skill.scripts.readme_showcase.visual_kernel import compiler as compiler_module
from skill.scripts.readme_showcase.visual_kernel.elk_backend import ElkGeometryResult


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/visual-kernel/qa-cases.v1.json"
REQUESTED_EVIDENCE_DIR = Path(
    "/Users/example/Codehub/readme-showcase/.omo/evidence/ulw/"
    "archscribe-kernel-20260804/G001-implement-and-validate-the-approved/a1"
)
if os.environ.get("VISUAL_QA_EVIDENCE_DIR"):
    EVIDENCE_DIR = Path(os.environ["VISUAL_QA_EVIDENCE_DIR"])
elif REQUESTED_EVIDENCE_DIR.parent.exists():
    EVIDENCE_DIR = REQUESTED_EVIDENCE_DIR
else:
    EVIDENCE_DIR = REPO_ROOT / ".omo/evidence/visual-kernel-qa"
RECEIPT = EVIDENCE_DIR / "task-48-visual-qa.txt"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _json_bytes(artifacts: dict[str, bytes], path: str) -> dict[str, object]:
    raw = artifacts[path]
    value = json.loads(raw.decode("utf-8"))
    if canonical_json_bytes(value) != raw:
        raise AssertionError(f"{path} is not canonical JSON")
    return value


class CompiledVisualQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence_dir = EVIDENCE_DIR
        cls.evidence_dir.mkdir(parents=True, exist_ok=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        cls.receipt = RECEIPT
        cls.receipt.write_text(
            "Task 48 compiled visual QA receipt\n"
            f"HEAD {head}\n"
            f"TREE {tree}\n"
            f"PYTHON {sys.version.split()[0]}\n"
            f"FIXTURE {FIXTURE}\n"
            f"EVIDENCE_DIR {cls.evidence_dir}\n",
            encoding="utf-8",
        )

    @classmethod
    def _record(cls, line: str) -> None:
        with cls.receipt.open("a", encoding="utf-8") as stream:
            stream.write(line.rstrip("\n") + "\n")

    @classmethod
    def _fixture(cls) -> tuple[dict[str, object], dict[str, object]]:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1 or raw.get("primary_case") != "swimlane":
            raise AssertionError("QA fixture envelope is not v1 or has no primary swimlane")
        evidence = validate_evidence_graph(raw["evidence"])
        cases = raw.get("cases")
        if not isinstance(cases, list):
            raise AssertionError("QA fixture cases must be an array")
        by_id: dict[str, object] = {}
        for item in cases:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise AssertionError("QA fixture case has no stable ID")
            if item["id"] in by_id:
                raise AssertionError(f"duplicate QA case: {item['id']}")
            by_id[item["id"]] = item
        if set(by_id) != {"architecture", "flow", "sequence", "swimlane"}:
            raise AssertionError("QA fixture must cover all four approved intents")
        return evidence, by_id

    @classmethod
    def _compile(cls, case: dict[str, object], evidence: dict[str, object]):
        spec = case["spec"]
        result = facade.compile_visual(spec, evidence)
        repeat = facade.compile_visual(copy.deepcopy(spec), copy.deepcopy(evidence))
        if result != repeat:
            raise AssertionError(f"{case['id']} compilation is not byte-deterministic")
        return result

    def _assert_compiled_surfaces(
        self,
        case_id: str,
        case: dict[str, object],
        compiled: object,
    ) -> None:
        artifacts = dict(compiled.artifacts)
        spec = case["spec"]
        locale = spec["locale"]
        self.assertEqual(set(spec["variants"]), {"desktop", "mobile"})
        self.assertEqual(len(artifacts), 13)
        spec_raw = artifacts["compiled/visual-spec.json"]
        spec_hash = hashlib.sha256(spec_raw).hexdigest()
        inventory = _json_bytes(artifacts, "compiled/inventory.json")
        self.assertEqual(inventory["inventory_sha256"], compiled.inventory_sha256)
        self.assertEqual(
            canonical_json_bytes(inventory),
            artifacts["compiled/inventory.json"],
        )
        inventory_paths: set[str] = set()
        for layer in inventory["layers"]:
            if "records" not in layer:
                continue
            for record in layer["records"]:
                if "path" not in record:
                    continue
                path = record["path"]
                inventory_paths.add(path)
                self.assertIn(path, artifacts)
                self.assertEqual(record["sha256"], hashlib.sha256(artifacts[path]).hexdigest())
        self.assertEqual(inventory_paths, set(artifacts) - {"compiled/inventory.json"})

        spec_kind = spec["intent"]["kind"]
        self.assertEqual(spec_kind, case_id)
        back_edges = {edge["id"] for edge in spec["edges"] if edge["kind"] == "back"}
        self.assertTrue(back_edges, f"{case_id} must exercise an explicit back edge")
        for variant, render_width in (("desktop", 900), ("mobile", 360)):
            prefix = f"assets/readme-showcase/{locale}/{variant}"
            svg_path = f"{prefix}.svg"
            scene_path = f"compiled/scenes/{locale}/{variant}.json"
            gate_path = f"compiled/gates/{locale}/{variant}.json"
            self.assertIn(svg_path, artifacts)
            self.assertIn(scene_path, artifacts)
            self.assertIn(gate_path, artifacts)
            scene = _json_bytes(artifacts, scene_path)
            gate = _json_bytes(artifacts, gate_path)
            svg = artifacts[svg_path]
            self.assertEqual(gate["status"], "pass")
            self.assertEqual(gate["diagnostics"], [])
            self.assertEqual(gate["spec_sha256"], spec_hash)
            scene_hash = hashlib.sha256(artifacts[scene_path]).hexdigest()
            svg_hash = hashlib.sha256(svg).hexdigest()
            self.assertEqual(gate["scene_sha256"], scene_hash)
            self.assertEqual(gate["svg_sha256"], svg_hash)
            self.assertEqual(scene["locale"], locale)
            self.assertEqual(scene["variant"], variant)
            scene_edge_ids = {
                primitive["source_id"]
                for primitive in scene["primitives"]
                if primitive["kind"] in {"line", "path"}
            }
            self.assertTrue(back_edges.issubset(scene_edge_ids))

            root = ET.fromstring(svg)
            self.assertEqual(root.tag.rsplit("}", 1)[-1], "svg")
            self.assertEqual(int(root.attrib["width"]), render_width)
            self.assertGreater(int(root.attrib["height"]), 0)
            view_box = tuple(int(value) for value in root.attrib["viewBox"].split())
            self.assertEqual(len(view_box), 4)
            self.assertEqual(
                int(root.attrib["height"]),
                (view_box[3] * render_width + view_box[2] - 1) // view_box[2],
            )
            titles = [node for node in root.iter() if _local_name(node.tag) == "title"]
            self.assertEqual(len(titles), 1)
            self.assertEqual("".join(titles[0].itertext()), spec["intent"]["label"])
            self.assertEqual(root.attrib["aria-labelledby"], "scene-title")
            dom_by_scene_id: dict[str, ET.Element] = {}
            dom_ids: set[str] = set()
            for node in root.iter():
                scene_id = node.attrib.get("data-scene-id")
                if scene_id is None:
                    continue
                self.assertNotIn(scene_id, dom_by_scene_id)
                dom_by_scene_id[scene_id] = node
                self.assertNotIn(node.attrib["id"], dom_ids)
                dom_ids.add(node.attrib["id"])
                expected_dom_id = "scene-p-" + hashlib.sha256(scene_id.encode()).hexdigest()
                self.assertEqual(node.attrib["id"], expected_dom_id)
            scene_ids = {primitive["id"] for primitive in scene["primitives"]}
            self.assertEqual(set(dom_by_scene_id), scene_ids)
            self.assertEqual(root.attrib["data-scene-locale"], locale)
            self.assertEqual(root.attrib["data-scene-variant"], variant)
            if case_id == "swimlane":
                svg_text = svg.decode("utf-8")
                for label in ("数据流泳道", "网关", "存储"):
                    self.assertIn(label, svg_text)
            self._record(
                f"PASS case={case_id} variant={variant} "
                f"svg_bytes={len(svg)} scene_sha256={scene_hash} "
                f"gate_sha256={hashlib.sha256(artifacts[gate_path]).hexdigest()} "
                f"inventory_sha256={compiled.inventory_sha256}"
            )

    def test_compile_variants_bind_svg_gate_inventory_and_back_edges(self) -> None:
        evidence, cases = self._fixture()
        for case_id in ("architecture", "flow", "sequence", "swimlane"):
            case = cases[case_id]
            compiled = self._compile(case, evidence)
            self._assert_compiled_surfaces(case_id, case, compiled)
        self._record("PASS scenario=all-four-intents deterministic compile facade")

    def test_rasterize_primary_swimlane_and_record_receipt(self) -> None:
        evidence, cases = self._fixture()
        primary = cases["swimlane"]
        compiled = self._compile(primary, evidence)
        self._assert_compiled_surfaces("swimlane", primary, compiled)
        renderer = shutil.which("rsvg-convert")
        if renderer is None:
            reason = "rsvg-convert unavailable; raster QA explicitly skipped"
            self._record(f"SKIP scenario=primary-raster reason={reason}")
            self.skipTest(reason)
        version = subprocess.run(
            [renderer, "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip().splitlines()[0]
        self._record(f"RENDERER path={renderer} version={version}")
        with tempfile.TemporaryDirectory(prefix="visual-qa-") as temporary:
            root = Path(temporary)
            for variant, width, evidence_name in (
                ("desktop", 900, "task-48-desktop-900.png"),
                ("mobile", 360, "task-48-mobile-360.png"),
            ):
                svg_path = root / f"{variant}.svg"
                png_path = root / f"{variant}.png"
                svg_path.write_bytes(
                    compiled.artifacts[f"assets/readme-showcase/zh-Hans/{variant}.svg"]
                )
                command = [
                    renderer,
                    "--width",
                    str(width),
                    "--output",
                    str(png_path),
                    str(svg_path),
                ]
                completed = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertEqual(completed.returncode, 0)
                png = png_path.read_bytes()
                self.assertGreater(len(png), 24)
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                actual_width, actual_height = struct.unpack(">II", png[16:24])
                self.assertEqual(actual_width, width)
                self.assertGreater(actual_height, 0)
                retained = self.evidence_dir / evidence_name
                retained.write_bytes(png)
                self.assertGreater(retained.stat().st_size, 24)
                self._record(
                    f"PASS scenario=raster variant={variant} command={' '.join(command)} "
                    f"png={retained} bytes={len(png)} dimensions={actual_width}x{actual_height}"
                )

    def test_failure_mutations_abort_before_raster_acceptance(self) -> None:
        evidence, cases = self._fixture()
        architecture = cases["architecture"]
        original_spec = copy.deepcopy(architecture["spec"])
        real_backend = compiler_module.render_elk_geometry

        def one_pixel_overlap(envelope: object, attempt: str) -> ElkGeometryResult:
            result = real_backend(envelope, attempt)
            raw = result.as_dict()
            if envelope["direction"] == "LR":
                by_id = {item["id"]: item for item in raw["geometry"]["nodes"]}
                first = by_id[envelope["nodes"][0]["id"]]
                second = by_id[envelope["nodes"][1]["id"]]
                old = dict(second)
                second["x"] = first["x"] + first["width"] - 1
                second["y"] = first["y"]
                dx = second["x"] - old["x"]
                dy = second["y"] - old["y"]
                for edge in raw["geometry"]["edges"]:
                    for section in edge["sections"]:
                        for name in ("start", "end"):
                            point = section[name]
                            if (
                                old["x"] <= point["x"] <= old["x"] + old["width"]
                                and old["y"] <= point["y"] <= old["y"] + old["height"]
                            ):
                                point["x"] += dx
                                point["y"] += dy
            return ElkGeometryResult(raw["geometry"], raw["metadata"])

        with tempfile.TemporaryDirectory(prefix="visual-qa-failure-") as temporary:
            with mock.patch.object(
                compiler_module,
                "render_elk_geometry",
                side_effect=one_pixel_overlap,
            ):
                with self.assertRaises(ContractError) as raised:
                    facade.compile_visual(architecture["spec"], evidence)
            self.assertEqual(raised.exception.code, "E_VISUAL_OVERLAP")
            self.assertEqual(architecture["spec"], original_spec)
            self.assertFalse((Path(temporary) / "should-not-rasterize.png").exists())
            self._record(
                "PASS scenario=1px-overlap gate_failed_before_png "
                f"code={raised.exception.code}"
            )

        text_overflow = copy.deepcopy(original_spec)
        text_overflow["intent"]["label"] = "x" * 50
        with tempfile.TemporaryDirectory(prefix="visual-qa-text-failure-") as temporary:
            with self.assertRaises(ContractError) as raised:
                facade.compile_visual(text_overflow, evidence)
            self.assertEqual(raised.exception.code, "E_VISUAL_TEXT_FIT")
            self.assertEqual(architecture["spec"], original_spec)
            self.assertFalse((Path(temporary) / "should-not-rasterize.png").exists())
            self._record(
                "PASS scenario=text-overflow gate_failed_before_png "
                f"code={raised.exception.code}"
            )

        real_serializer = compiler_module.serialize_svg

        def missing_title(scene: object, theme: object) -> bytes:
            svg = real_serializer(scene, theme)
            return re.sub(
                rb'<title id="scene-title">.*?</title>',
                b'<title id="scene-title"></title>',
                svg,
                count=1,
            )

        with tempfile.TemporaryDirectory(prefix="visual-qa-title-failure-") as temporary:
            with mock.patch.object(
                compiler_module,
                "serialize_svg",
                side_effect=missing_title,
            ):
                with self.assertRaises(ContractError) as raised:
                    facade.compile_visual(architecture["spec"], evidence)
            self.assertIn(raised.exception.code, {"E_VISUAL_SVG_SECURITY", "E_VISUAL_TEXT_FIT"})
            self.assertEqual(architecture["spec"], original_spec)
            self.assertFalse((Path(temporary) / "should-not-rasterize.png").exists())
            self._record(
                "PASS scenario=missing-title gate_failed_before_png "
                f"code={raised.exception.code}"
            )


if __name__ == "__main__":
    unittest.main()
