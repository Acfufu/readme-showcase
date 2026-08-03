from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from ...pipeline_contracts import ContractError, canonical_sha256
from ..contracts.publishing import (
    ALLOWED_ACTIONS,
    validate_approval_envelope_v2,
    validate_remote_state_v2,
)


ACTIONS = tuple(ALLOWED_ACTIONS)
AUTHORITY_CODE = "E_GITHUB_AUTHORITY"
LIVE_DISABLED_CODE = "E_GITHUB_LIVE_DISABLED"
PLAN_CODE = "E_GITHUB_PLAN"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_ATTEMPTS = 2
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_PULL_REQUEST_URL = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/([1-9][0-9]*)\Z")
_ACTIVE_OPERATIONS: set[str] = set()
_ACTIVE_LOCK = threading.Lock()

Execute = Callable[[str, list[str], float], dict[str, object]]
StateProvider = Callable[[str], dict[str, object]]


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _bounded_text(value: Any, context: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > maximum:
        _fail("E_GITHUB_PLAN", f"{context} must be bounded non-empty text")
    return value


def build_delivery_plan(
    approval_payload: Any,
    *,
    title: str,
    body: str,
    commit_message: str,
) -> dict[str, object]:
    approval = validate_approval_envelope_v2(approval_payload)
    if approval["decision"] != "approve":
        _fail("E_APPROVAL_DECISION", "delivery requires an explicit approve decision")
    candidates = [dict(item) for item in approval["candidate_hashes"]]
    return {
        "transport": "gh",
        "operation_id": approval["pr_fingerprint"],
        "repository": approval["repository"],
        "base_sha": approval["base_sha"],
        "branch": approval["proposed_branch"],
        "candidate_tree": candidates,
        "candidate_sha256": canonical_sha256(candidates),
        "evaluation_sha256": approval["evaluation_sha256"],
        "preview": dict(approval["preview"]),
        "action_order": list(ACTIONS),
        "title": _bounded_text(title, "pull request title", 240),
        "body": _bounded_text(body, "pull request body", 1200),
        "commit_message": _bounded_text(commit_message, "commit message", 240),
    }


def build_remote_state_v2(
    plan: dict[str, object],
    *,
    permissions: dict[str, bool] | None = None,
) -> dict[str, object]:
    allowed = {action: True for action in ACTIONS} if permissions is None else permissions
    state = {
        "schema_version": 2,
        "operation_id": plan["operation_id"],
        "actions": [
            {
                "action": action,
                "permission": allowed.get(action, False),
                "checked_repository": plan["repository"],
                "checked_base_sha": plan["base_sha"],
                "checked_branch": plan["branch"],
                "checked_candidate_sha256": plan["candidate_sha256"],
                "checked_evaluation_sha256": plan["evaluation_sha256"],
                "checked_approval_sha256": plan["operation_id"],
                "observed": None,
            }
            for action in ACTIONS
        ],
    }
    return validate_remote_state_v2(state)


def _arguments(plan: dict[str, object], action: str, commit_sha: str | None = None) -> list[str]:
    repository = plan["repository"]
    branch = plan["branch"]
    if action == "create-branch":
        return [
            "api", "--method", "POST", f"repos/{repository}/git/refs",
            "-f", f"ref=refs/heads/{branch}", "-f", f"sha={plan['base_sha']}",
        ]
    if action == "commit-files":
        return [
            "api", "--method", "POST", f"repos/{repository}/git/commits",
            "-f", f"message={plan['commit_message']}",
            "-f", f"candidate_sha256={plan['candidate_sha256']}",
            "-f", f"parent={plan['base_sha']}",
        ]
    if action == "push-branch":
        if commit_sha is None:
            _fail("E_GITHUB_OBSERVATION", "push requires an observed commit SHA")
        return [
            "api", "--method", "PATCH", f"repos/{repository}/git/refs/heads/{branch}",
            "-f", f"sha={commit_sha}", "-F", "force=false",
        ]
    if action == "open-pull-request":
        return [
            "pr", "create", "--repo", str(repository), "--base", str(plan["base_sha"]),
            "--head", str(branch), "--title", str(plan["title"]), "--body", str(plan["body"]),
        ]
    _fail("E_GITHUB_ACTION", "unsupported GitHub delivery action")


def _validate_observation(
    plan: dict[str, object],
    action: str,
    observed: Any,
    *,
    commit_sha: str | None,
) -> dict[str, object]:
    if not isinstance(observed, dict):
        _fail("E_GITHUB_OBSERVATION", f"{action} did not supply a structured observation")
    common = {"operation_id"}
    fields = {
        "create-branch": common | {"branch", "base_sha"},
        "commit-files": common | {"commit_sha", "candidate_sha256"},
        "push-branch": common | {"branch", "commit_sha"},
        "open-pull-request": common | {"branch", "commit_sha", "pr_url", "pr_number"},
    }[action]
    if set(observed) != fields:
        _fail("E_GITHUB_OBSERVATION", f"{action} observation fields are not closed")
    if observed["operation_id"] != plan["operation_id"]:
        _fail("E_GITHUB_CONFLICT", f"{action} observation belongs to another operation")
    if action == "create-branch":
        if observed["branch"] != plan["branch"] or observed["base_sha"] != plan["base_sha"]:
            _fail("E_GITHUB_CONFLICT", "same-name branch has mismatched bindings")
    elif action == "commit-files":
        if observed["candidate_sha256"] != plan["candidate_sha256"]:
            _fail("E_GITHUB_CONFLICT", "commit belongs to different candidate bytes")
        if not isinstance(observed["commit_sha"], str) or not _COMMIT.fullmatch(observed["commit_sha"]):
            _fail("E_GITHUB_OBSERVATION", "commit observation lacks an immutable SHA")
    elif action == "push-branch":
        if observed["branch"] != plan["branch"] or observed["commit_sha"] != commit_sha:
            _fail("E_GITHUB_CONFLICT", "pushed branch differs from observed commit")
    else:
        match = _PULL_REQUEST_URL.fullmatch(str(observed["pr_url"]))
        if (
            observed["branch"] != plan["branch"]
            or observed["commit_sha"] != commit_sha
            or type(observed["pr_number"]) is not int
            or match is None
            or f"{match.group(1)}/{match.group(2)}" != plan["repository"]
            or int(match.group(3)) != observed["pr_number"]
        ):
            _fail("E_GITHUB_CONFLICT", "pull request observation differs from bound delivery")
    return dict(observed)


def _invoke(
    plan: dict[str, object],
    action: str,
    execute: Execute,
    *,
    commit_sha: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    try:
        receipt = execute(action, _arguments(plan, action, commit_sha), timeout)
    except TimeoutError as exc:
        raise ContractError("E_GITHUB_TIMEOUT", f"{action} timed out") from exc
    except (InterruptedError, KeyboardInterrupt) as exc:
        raise ContractError("E_GITHUB_INTERRUPTED", f"{action} was interrupted") from exc
    if not isinstance(receipt, dict) or receipt.get("exit_code") != 0:
        _fail("E_GITHUB_EXECUTE", f"{action} failed without an observed result")
    return _validate_observation(plan, action, receipt.get("observed"), commit_sha=commit_sha)


def create_branch(plan: dict[str, object], execute: Execute, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    return _invoke(plan, "create-branch", execute, timeout=timeout)


def commit_files(plan: dict[str, object], execute: Execute, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    return _invoke(plan, "commit-files", execute, timeout=timeout)


def push_branch(plan: dict[str, object], commit_sha: str, execute: Execute, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    return _invoke(plan, "push-branch", execute, commit_sha=commit_sha, timeout=timeout)


def open_pull_request(plan: dict[str, object], commit_sha: str, execute: Execute, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    return _invoke(plan, "open-pull-request", execute, commit_sha=commit_sha, timeout=timeout)


def _state_entry(plan: dict[str, object], state_payload: Any, action: str) -> dict[str, object]:
    try:
        state = validate_remote_state_v2(state_payload)
    except ContractError as exc:
        raise ContractError(AUTHORITY_CODE, f"{action} remote state is not authoritative") from exc
    if state["operation_id"] != plan["operation_id"]:
        _fail("E_GITHUB_AUTHORITY", "remote state operation ID drifted")
    entries = {entry["action"]: entry for entry in state["actions"]}
    entry = entries[action]
    expected = {
        "checked_repository": plan["repository"],
        "checked_base_sha": plan["base_sha"],
        "checked_branch": plan["branch"],
        "checked_candidate_sha256": plan["candidate_sha256"],
        "checked_evaluation_sha256": plan["evaluation_sha256"],
        "checked_approval_sha256": plan["operation_id"],
    }
    if any(entry[field] != value for field, value in expected.items()):
        _fail("E_GITHUB_AUTHORITY", f"{action} binding drift revoked authority")
    if not entry["permission"]:
        _fail("E_GITHUB_PERMISSION", f"{action} permission denied")
    return entry


def execute_delivery(
    plan: dict[str, object],
    state_provider: StateProvider,
    execute: Execute,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
        _fail("E_GITHUB_RETRY", "retry count exceeds fixed policy")
    operation = str(plan["operation_id"])
    with _ACTIVE_LOCK:
        if operation in _ACTIVE_OPERATIONS:
            _fail("E_GITHUB_CONCURRENT", "delivery operation is already active")
        _ACTIVE_OPERATIONS.add(operation)
    observed: dict[str, dict[str, object]] = {}
    executed = 0
    commit_sha: str | None = None
    try:
        for action in ACTIONS:
            entry = _state_entry(plan, state_provider(action), action)
            if entry["observed"] is not None:
                item = _validate_observation(plan, action, entry["observed"], commit_sha=commit_sha)
            else:
                last_error: ContractError | None = None
                for _attempt in range(max_attempts):
                    try:
                        executed += 1
                        item = _invoke(plan, action, execute, commit_sha=commit_sha, timeout=timeout)
                        break
                    except ContractError as exc:
                        last_error = exc
                        if exc.code not in {"E_GITHUB_TIMEOUT", "E_GITHUB_EXECUTE"}:
                            raise
                else:
                    assert last_error is not None
                    raise last_error
            observed[action] = item
            if action == "commit-files":
                commit_sha = str(item["commit_sha"])
        pull = observed["open-pull-request"]
        return {
            "schema_version": 1,
            "status": "delivered",
            "operation_id": operation,
            "branch": plan["branch"],
            "commit_sha": commit_sha,
            "pr_url": pull["pr_url"],
            "pr_number": pull["pr_number"],
            "reason": None,
            "attempts": executed,
            "idempotent": executed == 0,
        }
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_OPERATIONS.discard(operation)


def execute_delivery_result(
    plan: dict[str, object],
    state_provider: StateProvider,
    execute: Execute,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    attempts = 0

    def counted(action: str, arguments: list[str], action_timeout: float) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        return execute(action, arguments, action_timeout)

    try:
        return execute_delivery(
            plan,
            state_provider,
            counted,
            max_attempts=max_attempts,
            timeout=timeout,
        )
    except ContractError as exc:
        return {
            "schema_version": 1,
            "status": "failed",
            "operation_id": plan["operation_id"],
            "branch": plan["branch"],
            "commit_sha": None,
            "pr_url": None,
            "pr_number": None,
            "reason": exc.code,
            "attempts": attempts,
            "idempotent": False,
        }


def dry_run_result(plan: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "dry-run",
        "transport": "gh",
        "operation_id": plan["operation_id"],
        "repository": plan["repository"],
        "base_sha": plan["base_sha"],
        "branch": plan["branch"],
        "candidate_tree": plan["candidate_tree"],
        "candidate_sha256": plan["candidate_sha256"],
        "title": plan["title"],
        "body": plan["body"],
        "action_order": list(ACTIONS),
        "observed_branch": None,
        "commit_sha": None,
        "pr_url": None,
        "pr_number": None,
    }


__all__ = [
    "ACTIONS",
    "AUTHORITY_CODE",
    "LIVE_DISABLED_CODE",
    "PLAN_CODE",
    "build_delivery_plan",
    "build_remote_state_v2",
    "commit_files",
    "create_branch",
    "dry_run_result",
    "execute_delivery",
    "execute_delivery_result",
    "open_pull_request",
    "push_branch",
]
