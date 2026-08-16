# Getting Started

`readme-showcase` is a Codex Skill that redesigns a GitHub repository homepage
from repository evidence—not invented product truth. This page covers
requirements, installation, first run, and where the optional configuration
file lives.

## Requirements

- macOS or Linux
- Python 3.11+
- Codex

Default operation adds no third-party Python runtime dependency.

## Install

### Option 1 · CLI

```bash
# English install check
npx --yes github:Acfufu/readme-showcase skills install
npx --yes github:Acfufu/readme-showcase skills check
```

Interactive installation detects an existing scope. Automation can choose one
explicitly:

```bash
# English explicit scopes
npx --yes github:Acfufu/readme-showcase skills install --project --yes
npx --yes github:Acfufu/readme-showcase skills install --user --yes
```

Project scope writes `.agents/skills/readme-showcase`; user scope writes
`${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase`. Observable success is
`"status":"installed"` followed by `"status":"current"`. Use the same scope
with `skills update` to refresh an existing installation. Legacy no-argument
install and `--check` invocations remain supported.

### Option 2 · Hand it to an Agent

Send this exact request to your coding Agent:

```text
Please install this Skill: https://github.com/Acfufu/readme-showcase
```

The Agent should confirm scope, run the official installer and `skills check`,
then report the installed path and status.

## First run

Start a new Codex task so Skill discovery reloads, then run:

```text
$readme-showcase shape [target]
```

`shape` maps evidence, narrative, scope, and visual direction. It waits for
approval and creates no candidate files. The other commands—`audit`,
`redesign`, `polish`, and `visualize`—are described in the
[README](../README.md).

## The `.env` file

`.env` lives in exactly one place: `skill/.env`, next to
`skill/.env.example`. It is auto-created there with mode `0600` on first use,
and process environment variables always win over values read from the file.

If you create or edit the file by hand, restrict permissions—it may hold
secrets:

```bash
chmod 600 skill/.env
```

Only keys prefixed `VISION_REVIEW_` are read from (or written to) the `.env`
file. The `.env` file is never published; `.env.example` is the committed
template.

## Next steps

- [FAQ](faq.md)
- [Roadmap](roadmap.md)
- [Project positioning](project-positioning.md)
