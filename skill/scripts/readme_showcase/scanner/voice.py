from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

_CONTRACTS = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
read_regular_bytes = _CONTRACTS.read_regular_bytes

from ..contracts.common import normalize_posix_path
from ..contracts.evidence import VOICE_GIT_LOG_SOURCE, build_fact
from .git import commit_subjects


VOICE_KIND = "voice-sample"
MIN_SENTENCES_FOR_MATCH = 10
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_SENTENCES = 500
_MAX_WORDS_PER_SENTENCE = 64
_MAX_CHANGELOG_LINES = 30
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")
_CHANGELOG_NAMES = ("CHANGELOG.md", "CHANGELOG", "HISTORY.md", "CHANGES.md")
_FENCE = re.compile(r"(?:```+|~~~+)\s*[A-Za-z0-9_-]*")
_HEADING = re.compile(r"#{1,6}\s+.*")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+")
_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:[.:-][A-Za-z0-9_]+)*")
# Common non-imperative sentence starters (pronouns, articles, conjunctions,
# determiners).  A sentence whose first word is absent from this set is
# counted as imperative; the heuristic is deterministic and documented.
_NON_IMPERATIVE_STARTERS = frozenset(
    {
        "a", "all", "an", "and", "any", "as", "at", "be", "but", "by", "each",
        "every", "for", "from", "how", "i", "if", "in", "into", "it", "its",
        "many", "more", "most", "my", "no", "not", "of", "on", "one", "or",
        "our", "ours", "since", "so", "some", "that", "the", "their", "there",
        "these", "they", "this", "those", "to", "two", "under", "we", "what",
        "when", "where", "which", "while", "who", "why", "with", "you", "your",
        "yours",
    }
)


def _words(sentence: str) -> list[str]:
    return _TOKEN.findall(sentence)[:_MAX_WORDS_PER_SENTENCE]


def _is_technical(token: str) -> bool:
    return (
        any(character.isdigit() for character in token)
        or "_" in token
        or "-" in token
        or "." in token
        or (len(token) >= 2 and token.isupper())
    )


def _script(text: str) -> str:
    non_ascii = sum(1 for character in text if ord(character) > 0x7F)
    return "cjk" if non_ascii > len(text) // 10 else "latin"


def voice_features(text: str) -> dict[str, object]:
    """Compute bounded, integer-valued voice features for one prose corpus.

    Returns the sentence-length distribution (word counts), imperative-sentence
    count, technical-term count, and the dominant script.  All numeric values
    are integers so a voice-sample fact stays within the evidence contract.
    """
    sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(text)]
    lengths: list[int] = []
    imperative_count = 0
    term_count = 0
    for sentence in sentences:
        if not sentence:
            continue
        tokens = _words(sentence)
        if not tokens:
            continue
        lengths.append(min(len(tokens), _MAX_WORDS_PER_SENTENCE))
        if tokens[0].lower() not in _NON_IMPERATIVE_STARTERS:
            imperative_count += 1
        term_count += sum(1 for token in tokens if _is_technical(token))
        if len(lengths) >= _MAX_SENTENCES:
            break
    return {
        "script": _script(text),
        "sentences": lengths,
        "imperative_count": imperative_count,
        "term_count": term_count,
    }


def _paragraph_spans(text: str) -> list[tuple[list[str], int, int]]:
    """First two prose paragraphs with 1-based inclusive line spans.

    Headings, fenced blocks, HTML comments, images, and blank lines separate
    paragraphs; list items and inline links stay inside prose.
    """
    paragraphs: list[tuple[list[str], int, int]] = []
    current: list[str] = []
    start: int | None = None
    fence = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if _FENCE.fullmatch(stripped):
            fence = not fence
            current, start = [], None
            continue
        if fence or stripped.startswith("<!--") or _HEADING.fullmatch(stripped) or stripped.startswith(("![", "<")):
            current, start = [], None
            continue
        if not stripped:
            if current:
                paragraphs.append((current, start or number, number - 1))
                current, start = [], None
                if len(paragraphs) == 2:
                    break
            continue
        if start is None:
            start = number
        current.append(line)
    if current and len(paragraphs) < 2:
        paragraphs.append((current, start or 1, len(text.splitlines()) or 1))
    return paragraphs


