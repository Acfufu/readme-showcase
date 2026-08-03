from __future__ import annotations

import html
import os
import re
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..orchestration.workspace import RunWorkspace
from .report import (
    MAX_PREVIEW_INPUT_BYTES,
    PreviewInputSnapshot,
    assert_preview_inputs_current,
    build_preview_snapshot,
)


_ASSET_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"})
_ACTIVE_SVG_ELEMENTS = frozenset({"script", "foreignobject", "iframe", "object", "embed", "audio", "video"})
_EXTERNAL_REFERENCE = re.compile(r"(?:https?:|//|data:|javascript:)", re.IGNORECASE)
_ACTIVE_STYLE = re.compile(r"(?:url\s*\(|@import|expression\s*\(|javascript:)", re.IGNORECASE)


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].casefold()


def _validate_svg(raw: bytes) -> None:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ContractError("E_PREVIEW_PATH", "preview SVG declarations are unsupported")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ContractError("E_PREVIEW_PATH", "preview SVG must be well-formed") from exc
    if _local_name(root.tag) != "svg":
        raise ContractError("E_PREVIEW_PATH", "preview SVG root must be svg")
    for element in root.iter():
        if _local_name(element.tag) in _ACTIVE_SVG_ELEMENTS:
            raise ContractError("E_PREVIEW_PATH", "preview SVG contains active content")
        for raw_name, value in element.attrib.items():
            name = _local_name(raw_name)
            if name.startswith("on") or name in {"src", "formaction"}:
                raise ContractError("E_PREVIEW_PATH", "preview SVG contains an active attribute")
            if _EXTERNAL_REFERENCE.search(value) or _ACTIVE_STYLE.search(value):
                raise ContractError("E_PREVIEW_PATH", "preview SVG contains an external reference")
        if _local_name(element.tag) == "style" and _ACTIVE_STYLE.search(element.text or ""):
            raise ContractError("E_PREVIEW_PATH", "preview SVG contains an active style")


def _collect_assets(
    workspace: RunWorkspace,
    snapshot: PreviewInputSnapshot,
) -> dict[str, bytes]:
    root = workspace.root / "stages/05-candidate/assets"
    try:
        info = root.lstat()
    except FileNotFoundError:
        return {"assets/README.txt": b"No candidate assets were provided.\n"}
    except OSError as exc:
        raise ContractError("E_PREVIEW_PATH", "cannot inspect candidate assets") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_PREVIEW_PATH", "candidate assets must be a real directory")
    output: dict[str, bytes] = {}
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names.sort(key=os.fsencode)
        files.sort(key=os.fsencode)
        for name in names:
            child = base / name
            child_info = child.lstat()
            if not stat.S_ISDIR(child_info.st_mode) or stat.S_ISLNK(child_info.st_mode):
                raise ContractError("E_PREVIEW_PATH", "candidate assets contain an unsafe directory")
        for name in files:
            source = base / name
            source_info = source.lstat()
            if not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode):
                raise ContractError("E_PREVIEW_PATH", "candidate assets contain a symlink or special file")
            relative = source.relative_to(root).as_posix()
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts or pure.suffix.casefold() not in _ASSET_SUFFIXES:
                raise ContractError("E_PREVIEW_PATH", "candidate asset path or type is not allowlisted")
            if source_info.st_size > MAX_PREVIEW_INPUT_BYTES:
                raise ContractError("E_PREVIEW_PATH", "candidate asset exceeds preview size limit")
            raw = snapshot.read(source) or b""
            if pure.suffix.casefold() == ".svg":
                _validate_svg(raw)
            output[f"assets/{relative}"] = raw
    return output or {"assets/README.txt": b"No candidate assets were provided.\n"}


_CSS = """*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#e8edf8;font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}main{max-width:1180px;margin:auto;padding:24px}h1,h2{font-family:ui-sans-serif,system-ui,sans-serif}section{background:#121a2e;border:1px solid #2c3d61;border-radius:12px;margin:18px 0;padding:18px}pre{background:#080d19;border-radius:8px;overflow:auto;padding:16px;white-space:pre-wrap;word-break:break-word}.mobile-preview{max-width:375px;border:8px solid #33466d;border-radius:24px;margin:auto}.meta{color:#9cb0d4}@media(max-width:640px){main{padding:10px}section{padding:10px;margin:10px 0}}"""


