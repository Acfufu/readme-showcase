# AGENTS.md — readme-showcase

Orientation map for AI coding agents (and humans) continuing work in this repo.
State snapshot: 2026-10-02, after PR #2 (Plan v3 animated route) was rebased, re-verified, and merged (see bottom).

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
| `tests/` | 102 unittest files (769 tests) mirroring the package; `tests/fixtures/contracts/` golden pairs have their own AGENTS.md |
| `docs/` | Bilingual user docs (`docs/` en + `docs/zh/`); `docs/superpowers/` is local-only design history |
| `scripts/install_skill.py` | Atomic project/user installer; npm `bin` entry |
| `assets/readme/` | Bilingual README artwork + editable sources (`workflow.diagram.json`, `workflow.engine.json`, `hero-motion.json`) |
| `.github/workflows/ci.yml` | 6-job CI matrix (3×Python, npm-package, visual-kernel, elk-unit, motion, real-elk) |
| `.claude-plugin/marketplace.json` | Claude marketplace manifest, version-pinned to `package.json` |

## Commands

```bash
# Full test suite (769 tests; "OK" expected)
npm test   # = PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

# Dev deps (requirements-dev.txt is the authoritative list)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Optional render stack (screenshot gate / animation verify)
brew install resvg ffmpeg        # resvg preferred, rsvg-convert fallback
nvm install && nvm use           # Node 22.22.3 — HARD-PINNED, see below
```

- **Node version trap**: `skill/scripts/render_elk.mjs` hard-pins
  `NODE_VERSION = "22.22.3"` and exits `E_ENGINE_RUNTIME` on any other version.
  Local default node ≠ 22.22.3 is an environment error, not a code bug. Node
  missing from PATH entirely (non-interactive shells) errors ~47 elk/compiled
  tests at once — export PATH="$HOME/.nvm/versions/node/v22.22.3/bin:$PATH"
  before judging a red suite.
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
   forward/reverse reachability. The former local-only `docs/superpowers/`
   scratch tree was archived to
   `~/.readme-showcase-superpowers-archive-20261002.tgz` and deleted on
   2026-10-02 (the contract test skips its superpowers assertions when the
   tree is absent; extract from the archive to restore).
5. `assets/readme/*` — referenced by both READMEs, partly test-pinned.
6. `dataset/retrieval/manifest.json`, `exemplars_index.json`, `exemplars/` —
   CI `validate-dataset` + dataset population tests.
7. `tests/fixtures/**` — the real-elk CI job renders from here.
8. Never commit `skill/.env`; the tracked template is `skill/.env.example`.
9. Do not edit dependency-detection `skipTestIf`-style guards in tests to force
   green. Gates deliberately degrade to skips when optional deps are missing —
   a gate test failing with `'pass' != 'fail'` (e.g. `test_clipped_svg_fails`)
   means your environment lacks Pillow, not that the gate is buggy.

## Open threads (as of 2026-10-02)

Derived from the 2026-09-04 handoff review (RS-HANDOFF-2026-09-04); the report
HTML itself was untracked and has been superseded by this section.

- **Resolved 2026-09-14 — push debt and public CI.** origin/main now carries
  everything through `1be0e28`, and all ten CI legs (6 jobs, 3×Python matrix)
  are green — first full green since 2026-08-11. Two push-time lessons: pin
  numpy to the 3.11-compatible line (2.5.x requires Python ≥3.12), and the
  doc-contract mutation test now tolerates the absent local-only
  `docs/superpowers/` tree on fresh clones.
- **Resolved 2026-10-02 — PR #2 merged.** "fix(skill): support Plan v3 animated
  route in the non-compiled readme pipeline" was rebased onto `7831c3b` (no
  conflicts), verified with a full-suite A/B run (main 762 / PR 769, both green
  with node 22.22.3 on PATH) and a 10/10 green CI matrix, then rebase-merged as
  `7d5b463`; branch deleted on both sides. Design note worth remembering: the
  schema-2 bundle carries a deterministic evaluation-pass *placeholder*
  envelope, which is never an approval shortcut — EvaluateStage re-runs
  `evaluate_generated_bundle` over freshly materialized bytes, and the publish
  gate re-validates before any remote write.
