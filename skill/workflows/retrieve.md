# Stage 2 · retrieve

Stage role: validate the licensed retrieval manifest, then retrieve up to five
train-only patterns for evidence-bound query dimensions. Retrieval patterns
are not target facts; every factual claim still requires current repository
evidence.

Validate the licensed retrieval patterns first:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" validate-dataset \
  --manifest "$README_SHOWCASE_SKILL/dataset/retrieval/manifest.json"
```

Then retrieve up to five train-only patterns for evidence-bound query
dimensions:

```bash
python3 "$README_SHOWCASE_SKILL/scripts/readme_pipeline.py" retrieve \
  --evidence "$RUN/repository-evidence.json" \
  --manifest "$README_SHOWCASE_SKILL/dataset/retrieval/manifest.json" \
  --project-type developer-tool \
  --section overview \
  --section quick-start \
  --tag workflow \
  --mode production \
  --output "$RUN/retrieval-packet.json"
```

Contract:

- The retrieval manifest keeps its exact contract: `purpose: "retrieval-only"`
  and 22 records (20 production `train`, 2 isolated `test`). Twenty production
  `train` patterns may guide structure; two isolated `test` patterns never
  enter production retrieval.
- Retrieval exemplars form a separate, non-manifest category under
  `dataset/retrieval/exemplars/`: 10 curated records (8 `train`, 2 `test`)
  plus 4 synthetic train-only breakdowns, all provenance-bound and
  license-screened. They merge at the ranker input and never enter
  `manifest["records"]`.

Next: [plan-import.md](plan-import.md).
