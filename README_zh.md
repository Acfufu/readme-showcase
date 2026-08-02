<p align="center">
  <img src="./assets/readme/hero-zh.gif" width="100%" alt="README Showcase 将仓库证据整理成经过验证、可供审查的 GitHub 项目主页">
</p>
<p align="center"><sub><a href="./assets/readme/hero-zh.svg">静态 SVG</a> · 证据约束的 README 设计 · 默认停在本地</sub></p>

<p align="center">
  <a href="./README.md">English</a> · <strong>简体中文</strong>
</p>

`readme-showcase` 是面向仓库维护者的 Codex Skill。它读取当前代码库，
选择符合项目类型的叙事，只生成有证据支持的文案与视觉，验证结果，
最后停在带指纹的本地交付。

<p align="center">
  <a href="#快速开始"><strong>快速开始</strong></a> ·
  <a href="#从证据到交付"><strong>工作流</strong></a> ·
  <a href="#选择模式"><strong>模式</strong></a> ·
  <a href="#安全边界"><strong>安全</strong></a>
</p>

> [!IMPORTANT]
> 评估通过只授权本地预览。提交、推送、发布和 Pull Request 始终需要另行明确批准。

## 从证据到交付

![仓库事实与有许可证的编辑模式经过单一 README Agent、声明与资产门禁，形成带指纹的本地交付](assets/readme/workflow-zh.svg)

_ELK 负责图布局，项目代码负责序列化并验证 SVG。Skill 仍负责全部声明、
标签、调色板、说明文字与发布边界。_

| 你的目标 | Skill 的动作 | 你得到的结果 |
| --- | --- | --- |
| 基于真实项目的主页 | 编写前扫描仓库证据 | 每条声明都可追溯的 README 文案 |
| 属于当前项目的视觉系统 | 从仓库语义提取叙事、调色板、字体和构图 | 可编辑静态源与可选 GitHub 安全 GIF |
| 安全的审查边界 | 审计链接、命令、资产、多语言和硬门禁 | 评估报告与带指纹的本地 bundle |

检索 dataset 包含 12 条有许可证、经过人工审查的抽象模式：10 条可用于生产检索，
两条保持隔离测试。模式只指导编辑结构，永远不会变成目标仓库的事实。

## 快速开始

环境要求：macOS 或 Linux、Python 3.10+ 与 Codex。默认路径不需要第三方
Python 依赖。

```bash
npx --yes github:Acfufu/readme-showcase
npx --yes github:Acfufu/readme-showcase --check
```

可观察的成功状态：

```text
"status":"installed"
"status":"current"
```

首次发布到 npm 后，可将 `github:Acfufu/readme-showcase` 缩短为
`readme-showcase`。新建 Codex 任务让 Skill discovery 重新加载，然后运行：

```text
$readme-showcase 围绕已验证行为和可运行的快速开始，重新设计这个仓库的 README。
```

第一个可见动作是检查仓库。如果范围不清楚，Skill 会询问要重做完整 README、
只制作资产，还是只做审计。

## 选择模式

| 模式 | 变更 | 适用场景 |
| --- | --- | --- |
| README | 阅读顺序、文案、证明、Markdown 与必要视觉 | 完整 GitHub 项目主页 |
| 仅资产 | 指定 Hero、图表、徽章或成套资产 | 不改变 README 内容的视觉工作 |
| 仅审计 | 只输出发现与证据 | 真实性、安全、多语言和发布准备检查 |

动效与 Hybrid 栅格合成需要明确选择，不是独立模式。ELK 是可选能力，
只用于关系密集的 `architecture`、`flowchart` 或 `c4` 正文图。

## 本地流水线如何工作

1. **校验** 固定版本的检索 manifest 与许可证证据。
2. **扫描** 目标仓库，生成确定性的证据事实。
3. **检索** 最多五条仅 train 编辑模式。
4. **编写** README、声明映射、资产清单与必要视觉。
5. **评估** 声明、链接、命令、资产、可访问性和多语言。
6. **交付** 全部硬门禁通过后，生成带指纹的本地 bundle。

安装后的 Skill 提供八条确定性流水线命令：

```text
validate-dataset  scan  retrieve  validate-bundle  evaluate
import-benchmark  build-pr-bundle  check-publish-gate
```

<details>
<summary><strong>可选 ELK 与动效边界</strong></summary>

<br>

- Skill 内置经过哈希验证的 `elkjs@0.9.3` bundle；ELK 路线要求精确的
  Node `22.22.3`。
- Adapter 接收严格语义 JSON，在两个全新进程中渲染，只接受字节完全相同、
  独立且 GitHub 安全的 SVG。
- 已验收 SVG 不做后处理。任何不一致都会选择项目自有静态 fallback，
  保持最近一次可靠资产不变。
- GIF 动效从已批准的静态 SVG 生成；可编辑 SVG 与 motion JSON 和派生 GIF 共存。
- 精确的 ELK bundle、包元数据与 EPL-2.0 许可证随 Skill 安装；不会安装
  `node_modules`，也不依赖 Docker、凭据或运行时下载。

</details>

## 安全边界

- 仓库证据决定公开声明。
- 检索模式只是编辑参考，不是目标事实。
- 命令、配置、限制与易变信息保留在可搜索的 Markdown 中。
- 带文字的视觉资产按 README 语言分别本地化。
- 静态 SVG 是所有视觉路线的确定性 fallback。
- 评估通过不会授予远程写入权限。
- 只有精确批准 envelope、当前 base SHA 与最新远端预检齐备后，发布 connector
  动作才可能符合条件。

## 本地验证

```bash
python3 skill/scripts/readme_pipeline.py validate-dataset \
  --manifest dataset/retrieval/manifest.json
python3 skill/scripts/audit_readme.py README.md
python3 skill/scripts/audit_readme.py README_zh.md
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

生成动效还需要 Pillow、`ffmpeg`，以及 `rsvg-convert` 或 macOS `sips`。
经过验证的 ELK 渲染使用 Node `22.22.3` 与
[`skill/references/elk-structure.md`](skill/references/elk-structure.md)
记录的内置文件。

## 仓库地图

```text
skill/
├── SKILL.md                 # 单 Agent 工作流与范围门禁
├── agents/openai.yaml       # Codex discovery 元数据
├── references/              # 叙事、视觉、动效与 ELK 规则
├── scripts/                 # 扫描、检索、评估、审计与渲染器
└── vendor/elkjs/            # 固定 ELK bundle、元数据、EPL-2.0 许可证
dataset/retrieval/manifest.json
scripts/install_skill.py     # 原子安装与升级回滚
package.json                 # npx 包入口
tests/                       # 确定性契约与失败场景
```

## 许可证与来源边界

项目采用 [GNU General Public License v3.0](LICENSE)。

视觉与动效流程采用了
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme)
的 MIT 许可规则；保留的声明位于
[`skill/references/motion-production.md`](skill/references/motion-production.md#upstream-license)。
内置的 `elkjs@0.9.3` 文件继续适用 `EPL-2.0`；未修改的许可证位于
[`skill/vendor/elkjs/LICENSE.md`](skill/vendor/elkjs/LICENSE.md)。