def _content_lines(text: str, maximum: int) -> tuple[list[str], int, int]:
    lines: list[str] = []
    first = last = 0
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped and not _FENCE.fullmatch(stripped) and not _HEADING.fullmatch(stripped):
            if not lines:
                first = number
            lines.append(line)
            last = number
            if len(lines) >= maximum:
                break
    return lines, first, last


def _read_source(root: Path, name: str) -> bytes | None:
    try:
        return read_regular_bytes(root / name, maximum=_MAX_SOURCE_BYTES, path_code="E_SCAN_IO", size_code="E_SCAN_IO")
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            return None
        return None


def _voice_fact(
    *,
    path: str,
    locator: dict[str, int],
    key: str,
    value: dict[str, object],
    raw: bytes,
    derivation: str,
) -> dict[str, Any]:
    return build_fact(
        kind=VOICE_KIND,
        path=normalize_posix_path(path),
        locator=locator,
        semantic_key=key,
        value=value,
        source_bytes=raw,
        confidence="derived",
        derivation=derivation,
    )


def _emit_value(features: dict[str, object]) -> dict[str, object]:
    """Select the fact value for one source, or {} to skip emission.

    The ASCII-only tokenizer yields no sentences for CJK corpora; rather than
    silently emitting nothing (the voice gate would then pass as a no-op),
    mark such facts with an explicit ``skipped`` boolean so the evaluator can
    report an honest skip.
    """
    if features["sentences"]:
        return features
    if features["script"] == "cjk":
        return {**features, "skipped": True}
    return {}


def extract_voice_samples(root: Path) -> list[dict[str, Any]]:
    """Extract voice-sample facts at scan time from the target repository.

    The evaluator never touches the repository: it consumes these facts from
    the repository-evidence graph.  Sources are the first two README prose
    paragraphs, up to 30 CHANGELOG content lines, and the last 20 git commit
    subjects; each source becomes one derived voice-sample fact.
    """
    facts: list[dict[str, Any]] = []
    for name in _README_NAMES:
        raw = _read_source(root, name)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        paragraphs = _paragraph_spans(text)
        if not paragraphs:
            continue
        lines, start, end = paragraphs[0]
        if len(paragraphs) > 1:
            lines = lines + paragraphs[1][0]
            end = paragraphs[1][2]
        features = voice_features(" ".join(lines))
        value = _emit_value(features)
        if value:
            facts.append(_voice_fact(
                path=name,
                locator={"line_start": start, "line_end": end},
                key="voice-sample:readme-prose",
                value=value,
                raw=raw,
                derivation="voice features derived from the first two README prose paragraphs",
            ))
        break
    for name in _CHANGELOG_NAMES:
        raw = _read_source(root, name)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines, first, last = _content_lines(text, _MAX_CHANGELOG_LINES)
        if not lines:
            continue
        features = voice_features("\n".join(lines))
        value = _emit_value(features)
        if value:
            facts.append(_voice_fact(
                path=name,
                locator={"line_start": first, "line_end": last},
                key="voice-sample:changelog",
                value=value,
                raw=raw,
                derivation=f"voice features derived from the first {_MAX_CHANGELOG_LINES} CHANGELOG content lines",
            ))
        break
    subjects = commit_subjects(root)
    if subjects:
        subject_text = "\n".join(subjects)
        raw = subject_text.encode("utf-8")
        features = voice_features(subject_text)
        value = _emit_value(features)
        if value:
            facts.append(_voice_fact(
                path=VOICE_GIT_LOG_SOURCE,
                locator={"line_start": 1, "line_end": len(subjects)},
                key="voice-sample:commit-subjects",
                value=value,
                raw=raw,
                derivation="voice features derived from the last 20 git commit subjects",
            ))
    return facts


__all__ = [
    "MIN_SENTENCES_FOR_MATCH",
    "extract_voice_samples",
    "voice_features",
]
