"""Vision-LLM aesthetic review with evidence-grounded rubric judging.

Optional advisory track. Default `model="auto"` inherits the host session's
LLM: probes session model + API config; falls back to host mode which writes
a review brief for the host agent to complete in-session. The host path is
same-model by construction, so it carries the informational E_REVIEW_SAME_MODEL
note just like the API path does on a family match. Reports are written with
the canonical-JSON convention (write_canonical_json_atomic).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from ...pipeline_contracts import ContractError, write_canonical_json_atomic

RUBRIC_PATH = Path(__file__).resolve().parents[3] / "references" / "visual-review-rubric.md"

_REPORT_FIELDS = frozenset({"schema_version", "status", "reviewer_model", "criteria", "pairwise", "findings"})
_CRITERION_FIELDS = frozenset({"name", "pass", "score_basis_points", "evidence", "reason"})
_EVIDENCE_FIELDS = frozenset({"screenshot", "region"})
_PAIRWISE_FIELDS = frozenset({"candidate_win_basis_points", "reference"})
_FINDING_FIELDS = frozenset({"code", "message"})

_CRITERION = re.compile(r"^### ([a-z_]+)\n(.*?)(?=^### |\Z)", re.MULTILINE | re.DOTALL)


def load_rubric(rubric_path: str | None = None) -> list[dict[str, str]]:
    text = Path(rubric_path or RUBRIC_PATH).read_text(encoding="utf-8")
    return [{"name": match.group(1), "description": match.group(2).strip()}
            for match in _CRITERION.finditer(text)]


def _session_model_probe() -> str | None:
    """Probe common session-model env vars used by host agents."""
    for name in ("ANTHROPIC_MODEL", "OPENCODE_MODEL", "OPENCODE_MODEL_ID", "CODEX_MODEL"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def _model_family(model: str) -> str | None:
    lowered = model.casefold()
    if lowered.startswith(("gpt-", "o", "openai/")):
        return "openai"
    if "claude" in lowered or "anthropic" in lowered:
        return "anthropic"
    return None


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _encode_image(path: str) -> str:
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def _chat_completion(model: str, api_base: str, api_key: str, messages: list[dict]) -> dict:
    body = json.dumps({"model": model, "messages": messages, "max_tokens": 2048}).encode("utf-8")
    request = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _build_prompt(criteria: list[dict[str, str]], screenshots: list[str],
                  reference: str | None, pairwise: bool) -> list[dict]:
    def image_part(path: str, label: str) -> dict:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_encode_image(path)}"},
        }

    content: list[dict] = [{
        "type": "text",
        "text": "You are a visual design reviewer. For EVERY criterion, you MUST "
                "cite the screenshot region (x,y,width,height in pixels) that "
                "supports your score. Respond with strict JSON only: "
                '{"criteria": [{"name": str, "pass": bool, "score_basis_points": int, '
                '"evidence": [{"screenshot": str, "region": "x,y,w,h"}], "reason": str}], '
                '"candidate_win_basis_points": int|null}. '
                "Scores are 0-10000. Do not invent defects; if a criterion is clean, "
                "state evidence as the region you checked.",
    }]
    for criterion in criteria:
        content.append({"type": "text", "text": f"### {criterion['name']}\n{criterion['description']}"})
    for index, path in enumerate(screenshots):
        content.append(image_part(path, f"candidate {index}: {Path(path).name}"))
        content.append({"type": "text", "text": f"Candidate {index} filename: {Path(path).name}"})
    if pairwise and reference:
        content.append(image_part(reference, "reference"))
        content.append({"type": "text",
                        "text": "The reference image is the quality bar. For each "
                                "criterion, also state candidate_win_basis_points: an "
                                "integer 0-10000 for how strongly the candidate beats "
                                "the reference (null when the reference is absent or "
                                "the result is a tie)."})
    return [{"role": "user", "content": content}]


def _write_host_brief(criteria: list[dict[str, str]], screenshots: list[str],
                      reference: str | None, out_dir: str) -> None:
    """Write a review brief the host agent completes in-session with its own model."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Vision Review Brief (host session)",
        "",
        "Complete this review with your own visual abilities and write the result to",
        "`vision-review-report.v1.json` in this directory. Strict JSON, no prose outside",
        "the file. Every criterion MUST cite the screenshot region (x,y,width,height)",
        "that supports the score. Do not invent defects.",
        "",
        "```json",
        '{"schema_version": 1, "status": "pass", "reviewer_model": "host-session",',
        ' "criteria": [{"name": "<criterion>", "pass": true, "score_basis_points": 10000,',
        '   "evidence": [{"screenshot": "<file>", "region": "x,y,w,h"}],',
        '   "reason": "one sentence with the region justification"}],',
        ' "pairwise": {"candidate_win_basis_points": null, "reference": null},',
        ' "findings": []}',
        "```",
        "",
        "## Screenshots under review",
    ]
    for path in screenshots:
        lines.append(f"- `{path}`")
    if reference:
        lines.append("")
        lines.append(f"Reference (quality bar, pairwise): `{reference}`")
    lines.append("")
    lines.append("## Frozen rubric")
    for criterion in criteria:
        lines.append(f"### {criterion['name']}")
        lines.append(criterion["description"])
    (out / "vision-review-brief.md").write_text("\n".join(lines), encoding="utf-8")


