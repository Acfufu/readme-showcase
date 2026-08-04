---
name: readme-showcase
description: Create, redesign, audit, or visually upgrade evidence-backed GitHub README homepages for apps, extensions, CLIs, services, libraries, and developer tools. Use for whole-README information architecture, project-native heroes and diagrams, README-only visual assets, GitHub-safe SVG or opt-in GIF animation, multilingual README variants, install/use/configuration flows, and removal of unsupported claims or decorative clutter.
---

# README Showcase

Turn verified repository behavior into a clear project homepage. Keep Markdown as the searchable content layer and use visuals only when they communicate identity, proof, comparison, sequence, or architecture.

## Choose the scope

Use exactly one operating mode:

- **README mode** — improve the README's reading order, copy, proof, Markdown, and any justified visual system.
- **Asset-only mode** — create only the requested hero, section header, workflow, diagram, badge, or coordinated asset set. Do not edit, reorder, or embed anything in the README without separate approval.
- **Audit-only mode** — inspect and report findings without generating or changing README or asset candidates.

If the request is ambiguous, inspect read-only context and ask whether the user wants the whole README, assets only, or an audit.

## Inspect before writing

1. Read the current README, repository tree, manifests, entry points, releases, install metadata, configuration, docs, tests, license/security files, screenshots, logos, design tokens, and real outputs.
2. Record the audience, one-sentence value, primary proof, first successful action, differentiator, limitations, and unsupported claims.
3. Preserve unrelated changes. Do not commit, push, publish, or modify a remote repository without explicit authorization.

Read [references/structure.md](references/structure.md) for evidence mapping, narrative selection, project-type sections, localization, and content validation.

For the opt-in visual compiler, read [references/visual-compiler.md](references/visual-compiler.md).
It documents the Plan v3 `diagram_route: "compiled"` contract, the Stage 5
author boundary, Stage 6 outputs, centralized retention, limits, retry rules,
and the local-only publication boundary. Compiled work stays inside the
existing eight-stage, one-README-Agent order; `none`, `static`, and `elk` keep
their ordinary behavior.

## One README Agent pipeline

Use one README Agent: this Agent. Never delegate README truth or writing to
another Agent, retrieval record, visual engine, or evaluator. Retrieval
patterns are not target facts; every factual claim still requires current
repository evidence.

Ownership stays fixed:

| Layer | Owns |
| --- | --- |
| This Skill | Dataset, target evidence, retrieval, claims, modes, routing, evaluation, fallback, local PR bundle, approval gate |
| Adapted `beautify-github-readme` rules | Story order, project-native title/palette/art direction/composition, visual and motion QA |
| Optional verified ELK | `architecture` / `flowchart` / `c4` body geometry only; project code owns safe SVG bytes |

Dataset revision 2 contains 10 production `train` patterns and two isolated
`test` patterns. Records are newly authored abstractions bound to pinned public
repository commits and per-source license evidence; they contain no copied
README text, code, assets, or benchmark answers.

Run one ordered artifact flow in orchestrator-managed central state. Never
create `.readme-showcase-run-*` or another run directory in or beside the target
repository. The default location is
`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`, keyed by target repository:

```bash
README_SHOWCASE_SKILL="${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase"
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" run \
  --root "$TARGET" \
  --mode readme \
  --project-type "$PROJECT_TYPE" \
  --locale en \
  --verbosity debug
```

The debug result exposes the selected internal workspace when plan or candidate
inputs must be written; use that path as `$RUN` below. Resume with
`resume --root "$TARGET"`. Omit debug verbosity from user-facing status and
handoff output. Use explicit `--workspace` only when the user requests a custom
absolute location. Never create a per-run virtual environment; use the existing
runtime and remove temporary files before returning.

When `readme-plan.json` is Plan v3 with `diagram_route: "compiled"`, the
external candidate owns only the locale README files, Claim Map v3, and Visual
Spec v1 in `stages/05-candidate/`. Existing `bundle-assemble` then compiles
independent desktop/mobile Scene, SVG, gate, timeline, interaction, and
fingerprint outputs under its immutable Stage 6 attempt; it owns Asset Manifest
v3 and Generated Bundle v3. This is an opt-in compatibility path, not a new
stage or command, and it does not grant publication authority.

