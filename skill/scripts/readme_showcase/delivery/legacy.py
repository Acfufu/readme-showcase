from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from ..evaluation import legacy as _EVALUATION_LEGACY
from ..validation import legacy as _BUNDLE

ContractError = _BUNDLE.ContractError
canonical_sha256 = _BUNDLE.canonical_sha256
read_regular_bytes = _BUNDLE.read_regular_bytes
validate_contract = _BUNDLE.validate_contract
MAX_ARTIFACT_BYTES = _BUNDLE.MAX_ARTIFACT_BYTES
MAX_GIT_OUTPUT_BYTES = _BUNDLE.MAX_GIT_OUTPUT_BYTES
_COMMIT = _BUNDLE._COMMIT
_SHA256 = _BUNDLE._SHA256
_BUNDLE_FIELDS = _BUNDLE._BUNDLE_FIELDS
_TARGET_FIELDS = _BUNDLE._TARGET_FIELDS
_EVALUATION = _BUNDLE._EVALUATION
_GITHUB_REPOSITORY = _BUNDLE._GITHUB_REPOSITORY
_PR_BUNDLE_FIELDS = _BUNDLE._PR_BUNDLE_FIELDS
_PR_CANDIDATE_FIELDS = _BUNDLE._PR_CANDIDATE_FIELDS
_PR_EVALUATION_FIELDS = _BUNDLE._PR_EVALUATION_FIELDS
_PR_EXCLUDED_NAMES = _BUNDLE._PR_EXCLUDED_NAMES
_PR_EXCLUDED_PARTS = _BUNDLE._PR_EXCLUDED_PARTS
_PR_EXCLUSIONS = _BUNDLE._PR_EXCLUSIONS
_PR_METADATA_FIELDS = _BUNDLE._PR_METADATA_FIELDS
_PR_TARGET_FIELDS = _BUNDLE._PR_TARGET_FIELDS
_REMOTE_PERMISSION_FIELDS = _BUNDLE._REMOTE_PERMISSION_FIELDS
_REMOTE_STATE_FIELDS = _BUNDLE._REMOTE_STATE_FIELDS
_APPROVAL_CANDIDATE_FIELDS = _BUNDLE._APPROVAL_CANDIDATE_FIELDS
_APPROVAL_FIELDS = _BUNDLE._APPROVAL_FIELDS
_fail = _BUNDLE._fail
_object = _BUNDLE._object
_text = _BUNDLE._text
_relative_path = _BUNDLE._relative_path
_reference = _BUNDLE._reference
_artifact_bytes = _BUNDLE._artifact_bytes
_artifact_json = _BUNDLE._artifact_json
_validate_evidence_checkout = _BUNDLE._validate_evidence_checkout
validate_generated_bundle = _BUNDLE.validate_generated_bundle
_validate_evaluation_report = _EVALUATION_LEGACY._validate_evaluation_report

