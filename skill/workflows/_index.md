# Workflow index

The eight-stage, one README Agent handoff. Each stage owns its contract,
commands, and gate rules in its own file; read this index first, then follow
the stages in order. [SKILL.md](../SKILL.md) summarizes the pipeline; these
files hold the detailed step contracts that the summary points to.

| # | Stage | Workflow |
| --- | --- | --- |
| 1 | scan | [scan.md](scan.md) |
| 2 | retrieve | [retrieve.md](retrieve.md) |
| 3 | plan-import | [plan-import.md](plan-import.md) |
| 4 | generation-request | [generation-request.md](generation-request.md) |
| 5 | candidate | [candidate.md](candidate.md) |
| 6 | bundle-assemble | [bundle-assemble.md](bundle-assemble.md) |
| 7 | validation | [validation.md](validation.md) |
| 8 | evaluation | [evaluation.md](evaluation.md) |

## Shared conventions

Every stage doc assumes the environment and state location from
[SKILL.md](../SKILL.md):

- Run state lives in orchestrator-managed central state at
  `${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`, keyed by target
  repository. Never create `.readme-showcase-run-*` or another run directory
  in or beside the target repository.
- The run's debug result exposes the internal workspace; use that path as
  `$RUN` for stage inputs and outputs below.
- `README_SHOWCASE_SKILL` resolves to the installed Skill root:
  `${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase`.
- Never create a per-run virtual environment; use the existing runtime and
  remove temporary files before returning.
- Run the stages in order; stop when a gate fails and revise only surfaced
  findings. Resume with `resume --root "$TARGET"`.

Next: [scan.md](scan.md).
