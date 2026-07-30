#!/usr/bin/env python3
# Adapted from oil-oil/beautify-github-readme under MIT; see ../references/motion-production.md.
"""Audit local README image references and basic SVG compatibility."""

from __future__ import annotations

import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit


MARKDOWN_IMAGE = re.compile(
    r"!\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
MARKDOWN_LINK = re.compile(
    r"(?<!!)\[([^\]]+)\]\(\s*([^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
HTML_ALT = re.compile(r"\balt=[\"']([^\"']*)[\"']", re.I)
HTML_LINK = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>", re.I)
HTML_ANCHOR = re.compile(r"<a\b[^>]*\b(?:id|name)=[\"']([^\"']+)[\"'][^>]*>", re.I)
UNSAFE_SVG_TAGS = {
    "animate",
    "foreignobject",
    "image",
    "mpath",
    "script",
    "set",
    "style",
}
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_SVG_ELEMENTS = 5000
MAX_SVG_PATHS = 2000
MAX_SVG_DEPTH = 64
MAX_SVG_DIMENSION = 20000
_SVG_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_FONT_NAMES = {
    "-apple-system",
    "arial",
    "blinkmacsystemfont",
    "helvetica",
    "menlo",
    "monospace",
    "pingfang sc",
    "sans-serif",
    "segoe ui",
    "sfmono-regular",
    "system-ui",
    "ui-monospace",
}
SvgIssue = tuple[str, str]


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _masked_markdown(text: str) -> str:
    return re.sub(
        r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$",
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text,
    )


def _is_external(src: str) -> bool:
    parsed = urlsplit(src)
    return bool(parsed.scheme or parsed.netloc or src.startswith("//"))


def local_target(src: str, base: Path) -> Path | None:
    if _is_external(src) or src.startswith(("data:", "mailto:", "#")):
        return None
    clean = unquote(src.split("#", 1)[0].split("?", 1)[0])
    return (base / clean).resolve()


def _xml_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].lower()


def _positive_number(value: str | None) -> float | None:
    if value is None or not re.fullmatch(r"(?:[1-9]\d*)(?:\.\d+)?", value.strip()):
        return None
    return float(value)


def _font_issues(value: str) -> bool:
    names = [name.strip().strip("\"'").lower() for name in value.split(",")]
    return not names or any(name not in _FONT_NAMES for name in names)


def audit_svg_bytes(
    raw: bytes,
    *,
    expected_title: str | None = None,
    expected_labels: list[str] | None = None,
) -> list[SvgIssue]:
    issues: list[SvgIssue] = []
    if not raw or len(raw) > MAX_SVG_BYTES:
        return [("E_SVG_LIMIT", f"SVG must be 1..{MAX_SVG_BYTES} bytes")]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [("E_SVG_UNSAFE", "SVG must be valid UTF-8")]
    lower = text.lower()
    if "<!doctype" in lower or "<!entity" in lower:
        issues.append(("E_SVG_UNSAFE", "contains DOCTYPE or ENTITY"))
    if re.search(r"\son[a-z]+\s*=", text, flags=re.I):
        issues.append(("E_SVG_UNSAFE", "contains event handler"))
    if re.search(r"(?:@import|url\s*\(\s*[\"']?(?!#))", text, flags=re.I):
        issues.append(("E_SVG_UNSAFE", "contains external CSS resource"))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return issues + [("E_SVG_UNSAFE", f"invalid SVG XML: {exc}")]
    if _xml_name(root.tag) != "svg":
        issues.append(("E_SVG_UNSAFE", "root element must be <svg>"))

    width = _positive_number(root.attrib.get("width"))
    height = _positive_number(root.attrib.get("height"))
    if (
        width is None
        or height is None
        or width > MAX_SVG_DIMENSION
        or height > MAX_SVG_DIMENSION
    ):
        issues.append(("E_SVG_LIMIT", "width and height must be positive unitless bounded values"))
    view_box = root.attrib.get("viewBox")
    try:
        values = [float(item) for item in view_box.split()] if view_box else []
    except ValueError:
        values = []
    if (
        len(values) != 4
        or values[2] <= 0
        or values[3] <= 0
        or values[2] > MAX_SVG_DIMENSION
        or values[3] > MAX_SVG_DIMENSION
    ):
        issues.append(("E_SVG_LIMIT", "viewBox must have positive bounded dimensions"))
    if root.attrib.get("role") != "img":
        issues.append(("E_SVG_ACCESSIBILITY", "missing role=img"))

    elements = list(root.iter())
    if len(elements) > MAX_SVG_ELEMENTS:
        issues.append(("E_SVG_LIMIT", f"contains more than {MAX_SVG_ELEMENTS} elements"))
    if sum(_xml_name(node.tag) == "path" for node in elements) > MAX_SVG_PATHS:
        issues.append(("E_SVG_LIMIT", f"contains more than {MAX_SVG_PATHS} paths"))
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_SVG_DEPTH:
            issues.append(("E_SVG_LIMIT", f"nesting exceeds {MAX_SVG_DEPTH}"))
            break
        stack.extend((child, depth + 1) for child in node)

    ids: set[str] = set()
    references: set[str] = set()
    titles: list[str] = []
    labels: list[str] = []
    for node in elements:
        tag = _xml_name(node.tag)
        if tag in UNSAFE_SVG_TAGS:
            issues.append(("E_SVG_UNSAFE", f"contains unsupported <{tag}>"))
        if tag == "title":
            titles.append(" ".join("".join(node.itertext()).split()))
        if tag == "text":
            labels.append(" ".join("".join(node.itertext()).split()))
        identifier = node.attrib.get("id")
        if identifier is not None:
            if not _SVG_ID.fullmatch(identifier):
                issues.append(("E_SVG_REFERENCE", f"invalid id: {identifier}"))
            elif identifier in ids:
                issues.append(("E_SVG_REFERENCE", f"duplicate id: {identifier}"))
            ids.add(identifier)
        for raw_name, value in node.attrib.items():
            name = _xml_name(raw_name)
            if name.startswith("on"):
                issues.append(("E_SVG_UNSAFE", f"contains event attribute: {name}"))
            if name == "href":
                if not value.startswith("#"):
                    issues.append(("E_SVG_UNSAFE", "contains external href"))
                else:
                    references.add(value[1:])
            if name in {"aria-labelledby", "aria-describedby"}:
                references.update(value.split())
            references.update(re.findall(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]*)\s*\)", value))
            if name == "font-family" and _font_issues(value):
                issues.append(("E_SVG_UNSAFE", "contains non-system font"))
            if name == "style":
                if re.search(r"(?:@import|url\s*\()", value, flags=re.I):
                    issues.append(("E_SVG_UNSAFE", "contains active style resource"))
                match = re.search(r"font-family\s*:\s*([^;]+)", value, flags=re.I)
                if match and _font_issues(match.group(1)):
                    issues.append(("E_SVG_UNSAFE", "contains non-system font"))

    if len(titles) != 1 or not titles[0]:
        issues.append(("E_SVG_ACCESSIBILITY", "requires exactly one nonempty <title>"))
    elif expected_title is not None and titles[0] != expected_title:
        issues.append(("E_SVG_ACCESSIBILITY", "title differs from semantic source"))
    for reference in sorted(references - ids):
        issues.append(("E_SVG_REFERENCE", f"unresolved id: {reference}"))
    if expected_labels is not None and sorted(labels) != sorted(expected_labels):
        issues.append(("E_SVG_LABELS", "visible text differs from semantic labels"))
    return issues


