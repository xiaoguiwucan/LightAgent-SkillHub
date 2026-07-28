---
name: douyin-video-share
schema_version: 2
version: 1.0.0
description: >
  识别当前消息中的单个抖音视频分享链接，下载公开视频并通过 send 发送到当前 Web、微信群或其他会话。当用户消息包含 v.douyin.com 短链接、iesdouyin.com 分享页或 douyin.com/video 链接时必须使用；即使用户只粘贴分享文案而没有明确说“下载”也要自动处理。仅处理单个公开视频，不处理主页、合集、直播、图集或批量下载。
author: 风
license: Apache-2.0
homepage: https://xiaoguiwucan.github.io/LightAgent-SkillHub/
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 2.1.0
max_lightagent_version: null
platforms: [linux, darwin, windows]
category: media
tags: [douyin, video, download, wechat-group, share-link]
status: active
publisher: community
release_notes: 首次发布；支持自动识别单个抖音分享链接、受限下载和原会话视频发送。
breaking_changes: []
requirements:
  env: []
  bins: [python3, ffmpeg]
  python: []
  npm: []
  downloads: []
  capabilities: [media-processing]
lightagent:
  network_domains: [v.douyin.com, www.douyin.com, m.douyin.com, www.iesdouyin.com, m.iesdouyin.com, aweme.snssdk.com, "*.douyinvod.com", "*.idouyinvod.com"]
  file_paths: [<workspace>/videos/douyin-video-share]
  tools: [skill_run, send]
  docker_notes: 需要 media-processing 能力；单个视频最大下载 200 MiB，超过 20 MiB 时生成不超过 20 MiB 的群聊发送版本，文件写入 LightAgent workspace。
  entrypoints:
    - name: download_video
      path: scripts/download_video.py
      runtime: python
      timeout_seconds: 600
      max_output_bytes: 65536
      max_memory_mb: 512
      max_processes: 64
      arguments:
        min_items: 3
        max_items: 3
        max_length: 4096
---

# 抖音视频分享下载

只处理当前消息里的第一个抖音公开视频链接。检测到链接后直接执行下载，不先询问用户，也不扫描历史消息。

## 执行

1. 从当前消息保留完整分享文案，不要手工改写或解析链接。
2. 调用 `skill_run` 的 `download_video` 入口：

```json
{"skill_name":"douyin-video-share","entrypoint":"download_video","arguments":["<当前完整消息>","--output-root","<workspace>"]}
```

3. 脚本返回 `ok: true` 后，立即调用一次：

```json
{"path":"<video_file>","message":"抖音视频下载完成"}
```

4. `send` 成功后只回复“视频已发送”，不要再发送下载地址或重复发送文件。

`<workspace>` 是 LightAgent workspace；官方 Docker 默认为 `/home/agent/lightagent`。下载文件固定保存到 `<workspace>/videos/douyin-video-share/<aweme_id>.mp4`。

## 微信群自动触发

技能不会自行修改 LightAgent 配置。管理员安装后需要在微信群设置中完成一次配置：

1. 将本技能的微信群权限设为“所有群成员可用”，或只授权需要使用的群。
2. 在“非 @ 主动回复”的强触发关键词中加入 `v.douyin.com` 和 `douyin.com/video/`。
3. 将强触发关键词规则分值设为不低于当前活跃度阈值；默认“普通”活跃度建议设为 `50`。
4. 开启非 `@` 主动回复的智能评分器，使强触发关键词跳过通用接话判断。
5. 为目标群开启非 `@` 主动回复。

完成后，群成员直接粘贴抖音分享文案即可自动触发；没有完成这些配置时，仍可通过 `@机器人 + 抖音链接` 使用。强触发关键词只负责把消息送入已安装技能，不会放宽技能的下载域名、文件体积或单视频限制。

## 约束

- 每轮最多调用一次 `skill_run` 和一次 `send`。
- 只接受 HTTPS 抖音域名，脚本会逐跳校验重定向并拒绝其他站点、内网地址和非标准端口。
- 单个源视频上限为 200 MiB；超限时不保留半成品。源视频超过 20 MiB 时，使用已声明的 `media-processing` 能力生成约 18 MiB 的群聊发送版本。
- 不处理用户主页、合集、直播、图集、音乐、评论或批量链接。
- 不使用 Cookie，不登录抖音，不绕过私密、好友可见、地区或账号访问限制。
- 不改用 `browser`、`web_fetch` 或 Bash 下载，也不把远程 URL 直接传给 `send`。
- 只下载用户在当前消息中主动提供的公开视频；提示用户尊重作者版权和平台规则。

实现方式参考活跃的 MIT 开源项目 [`jiji262/douyin-downloader`](https://github.com/jiji262/douyin-downloader)，具体来源记录见 `references/upstream.md`。本技能脚本为面向 LightAgent Runner 的独立最小实现，不包含该项目的批量下载、登录或浏览器回退代码。

## 失败处理

- `missing_url` 或 `unsupported_url`：提示用户发送完整的抖音 HTTPS 分享链接。
- `unsupported_item_type`：说明当前只支持单个视频，不处理图集、直播、主页或合集。
- `video_too_large`：说明视频超过 200 MiB，未下载也未发送。
- `missing_media_processing`、`transcode_failed` 或 `transcode_timeout`：说明群聊发送版本生成失败，不发送过大的源文件。
- `download_failed`、`invalid_video` 或页面结构变化：返回脚本中的简短错误，不尝试其他解析站或第三方接口。
- 任何失败都不得调用 `send`，不得声称下载或发送成功。
