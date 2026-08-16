# Failure recovery: stage matrix, resume map, global rules

How the eight-stage pipeline fails, what blocks, what recovers on its own, and
where a `resume` picks back up. Every error code below is grounded in the
pipeline source (`skill/scripts/readme_showcase/`); no code is invented.

## Stage × failure-mode matrix

Rows are the eight pipeline stages in order (contracts: `STAGE_NAMES` in
`contracts/run.py:32`; adapters: `STAGES` in `orchestration/stages.py:994`).
"Blocking" means the run stops and the stage needs something before it can
finish; "auto-recoverable" means a plain idempotent rerun or resume makes
progress without a code or state change.

| Stage | Error codes (primary) | Blocking? | Auto-recoverable? | User intervention | Recovery entry point |
| --- | --- | --- | --- | --- | --- |
| `scan` | `E_RUN_TARGET`, `E_RUN_STATE_ROOT`, `E_RUN_INPUT` (voice/visual facts); non-pass scan result | Yes, if target/state broken or facts malformed; content-only "failed" result is blocking | Yes for retries; not for a changed target | Fix target (must be a Git repo with immutable HEAD); move target out of the state root; replace malformed fact JSON | `resume --root <target>` (or `run --root <target>` for a fresh run) |
| `retrieve` | `E_RETRIEVAL_PACKET`, `E_DATASET_*`, `E_SCHEMA_TYPE`, `E_SCHEMA_UNKNOWN_FIELD`, `E_SCHEMA_MISSING_FIELD`, `E_RUN_INPUT` | Yes | Yes — rerun re-derives the packet from unchanged scan + dataset | Repair the vendored dataset manifest or the packet contract violation; do not hand-edit a packet | `resume --root <target>` |
| `plan-import` | `E_INPUT_NOT_FOUND` (→ `waiting-for-plan`, not a failure), `E_GENERATION_REQUEST_SIZE`, `E_RUN_INPUT`, plan validation `E_SCHEMA_*` / `E_SCHEMA_VERSION` | Only on validation/size errors | Yes — once the plan file changes, the fingerprint changes and resume re-imports | Write/repair `inputs/readme-plan.json` in the workspace; canonical JSON required | `resume --root <target>` (optionally `--plan <path>`); write plan first |
| `generation-request` | `E_RUN_INPUT`, `E_SCHEMA_VERSION`, `E_GENERATION_EVIDENCE`, `E_GENERATION_EVIDENCE_DANGLING`, `E_GENERATION_EVIDENCE_DUPLICATE`, `E_GENERATION_EVIDENCE_STALE`, `E_GENERATION_RETRIEVAL`, `E_GENERATION_REQUEST_VALUE`, `E_GENERATION_REQUEST_SIZE`, `E_LOCALE`, `E_SCHEMA_*` | Yes | Yes — deterministic from upstream stages | Fix upstream evidence/retrieval/plan so the request builds; do not hand-write the request | `resume --root <target>` |
| `candidate` | `E_INPUT_NOT_FOUND` (→ `waiting-for-candidate`, not a failure), `E_RUN_PATH`, `E_INPUT_SIZE`, `E_SCHEMA_VALUE`, `E_SCHEMA_VERSION` | Yes, on path/size/schema errors | Yes — once candidate files change, resume re-imports | Write/repair candidate files in `stages/05-candidate/`; respect the bounded tree rules | `resume --root <target>` |
| `bundle-assemble` | `E_SCHEMA_TYPE`, `E_RUN_PATH`, `E_BUNDLE_HASH`, `E_BUNDLE_PLAN`, `E_SCHEMA_FIELDS`, `E_VISUAL_PATH`, `E_BUNDLE_MODE`, `E_CLAIM_LANGUAGE`, `E_BUNDLE_ASSET`, `E_VISUAL_FINGERPRINT` | Yes | Yes — deterministic from upstream; a hash mismatch stays until the candidate bytes match their manifest | Repair the candidate asset manifest/hash or candidate bytes; for compiled routes fix the Visual Spec/Claim Map | `resume --root <target>` |
| `validation` | Report `status: "fail"` with `E_VISUAL_SPEC_EVIDENCE`, `E_OUTPUT_PATH`, and every bundle/claim/evidence gate code; `E_REVISION_COMMIT` / `E_REVISION_RECOVERY` re-raised | Yes | Auto-revision loop up to `MAX_REVISION_ATTEMPTS`; after that a resume needs changed input | Fix surfaced diagnostics; re-author candidate/plan; only then resume | `resume --root <target>`; revision history under `stages/04-generation-request/revisions/` |
| `evaluation` | Report `status: "fail"` (hard gate diagnostics: claims, evidence binding, locale, asset bytes); `E_REVISION_COMMIT` / `E_REVISION_RECOVERY` re-raised | Yes | No — evaluation failures never auto-pass; revise input and re-run | Address the gate diagnostics in the candidate/plan, then resume; no bypass flag exists | `resume --root <target>` |

