# Pinned Beautify GitHub README Delta

This ledger records rule reuse from
`oil-oil/beautify-github-readme@55bdb1c05414cd7a0cf911d02e55ece79777206e`.
Generic structured-engine guidance is pinned separately to merged PR #11,
commit `45eb8f7259bb5d4bcdfab703f43387d4ed308f45`, which addresses Issue #9.

`readme-showcase` remains the product owner. Upstream contributes visual and
editorial rules only.

## Reference packs

| Pinned pack | Classification | Destination |
| --- | --- | --- |
| `content-architecture.md` | already-covered | `structure.md` evidence map and value-to-detail sequence |
| `github-readme-canvas.md` | imported delta | hybrid publish format and source/fallback checks |
| `hybrid-svg-production.md` | imported | opt-in gate, editable layout/subject/prompt, PNG/WebP publication |
| `motion-production.md` | already-covered | `motion-production.md` and retained renderer |
| `project-native-hero.md` | already-covered | `visual-production.md` opening and project-native tests |
| `svg-production.md` | imported delta | optional structure-engine boundary and system-font output |
| `visual-direction.md` | already-covered | palette, typography, motif, density, composition |
| `showcase-contribution.md` | excluded | upstream showcase workflow and remote PR are outside product scope |

Mapped coverage: `7/8 = 87.5%`. The excluded showcase pack is not replaced by
ELK and does not grant remote-write authority.

## Rule decisions

| Rule | Classification | Local contract |
| --- | --- | --- |
| Pure SVG versus hybrid choice | imported | explicit choice or delegated decision before generation |
| Editable hybrid sources | imported | layout SVG, raster subject, prompt, and static fallback required |
| Published hybrid format | imported | final PNG/WebP; no unresolved raster SVG or large base64 layer |
| System fonts | already-covered | system stack only; no remote font import |
| Optional structure engine | imported | body layout/routing/wrapping only; no title, palette, claim, or publish ownership |
| Static fallback | already-covered + expanded | motion and hybrid failure leave README/fallback unchanged |
| Attribution | imported boundary | optional, owner-requested, previewed; never delivery or showcase requirement |
| Preview and publish safety | already-covered | `900px`/`360px`, light/dark, local diff, explicit write approval |
| Automatic ImageGen | excluded | no benchmark, audit, or CI generation |
| Showcase assets and examples | excluded | no upstream README, image, hero, badge, or case-study asset copied |

Issue #9 proposed ELK, but merged PR #11 added generic optional-engine
guidance only. This project does not claim upstream ELK integration.

## Recomputed counts

| Measurement | Count |
| --- | ---: |
| Reference packs mapped | `7/8 = 87.5%` |
| Pinned upstream showcase assets copied | `0/29 = 0%` |
| Pinned upstream script source size | `93 + 599 = 692` |
| Exact unchanged audit-script lines | `51/93 = 54.84%` |
| Exact unchanged motion-renderer lines | `598/599 = 99.83%` |
| Combined exact unchanged lines | `649/692 = 93.79%` |
| Local adapted script lines after attribution/extensions | `992` |
| Direct BGR Todo touchpoints | `7/19 = 36.84%` |
| Additional safe-handoff Todos | `2/19 = 10.53%` |
| Original product ownership | `19/19 = 100%` |

The pinned source contains 16 top-level `assets/readme` files and 13 English
variants. None are present here. Local README assets are project-owned.

Counts use `git diff --no-index --diff-algorithm=minimal` against the pinned
files; additions do not count as retained source lines. Both scripts are
MIT-derived. Full upstream license text and source URL remain in
`motion-production.md`; each adapted script carries a source note.
