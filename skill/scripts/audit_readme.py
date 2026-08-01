#!/usr/bin/env python3
# Adapted from oil-oil/beautify-github-readme under MIT; see ../references/motion-production.md.
"""Audit local README image references and basic SVG compatibility."""

from __future__ import annotations

import importlib
import io
import math
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
REFERENCE_DEFINITION = re.compile(
    r"(?m)^[ ]{0,3}\[([^\]]+)\]:[ \t]*(?:<([^>\r\n]+)>|([^\s\r\n]+))"
)
MARKDOWN_IMAGE_REFERENCE = re.compile(r"!\[([^\]]*)\]\[([^\]]*)\]")
MARKDOWN_LINK_REFERENCE = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
UNSAFE_SVG_TAGS = {
    "animate",
    "animatecolor",
    "animatemotion",
    "animatetransform",
    "discard",
    "foreignobject",
    "image",
    "mpath",
    "script",
    "set",
    "style",
}
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_README_BYTES = 4 * 1024 * 1024
MAX_SVG_ELEMENTS = 5000
MAX_SVG_PATHS = 2000
MAX_SVG_DEPTH = 64
MAX_SVG_DIMENSION = 20000
_SVG_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_OPACITY_VALUE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?\Z"
)
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
_CONTRACTS = importlib.import_module(
    "pipeline_contracts"
    if __package__ in (None, "")
    else "skill.scripts.pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
read_regular_bytes = _CONTRACTS.read_regular_bytes
SvgIssue = tuple[str, str]


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _masked_markdown(text: str) -> str:
    return re.sub(
        r"(?ms)^[ ]{0,3}(?:```|~~~).*?^[ ]{0,3}(?:```|~~~)[ \t]*$",
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
    return not names or (
        names != ["inter", "system-ui", "sans-serif"]
        and any(name not in _FONT_NAMES for name in names)
    )


def _bounded_svg_root(text: str) -> tuple[ET.Element | None, list[SvgIssue]]:
    depth = 0
    elements = 0
    paths = 0
    root: ET.Element | None = None
    try:
        iterator = ET.iterparse(io.StringIO(text), events=("start", "end"))
        for event, node in iterator:
            if event == "start":
                if root is None:
                    root = node
                depth += 1
                elements += 1
                paths += _xml_name(node.tag) == "path"
                if elements > MAX_SVG_ELEMENTS:
                    return None, [
                        ("E_SVG_LIMIT", f"contains more than {MAX_SVG_ELEMENTS} elements")
                    ]
                if paths > MAX_SVG_PATHS:
                    return None, [
                        ("E_SVG_LIMIT", f"contains more than {MAX_SVG_PATHS} paths")
                    ]
                if depth > MAX_SVG_DEPTH:
                    return None, [
                        ("E_SVG_LIMIT", f"nesting exceeds {MAX_SVG_DEPTH}")
                    ]
            else:
                depth -= 1
    except ET.ParseError as exc:
        return None, [("E_SVG_UNSAFE", f"invalid SVG XML: {exc}")]
    return root, []


def _svg_presentation(node: ET.Element) -> dict[str, str]:
    attributes = {
        _xml_name(name): value.strip().lower()
        for name, value in node.attrib.items()
    }
    style = {
        name.strip().lower(): value.strip().lower()
        for name, value in (
            declaration.split(":", 1)
            for declaration in attributes.get("style", "").split(";")
            if ":" in declaration
        )
    }
    return {**attributes, **style}


def _opacity_number(value: str | None) -> float | None:
    if value is None or _OPACITY_VALUE.fullmatch(value) is None:
        return None
    try:
        number = float(value.removesuffix("%"))
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _hidden_svg_node(node: ET.Element, inherited: bool) -> bool:
    values = _svg_presentation(node)
    opacities = [
        _opacity_number(values[name])
        for name in ("opacity", "fill-opacity")
        if name in values
    ]
    return inherited or (
        values.get("display") == "none"
        or values.get("visibility") in {"collapse", "hidden"}
        or any(value is None or value <= 0 for value in opacities)
        or values.get("clip-path", "none") != "none"
        or values.get("mask", "none") != "none"
    )


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
        return [("E_SVG_UNSAFE", "contains DOCTYPE or ENTITY")]
    if re.search(r"<\?xml-stylesheet\b", text, flags=re.I):
        return [("E_SVG_UNSAFE", "contains xml-stylesheet processing instruction")]
    if re.search(r"\son[a-z]+\s*=", text, flags=re.I):
        issues.append(("E_SVG_UNSAFE", "contains event handler"))
    if re.search(r"(?:@import|url\s*\(\s*[\"']?(?!#))", text, flags=re.I):
        issues.append(("E_SVG_UNSAFE", "contains external CSS resource"))
    root, parse_issues = _bounded_svg_root(text)
    if root is None:
        return issues + parse_issues
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
        or any(not math.isfinite(value) for value in values)
        or values[2] <= 0
        or values[3] <= 0
        or values[2] > MAX_SVG_DIMENSION
        or values[3] > MAX_SVG_DIMENSION
    ):
        issues.append(("E_SVG_LIMIT", "viewBox must have positive bounded dimensions"))
    if root.attrib.get("role") != "img":
        issues.append(("E_SVG_ACCESSIBILITY", "missing role=img"))

    ids: set[str] = set()
    references: set[str] = set()
    titles: list[str] = []
    labels: list[str] = []
    stack = [(root, False)]
    while stack:
        node, inherited_hidden = stack.pop()
        tag = _xml_name(node.tag)
        hidden = _hidden_svg_node(node, inherited_hidden)
        presentation = _svg_presentation(node)
        for name in ("opacity", "fill-opacity"):
            if name in presentation and _opacity_number(presentation[name]) is None:
                issues.append(("E_SVG_UNSAFE", f"contains invalid {name}"))
        if tag in UNSAFE_SVG_TAGS:
            issues.append(("E_SVG_UNSAFE", f"contains unsupported <{tag}>"))
        if tag == "title":
            titles.append(" ".join("".join(node.itertext()).split()))
        if tag == "text":
            if hidden:
                issues.append(("E_SVG_UNSAFE", "contains hidden semantic text"))
            else:
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
            if raw_name == "{http://www.w3.org/XML/1998/namespace}base":
                issues.append(("E_SVG_UNSAFE", "contains xml:base"))
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
        stack.extend((child, hidden) for child in reversed(list(node)))

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
        raw = read_regular_bytes(
            path,
            maximum=MAX_SVG_BYTES,
            path_code="E_SVG_UNSAFE",
            size_code="E_SVG_LIMIT",
        )
    except (ContractError, OSError) as exc:
        return [f"E_SVG_UNSAFE: cannot read SVG: {exc}"]
    return [f"{code}: {message}" for code, message in audit_svg_bytes(raw)]


def visible_svg_text(raw: bytes) -> list[str]:
    root, _ = _bounded_svg_root(raw.decode("utf-8"))
    if root is None:
        return []
    labels: list[str] = []
    stack = [(root, False)]
    while stack:
        node, inherited_hidden = stack.pop()
        hidden = _hidden_svg_node(node, inherited_hidden)
        if _xml_name(node.tag) == "text" and not hidden:
            labels.append(" ".join("".join(node.itertext()).split()))
        stack.extend((child, hidden) for child in reversed(list(node)))
    return [label for label in labels if label]


def _anchors(path: Path) -> set[str]:
    try:
        text = _masked_markdown(
            read_regular_bytes(
                path,
                maximum=MAX_README_BYTES,
                path_code="E_README_PATH",
                size_code="E_README_LIMIT",
            ).decode("utf-8")
        )
    except (ContractError, OSError, UnicodeDecodeError):
        return set()
    anchors = {
        value
        for match in re.finditer(r"<a\b[^>]*>", text, flags=re.I)
        for name in ("id", "name")
        if (value := _html_attribute(match.group(), name)) is not None
    }
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
    definitions = {
        " ".join(match.group(1).split()).casefold(): (
            match.group(2) or match.group(3)
        )
        for match in REFERENCE_DEFINITION.finditer(masked)
    }
    references = [
        (match.group(2), match.group(1).strip(), _line(masked, match.start()))
        for match in MARKDOWN_IMAGE.finditer(masked)
    ]
    for match in MARKDOWN_IMAGE_REFERENCE.finditer(masked):
        label = match.group(2) or match.group(1)
        source = definitions.get(" ".join(label.split()).casefold())
        if source is not None:
            references.append(
                (source, match.group(1).strip(), _line(masked, match.start()))
            )
    for match in re.finditer(r"<img\b[^>]*>", masked, flags=re.I):
        tag = match.group()
        source = _html_attribute(tag, "src")
        alt = _html_attribute(tag, "alt")
        if source is not None:
            references.append(
                (
                    source,
                    alt.strip() if alt is not None else "",
                    _line(masked, match.start()),
                )
            )
    return references


def _html_attribute(tag: str, name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
        tag,
        flags=re.I,
    )
    if match is None:
        return None
    return next(value for value in match.groups() if value is not None)


def audit_readme(path: Path, *, root: Path | None = None) -> tuple[list[str], int, int]:
    readme = path.resolve(strict=True)
    audit_root = (root or readme.parent).resolve(strict=True)
    try:
        readme.relative_to(audit_root)
    except ValueError:
        return (["line 1: README escapes audit root"], 0, 0)
    text = read_regular_bytes(
        readme,
        maximum=MAX_README_BYTES,
        path_code="E_README_PATH",
        size_code="E_README_LIMIT",
    ).decode("utf-8")
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
                f"line {line}: {src}: {issue}"
                for issue in audit_svg(target)
            )
    links = [
        (match.group(2), _line(masked, match.start()))
        for match in MARKDOWN_LINK.finditer(masked)
    ]
    definitions = {
        " ".join(match.group(1).split()).casefold(): (
            match.group(2) or match.group(3)
        )
        for match in REFERENCE_DEFINITION.finditer(masked)
    }
    links.extend(
        (source, _line(masked, match.start()))
        for match in MARKDOWN_LINK_REFERENCE.finditer(masked)
        if (
            source := definitions.get(
                " ".join((match.group(2) or match.group(1)).split()).casefold()
            )
        )
    )
    links.extend(
        (source, _line(masked, match.start()))
        for match in re.finditer(r"<a\b[^>]*>", masked, flags=re.I)
        if (source := _html_attribute(match.group(), "href")) is not None
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
    except (ContractError, OSError, UnicodeDecodeError) as exc:
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
