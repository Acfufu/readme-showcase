from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from ...pipeline_contracts import (
    ContractError,
    MAX_JSON_DEPTH,
    MAX_JSON_BYTES,
    canonical_json_bytes,
    canonical_sha256,
    read_regular_bytes,
    validate_json_nesting,
    write_canonical_json_atomic,
)
from ..contracts.assets import validate_asset_manifest
from ..contracts.claims import validate_claim_map
from ..contracts.common import MAX_JSON_NODES, normalize_posix_path
from ..contracts.evidence import validate_evidence_graph
from ..contracts.plan import validate_readme_plan, validate_readme_plan_v2
from ..contracts.run import canonical_repository
from ..visual_kernel.artifacts import MAX_COMPILED_BYTES
from ..visual_kernel.model import validate_visual_spec
from ..visual_kernel.reader import load_compiled_visual


GENERATED_BUNDLE_SCHEMA_VERSION = 2
GENERATED_BUNDLE_V3_SCHEMA_VERSION = 3
MAX_CANDIDATES = 10_000
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUNDLE_FIELDS = {"schema_version", "mode", "target", "candidate", "artifacts"}
_TARGET_FIELDS = {"repository", "base_sha"}
_CANDIDATE_FIELDS = {"readme", "assets", "candidate_sha256"}
_ARTIFACT_FIELDS = {"plan", "retrieval", "evidence", "claim_map", "asset_manifest", "evaluation"}
_REFERENCE_FIELDS = {"path", "sha256"}
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_BUNDLE_V3_FIELDS = {"schema_version", "mode", "target", "candidate", "artifacts", "compiled"}
_V3_TARGET_FIELDS = _TARGET_FIELDS
_V3_CANDIDATE_FIELDS = {"readmes", "assets", "candidate_sha256"}
_V3_ARTIFACT_FIELDS = {"plan", "retrieval", "evidence", "claim_map", "visual_spec", "asset_manifest"}
_V3_ARTIFACT_PATHS = {
    "plan": "readme-plan.json",
    "retrieval": "retrieval-packet.json",
    "evidence": "repository-evidence.json",
    "claim_map": "claim-map.json",
    "visual_spec": "visual-spec.json",
    "asset_manifest": "asset-manifest.json",
}
_V3_COMPILED_FIELDS = {"inventory", "fingerprint", "retention"}
_V3_SVG_PATH = re.compile(
    r"assets/readme-showcase/(?P<locale>[^/]+)/(?P<variant>desktop|mobile)\.svg\Z"
)


def _validate_bundle_structure(value: Any, path: str = "$") -> None:
    stack = [(value, path, 0)]
    nodes = 0
    while stack:
        item, item_path, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ContractError("E_INPUT_SIZE", "generated bundle exceeds structural limits")
        if isinstance(item, float):
            raise ContractError("E_SCHEMA_FLOAT", f"{item_path} must not contain floats")
        if isinstance(item, list):
            stack.extend(
                (item[index], f"{item_path}[{index}]", depth + 1)
                for index in range(len(item) - 1, -1, -1)
            )
        elif isinstance(item, dict):
            for key, child in reversed(item.items()):
                if not isinstance(key, str):
                    raise ContractError("E_SCHEMA_KEY_TYPE", f"{item_path} contains a non-string key")
                stack.append((child, f"{item_path}.{key}", depth + 1))