- **Credential hygiene (user action)**: the local `.git/config` `localgitea`
  remote URL embeds a plaintext password (`http://acfufu:***@localhost:3000/…`).
  Treat it as leaked: rotate the Gitea password, then strip credentials from the
  URL and switch to a credential helper (`git config credential.helper osxkeychain`)
  or SSH. Left untouched to avoid breaking the local push flow. Note: the
  `localgitea` mirror is behind main (its server was offline at push time,
  2026-09-14) — `git push localgitea main` once Gitea is up.
- **M1–M15 review findings**: spot-verified 2026-10-02 as closed in code: M1
  (nearest-background contrast + large-text 3:1), M3 (canonical atomic JSON
  writes), M7 (host-path same-model note, pairwise field aligned), M12
  (`check_readability_at_360`, 12px floor at the 360px projection), M13
  (rubric-only aesthetics), M10/M15 previously; M4 is the vendored resvg
  posture. Not individually audited: M2, M5, M6, M8, M9, M11, M14. The
  `docs/superpowers/` history question was resolved 2026-10-02: archived and
  deleted (see red line 4).
- **Local-only dirs (all gitignored, intentionally kept on disk)**: `.lazyzcode/`
  (goal-loop state for the LazyZCode loop), `.superpowers/` (SDD briefs/reports),
  `.codegraph` → symlink into `~/.omo/codegraph/` (local code index).

## Reading path for a new agent

`README.md` → `skill/SKILL.md` → `skill/references/failure-recovery.md` →
`skill/workflows/_index.md` → `skill/scripts/readme_showcase/` (`errors.py`).
Failed gates auto-append to the lessons ledger (`lessons-pending.json`;
promoted to `skill/references/lessons.md` only after human confirmation).

## 2026-09-14 sweep (this repo's last hygiene pass)

Deleted local junk (`.omo/` run records — archived to
`~/.readme-showcase-omo-archive-20260914.tgz` as `20260909`, `.DS_Store`, empty
`artifacts/`, `.impeccable/` cache); hardened `.gitignore` for all local tool
dirs; synced README_zh.md visual-routes table with the `animated` route; added
this file and `tests/fixtures/contracts/AGENTS.md`; fixed the two CI dep gaps
(elk-unit pip install; Pillow + numpy in requirements-dev.txt); pushed main and
verified the first all-green CI run since 2026-08-11 (numpy re-pinned to
3.11-compatible 2.4.6; doc-contract mutation test made clone-safe along the way).

## 2026-10-02 PR #2 merge (most recent pass)

Rebased PR #2 (Plan v3 animated route, 4 commits, previously red since
2026-08-16) onto `7831c3b` without conflicts; its old CI failures were the
stale pre-79cbb2c/d796ca6 environment gaps, not logic. Ran the full suite as
an A/B pair with node 22.22.3 on PATH (main 762 / PR 769, both OK — node
missing from a non-interactive PATH causes ~47 elk/compiled errors that look
like real breakage), force-pushed, watched all ten CI legs go green, and
rebase-merged as `7d5b463`. Local main synced, PR branch deleted both sides;
`.video_agent/` added to `.gitignore`; `pnpm-lock.yaml` (stray pnpm artifact,
repo ships npm) left on disk pending an owner decision.

## 2026-10-02 grilling pass (most recent)

Owner decisions taken in a grilling session and executed the same day:

- `localgitea` remote URL stripped of its embedded plaintext password;
  repo-local `credential.helper osxkeychain` set (keychain already holds an
  entry for localhost:3000 — after the password rotation it will 401 once,
  then git prompts and updates the entry).
- `docs/superpowers/` archived to
  `~/.readme-showcase-superpowers-archive-20261002.tgz` (4 files, roundtrip
  verified) and deleted; AGENTS.md red line 4 and local-only-dirs updated
  accordingly.
- Stray `pnpm-lock.yaml` deleted (repo ships npm; zero pnpm usage).
- Next direction chosen: real-world validation — `redesign` + `animated`
  route on dsh-desktop (fresh single-locale target), then the same-route
  rerun on alas-launcher (PR #2's origin repo) with merged main, both
  stopping at local preview; candidates stay out of the target repos.
