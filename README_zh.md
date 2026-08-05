<p align="center">
  <img src="./assets/readme/hero-zh.gif" width="100%" alt="README Showcase 将仓库证据交给单一 README Agent，形成已验证的本地候选，同时保持远端发布锁定">
</p>
<p align="center"><sub><a href="./assets/readme/hero-zh.svg">静态 fallback</a> · 输入仓库证据 · 输出可审查本地候选</sub></p>

<p align="center"><a href="./README.md">English</a> · <strong>简体中文</strong></p>

`readme-showcase` 是用于重新设计 GitHub 仓库主页的 Codex Skill，不会虚构产品
事实。它扫描目标仓库，以有许可证的编辑模式辅助组织结构，编写项目原生文案与视觉，
校验声明和资产，最后停在带指纹的本地预览。

> [!IMPORTANT]
> 评估通过只授权本地交付。提交、推送、发布和 Pull Request 仍需另行明确批准。

## 让访问者在第一分钟看懂项目

| 仓库提供 | 单一 README Agent 负责 | 你在本地审查 |
| --- | --- | --- |
| 已跟踪文件、命令、配置、测试 | 证据图谱、叙事顺序、文案、视觉方向 | README 候选与可编辑资产 |
| 当前 base SHA | 声明与 locale 绑定、硬门禁评估 | 离线预览与评估报告 |
| 仅 train 的许可模式 | 只用于编辑比较，不作为产品事实 | 带指纹 PR bundle，仍未发布 |

即使图片加载失败，README 仍可使用：命令、限制、链接和易变信息均保留为可搜索
Markdown。

## 安装、检查、调用

环境要求：macOS 或 Linux、Python 3.11+ 与 Codex。默认流程没有第三方 Python
依赖。

```bash
npx --yes github:Acfufu/readme-showcase
npx --yes github:Acfufu/readme-showcase --check
```

可观察的成功状态依次为 `"status":"installed"` 与 `"status":"current"`。
新建 Codex 任务以重新加载 Skill discovery，然后说明需要的范围和停止点：

```text
$readme-showcase 围绕已验证行为和可运行快速开始，重新设计这个仓库主页。使用动图。停在本地预览。
```

范围可选 `README`、`asset-only` 或 `audit-only`。动效与 Hybrid 栅格合成均需
明确选择。

## 从证据到锁定交付

![编辑模式只提供结构，仓库事实提供真值；单一 README Agent 评估已验证的本地 bundle，远端发布必须另行批准](assets/readme/workflow-zh.svg)

```text
扫描 → 检索 train 模式 → 计划 → 草拟 → 校验 → 评估 → 预览
```

- 公开产品声明只能来自仓库证据。
- 检索模式可以影响结构，不能替代目标事实。
- 带文字视觉与明确 locale 成对绑定。
- 门禁失败不能静默变成可发布状态。
- ELK 只可布局关系密集的正文图，不负责文案、视觉方向、验收或发布。

## 本仓库携带的证据

| 契约 | 当前证据 |
| --- | --- |
| 检索边界 | 22 条审查记录：20 条生产 `train`、2 条隔离 `test` |
| 本地化契约 | 7 个允许的 locale 标签，README 与文字资产显式配对 |
| 运行时契约 | Python 3.11+；可选 ELK 路线精确使用 Node 22.22.3 |
| 图形完整性 | 内置并校验哈希的 `elkjs@0.9.3`；无运行时下载 |
| 交付边界 | 候选回执、本地预览、评估报告、带指纹 PR bundle |

## 运行可恢复流水线

运行状态按目标仓库分组，集中保存在
`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`。目标仓库及其父目录不会出现
临时 run 目录。

```bash
python3.11 skill/scripts/readme_pipeline.py run \
  --root . \
  --mode readme \
  --project-type developer-tool \
  --locale en \
  --locale zh-Hans

python3.11 skill/scripts/readme_pipeline.py status
python3.11 skill/scripts/readme_pipeline.py resume
python3.11 skill/scripts/readme_pipeline.py preview
```

编排器记录八个有序阶段，并在需要明确计划或候选时等待，不会自行虚构内容。
`status`、`resume`、`explain` 与 `preview` 会定位当前仓库的最近一次运行；它复用
现有运行时，不创建每次 run 专属的虚拟环境。

<details>
<summary><strong>视觉路线与保留源文件</strong></summary>

<br>

| 路线 | 适用场景 | 保留源文件 |
| --- | --- | --- |
| `none` | Markdown 已能清楚解释项目 | Markdown |
| `static` | 项目专属 Hero 与紧凑图形 | 可编辑 SVG |
| `elk` | 关系密集的 architecture、flowchart 或 C4 正文图 | 语义 JSON + 已验证 SVG |
| `compiled` | 可选 Plan v3 desktop/mobile 投影 | Visual Spec + 不可变 Stage 6 产物 |

确定性的 Plan v3 路线在现有八阶段、单一 README Agent 流程内设置
`diagram_route: "compiled"`。不可变产物保存在
`stages/06-bundle-assemble/attempts/<attempt>/compiled/`：desktop 使用宽度
1,200 的 viewBox 并在 900 px 检查；mobile 独立规划，宽度不超过 720，并在
360 px 检查。这些 local-only 产物与交付 `dry-run` 只在本地运行，不会授予远端
权限；详见 [`visual-compiler.md`](skill/references/visual-compiler.md)。

GIF 动效从已批准 SVG 开始；SVG 与 motion JSON 和派生 GIF 并存。编译输出保留在
中央 run-state；`preview`、`build-pr-bundle` 与交付 `--dry-run` 均只在本地执行。

</details>

## 从源码验证

```bash
python3.11 skill/scripts/readme_pipeline.py validate-dataset --manifest dataset/retrieval/manifest.json
python3.11 skill/scripts/audit_readme.py README.md
python3.11 skill/scripts/audit_readme.py README_zh.md
python3.11 -m unittest discover -s tests -v
npm pack --dry-run
```

动图生成还需要 Pillow、`ffmpeg`，以及 `rsvg-convert` 或 macOS `sips`。可选 ELK
细节见 [`skill/references/elk-structure.md`](skill/references/elk-structure.md)。

## 仓库地图

```text
skill/
├── SKILL.md                 # 范围、证据与批准门禁
├── references/              # 叙事、视觉、动效、编译器、ELK
├── scripts/                 # 扫描、编排、审计、渲染器
└── vendor/elkjs/            # 固定 bundle 与 EPL-2.0 许可证
dataset/retrieval/manifest.json
scripts/install_skill.py     # 原子安装、备份、回滚
package.json                 # npx 入口
tests/                       # 契约、门禁、失败路径
```

## 许可证与来源边界

项目采用 [GNU General Public License v3.0](LICENSE)。视觉与动效规则采用 MIT
许可的 [`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme)；
声明见 [`motion-production.md`](skill/references/motion-production.md#upstream-license)。
内置 `elkjs@0.9.3` 继续适用 `EPL-2.0`；未修改的
[许可证](skill/vendor/elkjs/LICENSE.md)随 Skill 一同提供。
