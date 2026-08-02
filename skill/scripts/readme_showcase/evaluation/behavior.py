from __future__ import annotations

import hashlib
import os
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from ...pipeline_contracts import ContractError, canonical_sha256, read_regular_bytes
from ..contracts.evaluation import (
    MAX_ARGV,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT_MS,
    normalize_observation_cwd,
    validate_command_observation,
    validate_behavior_result,
)


_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_NETWORK_EXECUTABLES = {
    "curl", "ftp", "git", "nc", "netcat", "npm", "npx", "pip", "pip3",
    "ssh", "telnet", "wget",
}
_NETWORK_TOKENS = ("http://", "https://", "ftp://", "socket", "urllib", "requests")
_SECRET_TOKENS = ("password=", "passwd=", "secret=", "token=", "api_key=", "apikey=")


@dataclass(frozen=True)
class CommandPolicy:
    command_id: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_ms: int = 30_000
    max_output_bytes: int = 65_536

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


def _real_directory(path: Path, code: str = "E_OBSERVATION_UNSAFE") -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        info = absolute.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ContractError(code, f"linked directory is not allowed: {absolute}")
        resolved = absolute.resolve(strict=True)
        if not stat.S_ISDIR(info.st_mode):
            raise ContractError(code, f"directory is unavailable: {absolute}")
    except OSError as exc:
        raise ContractError(code, f"directory is unavailable: {absolute}") from exc
    return resolved


def _resolve_cwd(target_root: Path, relative: str) -> Path:
    root = _real_directory(target_root)
    normalized = normalize_observation_cwd(relative)
    candidate = root
    if normalized != ".":
        try:
            for part in PurePosixPath(normalized).parts:
                candidate /= part
                if stat.S_ISLNK(candidate.lstat().st_mode):
                    raise ContractError("E_OBSERVATION_UNSAFE", f"linked cwd is not allowed: {candidate}")
        except OSError as exc:
            raise ContractError("E_OBSERVATION_UNSAFE", f"observation cwd is unavailable: {candidate}") from exc
    resolved = _real_directory(candidate)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("E_OBSERVATION_UNSAFE", "observation cwd escapes target") from exc
    return resolved


def _validate_command_text(argv: Sequence[str]) -> None:
    if not argv or len(argv) > MAX_ARGV:
        raise ContractError("E_OBSERVATION_UNSAFE", "command argv is empty or oversized")
    lowered = [item.lower() for item in argv]
    if Path(argv[0]).name.lower() in _NETWORK_EXECUTABLES:
        raise ContractError("E_OBSERVATION_UNSAFE", "network-capable executable is outside behavior allowlist")
    flattened = "\n".join(lowered)
    if any(token in flattened for token in _NETWORK_TOKENS):
        raise ContractError("E_OBSERVATION_UNSAFE", "network-bearing command is outside behavior allowlist")
    if any(token in flattened for token in _SECRET_TOKENS):
        raise ContractError("E_OBSERVATION_UNSAFE", "secret-bearing command must not be observed")
    if len(argv) >= 4 and tuple(argv[1:4]) in (("-m", "pip", "install"), ("-m", "ensurepip", "--upgrade")):
        raise ContractError("E_OBSERVATION_UNSAFE", "package installation is outside behavior allowlist")


def _validate_executable(argv: Sequence[str]) -> None:
    executable = Path(argv[0])
    if not executable.is_absolute():
        raise ContractError("E_OBSERVATION_UNSAFE", "command executable must be an exact absolute path")
    absolute = Path(os.path.abspath(os.fspath(executable)))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ContractError("E_OBSERVATION_UNSAFE", f"linked executable path is not allowed: {current}")
        if not stat.S_ISREG(absolute.stat().st_mode) or not os.access(absolute, os.X_OK):
            raise ContractError("E_OBSERVATION_UNSAFE", "command executable must be a regular executable")
    except OSError as exc:
        raise ContractError("E_OBSERVATION_UNSAFE", "command executable is unavailable") from exc


def _read_binding(root: Path, binding: Mapping[str, str]) -> bytes:
    relative = normalize_observation_cwd(binding.get("path"))
    if relative == ".":
        raise ContractError("E_OBSERVATION_BINDING", "binding must name a regular file")
    try:
        return read_regular_bytes(
            root.joinpath(*PurePosixPath(relative).parts),
            maximum=16 * 1024 * 1024,
            path_code="E_OBSERVATION_BINDING",
            size_code="E_OBSERVATION_BINDING",
        )
    except ContractError as exc:
        raise ContractError("E_OBSERVATION_BINDING", f"bound input is unavailable: {relative}") from exc


def _verify_bindings(root: Path, bindings: Sequence[Mapping[str, str]], source: Mapping[str, str]) -> None:
    for binding in [*bindings, source]:
        raw = _read_binding(root, binding)
        if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
            raise ContractError("E_OBSERVATION_BINDING", f"bound input hash drifted: {binding.get('path')}")


