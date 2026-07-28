# 变更记录

## 2026-07-27

### GitHub 项目助手 1.0.0

- 新增 `skills/github-project-assistant/`，使用 Schema v2，最低支持 LightAgent `1.1.0`。
- 技能仅调用 LightAgent 内置 `github_project` 和 `scheduler` 工具，不携带 GitHub API 脚本、PAT 或依赖安装代码。
- 支持多项目查询、中文动态汇总、Issue 确认提交、PR 本地审查与严格合并，以及 Skill Hub 发布上架跟踪。
- 新增评测用例，覆盖 Issue 二次确认、PR Head SHA/CI 变更、不可信 diff 与发布状态表达。