## Per-stage failure codes (grounded)

Where each code is raised and what it means. All codes are real
`ContractError` codes from the files listed.

### `scan` (`orchestration/stages.py:161`, `ScanStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_RUN_TARGET` | `runner.py` `_git` (55-68), `_target_root`, `_resolved_workspace` (171-176), `read_manifest` (`workspace.py:332`) | Target is not a Git repository, HEAD is not immutable, or the workspace manifest points at a different target | Point `--root` at the real repo; `run` not `resume` if the target changed |
| `E_RUN_STATE_ROOT` | `runner.py:139` (allocation), `145` (open), `153` (non-dir entry) | Centralized state dir cannot be allocated/opened, or a run entry is not a real directory | Fix `CODEX_HOME` / state permissions; remove corrupt entries |
| `E_RUN_INPUT` | `stages.py:132`, `150` (voice/visual fact JSON malformed or not a bounded list) | Optional scan facts are corrupt | Remove or repair the fact file; rerun |
| scan non-pass | `stages.py:199` (`StageResult("failed")` when `value["status"] != "complete"`) | `scan_repository_v1` hit a scan limit (files/dirs/bytes/depth/seconds) | Enlarge limits or simplify the tree; rerun |

### `retrieve` (`stages.py:202`, `RetrieveStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_RETRIEVAL_PACKET` | `contracts/retrieval.py:108`, `110`, `116`, `119` | Packet slugs must be bounded/sorted/unique; text lists bounded, normalized, sorted, unique | Fix the ranker/packet producer, not the bytes by hand |
| `E_DATASET_*` | `retrieval/service.py` `validate_dataset_manifest` (`:99`; `E_DATASET_*` raises `:70-142`) | Dataset manifest contract violation (records, split, license, provenance, sha256) | Repair the vendored `dataset/retrieval/manifest.json` |
| `E_SCHEMA_TYPE` / `E_SCHEMA_UNKNOWN_FIELD` / `E_SCHEMA_MISSING_FIELD` | `contracts/retrieval.py:95-102` | Packet shape violates its schema | Fix the producer |
| `E_RUN_INPUT` | `stages.py:210` (evidence canonical bytes; raise in `_canonical_object`, `stages.py:96`) | Evidence JSON is not canonical | Re-run scan |

### `plan-import` (`stages.py:223`, `PlanImportStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_INPUT_NOT_FOUND` | `stages.py:244` | No `inputs/readme-plan.json` yet → stage reports `waiting-for-plan` (run pauses, not fails) | Write the plan; resume |
| `E_GENERATION_REQUEST_SIZE` | `stages.py:236`, `runner.py:221-224` (`_copy_plan`) | Plan exceeds `MAX_GENERATION_REQUEST_BYTES` | Shrink the plan |
| `E_RUN_INPUT` | `stages.py:242-247` (non-canonical plan bytes); `_canonical_object` (`stages.py:93-97`) | Plan JSON must use canonical bytes | Re-serialize with `canonical_json_bytes` |
| `E_SCHEMA_*` / `E_SCHEMA_VERSION` | `validate_readme_plan` (`stages.py:247`) | Plan violates the README Plan contract for the current mode | Fix the plan |

### `generation-request` (`stages.py:251`, `GenerationRequestStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_SCHEMA_VERSION` | `stages.py:117` (`_v3_evidence_graph`) | Evidence schema_version is not 1 or 2 | Re-scan to a supported version |
| `E_GENERATION_EVIDENCE*` | `generation/request.py` (`E_GENERATION_EVIDENCE`, `_DANGLING`, `_DUPLICATE`, `_STALE`) | Evidence graph/references are dangling, duplicated, or stale vs base SHA | Refresh evidence (re-scan) |
| `E_GENERATION_RETRIEVAL` / `E_GENERATION_REQUEST_VALUE` / `E_GENERATION_REQUEST_SIZE` | `generation/request.py` | Retrieval packet or request contract violation; request too large | Fix upstream packet / shrink plan |
| `E_LOCALE` | `generation/request.py` | Locale not in the allowed set or not paired with a README path | Fix plan locales |
| `E_RUN_INPUT` | `stages.py:258-260` (canonical upstream files; raise in `_canonical_object`, `stages.py:96`) | Upstream attempt files not canonical | Re-run upstream stages |
| `E_REVISION_*` | `runner.py` `_revision_root`/revision commit helpers | Revision history state corrupt (`E_REVISION_COMMIT`, `E_REVISION_RECOVERY` re-raised at `runner.py:726-728`) | Repair revision state under `stages/04-generation-request/revisions/`; these are never auto-recovered |