def _closed(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return value


def _sha(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError("E_BUNDLE_HASH", f"{context} must be lowercase SHA-256")
    return value


def _reference(value: Any, context: str) -> dict[str, str]:
    reference = _closed(value, _REFERENCE_FIELDS, context)
    try:
        path = normalize_posix_path(reference["path"])
    except ValueError as exc:
        raise ContractError("E_PATH", f"{context}.path must be safe relative POSIX path") from exc
    return {"path": path, "sha256": _sha(reference["sha256"], f"{context}.sha256")}


def _read_bytes(
    root: Path,
    reference: Mapping[str, str],
    context: str,
    *,
    maximum: int = MAX_JSON_BYTES,
) -> bytes:
    try:
        raw = read_regular_bytes(
            root.joinpath(*Path(reference["path"]).parts),
            maximum=maximum,
            path_code="E_PATH",
            size_code="E_INPUT_SIZE",
        )
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            raise ContractError("E_PATH", f"{context} is unavailable") from exc
        raise
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ContractError("E_BUNDLE_HASH", f"{context} bytes differ from reference")
    return raw


def _read_json(
    root: Path,
    reference: Mapping[str, str],
    context: str,
    *,
    maximum: int = MAX_JSON_BYTES,
    depth_code: str = "E_INPUT_SIZE",
) -> dict[str, Any]:
    raw = _read_bytes(root, reference, context, maximum=maximum)
    validate_json_nesting(
        raw,
        maximum_depth=MAX_JSON_DEPTH,
        code=depth_code,
        context=context,
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            raise ContractError(depth_code, f"{context} exceeds structural limits") from None
        raise ContractError("E_INPUT_JSON", f"{context} must be canonical UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must contain an object")
    if canonical_json_bytes(payload) != raw:
        raise ContractError("E_BUNDLE_HASH", f"{context} is not canonical JSON")
    return payload


def canonical_markdown_blocks(raw: bytes, context: str = "candidate README") -> list[bytes]:
    """Return LF-normalized UTF-8 blocks with no trailing newline."""
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ContractError("E_INPUT_ENCODING", f"{context} must be UTF-8") from exc
    blocks: list[bytes] = []
    current: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        marker = _FENCE.match(line)
        if marker and fence is None:
            fence = marker.group(1)[0]
        elif marker and fence == marker.group(1)[0]:
            fence = None
        if not line.strip() and fence is None:
            if current:
                blocks.append("\n".join(current).encode("utf-8"))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).encode("utf-8"))
    return blocks


def _readme_blocks(
    root: Path,
    readme: Mapping[str, str] | None,
    locales: list[Mapping[str, str]],
    primary_raw: bytes | None,
) -> dict[str, list[bytes]]:
    if readme is None:
        return {}
    documents: dict[str, list[bytes]] = {}
    for mapping in locales:
        locale = mapping["tag"]
        path = mapping["readme_path"]
        if path == readme["path"] and primary_raw is not None:
            raw = primary_raw
        else:
            try:
                raw = read_regular_bytes(
                    root.joinpath(*Path(path).parts),
                    maximum=MAX_JSON_BYTES,
                    path_code="E_PATH",
                    size_code="E_INPUT_SIZE",
                )
            except ContractError as exc:
                if exc.code == "E_INPUT_NOT_FOUND":
                    raise ContractError("E_CLAIM_COVERAGE", f"candidate README companion is missing: {path}") from exc
                raise
        documents[locale] = canonical_markdown_blocks(raw, f"candidate README {locale}")
    return documents


def _validate_claim_content(
    claims: Mapping[str, Any],
    blocks: Mapping[str, list[bytes]],
) -> None:
    markdown = claims["markdown_blocks"]
    if not isinstance(markdown, list):
        raise ContractError("E_SCHEMA_TYPE", "claim map.markdown_blocks must be an array")
    grouped: dict[str, list[dict[str, Any]]] = {locale: [] for locale in blocks}
    for claim in markdown:
        parts = claim["claim_id"].split(":", 2)
        if len(parts) != 3 or parts[0] != "markdown" or parts[1] not in grouped:
            raise ContractError("E_BUNDLE_CLAIM", "markdown claim identity must bind collection and candidate locale")
        grouped[parts[1]].append(claim)
    for locale, locale_blocks in blocks.items():
        locale_claims = grouped[locale]
        if len(locale_blocks) != len(set(locale_blocks)):
            raise ContractError("E_BUNDLE_CLAIM", f"candidate README {locale} has ambiguous duplicate blocks")
        identities: set[tuple[str, str, int, str]] = set()
        for ordinal, (claim, block) in enumerate(zip(locale_claims, locale_blocks)):
            identity = ("markdown_blocks", locale, ordinal, claim["claim_id"])
            if identity in identities:
                raise ContractError("E_CLAIM_DUPLICATE", "claim block identity is duplicated")
            identities.add(identity)
            if hashlib.sha256(block).hexdigest() != claim["content_sha256"]:
                raise ContractError("E_BUNDLE_HASH", f"claim {claim['claim_id']} differs from candidate README block {ordinal}")
        if len(locale_claims) != len(locale_blocks):
            raise ContractError("E_CLAIM_COVERAGE", f"candidate README {locale} blocks and claims differ")
    if markdown and not blocks:
        raise ContractError("E_CLAIM_COVERAGE", "markdown claims require a candidate README")


def _derive_claim_content(claims: Mapping[str, Any], blocks: Mapping[str, list[bytes]]) -> dict[str, Any]:
    structural = copy.deepcopy(dict(claims))
    values = structural.get("markdown_blocks")
    if isinstance(values, list):
        for ordinal, claim in enumerate(values):
            if isinstance(claim, dict):
                identity = f"markdown_blocks:{ordinal}:{claim.get('claim_id', '')}".encode("utf-8")
                claim["content_sha256"] = hashlib.sha256(identity).hexdigest()
    normalized = validate_claim_map(structural)
    markdown = normalized["markdown_blocks"]
    grouped: dict[str, list[dict[str, Any]]] = {locale: [] for locale in blocks}
    for claim in markdown:
        parts = claim["claim_id"].split(":", 2)
        if len(parts) != 3 or parts[0] != "markdown" or parts[1] not in grouped:
            raise ContractError("E_BUNDLE_CLAIM", "markdown claim identity must bind collection and candidate locale")
        grouped[parts[1]].append(claim)
    for locale, locale_blocks in blocks.items():
        if len(locale_blocks) != len(set(locale_blocks)):
            raise ContractError("E_BUNDLE_CLAIM", f"candidate README {locale} has ambiguous duplicate blocks")
        locale_claims = grouped[locale]
        if len(locale_claims) != len(locale_blocks):
            raise ContractError("E_CLAIM_COVERAGE", f"candidate README {locale} blocks and claims differ")
        for claim, block in zip(locale_claims, locale_blocks, strict=True):
            claim["content_sha256"] = hashlib.sha256(block).hexdigest()
    if markdown and not blocks:
        raise ContractError("E_CLAIM_COVERAGE", "markdown claims require a candidate README")
    return validate_claim_map(normalized)


def _validate_generated_bundle_v2(
    payload: Any,
    artifact_root: Path,
    *,
    claims_override: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    _validate_bundle_structure(payload)
    bundle = _closed(payload, _BUNDLE_FIELDS, "generated bundle")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != GENERATED_BUNDLE_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle requires schema_version 2")
    mode = bundle["mode"]
    if mode not in {"readme", "asset-only", "audit-only"}:
        raise ContractError("E_BUNDLE_MODE", "generated bundle mode is unsupported")
    target = _closed(bundle["target"], _TARGET_FIELDS, "generated bundle.target")
    repository = canonical_repository(target["repository"])
    if target["repository"] != repository:
        raise ContractError("E_BUNDLE_TARGET", "generated bundle repository must be canonical owner/name")
    base_sha = target["base_sha"]
    if not isinstance(base_sha, str) or not _SHA1.fullmatch(base_sha):
        raise ContractError("E_BUNDLE_TARGET", "generated bundle base_sha must be immutable")

    raw_candidate = _closed(bundle["candidate"], _CANDIDATE_FIELDS, "generated bundle.candidate")
    readme = None if raw_candidate["readme"] is None else _reference(raw_candidate["readme"], "generated bundle.candidate.readme")
    raw_assets = raw_candidate["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_CANDIDATES:
        raise ContractError("E_SCHEMA_TYPE", "generated bundle.candidate.assets must be bounded array")
    assets = [_reference(value, f"generated bundle.candidate.assets[{index}]") for index, value in enumerate(raw_assets)]
    if assets != sorted(assets, key=lambda item: item["path"]) or len({item["path"] for item in assets}) != len(assets):
        raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate assets must be unique path order")
    candidate = {"readme": readme, "assets": assets}
    candidate_hash = _sha(raw_candidate["candidate_sha256"], "generated bundle.candidate.candidate_sha256")
    if canonical_sha256(candidate) != candidate_hash:
        raise ContractError("E_BUNDLE_HASH", "generated bundle candidate hash differs from references")

    raw_artifacts = _closed(bundle["artifacts"], _ARTIFACT_FIELDS, "generated bundle.artifacts")
    artifacts = {name: _reference(raw_artifacts[name], f"generated bundle.artifacts.{name}") for name in sorted(_ARTIFACT_FIELDS)}

    if mode == "readme" and readme is None:
        raise ContractError("E_BUNDLE_MODE", "readme mode requires candidate README")
    if mode != "readme" and readme is not None:
        raise ContractError("E_BUNDLE_MODE", f"{mode} mode cannot contain candidate README")
    if mode == "asset-only" and not assets:
        raise ContractError("E_BUNDLE_MODE", "asset-only mode requires candidate asset")
    if mode == "audit-only" and assets:
        raise ContractError("E_BUNDLE_MODE", "audit-only mode cannot contain candidate assets")

    readme_raw = None
    if readme is not None:
        readme_raw = _read_bytes(artifact_root, readme, "generated bundle.candidate.readme")
    for index, reference in enumerate(assets):
        _read_bytes(artifact_root, reference, f"generated bundle.candidate.assets[{index}]")
    plan = validate_readme_plan_v2(_read_json(artifact_root, artifacts["plan"], "generated bundle.artifacts.plan"), mode=mode)
    if readme is not None and readme["path"] != plan["locales"][0]["readme_path"]:
        raise ContractError("E_CLAIM_COVERAGE", "candidate README must match first declared README path")
    retrieval = _read_json(artifact_root, artifacts["retrieval"], "generated bundle.artifacts.retrieval")
    if type(retrieval.get("schema_version")) is not int or retrieval["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle retrieval artifact requires schema_version 1")
    evidence = validate_evidence_graph(_read_json(artifact_root, artifacts["evidence"], "generated bundle.artifacts.evidence"))
    graph_ids = {fact["fact_id"] for fact in evidence["facts"]}
    if not set(plan["evidence_ids"]).issubset(graph_ids):
        raise ContractError("E_CLAIM_EVIDENCE", "README plan references missing evidence")
    if claims_override is None:
        raw_claims = _read_json(artifact_root, artifacts["claim_map"], "generated bundle.artifacts.claim_map")
    else:
        raw_claims = copy.deepcopy(dict(claims_override))
        if canonical_sha256(raw_claims) != artifacts["claim_map"]["sha256"]:
            raise ContractError("E_BUNDLE_HASH", "generated bundle.artifacts.claim_map bytes differ from reference")
    claims = validate_claim_map(raw_claims, evidence_graph=evidence)
    _validate_claim_content(claims, _readme_blocks(artifact_root, readme, plan["locales"], readme_raw))
    claim_ids = {
        identifier
        for collection in (claims["markdown_blocks"], claims["diagram_labels"])
        for claim in collection
        for identifier in claim["evidence_ids"]
    }
    if not claim_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "claim map references evidence outside README plan")
    manifest = validate_asset_manifest(
        _read_json(artifact_root, artifacts["asset_manifest"], "generated bundle.artifacts.asset_manifest"),
        evidence_graph=evidence,
        artifact_root=artifact_root,
        candidate_assets=assets,
    )
    asset_ids = {identifier for asset in manifest["assets"] for identifier in asset["evidence_ids"]}
    if not asset_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "asset manifest references evidence outside README plan")
    used_locales = {
        *(asset["locale"] for asset in manifest["assets"] if not asset["language_neutral"]),
        *(
            claim["claim_id"].split(":", 2)[1]
            for collection in (claims["markdown_blocks"], claims["diagram_labels"])
            for claim in collection
            if len(claim["claim_id"].split(":", 2)) == 3
            and claim["claim_id"].split(":", 2)[1] in {entry["tag"] for entry in plan["locales"]}
        ),
    }
    if not used_locales.issubset({entry["tag"] for entry in plan["locales"]}):
        raise ContractError("E_CLAIM_LANGUAGE", "claim or asset locale differs from README plan")
    evaluation = _closed(
        _read_json(artifact_root, artifacts["evaluation"], "generated bundle.artifacts.evaluation"),
        {"schema_version", "status", "candidate_sha256"},
        "evaluation artifact",
    )
    if evaluation != {"schema_version": 2, "status": "pass", "candidate_sha256": candidate_hash}:
        raise ContractError("E_EVALUATION_DRIFT", "evaluation does not pass for exact candidate")
    return {
        "schema_version": 2,
        "status": "pass",
        "mode": mode,
        "bundle_sha256": canonical_sha256(bundle),
        "evidence_sha256": evidence["evidence_sha256"],
        "candidate_sha256": candidate_hash,
        "candidate_count": (1 if readme is not None else 0) + len(assets),
    }


def _v3_reference(value: Any, context: str, *, expected_path: str | None = None) -> dict[str, str]:
    try:
        reference = _reference(value, context)
    except ContractError as exc:
        if exc.code == "E_EVIDENCE_PATH":
            raise ContractError("E_PATH", f"{context} must be a safe relative POSIX reference") from exc
        raise
    if expected_path is not None and reference["path"] != expected_path:
        raise ContractError("E_VISUAL_PATH", f"{context}.path must be {expected_path}")
    return reference


def _v3_compiled(value: Any) -> dict[str, Any]:
    compiled = _closed(value, _V3_COMPILED_FIELDS, "generated bundle.compiled")
    inventory = _v3_reference(
        compiled["inventory"],
        "generated bundle.compiled.inventory",
        expected_path="compiled/inventory.json",
    )
    fingerprint = _sha(compiled["fingerprint"], "generated bundle.compiled.fingerprint")
    if compiled["retention"] != "manual":
        raise ContractError("E_VISUAL_FINGERPRINT", "generated bundle.compiled.retention must be manual")
    return {"inventory": inventory, "fingerprint": fingerprint, "retention": "manual"}


def _v3_svg_reference(value: Any, context: str) -> tuple[dict[str, str], str, str]:
    # Use the v3 path adapter so unsafe paths are normalized to the v3
    # boundary's E_PATH code rather than leaking the legacy evidence code.
    reference = _v3_reference(value, context)
    match = _V3_SVG_PATH.fullmatch(reference["path"])
    if match is None:
        raise ContractError(
            "E_BUNDLE_ASSET",
            f"{context}.path must be a publishable stage-6 SVG",
        )
    try:
        from ..contracts.locale import parse_locale

        locale = parse_locale(match.group("locale"), f"{context}.locale")
    except ContractError as exc:
        raise ContractError("E_CLAIM_LANGUAGE", f"{context}.path has an unsupported locale") from exc
    return reference, locale, match.group("variant")


def _v3_readmes(
    value: Any,
    plan: Mapping[str, Any],
    root: Path,
    mode: str,
    *,
    context: str = "generated bundle.candidate.readmes",
) -> tuple[list[dict[str, str]], dict[str, list[bytes]]]:
    if not isinstance(value, list) or len(value) > MAX_CANDIDATES:
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be a bounded array")
    locales = plan.get("locales")
    if not isinstance(locales, list) or not locales:
        raise ContractError("E_SCHEMA_TYPE", "README plan.locales must be a non-empty array")
    if mode != "readme":
        if value:
            raise ContractError("E_BUNDLE_MODE", f"{mode} mode cannot contain candidate README refs")
        return [], {}
    if len(value) != len(locales):
        raise ContractError("E_CLAIM_LANGUAGE", "candidate README refs must cover every Plan v3 locale")
    refs: list[dict[str, str]] = []
    documents: dict[str, list[bytes]] = {}
    for index, (raw, locale_entry) in enumerate(zip(value, locales, strict=True)):
        reference = _v3_reference(raw, f"{context}[{index}]")
        expected_path = locale_entry["readme_path"]
        if reference["path"] != expected_path:
            raise ContractError("E_CLAIM_LANGUAGE", f"{context}[{index}] does not match Plan v3 locale order")
        raw_readme = _read_bytes(root, reference, f"{context}[{index}]")
        locale = locale_entry["tag"]
        documents[locale] = canonical_markdown_blocks(raw_readme, f"candidate README {locale}")
        refs.append(reference)
    return refs, documents


def _v3_candidate(
    value: Any,
    plan: Mapping[str, Any],
    root: Path,
    mode: str,
) -> tuple[dict[str, Any], dict[str, list[bytes]]]:
    candidate = _closed(value, _V3_CANDIDATE_FIELDS, "generated bundle.candidate")
    readmes, documents = _v3_readmes(candidate["readmes"], plan, root, mode)
    raw_assets = candidate["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_CANDIDATES:
        raise ContractError("E_SCHEMA_TYPE", "generated bundle.candidate.assets must be a bounded array")
    if mode == "audit-only" and raw_assets:
        raise ContractError("E_BUNDLE_MODE", "audit-only mode cannot contain publishable SVGs")
    if mode in {"readme", "asset-only"} and not raw_assets:
        raise ContractError("E_BUNDLE_MODE", f"{mode} mode requires publishable SVGs")
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_assets):
        reference, _, _ = _v3_svg_reference(raw, f"generated bundle.candidate.assets[{index}]")
        if reference["path"] in seen:
            raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate SVG paths must be unique")
        seen.add(reference["path"])
        _read_bytes(
            root,
            reference,
            f"generated bundle.candidate.assets[{index}]",
            maximum=MAX_COMPILED_BYTES,
        )
        assets.append(reference)
    if assets != sorted(assets, key=lambda item: item["path"]):
        raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate SVGs must use path order")
    candidate_body = {"readmes": readmes, "assets": assets}
    candidate_hash = _sha(candidate["candidate_sha256"], "generated bundle.candidate.candidate_sha256")
    if canonical_sha256(candidate_body) != candidate_hash:
        raise ContractError("E_BUNDLE_HASH", "generated bundle candidate hash differs from references")
    return {**candidate_body, "candidate_sha256": candidate_hash}, documents


def _validate_generated_bundle_v3(payload: Any, artifact_root: Path) -> dict[str, object]:
    """Validate one opt-in compiled Generated Bundle v3.

    The bundle is a projection over a single materialized root.  Orchestration
    owns projecting stage-5 author files and stage-6 compiled files into that
    root; this boundary enforces the closed field sets and logical topology so
    a stage-origin swap cannot become a publishable candidate.
    """

    _validate_bundle_structure(payload)
    bundle = _closed(payload, _BUNDLE_V3_FIELDS, "generated bundle")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != GENERATED_BUNDLE_V3_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle requires schema_version 3")
    mode = bundle["mode"]
    if mode not in {"readme", "asset-only", "audit-only"}:
        raise ContractError("E_BUNDLE_MODE", "generated bundle mode is unsupported")
    target = _closed(bundle["target"], _V3_TARGET_FIELDS, "generated bundle.target")
    repository = canonical_repository(target["repository"])
    if target["repository"] != repository:
        raise ContractError("E_BUNDLE_TARGET", "generated bundle repository must be canonical owner/name")
    if not isinstance(target["base_sha"], str) or not _SHA1.fullmatch(target["base_sha"]):
        raise ContractError("E_BUNDLE_TARGET", "generated bundle base_sha must be immutable")

    raw_artifacts = _closed(bundle["artifacts"], _V3_ARTIFACT_FIELDS, "generated bundle.artifacts")
    artifacts = {
        name: _v3_reference(
            raw_artifacts[name],
            f"generated bundle.artifacts.{name}",
            expected_path=_V3_ARTIFACT_PATHS[name],
        )
        for name in sorted(_V3_ARTIFACT_FIELDS)
    }
    # These are fixed logical stage boundaries, not user-supplied origin
    # metadata. Task 42 materializes the six refs into one root consumed here.
    visual_spec_reference = artifacts["visual_spec"]

    plan = validate_readme_plan(
        _read_json(artifact_root, artifacts["plan"], "generated bundle.artifacts.plan"),
        mode=mode,
    )
    if plan["schema_version"] != 3:
        raise ContractError("E_SCHEMA_VERSION", "compiled bundle requires README Plan v3")
    if plan["diagram_route"] != "compiled":
        raise ContractError("E_BUNDLE_PLAN", "compiled bundle requires Plan v3 diagram_route compiled")
    candidate, documents = _v3_candidate(bundle["candidate"], plan, artifact_root, mode)

    retrieval = _read_json(artifact_root, artifacts["retrieval"], "generated bundle.artifacts.retrieval")
    if type(retrieval.get("schema_version")) is not int or retrieval["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle retrieval artifact requires schema_version 1")
    evidence = validate_evidence_graph(
        _read_json(artifact_root, artifacts["evidence"], "generated bundle.artifacts.evidence")
    )
    graph_ids = {fact["fact_id"] for fact in evidence["facts"]}
    if not set(plan["evidence_ids"]).issubset(graph_ids):
        raise ContractError("E_CLAIM_EVIDENCE", "README plan references missing evidence")

    raw_spec = _read_bytes(artifact_root, visual_spec_reference, "generated bundle.artifacts.visual_spec")
    spec_payload = _read_json(
        artifact_root,
        visual_spec_reference,
        "generated bundle.artifacts.visual_spec",
        depth_code="E_VISUAL_SPEC_SIZE",
    )
    spec = validate_visual_spec(spec_payload, evidence_graph=evidence)
    if spec.canonical_bytes() != raw_spec:
        raise ContractError("E_BUNDLE_HASH", "stage-5 Visual Spec is not canonical")
    if spec.locale not in {entry["tag"] for entry in plan["locales"]}:
        raise ContractError("E_CLAIM_LANGUAGE", "Visual Spec locale is absent from README Plan v3")

    claims = validate_claim_map(
        _read_json(artifact_root, artifacts["claim_map"], "generated bundle.artifacts.claim_map"),
        evidence_graph=evidence,
        visual_spec=spec_payload,
    )
    _validate_claim_content(claims, documents)
    claim_ids = {
        identifier
        for collection in (claims["markdown_blocks"], claims["diagram_labels"])
        for claim in collection
        for identifier in claim["evidence_ids"]
    }
    if not claim_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "claim map references evidence outside README plan")

    compiled = _v3_compiled(bundle["compiled"])
    manifest_raw = _read_bytes(
        artifact_root,
        artifacts["asset_manifest"],
        "generated bundle.artifacts.asset_manifest",
        maximum=MAX_COMPILED_BYTES,
    )
    try:
        manifest_payload = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("E_INPUT_JSON", "generated bundle asset manifest must be canonical UTF-8 JSON") from exc
    manifest = validate_asset_manifest(
        manifest_payload,
        evidence_graph=evidence,
        artifact_root=artifact_root,
        # Asset Manifest v3 binds the author Visual Spec through its existing
        # candidate-asset hook.  The Visual Spec is stage-5 input, never a
        # publishable candidate; SVG candidates are checked below.
        candidate_assets=[visual_spec_reference],
    )
    if canonical_json_bytes(manifest) != manifest_raw:
        raise ContractError("E_BUNDLE_HASH", "generated bundle asset manifest is not canonical")
    if not isinstance(manifest.get("compiled"), Mapping):
        raise ContractError("E_VISUAL_FINGERPRINT", "generated bundle asset manifest lacks compiled projection")
    manifest_compiled = manifest["compiled"]
    manifest_spec = manifest_compiled["spec"]
    if manifest_spec["sha256"] != hashlib.sha256(raw_spec).hexdigest():
        raise ContractError("E_VISUAL_FINGERPRINT", "Asset Manifest compiled spec differs from stage-5 Visual Spec")
    compiled_spec_path = artifact_root / "compiled" / "visual-spec.json"
    try:
        compiled_spec_raw = read_regular_bytes(compiled_spec_path, maximum=MAX_COMPILED_BYTES, path_code="E_PATH", size_code="E_INPUT_SIZE")
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            raise ContractError("E_VISUAL_PATH", "stage-6 compiled Visual Spec is unavailable") from exc
        raise
    if compiled_spec_raw != raw_spec:
        raise ContractError("E_VISUAL_FINGERPRINT", "stage-6 compiled Visual Spec differs from stage-5 source")
    if compiled["inventory"] != manifest_compiled["inventory"]:
        raise ContractError("E_VISUAL_FINGERPRINT", "bundle compiled inventory differs from Asset Manifest")
    inventory_raw = _read_bytes(
        artifact_root,
        compiled["inventory"],
        "generated bundle.compiled.inventory",
        maximum=MAX_COMPILED_BYTES,
    )
    try:
        inventory = json.loads(inventory_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled inventory must be canonical JSON") from exc
    if not isinstance(inventory, dict) or inventory.get("inventory_sha256") != compiled["fingerprint"]:
        raise ContractError("E_VISUAL_FINGERPRINT", "bundle compiled fingerprint differs from inventory")

    manifest_svg_refs = {
        (asset["locale"], asset["variant"]): {
            "path": asset["path"],
            "sha256": asset["artifact_sha256"],
        }
        for asset in manifest["assets"]
    }
    plan_locales = {entry["tag"] for entry in plan["locales"]}
    expected_svg_keys = {
        (spec.locale, variant)
        for variant in spec.variants
    }
    if spec.locale not in plan_locales:
        raise ContractError("E_CLAIM_LANGUAGE", "Visual Spec locale is absent from README Plan v3")
    if set(manifest_svg_refs) != expected_svg_keys:
        raise ContractError("E_CLAIM_LANGUAGE", "Asset Manifest SVG variants must pair Visual Spec locale/variants")
    candidate_svg_refs: dict[tuple[str, str], dict[str, str]] = {}
    for index, ref in enumerate(candidate["assets"]):
        normalized, locale, variant = _v3_svg_reference(ref, f"generated bundle.candidate.assets[{index}]")
        candidate_svg_refs[(locale, variant)] = normalized
    if candidate_svg_refs != manifest_svg_refs and mode in {"readme", "asset-only"}:
        raise ContractError("E_BUNDLE_ASSET", "candidate SVGs must close over Asset Manifest v3 assets")
    if mode == "audit-only" and candidate_svg_refs:
        raise ContractError("E_BUNDLE_MODE", "audit-only mode cannot publish SVG assets")
    asset_ids = {identifier for asset in manifest["assets"] for identifier in asset["evidence_ids"]}
    if not asset_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "asset manifest references evidence outside README plan")

    # Reuse Task 36's trust boundary for complete inventory closure, no-follow
    # ancestry, exact hashes, and rejection of extra/missing compiled or SVG
    # files.  It intentionally receives the full bundle shape.
    load_compiled_visual(artifact_root, bundle)
    return {
        "schema_version": 3,
        "status": "pass",
        "mode": mode,
        "bundle_sha256": canonical_sha256(bundle),
        "evidence_sha256": evidence["evidence_sha256"],
        "candidate_sha256": candidate["candidate_sha256"],
        "inventory_sha256": compiled["fingerprint"],
        "candidate_count": len(candidate["readmes"]) + len(candidate["assets"]),
    }


