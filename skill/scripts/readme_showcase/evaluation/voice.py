from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..scanner.voice import MIN_SENTENCES_FOR_MATCH, voice_features


VOICE_MATCH_SIGMA = 2.0
_LOCALE_SCRIPTS = frozenset({"zh", "ja", "ko"})


def locale_script(tag: str) -> str:
    """Map a plan locale tag to the script family used for voice matching."""
    return "cjk" if tag.split("-", 1)[0] in _LOCALE_SCRIPTS else "latin"


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _population_std(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def collect_voice_samples(evidence: Mapping[str, Any]) -> dict[str, object]:
    """Merge voice-sample facts from a repository-evidence graph.

    Returns an empty mapping when the graph carries no voice samples; the
    evaluator then skips the gate instead of inventing one.
    """
    lengths: list[int] = []
    imperative_count = 0
    term_count = 0
    sentence_count = 0
    script_tallies: dict[str, int] = {}
    sources: set[str] = set()
    for fact in evidence.get("facts", []):
        if not isinstance(fact, Mapping) or fact.get("kind") != "voice-sample":
            continue
        value = fact.get("value")
        if not isinstance(value, Mapping):
            continue
        sample_lengths = value.get("sentences")
        script = value.get("script")
        if not isinstance(sample_lengths, list) or not all(type(item) is int and item > 0 for item in sample_lengths):
            continue
        if isinstance(script, str):
            script_tallies[script] = script_tallies.get(script, 0) + len(sample_lengths)
        lengths.extend(sample_lengths)
        imperative_count += int(value.get("imperative_count", 0))
        term_count += int(value.get("term_count", 0))
        if isinstance(fact.get("semantic_key"), str):
            sources.add(fact["semantic_key"])
    if not lengths:
        return {}
    sentence_count = len(lengths)
    script = max(script_tallies, key=lambda key: (script_tallies[key], key)) if script_tallies else "latin"
    return {
        "script": script,
        "lengths": lengths,
        "imperative_count": imperative_count,
        "term_count": term_count,
        "sentence_count": sentence_count,
        "sources": sorted(sources),
    }


def _ratio_std(ratio: float, sample_size: int) -> float:
    if sample_size < 1:
        return 0.0
    return max(math.sqrt(ratio * (1.0 - ratio) / sample_size), 0.02)


def voice_match_check(candidate_text: str, voice_samples: Mapping[str, Any]) -> dict[str, object]:
    """Compare candidate prose to scan-extracted voice samples.

    The distance is the largest standardized deviation across sentence-length
    mean, imperative ratio, and technical-term density; a distance beyond two
    sample standard deviations fails the hard gate.  ``score`` is a bounded
    0..1 similarity (1 / (1 + distance)) and ``evidence`` a deterministic
    explanation.
    """
    lengths = voice_samples.get("lengths")
    if not isinstance(lengths, list) or not all(type(item) is int and item > 0 for item in lengths):
        return {"pass": True, "score": 1.0, "evidence": "voice samples are unavailable"}
    sample_sentences = len(lengths)
    sample_mean = _mean(lengths)
    sample_std = max(_population_std(lengths), 0.5)
    sample_imperative = int(voice_samples.get("imperative_count", 0)) / sample_sentences
    sample_terms = int(voice_samples.get("term_count", 0)) / sample_sentences

    candidate = voice_features(candidate_text)
    candidate_lengths = candidate["sentences"]
    if not candidate_lengths:
        return {"pass": True, "score": 1.0, "evidence": "candidate prose has no sentences to match"}
    candidate_sentences = len(candidate_lengths)
    candidate_mean = _mean(candidate_lengths)
    candidate_imperative = int(candidate["imperative_count"]) / candidate_sentences
    candidate_terms = int(candidate["term_count"]) / candidate_sentences

    distance = max(
        abs(candidate_mean - sample_mean) / sample_std,
        abs(candidate_imperative - sample_imperative) / _ratio_std(sample_imperative, sample_sentences),
        abs(candidate_terms - sample_terms) / _ratio_std(sample_terms, sample_sentences),
    )
    evidence = (
        f"voice distance {distance:.2f} sigma against {sample_sentences} sample sentences: "
        f"candidate mean sentence length {candidate_mean:.1f} vs sample {sample_mean:.1f} words; "
        f"imperative ratio {candidate_imperative:.2f} vs sample {sample_imperative:.2f}; "
        f"term density {candidate_terms:.2f} vs sample {sample_terms:.2f}"
    )
    return {"pass": distance <= VOICE_MATCH_SIGMA, "score": 1.0 / (1.0 + distance), "evidence": evidence}


def evaluate_voice_match(candidate_text: str, voice_samples: Mapping[str, Any], locale_tag: str) -> dict[str, object]:
    """Apply the hard voice gate with the non-native-locale advisory downgrade.

    A sparse sample cannot support a two-sigma judgment, so it passes with an
    explanatory evidence string.  When the candidate locale script differs from
    the sample script (for example a zh-Hans candidate for an English
    repository), a failed match is advisory only and never fails the gate.
    """
    if not voice_samples:
        return {"pass": True, "score": 1.0, "evidence": "no voice samples in repository evidence"}
    sentence_count = int(voice_samples.get("sentence_count", 0))
    if sentence_count < MIN_SENTENCES_FOR_MATCH:
        return {
            "pass": True,
            "score": 1.0,
            "evidence": f"insufficient voice samples ({sentence_count} < {MIN_SENTENCES_FOR_MATCH}) for a two-sigma match",
        }
    match = voice_match_check(candidate_text, voice_samples)
    if match["pass"]:
        return match
    if locale_script(locale_tag) != voice_samples.get("script"):
        return {
            "pass": True,
            "score": match["score"],
            "evidence": f"{match['evidence']} (advisory: non-native locale {locale_tag}, voice match downgraded)",
        }
    return match


__all__ = [
    "VOICE_MATCH_SIGMA",
    "collect_voice_samples",
    "evaluate_voice_match",
    "locale_script",
    "voice_match_check",
]
