# tests/fixtures/contracts/ — golden contract fixtures

Valid/invalid JSON pairs proving the pipeline's wire contracts. Naming:
`<contract>-v<N>.{valid,invalid}.json`, matched to the same-version Draft 2020-12
schema in `skill/schemas/`.

## index.json is the registry

Each entry binds three things: `schema` (skill/schemas/…), `python_validator`
(`skill.scripts.readme_showcase.contracts.…`), and the valid/invalid fixture pair,
plus adapter kind and producer tag. `tests/test_schema_parity.py` reads this
registry and drives schema-vs-validator-vs-fixture parity from it. A fixture pair
without an index entry is dead; an index entry without both fixtures fails CI.

## Editing rules

- A subset of fixtures is **hash-pinned** in `tests/test_schema_parity.py`
  (readme-plan-v1/v2, claim-map-v2, asset-manifest-v2 pairs). Changing pinned
  bytes requires updating the pinned sha256 in the same commit.
- `.invalid.json` files are independent documents that must fail for a
  *documented* reason — many carry `cases[].code` with the expected error code
  (e.g. `E_SCHEMA_UNKNOWN_FIELD`) that contract tests assert on.
- New or changed contract version = schema in `skill/schemas/` + pair here +
  `index.json` entry + parity hashes, all in one commit.

## Consumers (14 test files)

test_schema_parity, test_pipeline_contracts, test_plan_lock, test_bundle_contracts,
test_visual_contracts, test_dataset_population; contract/ (approval-envelope,
feedback-event, run-manifest); integration/test_behavior_evaluation;
unit/{delivery,retrieval,evaluation}.
