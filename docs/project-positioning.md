# Project Positioning

`readme-showcase` is a Codex Skill that redesigns a GitHub repository homepage
from repository evidence—not invented product truth.

## The problem

Repository homepages drift. The README claims what the code no longer does, or
repeats generic marketing language that any project could publish. Rewriting
it by hand repeats the same guesswork.

## What this project does

One README Agent scans the target repository, retrieves licensed editorial
patterns, authors project-native copy and visuals, checks every public byte,
then stops at a fingerprinted local preview.

- Repository evidence is the only source of public target claims.
- Editorial patterns shape structure; target evidence stays the source of
  truth.
- Candidate assets bind to evidence, locale, exact bytes, and useful alt
  text.
- Failed gates cannot silently become a publishable result.

## Boundaries

- A passing evaluation authorizes local review only.
- Commit, push, publication, and pull-request creation always require separate
  explicit approval.
- Default operation adds no third-party Python runtime dependency.
- Run state lives outside the target repository under
  `${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`; no per-run virtual
  environment is created.

## Who it is for

Maintainers who want a README that reflects the repository's actual behavior,
with a reviewable local preview before anything reaches a remote.

## Related pages

- [Getting started](getting-started.md)
- [FAQ](faq.md)
- [Roadmap](roadmap.md)
