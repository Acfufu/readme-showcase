# Visual Compiler Core Decisions

These decisions resolve the open questions in the original Archscribe handoff.

| ID | Decision |
| --- | --- |
| D1 | `compiled` is opt-in Plan v3 behavior; it does not replace legacy routes by default. |
| D2 | JSON Schema Draft 2020-12 covers structural parity; repository Python validators own semantic, lineage, path, and byte checks. |
| D3 | Project-owned Scene is the geometry truth and project-owned SVG is the canonical renderer. ELK supplies bounded relationship geometry only. |
| D4 | PNG is a QA raster, not a compiled delivery contract. A production PNG path remains deferred. |
| D5 | Excalidraw compatibility is not promised. Any editable-output format needs a later contract and acceptance run. |
| D6 | Themes come from project-owned deterministic tokens with independent desktop/mobile policies; no Archscribe brand assets are absorbed. |
| D7 | Evidence lineage is explicit in Visual Spec elements, Scene primitives, claims, manifests, gates, evaluation, and approval fingerprints; unsupported claims fail rather than being visually embellished. |
| D8 | Desktop and mobile are independently planned artifacts. README candidates may reference both, but one is never a scaled substitute for the other. |
| D9 | Archscribe is a pinned behavior-only reference. No source, comments, symbols, fixtures, runtime, fonts, icons, screenshots, or encoded assets are copied. |
| D10 | The runner remains exactly eight stages; Stage 5 owns author import and Stage 6 alone owns compilation and promotion. |
| D11 | Attempts are immutable, last-known-good promotion is atomic, and retention is manual. |
| D12 | Delivery remains local dry-run until separate remote-write authority is granted. |
| D13 | Approval authority is bound to the evaluated complete Generated Bundle v3, not a manifest reconstructed from current bytes. |
| D14 | The ELK adapter executes from a verified isolated snapshot so a changed live path cannot run before identity rejection. |
| D15 | Caller-controlled Visual Spec and motion inputs have explicit structural/byte/work budgets, and motion subprocesses have finite timeouts. |

Rejected and deferred scope is shown visually in
[ARCHSCRIBE_ABSORPTION_FLOW.svg](ARCHSCRIBE_ABSORPTION_FLOW.svg) and explained in
[ARCHSCRIBE_ABSORPTION_ANALYSIS.md](ARCHSCRIBE_ABSORPTION_ANALYSIS.md).
