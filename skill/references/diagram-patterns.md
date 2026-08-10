# Diagram patterns

Hand-authored static SVG recipes for README diagrams. Read
[visual-production.md](visual-production.md) first: it owns sizes,
accessibility, locale, and font rules. This file owns pattern choice and
anatomy only. Every route falls back to a hand-authored static SVG when an
engine cannot render (see SKILL.md `### Diagram route`).

Shared vocabulary note: pattern names (before/after delta, mass, cross-section,
call-graph collapse, swimlane, boxes-and-arrows) are shared with the
`improve-codebase-architecture` skill's review reports. The recipes below are
authored for GitHub README constraints (static, no JS, light/dark backgrounds,
per-locale files) and are original bytes — do not copy SVG code from other
sources into this file.

## Pattern choice

| Pattern | Use when | Do not use when |
| --- | --- | --- |
| Before/after delta | Ownership, data flow, or component responsibility changed | The change is a pure table comparison |
| Mass | Explaining why a module is deep (small interface, large implementation) | The module boundary is not the point |
| Cross-section | Showing how many layers a call passes through | The diagram is about entities, not call depth |
| Call-graph collapse | Showing that many scattered calls consolidate into one module | The call tree is genuinely flat |
| Swimlane flow | Ordered multi-party workflow, install, or request flow | One linear happy path — use Markdown steps |
| Boxes-and-arrows | Precise module layout where automatic layout would misplace things | Relationships are the point — use a flow instead |

## Common anatomy (every pattern)

- `1200`-unit `viewBox`; height `320–760` (Diagram range).
- `role="img"` + `aria-labelledby="title desc"`, one nonempty `<title>`, one
  `<desc>`; meaningful Markdown alt text.
- System fonts; essential text `20`, supporting labels `18`.
- Complete backgrounds for GitHub light and dark surroundings.
- Dashed strokes for seams (`stroke-dasharray="4 4"`); solid for real edges.
- Per-locale files (`diagram.svg` + `diagram-zh.svg`); language-neutral SVG
  marks `data-readme-language="neutral"` on the root element.
- The diagram is self-evident: caption and alt carry the claim, the picture
  never needs a paragraph to be understood (see structure.md section 4).

## 1. Before/after delta

**When:** changed ownership, data flow, or component responsibility
(structure.md section 4 rule).

**Anatomy:** two panels side by side at `1200 × 480–640`. Left panel labeled
`Before`, right labeled `After`. Identical frame and coordinate system in both
panels so the delta reads at a glance; moved or new elements highlighted with
the project accent, removed elements in muted outline.

**Layout:** two `<g>` groups (`g#before`, `g#after`) each `x=0`–`580` /
`x=620`–`1200`; divider line at `x=600` with `stroke-dasharray="4 4"`.

**Alt example:** `Installation flow before and after: two commands instead of
four, package manager step removed.`

## 2. Mass

**When:** the point is module depth — small interface, large implementation.

**Anatomy:** per module, two rectangles on a shared baseline: interface area
and implementation area. Shallow module: interface rectangle nearly as tall as
the implementation rectangle. Deep module: short interface rectangle, tall
implementation rectangle. Label each module; annotate the ratio only when the
numbers are claims (bind them to evidence).

**Layout:** one row per module inside a `1200 × 360–520` canvas; interface
rect `width="80"`, implementation rect `width="140"`, `gap="40"`.

**Alt example:** `Order intake module: one interface method over validation,
pricing, and persistence logic.`

## 3. Cross-section

**When:** showing how many layers a call passes through — thin layers each
doing little versus one thick band owning the responsibility.

**Anatomy:** stacked horizontal bands full canvas width. Thin layer band:
`height="28"` with a `border`-like stroke and the layer name inside. Thick
band: `height="160+"` with the consolidated responsibility label. Before
panel: N thin bands. After panel: one thick band. Keep both panels in one
`1200 × 520–760` SVG.

**Alt example:** `A request previously crossed six thin layers; now one
consolidated module handles parse, validate, enrich, and persist.`

## 4. Call-graph collapse

**When:** many scattered calls consolidate into one module.

**Anatomy:** before panel is a nested call tree (root module box containing
sub-call boxes, two levels deep, `g` nested inside `g`). After panel is one
thick-bordered module box with the former calls shown as small inner boxes at
reduced opacity (`opacity="0.4"`) — inner boxes are decorative, not clickable
or semantic. Same `1200 × 480–640` canvas for both panels.

**Alt example:** `Handler, parser, and mapper calls collapse into a single
order-intake module.`

## 5. Swimlane flow

**When:** ordered multi-party workflow, install flow, or request flow where
the actor changes lanes.

**Anatomy:** horizontal lanes per actor (`rect` background per lane, label at
lane left edge, `x=24`, vertical divider). Steps as rounded rects, edges as
solid lines; loop or optional steps use dashed edges. Lane count 2–4, step
count 4–8; beyond that, split into Markdown steps. Canvas `1200 × 520–760`.

**Alt example:** `Publish flow: author writes, bot verifies, maintainer
releases, user installs.`

## 6. Boxes-and-arrows

**When:** precise module layout where the exact position carries meaning and
automatic layout would misplace things.

**Anatomy:** modules as `rect` with `rx="12"`; arrows as `<path>` with a
`marker-end` triangle. Absolute coordinates authored by hand; keep a
`g`-per-module so coordinates stay maintainable. Seams (adapters) drawn with
`stroke-dasharray="4 4"`; leaks in the project's alert color. Limit to ~8
boxes and ~12 edges; beyond that, use a swimlane or split the diagram.

**Alt example:** `Client talks to one intake module; pricing and persistence
sit behind the same seam.`
