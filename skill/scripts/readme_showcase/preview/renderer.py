from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.evidence import validate_evidence_graph
from ..evidence import adapt_v1_repository_evidence
from ..orchestration.workspace import RunWorkspace
from ..visual_kernel.model import validate_visual_spec
from ..visual_kernel.reader import load_compiled_visual
from .report import (
    MAX_PREVIEW_INPUT_BYTES,
    PreviewInputSnapshot,
    _attempt_path,
    _canonical_object,
    assert_preview_inputs_current,
    build_preview_snapshot,
)
from .interaction import project_interaction_preview


_ASSET_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"})
_ACTIVE_SVG_ELEMENTS = frozenset({"script", "foreignobject", "iframe", "object", "embed", "audio", "video"})
_EXTERNAL_REFERENCE = re.compile(r"(?:https?:|//|data:|javascript:)", re.IGNORECASE)
_ACTIVE_STYLE = re.compile(r"(?:url\s*\(|@import|expression\s*\(|javascript:)", re.IGNORECASE)
_LOCAL_SVG_REFERENCE = re.compile(rb"url\(\s*#[A-Za-z_][A-Za-z0-9_.:-]*\s*\)", re.IGNORECASE)
_MAX_PREVIEW_TREE_DEPTH = 16
_MAX_PREVIEW_TREE_ENTRIES = 10_000
_MAX_PREVIEW_TREE_BYTES = 64 * 1024 * 1024
_MAX_PREVIEW_FILE_BYTES = MAX_PREVIEW_INPUT_BYTES


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


def _validate_compiled_svg(raw: bytes) -> None:
    """Apply the preview SVG gate while allowing bounded local marker refs."""

    # Compiled scenes use SVG's local ``url(#id)`` references for arrow
    # markers.  The legacy candidate gate intentionally rejects every url()
    # expression, so normalize only this closed local form before reusing it;
    # any external/data/javascript URL remains visible to ``_validate_svg``.
    normalized = _LOCAL_SVG_REFERENCE.sub(b"#local-reference", raw)
    _validate_svg(normalized)


def _collect_assets(
    workspace: RunWorkspace,
    snapshot: PreviewInputSnapshot,
) -> dict[str, bytes]:
    root = workspace.root / "stages/05-candidate/assets"
    scanned = _enumerate_preview_tree(root, context="candidate assets")
    if scanned is None:
        return {"assets/README.txt": b"No candidate assets were provided.\n"}
    root_fd, files = scanned
    output: dict[str, bytes] = {}
    try:
        for relative, parts, source_info in sorted(files, key=lambda item: os.fsencode(item[0])):
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts or pure.suffix.casefold() not in _ASSET_SUFFIXES:
                raise ContractError("E_PREVIEW_PATH", "candidate asset path or type is not allowlisted")
            source = root.joinpath(*parts)
            snapshot_raw = snapshot.read(source) or b""
            raw = _read_preview_relative(root_fd, parts, source_info, context="candidate assets")
            if raw != snapshot_raw:
                raise ContractError("E_PREVIEW_STALE", "candidate asset bytes changed during preview rendering")
            if pure.suffix.casefold() == ".svg":
                _validate_svg(raw)
            output[f"assets/{relative}"] = raw
    finally:
        os.close(root_fd)
    return output or {"assets/README.txt": b"No candidate assets were provided.\n"}


_CSS = """*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#e8edf8;font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}main{max-width:1180px;margin:auto;padding:24px}h1,h2{font-family:ui-sans-serif,system-ui,sans-serif}section{background:#121a2e;border:1px solid #2c3d61;border-radius:12px;margin:18px 0;padding:18px}pre{background:#080d19;border-radius:8px;overflow:auto;padding:16px;white-space:pre-wrap;word-break:break-word}.mobile-preview{max-width:375px;border:8px solid #33466d;border-radius:24px;margin:auto}.meta{color:#9cb0d4}@media(max-width:640px){main{padding:10px}section{padding:10px;margin:10px 0}}"""


def _document(title: str, source: str) -> bytes:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main><h1>{html.escape(title)}</h1><pre>{html.escape(source, quote=True)}</pre></main></body></html>\n"
    ).encode("utf-8")


