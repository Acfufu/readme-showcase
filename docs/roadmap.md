# Roadmap

This page records where `readme-showcase` is today and the directions under
consideration. Items listed as planned are intent, not completed work; no
claim here goes beyond what the repository currently demonstrates.

## Current state

- Eight-stage handoff:
  `scan → retrieve → plan-import → generation-request → candidate →
  bundle-assemble → validation → evaluation → local preview`.
- Five commands—`shape`, `audit`, `redesign`, `polish`, and `visualize`—routing
  into the existing `README`, `asset-only`, and `audit-only` modes.
- Bilingual English / Simplified Chinese READMEs and asset pairs, with one
  README Agent evaluating a verified local bundle.
- Every public claim is bound to repository evidence; candidate assets bind to
  evidence, locale, exact bytes, and useful alt text.
- A passing evaluation authorizes local review only. Commit, push,
  publication, and pull-request creation each require separate explicit
  approval.
- Run state lives outside the target repository under
  `${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`; no per-run virtual
  environment or target-adjacent state directory is created.
- The installer is atomic: validation, lock, staging, hashes, backup,
  replacement, and rollback, with exact-byte verification.

## Under consideration

These are candidate directions. None is committed, and none changes the
approval gates:

- User documentation under `docs/` with the same en / zh-Hans pairing as the
  READMEs (this hierarchy).
- Deeper coverage of the motion and compiled visual routes, each retaining its
  editable evidence-bound source.
- More editorial patterns in the retrieval dataset, always license-screened
  and provenance-bound.
- Tighter content assertions so documentation and README claims stay
  verifiable as the repository changes.

## What we do not claim

- No automatic remote publication; publishing stays behind explicit approval.
- No browser, live, or production validation is asserted—only the audit-only
  runtime acceptance and the local hard gates in this repository.
- No change to the eight-stage pipeline; the two isolated `test` patterns
  never enter production retrieval.
