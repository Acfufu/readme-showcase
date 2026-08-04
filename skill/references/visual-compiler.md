# Opt-in compiled visual workflow

This reference covers the opt-in `diagram_route: "compiled"` path. It adds a
deterministic visual compilation step to the existing README pipeline; it does
not create another mode, stage, or public command. The ordinary `none`,
`static`, and `elk` routes, the three README modes, and the one-README-Agent
ordering in [`SKILL.md`](../SKILL.md) remain the compatibility baseline.

The compiled route emits canonical data and static SVG. It does not assert
interactive-agent, animated-output, or external-service validation, and it has
no authority to push, open a pull request, or publish remotely.

## Opt in with README Plan v3

Select the route explicitly in a canonical README Plan v3. The plan keeps the
existing mode and evidence contract, uses the ordered `locales` mappings, and
sets `diagram_route` to `compiled`:

```json
{
  "schema_version": 3,
  "mode": "readme",
  "locales": [{"tag": "en", "readme_path": "README.md"}],
  "sections": ["overview", "quick-start"],
  "visual_intent": "evidence-bound workflow",
  "diagram_route": "compiled",
  "commands": ["preview"],
  "evidence_ids": ["documentation:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
}
```

`compiled` is rejected for Plan v1 and v2. No default producer is migrated:
leave `none`, `static`, or `elk` in place when compilation is not explicitly
requested. The plan's `mode` must still match the selected `run` mode.

## The author/compile boundary

The pipeline still has exactly eight ordered stages:

1. `scan`
2. `retrieve`
3. `plan-import`
4. `generation-request`
5. `candidate`
6. `bundle-assemble`
7. `validation`
8. `evaluation`

Plan v3 changes the inputs and work inside stages 5 and 6; it does not add
another stage. The same one-agent order and explicit waiting states apply to
both the compiled and ordinary routes.

### Stage 5 — candidate author outputs

After the generation request is accepted, the external author writes only
these files under the centralized candidate input area
(`stages/05-candidate/`); the candidate importer records their hashes before
Stage 6 creates its immutable attempt:

- one `README*.md` for every Plan v3 locale mapping;
- `claim-map.json` using Claim Map v3; and
- `visual-spec.json` using Visual Spec v1.

The Visual Spec is closed, canonical, evidence-bound input. It declares the
intent, nodes, edges, groups, lanes, constraints, and the requested
`desktop`/`mobile` variants. Stage 5 validates IDs, evidence references,
canonical bytes, and the resource boundary. Stage 5 does not author the final
Asset Manifest, compile geometry, or create publishable SVGs. A compiled
candidate must not contain `asset-manifest.json`.

### Stage 6 — bundle-assemble compiler outputs

`bundle-assemble` imports the immutable Stage 5 author files, normalizes the
Visual Spec, lays out each variant independently, uses the existing vendored
ELK only as bounded geometry support, and applies project-owned semantic,
geometry, security, and determinism gates. A successful attempt is appended
atomically at:

```text
stages/06-bundle-assemble/attempts/<attempt>/
├── generated-readme-bundle.json   # Generated Bundle v3
├── asset-manifest.json             # Asset Manifest v3, owned by Stage 6
├── assets/readme-showcase/<locale>/<variant>.svg
├── compiled/visual-spec.json
├── compiled/theme.json
├── compiled/inventory.json
├── compiled/scenes/<locale>/<variant>.json
├── compiled/gates/<locale>/<variant>.json
├── compiled/timeline/<locale>/<variant>.json
└── compiled/interaction/<locale>/<variant>.json
```

The compiled directory is one source-bound artifact set. Scene v1 is the
canonical desktop/mobile layout projection; the SVG, Gate Report v1, Timeline
v1, and Interaction v1 are derived projections of that Scene. `inventory.json`
is the canonical layered fingerprint over the Visual Spec, scenes, theme,
compiler identities, gates, timelines, interactions, and artifact bytes.
Generated Bundle v3 and Asset Manifest v3 bind those hashes; Generated Bundle
v3 records compiled retention as `manual`. Stages 7 and 8 then validate and
evaluate the same bundle, with Evaluation Report v3 carrying the compiled gate
and determinism metrics. A later local PR handoff uses PR Bundle v2; legacy
v1/v2 readers and the ordinary routes remain unchanged. No stage silently
replaces a failed compile with an older asset.

## Central state and retention

By default, all run inputs, stage attempts, diagnostics, previews, and compiled
bytes live outside the target repository at:

```text
${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/
└── <sha256-of-target-path>/runs/run-<random>/
```

