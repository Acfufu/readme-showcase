#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


CORE_VERSION = "1.3.1"
SCHEMA_VERSION = "1.1.1"
LICENSE = "FSL-1.1-ALv2"
SOURCE_REPOSITORY = "https://github.com/MS-Teja/Glyphic"
SOURCE_COMMIT = "ed79edb1624e2de78041611971a963efaea5e080"
CORE_SRI = (
    "sha512-+wWBhFXOkgS6ZtGk4cHPooIueXt01g3meuHHcZnapBtgPW8IXy8nDFPO1lZX"
    "eETVK+NZ6BeCu+blmD3QGr5hDw=="
)
MAX_FILES = 20_000
MAX_BYTES = 256 * 1024 * 1024


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read valid JSON from {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return raw, value


def real_directory(path: Path, name: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        fail(f"{name} is unavailable: {exc}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        fail(f"{name} must be a real directory")


def tree_sha256(root: Path) -> str:
    files: list[Path] = []
    total = 0

    def walk(directory: Path) -> None:
        nonlocal total
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            fail(f"cannot read engine tree: {exc}")
        for entry in entries:
            path = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                fail(f"cannot inspect engine tree entry: {exc}")
            if stat.S_ISLNK(mode):
                fail("engine tree contains a symlink")
            if stat.S_ISDIR(mode):
                walk(path)
            elif stat.S_ISREG(mode):
                total += entry.stat(follow_symlinks=False).st_size
                if len(files) >= MAX_FILES or total > MAX_BYTES:
                    fail("engine tree exceeds file or byte bounds")
                files.append(path)
            else:
                fail("engine tree contains a special file")

    walk(root)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        raw = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_lock(
    install_root: Path,
    npm_sri: str,
    node_version: str,
    output: Path,
) -> dict[str, Any]:
    if not install_root.is_absolute() or not output.is_absolute():
        fail("install root and output must be absolute")
    if npm_sri != CORE_SRI:
        fail("npm SRI does not match @glyphicjs/core@1.3.1")
    if not re.fullmatch(r"22\.\d+\.\d+", node_version):
        fail("node version must be an exact Node 22 semantic version")

    real_directory(install_root, "install root")
    node_modules = install_root / "node_modules"
    core = node_modules / "@glyphicjs/core"
    schema = node_modules / "@glyphicjs/schema"
    for path, name in (
        (node_modules, "node_modules"),
        (core, "@glyphicjs/core"),
        (schema, "@glyphicjs/schema"),
    ):
        real_directory(path, name)
    try:
        output.relative_to(node_modules)
    except ValueError:
        pass
    else:
        fail("lock output must be outside node_modules")

    package_raw, package = load_json(core / "package.json")
    _, schema_package = load_json(schema / "package.json")
    if (
        package.get("name") != "@glyphicjs/core"
        or package.get("version") != CORE_VERSION
        or package.get("license") != LICENSE
        or package.get("type") != "module"
        or package.get("exports", {}).get(".", {}).get("import")
        != "./dist/index.js"
    ):
        fail("@glyphicjs/core package identity mismatch")
    if (
        schema_package.get("name") != "@glyphicjs/schema"
        or schema_package.get("version") != SCHEMA_VERSION
    ):
        fail("@glyphicjs/schema package identity mismatch")
    try:
        license_raw = (core / "LICENSE").read_bytes()
    except OSError as exc:
        fail(f"Glyphic license file unavailable: {exc}")

    return {
        "schema_version": 1,
        "package_name": "@glyphicjs/core",
        "package_version": CORE_VERSION,
        "core_version": CORE_VERSION,
        "schema_package_name": "@glyphicjs/schema",
        "schema_package_version": SCHEMA_VERSION,
        "npm_sri": CORE_SRI,
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "license_spdx": LICENSE,
        "license_file": "LICENSE",
        "license_sha256": hashlib.sha256(license_raw).hexdigest(),
        "package_json_sha256": hashlib.sha256(package_raw).hexdigest(),
        "tree_sha256": tree_sha256(node_modules),
        "node_version": node_version,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build canonical lock for isolated Glyphic packages."
    )
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--npm-sri", required=True)
    parser.add_argument("--node-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        value = build_lock(
            args.install_root,
            args.npm_sri,
            args.node_version,
            args.output,
        )
        atomic_write(args.output, value)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": 1,
                "status": "locked",
                "tree_sha256": value["tree_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
