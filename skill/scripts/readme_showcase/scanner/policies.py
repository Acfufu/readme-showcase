from __future__ import annotations

import importlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


_CONTRACTS = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
read_regular_bytes = _CONTRACTS.read_regular_bytes


CONFIG_NAME = ".readme-showcase.json"
MAX_CONFIG_BYTES = 64 * 1024
FIXED_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".agents", ".claude", ".codex", ".cursor", ".git", ".hg", ".omo", ".svn", ".trellis", ".venv",
        "__pycache__", "build", "dist", "evaluation-only", "node_modules", "vendor", "venv",
    }
)
SECRET_NAMES = frozenset(
    {".env", ".env.local", "credentials", "credentials.json", "id_dsa", "id_ed25519", "id_rsa"}
)
SECRET_SUFFIXES = frozenset({".key", ".p12", ".pem"})


@dataclass(frozen=True)
class ProfileLimits:
    indexed_files: int
    content_files: int
    total_bytes: int
    seconds: int

    def as_dict(self) -> dict[str, int]:
        return {
            "max_content_files": self.content_files,
            "max_indexed_files": self.indexed_files,
            "max_seconds": self.seconds,
            "max_total_bytes": self.total_bytes,
        }


PROFILE_LIMITS = {
    "fast": ProfileLimits(5_000, 50, 2 * 1024 * 1024, 5),
    "balanced": ProfileLimits(20_000, 250, 16 * 1024 * 1024, 20),
    "deep": ProfileLimits(100_000, 1_000, 64 * 1024 * 1024, 60),
}
HARD_LIMITS = PROFILE_LIMITS["deep"]
_LIMIT_FIELDS = frozenset({"max_indexed_files", "max_content_files", "max_total_bytes", "max_seconds"})
_SCANNER_FIELDS = frozenset({"tracked_only", "profile", "include", "exclude", "secret_policy", "limits"})


@dataclass(frozen=True)
class ScannerPolicy:
    profile: str
    tracked_only: bool
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    secret_policy: str
    limits: ProfileLimits

    def selects(self, path: str) -> bool:
        return (
            not _protected_path(path)
            and any(posix_glob_matches(pattern, path) for pattern in self.include)
            and not any(posix_glob_matches(pattern, path) for pattern in self.exclude)
        )


def _fail(message: str) -> None:
    raise ContractError("E_SCANNER_CONFIG", message)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"configuration contains duplicate key: {key}")
        result[key] = value
    return result


def _object(value: object, context: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail(f"{context} contains unknown field: {unknown[0]}")
    return value


def _patterns(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(f"{context} must be a list of strings")
    patterns = tuple(value)
    for pattern in patterns:
        _glob_regex(pattern)
    return patterns


def _glob_regex(pattern: str) -> re.Pattern[str]:
    if not pattern or "\\" in pattern or "\x00" in pattern or pattern.startswith("/") or "//" in pattern:
        _fail("glob must be a nonempty normalized relative POSIX path")
    parts = pattern.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("glob must be a normalized relative POSIX path")
    expression = "^"
    for index, part in enumerate(parts):
        if part == "**":
            expression += ".*" if index == len(parts) - 1 else "(?:[^/]+/)*"
        else:
            if "**" in part:
                _fail("double star must occupy a complete path segment")
            expression += "".join("[^/]*" if character == "*" else re.escape(character) for character in part)
            if index != len(parts) - 1:
                expression += "/"
    return re.compile(f"{expression}$")


def posix_glob_matches(pattern: str, path: str) -> bool:
    if not path or "\\" in path or "\x00" in path:
        return False
    value = PurePosixPath(path)
    if value.is_absolute() or "." in value.parts or ".." in value.parts or value.as_posix() != path:
        return False
    return _glob_regex(pattern).fullmatch(path) is not None


def _protected_path(path: str) -> bool:
    return any(part in FIXED_EXCLUDED_DIRECTORIES for part in PurePosixPath(path).parts)


def is_secret_path(path: str) -> bool:
    value = PurePosixPath(path)
    return value.name.lower() in SECRET_NAMES or value.suffix.lower() in SECRET_SUFFIXES


def _limits(value: object, profile: str) -> ProfileLimits:
    selected = PROFILE_LIMITS[profile]
    if value is None:
        return selected
    limits = _object(value, "scanner.limits", _LIMIT_FIELDS)
    values = selected.as_dict()
    maxima = HARD_LIMITS.as_dict()
    for key, candidate in limits.items():
        if type(candidate) is not int or candidate < 1:
            _fail(f"scanner.limits.{key} must be a positive integer")
        if candidate > maxima[key]:
            _fail(f"scanner.limits.{key} exceeds the hard maximum")
        values[key] = candidate
    return ProfileLimits(
        indexed_files=values["max_indexed_files"],
        content_files=values["max_content_files"],
        total_bytes=values["max_total_bytes"],
        seconds=values["max_seconds"],
    )


def _read_config(path: Path) -> dict[str, Any]:
    try:
        raw = read_regular_bytes(path, maximum=MAX_CONFIG_BYTES)
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_pairs)
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"configuration is not valid UTF-8 JSON: {path.name}")
    if not isinstance(value, dict):
        _fail("configuration must be an object")
    return value


def load_scanner_policy(root: Path) -> ScannerPolicy | None:
    path = root / CONFIG_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail(f"cannot inspect configuration: {exc}")
    if not stat.S_ISREG(info.st_mode):
        _fail("configuration must be a regular file")
    try:
        payload = _read_config(path)
    except ContractError as exc:
        if exc.code == "E_SCANNER_CONFIG":
            raise
        _fail(f"cannot read configuration: {exc}")
    top = _object(payload, "configuration", frozenset({"scanner"}))
    if "scanner" not in top:
        _fail("configuration is missing scanner")
    scanner = _object(top["scanner"], "scanner", _SCANNER_FIELDS)
    profile = scanner.get("profile", "balanced")
    if not isinstance(profile, str) or profile not in PROFILE_LIMITS:
        _fail("scanner.profile must be fast, balanced, or deep")
    tracked_only = scanner.get("tracked_only", True)
    if type(tracked_only) is not bool:
        _fail("scanner.tracked_only must be boolean")
    include = _patterns(scanner.get("include", ["**"]), "scanner.include")
    exclude = _patterns(scanner.get("exclude", []), "scanner.exclude")
    if any(any(part in FIXED_EXCLUDED_DIRECTORIES for part in pattern.split("/")) for pattern in include):
        _fail("scanner.include cannot name a fixed safety exclusion")
    secret_policy = scanner.get("secret_policy", "redact")
    if secret_policy != "redact":
        _fail("scanner.secret_policy must be redact")
    return ScannerPolicy(profile, tracked_only, include, exclude, secret_policy, _limits(scanner.get("limits"), profile))
