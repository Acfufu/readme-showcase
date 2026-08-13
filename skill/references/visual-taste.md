# README Visual Taste

Taste rules define *how good is good enough*. They never override evidence or
identity: the palette comes from repository tokens; taste introduces no new
colors. Every rule cites its source. Mechanical checkability is noted as
[M] where the screenshot gate can enforce it.

Sources: `design-taste-frontend` (DTF §n), `high-end-visual-design` (HEVD §n),
`Nutlope/hallmark` (HM), `clauswilke/dataviz` (WILKE),
`Wikipedia:Graphs and charts` (WPG), `W3C WAI Images tutorial` (WAI),
`GitHub Docs` (GHD).

## 0. Media adaptation statement

These rules are adapted from web-design skills to GitHub-safe SVG. Anything
requiring CSS layout, fonts, motion, or browser features does not transfer:
SVG here is static, system-font, no external resources, rendered inside an
`<img>` on github.com. Rules that assume interactivity are dropped; rules
about composition, hierarchy, density, and honesty transfer directly.

## 1. Anti-AI-slop ban list [M]

Banned signatures (DTF §0.D, §9.F; HM):

- Em-dash (U+2014) anywhere in visual text; use hyphen, comma, or line break
  (DTF §9.G). [M]
- Centered template hero: name centered + generic gradient behind it
  (DTF §4.3; HEVD §3). Use split, offset, or asymmetric composition.
- Three-equal feature rows (DTF §9.C). [M]
- Generic AI-purple/blue gradient backgrounds (DTF §4.2 LILA rule).
- Decoration text strips (`BRAND. MOTION. SPATIAL.`) at hero bottom (DTF §9.F).
- Numbered eyebrows (`00 / INDEX`, `01 · Capabilities`) (DTF §9.F). [M]
- Version labels in hero (`v0.6`, `BETA`) unless the project is a launch
  (DTF §9.F).
- Locale/city/weather strips in metadata (DTF §9.F).
- Fake-precise numbers (`92%`, `4.1×`) that no repository evidence supports
  (DTF §4.9; WPG). [M; evidence binding, see visual-production.md]
- Filler verbs (`Elevate`, `Seamless`, `Next-Gen`) (DTF §9.D).
- Badge walls: more than 3 shields in a row without grouping (GHD).
- Scroll cues, fake screenshots, hand-rolled decorative SVGs as decoration
  (DTF §4.8).

## 2. Typography hierarchy

- Three levels only: display (48+), section (40), supporting (18-20) at the
  1200-unit viewBox (visual-production.md size table; DTF §4.1).
- Distinguish levels by weight and tracking within the system font stack;
  never by adding a new font family (DTF §4.1 adaptation).
- Type-scale ratio: display/section/supporting ≈ 2.4 / 2 / 1; a supporting
  label smaller than 18 is nonessential (visual-production.md). [M]
- Italic descenders (`y g j p q`) need baseline clearance; clipped descenders
  fail the screenshot gate (DTF §4.1 italic rule; Plan 1 clipping check). [M]
- Emphasis uses italic or bold of the SAME family; never inject a serif word
  into a sans headline (DTF §4.1 emphasis rule).

## 3. Color discipline

- ONE accent from the repository palette, locked across all visuals
  (DTF §4.2 color consistency lock; visual-production.md identity gate).
- No pure `#000000` / `#ffffff`; use off-black / off-white (HEVD §8.B). [M]
- Text contrast ≥ 4.5:1 on light AND dark GitHub themes (DTF §4.5; WAI). [M]
- No color-only encoding; every state uses shape or text too (WAI).
- Colorblind-safe accent choices when the palette permits (WILKE ch. 3).

## 4. Shape consistency

- ONE radius system per visual set: all-sharp, all-soft, or documented mix
  (DTF §4.4 shape consistency lock). [M]
- Cards/panels only where elevation communicates hierarchy; prefer
  whitespace grouping (DTF §4.4).
- Strokes ≥ 1.5 at 1200-unit scale; hairlines below 1 render invisible
  (WPG SVG conventions). [M]

## 5. Composition and density

- 1200×360 hero carries at most 5 text elements (category, name, description,
  proof, metadata) (visual-production.md; DTF §4.7 hero stack discipline).
- Layout families do not repeat across hero/diagram/workflow scenes
  (DTF §4.7 section-layout-repetition ban). [M]
- Asymmetric-first: centered composition only for editorial/manifesto
  purposes (DTF §4.3; HEVD §3).
- Whitespace: each zone keeps ≥ 40 units of internal breathing room at the
  1200-unit scale (adapted from HEVD §4 macro-whitespace). [M]

## 6. Data-viz integrity

- Never truncate the y-axis or start it at a misleading offset (WILKE ch. 6;
  WPG accuracy rule). [M]
- Proportional ink: the area of a visual encoding matches the quantity it
  represents (WILKE ch. 6; Tufte via WPG).
- No 3D charts; pies only with ≤ 6 categories (WPG chart selection).
- Bars for comparison, lines for trends, scatter for correlation (WPG).
- Label axes, units, and data source on every chart (WPG).
- No fake precision: values shown must exist in repository evidence
  (DTF §4.9; visual-production.md evidence contract). [M]
- Colorblind-safe default palettes for series (WILKE ch. 3).
