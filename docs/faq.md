# Frequently Asked Questions

## What does `readme-showcase` do?

It redesigns a GitHub repository homepage from repository evidence. One README
Agent scans the target repository, retrieves licensed editorial patterns,
authors project-native copy and visuals, checks every public byte, then stops
at a fingerprinted local preview.

## Does it publish anything to GitHub?

No. A passing evaluation authorizes local review only. Commit, push,
publication, and pull-request creation always require separate explicit
approval. Run `build-pr-bundle` to create only a fingerprinted local handoff.

## What counts as a valid claim in the resulting README?

Repository evidence is the only source of public target claims. If the target
repository does not demonstrate a behavior, the README does not claim it.

## What is the optional vision-LLM review?

The `--review` track of `screenshot-gate` runs an optional aesthetic review
against a vision model. It is configured through the `skill/.env` file: set
the `VISION_REVIEW_API_KEY`, `VISION_REVIEW_MODEL`, and
`VISION_REVIEW_API_BASE` keys there, or leave them unset to fall back to the
host-session review. Process environment variables win over file values.

## Where do the review settings live?

`.env` lives in exactly one place: `skill/.env`, next to
`skill/.env.example`. Only `VISION_REVIEW_`-prefixed keys are read from (or
written to) the file. Restrict permissions with `chmod 600 skill/.env` when
the file contains secrets.

## Where does run state go?

Run state stays outside the target repository, under
`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`. No per-run virtual
environment and no target-adjacent state directory are created.

## What happens when images fail to load?

The README remains useful: commands, prerequisites, limitations, links, and
changing facts stay searchable Markdown.

## Which platforms are supported?

Codex is officially installed and verified in project or user scope. Claude
Code recognizes the Skill under `.claude/skills` (audit-only runtime
acceptance passed); OpenCode recognizes the project install under
`.agents/skills`. The current installer targets the Codex paths.

## How do I report an issue?

Open a GitHub issue at
<https://github.com/Acfufu/readme-showcase/issues> for hard-gate failures,
rule suggestions, or override decisions.