def _document(title: str, source: str) -> bytes:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main><h1>{html.escape(title)}</h1><pre>{html.escape(source, quote=True)}</pre></main></body></html>\n"
    ).encode("utf-8")


def _index(report: dict[str, Any], readmes: dict[str, str]) -> bytes:
    report_text = canonical_json_bytes(report).decode("utf-8")
    diff_text = "\n".join(f"## {name}\n{value}" for name, value in report["diff"].items())
    evidence_text = canonical_json_bytes({"evidence": report["evidence"], "claims": report["claims"]}).decode("utf-8")
    evaluation_text = canonical_json_bytes({"diagnostics": report["diagnostics"], "editorial": report["editorial"], "evaluation": report["evaluation"]}).decode("utf-8")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>README preview</title><style>{_CSS}</style></head><body><main>"
        "<h1>README offline preview</h1>"
        f"<p class=\"meta\">Fixed run time: {html.escape(report['generated_at'])}</p>"
        f"<section><h2>Rendered README</h2><pre>{html.escape(readmes['README.md'], quote=True)}</pre></section>"
        f"<section><h2>Diff</h2><pre>{html.escape(diff_text, quote=True)}</pre></section>"
        f"<section><h2>Evidence and claims</h2><pre>{html.escape(evidence_text, quote=True)}</pre></section>"
        f"<section><h2>Evaluation</h2><pre>{html.escape(evaluation_text, quote=True)}</pre></section>"
        f"<section><h2>Mobile / narrow view</h2><div class=\"mobile-preview\"><pre>{html.escape(readmes['README.md'], quote=True)}</pre></div></section>"
        f"<section><h2>Canonical report</h2><pre>{html.escape(report_text, quote=True)}</pre></section>"
        "</main></body></html>\n"
    ).encode("utf-8")


def _tree_bytes(root: Path) -> dict[str, bytes] | None:
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_PREVIEW_PATH", "preview destination must be a real directory")
    output: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: os.fsencode(item.relative_to(root).as_posix())):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode)):
            raise ContractError("E_PREVIEW_PATH", "preview destination contains unsafe data")
        if stat.S_ISREG(info.st_mode):
            output[path.relative_to(root).as_posix()] = path.read_bytes()
    return output


def render_preview(workspace: RunWorkspace, manifest: dict[str, Any]) -> dict[str, object]:
    snapshot = PreviewInputSnapshot()
    assets = _collect_assets(workspace, snapshot)
    report, readmes = build_preview_snapshot(workspace, manifest, snapshot)
    files = {
        "index.html": _index(report, readmes),
        "report.json": canonical_json_bytes(report),
        "README.escaped.html": _document("README.md escaped source", readmes["README.md"]),
        "README_zh.escaped.html": _document("README_zh.md escaped source", readmes["README_zh.md"]),
        **assets,
    }
    output_root = workspace.root / "output"
    output_info = output_root.lstat()
    if not stat.S_ISDIR(output_info.st_mode) or stat.S_ISLNK(output_info.st_mode):
        raise ContractError("E_PREVIEW_PATH", "preview output root must be a real directory")
    temporary = Path(tempfile.mkdtemp(prefix=".preview.", suffix=".tmp", dir=output_root))
    try:
        for relative, raw in sorted(files.items()):
            destination = temporary.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        destination = output_root / "preview"
        existing = _tree_bytes(destination)
        assert_preview_inputs_current(workspace, manifest, snapshot)
        if existing is not None:
            if existing != files:
                raise ContractError("E_PREVIEW_EXISTS", "preview output already exists with different bytes")
            shutil.rmtree(temporary)
        else:
            os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "schema_version": 1,
        "status": "complete",
        "preview": "output/preview/index.html",
        "report_sha256": canonical_sha256(report),
        "surface_count": len(files),
    }
