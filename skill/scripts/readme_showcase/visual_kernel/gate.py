"""Screenshot gate orchestration: render, check, report.

Canonical JSON output (write_canonical_json_atomic: compact, sort_keys,
atomic replace, fsync). Hard findings are sorted by (code, message). Missing
optional dependencies (rasterizer, Pillow, node/resvg-js) NEVER hard-fail:
they produce E_SCREENSHOT_*_SKIP notes in aesthetic_findings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...pipeline_contracts import ContractError, write_canonical_json_atomic
from .checks import (
    check_clipping_bbox,
    check_clipping_pixels,
    check_readability_at_360,
    check_svg_contrast,
    check_svg_contract,
)
from .raster import render_svg

REPORT_NAME = "screenshot-gate-report.v1.json"

_FINDING_FIELDS = frozenset({"code", "message"})
_REPORT_FIELDS = frozenset(
    {"schema_version", "status", "hard_gate", "aesthetic_findings", "screenshots"}
)


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _is_skip(message: str) -> bool:
    return message.startswith("SKIPPED:")


def validate_screenshot_gate_report_v1(value: Any) -> dict[str, Any]:
    """Validate a closed Screenshot Gate Report v1 value; raises ContractError.

    The parity index resolves this as the schema's python_validator, so its
    error codes must match the invalid-fixture case codes in
    tests/fixtures/contracts/screenshot-gate-report-v1.invalid.json.
    """
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", "screenshot gate report must be an object")
    unknown = sorted(set(value) - _REPORT_FIELDS)
    if unknown:
        raise ContractError(
            "E_SCHEMA_UNKNOWN_FIELD",
            f"screenshot gate report contains unknown field: {unknown[0]}",
        )
    missing = sorted(_REPORT_FIELDS - set(value))
    if missing:
        raise ContractError(
            "E_SCHEMA_MISSING_FIELD",
            f"screenshot gate report is missing field: {missing[0]}",
        )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "screenshot gate report requires schema_version 1")
    if value["status"] not in ("pass", "fail"):
        raise ContractError("E_SCHEMA_VALUE", "screenshot gate report status must be pass or fail")
    hard = value["hard_gate"]
    if not isinstance(hard, dict) or hard.get("status") not in ("pass", "fail"):
        raise ContractError(
            "E_SCHEMA_VALUE", "screenshot gate report hard_gate.status must be pass or fail"
        )

    def _check_findings(section: Any, context: str) -> None:
        if not isinstance(section, list):
            raise ContractError("E_SCHEMA_TYPE", f"{context} must be an array")
        for item in section:
            if not isinstance(item, dict) or set(item) != _FINDING_FIELDS:
                raise ContractError(
                    "E_SCHEMA_TYPE", f"{context} entries must be {{code, message}} objects"
                )
            if (not isinstance(item["code"], str) or not item["code"]
                    or not isinstance(item["message"], str) or not item["message"]):
                raise ContractError(
                    "E_SCHEMA_VALUE", f"{context} entries must have non-empty code and message"
                )
        ordered = sorted(section, key=lambda item: (item["code"], item["message"]))
        if section != ordered:
            raise ContractError(
                "E_SCHEMA_VALUE", f"{context} must be canonically sorted by (code, message)"
            )

    _check_findings(hard.get("findings"), "screenshot gate report hard_gate.findings")
    _check_findings(value["aesthetic_findings"], "screenshot gate report aesthetic_findings")
    screenshots = value["screenshots"]
    if not isinstance(screenshots, list):
        raise ContractError("E_SCHEMA_TYPE", "screenshot gate report screenshots must be an array")
    for shot in screenshots:
        if not isinstance(shot, dict) or set(shot) != {"svg", "900px", "360px"}:
            raise ContractError(
                "E_SCHEMA_TYPE", "screenshot entry must contain exactly svg, 900px, 360px"
            )
    return value


def run_screenshot_gate(svg_paths: list[str], out_dir: str, *, require_bbox: bool = False) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    hard_findings: list[dict[str, str]] = []
    aesthetic: list[dict[str, str]] = []
    screenshots: list[dict[str, str]] = []

    for svg in svg_paths:
        stem = Path(svg).stem
        contract_messages = check_svg_contract(svg)
        hard_findings.extend(
            _finding("E_SCREENSHOT_SVG", message) for message in contract_messages
        )
        if not contract_messages:
            # Ordering contract (T2 review): the viewBox finding from the
            # contract check is the hard gate; readability only runs on SVGs
            # whose contract passed (it silently passes on malformed viewBoxes).
            hard_findings.extend(
                _finding("E_SCREENSHOT_READABILITY", message)
                for message in check_readability_at_360(svg)
            )
        aesthetic.extend(_finding("E_SCREENSHOT_CONTRAST", message) for message in check_svg_contrast(svg))

        rendered: dict[str, str] = {}
        for width in (900, 360):
            png = out / f"{stem}-{width}.png"
            try:
                render_svg(svg, str(png), width)
            except ContractError as exc:
                if exc.code == "E_RASTER_DEPENDENCY":
                    # Optional dependency: advisory skip note, NEVER a hard fail.
                    if not any(f["code"] == "E_SCREENSHOT_RASTER_SKIP" for f in aesthetic):
                        aesthetic.append(_finding("E_SCREENSHOT_RASTER_SKIP", str(exc)))
                    break
                hard_findings.append(_finding("E_SCREENSHOT_RASTER", f"{exc.code}: {exc}"))
                continue
            rendered[str(width)] = f"{stem}-{width}.png"
            for message in check_clipping_pixels(str(png)):
                if _is_skip(message):
                    aesthetic.append(_finding("E_SCREENSHOT_PIXEL_SKIP", message))
                else:
                    hard_findings.append(_finding("E_SCREENSHOT_CLIP", message))
        if rendered.get("900") and rendered.get("360"):
            screenshots.append({
                "svg": svg,
                "900px": rendered["900"],
                "360px": rendered["360"],
            })

        for message in check_clipping_bbox(svg):
            if _is_skip(message):
                if require_bbox:
                    hard_findings.append(_finding("E_SCREENSHOT_BBOX_SKIP", message))
                else:
                    aesthetic.append(_finding("E_SCREENSHOT_BBOX_SKIP", message))
            else:
                hard_findings.append(_finding("E_SCREENSHOT_CLIP", message))

    hard_findings.sort(key=lambda item: (item["code"], item["message"]))
    aesthetic.sort(key=lambda item: (item["code"], item["message"]))
    status = "fail" if hard_findings else "pass"
    report = {
        "schema_version": 1,
        "status": status,
        "hard_gate": {"status": status, "findings": hard_findings},
        "aesthetic_findings": aesthetic,
        "screenshots": screenshots,
    }
    report = validate_screenshot_gate_report_v1(report)
    write_canonical_json_atomic(out / REPORT_NAME, report)
    return report
