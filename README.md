<p align="center">
  <img src="./assets/readme/hero.gif" width="100%" alt="README Showcase moves repository evidence through one README Agent into a verified local candidate while remote publishing stays locked">
</p>
<p align="center"><sub><a href="./assets/readme/hero.svg">Static fallback</a> · Repository evidence in · Reviewable local candidate out</sub></p>

<p align="center"><strong>English</strong> · <a href="./README_zh.md">简体中文</a></p>

`readme-showcase` is a Codex Skill for redesigning a GitHub repository homepage
without inventing product truth. It scans the target repository, uses licensed
editorial patterns for structure, writes project-native copy and visuals, checks
claims and assets, then stops at a fingerprinted local preview.

> [!IMPORTANT]
> Passing evaluation authorizes a local handoff only. Commit, push, publishing,
> and pull-request actions still require separate explicit approval.

## Make the first minute useful

| Repository gives | One README Agent does | You review locally |
| --- | --- | --- |
| Tracked files, commands, configuration, tests | Evidence map, story order, copy, visual direction | README candidates and editable assets |
| Current base SHA | Claim and locale binding, hard-gate evaluation | Offline preview and evaluation report |
| Licensed train-only patterns | Editorial comparison—not product facts | Fingerprinted PR bundle, still unpublished |

The README stays useful when images fail: commands, limitations, links, and
changing facts remain searchable Markdown.

## Install, check, invoke

Requirements: macOS or Linux, Python 3.11+, and Codex. The default flow has no
third-party Python dependency.

```bash
npx --yes github:Acfufu/readme-showcase
npx --yes github:Acfufu/readme-showcase --check
```

Success is observable as `"status":"installed"` followed by
`"status":"current"`. Start a new Codex task so Skill discovery reloads, then
ask for the scope and stop point you want:

```text
$readme-showcase Redesign this repository homepage around verified behavior and a runnable quick start. Use motion. Stop at local preview.
```

Choose `README`, `asset-only`, or `audit-only` mode. Motion and hybrid raster
composition are explicit opt-ins.

## From evidence to a locked handoff

![Editorial patterns provide structure while repository facts provide truth; one README Agent evaluates a verified local bundle before any separately approved remote publish](assets/readme/workflow.svg)

```text
scan → retrieve train-only patterns → plan → draft → validate → evaluate → preview
```

- Repository evidence is the only source of public product claims.
- Retrieval patterns can influence structure, never target facts.
- Text-bearing visuals are paired to explicit locales.
- A failed gate cannot silently become publish eligibility.
- ELK may lay out relationship-heavy body diagrams; it never owns copy,
  visual direction, acceptance, or publishing.

## Proof carried by this repository

| Contract | Current evidence |
| --- | --- |
| Retrieval boundary | 22 reviewed records: 20 production `train`, 2 isolated `test` |
| Localization contract | 7 allowed locale tags with explicit README and asset pairing |
| Runtime contract | Python 3.11+; exact Node 22.22.3 for the optional ELK route |
| Diagram integrity | Vendored, hash-verified `elkjs@0.9.3`; no runtime download |
| Delivery boundary | Candidate receipt, local preview, evaluation report, fingerprinted PR bundle |

## Run the resumable pipeline

Run state lives outside the target repository under
`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`, keyed by target repository.
The target and its parent stay free of temporary run directories.

```bash
python3.11 skill/scripts/readme_pipeline.py run \
  --root . \
  --mode readme \
  --project-type developer-tool \
  --locale en \
  --locale zh-Hans

python3.11 skill/scripts/readme_pipeline.py status
python3.11 skill/scripts/readme_pipeline.py resume
python3.11 skill/scripts/readme_pipeline.py preview
```

The orchestrator records eight ordered stages and waits for an explicit plan or
candidate instead of fabricating one. `status`, `resume`, `explain`, and
`preview` locate the latest run for the current repository. It reuses the
existing runtime and does not create a per-run virtual environment.

<details>
<summary><strong>Visual routes and retained sources</strong></summary>

<br>

| Route | Use it for | Retained source |
| --- | --- | --- |
| `none` | Markdown already explains the project | Markdown |
| `static` | Project-specific heroes and compact diagrams | Editable SVG |
| `elk` | Relationship-heavy architecture, flowchart, or C4 bodies | Semantic JSON + verified SVG |
| `compiled` | Opt-in Plan v3 desktop/mobile projections | Visual Spec + immutable Stage 6 outputs |

The deterministic Plan v3 route sets `diagram_route: "compiled"` inside the
existing eight-stage, one README Agent pipeline. It keeps immutable outputs in
`stages/06-bundle-assemble/attempts/<attempt>/compiled/`: desktop uses a
1,200-wide viewBox checked at 900 px, while mobile is planned independently at
no more than 720 wide and checked at 360 px. These local-only outputs and
delivery `dry-run` never grant remote authority; see
[`visual-compiler.md`](skill/references/visual-compiler.md).

GIF motion starts from an approved SVG. The SVG and motion JSON stay beside the
derived GIF. Compiled outputs remain in central run state; `preview`,
`build-pr-bundle`, and delivery `--dry-run` remain local-only.

</details>

## Verify from source

```bash
python3.11 skill/scripts/readme_pipeline.py validate-dataset --manifest dataset/retrieval/manifest.json
python3.11 skill/scripts/audit_readme.py README.md
python3.11 skill/scripts/audit_readme.py README_zh.md
python3.11 -m unittest discover -s tests -v
npm pack --dry-run
```

Motion generation additionally needs Pillow, `ffmpeg`, and `rsvg-convert` or
macOS `sips`. Optional ELK details live in
[`skill/references/elk-structure.md`](skill/references/elk-structure.md).

## Repository map

```text
skill/
├── SKILL.md                 # scope, evidence, and approval gates
├── references/              # narrative, visual, motion, compiler, ELK
├── scripts/                 # scan, orchestration, audit, renderers
└── vendor/elkjs/            # pinned bundle and EPL-2.0 license
dataset/retrieval/manifest.json
scripts/install_skill.py     # atomic install, backup, rollback
package.json                 # npx entrypoint
tests/                       # contracts, gates, failure paths
```

## License and source boundaries

Released under [GNU General Public License v3.0](LICENSE). Visual and motion
guidance adapts MIT-licensed
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme);
notice: [`motion-production.md`](skill/references/motion-production.md#upstream-license).
Vendored `elkjs@0.9.3` remains under `EPL-2.0`; its unmodified
[license](skill/vendor/elkjs/LICENSE.md) ships with the Skill.
