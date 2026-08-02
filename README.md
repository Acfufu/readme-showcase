<p align="center">
  <img src="./assets/readme/hero.gif" width="100%" alt="README Showcase maps repository evidence into a verified, reviewable GitHub homepage">
</p>
<p align="center"><sub><a href="./assets/readme/hero.svg">Static SVG</a> · Evidence-bound README design · Local by default</sub></p>

<p align="center">
  <strong>English</strong> · <a href="./README_zh.md">简体中文</a>
</p>

`readme-showcase` is a Codex Skill for repository maintainers. It reads the
current codebase, chooses a narrative that fits the project, creates only
evidence-backed copy and visuals, verifies the result, and stops at a local
fingerprinted handoff.

<p align="center">
  <a href="#quick-start"><strong>Quick start</strong></a> ·
  <a href="#from-evidence-to-handoff"><strong>Workflow</strong></a> ·
  <a href="#choose-a-mode"><strong>Modes</strong></a> ·
  <a href="#safety-boundary"><strong>Safety</strong></a>
</p>

> [!IMPORTANT]
> A passing evaluation authorizes a local preview only. Commits, pushes,
> publishing, and pull requests always require separate explicit approval.

## From evidence to handoff

![Repository facts and licensed editorial patterns pass through one README Agent, claim and asset gates, and a fingerprinted local handoff](assets/readme/workflow.svg)

_Glyphic-generated raw SVG. The Skill owns every claim, label, palette choice,
caption, acceptance decision, and publish boundary._

| What you need | What the Skill does | What you receive |
| --- | --- | --- |
| A homepage grounded in the real project | Scans repository evidence before drafting | README copy with traceable claims |
| A visual system that belongs to the project | Derives story, palette, typography, and composition from repository semantics | Editable static sources and optional GitHub-safe GIFs |
| A safe review boundary | Audits links, commands, assets, localization, and hard gates | Evaluation report and fingerprinted local bundle |

The retrieval dataset contains 12 licensed, human-reviewed abstract patterns:
10 are available to production retrieval and two remain isolated test records.
Patterns guide editorial structure only; they never become facts about the
target repository.

## Quick start

Requirements: macOS or Linux, Python 3.10+, and Codex. The default path has no
third-party Python dependency.

```bash
npx --yes github:Acfufu/readme-showcase
npx --yes github:Acfufu/readme-showcase --check
```

Observable success:

```text
"status":"installed"
"status":"current"
```

After the first npm release, shorten `github:Acfufu/readme-showcase` to
`readme-showcase`. Start a new Codex task so Skill discovery reloads, then run:

```text
$readme-showcase Redesign this repository README around verified behavior and a runnable quick start.
```

The first visible action is repository inspection. If scope is unclear, the
Skill asks whether the job is a whole README, assets only, or an audit.

## Choose a mode

| Mode | Changes | Best for |
| --- | --- | --- |
| README | Reading order, copy, proof, Markdown, and justified visuals | A complete GitHub homepage |
| Asset-only | Requested hero, diagram, badge, or coordinated asset set | Visual work without changing README content |
| Audit-only | Findings and evidence only | Truth, safety, localization, and publish-readiness review |

Motion and hybrid raster composition are explicit opt-ins, not separate modes.
Glyphic is optional and limited to relationship-heavy `architecture`,
`flowchart`, or `c4` body diagrams.

## How the local pipeline works

1. **Validate** the pinned retrieval manifest and its license evidence.
2. **Scan** the target repository into deterministic evidence facts.
3. **Retrieve** up to five train-only editorial patterns.
4. **Draft** the README, claim map, asset manifest, and any justified visuals.
5. **Evaluate** claims, links, commands, assets, accessibility, and localization.
6. **Handoff** a fingerprinted local bundle after every hard gate passes.

The installed Skill includes eight deterministic pipeline commands:

```text
validate-dataset  scan  retrieve  validate-bundle  evaluate
import-benchmark  build-pr-bundle  check-publish-gate
```

<details>
<summary><strong>Optional Glyphic and motion boundaries</strong></summary>

<br>

- Glyphic runs from a caller-supplied, hash-locked Node 22 tree with
  `@glyphicjs/core@1.3.1`.
- The adapter accepts strict semantic JSON, renders twice in fresh processes,
  and accepts only byte-identical, standalone, GitHub-safe SVG.
- Raw Glyphic SVG is never post-edited. Any mismatch selects the project-owned
  static fallback and leaves the last-known-good asset unchanged.
- GIF motion starts from an approved static SVG. The editable SVG and motion
  JSON remain beside the derived GIF.
- Glyphic packages, engine locks, `node_modules`, credentials, and generated
  benchmark answers are never installed into the Skill tree.

</details>

## Safety boundary

- Repository evidence owns public claims.
- Retrieved patterns are editorial references, never target facts.
- Commands, configuration, limitations, and changing information stay in
  searchable Markdown.
- Text-bearing visuals are localized per README language.
- Static SVG is the deterministic fallback for every visual route.
- Evaluation success never grants remote-write authority.
- An exact approval envelope, current base SHA, and fresh remote preflight are
  required before any publish connector action can become eligible.

## Verify locally

```bash
python3 skill/scripts/readme_pipeline.py validate-dataset \
  --manifest dataset/retrieval/manifest.json
python3 skill/scripts/audit_readme.py README.md
python3 skill/scripts/audit_readme.py README_zh.md
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

Motion generation additionally needs Pillow, `ffmpeg`, and either
`rsvg-convert` or macOS `sips`. Verified Glyphic rendering requires Node 22 and
the exact external engine lock documented in
[`skill/references/glyphic-structure.md`](skill/references/glyphic-structure.md).

## Repository map

```text
skill/
├── SKILL.md                 # one-Agent workflow and scope gates
├── agents/openai.yaml       # Codex discovery metadata
├── references/              # narrative, visual, motion, and Glyphic rules
└── scripts/                 # scan, retrieval, evaluation, audit, renderers
dataset/retrieval/manifest.json
scripts/install_skill.py     # atomic installer and upgrade rollback
package.json                 # npx package entrypoint
tests/                       # deterministic contracts and failure cases
```

## License and source boundaries

This project is distributed under the
[GNU General Public License v3.0](LICENSE).

The visual and motion workflow adapts MIT-licensed guidance from
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme);
the retained notice lives in
[`skill/references/motion-production.md`](skill/references/motion-production.md#upstream-license).
Optional Glyphic remains external under `FSL-1.1-ALv2` and is not distributed
with this repository.
