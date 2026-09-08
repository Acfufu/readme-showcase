# AGENTS.md — readme-showcase

Orientation map for AI coding agents (and humans) continuing work in this repo.
State snapshot: 2026-09-09, after the repo-sweep described at the bottom.

## What this repo is

`readme-showcase` is a portable Agent Skill package: when an assistant receives
`$readme-showcase redesign .`, it scans the target repo for real evidence (real
commands, real tests, real output) and rewrites that repo's GitHub README from
it — copy and artwork. Core principle: only claim what the repo can prove.

Runtime is 100% Python; distribution is npm/npx (`npx --yes github:Acfufu/readme-showcase
skills install`, which executes `scripts/install_skill.py`) plus a Claude Code
marketplace manifest (`.claude-plugin/marketplace.json`). The pipeline has 8
stages, each behind hard gates; passing evaluation never implies permission to
publish — commit/push/publish always require separate human approval.

## Layout

| Path | Role |
|---|---|
| `skill/SKILL.md` | Entry contract: modes `shape/audit/redesign/polish/visualize`, approval gates |
| `skill/references/` | 16 contract docs; `failure-recovery.md` maps error codes → recovery points |
| `skill/workflows/` | 8 stage docs (`_index.md` first) |
| `skill/schemas/` | 45 JSON Schemas (Draft 2020-12) |
| `skill/scripts/` | `readme_pipeline.py` (run/status/resume/preview/screenshot-gate/validate-dataset), `audit_readme.py`, renderers; core package `readme_showcase/` (error codes in `errors.py`) |
| `skill/vendor/` | Pinned elkjs 0.9.3 bundle + resvg-js manifest (hash-locked) |
| `dataset/` | Retrieval-mode dataset: `manifest.json` (22 records, hash-pinned), queries, candidates, exemplars + index. Read-only by contract |
| `tests/` | 101 unittest files (~748 tests) mirroring the package |
| `docs/` | Bilingual user docs (`docs/` en + `docs/zh/`); `docs/superpowers/` is local-only design history |
| `scripts/install_skill.py` | Atomic project/user installer; npm `bin` entry |
| `assets/readme/` | Bilingual README artwork + editable sources (`workflow.diagram.json`, `workflow.engine.json`, `hero-motion.json`) |
| `.github/workflows/ci.yml` | 6-job CI matrix (3×Python, npm-package, visual-kernel, elk-unit, motion, real-elk) |
| `.claude-plugin/marketplace.json` | Claude marketplace manifest, version-pinned to `package.json` |

## Commands

```bash
# Full test suite (~748 tests; "OK" expected)
npm test   # = PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

# Dev deps (requirements-dev.txt is the authoritative list)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Optional render stack (screenshot gate / animation verify)
brew install resvg ffmpeg        # resvg preferred, rsvg-convert fallback
nvm install && nvm use           # Node 22.22.3 — HARD-PINNED, see below
```

- **Node version trap**: `skill/scripts/render_elk.mjs` hard-pins
  `NODE_VERSION = "22.22.3"` and exits `E_ENGINE_RUNTIME` on any other version.
  Local default node ≠ 22.22.3 is an environment error, not a code bug.
- Dataset check: `python3 skill/scripts/readme_pipeline.py validate-dataset`.
- Installer: `python3 scripts/install_skill.py install|check|update`.
- Runtime state lives in `~/.codex/state/readme-showcase/` — never written into
  a target repo.

## Red lines (changing these requires updating their contract tests in lockstep)

1. `skill/vendor/elkjs/lib/elk.bundled.js` — sha256 pinned in `ci.yml`
   (real-elk + visual-kernel jobs); `.gitattributes` marks it `-whitespace`.
2. `.github/workflows/ci.yml` — pinned by `tests/test_ci_contract.py`, including
   the **literal content of `requirements-dev.txt`** and a negative assertion
   that no unpinned `jsonschema>=` install appears in the workflow.
3. `.claude-plugin/marketplace.json` — pinned by `tests/test_marketplace_contract.py`
   (version parity with `package.json`; must stay out of the npm tarball).
