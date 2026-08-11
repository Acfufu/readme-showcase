# Novice Guided Interview (`guide` flow)

Use this reference for a bare invocation whose request shows novice signals.
The interview turns a plain-language request into a canonical README Plan by
asking a fixed question set, one question at a time, with defaults. It is an
input layer only: it never bypasses evidence binding, evaluation gates,
preview, or publication approval, and it never writes candidate files.

The machine-readable catalog for the fixed question set lives in
[`schemas/interview.v1.schema.json`](../schemas/interview.v1.schema.json)
under `x-question-catalog`; the tables in this reference mirror it and must
stay byte-consistent in ids, option keys, labels, defaults, and plan mappings.

## 1. Activation

Enter the guided interview only when: bare invocation AND at least one novice
signal (no command vocabulary in the request; a plain-language ask such as
"帮我做"/"怎么做"; no explicit scope or target named).

Precedence is strict, evaluated in this order — the interview never swallows a
command:

1. **Explicit command** (`shape`, `audit`, `redesign`, `polish`, `visualize`,
   `status`, `resume`, `preview`) → route per
   [commands.md](commands.md). No interview.
2. **Clearly implied command** (`commands.md` routing rule 2, e.g. "review
   this README" → `audit`; "plan first" → `shape`) → route. No interview.
3. **Bare invocation without novice signals** → `commands.md` routing rule 4:
   inspect, recommend two or three concrete commands with targets, wait.
4. **Bare invocation with novice signals** → enter this interview.

## 2. Interaction contract

- At most two panels; one topic per panel; one question per row.
- Plain language only: no jargon in prompts, option labels, or descriptions.
- Every question has a default. Skipping, dismissing, or not answering =
  the default answer.
- Each option label is 1–5 words; each description is one line stating the
  consequence.
- Never auto-run anything after the interview. End with a plan summary and
  stop for explicit approval.

## 3. Pre-fill inspection

Before the first panel, inspect the target and pre-fill detected facts into
question defaults:

- README existence and language (e.g. `README.md` en, `README_zh.md`
  zh-Hans) → `locales` default; guarantee at least one locale
  (`[{"tag":"en","readme_path":"README.md"}]` when nothing is detected).
- Logo, design tokens, screenshots, CLI outputs, diagrams present in the
  repository → `proof` and `style` defaults.
- Dirty tree and latest run state → surface in the plan summary, never hide.
- Repository scans (stage 0 of the pipeline) produce `repository-evidence.json`
  with normative evidence ids; the written plan binds those ids, never
  placeholders.

## 4. Fixed question set

Exactly six questions: `goal`, `scope`, `audience`, `proof`, `style`,
`extras`. The canonical catalog (ids, prompts, option keys, labels, defaults,
routes) is:

| id | prompt (EN) | options (key: label → route / plan effect) | default |
| --- | --- | --- | --- |
| goal | What do you want? | 1 全新改版 (full redesign) → `redesign` (mode `readme`) · 2 只改局部 (refine one area) → `polish` (mode `readme`) · 3 只要视觉图 (visuals only) → `visualize` (mode `asset-only`) · 4 先诊断问题 (diagnose first) → `audit` (mode `audit-only`) · 5 不确定 (not sure) → `audit-then-recommend` (mode `audit-only`, then recommend) | 5 |
| scope | Whole README or visuals only? | 1 整个 README (whole README) · 2 只做视觉资产 (visual assets only) · 3 由你判断 (you decide) → resolves/overrides mode when compatible | 3 |
| audience | Who reads this? | 1 终端用户 (end users) · 2 开发者 (developers) · 3 贡献者 (contributors) · 4 混合 (mixed) → `project_type` (pipeline flag only, never a plan field) + narrative priority → `sections` ordering / `visual_intent` wording | 4 |
| proof | Best real showcase? | 1 截图 (screenshots) · 2 CLI/终端输出 (CLI/terminal output) · 3 示例代码 (example code) · 4 都没有 (none) → `visual_intent` (hero selection: raster/screenshot-led vs SVG vs Markdown-only) | 4 |
| style | Visual style? | 1 跟随现有品牌 (follow existing brand) · 2 极简 (minimal) · 3 技术暗色 (dark technical) · 4 活泼 (expressive) · 5 你推荐 (you recommend) → `visual_intent` | 5 |
| extras | Extras? (multi-select) | 1 GIF 动图 (animated GIF) → motion intent via `commands`/`visual_intent`, never `diagram_route` · 2 多语言 (localization) → `locales` from pre-fill + selection · 3 无 (none) → no extras | 3 |

Rendering rule: `prompt` is the EN canonical prompt; `label_zh` carries the
Chinese button text; agents render per the user's language. Multi-select
serialization: multiple `extras` keys are recorded as comma-joined digits in
`choice` (e.g. `"1,2"`), within the 1–64 character limit.

## 5. Panel batching

- **Panel 1 (always):** `goal` + `scope`.
- **Panel 2 (conditional):** `audience` + `proof` + `style` + `extras` — only
  when Panel 1 routes to `redesign`, `polish`, or `visualize`. Never for
  `audit` / `audit-then-recommend` (audience, proof, and style have no job in
  audit-only mode).

## 6. Question UI specification

- **Native question tool (REQUIRED when available):** OpenCode and OpenChamber
  expose the `question` tool, which renders a panel with selectable buttons and
  a custom-answer input. Use one tool call per panel. `header` ≤30 characters
  (the question id); option `label` 1–5 words; option `description` = one-line
  consequence; `multiple: true` for `extras`. Do NOT add "Other"/catch-all
  options — the tool adds a custom-answer input automatically (`custom` is
  enabled by default).
- **Fallback (Claude Code, Codex, pi, and other harnesses):** numbered text
  Q&A, one question at a time, same options and defaults. The fallback loses
  no functionality — only the presentation surface changes.

## 7. Free-text answers

When the user types a custom answer instead of picking a key:

1. Nearest-option match → map to that option, confirm once in chat, record the
   option key with `confidence: "custom"`.
2. Genuinely new intent → map to the nearest plan effect, confirm once in chat,
   record the raw text in `choice` with `confidence: "custom"`.
3. Free text never becomes a claim about the repository; it only shapes the
   plan.

## 8. Answer → Plan routing table

Map answers to EXACTLY the 8 readme-plan v2/v3 fields — `mode`, `commands`,
`sections`, `visual_intent`, `diagram_route`, `locales`, `evidence_ids`,
`schema_version` — constrained by the repo contracts:

- `additionalProperties: false` plus the semantic `E_SCHEMA_UNKNOWN_FIELD`
  check forbid any other field. `project_type` is a pipeline CLI flag
  (`--project-type`), never a plan field; it is recorded as a
  pipeline-invocation parameter. Asset-only specifics (asset type, coordinated
  set) belong to asset-manifest v3, not the plan.
- `evidence_ids` is NEVER empty in a written plan: v2/v3 require `minItems: 1`
  and each id must match the normative form `[a-z]+:[0-9a-f]{64}`
  (`E_CLAIM_EVIDENCE`). At answer time ids are "not yet bound"; the pipeline
  runs `scan` (stage 0) before plan-import (stage 2), so the plan is written
  with evidence ids taken from the scan output (`repository-evidence.json`).
  The routing table maps answer → intent only; binding to ids happens at
  plan-write time.
- `locales` requires `minItems: 1` regardless of mode — pre-fill guarantees at
  least one (default en + `README.md` when no variant is detected).
- `diagram_route` is enum-only: `none|static|elk` (v2) or
  `none|static|elk|compiled` (v3). Motion (GIF) is NOT a `diagram_route`
  value; it is recorded via `commands`/`visual_intent` and the motion spec.
- The routed `mode` must match the pipeline invocation mode
  (`readme_pipeline.py run --mode`), otherwise `E_BUNDLE_PLAN` at plan-import.
- Red-line constraints (红线) expressed by the user are recorded in chat only
  and NEVER emitted into the plan — no plan field exists for them.

Unanswered defaults are recorded with `confidence: "defaulted"`.

## 9. All-skip default path

Every question defaulted (or the user stops answering) → route to
`audit-then-recommend`: run audit-only mode, present findings plus two or
three concrete command recommendations, then STOP for approval. Never
auto-run a recommendation.

## 10. Guardrails

- The interview produces a plan only — no candidate files, no edits, no
  commits, no publish actions.
- Evidence binding, evaluation gates, preview, and publication approval all
  apply unchanged after the interview.
- Free text never becomes a repository claim.
- The interview never runs on explicit or implied commands (section 1).

## 11. Failure modes

- User stops answering → record defaults, present the plan summary, stop.
- Question tool unavailable or errors → text fallback (section 6).
- Schema validation of answers fails → re-ask the failing question once, then
  fall back to defaults for it.
- Pipeline invocation conflicts with the routed mode → stop with a
  diagnostic; do not guess.
