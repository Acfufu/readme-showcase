from __future__ import annotations

import ast
import copy
import json
import os
import re
import stat
import time
import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from ..contracts.common import ContractError, normalize_posix_path
from ..contracts.evidence import build_fact
from .policies import FIXED_EXCLUDED_DIRECTORIES, is_secret_path
from .service import _read


MAX_FILES = 2_000
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 5.0
MAX_LINES = 4_000
MAX_LINE_BYTES = 4_096
MAX_FACTS_PER_CONFIG = 100
_SECRET_KEYS = re.compile(r"(?:api[_-]?key|credential|password|secret|token)", re.IGNORECASE)
_README_SUFFIXES = {"", ".md", ".rst", ".txt"}
_CONFIG_MANIFESTS = {"package.json", "pyproject.toml"}


def _pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _line_number(error: BaseException) -> int:
    line = getattr(error, "lineno", None)
    if type(line) is int and line > 0:
        return line
    match = re.search(r"(?:at line|line) (\d+)", str(error))
    return int(match.group(1)) if match else 1


def _warning(code: str, path: str, line: int = 1) -> dict[str, object]:
    return {"code": code, "line": max(1, line), "path": path}


def _fact(
    raw: bytes,
    path: str,
    kind: str,
    locator: Mapping[str, object],
    key: str,
    value: object,
    confidence: str = "observed",
    derivation: str | None = None,
) -> dict[str, Any]:
    return build_fact(
        kind=kind,
        path=path,
        locator=locator,
        semantic_key=key,
        value=value,
        source_bytes=raw,
        confidence=confidence,
        derivation=derivation,
    )


def _bounded_lines(text: str) -> tuple[list[str], bool]:
    lines = text.splitlines()
    return [line[:MAX_LINE_BYTES] for line in lines[:MAX_LINES]], len(lines) > MAX_LINES or any(
        len(line.encode("utf-8")) > MAX_LINE_BYTES for line in lines[:MAX_LINES]
    )


def _json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


class ReadmeExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return path.name.lower().startswith("readme") and path.suffix.lower() in _README_SUFFIXES

    def extract(self, path: str, raw: bytes, text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        facts: list[dict[str, Any]] = []
        lines, limited = _bounded_lines(text)
        in_comment = False
        fence: str | None = None
        identity_found = False
        for number, original in enumerate(lines, 1):
            line = original
            if in_comment:
                if "-->" in line:
                    line = line.split("-->", 1)[1]
                    in_comment = False
                else:
                    continue
            while "<!--" in line:
                before, after = line.split("<!--", 1)
                if "-->" in after:
                    line = before + after.split("-->", 1)[1]
                else:
                    line, in_comment = before, True
                    break
            stripped = line.strip()
            marker = re.fullmatch(r"(```+|~~~+)\s*([A-Za-z0-9_-]*)", stripped)
            if marker:
                fence = None if fence else marker.group(2).lower()
                continue
            heading = re.fullmatch(r"#\s+(.+?)\s*#*", stripped)
            if heading and not identity_found:
                facts.append(_fact(raw, path, "package-metadata", {"line_start": number, "line_end": number}, "readme-identity", heading.group(1), "documented"))
                identity_found = True
            if fence in {"bash", "console", "shell", "sh", "zsh"}:
                command = stripped.removeprefix("$ ").strip()
                if command and not command.startswith(("#", "//")):
                    facts.append(_fact(raw, path, "documentation-statement", {"line_start": number, "line_end": number}, f"readme-command:{number}", command[:512], "documented"))
        return facts, [_warning("W_EXTRACT_TEXT_LIMIT", path)] if limited else []


class PythonProjectExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return path.name.lower() == "pyproject.toml"

    def extract(self, path: str, raw: bytes, _text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        try:
            payload = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            return [], [_warning("W_EXTRACT_TOML", path, _line_number(error))]
        project = payload.get("project")
        if not isinstance(project, dict):
            project = {}
        poetry = payload.get("tool", {}).get("poetry", {}) if isinstance(payload.get("tool"), dict) else {}
        if not isinstance(poetry, dict):
            poetry = {}
        facts: list[dict[str, Any]] = []
        name = project.get("name", poetry.get("name"))
        if isinstance(name, str) and name:
            pointer = "/project/name" if "name" in project else "/tool/poetry/name"
            facts.append(_fact(raw, path, "package-metadata", {"json_pointer": pointer}, "project-name", name))
        requirement = project.get("requires-python")
        pointer = "/project/requires-python"
        if not isinstance(requirement, str):
            dependencies = poetry.get("dependencies")
            requirement = dependencies.get("python") if isinstance(dependencies, dict) else None
            pointer = "/tool/poetry/dependencies/python"
        if isinstance(requirement, str) and requirement:
            facts.append(_fact(raw, path, "config-value", {"json_pointer": pointer}, "requires-python", requirement))
        scripts = project.get("scripts", poetry.get("scripts", {}))
        scripts_pointer = "/project/scripts" if "scripts" in project else "/tool/poetry/scripts"
        if isinstance(scripts, dict):
            for name, target in sorted(scripts.items()):
                if isinstance(name, str) and isinstance(target, str) and name and target:
                    facts.append(_fact(raw, path, "cli-entrypoint", {"json_pointer": f"{scripts_pointer}/{_pointer_part(name)}"}, f"python-script:{name}", target))
        return facts, []


class NodeProjectExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return path.name.lower() == "package.json"

    def extract(self, path: str, raw: bytes, _text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        try:
            payload = _json(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            return [], [_warning("W_EXTRACT_JSON", path, _line_number(error))]
        if not isinstance(payload, dict):
            return [], [_warning("W_EXTRACT_JSON", path)]
        facts: list[dict[str, Any]] = []
        if isinstance(payload.get("name"), str) and payload["name"]:
            facts.append(_fact(raw, path, "package-metadata", {"json_pointer": "/name"}, "package-name", payload["name"]))
        binary = payload.get("bin")
        if isinstance(binary, str):
            name = payload.get("name")
            if isinstance(name, str) and name:
                facts.append(_fact(raw, path, "cli-entrypoint", {"json_pointer": "/bin"}, f"node-bin:{name}", binary))
        elif isinstance(binary, dict):
            for name, target in sorted(binary.items()):
                if isinstance(name, str) and isinstance(target, str) and name and target:
                    facts.append(_fact(raw, path, "cli-entrypoint", {"json_pointer": f"/bin/{_pointer_part(name)}"}, f"node-bin:{name}", target))
        for field, prefix in (("engines", "node-engine"), ("scripts", "node-script")):
            values = payload.get(field)
            if isinstance(values, dict):
                for name, value in sorted(values.items()):
                    if isinstance(name, str) and isinstance(value, str) and name and value:
                        facts.append(_fact(raw, path, "config-value", {"json_pointer": f"/{field}/{_pointer_part(name)}"}, f"{prefix}:{name}", value))
        return facts, []


def _yaml_list(value: str) -> list[str]:
    match = re.fullmatch(r"\[([^\]]*)\]", value.strip())
    if not match:
        return []
    result = []
    for item in match.group(1).split(","):
        item = item.strip().strip("'\"")
        if re.fullmatch(r"[A-Za-z0-9_.-]+", item):
            result.append(item)
    return sorted(set(result))


def _yaml_values(value: str) -> list[str]:
    value = value.split(" #", 1)[0].strip()
    listed = _yaml_list(value)
    if listed:
        return listed
    return [value] if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else []


def _yaml_mapping(line: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+):(?:\s*(.*))?", line)
    return (match.group(1), (match.group(2) or "").strip()) if match else None


def _yaml_open_quote(value: str) -> str | None:
    if not value or value[0] not in {"'", '"'}:
        return None
    quote = value[0]
    return quote if value.count(quote) % 2 else None


def _os_families(values: list[str]) -> list[str]:
    return sorted({
        "linux" if value.startswith("ubuntu") else
        "macos" if value.startswith("macos") else
        "windows" if value.startswith("windows") else value
        for value in values
    })


class GitHubActionsExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return len(path.parts) >= 3 and path.parts[:2] == (".github", "workflows") and path.suffix.lower() in {".yml", ".yaml"}

    def extract(self, path: str, raw: bytes, text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        facts: list[dict[str, Any]] = []
        lines, limited = _bounded_lines(text)
        jobs = False
        job_indent: int | None = None
        property_indent: int | None = None
        context: str | None = None
        nested_indent: int | None = None
        block_indent: int | None = None
        quote_marker: str | None = None
        step_seen = False

        def malformed(number: int) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
            warnings = [_warning("W_EXTRACT_CI_STRUCTURE", path, number)]
            if limited:
                warnings.append(_warning("W_EXTRACT_TEXT_LIMIT", path))
            return [], warnings

        def add_os(number: int, values: list[str], *, families: bool = False) -> None:
            facts.append(_fact(raw, path, "config-value", {"line_start": number, "line_end": number}, "ci-os", values, "documented"))
            if families:
                facts.append(_fact(
                    raw, path, "config-value", {"line_start": number, "line_end": number},
                    "ci-os-families", _os_families(values), "derived",
                    "runner labels mapped to documented operating-system families",
                ))

        for number, line in enumerate(lines, 1):
            prefix = line[: len(line) - len(line.lstrip(" \t"))]
            if "\t" in prefix:
                return malformed(number)
            indent = len(prefix)
            stripped = line.strip()
            if quote_marker is not None:
                if stripped.count(quote_marker) % 2:
                    quote_marker = None
                continue
            if not stripped or stripped.startswith("#"):
                continue
            if block_indent is not None:
                if indent > block_indent:
                    continue
                block_indent = None
            mapping_text = stripped[2:].strip() if stripped.startswith("- ") else stripped
            mapping = _yaml_mapping(mapping_text)
            if mapping and (quote_marker := _yaml_open_quote(mapping[1])) is not None:
                continue
            if mapping and re.fullmatch(r"[|>][+-]?[0-9]?", mapping[1]):
                block_indent = indent
                continue

            if indent == 0:
                if re.match(r"jobs\s*:", stripped):
                    if stripped.split("#", 1)[0].strip() != "jobs:" or jobs:
                        return malformed(number)
                    jobs = True
                    job_indent = property_indent = nested_indent = None
                    context = None
                elif jobs:
                    jobs = False
                continue
            if not jobs:
                continue
            if job_indent is None:
                if not mapping or mapping[1]:
                    return malformed(number)
                job_indent = indent
                continue
            if indent == job_indent:
                if not mapping or mapping[1]:
                    return malformed(number)
                property_indent = nested_indent = None
                context = None
                step_seen = False
                continue
            if indent < job_indent:
                return malformed(number)
            if property_indent is None:
                if not mapping:
                    return malformed(number)
                property_indent = indent
            if indent < property_indent:
                return malformed(number)

            if indent == property_indent:
                if not mapping:
                    return malformed(number)
                key, value = mapping
                nested_indent = None
                step_seen = False
                if key == "runs-on":
                    if values := _yaml_values(value):
                        add_os(number, values)
                    context = None
                elif key in {"strategy", "steps", "env"} and not value:
                    context = key
                elif key in {"os", "python-version", "node-version", "run"}:
                    return malformed(number)
                else:
                    context = None
                continue

            if context == "strategy":
                if nested_indent is None and mapping == ("matrix", ""):
                    nested_indent = indent
                    continue
                if nested_indent is None and mapping and mapping[0] == "matrix":
                    return malformed(number)
                if nested_indent is not None and indent > nested_indent and mapping:
                    key, value = mapping
                    values = _yaml_values(value)
                    if key == "os" and values:
                        add_os(number, values, families=True)
                    elif key == "python-version" and values:
                        facts.append(_fact(raw, path, "config-value", {"line_start": number, "line_end": number}, "ci-python-versions", values, "documented"))
                    elif key == "node-version" and values:
                        facts.append(_fact(raw, path, "config-value", {"line_start": number, "line_end": number}, "ci-node-versions", values, "documented"))
                continue

            if context == "steps":
                step_indent = property_indent + 2
                if indent == step_indent and stripped.startswith("- "):
                    step_seen = True
                    item = _yaml_mapping(stripped[2:].strip())
                    if item and item[0] == "run" and item[1] and not item[1].startswith(("'", '"', "|", ">")):
                        facts.append(_fact(raw, path, "documentation-statement", {"line_start": number, "line_end": number}, f"ci-command:{number}", item[1][:512], "documented"))
                    continue
                if indent == step_indent + 2 and step_seen and mapping and mapping[0] == "run" and mapping[1] and not mapping[1].startswith(("'", '"', "|", ">")):
                    facts.append(_fact(raw, path, "documentation-statement", {"line_start": number, "line_end": number}, f"ci-command:{number}", mapping[1][:512], "documented"))
                    continue
                if mapping and mapping[0] == "run":
                    return malformed(number)
            elif context != "env" and mapping and mapping[0] in {"runs-on", "os", "python-version", "node-version", "run"}:
                return malformed(number)
        if quote_marker is not None:
            return malformed(len(lines) or 1)
        return facts, [_warning("W_EXTRACT_TEXT_LIMIT", path)] if limited else []


class CliEntrypointExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return path.suffix.lower() in {".py", ".js", ".mjs", ".cjs"}

    def extract(self, path: str, raw: bytes, text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        if path.endswith(".py"):
            try:
                tree = ast.parse(text, filename=path)
            except (SyntaxError, ValueError) as error:
                return [], [_warning("W_EXTRACT_PYTHON", path, _line_number(error))]
            module_parts = list(PurePosixPath(path).with_suffix("").parts)
            if module_parts and module_parts[0] == "src":
                module_parts.pop(0)
            module = ".".join(part for part in module_parts if part.isidentifier())
            facts = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main" and module:
                    facts.append(_fact(raw, path, "code-symbol", {"symbol": f"{module}.main"}, "python-main", "main"))
                if isinstance(node, ast.If) and _is_main_guard(node.test):
                    facts.append(_fact(raw, path, "cli-entrypoint", {"line_start": node.lineno, "line_end": getattr(node, "end_lineno", node.lineno)}, "python-main-guard", True))
            return facts, []
        first = text.splitlines()[0] if text.splitlines() else ""
        if re.fullmatch(r"#!\s*/usr/bin/env\s+node\s*", first):
            return [_fact(raw, path, "cli-entrypoint", {"line_start": 1, "line_end": 1}, "javascript-shebang", "node")], []
        return [], []


def _is_main_guard(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
        and len(node.ops) == len(node.comparators) == 1
        and isinstance(node.ops[0], ast.Eq)
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == "__main__"
    )


def _javascript_code_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    block = False
    quote: str | None = None
    escaped = False
    for number, line in enumerate(text.splitlines()[:MAX_LINES], 1):
        cleaned = ""
        index = 0
        while index < len(line):
            character = line[index]
            if quote is not None:
                cleaned += " "
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                index += 1
                continue
            if block:
                end = line.find("*/", index)
                if end < 0:
                    break
                cleaned += " " * (end + 2 - index)
                block, index = False, end + 2
                continue
            if line.startswith("//", index):
                break
            if line.startswith("/*", index):
                cleaned += "  "
                block, index = True, index + 2
                continue
            if character in {"'", '"', "`"}:
                cleaned += " "
                quote, index = character, index + 1
                continue
            cleaned += character
            index += 1
        result.append((number, cleaned))
    return result


class TestLayoutExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        lower = path.as_posix().lower()
        return path.suffix.lower() in {".py", ".js", ".mjs", ".cjs"} and (
            any(part in {"test", "tests"} for part in path.parts[:-1])
            or path.name.lower().startswith("test_")
            or ".test." in path.name.lower()
        )

    def extract(self, path: str, raw: bytes, text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        if path.endswith(".py"):
            try:
                tree = ast.parse(text, filename=path)
            except (SyntaxError, ValueError) as error:
                return [], [_warning("W_EXTRACT_PYTHON", path, _line_number(error))]
            tests = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")]
            framework = "unittest" if any(
                (isinstance(node, ast.Import) and any(alias.name == "unittest" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "unittest")
                for node in ast.walk(tree)
            ) else "pytest"
            line = tests[0].lineno if tests else 1
        else:
            code = _javascript_code_lines(text)
            tests = [number for number, line in code if re.search(r"\b(?:it|test)\s*\(", line)]
            framework = "javascript"
            for (_, cleaned), original in zip(code, text.splitlines()[:MAX_LINES], strict=True):
                if "import" in cleaned and "from" in cleaned and re.fullmatch(
                    r"\s*import\s+.+\s+from\s+['\"]node:test['\"]\s*;?\s*", original
                ):
                    framework = "node:test"
                    break
            line = tests[0] if tests else 1
        return [
            _fact(raw, path, "test-observation", {"line_start": line, "line_end": line}, "test-framework", framework),
            _fact(raw, path, "test-observation", {"line_start": line, "line_end": line}, "test-count", len(tests)),
        ], []


class GenericConfigExtractor:
    def matches(self, path: PurePosixPath) -> bool:
        return path.suffix.lower() in {".json", ".toml"} and path.name.lower() not in _CONFIG_MANIFESTS

    def extract(self, path: str, raw: bytes, _text: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        try:
            payload = _json(raw) if path.lower().endswith(".json") else tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
            code = "W_EXTRACT_JSON" if path.lower().endswith(".json") else "W_EXTRACT_TOML"
            return [], [_warning(code, path, _line_number(error))]
        facts: list[dict[str, Any]] = []
        warnings: list[dict[str, object]] = []

        def visit(value: object, parts: tuple[str, ...]) -> None:
            if len(facts) >= MAX_FACTS_PER_CONFIG:
                return
            if isinstance(value, dict):
                for key in sorted(value):
                    if not isinstance(key, str):
                        continue
                    if _SECRET_KEYS.search(key):
                        warnings.append(_warning("W_EXTRACT_SECRET_KEY", path))
                    else:
                        visit(value[key], (*parts, key))
                return
            if isinstance(value, list) and not all(item is None or type(item) in {bool, int, str} for item in value):
                warnings.append(_warning("W_EXTRACT_VALUE", path))
                return
            if value is not None and type(value) not in {bool, int, str, list}:
                warnings.append(_warning("W_EXTRACT_VALUE", path))
                return
            pointer = "".join(f"/{_pointer_part(part)}" for part in parts)
            if isinstance(value, str):
                value = value[:512]
            facts.append(_fact(raw, path, "config-value", {"json_pointer": pointer}, f"config:{pointer or '/'}", value))

        visit(payload, ())
        if len(facts) >= MAX_FACTS_PER_CONFIG:
            warnings.append(_warning("W_EXTRACT_FACT_LIMIT", path))
        return facts, warnings


DEFAULT_EXTRACTORS = (
    ReadmeExtractor(),
    PythonProjectExtractor(),
    NodeProjectExtractor(),
    GitHubActionsExtractor(),
    CliEntrypointExtractor(),
    TestLayoutExtractor(),
    GenericConfigExtractor(),
)


class ExtractorService:
    def __init__(
        self,
        extractors: Iterable[object] = DEFAULT_EXTRACTORS,
        *,
        max_files: int = MAX_FILES,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        max_seconds: float = MAX_SECONDS,
        clock: object = time.monotonic,
    ) -> None:
        if not all(type(value) is int and value > 0 for value in (max_files, max_file_bytes, max_total_bytes)):
            raise ValueError("extractor limits must be positive integers")
        if not isinstance(max_seconds, (int, float)) or max_seconds <= 0 or not callable(clock):
            raise ValueError("extractor time limit and clock are invalid")
        self.extractors = tuple(extractors)
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.max_seconds = float(max_seconds)
        self.clock = clock

    def extract(self, root: Path) -> dict[str, object]:
        try:
            root_info = root.lstat()
        except OSError as error:
            raise ContractError("E_EXTRACT_ROOT", f"cannot inspect extractor root: {error}") from error
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise ContractError("E_EXTRACT_ROOT", "extractor root must be a real directory")
        root = root.resolve(strict=True)
        started = self.clock()
        pending = [root]
        selected: list[tuple[str, bytes]] = []
        warnings: list[dict[str, object]] = []
        files = total = 0
        while pending:
            directory = pending.pop()
            try:
                entries = sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name), reverse=True)
            except OSError as error:
                raise ContractError("E_EXTRACT_IO", f"cannot list extractor root: {error}") from error
            for entry in entries:
                if self.clock() - started > self.max_seconds:
                    warnings.append(_warning("W_EXTRACT_TIME_LIMIT", entry.relative_to(root).as_posix()))
                    pending.clear()
                    break
                relative = normalize_posix_path(unicodedata.normalize("NFC", entry.relative_to(root).as_posix()))
                try:
                    info = entry.lstat()
                except OSError:
                    warnings.append(_warning("W_EXTRACT_RACE", relative))
                    continue
                if stat.S_ISLNK(info.st_mode):
                    warnings.append(_warning("W_EXTRACT_SYMLINK", relative))
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if entry.name not in FIXED_EXCLUDED_DIRECTORIES:
                        pending.append(entry)
                    continue
                files += 1
                if files > self.max_files:
                    warnings.append(_warning("W_EXTRACT_FILE_LIMIT", relative))
                    pending.clear()
                    break
                if is_secret_path(relative):
                    warnings.append(_warning("W_EXTRACT_SECRET", relative))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    warnings.append(_warning("W_EXTRACT_SPECIAL", relative))
                    continue
                path = PurePosixPath(relative)
                if not any(extractor.matches(path) for extractor in self.extractors):
                    continue
                if info.st_size > self.max_file_bytes or total + info.st_size > self.max_total_bytes:
                    warnings.append(_warning("W_EXTRACT_SIZE_LIMIT", relative))
                    continue
                raw = _read(entry, info, relative, self.max_file_bytes)
                total += len(raw)
                selected.append((relative, raw))
        return self.extract_files(selected, warnings=warnings)

    def extract_files(
        self,
        files: Iterable[tuple[str, bytes] | Mapping[str, object]],
        *,
        warnings: Iterable[Mapping[str, object]] = (),
    ) -> dict[str, object]:
        facts: list[dict[str, Any]] = []
        output_warnings = [copy.deepcopy(dict(warning)) for warning in warnings]
        normalized: list[tuple[str, bytes]] = []
        for item in files:
            if isinstance(item, Mapping):
                path, raw = item.get("path"), item.get("content", item.get("bytes"))
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
            else:
                path, raw = item
            path = normalize_posix_path(path)
            if is_secret_path(path):
                output_warnings.append(_warning("W_EXTRACT_SECRET", path))
                continue
            if not isinstance(raw, bytes):
                raise TypeError("extractor content must be bytes or UTF-8 text")
            normalized.append((path, raw))
        for path, raw in sorted(normalized, key=lambda item: os.fsencode(item[0])):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                output_warnings.append(_warning("W_EXTRACT_UTF8", path, _line_number(error)))
                continue
            pure = PurePosixPath(path)
            for extractor in self.extractors:
                if extractor.matches(pure):
                    extracted, found_warnings = extractor.extract(path, raw, text)
                    facts.extend(extracted)
                    output_warnings.extend(found_warnings)
        facts.sort(key=lambda fact: (
            os.fsencode(fact["source"]["path"]),
            fact["source"].get("line_start", 0),
            fact["kind"],
            fact["semantic_key"],
            fact["fact_id"],
        ))
        unique_warnings = {(
            str(warning["path"]), int(warning["line"]), str(warning["code"])
        ): warning for warning in output_warnings}
        ordered_warnings = [unique_warnings[key] for key in sorted(unique_warnings, key=lambda key: (os.fsencode(key[0]), key[1], key[2]))]
        return {"facts": facts, "warnings": ordered_warnings}


def extract_repository(root: Path) -> dict[str, object]:
    return ExtractorService().extract(root)


def extract_files(files: Iterable[tuple[str, bytes] | Mapping[str, object]]) -> dict[str, object]:
    return ExtractorService().extract_files(files)


__all__ = [
    "CliEntrypointExtractor",
    "ExtractorService",
    "GenericConfigExtractor",
    "GitHubActionsExtractor",
    "NodeProjectExtractor",
    "PythonProjectExtractor",
    "ReadmeExtractor",
    "TestLayoutExtractor",
    "extract_files",
    "extract_repository",
]
