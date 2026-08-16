# Stage 8 · evaluation

Stage role: evaluate the hard gates and revise only surfaced findings; after
Pass, check the exact publish state only after explicit approval and fresh
read-only remote preflight.

Evaluate the hard gates:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" evaluate \
  --bundle "$RUN/generated-readme-bundle.json" \
  --output "$RUN/evaluation-report.json"
```

Only after explicit approval and fresh read-only remote preflight, check the
exact publish state:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" check-publish-gate \
  --pr-bundle "$RUN/pr-bundle.json" \
  --remote-state "$RUN/remote-state.json" \
  --approval "$RUN/approval-envelope.json" \
  --output "$RUN/publish-gate.json"
```

Contract:

- Never publish from evaluation success alone. GitHub branch, commit, push,
  and PR writes require separate explicit approval bound to current
  fingerprint, target, branch, and base SHA.
- A passing evaluation authorizes local review only; the local fingerprinted
  handoff comes from [bundle-assemble.md](bundle-assemble.md).