1. Validate licensed retrieval patterns:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" validate-dataset \
     --manifest "$README_SHOWCASE_SKILL/dataset/retrieval/manifest.json"
   ```

2. Scan target repository:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" scan \
     --root "$TARGET" \
     --output "$RUN/repository-evidence.json"
   ```

3. Retrieve up to five train-only patterns for evidence-bound query dimensions:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" retrieve \
     --evidence "$RUN/repository-evidence.json" \
     --manifest "$README_SHOWCASE_SKILL/dataset/retrieval/manifest.json" \
     --project-type developer-tool \
     --section overview \
     --section quick-start \
     --tag workflow \
     --mode production \
     --output "$RUN/retrieval-packet.json"
   ```

4. Write `readme-plan.json`, candidate files, `claim-map.json`, and
   `asset-manifest.json` directly from target evidence. For v2 multilingual
   work, use an explicit ordered `locales` array of `{tag, readme_path}`
   mappings; allowed tags are exactly `en`, `zh-Hans`, `zh-Hant`, `ja`, `ko`,
   `fr`, and `de`. Every asset must declare either a supported `locale` or
   `language_neutral: true`, never both. Do not infer, normalize, or pair
   locales from filenames, directories, or suffixes: `workflow-zh.svg` is not
   locale metadata; legacy visual markup such as
   `data-readme-language="neutral"` is an output annotation, not v2 manifest
   metadata. Use semantic `language_pair_id` values and matching ordered
   evidence/support for localized claims. Keep v1 bilingual
   `README.md`/`README_zh.md` inputs readable and unchanged. If route is `elk`,
   invoke optional adapter once; its two fresh runs are validation, not hidden
   retries:

   ```bash
   node "$README_SHOWCASE_SKILL/scripts/render_elk.mjs" \
     --input "$RUN/diagram.diagram.json" \
     --output "$RUN/diagram.svg" \
     --metadata "$RUN/diagram.engine.json"
   ```

5. Assemble `generated-readme-bundle.json`, then validate it:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" validate-bundle \
     --bundle "$RUN/generated-readme-bundle.json"
   ```

6. Evaluate hard gates and revise only surfaced findings:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" evaluate \
     --bundle "$RUN/generated-readme-bundle.json" \
     --output "$RUN/evaluation-report.json"
   ```

7. After Pass, build local fingerprinted handoff only:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" build-pr-bundle \
     --bundle "$RUN/generated-readme-bundle.json" \
     --evaluation "$RUN/evaluation-report.json" \
     --output "$RUN/pr-bundle.json"
   ```

8. Only after explicit approval and fresh read-only remote preflight, check
   exact publish state:

   ```bash
   python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" check-publish-gate \
     --pr-bundle "$RUN/pr-bundle.json" \
     --remote-state "$RUN/remote-state.json" \
     --approval "$RUN/approval-envelope.json" \
     --output "$RUN/publish-gate.json"
   ```

Never publish from evaluation success alone. GitHub branch, commit, push, and PR
writes require separate explicit approval bound to current fingerprint, target,
branch, and base SHA.

### Diagram route

| Route | Select when | Result |
| --- | --- | --- |
| `none` | Diagram adds no material proof or comprehension | Keep facts and instructions in Markdown |
| `static` | Project identity/composition matters more than automatic layout; diagram is compact; type is unsupported by ELK | Skill-authored project-native static SVG |
| `elk` | Allowed `architecture`, `flowchart`, or `c4` body diagram has relationship-heavy grouping, routing, or label wrapping and exact Node 22.22.3 is available | Strict semantic JSON plus project-serialized standalone SVG |

ELK never owns hero/title bar, palette choice, claims, README copy,
surrounding composition, alt/caption, acceptance, fallback, or publishing.
Reference SVG as a separate relative Markdown image with evidence-bound alt
text and adjacent Markdown caption. Do not inline or post-edit accepted bytes.

