from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from ...pipeline_contracts import ContractError, MAX_JSON_BYTES, canonical_sha256, read_regular_bytes
from ..contracts.evidence import validate_evidence_graph
from ..validation.legacy import validate_generated_bundle
from .bundle import build_delivery_result


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[^/\s]+/[^/\s]+\Z")
_FILTER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_V3_SVG_PATH = re.compile(
    r"assets/readme-showcase/[^/]+/(?:desktop|mobile)\.svg\Z"
)
_TEMP_PREFIX = "readme-showcase-delivery-"
_MARKER = ".readme-showcase-delivery-root"
_GIT_OUTPUT_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class _Candidate:
    path: PurePosixPath
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class _MainSnapshot:
    index_sha256: str
    status: bytes
    refs: bytes
    tracked_sha256: str
    head_tree: bytes


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    return environment


def _run_git(
    root: Path,
    *arguments: str,
    code: str = "E_PR_GIT",
    check: bool = True,
    configurations: Sequence[tuple[str, str]] = (),
    input_bytes: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    configuration_arguments = [
        argument
        for key, value in configurations
        for argument in ("-c", f"{key}={value}")
    ]
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.attributesFile=/dev/null",
                "-c",
                "credential.helper=",
                "-c",
                "diff.external=",
                *configuration_arguments,
                "-C",
                str(root),
                *arguments,
            ],
            input=b"" if input_bytes is None else input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
            env=_git_environment(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ContractError(code, "local Git operation is unavailable") from exc
    if len(result.stdout) > _GIT_OUTPUT_LIMIT or len(result.stderr) > _GIT_OUTPUT_LIMIT:
        _fail(code, "local Git operation output exceeds limit")
    if check and result.returncode != 0:
        _fail(code, f"local Git operation failed: {arguments[0]}")
    return result.returncode, result.stdout, result.stderr


def _safe_path(value: Any, context: str) -> PurePosixPath:
    if not isinstance(value, str):
        _fail("E_PR_PATH", f"{context} must be a nonempty POSIX path")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("E_PR_PATH", f"{context} must be valid UTF-8") from exc
    if (
        not value
        or len(encoded) > 4096
        or value.startswith("~")
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or "\x00" in value
    ):
        _fail("E_PR_PATH", f"{context} must be a nonempty POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} or part.lower() == ".git" for part in path.parts)
    ):
        _fail("E_PR_PATH", f"{context} must be a safe relative path")
    return path


def _real_directory(path: Path, code: str, context: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError(code, f"{context} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail(code, f"{context} must be a real directory")
    return path.resolve(strict=True)


def _github_repository(value: str) -> str:
    patterns = (
        r"https://github\.com/(?P<repo>[^?#]+?)(?:\.git)?\Z",
        r"git@github\.com:(?P<repo>.+?)(?:\.git)?\Z",
        r"ssh://git@github\.com/(?P<repo>.+?)(?:\.git)?\Z",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value)
        if match and _REPOSITORY.fullmatch(match.group("repo")):
            return match.group("repo")
    _fail("E_PR_TARGET", "origin must identify the exact GitHub repository")


def _index_bytes(repository: Path, configurations: Sequence[tuple[str, str]]) -> bytes:
    index_path = _run_git(repository, "rev-parse", "--git-path", "index", configurations=configurations)[1].decode("utf-8").strip()
    path = Path(index_path)
    if not path.is_absolute():
        path = repository / path
    try:
        return read_regular_bytes(path, maximum=_GIT_OUTPUT_LIMIT, path_code="E_PR_INDEX", size_code="E_PR_INDEX")
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            return b""
        raise


def _tracked_files_digest(repository: Path, configurations: Sequence[tuple[str, str]]) -> str:
    raw_paths = _run_git(repository, "ls-files", "-z", configurations=configurations)[1]
    digest = hashlib.sha256()
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        try:
            value = raw_path.decode("utf-8", errors="surrogateescape")
            path = repository.joinpath(*PurePosixPath(value).parts)
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                file_digest = hashlib.sha256()
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                    ):
                        _fail("E_PR_WORKTREE", "tracked main-worktree bytes changed during inspection")
                    while True:
                        chunk = os.read(descriptor, 64 * 1024)
                        if not chunk:
                            break
                        file_digest.update(chunk)
                    after = os.fstat(descriptor)
                    if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                        _fail("E_PR_WORKTREE", "tracked main-worktree bytes changed during inspection")
                finally:
                    os.close(descriptor)
                content = file_digest.digest()
            elif stat.S_ISLNK(info.st_mode):
                content = os.readlink(path).encode("utf-8", errors="surrogateescape")
            else:
                content = b"<special>"
            digest.update(raw_path + b"\0" + str(stat.S_IFMT(info.st_mode)).encode("ascii") + b"\0" + content + b"\0")
        except (FileNotFoundError, OSError) as exc:
            raise ContractError("E_PR_WORKTREE", "tracked main-worktree bytes changed during inspection") from exc
    return digest.hexdigest()


def _snapshot_main(repository: Path, configurations: Sequence[tuple[str, str]]) -> _MainSnapshot:
    return _MainSnapshot(
        index_sha256=hashlib.sha256(_index_bytes(repository, configurations)).hexdigest(),
        status=_run_git(repository, "status", "--porcelain=v1", "-z", "--untracked-files=all", configurations=configurations)[1],
        refs=_run_git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)", configurations=configurations)[1],
        tracked_sha256=_tracked_files_digest(repository, configurations),
        head_tree=_run_git(repository, "rev-parse", "HEAD^{tree}", configurations=configurations)[1],
    )


def _filter_configurations(
    repository: Path,
    base_sha: str,
    extra_paths: Iterable[str] = (),
) -> tuple[tuple[str, str], ...]:
    tree_paths = _run_git(repository, "ls-tree", "-rz", "--name-only", base_sha)[1]
    paths = b"\0".join(
        [
            *[path for path in tree_paths.split(b"\0") if path],
            *[path.encode("utf-8") for path in extra_paths],
            b"",
        ]
    )
    attributes = _run_git(
        repository,
        "check-attr",
        "-z",
        "--stdin",
        f"--source={base_sha}",
        "filter",
        input_bytes=paths,
    )[1].split(b"\0")
    if attributes and attributes[-1] == b"":
        attributes.pop()
    if len(attributes) % 3:
        _fail("E_PR_GIT", "unexpected Git attribute result")
    names: set[str] = set()
    for index in range(0, len(attributes), 3):
        value = attributes[index + 2].decode("utf-8", errors="strict")
        if value in {"unspecified", "unset", "set"}:
            continue
        if not _FILTER_NAME.fullmatch(value):
            _fail("E_PR_GIT", "repository uses an unsupported checkout filter name")
        names.add(value)
    return tuple(
        setting
        for name in sorted(names)
        for setting in (
            (f"filter.{name}.clean", "cat"),
            (f"filter.{name}.smudge", "cat"),
            (f"filter.{name}.process", ""),
            (f"filter.{name}.required", "false"),
        )
    )


def _candidate_references(
    payload: Any,
    artifact_root: Path | None = None,
) -> tuple[str, str, list[dict[str, str]], str]:
    if isinstance(payload, dict) and payload.get("schema_version") == 3:
        if artifact_root is None:
            _fail("E_PR_PATH", "compiled delivery requires an artifact root")
        validation = validate_generated_bundle(payload, artifact_root)
        target = payload.get("target")
        candidate = payload.get("candidate")
        if not isinstance(target, dict) or not isinstance(candidate, dict):
            _fail("E_SCHEMA_TYPE", "compiled delivery bundle target and candidate must be objects")
        readmes = candidate.get("readmes")
        assets = candidate.get("assets")
        if not isinstance(readmes, list) or not isinstance(assets, list):
            _fail("E_SCHEMA_TYPE", "compiled delivery candidate readmes and assets must be lists")
        values = [*readmes, *assets]
        references: list[dict[str, str]] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
                _fail("E_SCHEMA_TYPE", f"compiled candidate reference {index} must contain path and sha256")
            path = _safe_path(value["path"], f"compiled candidate reference {index}")
            if path.name not in {"README.md", "README_zh.md"} and _V3_SVG_PATH.fullmatch(path.as_posix()) is None:
                _fail("E_PR_PATH", f"compiled candidate is not a README or stage-6 SVG: {path.as_posix()}")
            digest = value["sha256"]
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                _fail("E_BUNDLE_HASH", f"compiled candidate reference {index} must use lowercase SHA-256")
            references.append({"path": path.as_posix(), "sha256": digest})
        if not references:
            _fail("E_PR_NO_CHANGES", "compiled delivery bundle contains no candidates")
        target_repository = target.get("repository")
        base_sha = target.get("base_sha")
        if not isinstance(target_repository, str) or not isinstance(base_sha, str):
            _fail("E_SCHEMA_TYPE", "compiled delivery target is malformed")
        return target_repository, base_sha, references, str(validation["candidate_sha256"])
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        _fail("E_SCHEMA_VERSION", "delivery preparation requires generated bundle schema_version 2 or 3")
    if set(payload) != {"schema_version", "mode", "target", "candidate", "artifacts"}:
        _fail("E_SCHEMA_UNKNOWN_FIELD", "delivery bundle fields must match generated bundle v2")
    target = payload.get("target")
    candidate = payload.get("candidate")
    if not isinstance(target, dict) or not isinstance(candidate, dict):
        _fail("E_SCHEMA_TYPE", "delivery bundle target and candidate must be objects")
    if set(target) != {"repository", "base_sha"} or set(candidate) != {
        "readme",
        "assets",
        "candidate_sha256",
    }:
        _fail("E_SCHEMA_UNKNOWN_FIELD", "delivery target and candidate fields must match generated bundle v2")
    repository = target.get("repository")
    base_sha = target.get("base_sha")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        _fail("E_PR_TARGET", "bundle target.repository must be owner/name")
    if not isinstance(base_sha, str) or not _COMMIT.fullmatch(base_sha):
        _fail("E_PR_BASE", "bundle target.base_sha must be immutable")
    assets = candidate.get("assets")
    readme = candidate.get("readme")
    if not isinstance(assets, list):
        _fail("E_SCHEMA_TYPE", "bundle candidate.assets must be a list")
    if len(assets) > 10_000:
        _fail("E_PR_PATH", "delivery bundle contains too many assets")
    values = ([] if readme is None else [readme]) + assets
    references: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            _fail("E_SCHEMA_TYPE", f"candidate reference {index} must contain path and sha256")
        path = _safe_path(value.get("path"), f"candidate reference {index}")
        digest = value.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("E_BUNDLE_HASH", f"candidate reference {index} must use lowercase SHA-256")
        normalized = path.as_posix()
        if normalized in seen:
            _fail("E_PR_PATH", f"duplicate candidate path: {normalized}")
        seen.add(normalized)
        references.append({"path": normalized, "sha256": digest})
    asset_paths = [reference["path"] for reference in references[1 if readme is not None else 0 :]]
    if asset_paths != sorted(asset_paths):
        _fail("E_PR_PATH", "candidate assets must use deterministic path order")
    mode = payload.get("mode")
    if mode not in {"readme", "asset-only", "audit-only"}:
        _fail("E_BUNDLE_MODE", "delivery bundle mode is unsupported")
    if (mode == "readme") != (readme is not None):
        _fail("E_BUNDLE_MODE", "delivery bundle README differs from mode")
    if mode == "asset-only" and not assets:
        _fail("E_BUNDLE_MODE", "asset-only delivery requires an asset")
    if mode == "audit-only" and assets:
        _fail("E_BUNDLE_MODE", "audit-only delivery cannot contain assets")
    expected_candidate_sha = canonical_sha256({"readme": readme, "assets": assets})
    if candidate.get("candidate_sha256") != expected_candidate_sha:
        _fail("E_BUNDLE_HASH", "bundle candidate_sha256 is inconsistent")
    if not references:
        _fail("E_PR_NO_CHANGES", "delivery bundle contains no candidates")
    return repository, base_sha, references, expected_candidate_sha


def _read_candidates(artifact_root: Path, references: Sequence[dict[str, str]]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for reference in references:
        path = _safe_path(reference["path"], "candidate path")
        try:
            raw = read_regular_bytes(
                artifact_root.joinpath(*path.parts),
                maximum=MAX_JSON_BYTES,
                path_code="E_PR_PATH",
                size_code="E_PR_PATH",
            )
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                raise ContractError("E_PR_PATH", f"candidate is missing: {path.as_posix()}") from exc
            raise
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            _fail("E_BUNDLE_HASH", f"candidate hash differs: {path.as_posix()}")
        candidates.append(_Candidate(path=path, raw=raw, sha256=reference["sha256"]))
    return sorted(candidates, key=lambda item: item.path.as_posix())


def _validate_evidence_base(payload: dict[str, Any], artifact_root: Path, worktree: Path) -> None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        _fail("E_SCHEMA_TYPE", "delivery bundle artifacts must be an object")
    evidence_reference = artifacts.get("evidence")
    if evidence_reference is None:
        return
    if not isinstance(evidence_reference, dict) or set(evidence_reference) != {"path", "sha256"}:
        _fail("E_SCHEMA_TYPE", "bundle evidence reference must contain path and sha256")
    evidence_path = _safe_path(evidence_reference.get("path"), "bundle evidence path")
    evidence_digest = evidence_reference.get("sha256")
    if not isinstance(evidence_digest, str) or not _SHA256.fullmatch(evidence_digest):
        _fail("E_BUNDLE_HASH", "bundle evidence reference must use lowercase SHA-256")
    try:
        raw = read_regular_bytes(
            artifact_root.joinpath(*evidence_path.parts),
            maximum=MAX_JSON_BYTES,
            path_code="E_PR_EVIDENCE",
            size_code="E_PR_EVIDENCE",
        )
        evidence = validate_evidence_graph(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_PR_EVIDENCE", "bundle evidence must be UTF-8 JSON") from exc
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            raise ContractError("E_PR_EVIDENCE", "bundle evidence is missing") from exc
        raise
    if hashlib.sha256(raw).hexdigest() != evidence_digest:
        _fail("E_BUNDLE_HASH", "bundle evidence bytes differ from reference")
    source_cache: dict[str, str] = {}
    for fact in evidence["facts"]:
        source = fact["source"]
        source_path = _safe_path(source["path"], "evidence source path")
        normalized = source_path.as_posix()
        if normalized not in source_cache:
            try:
                source_raw = read_regular_bytes(
                    worktree.joinpath(*source_path.parts),
                    maximum=MAX_JSON_BYTES,
                    path_code="E_PR_EVIDENCE",
                    size_code="E_PR_EVIDENCE",
                )
            except ContractError as exc:
                raise ContractError("E_PR_EVIDENCE", f"evidence source is unavailable at base: {normalized}") from exc
            source_cache[normalized] = hashlib.sha256(source_raw).hexdigest()
        if source_cache[normalized] != fact["source_sha256"]:
            _fail("E_PR_EVIDENCE", f"evidence source differs from immutable base: {normalized}")


def _validate_allowlist(allowed_paths: Iterable[str], candidates: Sequence[_Candidate]) -> None:
    normalized: list[str] = [_safe_path(value, "allowlist path").as_posix() for value in allowed_paths]
    if len(normalized) != len(set(normalized)):
        _fail("E_PR_PATH", "delivery allowlist contains a duplicate path")
    candidate_paths = {item.path.as_posix() for item in candidates}
    if set(normalized) != candidate_paths:
        _fail("E_PR_PATH", "delivery allowlist must exactly match candidate paths")


def _create_temp_root(repository: Path, temporary_parent: Path | None) -> tuple[Path, Path]:
    parent: str | None = None
    if temporary_parent is not None:
        resolved = _real_directory(temporary_parent, "E_PR_PATH", "temporary parent")
        try:
            resolved.relative_to(repository)
        except ValueError:
            pass
        else:
            _fail("E_PR_PATH", "temporary worktree must stay outside target repository")
        parent = str(resolved)
    try:
        root = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=parent)).resolve(strict=True)
        (root / _MARKER).write_text("detached delivery worktree\n", encoding="ascii")
    except OSError as exc:
        raise ContractError("E_PR_GIT", "cannot create unique delivery temporary root") from exc
    try:
        root.relative_to(repository)
    except ValueError:
        return root, root / "candidate"
    shutil.rmtree(root, ignore_errors=True)
    _fail("E_PR_PATH", "temporary worktree must stay outside target repository")


def _add_worktree(
    repository: Path,
    destination: Path,
    base_sha: str,
    configurations: Sequence[tuple[str, str]] = (),
) -> None:
    _run_git(
        repository,
        "worktree",
        "add",
        "--detach",
        str(destination),
        base_sha,
        code="E_PR_BASE",
        configurations=configurations or _filter_configurations(repository, base_sha),
    )


def _target_before_sha256(worktree: Path, path: PurePosixPath) -> str | None:
    current = worktree
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            _fail("E_PR_PATH", f"candidate target crosses symlink: {path.as_posix()}")
        if index < len(path.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            _fail("E_PR_PATH", f"candidate parent is not a directory: {path.as_posix()}")
        if index == len(path.parts) - 1 and not stat.S_ISREG(info.st_mode):
            _fail("E_PR_PATH", f"candidate target is not a regular file: {path.as_posix()}")
    return hashlib.sha256(read_regular_bytes(current, maximum=MAX_JSON_BYTES, path_code="E_PR_PATH", size_code="E_PR_PATH")).hexdigest()


def _apply_candidates(worktree: Path, candidates: Sequence[_Candidate]) -> None:
    for candidate in candidates:
        parent = worktree
        for part in candidate.path.parts[:-1]:
            parent /= part
            try:
                info = parent.lstat()
            except FileNotFoundError:
                parent.mkdir(mode=0o755)
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                _fail("E_PR_PATH", f"candidate parent is unsafe: {candidate.path.as_posix()}")
        destination = worktree.joinpath(*candidate.path.parts)
        temporary = destination.with_name(f".{destination.name}.delivery-{secrets.token_hex(8)}")
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o644,
            )
            view = memoryview(candidate.raw)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, destination)
        except OSError as exc:
            raise ContractError("E_PR_PATH", f"cannot atomically apply candidate: {candidate.path.as_posix()}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _filesystem_entries(worktree: Path) -> dict[str, int]:
    entries: dict[str, int] = {}
    for directory, names, files in os.walk(worktree, topdown=True, followlinks=False):
        relative_directory = Path(directory).relative_to(worktree)
        if relative_directory == Path(".") and ".git" in files:
            files.remove(".git")
        for name in [*names, *files]:
            path = Path(directory) / name
            relative = path.relative_to(worktree).as_posix()
            entries[relative] = stat.S_IFMT(path.lstat().st_mode)
    return entries


def _changed_paths(worktree: Path, configurations: Sequence[tuple[str, str]]) -> set[str]:
    raw = _run_git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        configurations=configurations,
    )[1]
    paths: set[str] = set()
    records = raw.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            _fail("E_PR_GIT", "unexpected Git status record")
        paths.add(record[3:].decode("utf-8", errors="surrogateescape"))
        if record[:1] in {b"R", b"C"} and index < len(records):
            paths.add(records[index].decode("utf-8", errors="surrogateescape"))
            index += 1
    return paths