4. `skill/SKILL.md`, `skill/references/`, `skill/workflows/`, `skill/.env.example` —
   doc-contract tree; `tests/test_documentation_contract.py` enforces
   forward/reverse reachability. `docs/superpowers/` (gitignored) is also covered
   by an exact allowlist there — do not delete or move it.
5. `assets/readme/*` — referenced by both READMEs, partly test-pinned.
6. `dataset/retrieval/manifest.json`, `exemplars_index.json`, `exemplars/` —
   CI `validate-dataset` + dataset population tests.
7. `tests/fixtures/**` — the real-elk CI job renders from here.
8. Never commit `skill/.env`; the tracked template is `skill/.env.example`.
9. Do not edit dependency-detection `skipTestIf`-style guards in tests to force
   green. Gates deliberately degrade to skips when optional deps are missing —
   a gate test failing with `'pass' != 'fail'` (e.g. `test_clipped_svg_fails`)
   means your environment lacks Pillow, not that the gate is buggy.

## Open threads (as of 2026-09-09)

Derived from the 2026-09-04 handoff review (RS-HANDOFF-2026-09-04); the report
HTML itself was untracked and has been superseded by this section.

- **Push debt — 18 commits ahead of origin/main only.** GitHub sits at
  `a2059b9` (2026-08-13); the `localgitea` mirror is already in sync. Public
  `npx` users install the 3-week-old version until you push. Push only after
  the CI fix below is in.
- **Public CI red since 2026-08-13 — both root causes were missing CI deps, not
  regressions**: `elk-unit` had no `pip install -r requirements-dev.txt` step
  (→ `ImportError: jsonschema` in e2e tests), and `legacy-all` lacked Pillow
  (top-level `from PIL import Image` in `verify_animation_matrix.py`). Fixed
  in-tree on 2026-09-09 (elk-unit step added; Pillow + numpy added to
  `requirements-dev.txt`). Verify all six jobs green after push. Last known
  full green: 2026-08-11.
- **PR #2 is open on GitHub**: "fix(skill): support Plan v3 animated route…",
  4 commits, CI red, forked from `a2059b9`. Read its diff, rebase onto current
  main, re-run CI, then merge or close deliberately.
- **Credential hygiene (user action)**: the local `.git/config` `localgitea`
  remote URL embeds a plaintext password (`http://acfufu:***@localhost:3000/…`).
  Treat it as leaked: rotate the Gitea password, then strip credentials from the
  URL and switch to a credential helper (`git config credential.helper osxkeychain`)
  or SSH. Left untouched in the sweep to avoid breaking the local push flow.
- **M1–M15 review findings**: `docs/superpowers/reviews/…plans-review.md` lists
  15 visual-quality findings; later commits closed some (e.g. M10, M15) but full
  closure is unverified — check M12 (min font size at 360px) first. Then decide:
  commit sanitized copies or archive deliberately (they exist only on this machine).
- **Local-only dirs (all gitignored, intentionally kept on disk)**: `.lazyzcode/`
  (goal-loop state for the LazyZCode loop), `.superpowers/` (SDD briefs/reports),
  `docs/superpowers/` (design history, contract-test allowlisted),
  `.codegraph` → symlink into `~/.omo/codegraph/` (local code index).

## Reading path for a new agent

`README.md` → `skill/SKILL.md` → `skill/references/failure-recovery.md` →
`skill/workflows/_index.md` → `skill/scripts/readme_showcase/` (`errors.py`).
Failed gates auto-append to the lessons ledger (`lessons-pending.json`;
promoted to `skill/references/lessons.md` only after human confirmation).

## 2026-09-09 sweep (this repo's last hygiene pass)

Deleted local junk (`.omo/` run records — archived to
`~/.readme-showcase-omo-archive-20260909.tgz`, `.DS_Store`, empty `artifacts/`,
`.impeccable/` cache); hardened `.gitignore` for all local tool dirs; synced
README_zh.md visual-routes table with the `animated` route; fixed the two CI
dep gaps above; added this file.
