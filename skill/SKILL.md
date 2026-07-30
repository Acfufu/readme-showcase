---
name: readme-showcase
description: Create, redesign, audit, or visually upgrade evidence-backed GitHub README homepages for apps, extensions, CLIs, services, libraries, and developer tools. Use for whole-README information architecture, project-native heroes and diagrams, README-only visual assets, GitHub-safe SVG or opt-in GIF animation, multilingual README variants, install/use/configuration flows, and removal of unsupported claims or decorative clutter.
---

# README Showcase

Turn verified repository behavior into a clear project homepage. Keep Markdown as the searchable content layer and use visuals only when they communicate identity, proof, comparison, sequence, or architecture.

## Choose the scope

Use exactly one editing mode:

- **README mode** — improve the README's reading order, copy, proof, Markdown, and any justified visual system.
- **Asset-only mode** — create only the requested hero, section header, workflow, diagram, badge, or coordinated asset set. Do not edit, reorder, or embed anything in the README without separate approval.

If the request is ambiguous, inspect read-only context and ask whether the user wants the whole README or assets only. A read-only audit does not require choosing an editing mode.

## Inspect before writing

1. Read the current README, repository tree, manifests, entry points, releases, install metadata, configuration, docs, tests, license/security files, screenshots, logos, design tokens, and real outputs.
2. Record the audience, one-sentence value, primary proof, first successful action, differentiator, limitations, and unsupported claims.
3. Preserve unrelated changes. Do not commit, push, publish, or modify a remote repository without explicit authorization.

Read [references/structure.md](references/structure.md) for evidence mapping, narrative selection, project-type sections, localization, and content validation.

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
- Read [references/motion-production.md](references/motion-production.md) and use `scripts/render_motion_gif.py`.
- If dependencies, legibility, loop quality, or file-size limits fail, deliver the static SVG instead.

## Verify

- Run `python3 scripts/audit_readme.py /path/to/README.md` in README mode or after approved embedding.
- Verify claims, commands, releases, badges, links, anchors, local image paths, alt text, SVG basics, language switches, and observable success steps.
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
