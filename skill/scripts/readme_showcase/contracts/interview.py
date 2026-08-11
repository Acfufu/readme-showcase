from __future__ import annotations

from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes


INTERVIEW_SCHEMA_VERSION = 1
QUESTION_IDS = ("goal", "scope", "audience", "proof", "style", "extras")
CONFIDENCES = ("explicit", "defaulted", "uncertain", "custom")
MAX_CHOICE_CHARACTERS = 64
_TOP_LEVEL = {"schema_version", "answers"}
_ANSWER_FIELDS = {"question_id", "choice", "confidence"}


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def validate_interview(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", "interview must be a JSON object")

    unknown = sorted(set(value) - _TOP_LEVEL)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"interview contains unknown field: {unknown[0]}")

    missing = sorted(_TOP_LEVEL - set(value))
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"interview is missing required field: {missing[0]}")

    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != INTERVIEW_SCHEMA_VERSION
    ):
        _fail("E_SCHEMA_VERSION", "interview requires schema_version 1")

    answers = value["answers"]
    if not isinstance(answers, list) or len(answers) != len(QUESTION_IDS):
        _fail("E_INTERVIEW_ANSWER_COUNT", "interview requires exactly six answers")

    seen: set[str] = set()
    for item in answers:
        if not isinstance(item, dict):
            _fail("E_SCHEMA_TYPE", "interview answer must be a JSON object")

        item_unknown = sorted(set(item) - _ANSWER_FIELDS)
        if item_unknown:
            _fail(
                "E_SCHEMA_UNKNOWN_FIELD",
                f"interview answer contains unknown field: {item_unknown[0]}",
            )

        item_missing = sorted(_ANSWER_FIELDS - set(item))
        if item_missing:
            _fail(
                "E_SCHEMA_MISSING_FIELD",
                f"interview answer is missing required field: {item_missing[0]}",
            )

        question_id = item["question_id"]
        if question_id not in QUESTION_IDS:
            _fail("E_INTERVIEW_QUESTION_ID", "interview answer has an unsupported question_id")
        if question_id in seen:
            _fail("E_INTERVIEW_DUPLICATE", "interview answers repeat a question_id")
        seen.add(question_id)

        confidence = item["confidence"]
        if confidence not in CONFIDENCES:
            _fail("E_INTERVIEW_CONFIDENCE", "interview answer has an unsupported confidence")

        choice = item["choice"]
        if (
            not isinstance(choice, str)
            or not choice
            or len(choice) > MAX_CHOICE_CHARACTERS
        ):
            _fail("E_INTERVIEW_CHOICE", "interview answer choice must be a bounded string")

    canonical_json_bytes(value)
    return value


__all__ = [
    "CONFIDENCES",
    "INTERVIEW_SCHEMA_VERSION",
    "MAX_CHOICE_CHARACTERS",
    "QUESTION_IDS",
    "validate_interview",
]