def audit_svg(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"E_SVG_UNSAFE: cannot read SVG: {exc}"]
    return [f"{code}: {message}" for code, message in audit_svg_bytes(raw)]


def _anchors(path: Path) -> set[str]:
    try:
        text = _masked_markdown(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return set()
    anchors = set(HTML_ANCHOR.findall(text))
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", line)
        if not match:
            continue
        heading = unicodedata.normalize("NFKC", match.group(1)).lower()
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
        slug = re.sub(r"[ \t]+", "-", heading.strip())
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _resolve_local(
    src: str,
    readme: Path,
    root: Path,
    line: int,
) -> tuple[Path | None, str | None]:
    if _is_external(src) or src.startswith(("data:", "mailto:")):
        return None, None
    path_part, _, anchor = src.partition("#")
    clean = unquote(path_part.split("?", 1)[0])
    if "\\" in clean or Path(clean).is_absolute() or ".." in Path(clean).parts:
        return None, f"line {line}: local reference escapes README root: {src}"
    lexical = (readme.parent / clean).absolute() if clean else readme
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        return None, f"line {line}: local reference escapes README root: {src}"
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None, f"line {line}: local reference cannot be a symlink: {src}"
    target = lexical.resolve(strict=False)
    if not target.is_file():
        return None, f"line {line}: missing local reference: {src}"
    if anchor and anchor not in _anchors(target):
        return None, f"line {line}: broken anchor #{anchor}: {src}"
    return target, None


def image_references(text: str) -> list[tuple[str, str, int]]:
    masked = _masked_markdown(text)
    references = [
        (match.group(2), match.group(1).strip(), _line(masked, match.start()))
        for match in MARKDOWN_IMAGE.finditer(masked)
    ]
    for match in re.finditer(r"<img\b[^>]*>", masked, flags=re.I):
        tag = match.group()
        source = HTML_IMAGE.search(tag)
        alt = HTML_ALT.search(tag)
        if source:
            references.append(
                (
                    source.group(1),
                    alt.group(1).strip() if alt else "",
                    _line(masked, match.start()),
                )
            )
    return references


def audit_readme(path: Path, *, root: Path | None = None) -> tuple[list[str], int, int]:
    readme = path.resolve(strict=True)
    audit_root = (root or readme.parent).resolve(strict=True)
    try:
        readme.relative_to(audit_root)
    except ValueError:
        return (["line 1: README escapes audit root"], 0, 0)
    text = readme.read_text(encoding="utf-8")
    masked = _masked_markdown(text)
    warnings: list[str] = []
    image_count = 0
    link_count = 0
    for src, alt, line in image_references(text):
        if not alt:
            warnings.append(f"line {line}: image missing useful alt text: {src}")
        target, issue = _resolve_local(src, readme, audit_root, line)
        if issue:
            warnings.append(issue)
            continue
        if target is None:
            continue
        image_count += 1
        if target.suffix.lower() == ".svg":
            warnings.extend(
                f"line {line}: {src}: {code}: {message}"
                for code, message in audit_svg_bytes(target.read_bytes())
            )
    links = [
        (match.group(2), _line(masked, match.start()))
        for match in MARKDOWN_LINK.finditer(masked)
    ]
    links.extend(
        (match.group(1), _line(masked, match.start()))
        for match in HTML_LINK.finditer(masked)
    )
    for src, line in links:
        target, issue = _resolve_local(src, readme, audit_root, line)
        if issue:
            warnings.append(issue)
        elif target is not None:
            link_count += 1
    return warnings, image_count, link_count


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_readme.py /path/to/README.md", file=sys.stderr)
        return 2

    readme = Path(sys.argv[1]).expanduser().resolve()
    if not readme.is_file():
        print(f"ERROR: README not found: {readme}")
        return 2

    try:
        warnings, checked, links = audit_readme(readme)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot audit README: {exc}")
        return 2

    print(f"README: {readme}")
    print(f"Local images checked: {checked}")
    print(f"Local links checked: {links}")
    if warnings:
        print("Issues:")
        for warning in warnings:
            print(f"- {warning}")
        return 1
    print("OK: README links, accessibility, and SVG safety passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
