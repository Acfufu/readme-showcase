#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_PREFIX = "" if __package__ in (None, "") else "skill.scripts."
_RUN_PREFIX = "scripts." if __package__ in (None, "") else "skill.scripts."
_CONTRACTS = importlib.import_module(f"{_PREFIX}pipeline_contracts")
_CORE = importlib.import_module(f"{_PREFIX}pipeline_core")
_BENCHMARK = importlib.import_module(f"{_PREFIX}benchmark_adapter")
_RUNNER = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.orchestration.runner")
_RUN_LOGGING = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.orchestration.logging")
_RUN_CONTRACT = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.contracts.run")
_SCANNER = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.scanner.service")
_EVALUATION_CONTRACT = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.contracts.evaluation")
_APPROVAL = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.delivery.approval")
_GITHUB = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.delivery.github")
_PUBLISHING = importlib.import_module(f"{_RUN_PREFIX}readme_showcase.contracts.publishing")
ContractError = _CONTRACTS.ContractError
canonical_json_bytes = _CONTRACTS.canonical_json_bytes
canonical_sha256 = _CONTRACTS.canonical_sha256
read_json_object = _CONTRACTS.read_json_object
read_json_object_bytes = _CONTRACTS.read_json_object_bytes
validate_contract = _CONTRACTS.validate_contract
validate_dataset_manifest = _CORE.validate_dataset_manifest
scan_repository = _CORE.scan_repository
retrieve_patterns = _CORE.retrieve_patterns
validate_generated_bundle = _CORE.validate_generated_bundle
evaluate_generated_bundle = _CORE.evaluate_generated_bundle
build_pr_bundle = _CORE.build_pr_bundle
check_publish_gate = _CORE.check_publish_gate
write_canonical_json_atomic = _CONTRACTS.write_canonical_json_atomic
import_benchmark = _BENCHMARK.import_benchmark
StageLogger = _RUN_LOGGING.StageLogger
STAGE_NAMES = _RUN_CONTRACT.STAGE_NAMES
RunContractError = _RUNNER.ContractError


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
    if arguments.schema_version == 1:
        if arguments.project_type is not None:
            raise ContractError("E_SCHEMA_VERSION", "--project-type requires --schema-version 2")
        evidence = scan_repository(arguments.root)
    else:
        evidence = _SCANNER.scan_repository_v2(arguments.root, arguments.project_type or "unknown")
    write_canonical_json_atomic(arguments.output, evidence)
    return {
        "schema_version": arguments.schema_version,
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
    observation = None
    trusted: frozenset[str] = frozenset()
    if arguments.observation is not None:
        observation = _EVALUATION_CONTRACT.read_command_observation(arguments.observation)
        if arguments.trusted_observation_sha256 is not None:
            if len(arguments.trusted_observation_sha256) != 64 or any(
                character not in "0123456789abcdef"
                for character in arguments.trusted_observation_sha256
            ):
                raise ContractError("E_OBSERVATION_BINDING", "trusted observation receipt must be lowercase SHA-256")
            trusted = frozenset({arguments.trusted_observation_sha256})
    elif arguments.trusted_observation_sha256 is not None:
        raise ContractError("E_OBSERVATION_BINDING", "trusted observation receipt requires --observation")
    report = evaluate_generated_bundle(
        read_json_object(arguments.bundle),
        arguments.bundle.parent.resolve(),
        observation=observation,
        trusted_observation_sha256s=trusted,
    )
    write_canonical_json_atomic(arguments.output, report)
    return report


def _within(path: Path, root: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return True


def _read_canonical_input(path: Path, code: str) -> dict[str, object]:
    raw, payload = read_json_object_bytes(path)
    if raw != canonical_json_bytes(payload):
        raise ContractError(code, f"input is not canonical JSON: {path.name}")
    return payload


def _build_pr_bundle(arguments: argparse.Namespace) -> dict[str, object]:
    target_root = Path.cwd().resolve()
    for path in (arguments.bundle, arguments.evaluation, arguments.output):
        if _within(path, target_root):
            raise ContractError(
                "E_PR_PATH",
                "pipeline inputs and outputs must stay outside target repository",
            )
    bundle = _read_canonical_input(arguments.bundle, "E_PR_INPUT")
    evaluation = _read_canonical_input(arguments.evaluation, "E_PR_INPUT")
    result = build_pr_bundle(
        bundle,
        evaluation,
        arguments.bundle.parent.resolve(),
        target_root,
    )
    write_canonical_json_atomic(arguments.output, result)
    return result


def _check_publish_gate(arguments: argparse.Namespace) -> dict[str, object]:
    target_root = Path.cwd().resolve()
    paths = (
        arguments.pr_bundle,
        arguments.remote_state,
        arguments.approval,
        arguments.output,
    )
    if any(_within(path, target_root) for path in paths):
        raise ContractError(
            "E_PUBLISH_PATH",
            "publish-gate inputs and output must stay outside target repository",
        )
    result = check_publish_gate(
        _read_canonical_input(arguments.pr_bundle, "E_PUBLISH_INPUT"),
        _read_canonical_input(arguments.remote_state, "E_PUBLISH_INPUT"),
        _read_canonical_input(arguments.approval, "E_PUBLISH_INPUT"),
        arguments.pr_bundle.parent.resolve(),
    )
    write_canonical_json_atomic(arguments.output, result)
    return result


def _create_approval_template(arguments: argparse.Namespace) -> dict[str, object]:
    target_root = Path.cwd().resolve()
    if any(_within(path, target_root) for path in (arguments.pr_bundle, arguments.output)):
        raise ContractError(
            _APPROVAL.INPUT_ERROR_CODE,
            "approval inputs and output must stay outside target repository",
        )
    return _APPROVAL.create_approval_template_from_path(
        arguments.pr_bundle,
        arguments.output,
    )


def _deliver(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.transport != "gh" or not arguments.dry_run:
        raise ContractError(
            _GITHUB.LIVE_DISABLED_CODE,
            "this command permits only the local --transport gh --dry-run flow",
        )
    target_root = Path.cwd().resolve()
    if any(_within(path, target_root) for path in (arguments.bundle, arguments.approval, arguments.workspace)):
        raise ContractError(
            "E_PUBLISH_PATH",
            "delivery inputs and workspace must stay outside target repository",
        )
    bundle = _read_canonical_input(arguments.bundle, "E_PUBLISH_INPUT")
    approval = _read_canonical_input(arguments.approval, "E_PUBLISH_INPUT")
    gate = _PUBLISHING.check_approval_envelope(approval, bundle, arguments.workspace)
    if gate["status"] != "authorized":
        findings = ",".join(gate["findings"])
        raise ContractError(_GITHUB.AUTHORITY_CODE, f"delivery approval is not current: {findings}")
    metadata = bundle.get("metadata")
    if not isinstance(metadata, dict):
        raise ContractError(_GITHUB.PLAN_CODE, "delivery bundle metadata is missing")
    plan = _GITHUB.build_delivery_plan(
        approval,
        title=metadata.get("pull_request_title"),
        body=metadata.get("pull_request_body"),
        commit_message=metadata.get("commit_message"),
    )
    return _GITHUB.dry_run_result(plan)


def _stage_logger(arguments: argparse.Namespace) -> object:
    return StageLogger(format=arguments.log_format, verbosity=arguments.verbosity)


def _run(arguments: argparse.Namespace) -> dict[str, object]:
    return _RUNNER.start_run(
        root=arguments.root,
        workspace_path=arguments.workspace,
        mode=arguments.mode,
        project_type=arguments.project_type,
        locales=arguments.locale,
        scanner_profile=arguments.scanner_profile,
        plan=arguments.plan,
        stop_after=arguments.stop_after,
        logger=_stage_logger(arguments),
    )


def _resume(arguments: argparse.Namespace) -> dict[str, object]:
    return _RUNNER.resume_run(
        workspace_path=arguments.workspace,
        plan=arguments.plan,
        stop_after=arguments.stop_after,
        logger=_stage_logger(arguments),
    )


def _status(arguments: argparse.Namespace) -> dict[str, object]:
    return _RUNNER.run_status(arguments.workspace)


def _explain(arguments: argparse.Namespace) -> dict[str, object]:
    return _RUNNER.explain_run(arguments.workspace)


def _preview(arguments: argparse.Namespace) -> dict[str, object]:
    return _RUNNER.preview_run(arguments.workspace)


def _path_argument(
    parser: argparse.ArgumentParser,
    flag: str,
    *,
    required: bool = True,
) -> None:
    parser.add_argument(flag, type=Path, required=required)


def _run_observability(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-format", choices=("text", "json"), default="text")
    parser.add_argument("--verbosity", choices=("quiet", "normal", "debug"), default="normal")


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
    scan.add_argument("--schema-version", type=int, choices=(1, 2), default=1)
    scan.add_argument("--project-type", choices=("cli", "library", "app", "extension", "service", "unknown"))
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
    _path_argument(evaluate, "--observation", required=False)
    evaluate.add_argument("--trusted-observation-sha256")
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
    build_pr_bundle.set_defaults(handler=_build_pr_bundle)

    publish_gate = subcommands.add_parser("check-publish-gate")
    _path_argument(publish_gate, "--pr-bundle")
    _path_argument(publish_gate, "--remote-state")
    _path_argument(publish_gate, "--approval")
    _path_argument(publish_gate, "--output")
    publish_gate.set_defaults(handler=_check_publish_gate)

    run = subcommands.add_parser("run")
    _path_argument(run, "--root")
    _path_argument(run, "--workspace")
    run.add_argument("--mode", choices=("readme", "asset-only", "audit-only"), required=True)
    run.add_argument(
        "--project-type",
        choices=("developer-tool", "library", "runtime-toolchain", "web-framework"),
        required=True,
    )
    run.add_argument("--locale", action="append", required=True)
    run.add_argument("--scanner-profile", default="balanced")
    _path_argument(run, "--plan", required=False)
    run.add_argument("--stop-after", choices=STAGE_NAMES)
    _run_observability(run)
    run.set_defaults(handler=_run)

    resume = subcommands.add_parser("resume")
    _path_argument(resume, "--workspace")
    _path_argument(resume, "--plan", required=False)
    resume.add_argument("--stop-after", choices=STAGE_NAMES)
    _run_observability(resume)
    resume.set_defaults(handler=_resume)

    status = subcommands.add_parser("status")
    _path_argument(status, "--workspace")
    _run_observability(status)
    status.set_defaults(handler=_status)

    explain = subcommands.add_parser("explain")
    _path_argument(explain, "--workspace")
    explain.add_argument("--format", choices=("text", "json"), default="text")
    _run_observability(explain)
    explain.set_defaults(handler=_explain)

    preview = subcommands.add_parser("preview")
    _path_argument(preview, "--workspace")
    preview.set_defaults(handler=_preview)

    return parser


def _build_approval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readme_pipeline.py create-approval-template",
        description="Create a canonical reject-default approval envelope.",
    )
    _path_argument(parser, "--pr-bundle")
    _path_argument(parser, "--output")
    parser.set_defaults(handler=_create_approval_template)
    return parser


def _build_delivery_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readme_pipeline.py deliver",
        description="Plan a bound GitHub delivery without network or remote writes.",
    )
    parser.add_argument("--transport", choices=("gh",), required=True)
    parser.add_argument("--dry-run", action="store_true")
    _path_argument(parser, "--bundle")
    _path_argument(parser, "--approval")
    _path_argument(parser, "--workspace")
    parser.set_defaults(handler=_deliver)
    return parser


def main(arguments: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if raw_arguments[:1] == ["create-approval-template"]:
        parser = _build_approval_parser()
        parsed = parser.parse_args(raw_arguments[1:])
    elif raw_arguments[:1] == ["deliver"]:
        parser = _build_delivery_parser()
        parsed = parser.parse_args(raw_arguments[1:])
    else:
        parser = build_parser()
        parsed = parser.parse_args(raw_arguments)
    try:
        result = parsed.handler(parsed)
    except (ContractError, RunContractError) as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"E_OUTPUT_IO: {exc}", file=sys.stderr)
        return 2

    _ = sys.stdout.buffer.write(canonical_json_bytes(result))
    return 1 if result.get("status") in {"fail", "failed", "incomplete", "manual-review-required"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