Missing, mismatched, unsafe, oversized, timed-out, or nondeterministic engine
results select `static`; preserve README, semantic source, raw SVG, metadata,
and last-known-good asset bytes. Do not require ELK for README,
asset-only, or audit-only mode. Read
[references/elk-structure.md](references/elk-structure.md) before any
ELK route.

## README mode

1. Put value and proof before mechanism and exhaustive detail.
2. Choose only sections supported by the project. Make the quickest successful path observable with a command, output, screen, or health endpoint.
3. Decide whether visuals materially improve comprehension:
   - Use a Markdown hero when the project has no honest visual proof.
   - Create project-native static SVG or raster assets when the repository provides meaningful structure, flow, interface, or output.
   - When pure SVG and hybrid raster composition are both credible for a hero-like asset, require the user to choose or explicitly delegate the choice before generation.
4. Keep commands, API details, links, limitations, and frequently updated text in Markdown.
5. Render or preview the result before handoff.

Read [references/visual-production.md](references/visual-production.md) before creating or revising visual assets.

## Asset-only mode

1. Confirm the requested asset type and whether it is one asset or a coordinated set.
2. Derive copy, palette, typography, motif, composition, and proof from the repository.
3. Default to maintainable static SVG for deterministic graphics. Use PNG/WebP for screenshots, photos, generated art, or complex compositing.
4. Store project assets under `assets/readme/` unless the repository already has another convention.
5. Render and inspect every requested asset at wide and narrow GitHub widths.
6. Leave the README byte-for-byte unchanged unless the user separately approves embedding.

## Audit-only mode

1. Scan and inspect evidence without creating candidate README or asset files.
2. Use retrieval only as editorial comparison; never convert retrieved patterns into target claims.
3. Report hard findings and advisory observations separately.
4. Do not run visual generation, motion, PR bundle, publish gate, or remote writes.

## Optional hybrid composition

Hybrid output is opt-in, like motion. Use it only when an explicitly selected
raster subject communicates project identity better than repository-native
screenshots, outputs, logos, or deterministic SVG.

- Keep exact copy, typography, labels, alignment, and composition in an editable SVG layout.
- Keep the final subject PNG/WebP and generation prompt beside the layout.
- Publish the verified composed PNG/WebP, not an SVG with unresolved raster references or a large base64 layer.
- Preserve a project-owned static SVG fallback. If generation, transparency, composition, legibility, or size validation fails, leave the fallback and README unchanged.
- Never invoke ImageGen automatically in benchmark, audit, or CI paths.

## Optional motion

Animation is an output variant, not a third mode. Offer it only when motion explains a sequence, transition, state change, or relationship.

- Start from an approved static SVG.
- Require explicit opt-in before generating GIF.
- Keep the SVG and motion JSON as editable sources and the GIF as a derived artifact.
- Read [references/motion-production.md](references/motion-production.md) and use
  `"$README_SHOWCASE_SKILL/scripts/render_motion_gif.py"`.
- If dependencies, legibility, loop quality, or file-size limits fail, deliver the static SVG instead.

## Verify

- Run `python3 "$README_SHOWCASE_SKILL/scripts/audit_readme.py" /path/to/README.md`
  in README mode or after approved embedding.
- Verify claims, commands, releases, badges, links, anchors, local image paths, alt text, SVG basics, locale-matched text-bearing assets, language switches, and observable success steps.
- Audit every localized README separately. A primary-language Pass does not cover companion README assets.
- Inspect assets at approximately `900px` desktop width and `360px` mobile width.
- Report what changed, what stayed intentionally plain, what was not verified, and which files were deliberately left untouched.
- Show preview and diff before any publish action. Attribution is optional, requires an explicit request for a repository the user owns or maintains, and never changes delivery eligibility.

Pinned upstream classifications and reuse counts live in
[references/beautify-github-readme-delta.md](references/beautify-github-readme-delta.md).

## Quality bar

- The first screen explains what the project is, what it does for the visitor, and what to inspect next.
- Real proof appears before abstract claims.
- Removing the project name would not make the visual reusable for an unrelated repository.
- The README remains useful when images fail.
- Optional sections and visuals exist because evidence supports them, not because a reference README contains them.
- The result becomes clearer or shorter, not merely more decorated.
