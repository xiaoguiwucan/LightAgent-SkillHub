# 变更记录

## 2026-07-28

### Last 30 Days 3.18.3

- 基于 `mvanhorn/last30days-skill` `v3.18.3` 制作 LightAgent 适配包，保留 MIT 许可证和上游来源说明。
- 新增 Schema v2 `research`、`doctor` 与 `library_search` 入口，统一通过 `skill_run` 执行，不要求 Agent 直接运行 Bash 或解释器命令。
- 将上游引擎兼容到官方 Docker 的 Python 3.10，配置、缓存、SQLite 与研究文件固定保存到技能私有目录，不在运行时下载 Python 或安装系统包。
- 默认关闭宿主浏览器 Cookie读取，禁止公开 HTML 发布、任意输出路径和文件型计划参数；可选来源凭据只从已声明环境变量注入。
- 增加模拟研究、路径隔离、参数拒绝、元数据、许可证及官方 Docker 非 root 运行测试。
- Registry 打包统一忽略 `__pycache__`、`.pyc` 和 `.pyo`，防止本地验证产物进入技能 ZIP。

## 2026-07-27

### GitHub 项目助手 1.0.0

- 新增 `skills/github-project-assistant/`，使用 Schema v2，最低支持 LightAgent `1.1.0`。
- 技能仅调用 LightAgent 内置 `github_project` 和 `scheduler` 工具，不携带 GitHub API 脚本、PAT 或依赖安装代码。
- 支持多项目查询、中文动态汇总、Issue 确认提交、PR 本地审查与严格合并，以及 Skill Hub 发布上架跟踪。
- 新增评测用例，覆盖 Issue 二次确认、PR Head SHA/CI 变更、不可信 diff 与发布状态表达。
