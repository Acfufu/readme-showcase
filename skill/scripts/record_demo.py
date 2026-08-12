#!/usr/bin/env python3
"""Opt-in demo recording: asciinema cast + agg GIF rendering.

Demo recording is opt-in: this module never auto-installs asciinema or agg.
When either tool is missing, require_tools() raises a DemoDependencyError with
an install hint and static SVG stays the default. agg embeds its own GIF
encoder, so no ffmpeg step is needed; the deterministic claim is that one cast
rendered twice by agg yields identical bytes (SHA-256), verified empirically by
verify_determinism() and documented in references/motion-production.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, assert_never

if __package__ and __package__.startswith("skill."):
    from skill.scripts.pipeline_contracts import (
        ContractError,
        read_regular_bytes,
        write_bytes_atomic,
    )
    from skill.scripts.readme_showcase.contracts.demo_envelope import (
        validate_demo_envelope_v1,
    )
    from skill.scripts.readme_showcase.contracts.evidence import (
        validate_evidence_graph,
    )
else:  # The installed Skill runs this file directly from its scripts directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.pipeline_contracts import (
        ContractError,
        read_regular_bytes,
        write_bytes_atomic,
    )
    from scripts.readme_showcase.contracts.demo_envelope import (
        validate_demo_envelope_v1,
    )
    from scripts.readme_showcase.contracts.evidence import (
        validate_evidence_graph,
    )


MAX_DEMO_CAST_BYTES: Final = 64 * 1024 * 1024
MAX_DEMO_GIF_BYTES: Final = 64 * 1024 * 1024
MAX_DEMO_EVIDENCE_BYTES: Final = 8 * 1024 * 1024
MAX_DEMO_SUBPROCESS_SECONDS: Final = 300
IDLE_TIME_LIMIT_SECONDS: Final = "2"
LAST_FRAME_DURATION_SECONDS: Final = "2"
DEFAULT_FPS: Final = 20
DEFAULT_THEME: Final = "asciinema"
_LOCALES: Final = r"(?:en|zh-Hans|zh-Hant|ja|ko|fr|de)"
_DEMO_ASSET_PATH: Final = re.compile(
    rf"assets/readme-showcase/{_LOCALES}/(?:[^/]+\.(?:gif|cast))"
)
_DEMO_SCRIPT_PATH: Final = re.compile(r"demo/.*\.(?:cast|sh|txt)")

# Task 4.4 content review gate: leakage patterns scanned over the capture text.
# A single finding per pattern kind is reported, so a fixable capture produces a
# bounded, actionable review result.
_LEAK_PATTERNS: Final = (
    (
        "leak-path",
        re.compile(
            r"/(?:Users|home)/[^/\s\"'`;]+"
            r"|~/(?:[^\s\"'`]*)"
            r"|\$(?:HOME|\{HOME\})"
        ),
    ),
    # Email / `user@host` identities: the host must have >=2 dotted labels and
    # every label must contain a letter, so version/package syntax (`foo@beta`,
    # `bar@v1`, `module@v1.2.3`, `target@x86_64`) no longer matches. Tokens that
    # still look email-shaped (e.g. `python@2x.png`) are accepted residuals:
    # this is a demo-content gate, not a security boundary, and a capture
    # containing such a token can be edited and re-recorded.
    (
        "leak-identity",
        re.compile(
            r"\b[\w.+-]+@(?:[\w-]*[A-Za-z][\w-]*)"
            r"(?:\.[\w-]*[A-Za-z][\w-]*){1,}\b"
        ),
    ),
    (
        "leak-credential",
        re.compile(
            r"gh[pousr]_[A-Za-z0-9]{36,}"
            r"|sk-[A-Za-z0-9]{20,}"
            r"|(?:api[_-]?key|access[_-]?token|token|secret|password)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-.]{12,}"
            r"|bearer\s+[A-Za-z0-9._~+/=-]{16,}",
            re.IGNORECASE,
        ),
    ),
)

# Generic shell utilities never need repository capability evidence; every
# other command a capture shows must be backed by a cli-entrypoint or
# command-observation fact in the repository-evidence graph.
_GENERIC_SHELL_COMMANDS: Final = frozenset(
    {
        "apt", "apt-get", "awk", "bash", "brew", "bun", "cargo", "cat", "cd",
        "chmod", "cp", "curl", "date", "deno", "docker", "docker-compose",
        "echo", "env", "export", "false", "find", "gh", "git", "go", "grep",
        "gzip", "head", "jq", "kubectl", "ls", "make", "mkdir", "mv", "node",
        "npm", "npx", "pip", "pip3", "pnpm", "poetry", "printf", "pwd",
        "python", "python3", "rm", "ruby", "sed", "set", "sh", "sleep", "sort",
        "source", "sudo", "tail", "tar", "tee", "touch", "tree", "true",
        "uname", "uniq", "unzip", "uv", "wc", "wget", "which", "xargs", "yarn",
        "yes", "zip", "zsh",
    }
)

_PRINTABLE_RUN: Final = re.compile(rb"[\x20-\x7e]{8,}")

# A `$ token` line is a claimed command only when the token looks like a
# command: pure amounts (`$ 500`, `$ 1,299.50`) and currency-prefixed tokens
# (`$ €100`) are money, not commands, and must not fabricate a finding.
_AMOUNT_TOKEN: Final = re.compile(r"^\d+(?:[.,]\d+)*$")
_CURRENCY_PREFIX: Final = re.compile(r"^[$€£¥₩₹¢]")


class DemoRecordingError(Exception):
    """Base error for the opt-in demo recording pipeline."""


class DemoDependencyError(DemoRecordingError):
    """A required opt-in tool (asciinema or agg) is not installed."""


class DemoScriptError(DemoRecordingError):
    """The demo script path or bytes do not satisfy the demo archive contract."""


class DemoOutputError(DemoRecordingError):
    """An output path does not satisfy the role-demo asset path contract."""


class DemoExecutionError(DemoRecordingError):
    """A subprocess step (recording or rendering) failed."""


class DemoDeterminismError(DemoRecordingError):
    """agg rendered the same cast to different bytes across two runs."""


class DemoApprovalError(DemoRecordingError):
    """The demo script is not covered by its demo-envelope approval tier."""


class DemoReviewError(DemoRecordingError):
    """The recorded capture failed the content review gate (leakage or fabrication)."""


@dataclass(frozen=True, slots=True)
class DemoTools:
    """Resolved opt-in tool executables."""

    asciinema: str
    agg: str


@dataclass(frozen=True, slots=True)
class DemoArtifacts:
    """Role-demo artifacts with the exact SHA-256 a manifest must declare."""

    cast_path: Path
    gif_path: Path
    cast_sha256: str
    gif_sha256: str


@dataclass(frozen=True, slots=True)
class DeterminismProof:
    """Result of rendering one cast twice with agg."""

    cast_sha256: str
    first_sha256: str
    second_sha256: str
    identical: bool


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise DemoDependencyError(
            f"opt-in demo recording requires {name}; install it with "
            "'brew install asciinema agg' or your package manager — recording "
            "is opt-in and static SVG remains the default"
        )
    return path


def require_tools() -> DemoTools:
    """Resolve the opt-in tools or raise DemoDependencyError with a hint."""
    return DemoTools(asciinema=_require_tool("asciinema"), agg=_require_tool("agg"))


def _require_output_path(path: Path, suffix: str, label: str) -> None:
    if not _DEMO_ASSET_PATH.search(path.as_posix()) or path.suffix != suffix:
        raise DemoOutputError(
            f"{label} must be a role-demo asset under "
            f"assets/readme-showcase/<locale>/ with .{suffix} suffix: {path}"
        )


def _require_demo_script(path: Path) -> None:
    if (
        path.suffix not in {".cast", ".sh", ".txt"}
        or not _DEMO_SCRIPT_PATH.search(path.as_posix())
    ):
        raise DemoScriptError(
            f"demo script must be an archived script or cast under demo/ "
            f"with .cast, .sh, or .txt suffix: {path}"
        )


def _is_valid_cast(raw: bytes) -> bool:
    """True when the first line is a cast header object with an integer version."""
    first_line = raw.split(b"\n", 1)[0]
    try:
        header = json.loads(first_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(header, dict) and type(header.get("version")) is int


def _read_limited(
    path: Path, maximum: int, error_type: type[DemoRecordingError]
) -> bytes:
    try:
        return read_regular_bytes(path, maximum=maximum)
    except ContractError as exc:
        raise error_type(str(exc)) from exc


def _run_process(command: list[str], label: str, *, cwd: Path | None = None) -> None:
    run_kwargs: dict[str, object] = {}
    if cwd is not None:
        run_kwargs["cwd"] = cwd
    try:
        subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=MAX_DEMO_SUBPROCESS_SECONDS,
            **run_kwargs,
        )
    except subprocess.TimeoutExpired:
        raise DemoExecutionError(
            f"{label} timed out after {MAX_DEMO_SUBPROCESS_SECONDS} seconds"
        ) from None
    except subprocess.CalledProcessError:
        raise DemoExecutionError(f"{label} failed") from None
    except FileNotFoundError:
        raise DemoExecutionError(
            f"{label} failed: executable or working directory not found"
        ) from None


def agg_command(agg_path: str, fps: int, theme: str, cast: Path, output: Path) -> list[str]:
    """Deterministic agg render command shared by recording and verification."""
    return [
        agg_path,
        "--quiet",
        "--theme",
        theme,
        "--idle-time-limit",
        IDLE_TIME_LIMIT_SECONDS,
        "--last-frame-duration",
        LAST_FRAME_DURATION_SECONDS,
        "--fps-cap",
        str(fps),
        "--speed",
        "1",
        str(cast),
        str(output),
    ]


def _extract_commands(script_text: str) -> list[str]:
    """Distinct leading command tokens of a bash demo script, in order.

    Shebang, comment, `set`, `export`, `cd`, and `source` lines are control
    scaffolding, not demo commands; unparsable lines are skipped. The result
    feeds the demo-envelope tier check, where a command that is neither
    auto-approved nor already permitted by the client permission system is
    routed to per-command approval.
    """
    commands: list[str] = []
    seen: set[str] = set()
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("set ", "export ", "cd ", "source ", ". ")):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if not tokens:
            continue
        command = tokens[0]
        if command not in seen:
            seen.add(command)
            commands.append(command)
    return commands


def demo_approval_gate(
    envelope_payload: dict[str, object],
    *,
    permitted_commands: collections.abc.Iterable[str] = (),
) -> Callable[[Path], None]:
    """Build the approval_check callback from a demo-envelope.v1 payload.

    The envelope binds the demo script (archived path name + SHA-256) and
    declares the approval tier: `full` approves the whole script, while
    `read-only-auto` and `sandbox` auto-approve only the declared read-only
    command list. Commands the client permission system has already allowed
    (`permitted_commands`) skip the envelope — the recording still runs and
    the script is archived; every other command is reported for per-command
    approval (Codex permission fallback).
    """
    try:
        envelope = validate_demo_envelope_v1(envelope_payload)
    except ContractError as exc:
        raise DemoApprovalError(f"demo envelope is invalid: {exc}") from exc
    granularity = envelope["granularity"]
    auto_approved = frozenset(envelope.get("auto_approved") or ())
    permitted = frozenset(permitted_commands)
    bound_name = PurePosixPath(envelope["demo_script"]["path"]).name
    bound_sha256 = envelope["demo_script"]["sha256"]

    def check(script: Path) -> None:
        if script.name != bound_name:
            raise DemoApprovalError(
                "demo envelope binds a different script: "
                f"{script.name} != {bound_name}"
            )
        script_bytes = script.read_bytes()
        if hashlib.sha256(script_bytes).hexdigest() != bound_sha256:
            raise DemoApprovalError(
                "demo script bytes differ from the approval envelope sha256"
            )
        if granularity == "full":
            return
        unapproved = [
            command
            for command in _extract_commands(script_bytes.decode("utf-8"))
            if command not in auto_approved and command not in permitted
        ]
        if unapproved:
            raise DemoApprovalError(
                "demo script commands need per-command approval: "
                + ", ".join(unapproved)
                + " — approve each individually (Codex permission fallback) "
                "or extend the envelope"
            )

    return check


def _resolve_sandbox_cwd(sandbox_dir: str, workspace: Path) -> Path:
    """Resolve a sandbox envelope's sandbox_dir against the workspace root.

    The envelope validator already guarantees sandbox_dir is a safe relative
    POSIX path (no absolute path, `~`, or `..` parts); this runtime re-check
    defends the resolved boundary anyway: a workspace-relative path that
    escapes the workspace root (for example through a symlink) raises a typed
    DemoApprovalError instead of executing outside the sandbox.
    """
    root = workspace.expanduser().resolve()
    candidate = (root / sandbox_dir).resolve()
    if not candidate.is_relative_to(root):
        raise DemoApprovalError(
            "sandbox_dir must stay inside the workspace: "
            f"{sandbox_dir} resolves to {candidate}, outside {root}"
        )
    return candidate


def _decode_capture_bytes(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _read_capture_text(source: Path | bytes, label: str, maximum: int) -> str:
    if isinstance(source, Path):
        try:
            raw = read_regular_bytes(source, maximum=maximum)
        except ContractError as exc:
            raise DemoReviewError(f"demo capture {label} is unreadable: {exc}") from exc
    else:
        raw = source
    return _decode_capture_bytes(raw)


def _gif_visible_text(gif_bytes: bytes) -> str:
    """Text-like ASCII runs from a GIF (comment extensions, metadata)."""
    return b"\n".join(_PRINTABLE_RUN.findall(gif_bytes)).decode("ascii", errors="ignore")


def _evidence_capability_commands(evidence: Mapping[str, object]) -> set[str]:
    """Command names the repository evidence graph proves exist."""
    commands: set[str] = set()
    facts = evidence.get("facts")
    if not isinstance(facts, list):
        return commands
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        kind = fact.get("kind")
        key = fact.get("semantic_key")
        if kind == "cli-entrypoint" and isinstance(key, str):
            for prefix in ("python-script:", "node-bin:"):
                if key.startswith(prefix):
                    name = key[len(prefix) :]
                    if name:
                        commands.add(name)
                    break
            else:
                if key == "javascript-shebang":
                    commands.add("node")
                elif key == "python-main-guard":
                    commands.add("python")
        elif kind == "command-observation":
            value = fact.get("value")
            if isinstance(value, Mapping):
                command = value.get("command")
                if isinstance(command, str):
                    try:
                        tokens = shlex.split(command)
                    except ValueError:
                        continue
                    if tokens:
                        commands.add(tokens[0])
    return commands


def _cast_output_text(cast_text: str) -> str:
    """The output a cast shows: text payloads of its `"o"` events, in order."""
    parts: list[str] = []
    for line in cast_text.splitlines():
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(payload, list) and len(payload) >= 3 and isinstance(payload[2], str):
            parts.append(payload[2])
    return "\n".join(parts)


def _claimed_commands(cast_text: str) -> list[str]:
    """Distinct `$ command` prompt lines the capture itself shows, in order."""
    claimed: list[str] = []
    seen: set[str] = set()
    for line in cast_text.splitlines():
        match = re.match(r"\s*\$\s+(\S+)", line.strip())
        if not match:
            continue
        command = match.group(1)
        if _AMOUNT_TOKEN.match(command) or _CURRENCY_PREFIX.match(command):
            continue
        if command not in seen:
            seen.add(command)
            claimed.append(command)
    return claimed


def _snippet(match: re.Match[str]) -> str:
    text = match.group(0)
    return text if len(text) <= 120 else text[:117] + "..."


def review_demo_capture(
    cast: Path | bytes,
    gif: Path | bytes,
    evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Review a demo capture for environment leakage and fabrication.

    The leakage check always runs over the cast output and any text visible in
    the GIF: absolute user home paths, `~`/`$HOME` references, email-style
    identities, and tokens or secrets fail the gate with `leak-path`,
    `leak-identity`, or `leak-credential` findings. When `evidence` (a
    repository-evidence graph) is provided, commands the capture shows as
    `$ command` prompt lines must be backed by a `cli-entrypoint` or
    `command-observation` fact or be a generic shell utility; anything else is
    a `fabrication` finding. Returns {"pass": bool, "findings": [...]} with one
    finding per kind.
    """
    cast_text = _read_capture_text(cast, "cast", MAX_DEMO_CAST_BYTES)
    gif_raw = _read_capture_text(gif, "gif", MAX_DEMO_GIF_BYTES)
    gif_text = _gif_visible_text(gif_raw.encode("utf-8", errors="ignore"))
    cast_output = _cast_output_text(cast_text)
    scanned = f"{cast_output}\n{gif_text}"

    findings: list[dict[str, object]] = []
    for kind, pattern in _LEAK_PATTERNS:
        match = pattern.search(scanned)
        if match is not None:
            findings.append({"kind": kind, "detail": _snippet(match)})

    if evidence is not None:
        capability = _GENERIC_SHELL_COMMANDS | _evidence_capability_commands(evidence)
        for command in _claimed_commands(cast_output):
            if command not in capability:
                findings.append(
                    {
                        "kind": "fabrication",
                        "detail": (
                            f"demo claims command '{command}' which repository "
                            "evidence does not support"
                        ),
                    }
                )
                break

    return {"pass": not findings, "findings": findings}


