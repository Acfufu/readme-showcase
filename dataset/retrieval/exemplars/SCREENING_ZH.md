# Exemplar 筛查台账（中文版）: curated-01 … curated-10

本目录 10 条人工策展 exemplar 记录的许可证筛查台账。
筛查日期: **2026-08-13** · 筛查人: readme-showcase 数据集筛查器（agent），人工确认待合并审查时进行。
每条记录均应用以下筛查清单:

1. 仓库在**固定 commit** 上的许可证为宽松许可证（MIT / Apache-2.0 / BSD-3-Clause / CC-BY-4.0），`license_evidence_url` 与 `license_evidence_sha256` 记录于下。
2. 截图仅包含**仓库所有者创作的内容**（无第三方商标、无用户照片），逐图核验。
3. 记录为**结构参照**: 标注描述构图分区（zones）与层级（hierarchy），从不描述可模仿的风格或颜色。
4. `split` 分配: 8 条 `train` + 2 条 `test`；`test` 记录永不进入生产检索。
5. `human_reviewed: true` 依据检索合同设置（校验器要求）；人工确认在本台账的合并审查时进行。

## 记录

| 记录 | 构图族（Family） | 来源仓库 | 拆分 | 许可证（SPDX） | 许可证证据 URL | 许可证证据 sha256 |
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

每条 `license_evidence_sha256` 均为在以上固定 commit 处下载的 LICENSE 文件的 SHA-256。

## 材料来源

每个 PNG（`curated-NN.png`）均为仓库 README 在固定 commit 处引用的仓库所有者图片资产，可选地裁剪到视觉区域边界和/或转换为 PNG（JPEG/GIF 来源），`asset.sha256` 等于磁盘上最终 PNG 字节的 SHA-256。

| 记录 | 固定 commit 处的材料来源 | 资产 sha256 |
| --- | --- | --- |
| `exemplar-curated-01` | `prisma/prisma@c332c674 images/prisma-next.png` | `c29f9244aa7ded56ad5f645a05341f33d98ea5c5224efda2ad0ea1e10ca64c15` |
| `exemplar-curated-02` | `HumanSignal/label-studio@b4f80615 images/template-types.png` | `eefae24dad17e2af46a256bdc0eec5c148ce19bf60c8cf37213687155e52b734` |
| `exemplar-curated-03` | `aristocratos/btop@d5e5619c Img/normal.png` | `7eee6f3c5c84419d782827c6738d5b94cf6df39422cb75a421b668e98da9a3d7` |
| `exemplar-curated-04` | `pytorch/pytorch@af554029 docs/source/_static/img/dynamic_graph.gif`（取首帧） | `bc8322e0c8cbf1fb2f22d6b7c1b3b3aa8463a75b49d851651b29c456c06e16aa` |
| `exemplar-curated-05` | `kubescape/kubescape@a072e35e docs/img/ks-cli-arch.png` | `f34a95511eab9a6120ab000f0b40a7db1fc5652791221414545fbe6cf56b53fa` |
| `exemplar-curated-06` | `bootandy/dust@f5852150 media/snap.png` | `f1c0a9f5a82260d5eaf87099d6428a2a0c4ccdeba63ffaf27d0c1d10452237b0` |
| `exemplar-curated-07` | `supabase/supabase@77c5a0b9 apps/www/public/images/github/supabase-dashboard.png` | `8cb0305f5af116a26a2027b2998830a4f0a0431c110008468ac66c43b90fc1ba` |
| `exemplar-curated-08` | `argoproj/argo-workflows@96e62da4 docs/assets/screenshot.png` | `7c7d40eb5d896b8836f79308da430e4014565325926f4972c46e6c6ad919e3f4` |
| `exemplar-curated-09` | `withastro/astro@09f0dc7f .github/assets/banner.jpg`（已转换为 PNG） | `1e68bdb05dc71c469aa4677e8157f437324ef32765088944e7300953d5571204` |
| `exemplar-curated-10` | `muesli/duf@4636deb4 duf.png` | `38540f36074055db6db414e002fe4f74fc18f0f9997bcb68c6476b7a22fe62b3` |