def _git_output(root: Path, *arguments: str) -> bytes:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["LC_ALL"] = "C"
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(
                [
                    "git",
                    "--no-optional-locks",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "diff.external=",
                    "-C",
                    str(root),
                    *arguments,
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=10,
                env=environment,
            )
            output_size = os.fstat(stdout.fileno()).st_size
            error_size = os.fstat(stderr.fileno()).st_size
            if (
                output_size > MAX_GIT_OUTPUT_BYTES
                or error_size > MAX_GIT_OUTPUT_BYTES
            ):
                _fail("E_PR_GIT", "local Git inspection output exceeds limit")
            stdout.seek(0)
            output = stdout.read()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ContractError("E_PR_GIT", "local Git inspection is unavailable") from exc
    if result.returncode != 0:
        _fail("E_PR_GIT", f"local Git inspection failed: {arguments[0]}")
    return output


def _github_repository_from_origin(value: str) -> str:
    patterns = (
        r"https://github\.com/(?P<repo>[^?#]+?)(?:\.git)?\Z",
        r"git@github\.com:(?P<repo>.+?)(?:\.git)?\Z",
        r"ssh://git@github\.com/(?P<repo>.+?)(?:\.git)?\Z",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value)
        if match and _GITHUB_REPOSITORY.fullmatch(match.group("repo")):
            return match.group("repo")
    _fail("E_PR_TARGET", "origin must identify the exact GitHub repository")


def _publish_path(value: str, kind: str) -> PurePosixPath:
    path = _relative_path(value, f"PR {kind} path")
    if (
        any(part in _PR_EXCLUDED_PARTS for part in path.parts)
        or path.name in _PR_EXCLUDED_NAMES
    ):
        _fail("E_PR_PATH", f"excluded path cannot enter PR bundle: {value}")
    if kind == "readme" and path.name not in {"README.md", "README_zh.md"}:
        _fail("E_PR_PATH", "README candidate must target README.md or README_zh.md")
    if kind in {"asset", "semantic"} and path.parts[:2] != ("assets", "readme"):
        _fail("E_PR_PATH", f"{kind} candidate must stay under assets/readme")
    if kind == "semantic" and not path.name.endswith(".diagram.json"):
        _fail("E_PR_PATH", "ELK semantic source must use .diagram.json")
    return path


def _target_file_sha256(root: Path, path: PurePosixPath) -> str | None:
    current = root
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            _fail("E_PR_PATH", f"target path crosses symlink: {path.as_posix()}")
        if index < len(path.parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                _fail("E_PR_PATH", f"target parent is not a directory: {path.as_posix()}")
        elif not stat.S_ISREG(info.st_mode):
            _fail("E_PR_PATH", f"target candidate is not a regular file: {path.as_posix()}")
    return hashlib.sha256(
        read_regular_bytes(
            current,
            maximum=MAX_ARTIFACT_BYTES,
            path_code="E_PR_PATH",
            size_code="E_PR_PATH",
        )
    ).hexdigest()


def _candidate_change(
    *,
    artifact_root: Path,
    target_root: Path,
    reference: dict[str, str],
    kind: str,
) -> dict[str, object]:
    path = _publish_path(reference["path"], kind)
    _artifact_bytes(artifact_root, reference, f"PR {kind} candidate")
    before_sha256 = _target_file_sha256(target_root, path)
    after_sha256 = reference["sha256"]
    change = (
        "add"
        if before_sha256 is None
        else "unchanged"
        if before_sha256 == after_sha256
        else "modify"
    )
    return {
        "path": path.as_posix(),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "change": change,
    }


def build_pr_bundle(
    payload: Any,
    evaluation: Any,
    artifact_root: Path,
    target_root: Path,
) -> dict[str, object]:
    artifact_root = artifact_root.resolve(strict=True)
    try:
        target_info = target_root.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_PR_TARGET", "target repository is missing") from exc
    if stat.S_ISLNK(target_info.st_mode) or not stat.S_ISDIR(target_info.st_mode):
        _fail("E_PR_TARGET", "target repository must be a real directory")
    target_root = target_root.resolve(strict=True)
    try:
        artifact_root.relative_to(target_root)
    except ValueError:
        pass
    else:
        _fail("E_PR_PATH", "pipeline run directory must stay outside target repository")

    if isinstance(payload, dict) and payload.get("schema_version") == 2:
        bundle = _object(payload, _BUNDLE_FIELDS, "generated README bundle")
    else:
        bundle = validate_contract(
            payload,
            required=_BUNDLE_FIELDS,
            optional=set(),
            context="generated README bundle",
        )
    target = _object(bundle["target"], _TARGET_FIELDS, "bundle target")
    repository = _text(target["repository"], "bundle target.repository")
    base_sha = target["base_sha"]
    if not isinstance(base_sha, str) or not _COMMIT.fullmatch(base_sha):
        _fail("E_BUNDLE_TARGET", "bundle target.base_sha must be immutable")
    if not _GITHUB_REPOSITORY.fullmatch(repository):
        _fail("E_PR_TARGET", "bundle target.repository must be owner/name")

    head = _git_output(target_root, "rev-parse", "--verify", "HEAD").decode(
        "ascii",
        errors="strict",
    ).strip()
    if head != base_sha:
        _fail("E_PR_BASE", "target HEAD differs from bundle base SHA")
    origin = _git_output(target_root, "remote", "get-url", "origin").decode(
        "utf-8",
        errors="strict",
    ).strip()
    if _github_repository_from_origin(origin) != repository:
        _fail("E_PR_TARGET", "target origin differs from bundle repository")
    cached_before = _git_output(
        target_root,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
    )
    worktree = _git_output(
        target_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if worktree:
        _fail("E_PR_WORKTREE", "target worktree and index must be clean")

    validation = validate_generated_bundle(bundle, artifact_root)
    _validate_evidence_checkout(
        artifact_root,
        target_root,
        cast(str, validation["evidence_sha256"]),
        bundle=bundle,
    )
    bundle_sha256 = canonical_sha256(bundle)
    _validate_evaluation_report(
        evaluation,
        bundle_sha256=bundle_sha256,
        bundle_schema_version=cast(int, bundle["schema_version"]),
        expected_advisory=(
            _EVALUATION.evaluate_v2_advisory(bundle, artifact_root)
            if bundle["schema_version"] == 2
            else None
        ),
    )
    candidate = cast(dict[str, Any], bundle["candidate"])
    references: list[tuple[dict[str, str], str]] = []
    if candidate["readme"] is not None:
        references.append(
            (_reference(candidate["readme"], "bundle candidate.readme"), "readme")
        )
    references.extend(
        (
            _reference(value, f"bundle candidate.assets[{index}]"),
            "asset",
        )
        for index, value in enumerate(cast(list[Any], candidate["assets"]))
    )
    candidate_files = sorted(
        (
            _candidate_change(
                artifact_root=artifact_root,
                target_root=target_root,
                reference=reference,
                kind=kind,
            )
            for reference, kind in references
        ),
        key=lambda item: cast(str, item["path"]),
    )

    artifacts = cast(dict[str, Any], bundle["artifacts"])
    asset_manifest, _ = _artifact_json(
        artifact_root,
        artifacts["asset_manifest"],
        "bundle artifacts.asset_manifest",
    )
    semantic_references = [
        _reference(asset["semantic"], f"asset manifest.assets[{index}].semantic")
        for index, asset in enumerate(cast(list[dict[str, Any]], asset_manifest["assets"]))
        if asset.get("engine_kind") == "elk"
    ]
    semantic_sources = sorted(
        (
            _candidate_change(
                artifact_root=artifact_root,
                target_root=target_root,
                reference=reference,
                kind="semantic",
            )
            for reference in semantic_references
        ),
        key=lambda item: cast(str, item["path"]),
    )
    paths = [
        cast(str, item["path"])
        for item in [*candidate_files, *semantic_sources]
    ]
    if len(paths) != len(set(paths)):
        _fail("E_PR_PATH", "PR candidate paths must be unique")
    if not any(
        item["change"] != "unchanged"
        for item in [*candidate_files, *semantic_sources]
    ):
        _fail("E_PR_NO_CHANGES", "candidate bytes equal target base")
    if _git_output(
        target_root,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
    ) != cached_before:
        _fail("E_PR_INDEX", "cached diff changed during PR bundle inspection")

    mode = cast(str, bundle["mode"])
    metadata = {
        "commit_message": (
            "docs(readme): refresh project showcase"
            if mode == "readme"
            else "docs(readme): refresh showcase assets"
        ),
        "pull_request_title": (
            "docs: refresh README showcase"
            if mode == "readme"
            else "docs: refresh README showcase assets"
        ),
        "pull_request_body": (
            "## Summary\n\n"
            "- Refresh evidence-bound README showcase artifacts\n\n"
            "## Verification\n\n"
            "- Deterministic hard gates: pass\n"
        ),
    }
    projection: dict[str, object] = {
        "schema_version": 1,
        "mode": mode,
        "target": {
            "repository": repository,
            "base_sha": base_sha,
            "branch": f"readme-showcase/{bundle_sha256[:12]}",
        },
        "candidate_files": candidate_files,
        "semantic_sources": semantic_sources,
        "evaluation": {
            "status": "pass",
            "bundle_sha256": bundle_sha256,
            "report_sha256": canonical_sha256(evaluation),
        },
        "metadata": metadata,
        "exclusions": _PR_EXCLUSIONS,
    }
    return {
        **projection,
        "status": "ready",
        "fingerprint": canonical_sha256(projection),
    }


def _branch(value: Any, context: str) -> str:
    branch = _text(value, context, limit=128)
    if (
        branch.startswith(("-", "/", "."))
        or branch.endswith(("/", ".", ".lock"))
        or any(marker in branch for marker in ("..", "//", "@{", "\\", " "))
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)
    ):
        _fail("E_PUBLISH_BRANCH", f"{context} is not a safe Git branch")
    return branch


def _sha256(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail("E_PUBLISH_HASH", f"{context} must be a lowercase SHA-256")
    return value


def _validate_pr_candidate_list(
    value: Any,
    context: str,
    *,
    semantic: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("E_SCHEMA_TYPE", f"{context} must be a list")
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        candidate = _object(
            item,
            _PR_CANDIDATE_FIELDS,
            f"{context}[{index}]",
        )
        raw_path = candidate["path"]
        normalized = _relative_path(raw_path, f"{context}[{index}].path")
        kind = (
            "semantic"
            if semantic
            else "readme"
            if normalized.name in {"README.md", "README_zh.md"}
            else "asset"
        )
        path = _publish_path(raw_path, kind).as_posix()
        before = candidate["before_sha256"]
        if before is not None:
            _sha256(before, f"{context}[{index}].before_sha256")
        after = _sha256(
            candidate["after_sha256"],
            f"{context}[{index}].after_sha256",
        )
        if candidate["change"] not in {"add", "modify", "unchanged"}:
            _fail("E_PR_BUNDLE", f"{context}[{index}].change is unsupported")
        if (
            (candidate["change"] == "add" and before is not None)
            or (
                candidate["change"] == "unchanged"
                and before != after
            )
            or (
                candidate["change"] == "modify"
                and (before is None or before == after)
            )
        ):
            _fail("E_PR_BUNDLE", f"{context}[{index}] change/hash relation is invalid")
        candidates.append(candidate)
    paths = [cast(str, item["path"]) for item in candidates]
    if paths != sorted(set(paths)):
        _fail("E_PR_BUNDLE", f"{context} must be unique and path-sorted")
    return candidates


def _validate_pr_bundle(payload: Any) -> dict[str, Any]:
    pr = validate_contract(
        payload,
        required=_PR_BUNDLE_FIELDS,
        optional=set(),
        context="PR bundle",
    )
    if pr["status"] != "ready" or pr["mode"] not in {"readme", "asset-only"}:
        _fail("E_PR_BUNDLE", "PR bundle must be ready in a writable mode")
    target = _object(pr["target"], _PR_TARGET_FIELDS, "PR bundle.target")
    repository = _text(target["repository"], "PR bundle.target.repository")
    if not _GITHUB_REPOSITORY.fullmatch(repository):
        _fail("E_PR_TARGET", "PR bundle repository must be owner/name")
    if not isinstance(target["base_sha"], str) or not _COMMIT.fullmatch(
        target["base_sha"]
    ):
        _fail("E_PR_BASE", "PR bundle base SHA must be immutable")
    _branch(target["branch"], "PR bundle.target.branch")

    candidate_files = _validate_pr_candidate_list(
        pr["candidate_files"],
        "PR bundle.candidate_files",
        semantic=False,
    )
    semantic_sources = _validate_pr_candidate_list(
        pr["semantic_sources"],
        "PR bundle.semantic_sources",
        semantic=True,
    )
    combined_paths = [
        cast(str, item["path"])
        for item in [*candidate_files, *semantic_sources]
    ]
    if len(combined_paths) != len(set(combined_paths)):
        _fail("E_PR_BUNDLE", "PR candidate and semantic paths overlap")
    if not any(
        item["change"] != "unchanged"
        for item in [*candidate_files, *semantic_sources]
    ):
        _fail("E_PR_NO_CHANGES", "PR bundle contains no changed candidate")

    evaluation = _object(
        pr["evaluation"],
        _PR_EVALUATION_FIELDS,
        "PR bundle.evaluation",
    )
    if evaluation["status"] != "pass":
        _fail("E_PR_EVALUATION", "PR bundle evaluation must pass")
    _sha256(evaluation["bundle_sha256"], "PR bundle.evaluation.bundle_sha256")
    _sha256(evaluation["report_sha256"], "PR bundle.evaluation.report_sha256")
    metadata = _object(
        pr["metadata"],
        _PR_METADATA_FIELDS,
        "PR bundle.metadata",
    )
    for field in ("commit_message", "pull_request_title"):
        _text(metadata[field], f"PR bundle.metadata.{field}", limit=240)
    body = metadata["pull_request_body"]
    if not isinstance(body, str) or not body.strip() or len(body) > 1200 or "\0" in body:
        _fail("E_PR_BUNDLE", "PR bundle pull_request_body must be bounded text")
    if pr["exclusions"] != _PR_EXCLUSIONS:
        _fail("E_PR_BUNDLE", "PR bundle exclusions differ from fixed policy")
    fingerprint = _sha256(pr["fingerprint"], "PR bundle.fingerprint")
    projection = {
        key: value
        for key, value in pr.items()
        if key not in {"fingerprint", "status"}
    }
    if canonical_sha256(projection) != fingerprint:
        _fail("E_PR_FINGERPRINT", "PR bundle fingerprint does not match contents")
    return pr


def _approval_candidate_hashes(pr: dict[str, Any]) -> list[dict[str, str]]:
    candidates = [
        {
            "path": cast(str, item["path"]),
            "sha256": cast(str, item["after_sha256"]),
        }
        for item in [
            *cast(list[dict[str, Any]], pr["candidate_files"]),
            *cast(list[dict[str, Any]], pr["semantic_sources"]),
        ]
    ]
    return sorted(candidates, key=lambda item: item["path"])


def _validate_remote_state(payload: Any) -> dict[str, Any]:
    remote = validate_contract(
        payload,
        required=_REMOTE_STATE_FIELDS,
        optional=set(),
        context="remote state",
    )
    repository = _text(remote["repository"], "remote state.repository")
    if not _GITHUB_REPOSITORY.fullmatch(repository):
        _fail("E_REMOTE_TARGET", "remote repository must be owner/name")
    if not isinstance(remote["base_sha"], str) or not _COMMIT.fullmatch(
        remote["base_sha"]
    ):
        _fail("E_REMOTE_BASE", "remote base SHA must be immutable")
    _branch(remote["default_branch"], "remote state.default_branch")
    _branch(remote["proposed_branch"], "remote state.proposed_branch")
    if type(remote["branch_exists"]) is not bool:
        _fail("E_REMOTE_BRANCH", "remote branch_exists must be boolean")
    branch_head = remote["branch_head_sha"]
    if remote["branch_exists"]:
        if not isinstance(branch_head, str) or not _COMMIT.fullmatch(branch_head):
            _fail("E_REMOTE_BRANCH", "existing remote branch requires immutable head")
    elif branch_head is not None:
        _fail("E_REMOTE_BRANCH", "absent remote branch cannot have a head SHA")
    permissions = _object(
        remote["permissions"],
        _REMOTE_PERMISSION_FIELDS,
        "remote state.permissions",
    )
    if any(type(permissions[field]) is not bool for field in _REMOTE_PERMISSION_FIELDS):
        _fail("E_REMOTE_PERMISSION", "remote permissions must be booleans")
    return remote


def _validate_approval(payload: Any) -> dict[str, Any]:
    approval = validate_contract(
        payload,
        required=_APPROVAL_FIELDS,
        optional=set(),
        context="approval envelope",
    )
    if approval["decision"] not in {"approve", "reject"}:
        _fail("E_APPROVAL_DECISION", "approval decision is unsupported")
    repository = _text(approval["repository"], "approval envelope.repository")
    if not _GITHUB_REPOSITORY.fullmatch(repository):
        _fail("E_APPROVAL_TARGET", "approval repository must be owner/name")
    if not isinstance(approval["base_sha"], str) or not _COMMIT.fullmatch(
        approval["base_sha"]
    ):
        _fail("E_APPROVAL_BASE", "approval base SHA must be immutable")
    _branch(approval["branch"], "approval envelope.branch")
    _sha256(approval["fingerprint"], "approval envelope.fingerprint")
    _sha256(
        approval["evaluation_sha256"],
        "approval envelope.evaluation_sha256",
    )
    candidates = approval["candidate_hashes"]
    if not isinstance(candidates, list):
        _fail("E_SCHEMA_TYPE", "approval candidate_hashes must be a list")
    normalized: list[dict[str, str]] = []
    for index, value in enumerate(candidates):
        item = _object(
            value,
            _APPROVAL_CANDIDATE_FIELDS,
            f"approval candidate_hashes[{index}]",
        )
        normalized.append(
            {
                "path": _relative_path(
                    item["path"],
                    f"approval candidate_hashes[{index}].path",
                ).as_posix(),
                "sha256": _sha256(
                    item["sha256"],
                    f"approval candidate_hashes[{index}].sha256",
                ),
            }
        )
    if normalized != sorted(normalized, key=lambda item: item["path"]) or len(
        {item["path"] for item in normalized}
    ) != len(normalized):
        _fail("E_APPROVAL_CANDIDATES", "approval candidate hashes must be sorted and unique")
    return approval


def _publish_artifact_state(
    pr: dict[str, Any],
    candidate_root: Path,
) -> tuple[bool, bool]:
    try:
        info = candidate_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False, False
        candidate_root = candidate_root.resolve(strict=True)
        for item in [
            *cast(list[dict[str, Any]], pr["candidate_files"]),
            *cast(list[dict[str, Any]], pr["semantic_sources"]),
        ]:
            _artifact_bytes(
                candidate_root,
                {
                    "path": cast(str, item["path"]),
                    "sha256": cast(str, item["after_sha256"]),
                },
                "publish candidate",
            )
        candidate_current = True
    except (ContractError, OSError):
        candidate_current = False

    evaluation_current = False
    evaluation_path = candidate_root / "evaluation-report.json"
    try:
        evaluation_info = evaluation_path.lstat()
        if stat.S_ISREG(evaluation_info.st_mode) and not stat.S_ISLNK(
            evaluation_info.st_mode
        ):
            raw = read_regular_bytes(
                evaluation_path,
                maximum=MAX_ARTIFACT_BYTES,
                path_code="E_PUBLISH_PATH",
                size_code="E_PUBLISH_PATH",
            )
            evaluation_current = hashlib.sha256(raw).hexdigest() == cast(
                dict[str, Any],
                pr["evaluation"],
            )["report_sha256"]
    except (ContractError, OSError):
        pass
    return candidate_current, evaluation_current


def check_publish_gate(
    pr_payload: Any,
    remote_payload: Any,
    approval_payload: Any,
    candidate_root: Path,
) -> dict[str, object]:
    pr = _validate_pr_bundle(pr_payload)
    remote = _validate_remote_state(remote_payload)
    approval = _validate_approval(approval_payload)
    target = cast(dict[str, Any], pr["target"])
    evaluation = cast(dict[str, Any], pr["evaluation"])
    permissions = cast(dict[str, Any], remote["permissions"])
    candidate_hashes = _approval_candidate_hashes(pr)
    candidate_current, evaluation_current = _publish_artifact_state(
        pr,
        candidate_root,
    )
    findings: list[str] = []

    if not candidate_current:
        findings.append("E_CANDIDATE_DRIFT")
    if not evaluation_current:
        findings.append("E_EVALUATION_DRIFT")
    if remote["repository"] != target["repository"]:
        findings.append("E_REMOTE_REPOSITORY")
    if remote["base_sha"] != target["base_sha"]:
        findings.append("E_REMOTE_BASE")
    if remote["proposed_branch"] != target["branch"]:
        findings.append("E_REMOTE_BRANCH")
    if remote["branch_exists"]:
        findings.append("E_REMOTE_BRANCH_EXISTS")
    if not permissions["contents_write"] or not permissions["pull_requests_write"]:
        findings.append("E_REMOTE_PERMISSION")
    if approval["decision"] != "approve":
        findings.append("E_APPROVAL_DECISION")
    if approval["repository"] != target["repository"]:
        findings.append("E_APPROVAL_REPOSITORY")
    if approval["base_sha"] != target["base_sha"]:
        findings.append("E_APPROVAL_BASE")
    if approval["branch"] != target["branch"]:
        findings.append("E_APPROVAL_BRANCH")
    if approval["fingerprint"] != pr["fingerprint"]:
        findings.append("E_APPROVAL_FINGERPRINT")
    if approval["evaluation_sha256"] != evaluation["report_sha256"]:
        findings.append("E_APPROVAL_EVALUATION")
    if approval["candidate_hashes"] != candidate_hashes:
        findings.append("E_APPROVAL_CANDIDATES")

    findings = sorted(set(findings))
    authority = None
    if not findings:
        authority = {
            "repository": target["repository"],
            "base_sha": target["base_sha"],
            "branch": target["branch"],
            "fingerprint": pr["fingerprint"],
            "evaluation_sha256": evaluation["report_sha256"],
            "candidate_hashes": candidate_hashes,
            "connector_actions": [
                "create-branch",
                "commit-files",
                "push-branch",
                "open-pull-request",
            ],
        }
    return {
        "schema_version": 1,
        "status": "authorized" if authority is not None else "fail",
        "fingerprint": pr["fingerprint"],
        "findings": findings,
        "write_authority": authority,
    }
