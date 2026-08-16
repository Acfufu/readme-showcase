# Stage 6 · bundle-assemble

Stage role: assemble `generated-readme-bundle.json` from the candidate and,
after validation and evaluation pass, build the local fingerprinted handoff
only. Assembly and `build-pr-bundle` never push or publish.

Assembly consumes the candidate outputs from [candidate.md](candidate.md); the
resulting `generated-readme-bundle.json` is validated by
[validation.md](validation.md). For Plan v3 `diagram_route: "compiled"`,
`bundle-assemble` compiles independent desktop/mobile Scene, SVG, gate,
timeline, interaction, and fingerprint outputs under its immutable Stage 6
attempt at `stages/06-bundle-assemble/attempts/<attempt>/compiled/`; it owns
Asset Manifest v3 and Generated Bundle v3. See
[references/visual-compiler.md](../references/visual-compiler.md).

After Pass, build the local fingerprinted handoff only:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" build-pr-bundle \
  --bundle "$RUN/generated-readme-bundle.json" \
  --evaluation "$RUN/evaluation-report.json" \
  --output "$RUN/pr-bundle.json"
```

Next: [validation.md](validation.md).