def _index(
    report: dict[str, Any],
    readmes: dict[str, str],
    *,
    extra_css: str = "",
    extra_sections: str = "",
) -> bytes:
    primary_path = report["mobile"]["source"]
    primary = readmes[primary_path]
    report_text = canonical_json_bytes(report).decode("utf-8")
    diff_text = "\n".join(f"## {name}\n{value}" for name, value in report["diff"].items())
    evidence_text = canonical_json_bytes({"evidence": report["evidence"], "claims": report["claims"]}).decode("utf-8")
    evaluation_text = canonical_json_bytes({"diagnostics": report["diagnostics"], "editorial": report["editorial"], "evaluation": report["evaluation"]}).decode("utf-8")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>README preview</title><style>{_CSS}{extra_css}</style></head><body><main>"
        "<h1>README offline preview</h1>"
        f"<p class=\"meta\">Fixed run time: {html.escape(report['generated_at'])}</p>"
        f"<section><h2>Rendered README</h2><pre>{html.escape(primary, quote=True)}</pre></section>"
        f"<section><h2>Diff</h2><pre>{html.escape(diff_text, quote=True)}</pre></section>"
        f"<section><h2>Evidence and claims</h2><pre>{html.escape(evidence_text, quote=True)}</pre></section>"
        f"<section><h2>Evaluation</h2><pre>{html.escape(evaluation_text, quote=True)}</pre></section>"
        f"{extra_sections}"
        f"<section><h2>Mobile / narrow view</h2><div class=\"mobile-preview\"><pre>{html.escape(primary, quote=True)}</pre></div></section>"
        f"<section><h2>Canonical report</h2><pre>{html.escape(report_text, quote=True)}</pre></section>"
        "</main></body></html>\n"
    ).encode("utf-8")


