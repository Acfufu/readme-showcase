# 快速开始

`readme-showcase` 是从仓库证据出发重新设计 GitHub 仓库主页的 Codex Skill，不会
虚构产品事实。本页介绍环境要求、安装方式、首次运行，以及可选配置文件的位置。

## 环境要求

- macOS 或 Linux
- Python 3.11+
- Codex

默认运行不增加第三方 Python 运行时依赖。

## 安装

### 方式一 · CLI

```bash
# 中文安装检查
npx --yes github:Acfufu/readme-showcase skills install
npx --yes github:Acfufu/readme-showcase skills check
```

交互式安装会检测已有范围；自动化可以明确选择：

```bash
# 中文明确范围
npx --yes github:Acfufu/readme-showcase skills install --project --yes
npx --yes github:Acfufu/readme-showcase skills install --user --yes
```

项目级写入 `.agents/skills/readme-showcase`；用户级写入
`${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase`。可观察的成功状态依次为
`"status":"installed"` 与 `"status":"current"`。使用相同范围的
`skills update` 更新已有安装。原有无参数安装与 `--check` 调用继续兼容。

### 方式二 · 直接交给代理

把下面这句话发给编程代理：

```text
请安装这个 Skill：https://github.com/Acfufu/readme-showcase
```

代理应确认范围，运行官方安装器与 `skills check`，再报告安装路径和状态。

## 首次运行

新建 Codex 任务，让 Skill discovery 重新加载，然后运行：

```text
$readme-showcase shape [target]
```

`shape` 梳理证据、叙事、范围与视觉方向；它等待批准，不创建候选文件。其余
命令——`audit`、`redesign`、`polish` 与 `visualize`——在
[README](../README_zh.md) 中有说明。

## `.env` 文件

`.env` 只放在一个位置：`skill/.env`，与 `skill/.env.example` 同目录。首次使用
时会以 `0600` 权限在该位置自动创建；进程环境变量始终优先于从文件读取的值。

如果手动创建或编辑该文件，请收紧权限——文件可能包含密钥：

```bash
chmod 600 skill/.env
```

只有以 `VISION_REVIEW_` 为前缀的键才会从 `.env` 文件读取（或写入）。`.env`
文件永不发布；`.env.example` 才是提交到仓库的模板。

## 下一步

- [常见问题](faq.md)
- [路线图](roadmap.md)
- [项目定位](project-positioning.md)