def _tree_fingerprint(root: Path) -> str:
    entries: list[bytes] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            if stat.S_ISDIR(info.st_mode):
                continue
            raise ContractError("E_OBSERVATION_UNSAFE", f"unsupported target entry: {relative}")
        raw = read_regular_bytes(path, maximum=16 * 1024 * 1024, path_code="E_OBSERVATION_UNSAFE", size_code="E_OBSERVATION_UNSAFE")
        entries.append(relative.encode("utf-8") + b"\0" + hashlib.sha256(raw).digest())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


def _run_bounded(policy: CommandPolicy, cwd: Path, home: Path) -> tuple[int, bytes, bytes]:
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "README_SHOWCASE_CONTROLLED": "1",
    }
    argv = list(policy.argv)
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform == "darwin" and sandbox.is_file():
        argv = [
            str(sandbox), "-p",
            "(version 1) (allow default) (deny network*) (deny file-write*)",
            *argv,
        ]
    process = subprocess.Popen(
        argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        start_new_session=True, close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + policy.timeout_ms / 1_000
    failure: ContractError | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ContractError("E_OBSERVATION_TIMEOUT", "behavior command exceeded timeout")
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffer = streams[stream]
                if len(buffer) + len(chunk) > policy.max_output_bytes:
                    failure = ContractError("E_OBSERVATION_OUTPUT", "behavior command exceeded output bound")
                    break
                buffer.extend(chunk)
            if failure is not None:
                break
            if process.poll() is not None and not selector.get_map():
                break
        if failure is not None:
            _kill_group(process)
            raise failure
        return process.wait(), bytes(streams[process.stdout]), bytes(streams[process.stderr])
    finally:
        selector.close()
        _kill_group(process)
        process.stdout.close()
        process.stderr.close()


def _validate_policy(policy: CommandPolicy, *, execution: bool = False) -> CommandPolicy:
    if not isinstance(policy, CommandPolicy):
        raise ContractError("E_OBSERVATION_UNSAFE", "behavior command must use CommandPolicy")
    _validate_command_text(policy.argv)
    if execution:
        _validate_executable(policy.argv)
    if type(policy.timeout_ms) is not int or not 1 <= policy.timeout_ms <= MAX_TIMEOUT_MS:
        raise ContractError("E_OBSERVATION_UNSAFE", "behavior timeout is outside fixed bounds")
    if type(policy.max_output_bytes) is not int or not 1 <= policy.max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ContractError("E_OBSERVATION_UNSAFE", "behavior output limit is outside fixed bounds")
    # Reuse strict contract validation for ID, argv and command byte bounds.
    probe = {
        "schema_version": 1, "command_id": policy.command_id,
        "command": policy.command, "argv": list(policy.argv), "cwd": policy.cwd,
        "exit_code": 0, "stdout_sha256": _EMPTY_SHA256,
        "stderr_sha256": _EMPTY_SHA256, "stdout_bytes": 0, "stderr_bytes": 0,
        "observed_at_base_sha": "0" * 40, "input_hashes": [],
        "source_provenance": {"path": "source", "sha256": _EMPTY_SHA256},
        "runner": {"id": "controlled-local-v1", "controlled": True,
                   "clean_environment": True, "network": "blocked-by-allowlist"},
        "observed_at": "1970-01-01T00:00:00Z", "timeout_ms": policy.timeout_ms,
        "max_output_bytes": policy.max_output_bytes, "verification": "verified",
    }
    validate_command_observation(probe)
    return policy


def observe_command(
    policy: CommandPolicy,
    *,
    target_root: Path,
    base_sha: str,
    input_hashes: Sequence[Mapping[str, str]],
    source_provenance: Mapping[str, str],
    clock: Callable[[], str],
) -> dict[str, object]:
    """Execute one exact, pre-approved argv without a shell and return bound hashes only."""
    policy = _validate_policy(policy, execution=True)
    cwd = _resolve_cwd(target_root, policy.cwd)
    root = _real_directory(target_root)
    ordered_inputs = [dict(item) for item in input_hashes]
    source = dict(source_provenance)
    # Validate all caller bindings before touching subprocess state.
    template = {
        "schema_version": 1, "command_id": policy.command_id,
        "command": policy.command, "argv": list(policy.argv), "cwd": policy.cwd,
        "exit_code": 0, "stdout_sha256": _EMPTY_SHA256, "stderr_sha256": _EMPTY_SHA256,
        "stdout_bytes": 0, "stderr_bytes": 0, "observed_at_base_sha": base_sha,
        "input_hashes": ordered_inputs, "source_provenance": source,
        "runner": {"id": "controlled-local-v1", "controlled": True,
                   "clean_environment": True, "network": "blocked-by-allowlist"},
        "observed_at": clock(), "timeout_ms": policy.timeout_ms,
        "max_output_bytes": policy.max_output_bytes, "verification": "verified",
    }
    validate_command_observation(template)
    _verify_bindings(root, ordered_inputs, source)
    before = _tree_fingerprint(root)
    with tempfile.TemporaryDirectory(prefix="readme-showcase-observation-") as temporary:
        exit_code, stdout, stderr = _run_bounded(policy, cwd, Path(temporary))
    _verify_bindings(root, ordered_inputs, source)
    if _tree_fingerprint(root) != before:
        raise ContractError("E_OBSERVATION_MUTATION", "behavior command mutated target repository")
    observation = dict(template)
    observation.update({
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
    })
    return validate_command_observation(observation)


def _command_result(
    policy: CommandPolicy,
    observation: Mapping[str, object] | None,
    *,
    base_sha: str,
    input_hashes: Sequence[Mapping[str, str]],
    source_provenance: Mapping[str, str] | None,
) -> dict[str, object]:
    if observation is None:
        reason = f"observation-missing:{policy.command_id}"
        return {"command_id": policy.command_id, "status": "not-observed", "exit_code": None,
                "verification": None, "observation_sha256": None, "reasons": [reason]}
    value = validate_command_observation(observation)
    reasons: list[str] = []
    if value["command_id"] != policy.command_id:
        reasons.append(f"observation-command-id-drift:{policy.command_id}")
    if value["command"] != policy.command or value["argv"] != list(policy.argv):
        reasons.append(f"observation-command-drift:{policy.command_id}")
    if value["cwd"] != policy.cwd:
        reasons.append(f"observation-cwd-drift:{policy.command_id}")
    if value["timeout_ms"] != policy.timeout_ms or value["max_output_bytes"] != policy.max_output_bytes:
        reasons.append(f"observation-policy-drift:{policy.command_id}")
    if value["observed_at_base_sha"] != base_sha:
        reasons.append(f"observation-base-drift:{policy.command_id}")
    if value["input_hashes"] != [dict(item) for item in input_hashes]:
        reasons.append(f"observation-input-drift:{policy.command_id}")
    if source_provenance is None or value["source_provenance"] != dict(source_provenance):
        reasons.append(f"observation-source-drift:{policy.command_id}")
    digest = canonical_sha256(value)
    if reasons:
        return {"command_id": policy.command_id, "status": "unsupported", "exit_code": value["exit_code"],
                "verification": value["verification"], "observation_sha256": digest,
                "reasons": sorted(set(reasons))}
    if value["verification"] != "verified":
        return {"command_id": policy.command_id, "status": "unverified", "exit_code": value["exit_code"],
                "verification": value["verification"], "observation_sha256": digest,
                "reasons": [f"observation-imported-unverified:{policy.command_id}"]}
    status = "pass" if value["exit_code"] == 0 else "fail"
    reasons = [] if status == "pass" else [f"observation-nonzero-exit:{policy.command_id}"]
    return {"command_id": policy.command_id, "status": status, "exit_code": value["exit_code"],
            "verification": value["verification"], "observation_sha256": digest, "reasons": reasons}


def evaluate_behavior(
    *,
    policies: Sequence[CommandPolicy],
    observations: Sequence[Mapping[str, object]],
    base_sha: str,
    input_hashes: Sequence[Mapping[str, str]],
    source_provenance: Mapping[str, str] | None,
) -> dict[str, object]:
    normalized_policies = sorted((_validate_policy(policy) for policy in policies), key=lambda item: item.command_id)
    if len({policy.command_id for policy in normalized_policies}) != len(normalized_policies):
        raise ContractError("E_OBSERVATION_BINDING", "behavior allowlist contains duplicate command IDs")
    by_id: dict[str, Mapping[str, object]] = {}
    for observation in observations:
        value = validate_command_observation(observation)
        command_id = value["command_id"]
        if command_id in by_id:
            raise ContractError("E_OBSERVATION_BINDING", f"duplicate observation: {command_id}")
        by_id[command_id] = value
    allowed_ids = {policy.command_id for policy in normalized_policies}
    extras = sorted(set(by_id) - allowed_ids)
    if extras:
        raise ContractError("E_OBSERVATION_BINDING", f"observation is outside allowlist: {extras[0]}")
    commands = [
        _command_result(policy, by_id.get(policy.command_id), base_sha=base_sha,
                        input_hashes=input_hashes, source_provenance=source_provenance)
        for policy in normalized_policies
    ]
    covered = sum(command["status"] == "pass" for command in commands)
    statuses = {str(command["status"]) for command in commands}
    if commands and statuses == {"pass"}:
        status = "pass"
    elif "fail" in statuses:
        status = "fail"
    elif "unsupported" in statuses:
        status = "unsupported"
    elif "unverified" in statuses:
        status = "unverified"
    else:
        status = "not-observed"
    result = {
        "status": status,
        "reasons": sorted({reason for command in commands for reason in command["reasons"]}),
        "commands": commands,
        "observable_commands": covered,
        "total_commands": len(commands),
    }
    return validate_behavior_result(result)
