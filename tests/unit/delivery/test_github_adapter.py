from __future__ import annotations

import copy
import json
import threading
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_sha256
from skill.scripts.readme_showcase.delivery.github import (
    ACTIONS,
    build_delivery_plan,
    build_remote_state_v2,
    create_branch,
    execute_delivery,
    execute_delivery_result,
)
from skill.scripts.readme_showcase.contracts.publishing import (
    validate_delivery_result_v1,
    validate_remote_state_v2,
)


ROOT = Path(__file__).resolve().parents[3]


class GitHubAdapterTests(unittest.TestCase):
    def approval(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "decision": "approve",
            "repository": "owner/repo",
            "base_sha": "a" * 40,
            "proposed_branch": "readme-showcase/aaaaaaaaaaaa",
            "pr_fingerprint": "e" * 64,
            "candidate_hashes": [{"path": "README.md", "sha256": "b" * 64}],
            "evaluation_sha256": "d" * 64,
            "preview": {
                "path": "output/preview/index.html",
                "preview_sha256": "f" * 64,
                "report_path": "output/preview/report.json",
                "report_sha256": "1" * 64,
            },
            "actions": list(ACTIONS),
        }

    def plan(self) -> dict[str, object]:
        return build_delivery_plan(
            self.approval(),
            title="Improve README",
            body="Bound delivery body",
            commit_message="docs: improve README",
        )

    def observations(self, plan: dict[str, object]) -> dict[str, dict[str, object]]:
        operation = plan["operation_id"]
        branch = plan["branch"]
        return {
            "create-branch": {"branch": branch, "base_sha": plan["base_sha"], "operation_id": operation},
            "commit-files": {"commit_sha": "c" * 40, "candidate_sha256": plan["candidate_sha256"], "operation_id": operation},
            "push-branch": {"branch": branch, "commit_sha": "c" * 40, "operation_id": operation},
            "open-pull-request": {"branch": branch, "commit_sha": "c" * 40, "pr_url": "https://github.com/owner/repo/pull/7", "pr_number": 7, "operation_id": operation},
        }

    def test_plan_and_remote_state_are_closed_and_action_bound(self) -> None:
        plan = self.plan()
        self.assertEqual(plan["operation_id"], "e" * 64)
        self.assertEqual(plan["candidate_sha256"], canonical_sha256(self.approval()["candidate_hashes"]))
        state = build_remote_state_v2(plan)
        self.assertEqual([item["action"] for item in state["actions"]], list(ACTIONS))
        for item in state["actions"]:
            self.assertTrue(item["permission"])
            self.assertEqual(item["checked_repository"], plan["repository"])
            self.assertEqual(item["checked_base_sha"], plan["base_sha"])
            self.assertEqual(item["checked_branch"], plan["branch"])
            self.assertEqual(item["checked_candidate_sha256"], plan["candidate_sha256"])
            self.assertEqual(item["checked_evaluation_sha256"], plan["evaluation_sha256"])
            self.assertEqual(item["checked_approval_sha256"], plan["operation_id"])
            self.assertIsNone(item["observed"])

    def test_explicit_empty_permissions_fail_closed_without_changing_none_default(self) -> None:
        plan = self.plan()
        denied = build_remote_state_v2(plan, permissions={})
        self.assertEqual([item["permission"] for item in denied["actions"]], [False] * len(ACTIONS))

        default = build_remote_state_v2(plan, permissions=None)
        self.assertEqual([item["permission"] for item in default["actions"]], [True] * len(ACTIONS))

    def test_mock_execute_calls_exact_actions_and_returns_only_observations(self) -> None:
        plan = self.plan()
        state = build_remote_state_v2(plan)
        observations = self.observations(plan)
        calls: list[dict[str, object]] = []
        rechecks: list[str] = []

        def provider(action: str) -> dict[str, object]:
            rechecks.append(action)
            return copy.deepcopy(state)

        def execute(action: str, arguments: list[str], timeout: float) -> dict[str, object]:
            calls.append({"action": action, "arguments": arguments, "timeout": timeout})
            return {"exit_code": 0, "stdout": "success but not trusted", "observed": observations[action]}

        result = execute_delivery(plan, provider, execute)
        self.assertEqual(rechecks, list(ACTIONS))
        self.assertEqual([call["action"] for call in calls], list(ACTIONS))
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["branch"], plan["branch"])
        self.assertEqual(result["commit_sha"], "c" * 40)
        self.assertEqual(result["pr_url"], "https://github.com/owner/repo/pull/7")
        self.assertEqual(result["pr_number"], 7)
        self.assertEqual(result["attempts"], 4)
        self.assertIsNone(result["reason"])

    def test_matching_observations_are_idempotent_without_calls(self) -> None:
        plan = self.plan()
        state = build_remote_state_v2(plan)
        observations = self.observations(plan)
        for entry in state["actions"]:
            entry["observed"] = observations[entry["action"]]
        calls: list[str] = []
        result = execute_delivery(
            plan,
            lambda _action: copy.deepcopy(state),
            lambda action, _arguments, _timeout: calls.append(action),
        )
        self.assertEqual(calls, [])

        failed = execute_delivery_result(
            plan,
            lambda _action: {**copy.deepcopy(state), "operation_id": "0" * 64},
            lambda _action, _arguments, _timeout: self.fail("revoked operation executed"),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["reason"], "E_GITHUB_AUTHORITY")
        self.assertEqual(failed["attempts"], 0)
        self.assertIsNone(failed["commit_sha"])
        self.assertIsNone(failed["pr_url"])
        self.assertIsNone(failed["pr_number"])
        self.assertEqual(result["status"], "delivered")
        self.assertTrue(result["idempotent"])

    def test_drift_permission_conflict_and_fake_stdout_stop_before_next_call(self) -> None:
        for case in ("permission", "binding", "conflict", "fake-stdout"):
            with self.subTest(case=case):
                plan = self.plan()
                state = build_remote_state_v2(plan)
                observations = self.observations(plan)
                calls: list[str] = []

                if case == "permission":
                    state["actions"][0]["permission"] = False
                elif case == "binding":
                    state["actions"][1]["checked_evaluation_sha256"] = "0" * 64
                elif case == "conflict":
                    state["actions"][0]["observed"] = {
                        **observations["create-branch"],
                        "operation_id": "0" * 64,
                    }

                def execute(action: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
                    calls.append(action)
                    if case == "fake-stdout":
                        return {"exit_code": 0, "stdout": "created ccccc", "observed": None}
                    return {"exit_code": 0, "observed": observations[action]}

                with self.assertRaises(ContractError):
                    execute_delivery(plan, lambda _action: copy.deepcopy(state), execute)
                expected = [] if case in {"permission", "conflict"} else ["create-branch"]
                if case == "fake-stdout":
                    expected = ["create-branch"]
                self.assertEqual(calls, expected)

    def test_timeout_is_bounded_and_partial_failure_has_no_next_call(self) -> None:
        plan = self.plan()
        state = build_remote_state_v2(plan)
        attempts: list[str] = []

        def execute(action: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
            attempts.append(action)
            raise TimeoutError("mock timeout")

        with self.assertRaises(ContractError) as raised:
            execute_delivery(plan, lambda _action: copy.deepcopy(state), execute, max_attempts=2)
        self.assertEqual(raised.exception.code, "E_GITHUB_TIMEOUT")
        self.assertEqual(attempts, ["create-branch", "create-branch"])

        attempts.clear()
        observations = self.observations(plan)

        def retry_then_succeed(action: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
            attempts.append(action)
            if attempts == ["create-branch"]:
                raise TimeoutError("first attempt")
            return {"exit_code": 0, "observed": observations[action]}

        result = execute_delivery(plan, lambda _action: copy.deepcopy(state), retry_then_succeed, max_attempts=2)
        self.assertEqual(attempts, ["create-branch", "create-branch", "commit-files", "push-branch", "open-pull-request"])
        self.assertEqual(result["attempts"], 5)

    def test_every_pre_action_binding_and_permission_drift_revokes_before_that_call(self) -> None:
        for index, action in enumerate(ACTIONS):
            for kind in ("binding", "permission"):
                with self.subTest(action=action, kind=kind):
                    plan = self.plan()
                    state = build_remote_state_v2(plan)
                    if kind == "binding":
                        state["actions"][index]["checked_candidate_sha256"] = "0" * 64
                    else:
                        state["actions"][index]["permission"] = False
                    observations = self.observations(plan)
                    calls: list[str] = []

                    def execute(name: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
                        calls.append(name)
                        return {"exit_code": 0, "observed": observations[name]}

                    with self.assertRaises(ContractError):
                        execute_delivery(plan, lambda _name: copy.deepcopy(state), execute)
                    self.assertEqual(calls, list(ACTIONS[:index]))

    def test_partial_failure_interruption_and_conflicting_prior_result_do_not_advance(self) -> None:
        plan = self.plan()
        state = build_remote_state_v2(plan)
        calls: list[str] = []

        def partial(action: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
            calls.append(action)
            return {"exit_code": 1, "stdout": "looks successful", "observed": None}

        with self.assertRaises(ContractError) as partial_error:
            execute_delivery(plan, lambda _name: copy.deepcopy(state), partial)
        self.assertEqual(partial_error.exception.code, "E_GITHUB_EXECUTE")
        self.assertEqual(calls, ["create-branch", "create-branch"])

        calls.clear()
        with self.assertRaises(ContractError) as interrupted:
            execute_delivery(
                plan,
                lambda _name: copy.deepcopy(state),
                lambda action, _arguments, _timeout: calls.append(action) or (_ for _ in ()).throw(InterruptedError()),
            )
        self.assertEqual(interrupted.exception.code, "E_GITHUB_INTERRUPTED")
        self.assertEqual(calls, ["create-branch"])

        conflict = build_remote_state_v2(plan)
        conflict["actions"][0]["observed"] = {
            **self.observations(plan)["create-branch"],
            "base_sha": "0" * 40,
        }
        calls.clear()
        with self.assertRaises(ContractError) as conflict_error:
            execute_delivery(
                plan,
                lambda _name: copy.deepcopy(conflict),
                lambda action, _arguments, _timeout: calls.append(action),
            )
        self.assertEqual(conflict_error.exception.code, "E_GITHUB_CONFLICT")
        self.assertEqual(calls, [])

    def test_concurrent_same_operation_is_rejected_without_second_call(self) -> None:
        plan = self.plan()
        state = build_remote_state_v2(plan)
        observations = self.observations(plan)
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        worker_errors: list[BaseException] = []

        def blocking(action: str, _arguments: list[str], _timeout: float) -> dict[str, object]:
            calls.append(action)
            if action == "create-branch":
                entered.set()
                self.assertTrue(release.wait(5))
            return {"exit_code": 0, "observed": observations[action]}

        def worker() -> None:
            try:
                execute_delivery(plan, lambda _name: copy.deepcopy(state), blocking)
            except BaseException as exc:  # captured for assertion on the test thread
                worker_errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(5))
        with self.assertRaises(ContractError) as raised:
            execute_delivery(plan, lambda _name: copy.deepcopy(state), blocking)
        self.assertEqual(raised.exception.code, "E_GITHUB_CONCURRENT")
        self.assertEqual(calls, ["create-branch"])
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(calls, list(ACTIONS))

    def test_concrete_create_branch_arguments_are_not_generic_transport(self) -> None:
        plan = self.plan()
        seen: list[list[str]] = []
        observed = self.observations(plan)["create-branch"]
        result = create_branch(
            plan,
            lambda _action, arguments, _timeout: seen.append(arguments) or {"exit_code": 0, "observed": observed},
        )
        self.assertEqual(seen, [["api", "--method", "POST", f"repos/{plan['repository']}/git/refs", "-f", f"ref=refs/heads/{plan['branch']}", "-f", f"sha={plan['base_sha']}"]])
        self.assertEqual(result, observed)

    def test_owned_schema_fixtures_match_python_validation(self) -> None:
        pairs = (
            ("remote-state.v2.schema.json", "remote-state-v2.valid.json", validate_remote_state_v2),
            ("delivery-result.v1.schema.json", "delivery-result-v1.valid.json", validate_delivery_result_v1),
        )
        for schema_name, fixture_name, validator in pairs:
            with self.subTest(schema=schema_name):
                schema = json.loads((ROOT / "skill/schemas" / schema_name).read_text(encoding="utf-8"))
                payload = json.loads((ROOT / "tests/fixtures/contracts" / fixture_name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                self.assertEqual(list(Draft202012Validator(schema).iter_errors(payload)), [])
                self.assertEqual(validator(payload), payload)


if __name__ == "__main__":
    unittest.main()