def _compiled_preview(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    snapshot: PreviewInputSnapshot,
    report: dict[str, Any],
) -> dict[str, Any] | None:
    """Load the raw, already-validated v3 artifacts needed by the HTML view."""

    if "compiled" not in report:
        return None
    bundle_path = _attempt_path(workspace, manifest, 5, "generated-readme-bundle.json")
    if bundle_path is None:
        raise ContractError("E_PREVIEW_STATE", "compiled preview requires a committed generated bundle")
    bundle = _canonical_object(snapshot, bundle_path)
    if bundle is None or bundle.get("schema_version") != 3:
        raise ContractError("E_SCHEMA_VERSION", "compiled preview requires Generated Bundle v3")

    # The report only carries display fingerprints.  Read the committed stage
    # through the single Task 36 trust boundary before selecting any bytes.
    loaded = load_compiled_visual(bundle_path.parent, bundle)
    compiled_spec_raw = loaded.artifacts.get("compiled/visual-spec.json")
    if compiled_spec_raw is None:
        raise ContractError("E_VISUAL_PATH", "compiled Visual Spec is unavailable")
    try:
        spec_payload = json.loads(compiled_spec_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled Visual Spec is not canonical JSON") from exc

    evidence_path = _attempt_path(workspace, manifest, 0, "repository-evidence.json")
    if evidence_path is None:
        raise ContractError("E_PREVIEW_STATE", "compiled preview requires repository evidence")
    evidence_payload = _canonical_object(snapshot, evidence_path)
    if evidence_payload is None:
        raise ContractError("E_PREVIEW_PATH", "compiled repository evidence is unavailable")
    if evidence_payload.get("schema_version") == 1:
        evidence_graph = adapt_v1_repository_evidence(evidence_payload)
    elif evidence_payload.get("schema_version") == 2:
        evidence_graph = validate_evidence_graph(evidence_payload)
    else:
        raise ContractError("E_SCHEMA_VERSION", "compiled preview requires Evidence v1 or v2")

    spec = validate_visual_spec(spec_payload, evidence_graph=evidence_graph)
    if spec.canonical_bytes() != compiled_spec_raw:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled Visual Spec bytes are not canonical")
    labels = {
        item.id: item.label
        for item in (*spec.groups, *spec.lanes, *spec.nodes)
        if item.label is not None
    }

    svg_entries: list[dict[str, Any]] = []
    assets: dict[str, bytes] = {}
    projections: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(loaded.artifacts, key=lambda item: item.encode("utf-8")):
        if not path.startswith("assets/readme-showcase/") or not path.endswith(".svg"):
            continue
        parts = path.split("/")
        if len(parts) != 4 or parts[3][:-4] not in {"desktop", "mobile"}:
            raise ContractError("E_VISUAL_PATH", "compiled SVG path is not a desktop/mobile asset")
        locale, variant = parts[2], parts[3][:-4]
        raw = loaded.artifacts[path]
        # Keep the existing candidate gate in the preview boundary.  Local
        # marker references are normalized solely so the legacy gate can be
        # reused; the reader has already applied the authoritative SVG audit.
        _validate_compiled_svg(raw)
        assets[path] = raw
        svg_entries.append({"locale": locale, "variant": variant, "path": path})

        interaction_path = f"compiled/interaction/{locale}/{variant}.json"
        interaction_raw = loaded.artifacts.get(interaction_path)
        if interaction_raw is None:
            raise ContractError("E_VISUAL_PATH", f"compiled interaction is unavailable: {interaction_path}")
        try:
            interaction_payload = json.loads(interaction_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ContractError("E_VISUAL_FINGERPRINT", "compiled interaction is not canonical JSON") from exc
        if canonical_json_bytes(interaction_payload) != interaction_raw:
            raise ContractError("E_VISUAL_FINGERPRINT", "compiled interaction bytes are not canonical")
        projection = project_interaction_preview(
            interaction_payload,
            evidence_graph,
            labels=labels,
            expected_interaction_sha256=hashlib.sha256(interaction_raw).hexdigest(),
        ).as_dict()
        projections[(locale, variant)] = projection

    if not svg_entries:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled preview contains no SVG variants")
    locales = {entry["locale"] for entry in svg_entries}
    for locale in locales:
        variants = {entry["variant"] for entry in svg_entries if entry["locale"] == locale}
        if variants != {"desktop", "mobile"}:
            raise ContractError("E_VISUAL_FINGERPRINT", "compiled preview requires desktop and mobile SVGs")
        if any((locale, variant) not in projections for variant in variants):
            raise ContractError("E_VISUAL_FINGERPRINT", "compiled preview requires interaction for both viewports")

    return {
        "assets": assets,
        "viewports": svg_entries,
        "projections": projections,
        "primary_locale": spec.locale,
    }


def _compiled_index(report: dict[str, Any], readmes: dict[str, str], compiled: dict[str, Any]) -> bytes:
    """Render v3's static compiled surface without script or raw JSON artifacts."""

    viewport_sections: list[str] = []
    for entry in compiled["viewports"]:
        variant = entry["variant"]
        title = "Desktop viewport" if variant == "desktop" else "Mobile viewport"
        locale_suffix = f" ({entry['locale']})" if len({item['locale'] for item in compiled['viewports']}) > 1 else ""
        asset_path = entry["path"]
        viewport_sections.append(
            f"<section><h2>{title}{locale_suffix}</h2>"
            f"<p class=\"meta\">Safe compiled SVG asset: {html.escape(asset_path, quote=True)}</p>"
            f"<img class=\"compiled-svg {'mobile-preview' if variant == 'mobile' else ''}\" src=\"{html.escape(asset_path, quote=True)}\" alt=\"{title}{locale_suffix}\">"
            f"<h3>Inert interaction projection</h3>"
            f"<pre>{html.escape(canonical_json_bytes(compiled['projections'][(entry['locale'], variant)]).decode('utf-8'), quote=True)}</pre>"
            "</section>"
        )

    primary_entry = None
    for entry in compiled["viewports"]:
        if entry["locale"] == compiled["primary_locale"] and entry["variant"] == "desktop":
            primary_entry = entry
            break
    if primary_entry is None:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled preview has no primary desktop viewport")
    projection = compiled["projections"][(primary_entry["locale"], primary_entry["variant"])]
    evidence_by_id = {item["evidence_id"]: item for item in projection["evidence"]}
    fallback_rows: list[str] = []
    for focus in projection["focus"]:
        evidence_ids = focus["evidence_ids"]
        evidence_labels = []
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            source = f" ({evidence['source_path']})" if evidence is not None else ""
            evidence_labels.append(f"{evidence_id}{source}")
        fallback_rows.append(
            f"<li><strong>{html.escape(focus['element_id'], quote=True)}</strong> "
            f"{html.escape(focus['label'], quote=True)}"
            f"<span class=\"meta\"> — Evidence: {html.escape(', '.join(evidence_labels), quote=True)}</span></li>"
        )

    compiled_css = ".compiled-svg{display:block;max-width:100%;height:auto;margin:12px auto;border:1px solid #33466d;background:#080d19}.compiled-svg.mobile-preview{max-width:375px}.fallback-list{margin:0;padding-left:28px}.fallback-list li{margin:8px 0}"
    compiled_sections = (
        "<section><h2>Static interaction fallback</h2>"
        "<p class=\"meta\">This no-script list preserves the compiled focus order and its Evidence links.</p>"
        f"<ol class=\"fallback-list\">{''.join(fallback_rows)}</ol></section>"
        + "".join(viewport_sections)
    )
    return _index(
        report,
        readmes,
        extra_css=compiled_css,
        extra_sections=compiled_sections,
    )


def _open_preview_directory(parent: int | None, name: str | Path, *, context: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, **({"dir_fd": parent} if parent is not None else {}))
        info = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ContractError("E_PREVIEW_PATH", f"{context} directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise ContractError("E_PREVIEW_PATH", f"{context} must be a real directory")
    return descriptor


def _open_preview_child_directory(parent: int, name: str, expected: os.stat_result, *, context: str) -> int:
    descriptor = _open_preview_directory(parent, name, context=context)
    try:
        observed = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise ContractError("E_PREVIEW_PATH", f"{context} directory is unavailable") from exc
    if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
        os.close(descriptor)
        raise ContractError("E_PREVIEW_PATH", f"{context} directory changed during read")
    return descriptor


def _open_preview_relative_directory(
    root: int,
    parts: tuple[str, ...],
    expected: os.stat_result,
    *,
    context: str,
) -> int:
    parent = os.dup(root)
    try:
        if not parts:
            observed = os.fstat(parent)
            if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
                raise ContractError("E_PREVIEW_PATH", f"{context} directory changed during read")
            return parent
        for name in parts[:-1]:
            child = _open_preview_directory(parent, name, context=context)
            os.close(parent)
            parent = child
        child = _open_preview_child_directory(parent, parts[-1], expected, context=context)
        os.close(parent)
        return child
    except BaseException:
        os.close(parent)
        raise


def _read_preview_file(parent: int, name: str, expected: os.stat_result, *, context: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    try:
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise ContractError("E_PREVIEW_PATH", f"{context} file is unavailable") from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)
        ):
            raise ContractError("E_PREVIEW_PATH", f"{context} file changed during read")
        chunks: list[bytes] = []
        remaining = _MAX_PREVIEW_FILE_BYTES + 1
        while remaining:
            try:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
            except OSError as exc:
                raise ContractError("E_PREVIEW_PATH", f"cannot read {context} file") from exc
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(raw) > _MAX_PREVIEW_FILE_BYTES or (
            len(raw) != opened.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise ContractError("E_PREVIEW_PATH", f"{context} file changed during read")
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_preview_relative(
    root: int,
    parts: tuple[str, ...],
    expected: os.stat_result,
    *,
    context: str,
) -> bytes:
    parent = os.dup(root)
    try:
        for name in parts[:-1]:
            child = _open_preview_directory(parent, name, context=context)
            os.close(parent)
            parent = child
        return _read_preview_file(parent, parts[-1], expected, context=context)
    finally:
        os.close(parent)


def _enumerate_preview_tree(
    root: Path,
    *,
    context: str,
) -> tuple[int, list[tuple[str, tuple[str, ...], os.stat_result]]] | None:
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContractError("E_PREVIEW_PATH", f"{context} directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_PREVIEW_PATH", f"{context} must be a real directory")

    # Pin the root before walking it.  Every child directory is opened from a
    # descriptor with O_NOFOLLOW and checked against its scanned identity.
    root_fd = _open_preview_directory(None, root, context=context)
    try:
        root_observed = os.fstat(root_fd)
    except OSError as exc:
        os.close(root_fd)
        raise ContractError("E_PREVIEW_PATH", f"{context} directory is unavailable") from exc
    if (root_observed.st_dev, root_observed.st_ino) != (info.st_dev, info.st_ino):
        os.close(root_fd)
        raise ContractError("E_PREVIEW_PATH", f"{context} changed during read")

    # Enumerate metadata before reading any bytes.  This keeps hostile trees
    # from growing an unbounded path list or byte map before a limit is seen.
    files: list[tuple[str, tuple[str, ...], os.stat_result]] = []
    pending: list[tuple[str, tuple[str, ...], int, os.stat_result]] = [("", (), 0, info)]
    entries_seen = 0
    total_bytes = 0
    try:
        while pending:
            prefix, path_parts, depth, expected_directory = pending.pop()
            directory = _open_preview_relative_directory(
                root_fd,
                path_parts,
                expected_directory,
                context=context,
            )
            try:
                if depth > _MAX_PREVIEW_TREE_DEPTH:
                    raise ContractError("E_PREVIEW_PATH", f"{context} tree exceeds its depth bound")
                try:
                    children = []
                    with os.scandir(directory) as iterator:
                        for child in iterator:
                            entries_seen += 1
                            if entries_seen > _MAX_PREVIEW_TREE_ENTRIES:
                                raise ContractError("E_PREVIEW_PATH", f"{context} tree exceeds its entry bound")
                            children.append(child)
                except OSError as exc:
                    raise ContractError("E_PREVIEW_PATH", f"{context} directory is unavailable") from exc
                children.sort(key=lambda item: os.fsencode(item.name))
                for child in children:
                    relative = f"{prefix}/{child.name}" if prefix else child.name
                    child_parts = (*path_parts, child.name)
                    try:
                        child_info = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ContractError("E_PREVIEW_PATH", f"{context} entry is unavailable") from exc
                    if stat.S_ISLNK(child_info.st_mode):
                        raise ContractError("E_PREVIEW_PATH", f"{context} contains unsafe data")
                    if stat.S_ISDIR(child_info.st_mode):
                        if depth + 1 > _MAX_PREVIEW_TREE_DEPTH:
                            raise ContractError("E_PREVIEW_PATH", f"{context} tree exceeds its depth bound")
                        pending.append((relative, child_parts, depth + 1, child_info))
                        continue
                    if not stat.S_ISREG(child_info.st_mode):
                        raise ContractError("E_PREVIEW_PATH", f"{context} contains unsafe data")
                    size = child_info.st_size
                    if size > _MAX_PREVIEW_FILE_BYTES:
                        raise ContractError("E_PREVIEW_PATH", f"{context} file exceeds its per-file byte bound")
                    total_bytes += size
                    if total_bytes > _MAX_PREVIEW_TREE_BYTES:
                        raise ContractError("E_PREVIEW_PATH", f"{context} tree exceeds its aggregate byte bound")
                    files.append((relative, child_parts, child_info))
            finally:
                os.close(directory)

    except BaseException:
        os.close(root_fd)
        raise
    return root_fd, files


def _tree_bytes(root: Path) -> dict[str, bytes] | None:
    scanned = _enumerate_preview_tree(root, context="preview destination")
    if scanned is None:
        return None
    root_fd, files = scanned
    try:
        return {
            relative: _read_preview_relative(
                root_fd,
                parts,
                expected,
                context="preview destination",
            )
            for relative, parts, expected in sorted(files, key=lambda item: os.fsencode(item[0]))
        }
    finally:
        os.close(root_fd)


def _readme_documents(report: dict[str, Any], readmes: dict[str, str]) -> dict[str, bytes]:
    locale_by_path = report.get("locale_by_path")
    if isinstance(locale_by_path, dict):
        return {
            f"locales/{locale}.escaped.html": _document(f"{path} escaped source", readmes[path])
            for path, locale in locale_by_path.items()
        }
    return {
        "README.escaped.html": _document("README.md escaped source", readmes["README.md"]),
        "README_zh.escaped.html": _document("README_zh.md escaped source", readmes["README_zh.md"]),
    }


def render_preview(workspace: RunWorkspace, manifest: dict[str, Any]) -> dict[str, object]:
    snapshot = PreviewInputSnapshot()
    # Keep the legacy input ordering and failure surface stable.  Compiled
    # candidates do not publish these bytes, but they are still checked before
    # any v3 projection is accepted.
    assets = _collect_assets(workspace, snapshot)
    report, readmes = build_preview_snapshot(workspace, manifest, snapshot)
    compiled = _compiled_preview(workspace, manifest, snapshot, report)
    if compiled is None:
        files = {
            "index.html": _index(report, readmes),
            "report.json": canonical_json_bytes(report),
            **assets,
        }
    else:
        files = {
            "index.html": _compiled_index(report, readmes, compiled),
            "report.json": canonical_json_bytes(report),
            **compiled["assets"],
        }
    files.update(_readme_documents(report, readmes))
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
