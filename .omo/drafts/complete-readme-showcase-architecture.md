---
slug: complete-readme-showcase-architecture
status: approved
intent: clear
review_required: false
pending-action: execute plan
approach: "Execute the 26 remaining Trellis tasks in corrected dependency order, preserving v1 contracts and using task-owned worktrees; keep delivery offline until a separately approved publish flow exists."
---

# Draft: complete-readme-showcase-architecture

## Components (topology ledger)

The start-work bootstrap leaves 26 implementation tasks. Each task owns its worktree, tests, and evidence; milestone rows are the independently gateable topology.

| component | outcome | status | remaining tasks | evidence |
| --- | --- | --- | --- | --- |
| M1 core orchestration | Resumable `run/resume/status/explain`, run workspace, stage runner, and generation request without changing v1 CLI or imports. | active | M1-T1, M1-T2, M1-T3 (3) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:554-779` |
| M2 core scanning | Tracked-file index, scanner profiles, and explicit partial/incomplete scan results that remain ineligible for publish. | active | M2-T1, M2-T2, M2-T3 (3) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:785-972` |
| M3 core evidence | Evidence v2, selective extractors, and multi-evidence claim maps with v1 adapters. | active | M3-T1, M3-T2, M3-T3 (3) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:972-1160` |
| M4 core diagnostics | Fail-fast security errors plus aggregated content diagnostics and bounded revision context. | active | M4-T1, M4-T2 (2) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1160-1267` |
| M5 quality | Contract, behavior, editorial evaluation, and preview reporting; preview never executes candidate commands. | active | M5-T1, M5-T2, M5-T3, M5-T4 (4) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1267-1440` |
| M6 data/retrieval | Deterministic project classification, hybrid ranking, offline benchmark, and curated retrieval inputs. | active | M6-T1, M6-T2, M6-T3, M6-T4 (4) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1440-1585` |
| M7 quality/data contracts | Draft 2020-12 JSON Schema parity and BCP 47 locale validation across package boundaries. | active | M7-T1, M7-T2 (2) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1585-1667` |
| M8 delivery | Immutable-base temporary worktree and GitHub adapter contracts, demonstrated with mock transport or `--dry-run` only. | active, offline-only | M8-T1, M8-T2, M8-T3 (3) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1667-1786` |
| M9 feedback | Append-only local feedback events and advisory aggregate metrics; feedback cannot override evidence or safety policy. | active | M9-T1, M9-T2 (2) | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1786-1870` |
| governance | Compatibility, security, evidence provenance, review gates, CI pinning, task ownership, and release boundaries. | active | Cross-cutting; no extra task | `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:142-148`, `2041-2078` |

**Total remaining:** 26 tasks (3 + 3 + 3 + 2 + 4 + 4 + 2 + 3 + 2).

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Compatibility baseline | Preserve v1 CLI, public Python imports, canonical JSON, error codes, and fail-closed behavior; add v2 adapters rather than replacing v1. | The architecture explicitly requires parallel schema migration and compatibility protection. | Yes, after a separately reviewed major-version migration. |
| Current test baseline | Treat 89/89 as the current baseline for Python 3.11, Python 3.13, and supported `npm test`; refresh evidence after source changes. | The reviewed Batch 1 acceptance records 89/89. | Yes, only with a recorded baseline update. |
| JSON Schema tooling | Pin `jsonschema==4.26.0` in CI/dev validation. | Deterministic validator behavior is preferable to an unbounded environment dependency. | Yes, through a reviewed dependency update. |
| Dataset inputs | Treat all 12 vetted retrieval candidates as inputs; promote at least eight only after an independent human approval record, bringing every current project type to at least five records. `source.human_reviewed` is never self-attested. | Retrieval patterns are editorial inputs, never target facts; review status must be independently recorded. | Yes, per-record review can be added or revoked. |
| Delivery transport | M8 demonstrations use mock transport and `--dry-run` only; no live GitHub write is in this execution. | Dry-run must not fabricate a commit SHA, PR URL, or PR number. | Yes, only after a new explicit approval and remote preflight. |
| Rendering/model boundary | No YAML/Markdown renderer and no model-provider API or SDK; Codex supplies generation outside this repository. | Keep the deterministic core small and the generation boundary explicit. | Yes, only by a new scoped architecture decision. |
| Worktree ownership | Every Trellis task uses a task-owned worktree; M8 delivery worktrees are temporary, detached from an immutable base SHA, and outside the target repository. | Prevent cross-task mutation and keep uncommitted main-worktree bytes out of evidence and delivery. | Yes, if an equivalent isolated ownership boundary is documented. |

## Findings (cited - path:lines)

- Batch 1 is recorded as complete, while the next execution entry is Batch 2; the reviewed acceptance states 89/89 on Python 3.11 and 3.13 and preserves v1 behavior: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:12-29`.
- The milestone topology is M1-M9, and the dependency graph is not a simple numeric sequence; Trellis children must repeat their own dependencies: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:384-421`.
- Existing safety and compatibility semantics require v1 `build_pr_bundle` behavior to remain unchanged, with M8 dirty-worktree support isolated behind a v2 path: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:142-148`.
- Schema producers own validators, Draft 2020-12 schemas, fixtures, and v1 adapters; M7 owns parity and package/CI audit: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:367-380`, `1585-1606`.
- M8 requires an external temporary worktree and explicitly limits dry-run to planned metadata without fabricated remote identifiers; mock transport covers failures: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1667-1755`.
- M9 feedback is local, append-only, privacy-bounded, and advisory only: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:1786-1868`.
- The explicit non-goals exclude dependency/ELK supply-chain changes, renderer plugins, multi-agent generation, databases, SaaS, automatic unapproved publishing, arbitrary README command execution, and model SDKs: `README_SHOWCASE_CODEX_ARCHITECTURE_PLAN.md:2062-2078`.
- CI keeps the legacy all-tests lane, validation, package dry-run, and read-only permissions; this is the integration point for the announced `jsonschema==4.26.0` dev/CI pin: `.github/workflows/ci.yml:8-32`, `34-49`.
- The repository dataset currently contains 12 records across four project types and tests assert the count and review flag; the new governance default requires independent approval before treating that flag as trusted: `tests/test_dataset_population.py:15-35`, `dataset/retrieval/manifest.json:1-5`.

## Decisions (with rationale)

1. **Execute all 26 remaining tasks, not a partial architecture slice.** The user’s start-work bootstrap explicitly ordered Trellis task creation and complete execution; the count is the M1-M9 task ledger above.
2. **Use the corrected dependency graph, not milestone-number order.** M1 generation request feeds M3 claims, M3/M4 feed M5 and M8, M6 follows evidence, schemas flow into M7, and M8 precedes M9; each task records those edges in its own Trellis record.
3. **Preserve v1 as a hard compatibility line.** New artifacts and stages may be v2, but no existing v1 command, import, safety check, or output contract is weakened.
4. **Keep work isolated by task-owned worktree.** A task may consume immutable predecessor artifacts but must not edit another task’s worktree or the main checkout.
5. **Keep M8 offline in this run.** Mock and dry-run are the only delivery observables; live GitHub branch, commit, push, and PR writes remain outside scope and require a later explicit approval.
6. **Require independent dataset approval.** The 12 vetted candidates are usable inputs for retrieval work, but `human_reviewed` becomes trusted only after an independent reviewer records approval.
7. **Keep the core deterministic and dependency-light.** No YAML/Markdown renderer, model API, or new abstraction for a single transport; use existing JSON, Python, and `gh`-specific contracts where already specified.

## Scope IN

- Implement M1-M9 as 26 Trellis tasks using the dependency graph above.
- Build the resumable orchestration, scanner partial-success model, evidence/claim v2, diagnostics loop, three-layer evaluation, deterministic retrieval benchmark, JSON Schema/locale contracts, offline M8 delivery contracts, and local M9 feedback loop.
- Preserve v1 CLI/import/error/security behavior and add compatibility adapters where v2 artifacts are introduced.
- Add the CI/dev `jsonschema==4.26.0` pin and keep the 89/89 baseline as the regression gate.
- Treat the 12 researched candidates as curation inputs; after independent human approval, promote at least eight so the manifest has at least 20 records and every current project type has at least five.
- Record per-task tests and evidence in task-owned worktrees; use mock/dry-run artifacts for M8.

## Scope OUT (Must NOT have)

- No YAML renderer, Markdown renderer, model-provider API/SDK, or automatic README command execution.
- No live GitHub branch creation, commit, push, PR write, or other remote mutation in M8 for this execution.
- No unapproved dataset promotion, copied upstream README/code/assets, or retrieval leakage from test split into production ranking.
- No weakening of v1, path/symlink/special-file, hash, approval, evidence, or immutable-base safety gates.
- No ELK upgrade, npm/dependency-lock or supply-chain redesign, renderer plugin system, multi-agent generation, vector/cloud database, SaaS console, or automatic unapproved publishing.
- No product implementation or rewrite of the existing work plan from this draft; this file is the planning bootstrap only.

## Open questions

None. The defaults above are announced and reversible; each Trellis task carries its own acceptance gate, and any requested deviation becomes a new explicit decision rather than an implicit assumption.

## Approval gate

status: approved

Basis: start-work bootstrap completed with clear intent, `review_required: false`, and the user’s explicit order to create Trellis tasks and complete all 26 remaining tasks. The next action is execution of the approved plan, not another exploration or approval round.
