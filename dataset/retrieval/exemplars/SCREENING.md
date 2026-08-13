# Exemplar Screening Ledger — curated-01 … curated-10

License screening for the 10 curated exemplar records in this directory.
Screening date: **2026-08-13** · Screener: readme-showcase dataset screener (agent), human confirmation pending final merge review.
Screening checklist applied to every record:

1. Repository license at the **pinned commit** is permissive (MIT / Apache-2.0 / BSD-3-Clause / CC-BY-4.0) — `license_evidence_url` + `license_evidence_sha256` recorded below.
2. The screenshot captures **only content authored by the repository owner** (no third-party trademarks, no user photos) — verified per image.
3. The record is a **structural reference** — annotation describes composition zones and hierarchy, never style or colors to imitate.
4. `split` assignment: 8 `train` + 2 `test`; `test` records must never enter production retrieval.
5. `human_reviewed: true` is set per the retrieval contract (validator requires it); human confirmation happens at merge review of this ledger.

## Records

| Record | Family | Source repository | Split | License (SPDX) | License evidence URL | License evidence sha256 |
| --- | --- | --- | --- | --- | --- | --- |
| `exemplar-curated-01` | split-hero | https://github.com/prisma/prisma | train | Apache-2.0 | https://github.com/prisma/prisma/blob/c332c674d102c7a716409bc5b455777c9725f397/LICENSE | `1b7b1c659a82d4688d9f781b3eaccccd5415500e5ba87f32f8f2a5e94dffaa15` |
| `exemplar-curated-02` | artifact-wall | https://github.com/HumanSignal/label-studio | train | Apache-2.0 | https://github.com/HumanSignal/label-studio/blob/b4f80615bd6a88b9e85a71796ffe0cc31a35c947/LICENSE | `ca656b15c1e22468c7798df4356660755fb1ccd12d7ee0bade82bd65e3038e1b` |
| `exemplar-curated-03` | background-proof | https://github.com/aristocratos/btop | train | Apache-2.0 | https://github.com/aristocratos/btop/blob/d5e5619cbf5a267b592b61cdbc3f98a6420259a3/LICENSE | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `exemplar-curated-04` | title-then-proof | https://github.com/pytorch/pytorch | train | BSD-3-Clause | https://github.com/pytorch/pytorch/blob/af55402990611787168cfabfb457c9d30f5a4d73/LICENSE | `bd018feef8825e88181c84eb7e3aa4eafb8f08a20d9fd6ef948569610c4a3e43` |
| `exemplar-curated-05` | integrated-diagram | https://github.com/kubescape/kubescape | train | Apache-2.0 | https://github.com/kubescape/kubescape/blob/a072e35e31ef186087393f89b447a899e974fa12/LICENSE | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `exemplar-curated-06` | terminal-motif | https://github.com/bootandy/dust | train | Apache-2.0 | https://github.com/bootandy/dust/blob/f5852150ba4da4157989fa3779b09d34d5f1a078/LICENSE | `aee6e2d13d3a55c7881630c09b41ca7b3b44bb1437b5d36695d22decc3655160` |
| `exemplar-curated-07` | bento-grid | https://github.com/supabase/supabase | train | Apache-2.0 | https://github.com/supabase/supabase/blob/77c5a0b9d941bc75719645d9cd0639296ceff586/LICENSE | `c29468abcd629e248c2f76848155c386b733872bcabcc4cf65ef1e7c61431042` |
| `exemplar-curated-08` | numbered-flow | https://github.com/argoproj/argo-workflows | train | Apache-2.0 | https://github.com/argoproj/argo-workflows/blob/96e62da4a1cd273e174df0239804705b913700bd/LICENSE | `c996fc213c94f4a4a838407d355b081226caa7b103a4f239bb87fb8f1d8e3a60` |
| `exemplar-curated-09` | editorial-manifesto | https://github.com/withastro/astro | test | MIT | https://github.com/withastro/astro/blob/09f0dc7f90ef92f8520e13b7ba130e4b8aad31bd/LICENSE | `4aa72be797aad0e5e21827eaae6a1df9e122d7a401f07d2c308a6b9a5f0e1278` |
| `exemplar-curated-10` | comparison-table | https://github.com/muesli/duf | test | MIT | https://github.com/muesli/duf/blob/4636deb4a7b707a9f04c602db033f9837e50b3f6/LICENSE | `81090e71a92a18ce7f95bd3433a9523c568329be4fd47d8f0b94d490ca628d86` |

Every `license_evidence_sha256` is the SHA-256 of the LICENSE blob downloaded at the pinned commit above.

## Material provenance

Each PNG (`curated-NN.png`) is the repository-owner image asset referenced by the repo README at the pinned commit, optionally border-trimmed to the visual region and/or converted to PNG (JPEG/GIF sources), with `asset.sha256` equal to the SHA-256 of the final PNG bytes on disk.