### `candidate` (`stages.py:506`, `CandidateImportStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_INPUT_NOT_FOUND` | `stages.py:477-478` (`candidate_files`) | No candidate yet → `waiting-for-candidate` (pause) | Write candidate files; resume |
| `E_RUN_PATH` | `stages.py:289`, `300`, `331`, `418`, `487-489`; `_read_candidate_asset` | Candidate tree has symlinks, non-regular files, or unsafe paths | Rewrite candidate tree without links/irregular files |
| `E_INPUT_SIZE` | `stages.py:284-285`, `333`, `359`, `394`, `401`, `434`, `438`, `482` | Candidate exceeds per-file/entry/depth/total byte bounds | Trim candidate assets |
| `E_SCHEMA_VALUE` | `stages.py:459` | Compiled candidate must not supply `asset-manifest.json` | Remove it |
| `E_SCHEMA_VERSION` | `stages.py:468` | Compiled candidate Claim Map requires `schema_version: 3` | Fix the Claim Map |

### `bundle-assemble` (`stages.py:524`, `BundleAssembleStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| `E_SCHEMA_TYPE` | `stages.py:537`, `541`, `860`, `874`, `894` | Manifest/bundle arrays or entries malformed | Fix the manifest |
| `E_RUN_PATH` | `stages.py:544`, `817` | Asset path escapes `assets/` or duplicates a materialized path | Fix candidate paths |
| `E_BUNDLE_HASH` | `stages.py:547`, `804`, `819`, `884`, `908`, `912` | Asset/artifact bytes differ from their declared sha256 | Recompute hashes or restore bytes |
| `E_BUNDLE_PLAN` | `stages.py:785` | Bundle v3 requires README Plan v3 with `diagram_route: "compiled"` | Align plan and route |
| `E_VISUAL_PATH` / `E_VISUAL_FINGERPRINT` | `stages.py:797`, `813`, `815`, `878`, `880`, `898` (`E_VISUAL_PATH`), `844` (`E_VISUAL_FINGERPRINT`) | Visual Spec path/hash mismatch between stage 5 and stage 6 | Re-compile from the stage-5 source |
| `E_BUNDLE_MODE` / `E_CLAIM_LANGUAGE` | `stages.py:863`, `865`, `882` | Bundle README references disagree with the plan's mode/locales | Fix candidate references vs plan |
| `E_BUNDLE_ASSET` | `stages.py:900`, `902`, `905` | Candidate SVG not a stage-6 SVG, duplicated, or absent from inventory | Re-run the compiled route |

### `validation` (`stages.py:959`, `ValidateStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| Report `fail` (any gate code) | `stages.py:971-972` | `validate_generated_bundle` diagnostics; the failure is captured into `validation-report.json`, the stage reports failed | Fix the surfaced diagnostics; auto-revision loop fires at index 6 (`runner.py:721-722`) |
| `E_VISUAL_SPEC_EVIDENCE` | `visual_kernel/gates.py:180`, `186`, `189`, `192` | Compiled scene claims must bind to Evidence v2 graph ids | Fix the Visual Spec / scene claims |
| `E_OUTPUT_PATH` | `stages.py:919`, `929` | Materialization destination ancestry is unsafe | Move/clean the materialization target |
| `E_REVISION_COMMIT` / `E_REVISION_RECOVERY` | `runner.py:726-728` | Revision commit failed or unrecoverable | Repair revision state; not auto-recovered |

### `evaluation` (`stages.py:976`, `EvaluateStage`)

| Code | Raised where | Meaning | Recover |
| --- | --- | --- | --- |
| Report `fail` | `stages.py:986-987` | Hard evaluation gates failed; the stage reports failed and downstream stages go `stale` | Address diagnostics; resume. No bypass: evaluation is the authority |
| `E_REVISION_COMMIT` / `E_REVISION_RECOVERY` | as above | Revision machinery broken | Repair revision state |

## Resume map

Run state lives outside the target in a centralized directory
(`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`, keyed by
`sha256(target_absolute_path)`, `runner.py:100-122`). Each run is
`runs/run-<32-hex>/`. The source of truth is `run-manifest.json`
(`workspace.py:328-335`), which stores per-stage status, attempt number,
`input_sha256`, and `output_sha256` (`contracts/run.py`).

### Artifact layout and `output_sha256`