def _format_review_findings(findings: Iterable[Mapping[str, object]]) -> str:
    parts = [f"{finding.get('kind')}: {finding.get('detail')}" for finding in findings]
    return "demo capture review failed: " + "; ".join(parts)


def record_demo(
    demo_script: Path,
    out_cast: Path,
    out_gif: Path,
    *,
    approval_check: Callable[[Path], None] | None = None,
    demo_envelope: dict[str, object] | None = None,
    permitted_commands: Iterable[str] = (),
    evidence: Mapping[str, object] | None = None,
    tools: DemoTools | None = None,
    fps: int = DEFAULT_FPS,
    theme: str = DEFAULT_THEME,
    workspace: Path | None = None,
) -> DemoArtifacts:
    """Record a demo script as a cast and render a deterministic GIF.

    `demo_script` must be an archived `.sh`/`.txt` script or `.cast` recording
    under `demo/`; outputs must be role-demo assets under
    `assets/readme-showcase/<locale>/` (Task 4.1 contract). `approval_check`
    (the Task 4.3 approval-envelope seam) runs before any script execution and
    aborts the recording by raising; `.cast` inputs are never executed and so
    never trigger the check. When `demo_envelope` (a demo-envelope.v1 payload)
    is provided, it replaces the raw callback with the envelope gate, whose
    tier is enforced against `permitted_commands` — commands the client
    permission system already allowed skip the envelope while the script is
    still recorded and archived. When the envelope granularity is `sandbox`,
    the asciinema recording subprocess runs with cwd set to `sandbox_dir`
    resolved against `workspace` (default: the current working directory); a
    `sandbox_dir` that escapes the workspace at runtime raises
    DemoApprovalError before any execution. After rendering, the Task 4.4
    content review gate (`review_demo_capture`) runs over the capture: the
    leakage check is always enforced, and the no-fabrication capability check
    runs when `evidence` (a repository-evidence graph) is provided; a failing
    review raises DemoReviewError before any artifacts are returned. The
    returned DemoArtifacts carries the exact SHA-256 values the producer must
    declare in the asset manifest.
    """
    script = demo_script.expanduser()
    _require_demo_script(script)
    if not script.is_file():
        raise DemoScriptError(f"demo script does not exist: {script}")
    cast_output = out_cast.expanduser()
    gif_output = out_gif.expanduser()
    _require_output_path(cast_output, ".cast", "cast output")
    _require_output_path(gif_output, ".gif", "gif output")
    tools = tools or require_tools()
    sandbox_cwd: Path | None = None
    if demo_envelope is not None:
        approval_check = demo_approval_gate(
            demo_envelope, permitted_commands=permitted_commands
        )
        if demo_envelope.get("granularity") == "sandbox":
            sandbox_dir = demo_envelope.get("sandbox_dir")
            if not isinstance(sandbox_dir, str):
                raise DemoApprovalError(
                    "sandbox granularity requires sandbox_dir"
                )
            sandbox_cwd = _resolve_sandbox_cwd(
                sandbox_dir, workspace if workspace is not None else Path.cwd()
            )

    cast_bytes: bytes
    match script.suffix:
        case ".cast":
            cast_bytes = _read_limited(
                script, MAX_DEMO_CAST_BYTES, DemoScriptError
            )
            if not _is_valid_cast(cast_bytes):
                raise DemoScriptError(
                    f"demo script is not a valid asciinema cast: {script}"
                )
            write_bytes_atomic(cast_output, cast_bytes)
        case ".sh" | ".txt":
            if approval_check is not None:
                approval_check(script)
            cast_output.parent.mkdir(parents=True, exist_ok=True)
            _run_process(
                [
                    tools.asciinema,
                    "rec",
                    str(cast_output),
                    "--overwrite",
                    "--quiet",
                    "--idle-time-limit",
                    IDLE_TIME_LIMIT_SECONDS,
                    "--command",
                    f"bash {shlex.quote(str(script))}",
                ],
                "asciinema recording",
                cwd=sandbox_cwd,
            )
            cast_bytes = _read_limited(
                cast_output, MAX_DEMO_CAST_BYTES, DemoExecutionError
            )
            if not _is_valid_cast(cast_bytes):
                raise DemoExecutionError(
                    f"asciinema produced an invalid cast: {cast_output}"
                )
        case unreachable:
            assert_never(unreachable)

    gif_output.parent.mkdir(parents=True, exist_ok=True)
    _run_process(
        agg_command(tools.agg, fps, theme, cast_output, gif_output),
        "agg GIF rendering",
    )
    gif_bytes = _read_limited(gif_output, MAX_DEMO_GIF_BYTES, DemoExecutionError)
    review = review_demo_capture(cast_output, gif_output, evidence=evidence)
    if not review["pass"]:
        raw_findings = review["findings"]
        findings = (
            [f for f in raw_findings if isinstance(f, Mapping)]
            if isinstance(raw_findings, list)
            else []
        )
        raise DemoReviewError(_format_review_findings(findings))
    return DemoArtifacts(
        cast_path=cast_output,
        gif_path=gif_output,
        cast_sha256=hashlib.sha256(cast_bytes).hexdigest(),
        gif_sha256=hashlib.sha256(gif_bytes).hexdigest(),
    )