def _cleanup(repository: Path, destination: Path, temporary_root: Path) -> None:
    if destination.exists() or destination.is_symlink():
        _run_git(repository, "worktree", "remove", "--force", str(destination), check=False)
    listed = _run_git(repository, "worktree", "list", "--porcelain", check=False)[1]
    destination_marker = f"worktree {destination}\n".encode("utf-8")
    if destination_marker in listed:
        _fail("E_PR_GIT", "delivery worktree cleanup failed")
    marker = temporary_root / _MARKER
    if temporary_root.name.startswith(_TEMP_PREFIX) and marker.is_file():
        shutil.rmtree(temporary_root)
    elif temporary_root.exists():
        _fail("E_PR_GIT", "refusing to remove unmarked delivery temporary root")


def cleanup_delivery_worktree(repository_root: Path, worktree_path: Path) -> None:
    repository = _real_directory(repository_root, "E_PR_TARGET", "target repository")
    destination = Path(os.path.abspath(worktree_path))
    temporary_root = destination.parent
    if destination.name != "candidate" or not temporary_root.name.startswith(_TEMP_PREFIX):
        _fail("E_PR_PATH", "retained path is not a delivery worktree")
    if not temporary_root.exists():
        return
    _cleanup(repository, destination, temporary_root)


def prepare_delivery_worktree(
    payload: Any,
    artifact_root: Path,
    target_root: Path,
    allowed_paths: Iterable[str],
    *,
    audit_retain_failure: bool = False,
    retention_reason: str | None = None,
    temporary_parent: Path | None = None,
) -> dict[str, object]:
    """Prepare v2 or compiled v3 candidates in an external detached worktree."""

    if audit_retain_failure and (not isinstance(retention_reason, str) or not retention_reason.strip()):
        _fail("E_PR_PATH", "audited failure retention requires a nonempty reason")
    repository = _real_directory(target_root, "E_PR_TARGET", "target repository")
    artifacts = _real_directory(artifact_root, "E_PR_PATH", "artifact root")
    try:
        artifacts.relative_to(repository)
    except ValueError:
        pass
    else:
        _fail("E_PR_PATH", "artifact root must stay outside target repository")
    git_info = repository / ".git"
    if not git_info.is_dir() or git_info.is_symlink():
        _fail("E_PR_TARGET", "target repository .git must be a real directory")

    repository_name, base_sha, references, candidate_sha = _candidate_references(payload, artifacts)
    candidates = _read_candidates(artifacts, references)
    _validate_allowlist(allowed_paths, candidates)
    origin = _run_git(repository, "remote", "get-url", "origin", code="E_PR_TARGET")[1].decode("utf-8").strip()
    if _github_repository(origin) != repository_name:
        _fail("E_PR_TARGET", "target origin differs from bundle repository")
    if _run_git(repository, "cat-file", "-e", f"{base_sha}^{{commit}}", check=False)[0] != 0:
        _fail("E_PR_BASE", "bundle base commit is unavailable")
    resolved = _run_git(repository, "rev-parse", "--verify", f"{base_sha}^{{commit}}", code="E_PR_BASE")[1].decode("ascii").strip()
    if resolved != base_sha:
        _fail("E_PR_BASE", "bundle base does not resolve to the exact commit")

    filter_configurations = _filter_configurations(
        repository,
        base_sha,
        (candidate.path.as_posix() for candidate in candidates),
    )
    main_before = _snapshot_main(repository, filter_configurations)
    temporary_root: Path | None = None
    destination: Path | None = None
    worktree_created = False
    result: dict[str, object] | None = None
    failure: BaseException | None = None
    try:
        temporary_root, destination = _create_temp_root(repository, temporary_parent)
        _add_worktree(repository, destination, base_sha, filter_configurations)
        worktree_created = True
        detached_head = _run_git(destination, "rev-parse", "--verify", "HEAD", code="E_PR_BASE", configurations=filter_configurations)[1].decode("ascii").strip()
        if detached_head != base_sha:
            _fail("E_PR_BASE", "detached worktree HEAD differs from bundle base")
        if _run_git(destination, "status", "--porcelain=v1", "-z", configurations=filter_configurations)[1]:
            _fail("E_PR_WORKTREE", "detached base worktree is not clean")
        _validate_evidence_base(payload, artifacts, destination)
        base_tree = _run_git(destination, "rev-parse", "HEAD^{tree}", configurations=filter_configurations)[1].decode("ascii").strip()
        filesystem_before = _filesystem_entries(destination)
        changes: list[dict[str, object]] = []
        for candidate in candidates:
            before = _target_before_sha256(destination, candidate.path)
            changes.append(
                {
                    "path": candidate.path.as_posix(),
                    "before_sha256": before,
                    "after_sha256": candidate.sha256,
                    "change": "add" if before is None else "unchanged" if before == candidate.sha256 else "modify",
                }
            )
        _apply_candidates(destination, candidates)
        candidate_paths = {item.path.as_posix() for item in candidates}
        filesystem_after = _filesystem_entries(destination)
        added_entries = set(filesystem_after) - set(filesystem_before)
        unexpected_entries = {
            path for path in added_entries if path not in candidate_paths and not any(candidate.startswith(path + "/") for candidate in candidate_paths)
        }
        expected_changed_paths = {
            str(change["path"]) for change in changes if change["change"] != "unchanged"
        }
        if unexpected_entries or _changed_paths(destination, filter_configurations) != expected_changed_paths:
            _fail("E_PR_PATH", "detached worktree contains a non-allowlisted change")
        _run_git(destination, "add", "--", *sorted(candidate_paths), configurations=filter_configurations)
        diff = _run_git(
            destination,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            configurations=filter_configurations,
        )[1]
        if not diff:
            _fail("E_PR_NO_CHANGES", "delivery candidates do not change the immutable base")
        candidate_tree = _run_git(destination, "write-tree", configurations=filter_configurations)[1].decode("ascii").strip()
        result = build_delivery_result(
            repository=repository_name,
            base_sha=base_sha,
            base_tree=base_tree,
            candidate_tree=candidate_tree,
            candidate_sha256=candidate_sha,
            changes=changes,
            diff=diff,
        )
    except KeyboardInterrupt as exc:
        failure = ContractError("E_PR_GIT", "delivery preparation was interrupted")
        failure.__cause__ = exc
    except ContractError as exc:
        failure = exc
    except Exception as exc:
        failure = ContractError("E_PR_GIT", "delivery preparation failed")
        failure.__cause__ = exc
    finally:
        retain = bool(failure is not None and audit_retain_failure and worktree_created)
        if retain and destination is not None:
            setattr(failure, "retained_path", str(destination))
            setattr(failure, "retention_reason", retention_reason.strip())
            setattr(
                failure,
                "failure_result",
                {
                    "schema_version": 2,
                    "status": "failed",
                    "retained_path": str(destination),
                    "retention_reason": retention_reason.strip(),
                },
            )
        elif temporary_root is not None and destination is not None:
            try:
                _cleanup(repository, destination, temporary_root)
            except ContractError as cleanup_error:
                if failure is None:
                    failure = cleanup_error
        try:
            main_after = _snapshot_main(repository, filter_configurations)
            if main_after != main_before and failure is None:
                failure = ContractError("E_PR_WORKTREE", "main worktree changed during delivery preparation")
        except ContractError as snapshot_error:
            if failure is None:
                failure = snapshot_error
    if failure is not None:
        raise failure
    if result is None:
        _fail("E_PR_GIT", "delivery preparation produced no result")
    return result


__all__ = ["cleanup_delivery_worktree", "prepare_delivery_worktree"]
