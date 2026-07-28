---
name: douyin-video-share
schema_version: 2
version: 1.1.0
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
release_notes: 优先请求页面声明的高规格来源，使用 ffprobe 检测实际宽高、帧率、编码、码率和时长；旧低清缓存会自动刷新，所有视频均保持下载原文件发送，不再转码压缩。
breaking_changes: []
requirements:
  env: []
  bins: [python3, ffprobe]
  python: []
  npm: []
  downloads: []
  capabilities: [media-processing]
lightagent:
  network_domains: [v.douyin.com, www.douyin.com, m.douyin.com, www.iesdouyin.com, m.iesdouyin.com, aweme.snssdk.com, "*.douyinvod.com", "*.idouyinvod.com"]
  file_paths: [<workspace>/videos/douyin-video-share]
  tools: [skill_run, send]
  docker_notes: 需要 media-processing 能力中的 ffprobe；单个视频最大下载 200 MiB，技能不转码压缩。当前微信 Web 协议对超过约 25 MB 的文件缺少稳定兼容保证，大文件可能发送失败。
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

1. 从当前消息保留完整分享文案，不要手工改写、总结、转义、脱敏或解析链接。只要当前消息含有字面量 `https://v.douyin.com/`、`https://www.douyin.com/video/` 或其他已声明抖音 HTTPS 地址，就必须先原样调用入口；不得自行回复“链接无效”“地址被本地化”。
2. 调用 `skill_run` 的 `download_video` 入口：

```json
{"skill_name":"douyin-video-share","entrypoint":"download_video","arguments":["<当前完整消息>","--output-root","<workspace>"]}
```

3. 脚本返回 `ok: true` 后，检查 `output_width`、`output_height`、`fps`、编码、码率、`size_bytes` 和 `large_file_compatibility_warning`，然后将原文件立即调用一次：

```json
{"path":"<video_file>","message":"抖音视频下载完成"}
```

4. `send` 成功后只回复“视频已发送”，不要再发送下载地址或重复发送文件。若大文件发送失败，明确说明当前微信通道可能不支持该原始文件大小；不得改为压缩后重发。

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
- `skill_run` 的第一项参数必须逐字来自当前入站消息，不能从近期摘要、引用摘要或模型复述中重建 URL。用户只说“这个”而当前消息没有完整链接时，要求重新发送完整分享文案，不能把摘要中的 `http[local-path]` 当作原链接。
- 只接受 HTTPS 抖音域名，脚本会逐跳校验重定向并拒绝其他站点、内网地址和非标准端口。
- 优先请求页面视频 ID 对应的 1080p 无水印地址，并以 `ffprobe` 的真实宽高为准；如果接口拒绝请求或实际清晰度低于页面声明，再回退页面提供的地址。抖音匿名接口可能降级清晰度，`quality_preserved` 会如实标记，技能不宣称得到平台未实际提供的规格。
- 缓存文件低于页面声明的分辨率时自动重新下载，避免继续发送旧版 720p 低清缓存。
- 单个源视频上限为 200 MiB；超限时不保留半成品。下载后的原 MP4 直接交给 `send`，任何大小都不转码、不缩放、不降低码率。
- 20 MiB 是旧版技能的保守阈值，并非已确认的微信官方硬限制。Wechaty 历史 Web 协议资料表明超过约 25 MB 的文件需要额外上传签名；当前 LightAgent 通道未确认支持该流程，因此脚本对超过 24 MiB 的原文件标记兼容性警告，但仍按用户要求尝试原版发送。
- 不处理用户主页、合集、直播、图集、音乐、评论或批量链接。
- 不使用 Cookie，不登录抖音，不绕过私密、好友可见、地区或账号访问限制。
- 不改用 `browser`、`web_fetch` 或 Bash 下载，也不把远程 URL 直接传给 `send`。
- 只下载用户在当前消息中主动提供的公开视频；提示用户尊重作者版权和平台规则。

实现方式参考活跃的 MIT 开源项目 [`jiji262/douyin-downloader`](https://github.com/jiji262/douyin-downloader)，具体来源记录见 `references/upstream.md`。本技能脚本为面向 LightAgent Runner 的独立最小实现，不包含该项目的批量下载、登录或浏览器回退代码。

## 失败处理

- `missing_url` 或 `unsupported_url`：提示用户发送完整的抖音 HTTPS 分享链接。
- `unsupported_item_type`：说明当前只支持单个视频，不处理图集、直播、主页或合集。
- `video_too_large`：准确说明原版视频超过 200 MiB 安全上限；这是文件大小问题，不得误报为没有有效 HTTPS 链接。若用户坚持不压缩，则无法通过本技能发送该视频。
- `missing_media_processing`：说明缺少 `ffprobe`，无法确认真实视频规格，因此不发送未经检测的文件。
- `download_failed`、`invalid_video` 或页面结构变化：返回脚本中的简短错误，不尝试其他解析站或第三方接口。
- 任何失败都不得调用 `send`，不得声称下载或发送成功。
