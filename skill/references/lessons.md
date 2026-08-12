# README Showcase Lessons

## How this file is used

`lessons.md` is the human-confirmed incident ledger for the README Showcase
skill. Automated evaluation runs never write here. They append candidate
entries to `lessons-pending.json` in the same directory as the evaluation
report — the `evaluate --output` directory or
`stages/07-validation/attempts/<N>/` — and `check-publish-gate` records
identity overrides there as independent non-failure entries.

Only a human reviewer moves a confirmed entry from `lessons-pending.json`
into this file, converting the automated `fix_hint` into a durable
`rule_change`. Until a human confirms it, a lesson is a candidate, not a rule.

## Entry format

| Field | Meaning |
| --- | --- |
| `gate` / `incident` | Gate or failure that produced the lesson, e.g. `E_IDENTITY_MATCH` |
| `root_cause` | What went wrong, as recorded in the evaluation finding |
| `rule_change` | The durable rule a future run should follow |

Each confirmed entry is a Markdown list item: the gate, the root cause, and
the rule change. Keep this section append-only and sorted by gate so a rule
that changes can be traced to the incident that produced it.

## Confirmed lessons

No lessons confirmed yet. Migrate entries from `lessons-pending.json` here
only after human review.
