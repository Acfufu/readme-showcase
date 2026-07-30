<p align="center">
  <img src="./assets/readme/hero.gif" width="100%" alt="README Showcase turns repository facts into clear GitHub homepages">
</p>

<p align="center">
  <strong>English</strong> · <a href="./README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-1d1b17" alt="GPL-3.0 license"></a>
  <img src="https://img.shields.io/badge/runtime-Codex-c54a36" alt="Built for Codex">
  <img src="https://img.shields.io/badge/dependencies-stdlib_only-686257" alt="Audit script uses Python standard library only">
</p>

`readme-showcase` is a Codex skill for redesigning GitHub README homepages from
verified repository behavior. Markdown carries searchable facts; visuals earn
their place by proving identity, output, sequence, or architecture.

## Evidence before decoration

![Five review desks move repository evidence through inspect, select, draft, visualize, and verify](assets/readme/workflow.svg)

The skill follows one reading order:

> **Value → Proof → Mechanism → First use → Detail**

| Desk | Question it must answer |
| --- | --- |
| Inspect | Who is this for, what works, and where are the limits? |
| Select | Which project type, sections, and editing mode fit the evidence? |
| Draft | What is the shortest truthful route from value to first success? |
| Visualize | Does an SVG, screenshot, or opt-in GIF improve understanding? |
| Verify | Do claims, commands, links, assets, and language variants hold up? |

Unsupported claims are removed. Decorative visuals stay out. Publishing always
requires separate approval.

## Install and run

```bash
git clone https://github.com/Acfufu/readme-showcase.git
cd readme-showcase

skill_target="${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase"
if [ -e "$skill_target" ]; then
  printf 'Target already exists: %s\n' "$skill_target"
else
  cp -R skill "$skill_target"
  printf 'Installed: %s\n' "$skill_target"
fi
```

Expected install result:

```text
Installed: .../skills/readme-showcase
```

Start a new Codex task so skill discovery reloads, then invoke:

```text
$readme-showcase Redesign this repository README around verified behavior and a runnable quick start.
```

First observable behavior: skill inspects repository evidence, then selects
README mode or asks whether scope is whole-README versus asset-only when unclear.

## Two modes, one boundary

| Mode | Changes | Use it for |
| --- | --- | --- |
| README | Copy, reading order, proof, Markdown, justified visuals | A complete repository homepage |
| Asset-only | Requested visual files only | A hero, workflow, badge, diagram, or coordinated set |

README mode may change README structure and justified assets. Asset-only mode
leaves README byte-for-byte unchanged unless embedding receives separate approval.
GIF generation requires explicit opt-in in either mode.

Example asset-only request:

```text
$readme-showcase Create a static workflow SVG from this repository's real architecture. Do not edit the README.
```

## What ships

```text
skill/
├── SKILL.md                  # workflow and scope gates
├── agents/openai.yaml        # Codex discovery metadata
├── references/
│   ├── structure.md          # evidence map and narrative choices
│   ├── visual-production.md  # GitHub-safe visual rules
│   └── motion-production.md  # opt-in GIF workflow
└── scripts/
    ├── audit_readme.py       # README and SVG checks
    └── render_motion_gif.py  # approved motion renderer
```

## Verify a result

Audit any generated README:

```bash
python3 skill/scripts/audit_readme.py /path/to/project/README.md
```

Check bundled Python scripts:

```bash
python3 -m py_compile skill/scripts/audit_readme.py skill/scripts/render_motion_gif.py
```

`audit_readme.py` uses Python standard library only. Motion rendering additionally
needs Pillow, `ffmpeg`, and either `rsvg-convert` or macOS `sips`.

## Boundaries

- Repository evidence controls claims and sections.
- Commands and changing facts remain copyable Markdown.
- Static SVG is default for deterministic visuals; GIF is opt-in.
- Generated assets use target repository's `assets/readme/` convention.
- Commits, pushes, publishing, and remote changes need explicit approval.

## License

Project distributed under [GNU General Public License v3.0](LICENSE).

Bundled rendering and audit scripts include work adapted from
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme).
Upstream MIT attribution and license text remain in
[`skill/references/motion-production.md`](skill/references/motion-production.md#upstream-license).