def _host_report(out_dir: str, reference: str | None, findings: list[dict[str, str]]) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "status": "host",
        "reviewer_model": "host-session",
        "criteria": [],
        "pairwise": {"candidate_win_basis_points": None, "reference": reference},
        "findings": sorted(findings, key=lambda item: (item["code"], item["message"])),
    }
    write_canonical_json_atomic(out / "vision-review-report.v1.json", report)
    return report


def validate_vision_review_report_v1(value: Any) -> dict[str, Any]:
    """Validate a closed Vision Review Report v1 value; raises ContractError.

    The parity index resolves this as the schema's python_validator, so its
    error codes must match the invalid-fixture case codes in
    tests/fixtures/contracts/vision-review-report-v1.invalid.json.
    """
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", "vision review report must be an object")
    unknown = sorted(set(value) - _REPORT_FIELDS)
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD",
                            f"vision review report contains unknown field: {unknown[0]}")
    missing = sorted(_REPORT_FIELDS - set(value))
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD",
                            f"vision review report is missing field: {missing[0]}")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "vision review report requires schema_version 1")
    if value["status"] not in ("pass", "fail", "skipped", "host"):
        raise ContractError("E_SCHEMA_VALUE", "vision review report status is not recognized")
    criteria = value["criteria"]
    if not isinstance(criteria, list):
        raise ContractError("E_SCHEMA_TYPE", "vision review report criteria must be an array")
    for criterion in criteria:
        if not isinstance(criterion, dict) or set(criterion) != _CRITERION_FIELDS:
            raise ContractError("E_SCHEMA_TYPE",
                                "vision review criterion must contain exactly name, pass, "
                                "score_basis_points, evidence, reason")
        if (not isinstance(criterion["score_basis_points"], int)
                or not 0 <= criterion["score_basis_points"] <= 10000):
            raise ContractError("E_SCHEMA_VALUE",
                                "criterion score_basis_points must be an integer 0-10000")
        for evidence in criterion["evidence"]:
            if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
                raise ContractError("E_SCHEMA_TYPE",
                                    "criterion evidence must contain exactly screenshot and region")
    pairwise = value["pairwise"]
    if not isinstance(pairwise, dict) or set(pairwise) != _PAIRWISE_FIELDS:
        raise ContractError("E_SCHEMA_TYPE",
                            "pairwise must contain exactly candidate_win_basis_points and reference")
    points = pairwise["candidate_win_basis_points"]
    if points is not None and (not isinstance(points, int) or not 0 <= points <= 10000):
        raise ContractError("E_SCHEMA_VALUE",
                            "pairwise.candidate_win_basis_points must be int|null within 0-10000")
    findings = value["findings"]
    if not isinstance(findings, list):
        raise ContractError("E_SCHEMA_TYPE", "vision review report findings must be an array")
    for item in findings:
        if not isinstance(item, dict) or set(item) != _FINDING_FIELDS:
            raise ContractError("E_SCHEMA_TYPE", "finding entries must be {code, message} objects")
    ordered = sorted(findings, key=lambda item: (item["code"], item["message"]))
    if findings != ordered:
        raise ContractError("E_SCHEMA_VALUE",
                            "vision review report findings must be canonically sorted by (code, message)")
    return value


