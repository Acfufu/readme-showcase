# Project-Native README Visuals

Read this reference before creating or revising README assets. Visuals must communicate project identity, proof, comparison, sequence, or architecture; decoration alone is not a reason to add them.

## Contents

1. [Derive the visual system](#derive-the-visual-system)
2. [Find the logo, do not invent it](#find-the-logo-do-not-invent-it)
3. [Explainable identity gate](#explainable-identity-gate)
4. [Choose the opening](#choose-the-opening)
5. [Use GitHub-safe assets](#use-github-safe-assets)
6. [Choose a structure implementation](#choose-a-structure-implementation)
7. [Produce SVG](#produce-svg)
8. [Preview and validate](#preview-and-validate)
9. [Demo recording contract](#demo-recording-contract)

## Derive the Visual System

Write five project facts first:

```text
Project category:
Main user:
Main action:
Best proof:
Native visual material:
```

Choose art direction in this order:

1. Real product semantics.
2. Existing logo, UI tokens, screenshots, diagrams, code style, and documentation tone.
3. Audience expectations such as technical trust, creative energy, research clarity, or operational confidence.
4. Palette, typography, shapes, motif, density, and composition.

Freeze a compact system:

```text
Palette: background / foreground / primary / accent / muted
Typography: system stack / display / section / supporting scale
Shape: radius / stroke / spacing unit
Motif: one recurring project-specific cue
Density: sparse editorial / compact technical / expressive gallery
Composition: split / integrated / artifact wall / background proof / title-only
```

Do not apply the same template to every repository. A CLI may use command rhythm and cursor marks; a data project may use measured charts and labels; an Agent Skill may use its real invocation, state flow, and stop gates.

## Find the Logo, Do Not Invent It

Never draw a fictional mark for a project that already ships one. A made-up
logo presents invented product truth and passes off the Skill's work as the
project's identity. The real incident: repo-visuals once shipped a fake purple
"V lightning" logo for VeloxDB instead of the project's actual logo at
`<homepage>/logo-dark.svg`.

Follow the Logo Protocol search order, in this exact sequence, and stop at the
first hit:

1. README and tracked repo files, including Markdown image links, favicons,
   and existing `assets/`.
2. Project-owned `assets/` and branding directories, whatever their layout.
3. The project website or brand page referenced by the repository.
4. Public documentation that shows an official mark.

Prefer vector sources (SVG) over raster ones, and never re-draw the found mark
from memory. When the search genuinely finds no official logo, do not invent
one: use a typographic treatment of the project name built from the derived
visual system instead. Record where each logo search step was checked so the
absence is an audited fact, not an omission.

## Explainable Identity Gate

The scan stage derives the repository's visual identity tokens from its own
materials — SVG assets (logos, favicons, diagrams) and design-token files
(`theme.json`, `tokens.json`, `variables.css`, and similar) — and stores them
as `visual` evidence facts (`palette` and `typography` token lists). The
evaluation stage then compares the candidate's SVG tokens against those
product tokens and fails the hard gate on any conflict:

- **Palette**: every non-grayscale candidate color must already exist in the
  repository palette. Black, white, and pure grays are scaffolding and never
  conflict.
- **Typography**: every candidate font family must already exist in the
  repository typography or be a system font stack the contract already allows.

A conflict is reported with the product values versus the chosen value, for
example "candidate color `#4fd1c5` is not part of the repository identity
palette (product palette: `#0a1f44`, `#22c55e`)".

The gate covers every candidate SVG asset — hand-authored assets and
compiled-route diagram projections alike. Compiled outputs are renderer
outputs of the resolved Theme, so their palette must still match the
repository identity (or stay neutral/system); there is no compiled-route
exemption.

When the repository has no visual materials, the gate fails closed: with an
empty product token set, every non-neutral, non-system candidate token
conflicts, so the evaluation fails and requires human attention. Candidates
restricted to neutral grayscale colors and system font families still pass.
When the candidate genuinely must deviate — a deliberate rebrand with a
written reason — an approved override (`--identity-override-reason` and
`--identity-override-approved-by` on `evaluate`) converts the conflict into a
pass and records `identity_override` (reason and approver) on the evaluation
report. Publishing-side validation of that override belongs to the publish
gate, not to this reference.

## Choose the Opening

Use one of three openings:

- **Markdown hero** — for intentionally minimal projects or repositories without honest visual proof.
- **One-board hero** — combine identity and a few legible outputs when the outputs remain understandable at README width.
- **Title followed by proof** — use a concise title asset followed immediately by a larger screenshot, workflow, or showcase when proof would become too dense inside the hero.

A useful opening can contain:

1. Category or technical context.
2. Repository name.
3. One concrete description.
4. Real project material.
5. Small verified metadata.

Before accepting it, ask:

1. If the repository name disappeared, could this belong to an unrelated project?
2. Does the visual explain the project or only look technical?
3. Can a first-time visitor understand the project without reading the whole body?
4. Does the typography fit the project's character and audience?
5. Is the proof still legible at GitHub content width?

If the first answer is yes or the second is decoration, redesign or use Markdown instead.

## Use GitHub-Safe Assets

Reliable README building blocks include Markdown, tables, links, code blocks, `<details>`, and local images. Use HTML only for simple alignment and sizing.

Use:

- SVG for deterministic titles, section headers, diagrams, badges, and vector proof.
- PNG/WebP for screenshots, photos, generated artwork, and complex compositing.
- GIF only for explicitly approved meaningful motion; keep a static SVG source and fallback.

Avoid relying on:

- `<script>` or `foreignObject`
- remote fonts, stylesheets, or images inside SVG
- essential hover states or SVG animation
- fragile filters and large shadows

Store project assets under the repository's established convention or:

```text
assets/readme/
├── hero.svg
├── hero-zh.svg
├── hero.gif
├── hero-motion.json
├── workflow.svg
├── workflow-zh.svg
├── showcase.webp
└── section-*.svg
```

Do not add unused variants or generic templates.

For multilingual README work, inventory visuals beside content before drawing.
Generate each text-bearing SVG once per requested locale and use a locale suffix
or directory. A shared asset is allowed only when its visible text is genuinely
language-neutral; declare that on the SVG root with
`data-readme-language="neutral"`. Renaming an untranslated asset does not count
as localization.

## Asset Replacement Contract

GitHub serves README images through its `camo` proxy, which caches by URL for
roughly a year by default; `PURGE` requests are unreliable. The only dependable
cache-bust is changing the URL itself.

Therefore, every time a published README asset changes, ship it under a new
filename or a new query parameter — never in place:

- `hero.svg` → `hero-2026-08.svg` (new filename)
- `hero.svg?v=2` (new query parameter)

Keep the retained editable source current so the next revision starts from
truth, and keep the old file only while any published README still references
it. Check the final README URLs with `audit_readme.py` before handoff; an
in-place replacement of a previously published asset is a contract violation.

## Choose a Structure Implementation

Hand-author compact SVGs, title systems, and diagrams whose project-specific
composition matters more than automatic layout.

An optional structured engine may place only relationship-heavy body diagrams
where grouping, edge routing, and label wrapping dominate the work. Keep strict
project-owned semantic source beside its exported asset. Engine output must be
static, self-contained, system-font-based, palette-bound, and GitHub-safe.

The Skill still owns project title and title bar, palette choice, factual
claims, surrounding composition, alt/caption, visual acceptance, fallback, and
publishing. Engine failure must leave the current README and static fallback
byte-for-byte unchanged. Never make an engine a default dependency merely to
draw a few boxes.

## Produce SVG

Use a `1200`-unit-wide `viewBox` for full-width modules. Starting heights:

```text
Hero:          1200 × 300–420
Section title: 1200 × 120–170
Diagram:       1200 × 320–760
```

At a conservative `900px` desktop render:

| Role | Minimum SVG size | Approximate rendered size |
| --- | ---: | ---: |
| Hero/project title | `48` | `36px` |
| Section title | `40` | `30px` |
| Essential diagram text | `20` | `15px` |
| Supporting label | `18` | `13.5px` |

Keep smaller text nonessential. At `360px` mobile width, move required detail into adjacent Markdown if it cannot remain legible.

Start from this accessible skeleton:

```svg
<svg xmlns="http://www.w3.org/2000/svg"
     width="1200" height="360" viewBox="0 0 1200 360"
     role="img" aria-labelledby="title desc">
  <title id="title">Repository name</title>
  <desc id="desc">Plain-language purpose of the visual.</desc>
  <rect width="1200" height="360" rx="26" fill="#050607"/>
  <g id="title-block"><!-- identity --></g>
  <g id="project-proof"><!-- real project material --></g>
</svg>
```

Build in this order:

1. Background and major structure.
2. Repository name and concrete description.
3. Real project material.
4. Verified metadata.
5. Only the decoration still needed.

Use system fonts. Name groups by role, keep coordinates maintainable, supply complete backgrounds for light/dark surroundings, and use meaningful Markdown alt text. Never hide commands or critical instructions inside images.

For diagram-shaped assets, choose a pattern from
[diagram-patterns.md](diagram-patterns.md) before authoring coordinates.

## Preview and Validate

Render every asset and inspect:

- approximately `900px` desktop and `360px` mobile widths;
- clipped text, paths, and screenshots;
- missing `viewBox`, `<title>`, `<desc>`, or alt text;
- weak contrast on GitHub light and dark pages;
- generic motifs unrelated to the project;
- unreadable proof or excessive density;
- accidental remote resources or CSP-sensitive SVG features (script, external references, `foreignObject`).
- matching language between each localized README and every text-bearing SVG.

Run `audit_readme.py` once per README variant. Its `E_SVG_LOCALE` hard gate
rejects an unlocalized text-bearing SVG, a conflicting language marker, and a
Chinese SVG with no visible Chinese text.

When two versions communicate equally well, keep the simpler one.

For a user-requested attribution mark, derive one compact signature from the
project's existing visual system and preview it before embedding. Never add an
unsolicited backlink or make attribution a condition of delivery.

## Demo Recording Contract

Runtime-captured demos (role-`demo` assets, see
[motion-production.md](motion-production.md#demo-recording)) are terminal
recordings of an archived script, so the capture itself is part of the
deliverable. Every capture must pass the content review gate
(`skill/scripts/record_demo.py` `review_demo_capture`; Task 4.4) before the
artifacts are returned or declared in a manifest. The gate enforces two rules:

**No environment leakage.** The output the capture shows must not expose the
recording host: no absolute user paths (`/Users/<name>/…`, `/home/<name>/…`),
no `~` or `$HOME` home references, no email addresses or `user@host`
identities, and no tokens, API keys, or secrets. The leakage check always runs
and reports `leak-path`, `leak-identity`, or `leak-credential` findings.

**No fabrication.** Every command the capture shows must exist in the
repository's own capability: command names declared by `cli-entrypoint`
evidence facts (`python-script:<name>`, `node-bin:<name>`) or the leading
tokens of verified `command-observation` facts in the repository-evidence
graph. Generic shell utilities (`echo`, `ls`, `git`, `python3`, `node`, …) are
always allowed; any other claimed command is reported as `fabrication` with
the evidence set it was checked against. Pass the repository-evidence graph
with `--evidence` to enable the capability check.

So that a capture is reviewable, the archived script must echo each command as
a `$ command` prompt line before running it — the cast then shows exactly what
was executed. Never fabricate output or hand-edit a cast; a `.cast` input is
used as recorded.

Path conventions (Task 4.1): the script archive lives at
`demo/<name>.sh`, `demo/<name>.txt`, or `demo/<name>.cast`; outputs are
locale-scoped assets `assets/readme-showcase/<locale>/<name>.cast` and
`assets/readme-showcase/<locale>/<name>.gif`, with `<name>-demo.svg` reserved
for the retained static source variant. The script archive must exist in the
repository before manifest validation: validation byte-checks `demo_script_ref`
against the archived file, so the producer materializes the archive (recording
does this) before declaring the asset. A failed review raises
`DemoReviewError` and the CLI exits 2.
