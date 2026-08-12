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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

if __package__ and __package__.startswith("skill."):
    from skill.scripts.pipeline_contracts import (
        ContractError,
        read_regular_bytes,
        write_bytes_atomic,
    )
else:  # The installed Skill runs this file directly from its scripts directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.pipeline_contracts import (
        ContractError,
        read_regular_bytes,
        write_bytes_atomic,
    )


MAX_DEMO_CAST_BYTES: Final = 64 * 1024 * 1024
MAX_DEMO_GIF_BYTES: Final = 64 * 1024 * 1024
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


def _run_process(command: list[str], label: str) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=MAX_DEMO_SUBPROCESS_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise DemoExecutionError(
            f"{label} timed out after {MAX_DEMO_SUBPROCESS_SECONDS} seconds"
        ) from None
    except subprocess.CalledProcessError:
        raise DemoExecutionError(f"{label} failed") from None


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


def record_demo(
    demo_script: Path,
    out_cast: Path,
    out_gif: Path,
    *,
    approval_check: Callable[[Path], None] | None = None,
    tools: DemoTools | None = None,
    fps: int = DEFAULT_FPS,
    theme: str = DEFAULT_THEME,
) -> DemoArtifacts:
    """Record a demo script as a cast and render a deterministic GIF.

    `demo_script` must be an archived `.sh`/`.txt` script or `.cast` recording
    under `demo/`; outputs must be role-demo assets under
    `assets/readme-showcase/<locale>/` (Task 4.1 contract). `approval_check`
    (the Task 4.3 approval-envelope seam) runs before any script execution and
    aborts the recording by raising; `.cast` inputs are never executed and so
    never trigger the check. The returned DemoArtifacts carries the exact
    SHA-256 values the producer must declare in the asset manifest.
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
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="agg fps cap")
    parser.add_argument(
        "--theme", default=DEFAULT_THEME, help="agg color theme (default: asciinema)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        tools = require_tools()
        artifacts = record_demo(
            args.demo_script,
            args.out_cast,
            args.out_gif,
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
