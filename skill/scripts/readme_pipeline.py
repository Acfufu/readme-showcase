#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Callable

_PREFIX = "" if __package__ in (None, "") else "skill.scripts."
_CONTRACTS = importlib.import_module(f"{_PREFIX}pipeline_contracts")
_CORE = importlib.import_module(f"{_PREFIX}pipeline_core")
_BENCHMARK = importlib.import_module(f"{_PREFIX}benchmark_adapter")
ContractError = _CONTRACTS.ContractError
canonical_json_bytes = _CONTRACTS.canonical_json_bytes
canonical_sha256 = _CONTRACTS.canonical_sha256
read_json_object = _CONTRACTS.read_json_object
validate_contract = _CONTRACTS.validate_contract
validate_dataset_manifest = _CORE.validate_dataset_manifest
scan_repository = _CORE.scan_repository
retrieve_patterns = _CORE.retrieve_patterns
validate_generated_bundle = _CORE.validate_generated_bundle
evaluate_generated_bundle = _CORE.evaluate_generated_bundle
write_canonical_json_atomic = _CONTRACTS.write_canonical_json_atomic
import_benchmark = _BENCHMARK.import_benchmark


Handler = Callable[[argparse.Namespace], dict[str, object]]


def _pending(command: str) -> Handler:
    def run(_: argparse.Namespace) -> dict[str, object]:
        raise ContractError(
            "E_COMMAND_NOT_IMPLEMENTED",
            f"{command} is reserved for its owning implementation task",
        )

    return run


def _validate_bundle(arguments: argparse.Namespace) -> dict[str, object]:
    payload = read_json_object(arguments.bundle)
    return validate_generated_bundle(payload, arguments.bundle.parent.resolve())


def _validate_dataset(arguments: argparse.Namespace) -> dict[str, object]:
    return validate_dataset_manifest(read_json_object(arguments.manifest))


def _scan(arguments: argparse.Namespace) -> dict[str, object]:
    evidence = scan_repository(arguments.root)
    write_canonical_json_atomic(arguments.output, evidence)
    return {
        "schema_version": 1,
        "status": evidence["status"],
        "file_count": len(evidence["files"]),
        "evidence_sha256": canonical_sha256(evidence),
    }


def _retrieve(arguments: argparse.Namespace) -> dict[str, object]:
    evidence = read_json_object(arguments.evidence)
    manifest = (
        read_json_object(arguments.manifest)
        if arguments.manifest is not None and arguments.manifest.exists()
        else None
    )
    packet = retrieve_patterns(
        evidence,
        manifest,
        project_type=arguments.project_type,
        sections=arguments.section,
        tags=arguments.tag,
        mode=arguments.mode,
    )
    write_canonical_json_atomic(arguments.output, packet)
    return {
        "schema_version": 1,
        "status": packet["status"],
        "record_count": len(packet["records"]),
        "retrieval_sha256": canonical_sha256(packet),
    }


def _import_benchmark(arguments: argparse.Namespace) -> dict[str, object]:
    return import_benchmark(
        arguments.input,
        arguments.license_sidecar,
        arguments.output_dir,
    )


def _evaluate(arguments: argparse.Namespace) -> dict[str, object]:
    report = evaluate_generated_bundle(
        read_json_object(arguments.bundle),
        arguments.bundle.parent.resolve(),
    )
    write_canonical_json_atomic(arguments.output, report)
    return report


def _path_argument(
    parser: argparse.ArgumentParser,
    flag: str,
    *,
    required: bool = True,
) -> None:
    parser.add_argument(flag, type=Path, required=required)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and assemble deterministic README pipeline artifacts."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate_dataset = subcommands.add_parser("validate-dataset")
    _path_argument(validate_dataset, "--manifest")
    validate_dataset.set_defaults(handler=_validate_dataset)

    scan = subcommands.add_parser("scan")
    _path_argument(scan, "--root")
    _path_argument(scan, "--output")
    scan.set_defaults(handler=_scan)

    retrieve = subcommands.add_parser("retrieve")
    _path_argument(retrieve, "--evidence")
    _path_argument(retrieve, "--manifest", required=False)
    retrieve.add_argument(
        "--project-type",
        choices=("developer-tool", "library", "runtime-toolchain", "web-framework"),
        required=True,
    )
    retrieve.add_argument("--section", action="append", default=[])
    retrieve.add_argument("--tag", action="append", default=[])
    retrieve.add_argument(
        "--mode",
        choices=("production", "benchmark"),
        default="production",
    )
    _path_argument(retrieve, "--output")
    retrieve.set_defaults(handler=_retrieve)

    validate_bundle = subcommands.add_parser("validate-bundle")
    _path_argument(validate_bundle, "--bundle")
    validate_bundle.set_defaults(handler=_validate_bundle)

    evaluate = subcommands.add_parser("evaluate")
    _path_argument(evaluate, "--bundle")
    _path_argument(evaluate, "--output")
    evaluate.set_defaults(handler=_evaluate)

    import_benchmark = subcommands.add_parser("import-benchmark")
    _path_argument(import_benchmark, "--input")
    _path_argument(import_benchmark, "--license-sidecar")
    _path_argument(import_benchmark, "--output-dir")
    import_benchmark.set_defaults(handler=_import_benchmark)

    build_pr_bundle = subcommands.add_parser("build-pr-bundle")
    _path_argument(build_pr_bundle, "--bundle")
    _path_argument(build_pr_bundle, "--evaluation")
    _path_argument(build_pr_bundle, "--output")
    build_pr_bundle.set_defaults(handler=_pending("build-pr-bundle"))

    publish_gate = subcommands.add_parser("check-publish-gate")
    _path_argument(publish_gate, "--pr-bundle")
    _path_argument(publish_gate, "--remote-state")
    _path_argument(publish_gate, "--approval")
    _path_argument(publish_gate, "--output")
    publish_gate.set_defaults(handler=_pending("check-publish-gate"))

    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(arguments)
    try:
        result = parsed.handler(parsed)
    except ContractError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"E_OUTPUT_IO: {exc}", file=sys.stderr)
        return 2

    _ = sys.stdout.buffer.write(canonical_json_bytes(result))
    return 1 if result.get("status") in {"fail", "incomplete"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