def review_screenshots(
    screenshots: list[str],
    out_dir: str,
    *,
    model: str = "auto",
    api_base: str | None = None,
    api_key_env: str = "VISION_REVIEW_API_KEY",
    reference: str | None = None,
) -> dict:
    if not screenshots:
        return {"schema_version": 1, "status": "skipped", "reviewer_model": model,
                "criteria": [], "pairwise": {"candidate_win_basis_points": None,
                "reference": reference},
                "findings": [_finding("E_REVIEW_SKIPPED", "E_REVIEW_SKIPPED: no screenshots provided")]}
    criteria = load_rubric()

    # Resolve reviewer: explicit > env > session probe > host mode.
    api_key = os.environ.get(api_key_env)
    resolved_model: str | None = None
    if model != "auto":
        resolved_model = model
    elif os.environ.get("VISION_REVIEW_MODEL"):
        resolved_model = os.environ["VISION_REVIEW_MODEL"]
    elif api_key and _session_model_probe():
        resolved_model = _session_model_probe()

    findings: list[dict[str, str]] = []
    if resolved_model and api_key:
        family = _model_family(resolved_model)
        if family:
            findings.append(_finding(
                "E_REVIEW_SAME_MODEL",
                f"reviewer family {family} may match the generator family; "
                "self-preference bias applies (research: Panickssery 2024). "
                "Pass --model <other-family> for an independent judge."))
        pairwise = reference is not None
        prompt = _build_prompt(criteria, screenshots, reference, pairwise)
        resolved_base = api_base or os.environ.get("VISION_REVIEW_API_BASE",
                                                   "https://api.openai.com/v1")
        try:
            completion = _chat_completion(resolved_model, resolved_base, api_key, prompt)
        except Exception as exc:  # network/API failure → host mode, not blocking
            findings.append(_finding("E_REVIEW_API_FAILED", str(exc)))
            _write_host_brief(criteria, screenshots, reference, out_dir)
            return _host_report(out_dir, reference, findings)
        try:
            # Shape extraction, JSON parse, and dict access all live inside the
            # exception-handled region: a 200 response with a non-standard shape
            # (gateway {"error": ...}, JSON array, ...) must fall back to host
            # mode, never block.
            content = completion["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            criteria_out = parsed.get("criteria", [])
            # "fail" is REACHABLE: derived from the model's own criterion verdicts.
            status = "pass" if all(
                isinstance(c, dict) and c.get("pass") is True for c in criteria_out
            ) else "fail"
            report = {
                "schema_version": 1,
                "status": status,
                "reviewer_model": resolved_model,
                "criteria": criteria_out,
                "pairwise": {
                    "candidate_win_basis_points": parsed.get("candidate_win_basis_points"),
                    "reference": reference,
                },
                "findings": sorted(findings, key=lambda item: (item["code"], item["message"])),
            }
        except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                message = "model returned non-JSON"
            else:
                message = f"model returned malformed response: {exc!r}"
            findings.append(_finding("E_REVIEW_PARSE", message))
            _write_host_brief(criteria, screenshots, reference, out_dir)
            return _host_report(out_dir, reference, findings)
    else:
        findings.append(_finding(
            "E_REVIEW_SAME_MODEL",
            "host-session review uses the generator's own model by construction; "
            "self-preference bias applies (research: Panickssery 2024). "
            "Pass --model <other-family> for an independent judge."))
        findings.append(_finding(
            "E_REVIEW_HOST",
            "no explicit model or API config; host session performs the review "
            "from vision-review-brief.md"))
        _write_host_brief(criteria, screenshots, reference, out_dir)
        return _host_report(out_dir, reference, findings)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_canonical_json_atomic(out / "vision-review-report.v1.json", report)
    return report
