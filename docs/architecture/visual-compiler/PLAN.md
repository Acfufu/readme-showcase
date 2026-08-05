# Visual Compiler Core Implementation Plan

Status: implementation continuation and acceptance index.

Baseline: `dev@e77c1c3b5c0a841317b2a3974f031403224b6332`. The detailed historical
execution plan lived under local `.omo/` state; this tracked file keeps the
portable sequence without copying that 794-line ledger.

## Delivery sequence

1. Freeze baseline, worktree, default routes, legacy hashes, and clean-room
   boundaries.
2. Add Visual Spec, immutable model, diagnostics, normalization, graph planning,
   variants, and theme tokens.
3. Add bounded ELK geometry, Scene, deterministic SVG, Timeline, Interaction,
   security/geometry/semantic gates, inventory, and atomic promotion.
4. Add the opt-in Plan/Claim/Manifest/Bundle/Evaluation/PR contract chain and
   connect Stage 5 author import to Stage 6 compilation without changing the
   eight-stage registry.
5. Bind validation, preview, approval, and dry-run delivery to the complete
   compiled artifact identity while retaining legacy behavior.
6. Publish the human-readable current/target flows and clean-room evidence.
7. Repair any completion-audit finding at its shared trust boundary with one
   adversarial regression before rerunning acceptance.

## Verification order

Run narrow security regressions first, then owning suites, then:

```text
Python 3.11 full suite
Python 3.12 full suite
Python 3.13 full suite
schema parity and Node syntax/pinned ELK identity
npm package list and isolated install/check
fresh deterministic SVG rerender and desktop/mobile raster inspection
independent F1 plan, F2 code/security, F3 manual QA, F4 scope review
```

Every final claim must name the tested commit/tree, runtime version, command,
result, and evidence path. A failed earlier gate blocks later approval.

## Commit and publication boundary

Keep implementation/tests together in exact-scope local commits; keep portable
architecture documents in a separate local commit. Preserve unrelated `.omo/`
state and the dirty recovery worktree. Do not merge `dev`, push, open a PR,
publish, or delete recovery material without separate user authorization.