def validate_generated_bundle_v3(payload: Any, artifact_root: Path) -> dict[str, object]:
    return _validate_generated_bundle_v3(payload, artifact_root)


def assemble_generated_bundle_v3(
    artifact_root: Path,
    *,
    mode: str,
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    compiled: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble a canonical Bundle v3 over already materialized stage bytes."""

    if not isinstance(candidate, Mapping) or not isinstance(artifacts, Mapping):
        raise ContractError("E_SCHEMA_TYPE", "generated bundle v3 candidate and artifacts must be objects")
    candidate_body = {
        "readmes": copy.deepcopy(candidate.get("readmes")),
        "assets": copy.deepcopy(candidate.get("assets")),
    }
    candidate_body["candidate_sha256"] = canonical_sha256(candidate_body)
    artifacts_copy = copy.deepcopy(dict(artifacts))
    if not isinstance(compiled, Mapping):
        raise ContractError("E_SCHEMA_TYPE", "generated bundle v3 compiled must be an object")
    compiled_copy = copy.deepcopy(dict(compiled))
    bundle = {
        "schema_version": GENERATED_BUNDLE_V3_SCHEMA_VERSION,
        "mode": mode,
        "target": {"repository": canonical_repository(target.get("repository")), "base_sha": target.get("base_sha")},
        "candidate": candidate_body,
        "artifacts": artifacts_copy,
        "compiled": compiled_copy,
    }
    _validate_generated_bundle_v3(bundle, artifact_root)
    return copy.deepcopy(bundle)


def write_generated_bundle_v3_atomic(destination: Path, payload: Any, *, artifact_root: Path) -> None:
    validate_generated_bundle_v3(payload, artifact_root)
    write_canonical_json_atomic(destination, payload)


def validate_generated_bundle_v2(payload: Any, artifact_root: Path) -> dict[str, object]:
    return _validate_generated_bundle_v2(payload, artifact_root)


def assemble_generated_bundle(
    artifact_root: Path,
    *,
    mode: str,
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_copy = copy.deepcopy(dict(candidate))
    candidate_copy["candidate_sha256"] = canonical_sha256(candidate_copy)
    artifacts_copy = copy.deepcopy(dict(artifacts))
    readme = candidate_copy.get("readme")
    if mode == "readme" and isinstance(readme, Mapping):
        readme_reference = _reference(readme, "generated bundle.candidate.readme")
        readme_raw = _read_bytes(artifact_root, readme_reference, "generated bundle.candidate.readme")
        plan_reference = _reference(artifacts_copy.get("plan"), "generated bundle.artifacts.plan")
        plan = validate_readme_plan_v2(
            _read_json(artifact_root, plan_reference, "generated bundle.artifacts.plan"),
            mode=mode,
        )
        claim_reference = _reference(artifacts_copy.get("claim_map"), "generated bundle.artifacts.claim_map")
        claims = _derive_claim_content(
            _read_json(artifact_root, claim_reference, "generated bundle.artifacts.claim_map"),
            _readme_blocks(artifact_root, readme_reference, plan["locales"], readme_raw),
        )
        artifacts_copy["claim_map"]["sha256"] = canonical_sha256(claims)
    else:
        claims = None
    bundle = {
        "schema_version": GENERATED_BUNDLE_SCHEMA_VERSION,
        "mode": mode,
        "target": {"repository": canonical_repository(target.get("repository")), "base_sha": target.get("base_sha")},
        "candidate": candidate_copy,
        "artifacts": artifacts_copy,
    }
    _validate_generated_bundle_v2(bundle, artifact_root, claims_override=claims)
    if claims is not None:
        claim_path = artifact_root.joinpath(*Path(claim_reference["path"]).parts)
        write_canonical_json_atomic(claim_path, claims)
    return copy.deepcopy(bundle)


def write_generated_bundle_atomic(destination: Path, payload: Any, *, artifact_root: Path) -> None:
    validate_generated_bundle_v2(payload, artifact_root)
    write_canonical_json_atomic(destination, payload)
