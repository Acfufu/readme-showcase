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
boundaries instead of inventing a candidate. By default, run state is kept
under `${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`, keyed by the target
repository, so the repository and its parent stay clean:

```bash
python3 skill/scripts/readme_pipeline.py run \
  --root . \
  --mode readme \
  --project-type developer-tool \
  --locale en \
  --locale zh-Hans

python3 skill/scripts/readme_pipeline.py status
python3 skill/scripts/readme_pipeline.py resume
python3 skill/scripts/readme_pipeline.py preview
```

`resume`, `status`, `explain`, and `preview` find the latest run for the current
repository. Normal output hides the internal path; `--verbosity debug` exposes
it for troubleshooting. `--workspace /absolute/path` remains an explicit expert
override.

Central state is durable recovery data, not scratch space. The orchestrator
creates no per-run virtual environment and removes preview temporary files
before returning; immutable run attempts remain until an operator applies a
separate retention policy. When Plan v3 opts into `diagram_route: "compiled"`,
its Stage 6 outputs stay in the immutable
`stages/06-bundle-assemble/attempts/<attempt>/compiled/` directory inside that
central workspace, outside the target repository.

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
- Compiled diagrams are an opt-in Plan v3 route (`diagram_route: "compiled"`).
  The existing `none`, `static`, and `elk` routes, eight-stage order, and one
  README Agent remain compatible; see [`visual-compiler.md`](skill/references/visual-compiler.md).
- Compiled local previews emit deterministic static SVG and independent
  `desktop`/`mobile` projections. Desktop uses a 1,200-wide viewBox checked at
  900 px; mobile is planned independently at most 720 wide and checked at
  360 px.
- `preview`, `build-pr-bundle`, and delivery `--dry-run` are local-only. They
  do not push, open a pull request, publish, or call an external provider;
  remote writes still require separate explicit approval.

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
