"""Exact arithmetic for deterministic retrieval ranking."""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Iterable, Sequence


K1 = Fraction(120, 100)
B = Fraction(75, 100)
LAMBDA = Fraction(70, 100)
K = 5


def basis_points(value: Fraction | int, denominator: int | None = None) -> int:
    ratio = Fraction(value, denominator) if denominator is not None else Fraction(value)
    scaled = ratio * 10_000
    return (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)


def overlap(left: Iterable[str], right: Iterable[str]) -> Fraction:
    wanted, available = set(left), set(right)
    return Fraction(len(wanted & available), len(wanted)) if wanted else Fraction(0)


def jaccard(left: Iterable[str], right: Iterable[str]) -> Fraction:
    first, second = set(left), set(right)
    union = first | second
    return Fraction(len(first & second), len(union)) if union else Fraction(0)


def bm25_scores(query: Sequence[str], documents: Sequence[Sequence[str]]) -> list[Fraction]:
    if not documents or not query:
        return [Fraction(0) for _ in documents]
    lengths = [len(document) for document in documents]
    average = Fraction(sum(lengths), len(lengths))
    if average == 0:
        return [Fraction(0) for _ in documents]
    frequencies = [Counter(document) for document in documents]
    total = len(documents)
    scores = [Fraction(0) for _ in documents]
    for term in sorted(set(query)):
        document_frequency = sum(term in frequency for frequency in frequencies)
        if not document_frequency:
            continue
        # Rational Robertson-style IDF keeps every operation exact and portable.
        inverse_frequency = Fraction(total - document_frequency + 1, document_frequency)
        for index, frequency in enumerate(frequencies):
            count = frequency[term]
            if not count:
                continue
            saturation = Fraction(count) * (K1 + 1)
            saturation /= Fraction(count) + K1 * (1 - B + B * Fraction(lengths[index], 1) / average)
            scores[index] += inverse_frequency * saturation
    return scores


__all__ = ["B", "K", "K1", "LAMBDA", "basis_points", "bm25_scores", "jaccard", "overlap"]
