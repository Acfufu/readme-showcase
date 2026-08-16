# Stage 3 · plan-import

Stage role: write `readme-plan.json` directly from target evidence and import
the plan into the orchestrator. The orchestrator waits for an explicit plan
instead of inventing one.

Write the plan, candidate files, `claim-map.json`, and `asset-manifest.json`
directly from target evidence. The v2 multilingual locale contract lives in
[SKILL.md](../SKILL.md) "One README Agent pipeline": use an explicit ordered
`locales` array of `{tag, readme_path}` mappings, keep allowed tags to exactly
`en`, `zh-Hans`, `zh-Hant`, `ja`, `ko`, `fr`, and `de`, and never infer
locales from filenames, directories, or suffixes.

If the plan selects the `elk` route, invoke the optional adapter once; its two
fresh runs are validation, not hidden retries:

```bash
node "$README_SHOWCASE_SKILL/scripts/render_elk.mjs" \
  --input "$RUN/diagram.diagram.json" \
  --output "$RUN/diagram.svg" \
  --metadata "$RUN/diagram.engine.json"
```

Route selection rules live in the Diagram route table in
[SKILL.md](../SKILL.md). Read
[references/elk-structure.md](../references/elk-structure.md) before any ELK
route.

Next: [generation-request.md](generation-request.md).
