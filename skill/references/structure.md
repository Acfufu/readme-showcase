# README Content Structure

Use this reference in README mode or for a read-only content audit. It combines an evidence-driven project narrative with the plain-language sequence:

```text
Value → Proof → Mechanism → First use → Detail
```

## Contents

1. [Evidence map](#evidence-map)
2. [Retrieval dataset boundary](#retrieval-dataset-boundary)
3. [First-screen test](#first-screen-test)
4. [Narrative architecture](#narrative-architecture)
5. [Project-type selection](#project-type-selection)
6. [Content rules](#content-rules)
7. [Language layout](#language-layout)
8. [Validation](#validation)

## Evidence Map

Record the source for every public claim before drafting:

| Question | Typical repository evidence |
| --- | --- |
| Who is it for and what result does it create? | Existing docs, product UI, CLI help, entry point |
| What problem does it replace or improve? | Current architecture, migration docs, verified limitations |
| What is genuinely different? | Shared flow, implementation, compatibility layer, tests |
| What proves it works? | Screenshots, outputs, examples, tests, public artifacts |
| How does a new user succeed? | Release files, install scripts, package metadata, smoke command |
| What can be configured? | Example config, environment schema, settings UI |
| What are the boundaries? | Compatibility docs, issue templates, tests, external constraints |
| Why should users trust it? | CI, releases, license, security policy, reproducible checks |

If a claim has no source, remove it or label it explicitly as a limitation or future idea.

## Retrieval Dataset Boundary

Revision 2 contains 12 project-owned abstract pattern records: 10 production
`train` records and two isolated `test` records. Sources are pinned public
repository commits with material SHA-256 plus commit-pinned SPDX/license
evidence. Human review rewrites only `summary`, `structure`, and `proof`;
source README text, code, badges, logos, images, animation, and benchmark
answers are never copied.

Production retrieval scores up to five `train` patterns by declared project
type, section intent, and tags. `test` identities are unreachable in production.
Retrieved patterns guide editorial structure only. Every target claim, command,
label, compatibility statement, and visual still binds to current target
repository evidence.

Read the installed `dataset/README.md` for the 12-source ledger and assembly
flow. Validate the manifest before scanning or retrieval.

## First-screen Test

Without scrolling, a new visitor should understand:

1. What is this?
2. What can it do for me?
3. What should I inspect next?

The hero answers the first two. The next module supplies real proof. Do not begin an unfamiliar project with architecture, contributor instructions, a command, or a long table of contents.

## Narrative Architecture

Build top to bottom and skip unsupported stages.

### 1. Identity and orientation

Use the smallest useful set:

- Project name and plain-language positioning.
- Logo or project-native hero when justified.
- Verified status signals such as release, build, package, platform, or license.
- Page navigation only when the README is long.
- Language switch only when variants exist.

Do not guess badges, create broken dark-mode variants, or link to sections that do not exist.

### 2. Proof

Place a real screenshot, output, diagram, artifact, or minimal end-to-end example immediately after the opening. Prefer one strong proof over several decorative modules.

### 3. Overview and boundary

Explain the audience, input/output or workflow, and the most important limitation or prerequisite.

### 4. Motivation or architecture difference

Use this section only when the project changes an established workflow or solves a recognizable problem:

1. Previous behavior or user problem.
2. Why it persists.
3. The project's concrete approach.
4. The resulting user-visible difference.

Use a before/after diagram for changed ownership, data flow, or component responsibility. Otherwise use a short comparison table.

### 5. Differentiators

Compare against a real baseline:

| Capability | Previous or alternative approach | This project |
| --- | --- | --- |
| Verified dimension | Concrete old behavior | Concrete new behavior |

Group features by user outcome. Explain enough mechanism to make each claim credible; do not dump every minor feature.

### 6. Quick start

Put the lowest-friction route first:

1. Hosted demo or package-manager install, if available.
2. Recommended local or deployment path.
3. Alternative platforms or artifacts.
4. An observable success command, URL, screen, or output.

When several artifacts exist, add a chooser table before detailed instructions. Put long alternative flows in `<details>` without hiding prerequisites or warnings.

### 7. Usage

Show one complete happy path before listing parameters, modes, or advanced features. Use safe copyable examples and state the expected result.

### 8. Configuration and operations

Document only implemented settings. Group them by user decision rather than source-file order. Include verified defaults and reload requirements. Add persistence, upgrade, diagnostics, or troubleshooting only when the project needs them.

### 9. Trust and close

Choose from security, privacy/permissions, compatibility/limitations, development, license, acknowledgements, support, and restrained social proof. Do not copy optional footer sections merely because a reference uses them.

## Project-Type Selection

| Project shape | Usually prioritize | Add only when supported |
| --- | --- | --- |
| CLI or library | Install, minimal example, API/options, development | Benchmarks, migration, compatibility matrix |
| Desktop/mobile app | Download, screenshots, main workflow, permissions | Signing, packaging, platform limitations |
| Browser extension | Install, use, supported sites, permissions/privacy | Provider setup, native helper, store links |
| Service/backend | Architecture, deploy, health check, configuration | Persistence, observability, security profiles |
| Agent Skill | Promise, invocation, workflow, safety boundaries | Visual workflow, requirements, customization |
| Developer utility | Problem, before/after, quick start, examples | Internals, integration recipes |

Short projects should collapse this aggressively. Empty sections do not improve a README.

## Content Rules

- Replace internal jargon with concrete outcomes.
- Explain each mechanism once and remove repeated promises.
- Put the shortest working install path before advanced configuration.
- Keep limitations visible when they affect user choice.
- Prefer one end-to-end example over disconnected snippets.
- Never invent adoption, benchmarks, compatibility, testimonials, roadmap features, licenses, or community size.
- Keep commands, links, API details, and frequently updated facts out of images.
- Use GitHub callouts by meaning: `NOTE` for context, `TIP` for a faster path, `IMPORTANT` for prerequisites, and `WARNING` for material risk.
- Use tables for exact comparison, artifact choice, compatibility, and configuration defaults.
- Use `<details>` for long alternatives or troubleshooting, not critical safety information.
- If no license is present, say so; never infer one.
- Quote shell placeholders instead of using executable angle-bracket placeholders.

## Language Layout

Follow repository convention and user scope:

- Keep one README when one language is requested.
- For English and Simplified Chinese, normally use `README.md` and `README_zh.md` or the repository's existing convention.
- Add reciprocal links near the opening.
- Keep claims, commands, paths, and status equivalent; localize meaning rather than translating mechanically.
- Preserve product names, protocols, APIs, file paths, and commands unless the product localizes them.

## Validation

Verify:

- The first screen passes the three-question test.
- The next module presents real proof.
- Every feature, compatibility, privacy, and support claim has evidence.
- Install and quick-start commands match current scripts and artifacts.
- The quick start names an observable success condition.
- Badges, releases, license, security, support, anchors, and language links exist.
- Unsupported sections from reference READMEs were not copied.
- Each language variant carries the same product truth.
- Markdown remains readable, searchable, and copyable when images fail.
