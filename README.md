# README Showcase

Evidence-backed GitHub README design for Codex.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

`readme-showcase` turns verified repository behavior into a clear project
homepage. It keeps searchable content in Markdown, selects sections by project
type, and adds visuals only when they prove identity, output, sequence, or
architecture.

## What it does

- Audits repository evidence before drafting claims.
- Chooses README or asset-only scope explicitly.
- Organizes content as `Value → Proof → Mechanism → First use → Detail`.
- Supports project-native SVG and opt-in GIF workflows.
- Checks local images, alt text, and basic SVG compatibility.
- Keeps unsupported claims, decorative clutter, and unverified badges out.

## Workflow

| Stage | Result |
| --- | --- |
| Inspect | Audience, value, proof, first success, limits, and claim sources |
| Select | Project type, supported sections, and one editing mode |
| Draft | Evidence-backed Markdown with observable quick start |
| Visualize | Static project-native assets only when they improve comprehension |
| Verify | Claims, commands, links, anchors, images, SVG basics, and language parity |

Detailed guidance stays load-on-demand:

```text
skill/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── structure.md
│   ├── visual-production.md
│   └── motion-production.md
└── scripts/
    ├── audit_readme.py
    └── render_motion_gif.py
```

## Install

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

Expected result:

```text
Installed: .../skills/readme-showcase
```

Start a new Codex task so skill discovery reloads.

## Use

Invoke skill explicitly:

```text
$readme-showcase Redesign this repository README around verified behavior and a runnable quick start.
```

For one requested visual without README edits:

```text
$readme-showcase Create an asset-only workflow diagram from this repository's real architecture.
```

README mode may change README structure and justified assets. Asset-only mode
leaves README byte-for-byte unchanged unless embedding receives separate
approval. GIF generation also requires explicit opt-in.

## Verify

Audit any generated README:

```bash
python3 skill/scripts/audit_readme.py /path/to/project/README.md
```

Check bundled Python scripts:

```bash
python3 -m py_compile skill/scripts/audit_readme.py skill/scripts/render_motion_gif.py
```

`audit_readme.py` uses Python standard library only. Motion rendering additionally
requires Pillow, `ffmpeg`, and either `rsvg-convert` or macOS `sips`.

## Boundaries

- Repository evidence controls public claims and sections.
- Animation is optional output, never default mode.
- Commands and frequently changing facts remain Markdown, not images.
- Generated assets default to target repository's `assets/readme/` convention.
- Publishing, commits, pushes, and remote changes still require explicit approval.

## License

Project distributed under [GNU General Public License v3.0](LICENSE).

Bundled rendering and audit scripts include work adapted from
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme).
Upstream MIT attribution and license text remain in
[`skill/references/motion-production.md`](skill/references/motion-production.md#upstream-license).
