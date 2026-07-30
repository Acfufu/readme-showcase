# Project-Native README Visuals

Read this reference before creating or revising README assets. Visuals must communicate project identity, proof, comparison, sequence, or architecture; decoration alone is not a reason to add them.

## Contents

1. [Derive the visual system](#derive-the-visual-system)
2. [Choose the opening](#choose-the-opening)
3. [Use GitHub-safe assets](#use-github-safe-assets)
4. [Choose a structure implementation](#choose-a-structure-implementation)
5. [Produce SVG](#produce-svg)
6. [Preview and validate](#preview-and-validate)

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
├── hero.gif
├── hero-motion.json
├── workflow.svg
├── showcase.webp
└── section-*.svg
```

Do not add unused variants or generic templates.

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

## Preview and Validate

Render every asset and inspect:

- approximately `900px` desktop and `360px` mobile widths;
- clipped text, paths, and screenshots;
- missing `viewBox`, `<title>`, `<desc>`, or alt text;
- weak contrast on GitHub light and dark pages;
- generic motifs unrelated to the project;
- unreadable proof or excessive density;
- accidental remote resources or sanitizer-sensitive SVG features.

When two versions communicate equally well, keep the simpler one.

For a user-requested attribution mark, derive one compact signature from the
project's existing visual system and preview it before embedding. Never add an
unsolicited backlink or make attribution a condition of delivery.
