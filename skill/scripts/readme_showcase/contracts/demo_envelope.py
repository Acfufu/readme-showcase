from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Final

from ...pipeline_contracts import ContractError, canonical_json_bytes


DEMO_ENVELOPE_SCHEMA_VERSION: Final = 1
GRANULARITIES: Final = ("full", "read-only-auto", "sandbox")
_MAX_AUTO_APPROVED: Final = 256
_DEMO_SCRIPT_PATH: Final = re.compile(r"demo/[^/]+\.(?:sh|txt|cast)\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_AUTO_COMMAND: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_FIELDS: Final = {
    "schema_version",
    "demo_script",
    "granularity",
    "auto_approved",
    "sandbox_dir",
}
_REQUIRED_FIELDS: Final = {"schema_version", "demo_script", "granularity"}
_SCRIPT_FIELDS: Final = {"path", "sha256"}


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _closed_object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    missing = sorted(fields - set(value))
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    return value


def _safe_relative_path(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        _fail("E_DEMO_ENVELOPE_BINDING", f"{context} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("~/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or len(value.encode("utf-8")) > 4096
        or path.as_posix() != value
    ):
        _fail("E_DEMO_ENVELOPE_BINDING", f"{context} must be a safe relative POSIX path")
    return value


def validate_demo_envelope_v1(payload: Any) -> dict[str, Any]:
    """Validate a demo-envelope.v1 approval document (three-tier demo gate).

    The envelope is advisory for commands the client permission system has
    already allowed; `record_demo` remains the enforcement point. `full`
    approves the whole script, `read-only-auto` auto-approves a declared
    read-only command list (writes need per-command approval), and `sandbox`
    declares the sandbox working directory the recording host must honor.
    """
    if not isinstance(payload, dict):
        _fail("E_SCHEMA_TYPE", "demo envelope v1 must be an object")
    envelope = payload
    unknown = sorted(set(envelope) - _FIELDS)
    if unknown:
        _fail(
            "E_SCHEMA_UNKNOWN_FIELD",
            f"demo envelope v1 contains unknown field: {unknown[0]}",
        )
    missing = sorted(_REQUIRED_FIELDS - set(envelope))
    if missing:
        _fail(
            "E_SCHEMA_MISSING_FIELD",
            f"demo envelope v1 is missing required field: {missing[0]}",
        )
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        _fail("E_SCHEMA_VERSION", "demo envelope requires schema_version 1")

    script = _closed_object(envelope["demo_script"], _SCRIPT_FIELDS, "demo envelope demo_script")
    path = script["path"]
    if not isinstance(path, str) or not _DEMO_SCRIPT_PATH.fullmatch(path):
        _fail(
            "E_DEMO_ENVELOPE_BINDING",
            "demo envelope demo_script.path must be an archived demo script under demo/",
        )
    sha256 = script["sha256"]
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        _fail("E_DEMO_ENVELOPE_BINDING", "demo envelope demo_script.sha256 must be a lowercase SHA-256")

    granularity = envelope["granularity"]
    if granularity not in GRANULARITIES:
        _fail("E_SCHEMA_VALUE", "demo envelope granularity is unsupported")

    has_auto = "auto_approved" in envelope
    if has_auto:
        raw_auto = envelope["auto_approved"]
        if not isinstance(raw_auto, list) or isinstance(raw_auto, (str, bytes)):
            _fail("E_SCHEMA_TYPE", "demo envelope auto_approved must be an array")
        if len(raw_auto) > _MAX_AUTO_APPROVED:
            _fail("E_SCHEMA_VALUE", f"demo envelope auto_approved must list at most {_MAX_AUTO_APPROVED} commands")
        for index, item in enumerate(raw_auto):
            if not isinstance(item, str) or not _AUTO_COMMAND.fullmatch(item):
                _fail("E_SCHEMA_VALUE", f"demo envelope auto_approved[{index}] must be a bare command name")

    has_sandbox = "sandbox_dir" in envelope
    if has_sandbox:
        _safe_relative_path(envelope["sandbox_dir"], "demo envelope sandbox_dir")

    if granularity == "full":
        if has_auto or has_sandbox:
            _fail(
                "E_DEMO_ENVELOPE_GRANULARITY",
                "full granularity approves the whole script and must not declare "
                "auto_approved or sandbox_dir",
            )
    elif granularity == "read-only-auto":
        if has_sandbox or not has_auto or not envelope["auto_approved"]:
            _fail(
                "E_DEMO_ENVELOPE_GRANULARITY",
                "read-only-auto granularity requires a non-empty auto_approved "
                "read-only command list and no sandbox_dir",
            )
    else:  # sandbox
        if not has_sandbox:
            _fail(
                "E_DEMO_ENVELOPE_GRANULARITY",
                "sandbox granularity requires sandbox_dir",
            )
    canonical_json_bytes(envelope)
    return envelope


__all__ = [
    "DEMO_ENVELOPE_SCHEMA_VERSION",
    "GRANULARITIES",
    "validate_demo_envelope_v1",
]
