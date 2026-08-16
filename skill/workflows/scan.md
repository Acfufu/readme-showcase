# Stage 1 · scan

Stage role: scan the target repository into one evidence file. Repository
evidence is the only source of public target claims — no retrieved pattern,
visual engine, or evaluator supplies facts.

Run after the target repository is chosen and the run workspace is known:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" scan \
  --root "$TARGET" \
  --output "$RUN/repository-evidence.json"
```

Contract:

- `$TARGET` is the target repository root; `$RUN` and
  `README_SHOWCASE_SKILL` follow the shared conventions in
  [_index.md](_index.md).
- The scan output `repository-evidence.json` is the input to the next stage.
- The run workspace stays outside the target under
  `${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`; never create
  `.readme-showcase-run-*` or another run directory in or beside the target
  repository.

Next: [retrieve.md](retrieve.md).