The runner rejects a target-adjacent state path, symlinked ancestry, and unsafe
relative paths. It also never creates `.readme-showcase-run-*` beside the
target or a per-run virtual environment. `--workspace /absolute/path` is an
explicit expert override only when that path remains outside the target.

Stage attempts are immutable and have a `current.json` pointer. A failed Stage
6 compile removes only its uncommitted temporary attempt; the previous
`current.json` and committed attempt remain byte-identical. There is no
cross-run cache and no automatic pruning or cleanup. Manual retention is the
authoritative policy: keep, inspect, archive, or remove attempts deliberately
after the evidence and approval records have been handled.

## Existing command surfaces

The commands below are the current `readme_pipeline.py --help` surface. The
compiled route uses the same commands as ordinary routes; the route is selected
by the Plan, not by a new compiler command.

| Purpose | Command |
| --- | --- |
| Retrieve and inspect inputs | `validate-dataset`, `scan`, `retrieve` |
| Start/resume and inspect a run | `run`, `resume`, `status`, `explain`, `preview` |
| Validate and evaluate artifacts | `validate-bundle`, `evaluate` |
| Import a benchmark packet | `import-benchmark` |
| Build local handoff and check approval state | `build-pr-bundle`, `check-publish-gate` |

For a compiled local run, keep the same ordered flow and use the centralized
workspace returned by debug output:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" run \
  --root "$TARGET" \
  --mode readme \
  --project-type "$PROJECT_TYPE" \
  --locale en \
  --plan "$PLAN_V3" \
  --verbosity debug

python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" resume \
  --workspace "$RUN"
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" status \
  --workspace "$RUN"
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" preview \
  --workspace "$RUN"
```

The run may report `waiting-for-plan` or `waiting-for-candidate`; write the
missing canonical input at the reported central workspace and call `resume`.
`status` intentionally redacts the workspace unless debug verbosity is
requested; `explain` returns the canonical manifest for local inspection.
Standalone `validate-bundle` and `evaluate` use caller-supplied artifact roots;
`build-pr-bundle` and `check-publish-gate` enforce the outside-target
publication boundary, as enforced by the current CLI.

## Failure and explicit retry

Compilation is fail-closed. Invalid evidence, Visual Spec content, unsafe paths
or resources, geometry, SVG security, size limits, fingerprint drift, and
nondeterminism produce a diagnostic and no promotable Stage 6 artifact set.
There are no hidden retries and no best-effort fallback that changes approved
bytes.

After fixing the Stage 5 candidate or plan, retry explicitly with
`readme_pipeline.py resume --workspace "$RUN"`. Changed upstream fingerprints
mark downstream stages stale and recompute them in order. A failed attempt is
not promoted, and the prior committed attempt remains available for manual
comparison. If validation or evaluation remains failed, stop at its diagnostic
and do not create a publish handoff.

## Resource and visual limits

The trust boundary rejects values before promotion. The current hard limits are:

- canonical Visual Spec: 256 KiB;
- each Scene and SVG: 2 MiB;
- each Gate Report, Timeline, and Interaction: 512 KiB;
- all compiled bytes for one run: 16 MiB;
- SVG: at most 5,000 elements, 2,000 paths, depth 64, and dimension 20,000;
- geometry coordinates and dimensions: non-negative integers no larger than
  20,000, with no unrelated node overlap or illegal edge crossing;
- desktop: 1,200-wide viewBox, core text at least 16 units, checked at 900 px;
- mobile: independently planned width at most 720, core text at least 24
  units, checked at 360 px.

Unsafe URLs, absolute or traversal paths, local asset bytes, scripts, external
references, foreign objects, XML entities/DOCTYPE, symlinks, special files,
and output-parent replacement races fail closed. Timeline and interaction
outputs are data only; they contain no executable HTML or script.

## Local preview and publication boundary

`preview` is a local projection of the committed attempt. `build-pr-bundle`
creates a fingerprinted local handoff, and `check-publish-gate` checks that a
fresh remote-state snapshot and explicit approval envelope match it. Neither
command publishes, pushes, opens a pull request, or calls an external provider.
Remote writes require a separate, later user authorization bound to the exact
fingerprint, target, branch, and base SHA; this Skill has no publication
authority.

Read the [visual-kernel clean-room note](visual-kernel-clean-room.md) for the
behavior-only design boundary, excluded runtime payloads, and no-copy fixture
rule. The existing visual and motion references remain separate opt-in routes;
those checks remain outside the compiled contract.
