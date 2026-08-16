"""Centralized environment resolution with a prefixed ``.env`` fallback.

Single place where process-environment variables and the ``.env`` file are
read for the readme-showcase tracks (currently the optional vision-LLM
review). Rules, in priority order:

- **Process environment wins** over the ``.env`` file (env-first).
- **Prefix whitelist**: the ``.env`` file is only consulted for keys matching
  ``DEFAULT_PREFIX`` (``VISION_REVIEW_``). Non-prefixed keys are never read
  from, and never written to, ``.env``.
- **Required contract**: ``resolve_config(required=[...])`` raises
  ``ContractError("E_CONFIG_MISSING_KEY", ...)`` when a required name does not
  resolve. The message carries the key *name* only, never a value. The check
  applies to the actually-resolved key name: a dynamic name (e.g. a
  ``--api-key-env`` override) is resolved as a process-environment variable,
  so the static default name never leaks its value under the dynamic name.
- **Secrets hygiene**: ``.env`` is created (and rewritten) with mode ``0o600``.

The ``.env`` file lives at ``skill/.env`` (next to ``skill/.env.example``) —
that is the only place ``.env`` lives; ``CODEX_HOME``/``HOME`` only matter for
the standalone ``codex_home()`` helper. No third-party dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..pipeline_contracts import ContractError

# skill/scripts/readme_showcase/config_env.py -> parents[2] == skill/
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_PREFIX = "VISION_REVIEW_"
_ENV_FILE_MODE = 0o600


def codex_home() -> Path:
    """Resolve ``${CODEX_HOME:-$HOME/.codex}``; falls back to ``~/.codex``."""
    explicit = os.environ.get("CODEX_HOME")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("HOME") or Path.home())
    return base / ".codex"


def parse_env_file(text: str) -> dict[str, str]:
    """Self-written ``.env`` KV parser.

    Blank lines and ``#`` comments are ignored; values are stripped and, when
    wrapped in matching single or double quotes, unquoted. Inline comments
    after a value are not stripped (a ``#`` inside a key's value is data).
    """
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def load_env_file(env_path: str | Path | None = None) -> dict[str, str]:
    """Read and parse the ``.env`` file, filtered to the configured prefix.

    A missing file yields ``{}``. Only keys starting with ``DEFAULT_PREFIX``
    are returned — the prefix whitelist is enforced at read time so every
    downstream lookup (``config_get``, ``resolve_config``, the required
    contract) inherits it.
    """
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not path.is_file():
        return {}
    return {
        key: value
        for key, value in parse_env_file(path.read_text(encoding="utf-8")).items()
        if key.startswith(DEFAULT_PREFIX)
    }


def ensure_env_file(env_path: str | Path | None = None, mode: int = _ENV_FILE_MODE) -> Path:
    """Create the ``.env`` file with mode ``0o600`` if it does not exist.

    Never writes key values; creation only.
    """
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.close(fd)
    return path


def write_env_file(
    updates: dict[str, str],
    env_path: str | Path | None = None,
    mode: int = _ENV_FILE_MODE,
) -> Path:
    """Persist prefixed keys into the ``.env`` file with mode ``0o600``.

    Non-prefixed keys are silently dropped — they are never written to
    ``.env``. Existing prefixed entries are preserved; mode ``0o600`` is
    enforced on every write because this file may hold secrets.
    """
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_env_file(path) if path.is_file() else {}
    existing.update(
        {key: value for key, value in updates.items() if key.startswith(DEFAULT_PREFIX)}
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in sorted(existing.items()))
    finally:
        os.chmod(path, mode)
    return path


def config_get(
    name: str,
    default: str | None = None,
    env_path: str | Path | None = None,
) -> str | None:
    """Resolve one key: process environment first, then the prefixed ``.env``.

    The lookup is by exact name — a dynamic name (e.g. a ``--api-key-env``
    override) keeps process-environment-variable semantics: it is never
    aliased to, or satisfied by, a different (static) key's value.
    """
    value = os.environ.get(name)
    if value is None:
        value = load_env_file(env_path).get(name)
    return default if value is None else value


def resolve_config(
    prefix: str = DEFAULT_PREFIX,
    required: tuple[str, ...] | list[str] = (),
    env_path: str | Path | None = None,
) -> dict[str, str]:
    """Resolve the full prefixed configuration.

    For every key matching ``prefix`` present in either source, the process
    environment wins over the ``.env`` file. ``required`` names must each
    resolve (process environment first, then the prefixed ``.env``) or a
    ``ContractError("E_CONFIG_MISSING_KEY", ...)`` is raised — the message
    contains the key name only, never a value.
    """
    env_file = load_env_file(env_path)
    prefixed_env = {key: value for key, value in os.environ.items() if key.startswith(prefix)}
    names = set(prefixed_env) | set(env_file)
    resolved: dict[str, str] = {}
    for name in names:
        env_value = prefixed_env.get(name)
        resolved[name] = env_value if env_value is not None else env_file[name]
    for name in required:
        value = os.environ.get(name)
        if value is None:
            value = env_file.get(name)
        if value is None:
            raise ContractError(
                "E_CONFIG_MISSING_KEY",
                f"missing required environment key {name}",
            )
    return resolved