def verify_determinism(
    cast: Path,
    *,
    workspace: Path,
    agg_path: str,
    fps: int = DEFAULT_FPS,
    theme: str = DEFAULT_THEME,
) -> DeterminismProof:
    """Render the same cast twice and compare SHA-256; raise on divergence."""
    cast_bytes = _read_limited(cast, MAX_DEMO_CAST_BYTES, DemoExecutionError)
    first = workspace / "demo-verify-1.gif"
    second = workspace / "demo-verify-2.gif"
    try:
        _run_process(
            agg_command(agg_path, fps, theme, cast, first),
            "agg determinism render 1",
        )
        _run_process(
            agg_command(agg_path, fps, theme, cast, second),
            "agg determinism render 2",
        )
        first_raw = _read_limited(first, MAX_DEMO_GIF_BYTES, DemoExecutionError)
        second_raw = _read_limited(second, MAX_DEMO_GIF_BYTES, DemoExecutionError)
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
    first_hash = hashlib.sha256(first_raw).hexdigest()
    second_hash = hashlib.sha256(second_raw).hexdigest()
    if first_hash != second_hash:
        raise DemoDeterminismError(
            "agg rendered the same cast to different bytes: "
            f"{first_hash} != {second_hash}"
        )
    return DeterminismProof(
        cast_sha256=hashlib.sha256(cast_bytes).hexdigest(),
        first_sha256=first_hash,
        second_sha256=second_hash,
        identical=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record a demo script with asciinema and render a deterministic "
            "GIF with agg (opt-in)."
        )
    )
    parser.add_argument(
        "demo_script",
        type=Path,
        help="archived demo script under demo/ (.sh/.txt) or cast (.cast)",
    )
    parser.add_argument(
        "out_cast",
        type=Path,
        help="role-demo cast output under assets/readme-showcase/<locale>/",
    )
    parser.add_argument(
        "out_gif",
        type=Path,
        help="role-demo GIF output under assets/readme-showcase/<locale>/",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-render the cast twice and compare SHA-256 for determinism",
    )
    parser.add_argument(
        "--envelope",
        type=Path,
        help="demo-envelope.v1 JSON declaring the script's approval tier",
    )
    parser.add_argument(
        "--permitted-command",
        action="append",
        default=[],
        help="command already allowed by the client permission system (repeatable)",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="repository-evidence.v2 JSON enabling the no-fabrication capability review",
    )
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="agg fps cap")
    parser.add_argument(
        "--theme", default=DEFAULT_THEME, help="agg color theme (default: asciinema)"
    )
    return parser.parse_args(argv)


