# Visual kernel clean-room boundary

This note is normative for the project-owned `readme_showcase.visual_kernel`
package. It records observed behavior only. It is not a source, fixture, or
asset import plan.

## Pinned references

- `readme-showcase`: commit `e77c1c3b5c0a841317b2a3974f031403224b6332`
  (`e77c1c3`), tree `43e558ba9840d252bdb924e0d14f7bbd493e26ec`
  (`43e558b`).
- Archscribe behavior reference: commit
  [`46ea42cfc6c557ab238867c390bb18320fd36769`](https://github.com/lazypay/Archscribe/tree/46ea42cfc6c557ab238867c390bb18320fd36769).

The Archscribe commit is a read-only behavior reference. This document records
the pin and source locations without reproducing external license text.

## Behavior-only included

The kernel may independently implement these observed behaviors, using its own
contracts, names, constants, fixtures, and serializers:

- renderer-neutral visual planning and immutable scene semantics;
- graph cycle handling, rank/layer planning, stable ordering, and routed edges;
- swimlane sizing, lane assignment, and explicit loop channels;
- field-level diagnostics for invalid input, ignored data, and capacity limits;
- deterministic text-fit decisions for bounded labels;
- serializable drawing operations, timeline state, and interaction state;
- repository-owned evidence binding, variant policy, gates, and deterministic
  output, with existing vendored ELK retained only as the bounded geometry
  backend.

Behavior references, reviewed at the pinned commit:

| Behavior | Reference location |
| --- | --- |
| plan/scene shape and graph behavior | `graph_model.py:4-19,318-446,468-844` |
| text fit, operations, and structured diagnostics | `render_animated_diagram.py:330-421,531-589,1539-1583,2218-2441` |
| interaction and timeline state | `svg_renderer.py:2151-2293,2364-2669` |

These are reading pointers, not code or symbol names to reproduce.

## Excluded payloads and dependencies

Kernel runtime and package contents must not include:

- Archscribe imports, subprocesses, CLI/runtime packages, source trees, or
  copied comments, symbols, constants, or renderer code;
- rough.js, Chromium/browser requirements, or other external renderer runtime;
- bundled fonts, icon libraries, brand themes, screenshots, or packaged assets;
- copied fixture bytes, golden images, encoded outputs, or fixed panorama
  coordinates;
- custom local-asset paths, Excalidraw/PNG/codec implementations, or a second
  general graph-layout engine;
- a `skill/scripts/readme_showcase/visual_kernel/vendor/` tree.

The kernel owns semantics, validation, scene data, serialization, and gates.
`skill/vendor/elkjs` remains an existing project dependency used only through a
bounded geometry adapter; it is outside the kernel package boundary.

## No-copy fixture rule

Every kernel fixture must be authored from the readme-showcase specification.
Use independent IDs, labels, coordinates, colors, and expected bytes. Fixtures
may compare invariants and deterministic behavior, but must not copy
Archscribe source, comments, symbols, constants, fixture bytes, screenshots,
fonts, icons, themes, or other assets. A behavior comparison can cite the
pinned reference; it must not make that repository a runtime or test-data
dependency.

## Machine boundary

Runtime scanning is scoped to `skill/scripts/readme_showcase/visual_kernel/`.
`tests/test_documentation_contract.py` rejects forbidden Archscribe, rough.js,
font, or icon imports and rejects a child `vendor/` tree. The scanner is also
exercised against temporary mutation fixtures so a forbidden import or vendor
payload produces a failing assertion without modifying repository files.
