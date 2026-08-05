# Visual Compiler Core Specification

Status: normative for the opt-in `diagram_route: "compiled"` path.

This file is the portable contract index. The implemented flow and absorption
matrix remain in [ARCHSCRIBE_ABSORPTION_ANALYSIS.md](ARCHSCRIBE_ABSORPTION_ANALYSIS.md);
the clean-room source boundary remains in
[visual-kernel-clean-room.md](../../../skill/references/visual-kernel-clean-room.md).

## Scope

The compiler MUST:

- preserve the existing eight stages and run only inside Stage 6
  (`bundle-assemble`) after Stage 5 imports author-owned README, Claim Map v3,
  and Visual Spec v1 bytes;
- activate only through canonical Plan v3 with `diagram_route: "compiled"`;
- keep semantic validation, Evidence lineage, variants, Scene, SVG, gates,
  derived state, fingerprints, and promotion inside the project-owned
  `readme_showcase.visual_kernel` package;
- use vendored ELK only for bounded geometry;
- emit independent desktop/mobile Scene, SVG, Gate, Timeline, and Interaction
  artifacts plus one canonical inventory, Asset Manifest v3, and Generated
  Bundle v3;
- fail closed without replacing the last-known-good attempt.

It MUST NOT add a ninth stage, change legacy defaults, import Archscribe code or
assets, add a browser/runtime dependency, auto-render motion, or perform a
remote write.

## Trust and determinism

- Visual Spec input is closed, canonical, float-free, Evidence-bound, limited
  to 256 KiB, and bounded by shared JSON depth/node limits before copying or
  recursive library calls.
- Adapter and vendored ELK bytes are verified, copied into an isolated execution
  snapshot, and executed from that snapshot. Geometry and metadata are bounded,
  canonical, identity-checked, and revalidated by the Python boundary.
- Generated paths are relative, no-follow, bounded, and inventory-complete.
  SVG rejects active content, external references, unsafe XML, traversal,
  special files, and output-parent replacement races.
- Approval Envelope v2 re-reads and fully validates the original Generated
  Bundle v3 and requires its hash to equal the immutable bundle hash already
  recorded in PR Bundle v2's passing evaluation. Rebuilding trust from a
  changed manifest is forbidden.
- The explicit legacy motion renderer reads JSON/SVG inputs without following
  links, bounds structure, bytes, dimensions, elements, and frame work before
  rendering, gives renderer/ffmpeg subprocesses finite timeouts, and replaces a
  prior GIF only after a bounded encoded result is complete.

Repeated compilation of the same canonical inputs and pinned identities MUST
produce identical canonical data and SVG bytes.

## Compatibility and authority

Plan v1/v2 and `none`, `static`, and `elk` routes retain their prior producers,
readers, bytes, and delivery gates. Compiled internal JSON stays local; only
README and compiled SVG candidates enter PR Bundle v2. Preview, approval, PR
projection, and `deliver --transport gh --dry-run` are local handoff surfaces.
Push, PR creation, merge, release, live providers, and cleanup require separate
authorization.

## Acceptance

Acceptance requires all of the following on one recorded commit/tree:

1. adversarial regressions for approval drift, mutable adapter execution,
   structural input exhaustion, and motion resource exhaustion;
2. supported Python 3.11/3.12/3.13 suites, schema parity, real pinned ELK,
   deterministic rerender, and package/install checks;
3. fresh desktop/mobile and current/target diagram raster inspection;
4. independent plan, code/security, manual-QA, and scope-fidelity approval.

Historical receipts do not approve a later tree.
