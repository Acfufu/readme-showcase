"""Fixed, local-only Markdown readability checks. Never a release gate."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

from ...pipeline_contracts import canonical_json_bytes
from ...pipeline_core import segment_markdown_blocks
from ..diagnostics import Diagnostic


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_IMAGE = re.compile(r"^\s*!\[([^]]*)\]\([^)]*\)\s*$")
_BADGE = re.compile(r"(?:badge|badgen|shields\.io)", re.IGNORECASE)
_LINK = re.compile(r"\[([^]]+)\]\([^)]*\)")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_ACTION_LABELS = frozenset({"quick start", "get started", "install", "usage", "快速开始", "开始使用", "安装", "使用"})
_QUICK_START = re.compile(r"^quick\s*start$|^getting\s*started$|^快速开始$|^开始使用$", re.IGNORECASE)
_SECTION_ALIASES = {"quick start": "quick-start", "getting started": "quick-start", "快速开始": "quick-start", "开始使用": "quick-start", "install": "install", "installation": "install", "安装": "install", "usage": "usage", "use": "usage", "使用": "usage", "用法": "usage", "plan": "plan", "roadmap": "plan", "计划": "plan", "路线图": "plan", "architecture": "architecture", "架构": "architecture"}


@dataclass(frozen=True, slots=True)
class EditorialDiagnostic(Diagnostic):
    """A ``Diagnostic`` with optional Markdown-heading context."""

    heading: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {**Diagnostic.as_dict(self), "heading": self.heading}

    def sort_key(self) -> tuple[object, ...]:
        return (*Diagnostic.sort_key(self), self.heading or "")


@dataclass(frozen=True, slots=True)
class EditorialReport:
    """Advisory results; ``status`` is deliberately never a hard-gate failure."""

    findings: tuple[EditorialDiagnostic, ...]
    not_applicable: tuple[EditorialDiagnostic, ...]
    status: Literal["pass"] = "pass"

    @property
    def reasons(self) -> tuple[EditorialDiagnostic, ...]:
        return (*self.findings, *self.not_applicable)

    def as_dict(self) -> dict[str, object]:
        return {"findings": [item.as_dict() for item in self.findings], "not_applicable": [item.as_dict() for item in self.not_applicable], "status": self.status}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())


@dataclass(frozen=True, slots=True)
class _Heading:
    level: int
    text: str
    line: int


@dataclass(frozen=True, slots=True)
class _Document:
    path: str
    visible: tuple[tuple[int, str], ...]
    headings: tuple[_Heading, ...]
    paragraphs: tuple[tuple[int, str, str | None], ...]


def _normalized(value: str) -> str: return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _clean_text(value: str) -> str: return _normalized(re.sub(r"[`*_>#]", "", _LINK.sub(r"\1", value)))


def _mask_prose(line: str, comment: bool, inline: int | None) -> tuple[str, bool, int | None]:
    masked, index = list(line), 0
    while index < len(line):
        if comment:
            end = line.find("-->", index)
            stop = len(line) if end < 0 else end + 3
            masked[index:stop] = " " * (stop - index)
            if end < 0:
                return "".join(masked), True, inline
            comment, index = False, stop
        elif line[index] == "`":
            stop = index + 1
            while stop < len(line) and line[stop] == "`":
                stop += 1
            ticks = stop - index
            inline = ticks if inline is None else (None if inline == ticks else inline)
            masked[index:stop] = " " * ticks
            index = stop
        elif inline is not None:
            masked[index] = " "
            index += 1
        elif inline is None and line.startswith("<!--", index):
            comment = True
        else:
            index += 1
    return "".join(masked), comment, inline


def _fence(line: str, active: tuple[str, int] | None) -> tuple[tuple[str, int] | None, bool]:
    match = _FENCE.match(line)
    if match is None:
        return active, False
    run, trailing = match.groups()
    if active is None:
        return (run[0], len(run)), True
    return (None, True) if run[0] == active[0] and len(run) >= active[1] and not trailing.strip() else (active, False)


def _masked_lines(text: str) -> tuple[str, ...]:
    source = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []; comment = False
    fence: tuple[str, int] | None = None
    inline: int | None = None
    for line in source:
        next_fence, fence_line = _fence(line, fence) if not comment else (fence, False)
        if fence_line:
            fence = next_fence
            output.append(line)
        elif fence is not None:
            output.append(line)
        else:
            line, comment, inline = _mask_prose(line, comment, inline)
            output.append(line)
    return tuple(output)


def _visible_lines(text: str) -> tuple[tuple[str, ...], tuple[tuple[int, str], ...]]:
    lines = _masked_lines(text)
    # Keep segmentation behavior as the common safety boundary; line metadata is added below.
    segment_markdown_blocks("\n".join(lines))
    visible: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    script = False
    for line_number, raw in enumerate(lines, 1):
        fence, fence_line = _fence(raw, fence)
        if fence_line or fence is not None:
            continue
        lowered = raw.casefold()
        if "<script" in lowered:
            script = "</script" not in lowered
            continue
        if script:
            if "</script" in lowered:
                script = False
            continue
        visible.append((line_number, raw))
    return lines, tuple(visible)


def _document(path: str, text: str) -> _Document:
    lines, visible = _visible_lines(text)
    headings: list[_Heading] = []
    paragraphs: list[tuple[int, str, str | None]] = []
    current: list[tuple[int, str]] = []
    current_heading: str | None = None
    for line_number, line in (*visible, (len(lines) + 1, "")):
        match = _HEADING.match(line)
        if match or not line.strip():
            if current:
                paragraphs.append((current[0][0], "\n".join(item[1] for item in current), current_heading))
                current = []
            if match:
                current_heading = match.group(2).strip()
                headings.append(_Heading(len(match.group(1)), current_heading, line_number))
            continue
        if not _IMAGE.match(line):
            current.append((line_number, line))
    return _Document(path, visible, tuple(headings), tuple(paragraphs))


def _diagnostic(
    code: str, message: str, path: str | None, line: int | None, heading: str | None, suggested_action: str, related_ids: Iterable[str] = (),
) -> EditorialDiagnostic:
    return EditorialDiagnostic(code, "warning", "editorial", message, path, line, tuple(sorted(set(related_ids))), suggested_action, heading)


def _not_applicable(rule: str) -> EditorialDiagnostic:
    return EditorialDiagnostic("I_EDITORIAL_NOT_APPLICABLE", "info", "editorial", f"{rule} has no applicable input", related_ids=(rule,), suggested_action="Provide the relevant editorial input to review this rule.")


def _first_screen(document: _Document) -> list[EditorialDiagnostic]:
    screen = [item for item in document.visible if item[1].strip()][:20]
    definition = next((item for item in screen if len(_clean_text(item[1])) >= 20 and not _HEADING.match(item[1])), None)
    action = next((item for item in screen if (match := _HEADING.match(item[1])) is not None and _QUICK_START.match(_normalized(match.group(2)))), None)
    if action is None:
        action = next((item for item in screen if any(_normalized(label) in _ACTION_LABELS for label in _LINK.findall(item[1]))), None)
    if definition is not None and action is not None:
        return []
    anchor = definition or action or (screen[0] if screen else (1, ""))
    heading = document.headings[0].text if document.headings else None
    missing = "definition" if definition is None else "primary action"
    return [_diagnostic("W_EDITORIAL_FIRST_SCREEN", f"first screen lacks a project {missing}", document.path, anchor[0], heading, "Add a concise project definition and a visible Quick Start or action link.", ("first-screen",))]


def _heading_at(document: _Document, line: int) -> str | None:
    return next((heading.text for heading in reversed(document.headings) if heading.line <= line), None)


def _adjacent_image_line(document: _Document) -> int | None:
    block: list[tuple[int, str]] = []
    images_in_run = 0
    for line, value in (*document.visible, (0, "")):
        if value.strip():
            block.append((line, value))
        elif block:
            if all(_IMAGE.match(value) for _line, value in block):
                if images_in_run + len(block) >= 2:
                    return block[0][0] if images_in_run else block[1][0]
                images_in_run += len(block)
            else:
                images_in_run = 0
            block = []
    return None


def _review_document(document: _Document, planned_sections: set[str], diff_lines: int | None) -> tuple[list[EditorialDiagnostic], list[EditorialDiagnostic]]:
    findings = _first_screen(document)
    not_applicable: list[EditorialDiagnostic] = []
    previous_level = 0
    for heading in document.headings:
        if (previous_level == 0 and heading.level != 1) or (previous_level and heading.level > previous_level + 1):
            findings.append(_diagnostic("W_EDITORIAL_HEADING_HIERARCHY", "heading level skips hierarchy", document.path, heading.line, heading.text, "Use one H1 and increase heading depth by at most one level.", ("heading-hierarchy",)))
        previous_level = heading.level
    seen: dict[str, int] = {}
    for line, paragraph, heading in document.paragraphs:
        cleaned = _clean_text(paragraph)
        if len(cleaned) > 600:
            findings.append(_diagnostic("W_EDITORIAL_LONG_PARAGRAPH", "paragraph exceeds 600 Unicode code points", document.path, line, heading, "Split this paragraph into shorter, focused paragraphs.", ("long-paragraph",)))
        if len(cleaned) >= 20:
            earlier = seen.get(cleaned)
            if earlier is not None:
                findings.append(_diagnostic("W_EDITORIAL_DUPLICATE_PARAGRAPH", "paragraph duplicates earlier content", document.path, line, heading, "Remove or consolidate the repeated paragraph.", ("duplicate-paragraph", f"line:{earlier}")))
            else:
                seen[cleaned] = line
    badges = [(line, value) for line, value in document.visible if _IMAGE.match(value) and _BADGE.search(value)]
    if len(badges) > 8:
        line, _ = badges[8]
        findings.append(_diagnostic("W_EDITORIAL_BADGES", "README contains more than 8 badges", document.path, line, _heading_at(document, line), "Keep only the most useful eight badges.", ("badges",)))
    image_line = _adjacent_image_line(document)
    if image_line is not None:
        findings.append(_diagnostic("W_EDITORIAL_ADJACENT_IMAGES", "adjacent images have no caption", document.path, image_line, _heading_at(document, image_line), "Add captions or explanatory text between related images.", ("adjacent-images",)))
    quick = next((heading for heading in document.headings if _QUICK_START.match(_normalized(heading.text))), None)
    if quick is None:
        not_applicable.append(_not_applicable("quick-start-distance"))
    else:
        nonempty = sum(bool(value.strip()) for _line, value in document.visible if _line <= quick.line)
        if nonempty > 120:
            findings.append(_diagnostic("W_EDITORIAL_QUICK_START_DISTANCE", "Quick Start appears after 120 nonempty lines", document.path, quick.line, quick.text, "Move Quick Start closer to the top of the README.", ("quick-start-distance",)))
    if planned_sections:
        present = {_normalized(heading.text) for heading in document.headings}
        for section in sorted(planned_sections - present):
            anchor = document.headings[-1] if document.headings else _Heading(1, "", 1)
            findings.append(_diagnostic("W_EDITORIAL_PLAN_COVERAGE", f"planned section is missing: {section}", document.path, anchor.line, anchor.text or None, "Add a heading for this planned section.", ("plan-coverage", section)))
    else:
        not_applicable.append(_not_applicable("plan-coverage"))
    if diff_lines is None:
        not_applicable.append(_not_applicable("diff-size"))
    elif diff_lines > 500:
        findings.append(_diagnostic("W_EDITORIAL_DIFF_SIZE", "README diff exceeds 500 lines", document.path, 1, document.headings[0].text if document.headings else None, "Request human review for this large README diff.", ("diff-size",)))
    return findings, not_applicable


def _locale(path: str) -> str:
    return "zh" if re.search(r"(?:^|[_-])zh(?:[_-]|\.)", path.casefold()) else "en"


def _locale_structure(document: _Document) -> tuple[tuple[int, str], ...]:
    return tuple((heading.level, "title" if index == 0 and heading.level == 1 else _SECTION_ALIASES.get(_normalized(heading.text), _normalized(heading.text))) for index, heading in enumerate(document.headings))


def evaluate_editorial(
    readmes: Mapping[str, str], *, planned_sections: Sequence[str] = (), diff_lines: Mapping[str, int] | None = None,
) -> EditorialReport:
    """Review Markdown strings deterministically; this function never opens paths or executes content."""
    if not isinstance(readmes, Mapping) or not all(isinstance(path, str) and isinstance(text, str) and "\0" not in path for path, text in readmes.items()):
        raise ValueError("readmes must map non-NUL paths to Markdown strings")
    if not all(isinstance(section, str) for section in planned_sections):
        raise ValueError("planned_sections must contain strings")
    if diff_lines is not None and (not isinstance(diff_lines, Mapping) or not all(isinstance(path, str) and type(lines) is int and lines >= 0 for path, lines in diff_lines.items())):
        raise ValueError("diff_lines must map paths to non-negative integers")
    planned = {_normalized(section) for section in planned_sections}
    documents = [_document(path, readmes[path]) for path in sorted(readmes)]
    primary_locale = "en" if any(_locale(document.path) == "en" for document in documents) else "zh"
    findings: list[EditorialDiagnostic] = []
    not_applicable: list[EditorialDiagnostic] = []
    for document in documents:
        document_findings, document_na = _review_document(document, planned if _locale(document.path) == primary_locale else set(), None if diff_lines is None else diff_lines.get(document.path))
        findings.extend(document_findings)
        not_applicable.extend(document_na)
    locales = {locale: document for locale, document in ((_locale(document.path), document) for document in documents) if locale in {"en", "zh"}}
    if set(locales) == {"en", "zh"}:
        en, zh = locales["en"], locales["zh"]
        en_structure = _locale_structure(en)
        zh_structure = _locale_structure(zh)
        if en_structure != zh_structure:
            mismatch = next((index for index, pair in enumerate(zip(en_structure, zh_structure)) if pair[0] != pair[1]), min(len(en_structure), len(zh_structure)))
            document, anchor = (zh, zh.headings[mismatch]) if mismatch < len(zh.headings) else (en, en.headings[mismatch])
            findings.append(_diagnostic("W_EDITORIAL_LOCALE_STRUCTURE", "English and Chinese README heading structures differ", document.path, anchor.line, anchor.text, "Align bilingual section identities, levels, and order.", ("locale-structure",)))
    else:
        not_applicable.append(_not_applicable("locale-structure"))
    return EditorialReport(tuple(sorted(set(findings), key=EditorialDiagnostic.sort_key)), tuple(sorted(set(not_applicable), key=EditorialDiagnostic.sort_key)))
