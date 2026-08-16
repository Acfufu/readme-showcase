# Stage 7 · validation

Stage role: validate the assembled bundle against its schema and hard gates.
Failed gates cannot silently become a publishable result.

Validate the assembled bundle:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" validate-bundle \
  --bundle "$RUN/generated-readme-bundle.json"
```

Contract:

- Validation runs only on the bundle assembled by
  [bundle-assemble.md](bundle-assemble.md).
- Candidate assets bind to evidence, locale, exact bytes, and useful alt text.
- Gate failures stop the run; the next stage revises only surfaced findings.

Next: [evaluation.md](evaluation.md).
