from __future__ import annotations

import importlib
import stat
from pathlib import Path, PurePosixPath

ContractError = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
).ContractError


_LANGUAGES = {
    ".c": "c",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".md": "markdown",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_ASSETS = {".gif", ".ico", ".jpeg", ".jpg", ".pdf", ".png", ".svg", ".webp"}
_MANIFESTS = {"cargo.toml", "go.mod", "package.json", "pyproject.toml", "requirements.txt"}
_CONFIGURATION = {".editorconfig", ".gitattributes", ".gitignore", "dockerfile", "makefile"}


def language(path: PurePosixPath) -> str | None:
    return _LANGUAGES.get(path.suffix.lower())


def role(path: PurePosixPath) -> str:
    lower = path.as_posix().lower()
    name = path.name.lower()
    parts = {part.lower() for part in path.parts[:-1]}
    if name.startswith("readme") and path.suffix.lower() in {"", ".md", ".rst", ".txt"}:
        return "readme"
    if ".github" in parts and "workflows" in parts:
        return "workflow"
    if "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if "docs" in parts or "documentation" in parts:
        return "documentation"
    if name in _MANIFESTS:
        return "manifest"
    if name in _CONFIGURATION or lower.startswith(".github/"):
        return "configuration"
    if "example" in parts or "examples" in parts:
        return "example"
    if path.suffix.lower() in _ASSETS:
        return "asset"
    if language(path) not in {None, "markdown", "json", "toml", "yaml"}:
        return "source"
    return "other"


def _regular_file(root: Path, value: str) -> tuple[Path, int]:
    path = PurePosixPath(value)
    current = root
    try:
        for part in path.parts[:-1]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ContractError("E_SCAN_IO", f"tracked parent must be a real directory: {value}")
        target = current / path.name
        info = target.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_SCAN_IO", f"tracked path is missing: {value}") from exc
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot inspect tracked path {value}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ContractError("E_SCAN_IO", f"tracked path must be a regular file: {value}")
    return target, info.st_size


def build_file_index(root: Path, paths: tuple[str, ...]) -> list[dict[str, object]]:
    files = []
    for value in paths:
        _, size = _regular_file(root, value)
        path = PurePosixPath(value)
        files.append(
            {
                "bytes": size,
                "language": language(path),
                "path": value,
                "role": role(path),
                "selected_for_content": False,
                "sha256": None,
                "tracked": True,
            }
        )
    return files
