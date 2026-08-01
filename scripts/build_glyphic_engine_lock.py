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
MAX_DIRECTORIES = 5_000
MAX_DEPTH = 64
MAX_METADATA_BYTES = 64 * 1024
MAX_LICENSE_BYTES = 1024 * 1024


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def read_bounded(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        expected = path.lstat()
        if (
            stat.S_ISLNK(expected.st_mode)
            or not stat.S_ISREG(expected.st_mode)
            or expected.st_size > maximum
        ):
            fail(f"{path} must be a bounded regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (
                expected.st_dev,
                expected.st_ino,
                expected.st_size,
                expected.st_mtime_ns,
            )
        ):
            fail(f"{path} changed before read")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum
            or len(raw) != opened.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            fail(f"{path} changed during read or exceeds bounds")
        return raw
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = read_bounded(path, MAX_METADATA_BYTES)
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
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
    entries: list[tuple[str, str, bytes | None]] = []
    files = 0
    directories = 0
    total = 0

    def walk(directory: Path, depth: int) -> None:
        nonlocal directories, files, total
        if depth > MAX_DEPTH:
            fail("engine tree exceeds directory depth bound")
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            fail(f"cannot read engine tree: {exc}")
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                fail(f"cannot inspect engine tree entry: {exc}")
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                fail("engine tree contains a symlink")
            if stat.S_ISDIR(mode):
                directories += 1
                if directories > MAX_DIRECTORIES:
                    fail("engine tree exceeds directory count bound")
                entries.append(("D", relative, None))
                walk(path, depth + 1)
            elif stat.S_ISREG(mode):
                files += 1
                if files > MAX_FILES or total + info.st_size > MAX_BYTES:
                    fail("engine tree exceeds file or byte bounds")
                raw = read_bounded(path, MAX_BYTES - total)
                total += len(raw)
                entries.append(("F", relative, raw))
            else:
                fail("engine tree contains a special file")

    walk(root, 0)
    digest = hashlib.sha256()
    for kind, relative, raw in sorted(entries, key=lambda item: (item[1], item[0])):
        digest.update(kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if raw is not None:
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
    expected_tree_sha256: str,
    output: Path,
) -> dict[str, Any]:
    if not install_root.is_absolute() or not output.is_absolute():
        fail("install root and output must be absolute")
    if npm_sri != CORE_SRI:
        fail("npm SRI does not match @glyphicjs/core@1.3.1")
    if not re.fullmatch(r"22\.\d+\.\d+", node_version):
        fail("node version must be an exact Node 22 semantic version")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_tree_sha256):
        fail("expected tree SHA-256 must be lowercase")

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
    license_raw = read_bounded(core / "LICENSE", MAX_LICENSE_BYTES)
    tree_digest = tree_sha256(node_modules)
    if tree_digest != expected_tree_sha256:
        fail("engine tree does not match trusted expected SHA-256")

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
        "tree_sha256": tree_digest,
        "node_version": node_version,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build canonical lock for isolated Glyphic packages."
    )
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--npm-sri", required=True)
    parser.add_argument("--node-version", required=True)
    parser.add_argument("--expected-tree-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        value = build_lock(
            args.install_root,
            args.npm_sri,
            args.node_version,
            args.expected_tree_sha256,
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
