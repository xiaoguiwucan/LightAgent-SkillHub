# 贡献指南

## 基本要求

- 技能目录必须为 `skills/<name>`，名称只允许小写字母、数字和连字符。
- 必须包含 `SKILL.md`，版本遵循 SemVer，许可证使用 SPDX 表达式，`category` 使用稳定的小写分类名。
- 一个 PR 原则上只新增或更新一个技能。
- PR 必须使用中文说明功能、测试、依赖、访问范围和兼容平台。
- 不得提交密钥、个人数据、未获授权的代码或素材，也不得包含隐藏遥测。
- 不得使用 LightAgent 内置技能名、核心命令、工具名或 `lightagent-*` 官方命名空间。

## 版本规则

- 行为兼容的修复提升 patch。
- 新增兼容能力提升 minor。
- 破坏性修改提升 major，并在变更说明中给出迁移方法。
- 已发布版本内容不可覆盖；修改必须发布新版本。

## 依赖与权限

所有环境变量、二进制、Python、npm 和下载依赖必须声明。下载依赖必须使用 HTTPS 并提供 SHA-256。需要系统包、管理员权限、执行下载脚本、写工作区外目录或访问外部域名时，必须在技能正文和对应权限字段中明确说明。

Schema v2 的脚本技能必须在 `lightagent.entrypoints` 声明结构化入口，并在正文中要求 LightAgent 通过 `skill_run` 调用。不得要求 Agent 使用 Bash、Python 或 Node 命令直接执行技能目录中的脚本。`requirements.env` 只声明技能自身需要的变量，不得声明 `PATH`、`PYTHONPATH`、`HOME`、`TMPDIR` 或 `LIGHTAGENT_*` 等 Runner 保留变量。系统组件通过 `requirements.capabilities` 声明，不得提交 apt、brew、sudo 等运行时安装命令。

提交 PR 即声明你有权按所填许可证发布全部代码和素材，并同意项目按安全、版权或质量原因撤销版本。
