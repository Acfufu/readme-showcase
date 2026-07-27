# README Showcase

[English](README.md) | **简体中文**

面向 Codex 的证据驱动 GitHub README 设计 skill。

[![许可证：GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

`readme-showcase` 将仓库中已验证的行为整理成清晰的项目主页。它以
Markdown 保留可搜索内容，按项目类型选择章节，只在视觉内容能够证明项目
身份、输出、流程或架构时才创建资产。

## 能做什么

- 写作前先审计仓库证据。
- 明确选择 README 模式或仅资产模式。
- 按“价值 → 证明 → 机制 → 首次使用 → 细节”组织内容。
- 支持项目原生 SVG，以及明确选择后才生成的 GIF。
- 检查本地图片、替代文本和基础 SVG 兼容性。
- 删除无依据的声明、装饰性内容和未验证徽章。

## 工作流

![README Showcase 从仓库证据到可运行验证的五阶段双语流程图](assets/readme/workflow.svg)

| 阶段 | 结果 |
| --- | --- |
| 检查 | 受众、价值、证明、首次成功、限制与声明来源 |
| 选择 | 项目类型、受证据支持的章节与一种编辑模式 |
| 起草 | 证据驱动 Markdown，以及可观察的快速开始 |
| 展示 | 只在提升理解时创建项目原生静态资产 |
| 验证 | 声明、命令、链接、锚点、图片、SVG 与语言一致性 |

详细指导按需加载：

```text
skill/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── structure.md
│   ├── visual-production.md
│   └── motion-production.md
└── scripts/
    ├── audit_readme.py
    └── render_motion_gif.py
```

## 安装

```bash
git clone https://github.com/Acfufu/readme-showcase.git
cd readme-showcase

skill_target="${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase"
if [ -e "$skill_target" ]; then
  printf 'Target already exists: %s\n' "$skill_target"
else
  cp -R skill "$skill_target"
  printf 'Installed: %s\n' "$skill_target"
fi
```

首次安装预期输出：

```text
Installed: .../skills/readme-showcase
```

新建 Codex 任务，让 skill discovery 重新加载。

## 使用

显式调用：

```text
$readme-showcase 围绕已验证行为和可运行的快速开始，重新设计这个仓库的 README。
```

只生成一个视觉资产，不修改 README：

```text
$readme-showcase 根据这个仓库的真实架构，仅创建一个工作流图。
```

README 模式可以调整 README 结构和有依据的资产。仅资产模式不会修改
README；除非另外批准嵌入。生成 GIF 同样需要明确选择。

## 验证

审计生成的 README：

```bash
python3 skill/scripts/audit_readme.py /path/to/project/README.md
```

检查自带 Python 脚本：

```bash
python3 -m py_compile skill/scripts/audit_readme.py skill/scripts/render_motion_gif.py
```

`audit_readme.py` 只使用 Python 标准库。动态渲染还需要 Pillow、`ffmpeg`，
以及 `rsvg-convert` 或 macOS `sips`。

## 边界

- 仓库证据决定公开声明和章节。
- 动画只是可选输出，不是默认模式。
- 命令和频繁变化的事实保留在 Markdown 中，不写进图片。
- 生成资产默认使用目标仓库的 `assets/readme/` 约定。
- 发布、提交、推送和远程修改仍需明确授权。

## 许可证

项目采用 [GNU General Public License v3.0](LICENSE)。

自带渲染与审计脚本包含基于
[`oil-oil/beautify-github-readme`](https://github.com/oil-oil/beautify-github-readme)
改编的内容。上游 MIT 署名与许可证全文保留在
[`skill/references/motion-production.md`](skill/references/motion-production.md#upstream-license)。