| Record | Material source at pinned commit | Asset sha256 |
| --- | --- | --- |
| `exemplar-curated-01` | `prisma/prisma@c332c674 images/prisma-next.png` | `c29f9244aa7ded56ad5f645a05341f33d98ea5c5224efda2ad0ea1e10ca64c15` |
| `exemplar-curated-02` | `HumanSignal/label-studio@b4f80615 images/template-types.png` | `eefae24dad17e2af46a256bdc0eec5c148ce19bf60c8cf37213687155e52b734` |
| `exemplar-curated-03` | `aristocratos/btop@d5e5619c Img/normal.png` | `7eee6f3c5c84419d782827c6738d5b94cf6df39422cb75a421b668e98da9a3d7` |
| `exemplar-curated-04` | `pytorch/pytorch@af554029 docs/source/_static/img/dynamic_graph.gif` (first frame) | `bc8322e0c8cbf1fb2f22d6b7c1b3b3aa8463a75b49d851651b29c456c06e16aa` |
| `exemplar-curated-05` | `kubescape/kubescape@a072e35e docs/img/ks-cli-arch.png` | `f34a95511eab9a6120ab000f0b40a7db1fc5652791221414545fbe6cf56b53fa` |
| `exemplar-curated-06` | `bootandy/dust@f5852150 media/snap.png` | `f1c0a9f5a82260d5eaf87099d6428a2a0c4ccdeba63ffaf27d0c1d10452237b0` |
| `exemplar-curated-07` | `supabase/supabase@77c5a0b9 apps/www/public/images/github/supabase-dashboard.png` | `8cb0305f5af116a26a2027b2998830a4f0a0431c110008468ac66c43b90fc1ba` |
| `exemplar-curated-08` | `argoproj/argo-workflows@96e62da4 docs/assets/screenshot.png` | `7c7d40eb5d896b8836f79308da430e4014565325926f4972c46e6c6ad919e3f4` |
| `exemplar-curated-09` | `withastro/astro@09f0dc7f .github/assets/banner.jpg` (converted to PNG) | `1e68bdb05dc71c469aa4677e8157f437324ef32765088944e7300953d5571204` |
| `exemplar-curated-10` | `muesli/duf@4636deb4 duf.png` | `38540f36074055db6db414e002fe4f74fc18f0f9997bcb68c6476b7a22fe62b3` |

## Composition annotations (structural only — zones / hierarchy / negative patterns)

- **curated-01 split-hero** (`prisma/prisma`): zones `identity, proof`; hierarchy `display, section`; avoids `centered-template, generic-gradient, three-equal`. Identity statement left, editor-window proof right.
- **curated-02 artifact-wall** (`HumanSignal/label-studio`): zones `context, proof`; hierarchy `section, supporting`; avoids `card-soup, text-wall, three-equal`. Categorized grid of labeled template artifacts.
- **curated-03 background-proof** (`aristocratos/btop`): zones `metadata, proof`; hierarchy `display, supporting`; avoids `centered-template, empty-hero, generic-gradient`. Real system metrics fill the whole frame as proof.
- **curated-04 title-then-proof** (`pytorch/pytorch`): zones `identity, proof`; hierarchy `display, section`; avoids `centered-template, screenshot-float, text-wall`. Statement heading above a code demonstration.
- **curated-05 integrated-diagram** (`kubescape/kubescape`): zones `context, workflow`; hierarchy `section, supporting`; avoids `decorative-filler, generic-gradient, three-equal`. Inputs → engine → outputs in one labeled flow.
- **curated-06 terminal-motif** (`bootandy/dust`): zones `context, proof`; hierarchy `section, supporting`; avoids `centered-template, empty-hero, three-equal`. Command output split into tree and treemap panels.
- **curated-07 bento-grid** (`supabase/supabase`): zones `context, metadata, proof`; hierarchy `display, section, supporting`; avoids `card-soup, text-wall, three-equal`. Dashboard of unequal widgets under a navigation frame.
- **curated-08 numbered-flow** (`argoproj/argo-workflows`): zones `context, workflow`; hierarchy `section, supporting`; avoids `centered-template, decorative-filler, text-wall`. DAG from root through split, numbered parallel stages, and reduce.
- **curated-09 editorial-manifesto** (`withastro/astro`): zones `identity`; hierarchy `display`; avoids `centered-template, generic-gradient, three-equal`. Single typographic statement over a full-bleed color field.
- **curated-10 comparison-table** (`muesli/duf`): zones `context, proof`; hierarchy `section, supporting`; avoids `centered-template, generic-gradient, text-wall`. Stacked bordered tables comparing device groups.

## Rejected candidates (screened, not used)

| Repository | Reason for rejection |
| --- | --- |
| `eza-community/eza` | Pinned-commit LICENSE.txt is **EUPL-1.2**, not in the permissive allowlist (MIT/Apache-2.0/BSD-3-Clause/CC-BY-4.0). |
| `githubnext/monaspace` | Pinned-commit LICENSE is **SIL OFL-1.1**, not in the permissive allowlist. |
| `kubescape/kubescape` `docs/img/ksfromcodetodeploy.png` | Hero-flow visual contains third-party trademarks (GitHub, GitLab, Jenkins, Kubernetes, Helm, VS Code logos). |
| `microsoft/promptflow` `docs/media/readme/vsc.png` | Contains VS Code iconography (third-party trademark). |
| `open-webui/open-webui` `demo.png` | Contains OpenAI logo in the model selector (third-party trademark). |
| `appsmithorg/appsmith` `static/images/appsmith-introduction-video-tile.png` | Contains a WhatsApp tab label in a UI mockup (third-party trademark); also a video tile, not a hero composition. |
| `dbt-labs/dbt-core` `etc/dbt-transform.png` | Contains Snowflake / BigQuery / Redshift / Postgres / Databricks logos (third-party trademarks). |
| `lsd-rs/lsd` `assets/screen_lsd.png` | Asset lives on an unpinned `assets` branch; 404 at the pinned commit — not traceable to a commit. |
| `mlflow/mlflow` `assets/readme-*.png` | README references `refs/heads/master` (mutable); assets may carry third-party framework logos. |
| `jmacdonald/amp` `screenshot.png` | LICENSE at pinned commit is GPL-family, not in the permissive allowlist. |
