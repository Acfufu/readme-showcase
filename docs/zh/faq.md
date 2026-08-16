# 常见问题

## `readme-showcase` 做什么？

它从仓库证据出发重新设计 GitHub 仓库主页。单一 README Agent 扫描目标仓库、
检索有许可证的编辑模式、编写项目原生文案与视觉、检查每一个公开字节，最后停在
带指纹的本地预览。

## 它会发布到 GitHub 吗？

不会。评估通过只授权本地审查。提交、推送、发布与创建 Pull Request 始终需要
另行明确批准。运行 `build-pr-bundle` 只创建带指纹的本地交付。

## 什么算作结果 README 中的有效声明？

目标仓库公开声明只能来自仓库证据。如果目标仓库没有展示某种行为，README 就
不会声明它。

## 什么是可选的视觉-LLM 评审？

`screenshot-gate` 的 `--review` 轨道会针对视觉模型运行可选的美学评审。它通过
`skill/.env` 文件配置：在该文件中设置 `VISION_REVIEW_API_KEY`、
`VISION_REVIEW_MODEL` 与 `VISION_REVIEW_API_BASE`，或者不设置以回退到主机
会话评审。进程环境变量优先于文件值。

## 评审设置放在哪里？

`.env` 只放在一个位置：`skill/.env`，与 `skill/.env.example` 同目录。只有
`VISION_REVIEW_` 前缀的键才会从文件读取（或写入）。当文件包含密钥时，请用
`chmod 600 skill/.env` 收紧权限。

## 运行状态保存在哪里？

运行状态保存在目标仓库外部的
`${CODEX_HOME:-$HOME/.codex}/state/readme-showcase/`；不会创建每次 run 专属的
虚拟环境，也不会把状态目录放在目标旁边。

## 图片加载失败会怎样？

README 仍可使用：命令、前置条件、限制、链接与易变事实都保留为可搜索
Markdown。

## 支持哪些平台？

Codex 已正式支持并验证项目级与用户级安装。Claude Code 能识别
`.claude/skills` 下的 Skill（已通过 Claude Code 2.1.222 的 audit-only 运行时
验收）；OpenCode 能识别当前 `.agents/skills` 项目级安装（已通过
OpenCode 1.18.13 的 audit-only 运行时验收）。当前安装器面向 Codex 路径。

## 如何报告问题？

在 <https://github.com/Acfufu/readme-showcase/issues> 提交 GitHub issue，
报告硬门禁失败、规则建议或覆盖决策。
