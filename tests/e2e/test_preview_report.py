from __future__ import annotations

import fcntl
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.orchestration import runner as runner_module
from skill.scripts.readme_showcase.preview import report as report_module
from skill.scripts.readme_showcase.preview import renderer as renderer_module
from tests import test_pipeline_contracts as pipeline_contracts


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
RUN_FIXTURES = REPO_ROOT / "tests/fixtures/run-workspaces"
MALICIOUS = REPO_ROOT / "tests/fixtures/preview/malicious-candidate"


class _OfflineHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)


class PreviewReportTests(unittest.TestCase):
    def test_renderer_exposes_arbitrary_declared_locales_without_filename_inference(self) -> None:
        readmes = {"docs/readme-zh.md": "English", "localized/README.md": "日本語"}
        report = {
            "locale_by_path": {"docs/readme-zh.md": "en", "localized/README.md": "ja"},
        }
        files = renderer_module._readme_documents(report, readmes)
        self.assertEqual(set(files), {"locales/en.escaped.html", "locales/ja.escaped.html"})
        self.assertIn(b"English", files["locales/en.escaped.html"])
        self.assertIn("日本語".encode(), files["locales/ja.escaped.html"])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.workspace = self.root / "workspace"
        self.target.mkdir()
        (self.target / "README.md").write_text("target repository evidence\n", encoding="utf-8")
        (self.target / "README_zh.md").write_text("# 之前\n\n旧项目文本。\n", encoding="utf-8")
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "README.md", "README_zh.md")
        self.git("commit", "-m", "fixture")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.target, check=True, capture_output=True)

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments], cwd=REPO_ROOT,
            capture_output=True, text=True, check=False,
        )

    def prepare(self, *, malicious: bool = False) -> subprocess.CompletedProcess[str]:
        started = self.cli(
            "run", "--root", str(self.target), "--workspace", str(self.workspace),
            "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
            "--plan", str(RUN_FIXTURES / "v1-plan.json"), "--stop-after", "generation-request",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        candidate = self.workspace / "stages/05-candidate"
        shutil.copytree(RUN_FIXTURES / "v1-candidate", candidate, dirs_exist_ok=True)
        if malicious:
            shutil.copyfile(MALICIOUS / "README.md", candidate / "README.md")
            shutil.copyfile(MALICIOUS / "README_zh.md", candidate / "README_zh.md")
            claim_map_path = candidate / "claim-map.json"
            claim_map = json.loads(claim_map_path.read_text(encoding="utf-8"))
            claim_map["markdown_blocks"][0]["content_sha256"] = hashlib.sha256(
                (candidate / "README.md").read_bytes().rstrip(b"\n")
            ).hexdigest()
            claim_map_path.write_bytes(canonical_json_bytes(claim_map))
        return self.cli("resume", "--workspace", str(self.workspace))

    def preview_bytes(self) -> dict[str, bytes]:
        root = self.workspace / "output/preview"
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    def prepare_compiled(self) -> Path:
        plan, candidate, _, _ = pipeline_contracts.BundleAssembleStageTests._compiled_inputs_with_v1_evidence()
        plan_path = self.root / "readme-plan-v3.json"
        plan_path.write_bytes(canonical_json_bytes(plan))
        started = self.cli(
            "run", "--root", str(self.target), "--workspace", str(self.workspace),
            "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
            "--plan", str(plan_path),
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(json.loads(started.stdout)["status"], "waiting-for-candidate")
        candidate_root = self.workspace / "stages/05-candidate"
        for relative, raw in candidate.items():
            destination = candidate_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        resumed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        stage6 = self.workspace / "stages/06-bundle-assemble/attempts/1"
        self.assertTrue((stage6 / "compiled/scenes/en/desktop.json").is_file())
        return stage6

    def test_cli_produces_deterministic_five_surface_offline_report(self) -> None:
        completed = self.prepare()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        first = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = self.preview_bytes()
        required = {"index.html", "report.json", "README.escaped.html", "README_zh.escaped.html"}
        self.assertTrue(required.issubset(first_bytes))
        self.assertTrue(any(name.startswith("assets/") for name in first_bytes))
        self.assertTrue(all(first_bytes[name] for name in first_bytes))

        report = json.loads(first_bytes["report.json"])
        manifest = json.loads((self.workspace / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["generated_at"], manifest["created_at"])
        self.assertNotIn("compiled", report)
        for section in ("diff", "evidence", "diagnostics", "evaluation", "editorial", "mobile"):
            self.assertIn(section, report)
            self.assertTrue(report[section])

        parser = _OfflineHTMLParser()
        parser.feed(first_bytes["index.html"].decode("utf-8"))
        self.assertFalse({"script", "iframe", "object", "embed"} & set(parser.tags))
        self.assertFalse(any(name.lower().startswith("on") for name, _ in parser.attributes))
        self.assertFalse(any(
            value and (value.startswith(("http:", "https:", "//")) or "url(" in value.lower())
            for _name, value in parser.attributes
        ))
        index = first_bytes["index.html"].decode("utf-8")
        for marker in ("Rendered README", "Diff", "Evidence and claims", "Evaluation", "Mobile / narrow view"):
            self.assertIn(marker, index)

        second = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_bytes, self.preview_bytes())

    def test_compiled_v3_preview_accepts_nested_stage_outputs(self) -> None:
        stage6 = self.prepare_compiled()

        first = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn("E_PREVIEW_PATH", first.stderr)
        first_bytes = self.preview_bytes()
        report = json.loads(first_bytes["report.json"])
        self.assertIn("compiled", report)
        compiled = report["compiled"]
        self.assertEqual(
            set(compiled),
            {"bundle", "inventory", "artifacts", "identities", "viewports"},
        )
        self.assertEqual(set(compiled["bundle"]), {"path", "sha256"})
        self.assertEqual(set(compiled["inventory"]), {"path", "sha256", "fingerprint"})
        self.assertEqual(
            set(compiled["artifacts"]),
            {"svgs", "scenes", "gates", "timelines", "interactions"},
        )
        for references in compiled["artifacts"].values():
            self.assertTrue(references)
            self.assertEqual(
                references,
                sorted(references, key=lambda item: item["path"].encode("utf-8")),
            )
            for reference in references:
                self.assertEqual(set(reference), {"path", "sha256"})
                self.assertFalse(Path(reference["path"]).is_absolute())
                self.assertNotIn("..", Path(reference["path"]).parts)
        self.assertEqual(set(compiled["identities"]), {"kernel", "elk", "renderer"})
        self.assertTrue(compiled["viewports"]["complete"])
        self.assertEqual(len(compiled["viewports"]["checks"]), 2)
        self.assertEqual(
            {(item["locale"], item["variant"]) for item in compiled["viewports"]["checks"]},
            {("en", "desktop"), ("en", "mobile")},
        )
        report_text = first_bytes["report.json"].decode("utf-8")
        self.assertNotIn(str(self.workspace), report_text)
        self.assertNotIn("CODEX_HOME", report_text)
        second = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_bytes, self.preview_bytes())

    def test_compiled_v3_preview_exposes_safe_viewports_and_static_fallback(self) -> None:
        stage6 = self.prepare_compiled()
        rendered = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        files = self.preview_bytes()
        expected_assets = {
            "assets/readme-showcase/en/desktop.svg",
            "assets/readme-showcase/en/mobile.svg",
        }
        self.assertTrue(expected_assets.issubset(files))
        self.assertNotIn("assets/README.txt", files)
        for path in expected_assets:
            self.assertEqual(files[path], (stage6 / path).read_bytes())
            renderer_module._validate_compiled_svg(files[path])

        index = files["index.html"].decode("utf-8")
        self.assertIn("Desktop viewport", index)
        self.assertIn("Mobile viewport", index)
        self.assertIn("Static interaction fallback", index)
        self.assertIn("assets/readme-showcase/en/desktop.svg", index)
        self.assertIn("assets/readme-showcase/en/mobile.svg", index)
        self.assertNotIn('"layers"', index)
        self.assertNotIn(str(self.workspace), index)
        parser = _OfflineHTMLParser()
        parser.feed(index)
        self.assertFalse({"script", "iframe", "object", "embed"} & set(parser.tags))
        self.assertFalse(any(name.lower().startswith("on") for name, _ in parser.attributes))
        interaction = json.loads((stage6 / "compiled/interaction/en/desktop.json").read_bytes())
        for identifier in interaction["focus_order"]:
            self.assertIn(identifier, index)

    def test_compiled_artifact_drift_preserves_last_good_preview(self) -> None:
        stage6 = self.prepare_compiled()
        rendered = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        before = self.preview_bytes()
        for relative in (
            "assets/readme-showcase/en/desktop.svg",
            "compiled/scenes/en/desktop.json",
            "compiled/gates/en/desktop.json",
            "compiled/interaction/en/desktop.json",
        ):
            path = stage6 / relative
            original = path.read_bytes()
            path.write_bytes(original[:-1] + bytes((original[-1] ^ 1,)))
            failed = self.cli("preview", "--workspace", str(self.workspace))
            self.assertEqual(failed.returncode, 2)
            self.assertRegex(failed.stderr, r"E_PREVIEW_STALE|E_VISUAL_FINGERPRINT")
            self.assertEqual(before, self.preview_bytes())
            path.write_bytes(original)

        mobile = stage6 / "assets/readme-showcase/en/mobile.svg"
        mobile_raw = mobile.read_bytes()
        mobile.unlink()
        failed = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2)
        self.assertRegex(failed.stderr, r"E_PREVIEW_STALE|E_VISUAL_FINGERPRINT|E_VISUAL_PATH")
        self.assertEqual(before, self.preview_bytes())
        mobile.write_bytes(mobile_raw)

        bundle = stage6 / "generated-readme-bundle.json"
        bundle_raw = bundle.read_bytes()
        bundle.write_bytes(bundle_raw[:-1] + bytes((bundle_raw[-1] ^ 1,)))
        failed = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("E_PREVIEW_STALE", failed.stderr)
        self.assertEqual(before, self.preview_bytes())
        bundle.write_bytes(bundle_raw)

        svg = stage6 / "assets/readme-showcase/en/desktop.svg"
        svg_raw = svg.read_bytes()
        outside = self.root / "outside-stage6.svg"
        outside.write_bytes(svg_raw)
        svg.unlink()
        svg.symlink_to(outside)
        failed = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2)
        self.assertRegex(failed.stderr, r"E_PREVIEW_STALE|E_PREVIEW_PATH")
        self.assertEqual(before, self.preview_bytes())
        svg.unlink()
        svg.write_bytes(svg_raw)

    def test_malicious_candidate_is_literal_and_unsafe_assets_fail_closed(self) -> None:
        resumed = self.prepare(malicious=True)
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        candidate = self.workspace / "stages/05-candidate"
        (candidate / "assets/attack.svg").write_bytes((MALICIOUS / "asset.svg").read_bytes())
        failed = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("E_PREVIEW_PATH", failed.stderr)
        self.assertFalse((self.workspace / "output/preview").exists())

        (candidate / "assets/attack.svg").unlink()
        rendered = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        document = (self.workspace / "output/preview/README.escaped.html").read_text(encoding="utf-8")
        self.assertIn(html.escape('<script>alert("preview-script")</script>'), document)
        parser = _OfflineHTMLParser(); parser.feed(document)
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("iframe", parser.tags)

    def test_symlink_stale_hash_malformed_manifest_and_concurrency_fail_closed(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        candidate = self.workspace / "stages/05-candidate"
        outside = self.root / "outside.svg"
        outside.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"/>", encoding="utf-8")
        linked = candidate / "assets/linked.svg"
        linked.symlink_to(outside)
        symlinked = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(symlinked.returncode, 2)
        self.assertIn("E_PREVIEW_PATH", symlinked.stderr)
        linked.unlink()

        readme = candidate / "README.md"
        readme.write_bytes(readme.read_bytes() + b"stale\n")
        stale = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(stale.returncode, 2)
        self.assertIn("E_PREVIEW_STALE", stale.stderr)
        readme.write_bytes(readme.read_bytes()[:-6])

        lock = os.open(self.workspace / ".runner.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            concurrent = self.cli("preview", "--workspace", str(self.workspace))
        finally:
            os.close(lock)
        self.assertEqual(concurrent.returncode, 2)
        self.assertIn("E_RUN_LOCKED", concurrent.stderr)

        manifest_path = self.workspace / "run-manifest.json"
        before = manifest_path.read_bytes()
        manifest_path.write_bytes(b"{malformed")
        malformed = self.cli("preview", "--workspace", str(self.workspace))
        self.assertEqual(malformed.returncode, 2)
        self.assertIn("E_INPUT_JSON", malformed.stderr)
        manifest_path.write_bytes(before)
        self.assertFalse((self.workspace / "output/preview").exists())

    def test_preview_never_enters_candidate_hashes(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        manifest_before = (self.workspace / "run-manifest.json").read_bytes()
        candidate_before = json.loads(manifest_before)["stages"][4]["output_sha256"]
        self.assertEqual(self.cli("preview", "--workspace", str(self.workspace)).returncode, 0)
        manifest_after = (self.workspace / "run-manifest.json").read_bytes()
        self.assertEqual(manifest_before, manifest_after)
        self.assertEqual(candidate_before, json.loads(manifest_after)["stages"][4]["output_sha256"])
        self.assertFalse(any("output/preview" in path for path in self.preview_bytes()))

    def test_mid_render_candidate_mutation_fails_without_replacing_preview(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(self.cli("preview", "--workspace", str(self.workspace)).returncode, 0)
        preview_before = self.preview_bytes()
        readme = self.workspace / "stages/05-candidate/README.md"
        original_fingerprint = report_module.CandidateImportStage.fingerprint
        mutated = False

        def mutate_after_fingerprint(stage: object, context: object) -> str:
            nonlocal mutated
            fingerprint = original_fingerprint(stage, context)
            if not mutated:
                readme.write_bytes(readme.read_bytes() + b"mid-render mutation\n")
                mutated = True
            return fingerprint

        with mock.patch.object(
            report_module.CandidateImportStage,
            "fingerprint",
            new=mutate_after_fingerprint,
        ):
            with self.assertRaises(ContractError) as raised:
                runner_module.preview_run(self.workspace)
        self.assertEqual(raised.exception.code, "E_PREVIEW_STALE")
        self.assertTrue(mutated)
        self.assertEqual(self.preview_bytes(), preview_before)


if __name__ == "__main__":
    unittest.main()
