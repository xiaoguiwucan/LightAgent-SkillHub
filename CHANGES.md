# 变更记录

## 2026-07-28

### 抖音视频分享下载 1.0.0

- 新增 `skills/douyin-video-share/`，识别单个抖音公开视频分享链接并通过原会话发送下载完成的视频。
- 使用 Schema v2 `download_video` Runner 入口，不要求 Agent 直接执行 Bash、Python 命令或访问第三方解析站。
- 下载器逐跳校验 HTTPS 抖音域名，限制页面与视频体积，校验 MP4 文件头并使用临时文件原子落盘；大文件通过声明的媒体能力生成适合微信群回传的发送版本。
- 新增解析、域名限制、重定向和真实入口流程测试，并记录参考开源项目的许可证、提交与更新日期。
- 文档补充微信群无 `@` 自动触发所需的技能 ACL、强触发关键词、规则分值和智能评分器配置。

## 2026-07-27

### GitHub 项目助手 1.0.0

- 新增 `skills/github-project-assistant/`，使用 Schema v2，最低支持 LightAgent `1.1.0`。
- 技能仅调用 LightAgent 内置 `github_project` 和 `scheduler` 工具，不携带 GitHub API 脚本、PAT 或依赖安装代码。
- 支持多项目查询、中文动态汇总、Issue 确认提交、PR 本地审查与严格合并，以及 Skill Hub 发布上架跟踪。
- 新增评测用例，覆盖 Issue 二次确认、PR Head SHA/CI 变更、不可信 diff 与发布状态表达。
