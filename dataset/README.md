# README Showcase Retrieval Dataset

Project-owned, retrieval-only pattern records. Records describe reusable README
structure and proof patterns; they do not copy third-party README text, code,
assets, or benchmark answers.

Each source pins a GitHub repository commit, source-material hash, reviewed SPDX
license, and commit-pinned license evidence. Reused source identities cannot
cross `train` and `test` splits.

Revision 2 contains 12 human-reviewed abstract records across four project
types: web frameworks, libraries, developer tools, and runtime/toolchains.
Ten records are production-retrieval `train` material; two are isolated `test`
records reserved for evaluation. Pattern text is newly authored for this
dataset. Source README text, code, badges, logos, assets, animation, and
benchmark answers are not stored.

Validate before use:

```bash
python3 skill/scripts/readme_pipeline.py validate-dataset \
  --manifest dataset/retrieval/manifest.json
```