def _read_envelope(path: Path) -> dict[str, object]:
    try:
        raw = read_regular_bytes(path, maximum=64 * 1024)
    except ContractError as exc:
        raise DemoApprovalError(f"demo envelope is unreadable: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoApprovalError(f"demo envelope is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DemoApprovalError(f"demo envelope must be a JSON object: {path}")
    return payload


def _read_evidence(path: Path) -> dict[str, object]:
    try:
        raw = read_regular_bytes(path, maximum=MAX_DEMO_EVIDENCE_BYTES)
    except ContractError as exc:
        raise DemoReviewError(f"demo review evidence is unreadable: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoReviewError(f"demo review evidence is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DemoReviewError(f"demo review evidence must be a JSON object: {path}")
    try:
        return validate_evidence_graph(payload)
    except ContractError as exc:
        raise DemoReviewError(f"demo review evidence failed validation: {exc}") from exc


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        tools = require_tools()
        envelope = (
            _read_envelope(args.envelope) if args.envelope is not None else None
        )
        evidence = (
            _read_evidence(args.evidence) if args.evidence is not None else None
        )
        artifacts = record_demo(
            args.demo_script,
            args.out_cast,
            args.out_gif,
            demo_envelope=envelope,
            permitted_commands=args.permitted_command,
            evidence=evidence,
            tools=tools,
            fps=args.fps,
            theme=args.theme,
        )
        print(f"CAST: {artifacts.cast_path}")
        print(f"GIF: {artifacts.gif_path}")
        if args.verify:
            with tempfile.TemporaryDirectory(prefix="readme-demo-verify-") as temporary:
                proof = verify_determinism(
                    artifacts.cast_path,
                    workspace=Path(temporary),
                    agg_path=tools.agg,
                    fps=args.fps,
                    theme=args.theme,
                )
            print(f"Determinism: identical={proof.identical}")
            print(f"cast sha256: {proof.cast_sha256}")
            print(f"render 1 sha256: {proof.first_sha256}")
            print(f"render 2 sha256: {proof.second_sha256}")
    except DemoRecordingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
