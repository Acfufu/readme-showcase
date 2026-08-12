from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...pipeline_contracts import ContractError
from ..scanner.visual import is_neutral_color


# Font stacks the visual-production contract permits unconditionally; any
# other family must already exist in the repository's own typography tokens.
_SYSTEM_FONT_FAMILIES = frozenset({
    "-apple-system", "arial", "courier", "courier new", "geneva", "georgia",
    "helvetica", "helvetica neue", "menlo", "monaco", "monospace", "sans-serif",
    "segoe ui", "serif", "system-ui", "tahoma", "times", "times new roman",
    "trebuchet ms", "ui-monospace", "ui-sans-serif", "ui-serif", "verdana",
})
_IDENTITY_OVERRIDE_FIELDS = frozenset({"reason", "approved_by"})
_MAX_EVIDENCE = 4096


def _normalize_family(family: str) -> str:
    return family.strip().strip("'\"").lower()[:64]


def collect_visual_tokens(evidence: Mapping[str, Any]) -> dict[str, object]:
    """Merge visual facts from a repository-evidence graph.

    Returns an empty mapping when the graph carries no visual facts; the
    evaluator then skips the gate instead of inventing one.
    """
    palette: set[str] = set()
    typography: set[str] = set()
    sources: set[str] = set()
    for fact in evidence.get("facts", []):
        if not isinstance(fact, Mapping) or fact.get("kind") != "visual":
            continue
        value = fact.get("value")
        if not isinstance(value, Mapping):
            continue
        for name, collection in (("palette", palette), ("typography", typography)):
            items = value.get(name)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item:
                    collection.add(item)
        key = fact.get("semantic_key")
        if isinstance(key, str):
            sources.add(key)
    if not palette and not typography:
        return {}
    return {
        "palette": sorted(palette),
        "typography": sorted(typography),
        "sources": sorted(sources),
    }


def _conflicts(asset_tokens: Mapping[str, Any], product_tokens: Mapping[str, Any]) -> list[dict[str, object]]:
    product_palette = product_tokens.get("palette", ())
    product_typography = product_tokens.get("typography", ())
    conflicts: list[dict[str, object]] = []
    for color in asset_tokens.get("palette", ()):
        if not isinstance(color, str) or not color:
            continue
        if is_neutral_color(color) or color in product_palette:
            continue
        conflicts.append({
            "token": "palette",
            "chosen": color,
            "product": sorted(product_palette),
            "message": (
                f"candidate color {color} is not part of the repository identity palette "
                f"(product palette: {', '.join(sorted(product_palette)) or 'none'})"
            ),
        })
    for family in asset_tokens.get("typography", ()):
        if not isinstance(family, str) or not family:
            continue
        normalized = _normalize_family(family)
        if normalized in product_typography or normalized in _SYSTEM_FONT_FAMILIES:
            continue
        conflicts.append({
            "token": "typography",
            "chosen": family,
            "product": sorted(product_typography),
            "message": (
                f"candidate font {family} is not part of the repository identity typography "
                f"(product typography: {', '.join(sorted(product_typography)) or 'none'})"
            ),
        })
    return conflicts


def identity_check(
    asset_tokens: Mapping[str, Any],
    product_tokens: Mapping[str, Any],
) -> dict[str, object]:
    """Compare candidate asset tokens against scan-extracted product tokens.

    Neutral grayscale colors and system font families are always allowed;
    every other candidate token must already exist in the repository's own
    visual identity.  Conflicts carry the product values versus the chosen
    value so the decision is explainable.
    """
    conflicts = _conflicts(asset_tokens, product_tokens)
    return {"pass": not conflicts, "conflicts": conflicts, "override": False}


def _validate_override(override: Any) -> dict[str, str]:
    if not isinstance(override, Mapping):
        raise ContractError("E_EVALUATION_REPORT", "identity override must be an object with reason and approved_by")
    unknown = sorted(set(override) - _IDENTITY_OVERRIDE_FIELDS)
    missing = sorted(_IDENTITY_OVERRIDE_FIELDS - set(override))
    if unknown:
        raise ContractError("E_EVALUATION_REPORT", f"identity override has unknown field {unknown[0]}")
    if missing:
        raise ContractError("E_EVALUATION_REPORT", f"identity override is missing {missing[0]}")
    normalized: dict[str, str] = {}
    for field in ("reason", "approved_by"):
        value = override[field]
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or len(value.encode("utf-8")) > _MAX_EVIDENCE
        ):
            raise ContractError("E_EVALUATION_REPORT", f"identity override {field} is invalid")
        normalized[field] = value
    return normalized


def evaluate_identity_gate(
    asset_tokens: Mapping[str, Any],
    product_tokens: Mapping[str, Any],
    *,
    override: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Apply the explainable identity hard gate with written-reason override.

    Without product tokens the gate cannot judge identity and passes with an
    explanatory evidence string (evidence-driven fail-open, like the voice
    gate).  A written override (reason and approved_by) converts conflicts
    into a pass and records the override for the report; an override that is
    never needed because there were no conflicts is not recorded.
    """
    if not product_tokens:
        return {
            "pass": True,
            "conflicts": [],
            "override": False,
            "identity_override": None,
            "evidence": "no visual identity facts in repository evidence",
        }
    conflicts = _conflicts(asset_tokens, product_tokens)
    if not conflicts:
        return {
            "pass": True,
            "conflicts": [],
            "override": False,
            "identity_override": None,
            "evidence": "candidate visual tokens match the repository identity",
        }
    if override is not None:
        normalized = _validate_override(override)
        return {
            "pass": True,
            "conflicts": conflicts,
            "override": True,
            "identity_override": normalized,
            "evidence": (
                "identity conflicts overridden with written reason: "
                f"{normalized['reason']} (approved by {normalized['approved_by']})"
            ),
        }
    summary = "; ".join(str(conflict["message"]) for conflict in conflicts)[:_MAX_EVIDENCE]
    return {
        "pass": False,
        "conflicts": conflicts,
        "override": False,
        "identity_override": None,
        "evidence": f"identity conflicts: {summary}",
    }


__all__ = [
    "collect_visual_tokens",
    "evaluate_identity_gate",
    "identity_check",
]
