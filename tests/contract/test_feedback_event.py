from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.feedback import (
    EVENTS,
    build_feedback_event,
    feedback_event_id,
    validate_feedback_event,
)
from skill.scripts.readme_showcase.delivery.feedback import (
    LOG_PATH,
    append_feedback_event,
    record_feedback,
)
from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "skill/scripts/readme_pipeline.py"
FIXTURES = ROOT / "tests/fixtures/contracts"
CONFIG = {
    "mode": "readme",
    "project_type": "developer-tool",
    "locales": ["en"],
    "scanner_profile": "balanced",
}


class FeedbackEventContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "target"
        self.workspace = self.root / "workspace"
        manifest = self.initialize_workspace(self.workspace, self.target)
        self.run_id = manifest["run_id"]
        self.fingerprint = "e" * 64
        self.delivery_path = self.root / "delivery-result.json"
        self.delivery = {
            "schema_version": 1, "status": "delivered", "operation_id": self.fingerprint,
            "branch": "readme-showcase/aaaaaaaaaaaa", "commit_sha": "c" * 40,
            "pr_url": "https://github.com/owner/repo/pull/7", "pr_number": 7,
            "reason": None, "attempts": 4, "idempotent": False,
        }
        write_canonical_json_atomic(self.delivery_path, self.delivery)

    @staticmethod
    def initialize_workspace(workspace: Path, target: Path) -> dict[str, object]:
        target.mkdir()
        return RunWorkspace(workspace, target).initialize(
            repository="owner/repo", base_sha="a" * 40, configuration=CONFIG, clock=lambda: "2026-08-04T00:00:00Z",
        )

    def details(self, event: str) -> dict[str, object]:
        return {
            "preview-approved": {"pattern_ids": ["pattern:hero"], "accepted_ids": ["section:overview"]},
            "preview-rejected": {"section_ids": ["section:install"], "rejected_ids": ["section:install"]},
            "candidate-edited": {"manual_edit_distance": {"changed": 2, "total": 10}},
            "asset-rejected": {"asset_ids": ["asset:hero"], "rejected_ids": ["asset:hero"]},
            "pr-opened": {"pr_number": 7, "pr_outcome": "opened"},
            "pr-closed": {"pr_number": 7, "pr_outcome": "closed"},
            "pr-merged": {"pr_number": 7, "pr_outcome": "merged"},
        }[event]

    def event(self, event: str = "preview-approved", second: int = 0) -> dict[str, object]:
        return self.build(event=event, recorded_at=f"2026-08-04T12:00:{second:02d}Z")

    def build(self, *, event: str = "preview-approved", recorded_at: str = "2026-08-04T12:00:00Z",
              details: object | None = None) -> dict[str, object]:
        return build_feedback_event(
            run_id=self.run_id, fingerprint=self.fingerprint, event=event,
            recorded_at=recorded_at, details=self.details(event) if details is None else details,
        )

    def assert_code(self, code: str, function: Callable[..., object], *arguments: object,
                    **keywords: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*arguments, **keywords)
        self.assertEqual(raised.exception.code, code)

    def log_path(self) -> Path:
        return self.workspace / LOG_PATH

    def record(self, *, workspace: Path | None = None, fingerprint: str | None = None) -> dict[str, object]:
        return record_feedback(
            workspace=workspace or self.workspace, delivery_result_path=self.delivery_path,
            run_id=self.run_id, fingerprint=fingerprint or self.fingerprint, event="preview-approved",
            details=self.details("preview-approved"),
            recorded_at="2026-08-04T12:00:00Z",
        )

    def run_cli(self, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments], cwd=ROOT, capture_output=True, text=True,
        )

    def test_fixture_schema_python_parity_and_every_event_shape(self) -> None:
        schema = json.loads((ROOT / "skill/schemas/feedback-event.v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        draft = Draft202012Validator(schema)
        fixture = json.loads((FIXTURES / "feedback-event-v1.valid.json").read_text())
        self.assertEqual(list(draft.iter_errors(fixture)), [])
        self.assertEqual(validate_feedback_event(fixture), fixture)
        for index, event in enumerate(EVENTS):
            with self.subTest(event=event):
                payload = self.event(event, index)
                self.assertEqual(list(draft.iter_errors(payload)), [])
                self.assertEqual(validate_feedback_event(payload), payload)

    def test_schema_acceptance_requires_python_semantic_validation(self) -> None:
        schema = json.loads((ROOT / "skill/schemas/feedback-event.v1.schema.json").read_text())
        draft = Draft202012Validator(schema)
        cases = (
            ({"recorded_at": "2026-08-04Z"}, "E_FEEDBACK_TIME"),
            ({"details": {"accepted_ids": ["section:ok"], "pr_number": 7, "pr_outcome": "opened"}}, "E_FEEDBACK_DETAILS"),
            ({"details": {"accepted_ids": ["section:z", "section:a"]}}, "E_FEEDBACK_ID"),
        )
        for changes, code in cases:
            payload = {**self.event(), **changes}
            payload["event_id"] = feedback_event_id({key: value for key, value in payload.items() if key != "event_id"})
            with self.subTest(code=code):
                self.assertEqual(list(draft.iter_errors(payload)), [])
                self.assert_code(code, validate_feedback_event, payload)

    def test_injected_time_is_deterministic_and_event_id_binds_every_field(self) -> None:
        first = self.event()
        self.assertEqual(first, self.event())
        changed_time = self.event(second=1)
        self.assertNotEqual(first["event_id"], changed_time["event_id"])
        for field, value in (("run_id", "f" * 64), ("fingerprint", "d" * 64)):
            changed = dict(first)
            changed[field] = value
            self.assert_code("E_FEEDBACK_ID", validate_feedback_event, changed)
        changed_event = dict(first)
        changed_event["event"] = "preview-rejected"
        changed_event["details"] = self.details("preview-rejected")
        self.assert_code("E_FEEDBACK_ID", validate_feedback_event, changed_event)

    def test_details_ids_counts_privacy_and_unknown_fields_fail_closed(self) -> None:
        cases = (
            ({"accepted_ids": ["b", "a"]}, "E_FEEDBACK_ID"),
            ({"accepted_ids": ["a", "a"]}, "E_FEEDBACK_ID"),
            ({"accepted_ids": ["user:7"]}, "E_FEEDBACK_PRIVACY"),
            ({"manual_edit_distance": {"changed": 2, "total": 1}}, "E_FEEDBACK_DETAILS"),
            ({"manual_edit_distance": {"changed": 1.0, "total": 1}}, "E_FEEDBACK_DETAILS"),
            ({"comment_body": "private"}, "E_SCHEMA_UNKNOWN_FIELD"),
            ({"accepted_ids": ["section:ok"], "source": "secret"}, "E_SCHEMA_UNKNOWN_FIELD"),
        )
        for details, code in cases:
            with self.subTest(details=details):
                self.assert_code(
                    code, self.build,
                    event="candidate-edited" if "manual_edit_distance" in details else "preview-approved",
                    details=details,
                )
        self.assert_code("E_FEEDBACK_EVENT", self.build, event="uploaded", details={"accepted_ids": ["section:ok"]})

    def test_nonexistent_run_and_stale_fingerprint_do_not_create_log(self) -> None:
        self.assert_code("E_FEEDBACK_RUN", self.record, workspace=self.root / "missing")
        self.assert_code("E_FEEDBACK_BINDING", self.record, fingerprint="f" * 64)
        self.assertFalse(self.log_path().exists())

    def test_append_is_canonical_idempotent_and_collision_closed(self) -> None:
        first = self.event()
        appended = append_feedback_event(self.workspace, first)
        before = self.log_path().read_bytes()
        duplicate = append_feedback_event(self.workspace, first)
        self.assertEqual(appended["status"], "appended")
        self.assertEqual(duplicate["status"], "ignored_duplicate")
        self.assertEqual(self.log_path().read_bytes(), before)
        self.assertEqual(before, canonical_json_bytes(first))
        with mock.patch(
            "skill.scripts.readme_showcase.contracts.feedback.feedback_event_id",
            return_value=first["event_id"],
        ):
            colliding = self.event("preview-rejected", 1)
            self.assert_code("E_FEEDBACK_COLLISION", append_feedback_event, self.workspace, colliding)
        self.assertEqual(self.log_path().read_bytes(), before)

    def test_concurrent_appenders_add_one_complete_line_each(self) -> None:
        count = 16
        barrier = threading.Barrier(count)
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                barrier.wait()
                results.append(append_feedback_event(self.workspace, self.event(second=index)))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), count)
        lines = self.log_path().read_bytes().splitlines(keepends=True)
        self.assertEqual(len(lines), count)
        payloads = [json.loads(line) for line in lines]
        self.assertEqual(len({item["event_id"] for item in payloads}), count)
        self.assertTrue(all(canonical_json_bytes(item) == line for item, line in zip(payloads, lines, strict=True)))

    def test_interrupted_append_restores_prior_bytes_and_new_empty_log(self) -> None:
        first = self.event()
        append_feedback_event(self.workspace, first)
        before = self.log_path().read_bytes()

        def short_write(descriptor: int, data: bytes) -> int:
            return os.write(descriptor, data[: len(data) // 2])

        self.assert_code(
            "E_FEEDBACK_INTERRUPTED", append_feedback_event, self.workspace, self.event(second=1), write=short_write,
        )
        self.assertEqual(self.log_path().read_bytes(), before)
        other = self.root / "other"
        target = self.root / "other-target"
        self.initialize_workspace(other, target)
        self.assert_code("E_FEEDBACK_INTERRUPTED", append_feedback_event, other, first, write=short_write)
        self.assertFalse((other / LOG_PATH).exists())

    def test_process_replacement_between_check_and_write_never_reports_success(self) -> None:
        append_feedback_event(self.workspace, self.event())
        log = self.log_path()
        moved = log.with_suffix(".moved")
        before = log.read_bytes()
        start_read, start_write = os.pipe()
        done_read, done_write = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                os.close(start_write)
                os.close(done_read)
                _ = os.read(start_read, 1)
                log.rename(moved)
                log.write_bytes(b"")
                _ = os.write(done_write, b"1")
            finally:
                os._exit(0)
        os.close(start_read)
        os.close(done_write)

        def replace_then_write(descriptor: int, data: bytes) -> int:
            _ = os.write(start_write, b"1")
            self.assertEqual(os.read(done_read, 1), b"1")
            return os.write(descriptor, data)

        try:
            self.assert_code(
                "E_FEEDBACK_PATH", append_feedback_event, self.workspace, self.event(second=1), write=replace_then_write
            )
        finally:
            os.close(start_write)
            os.close(done_read)
            _, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        self.assertEqual(log.read_bytes(), b"")
        self.assertEqual(moved.read_bytes(), before)

    def test_malformed_symlink_fifo_and_traversal_are_rejected_without_mutation(self) -> None:
        feedback = self.workspace / "feedback"
        feedback.mkdir()
        log = self.log_path()
        log.write_bytes(b'{"not":"canonical"}\n')
        before = log.read_bytes()
        self.assert_code("E_FEEDBACK_LOG", append_feedback_event, self.workspace, self.event())
        self.assertEqual(log.read_bytes(), before)
        log.unlink()
        outside = self.root / "outside"
        outside.write_bytes(b"sentinel\n")
        log.symlink_to(outside)
        self.assert_code("E_FEEDBACK_PATH", append_feedback_event, self.workspace, self.event())
        self.assertEqual(outside.read_bytes(), b"sentinel\n")
        log.unlink()
        os.mkfifo(log)
        self.assert_code("E_FEEDBACK_PATH", append_feedback_event, self.workspace, self.event())
        log.unlink()
        self.assert_code("E_FEEDBACK_PATH", append_feedback_event, self.workspace, self.event(),
                         relative_log="../events.jsonl")

    def test_real_cli_happy_duplicate_and_failure_surfaces(self) -> None:
        details = self.root / "details.json"
        write_canonical_json_atomic(details, self.details("pr-merged"))
        arguments = (
            "record-feedback", "--workspace", str(self.workspace), "--delivery-result", str(self.delivery_path),
            "--details", str(details), "--run-id", self.run_id, "--fingerprint", self.fingerprint,
            "--event", "pr-merged", "--recorded-at", "2026-08-04T12:00:00Z",
        )
        first, second = self.run_cli(arguments), self.run_cli(arguments)
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
        self.assertEqual(json.loads(first.stdout)["status"], "appended")
        self.assertEqual(json.loads(second.stdout)["status"], "ignored_duplicate")
        before = hashlib.sha256(self.log_path().read_bytes()).hexdigest()
        failed = self.run_cli((*arguments[:-8], "--run-id", "f" * 64, *arguments[-6:]))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("E_FEEDBACK_RUN", failed.stderr)
        self.assertEqual(hashlib.sha256(self.log_path().read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
