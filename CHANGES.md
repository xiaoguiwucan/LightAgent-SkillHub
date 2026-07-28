# 变更记录

## 2026-07-28

### 多平台媒体下载 2.0.1

- 修复 TikTok 完整作品链接被误当短链预解析的问题；仅 `vm.tiktok.com` 和 `vt.tiktok.com` 进入受控重定向流程。
- 完整作品页现在直接交给 `yt-dlp`，避免 `urllib` 请求 TikTok 时因 TLS EOF 提前返回 `link_resolution_failed`。
- 增加完整作品链接跳过预解析、短链接继续执行域名受限重定向的回归测试。

### 多平台媒体下载 2.0.0

- 新增 `social-media-downloader`，统一识别抖音、TikTok、YouTube 和 Telegram 链接，支持分享文案、短链、图集、媒体组、Shorts、频道和播放列表。
- 新增持久任务清单、原子写入、`.part` 断点、进度/速度/剩余时间查询；Runner 在 540 秒主动保存断点，避免触碰 600 秒硬超时。
- 下载保持平台实际提供的原始规格；视频通过 FFmpeg stream copy 合并和分段，图集保持原图字节，不以压缩换取发送成功。
- 新增三任务并发槽、同作品互斥下载和每个请求独立发送游标；多段文件通过“继续发送”逐段交付，文件名包含提问人和第 X/N 段。
- 新增 Telegram `tdl v0.20.3` 管理员辅助安装与二维码登录，固定 Linux、macOS、Windows x64/arm64 官方产物 SHA-256，会话只保存在技能数据目录。
- 微信群发送上限改为部署实测配置：依次验证 20/50/100/200/500/1000 MiB，首个失败档位停止，切段目标为最高成功档位的 95%；没有成功档位时禁用自动群发。

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