- Attempt outputs live at
  `stages/<NN>-<stage>/attempts/<attempt>/<file>` for each of the eight
  stages (`RunContext.attempt_file`, `stages.py:66-75`; written by
  `RunWorkspace.append_attempt`, `workspace.py:572`).
- `output_sha256` of a stage is not a file hash: it is the canonical SHA-256
  over the sorted list of `{path, sha256}` pairs of every file in that
  attempt directory (`RunWorkspace.attempt_output_sha256`, `workspace.py:337`).
  The candidate stage is special-cased at attempt 0 to use `input_sha256`
  (`runner.py:664-668`).
- `input_sha256` is the adapter's `fingerprint()` (`stages.py:88`), which
  covers everything the stage consumes: scan evidence for `scan`, dataset +
  upstream scan for `retrieve`, plan bytes for `plan-import`, upstream stage
  hashes for later stages.
- Skipping: `_drive` skips a stage only when status is `pass`, `input_sha256`
  equals the live fingerprint, and the stored output matches the manifest
  (`runner.py:669-674`). Otherwise the stage re-runs; changed inputs mark
  downstream stages `stale` (`_stale_from`, `runner.py:250-252`).

### Last-good state → resume point per failure

| Failure | Last-good state | Resume point |
| --- | --- | --- |
| `scan` content failure | Nothing persisted (stage 0) | `resume --root <target>` re-scans |
| `retrieve` packet failure | `stages/01-scan/attempts/N/repository-evidence.json` (+ optional voice/visual facts) | `resume --root <target>`; packet re-derived deterministically |
| `plan-import` waiting/validation | scan attempt; no plan imported | Write `inputs/readme-plan.json` (canonical bytes), then `resume --root <target>` (or `--plan <path>`) |
| `generation-request` failure | plan + retrieval attempts | Fix upstream evidence/plan; `resume --root <target>` |
| `candidate` waiting/validation | plan-import attempt; `stages/04-generation-request/...` | Write candidate files under `stages/05-candidate/`; `resume --root <target>` |
| `bundle-assemble` failure | candidate files in place | Fix manifest/hash/bytes; `resume --root <target>` |
| `validation` fail | stage-6 attempt written; revision may be pending (`revisions/`) | Fix diagnostics; `resume --root <target>` (auto-revision loop already tried up to `MAX_REVISION_ATTEMPTS`) |
| `evaluation` fail | validated bundle attempt; run `manual-review-required` | Fix candidate/plan; `resume --root <target>` |

`resume` resolves the workspace with `_resolved_workspace` (`runner.py:171-176`):
an explicit `--workspace` wins; otherwise the latest run for `--root`'s target
(`latest_default_workspace`, `runner.py:141-168`) — which errors with
`E_RUN_NOT_FOUND` when no run exists, so `resume` on a never-run target fails
loudly instead of inventing state.

## Global rules

1. **Idempotent rerun.** Re-running or resuming re-executes only non-pass /
   drifted stages. Passed stages whose fingerprint and output hash are intact
   are skipped (`runner.py:669-674`). Fixing an input marks all downstream
   stages `stale`; nothing is silently reused.
2. **Lock semantics (`E_RUN_LOCKED`).** Every `run` / `resume` / `preview`
   acquires two locks: the workspace mutex (`RunWorkspace.lock`) and a
   `flock` on `<workspace>/.runner.lock` (`_runner_lock`, `runner.py:189-213`).
   A concurrent run raises `E_RUN_LOCKED` ("run workspace is locked") — never
   force, delete, or bypass the lock; serialize and retry after the other run
   exits. Stage mutations take the same lock individually.
3. **No gate bypass.** There is no flag, manifest edit, or command that marks a
   failed validation/evaluation stage as passed. Non-pass stages set the run to
   `manual-review-required`, downstream stages go `stale`, and `resume`
   re-executes the failing stage against real inputs. Hand-editing
   `run-manifest.json` is rejected: `read_manifest` validates the manifest and
   requires canonical bytes (`E_RUN_MANIFEST_CANONICAL`, `workspace.py:334`).
   `E_REVISION_COMMIT` / `E_REVISION_RECOVERY` are deliberately re-raised
   (`runner.py:726-728`) so a corrupt revision history can never be papered over.
4. **Fresh-chat recovery.** Any new session resumes an interrupted run with
   `python3 .../readme_pipeline.py resume --root <target>` (or the explicit
   `--workspace` from a debug summary). `status` / `explain` read the manifest
   without locking; `preview` re-locks and re-verifies the revision pointer
   before rendering. State survives across chats because it lives outside the
   target and is keyed by absolute target path.
