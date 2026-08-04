<p align="center">
  <img src="./assets/readme/hero.gif" width="100%" alt="README Showcase turns repository evidence into a verified local GitHub homepage while remote publishing stays locked">
</p>
<p align="center"><sub><a href="./assets/readme/hero.svg">Static fallback</a> · Evidence-bound design · Local-first handoff</sub></p>

<p align="center"><strong>English</strong> · <a href="./README_zh.md">简体中文</a></p>

`readme-showcase` is a Codex Skill that redesigns a repository homepage from
the repository itself. One Agent scans evidence, retrieves licensed editorial
patterns, builds project-native copy and visuals, validates every claim and
asset, then stops at a fingerprinted local preview.

<p align="center">
  <a href="#install-in-60-seconds"><strong>Install</strong></a> ·
  <a href="#the-evidence-rail"><strong>Workflow</strong></a> ·
  <a href="#run-the-resumable-pipeline"><strong>Pipeline</strong></a> ·
  <a href="#the-remote-stays-locked"><strong>Safety</strong></a>
</p>

> [!IMPORTANT]
> A green evaluation authorizes a local preview, not a remote write. Commit,
> push, publishing, and pull-request actions require separate explicit approval.

## Proof at a glance

| Contract | Current repository fact | Why it matters |
| --- | --- | --- |
| Editorial retrieval | 22 reviewed records: 20 train, 2 isolated test | Test patterns cannot leak into production retrieval |
| Localization | 7 explicit locale tags | Each text-bearing README asset can stay paired with its language |
| Runtime | Python 3.11+; exact Node 22.22.3 for ELK | Reproducible local execution |
| Diagram engine | Vendored, hash-verified `elkjs@0.9.3` | No runtime download or hidden layout dependency |
| Handoff | Candidate receipt + local preview | Review happens before remote authority exists |

Patterns influence structure only. Repository evidence remains the sole source
of public product claims.

## The evidence rail

![Repository facts and licensed editorial patterns pass through one README Agent, claim and asset gates, and a fingerprinted local handoff](assets/readme/workflow.svg)

| Input | One-Agent work | Local output |
| --- | --- | --- |
| Tracked files, commands, config, tests | Story, copy, visual system, localization | `README.md` and localized counterparts |
| Licensed train-only patterns | Claim and asset binding | Editable SVG, optional derived GIF, manifests |
| Current base SHA | Hard-gate validation and evaluation | Fingerprinted candidate, report, offline preview |

ELK owns layout only. Project code owns serialization and verification; the
Skill owns claims, labels, visual direction, captions, and publish boundaries.

## Install in 60 seconds

Requirements: macOS or Linux, Python 3.11+, and Codex. Default flow has no
third-party Python dependency.

```bash
npx --yes github:Acfufu/readme-showcase
npx --yes github:Acfufu/readme-showcase --check
```

Success is observable:

```text
"status":"installed"
"status":"current"
```

Start a new Codex task so Skill discovery reloads, then invoke it:

```text
$readme-showcase Redesign this repository homepage around verified behavior and a runnable quick start. Use motion. Stop at local preview.
```

Choose `README`, `asset-only`, or `audit-only` scope. Motion and hybrid raster
composition remain explicit opt-ins. ELK is reserved for relationship-heavy
`architecture`, `flowchart`, and `c4` body diagrams.

## Run the resumable pipeline

The orchestrator records each deterministic stage and waits at explicit input
boundaries instead of inventing a candidate:

```bash
python3 skill/scripts/readme_pipeline.py run \
  --root . \
  --workspace ../readme-showcase-run \
  --mode readme \
  --project-type developer-tool \
  --locale en \
  --locale zh-Hans

python3 skill/scripts/readme_pipeline.py status \
  --workspace ../readme-showcase-run
python3 skill/scripts/readme_pipeline.py resume \
  --workspace ../readme-showcase-run
python3 skill/scripts/readme_pipeline.py preview \
  --workspace ../readme-showcase-run
```

The 13 CLI surfaces divide cleanly:

| Purpose | Commands |
| --- | --- |
| Orchestrate | `run` · `resume` · `status` · `explain` · `preview` |
| Build evidence | `validate-dataset` · `scan` · `retrieve` |
| Gate candidates | `validate-bundle` · `evaluate` |
| Benchmark and delivery | `import-benchmark` · `build-pr-bundle` · `check-publish-gate` |

The resulting handoff keeps `claim-map.json`, `asset-manifest.json`, editable
visual sources, evaluation output, and preview files beside the candidate.

<details>
<summary><strong>Deterministic visual routes</strong></summary>

<br>

- Static SVG is the fallback for every visual route.
- ELK accepts strict semantic JSON, renders twice in fresh processes, and only
  accepts byte-identical standalone SVG.
- Accepted ELK bytes are never post-edited; a mismatch preserves the last-known-good asset.
- GIF motion begins with an approved SVG. Motion JSON and SVG remain editable
  beside the derived GIF.
- Vendored ELK uses exact Node `22.22.3`; no `node_modules`, Docker, credentials,
  or runtime download is required.

</details>

## The remote stays locked

- Evidence owns factual claims; retrieval patterns never do.
- Commands, limitations, and changing information remain searchable Markdown.
- Text-bearing visuals receive locale-specific assets.
- Validation failure cannot silently degrade into publish eligibility.
- Passing evaluation cannot grant remote-write authority.
- Publishing requires an exact approval envelope, matching base SHA, and fresh
  remote preflight.

## Verify from source

```bash
python3 skill/scripts/readme_pipeline.py validate-dataset \
  --manifest dataset/retrieval/manifest.json
python3 skill/scripts/audit_readme.py README.md
python3 skill/scripts/audit_readme.py README_zh.md
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

Motion generation additionally needs Pillow, `ffmpeg`, and `rsvg-convert` or
macOS `sips`. ELK details live in
[`skill/references/elk-structure.md`](skill/references/elk-structure.md).

## Repository map

```text
skill/
├── SKILL.md                 # one-Agent workflow and scope gates
├── references/              # narrative, visual, motion, and ELK rules
├── scripts/                 # evidence, orchestration, audit, renderers
└── vendor/elkjs/            # pinned bundle, metadata, EPL-2.0 license
dataset/retrieval/manifest.json
scripts/install_skill.py     # atomic install, backup, rollback
package.json                 # npx entrypoint
tests/                       # contracts, gates, failure cases
```

## License and source boundaries

Released under [GNU General Public License v3.0](LICENSE). Visual and motion
guidance adapts MIT-licensed
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme);
notice: [`motion-production.md`](skill/references/motion-production.md#upstream-license).
Vendored `elkjs@0.9.3` remains under `EPL-2.0`; its unmodified
[license](skill/vendor/elkjs/LICENSE.md) ships with the Skill.