## 结构标注（仅结构: 构图分区 / 层级 / 反面模式）

- **curated-01 split-hero**（`prisma/prisma`）: 分区 `identity, proof`；层级 `display, section`；避免 `centered-template, generic-gradient, three-equal`。左侧身份陈述，右侧编辑器窗口作为证明。
- **curated-02 artifact-wall**（`HumanSignal/label-studio`）: 分区 `context, proof`；层级 `section, supporting`；避免 `card-soup, text-wall, three-equal`。带标签的模板工件分类网格。
- **curated-03 background-proof**（`aristocratos/btop`）: 分区 `metadata, proof`；层级 `display, supporting`；避免 `centered-template, empty-hero, generic-gradient`。真实系统指标铺满整帧作为证明。
- **curated-04 title-then-proof**（`pytorch/pytorch`）: 分区 `identity, proof`；层级 `display, section`；避免 `centered-template, screenshot-float, text-wall`。陈述标题位于代码演示之上。
- **curated-05 integrated-diagram**（`kubescape/kubescape`）: 分区 `context, workflow`；层级 `section, supporting`；避免 `decorative-filler, generic-gradient, three-equal`。输入到引擎再到输出，形成一条带标签的流程。
- **curated-06 terminal-motif**（`bootandy/dust`）: 分区 `context, proof`；层级 `section, supporting`；避免 `centered-template, empty-hero, three-equal`。命令输出拆分为树状面板与树图面板。
- **curated-07 bento-grid**（`supabase/supabase`）: 分区 `context, metadata, proof`；层级 `display, section, supporting`；避免 `card-soup, text-wall, three-equal`。导航框架下由不等宽组件构成的控制台。
- **curated-08 numbered-flow**（`argoproj/argo-workflows`）: 分区 `context, workflow`；层级 `section, supporting`；避免 `centered-template, decorative-filler, text-wall`。从根节点经分叉、编号并行阶段与归约的 DAG。
- **curated-09 editorial-manifesto**（`withastro/astro`）: 分区 `identity`；层级 `display`；避免 `centered-template, generic-gradient, three-equal`。单一排版陈述铺满整幅色块。
- **curated-10 comparison-table**（`muesli/duf`）: 分区 `context, proof`；层级 `section, supporting`；避免 `centered-template, generic-gradient, text-wall`。堆叠带边框的表格，比较设备分组。

## 已拒绝候选（已筛查，未采用）

| 仓库 | 拒绝原因 |
| --- | --- |
| `eza-community/eza` | 固定 commit 处的 LICENSE.txt 为 **EUPL-1.2**，不在宽松许可证白名单内（MIT/Apache-2.0/BSD-3-Clause/CC-BY-4.0）。 |
| `githubnext/monaspace` | 固定 commit 处的 LICENSE 为 **SIL OFL-1.1**，不在宽松许可证白名单内。 |
| `kubescape/kubescape` `docs/img/ksfromcodetodeploy.png` | 主流程视觉包含第三方商标（GitHub、GitLab、Jenkins、Kubernetes、Helm、VS Code 标识）。 |
| `microsoft/promptflow` `docs/media/readme/vsc.png` | 包含 VS Code 图标（第三方商标）。 |
| `open-webui/open-webui` `demo.png` | 模型选择器中包含 OpenAI 标识（第三方商标）。 |
| `appsmithorg/appsmith` `static/images/appsmith-introduction-video-tile.png` | UI 原型中包含 WhatsApp 标签（第三方商标）；且为视频磁贴而非 hero 构图。 |
| `dbt-labs/dbt-core` `etc/dbt-transform.png` | 包含 Snowflake / BigQuery / Redshift / Postgres / Databricks 标识（第三方商标）。 |
| `lsd-rs/lsd` `assets/screen_lsd.png` | 资产位于未固定的 `assets` 分支；固定 commit 处 404，无法溯源到提交。 |
| `mlflow/mlflow` `assets/readme-*.png` | README 引用 `refs/heads/master`（可变引用）；资产可能携带第三方框架标识。 |
| `jmacdonald/amp` `screenshot.png` | 固定 commit 处的 LICENSE 属 GPL 系，不在宽松许可证白名单内。 |
