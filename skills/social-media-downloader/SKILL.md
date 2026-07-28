---
name: social-media-downloader
schema_version: 2
version: 2.1.0
description: >
  识别当前消息中的抖音、TikTok、YouTube 或 Telegram 链接，创建可续传下载任务并按群聊实测上限发送原始画质视频或原图。支持分享文案、短链接、图集、媒体组、Shorts、频道和播放列表；用户查询进度、继续下载、继续发送、重发上一段或取消任务时也必须使用。
author: 风
license: Apache-2.0
homepage: https://xiaoguiwucan.github.io/LightAgent-SkillHub/
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 2.1.0
max_lightagent_version: null
platforms: [linux, darwin, windows]
category: media
tags: [douyin, tiktok, youtube, telegram, video, gallery, download]
status: active
publisher: community
release_notes: |
  修复 Telegram 单条媒体已经下载到持久目录后，tdl 因 --skip-same 跳过重复文件并返回非零，技能却误报下载失败的问题。
  单条任务现在会在启动下载器前复用同平台、同作品 ID 的已完成文件，并重新执行媒体校验、分段和发送准备。
  Telegram range:N 等范围任务不会复用不完整缓存，仍按用户指定范围逐项下载。
  新增发送成功确认和两分钟延迟清理：最后一段经 send 成功后调用 confirm_delivery，120 秒后删除下载原文件和分段文件。
  多用户复用同一媒体时保留共享文件，直到所有相关任务均完成发送确认，避免提前删除其他用户待发送内容。
breaking_changes: []
requirements:
  env: []
  bins: [python3, ffmpeg, ffprobe]
  python: [yt-dlp==2026.7.4, gallery-dl==1.32.8]
  npm: []
  downloads: []
  capabilities: [media-processing]
lightagent:
  network_domains: [v.douyin.com, www.douyin.com, m.douyin.com, www.iesdouyin.com, m.iesdouyin.com, aweme.snssdk.com, "*.douyinvod.com", "*.idouyinvod.com", "*.douyinpic.com", "*.douyinstatic.com", tiktok.com, www.tiktok.com, m.tiktok.com, vm.tiktok.com, vt.tiktok.com, youtube.com, www.youtube.com, m.youtube.com, youtu.be, googlevideo.com, "*.googlevideo.com", ytimg.com, "*.ytimg.com", t.me, telegram.me, www.t.me, api.telegram.org, github.com, objects.githubusercontent.com]
  file_paths: [<skill_data>, <temp>]
  tools: [skill_run, send]
  docker_notes: 需要 media-processing 能力中的 FFmpeg/ffprobe。yt-dlp 与 gallery-dl 安装在技能私有 Python 环境；Telegram 的 tdl v0.20.3 由管理员辅助程序按平台下载并校验官方 SHA-256。默认并发上限为 3。
  entrypoints:
    - name: prepare_media
      path: scripts/prepare_media.py
      runtime: python
      timeout_seconds: 600
      max_output_bytes: 65536
      max_memory_mb: 1024
      max_processes: 64
      arguments: {min_items: 1, max_items: 3, max_length: 4096}
    - name: continue_download
      path: scripts/continue_download.py
      runtime: python
      timeout_seconds: 600
      max_output_bytes: 65536
      max_memory_mb: 1024
      max_processes: 64
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: task_status
      path: scripts/task_status.py
      runtime: python
      timeout_seconds: 30
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: next_delivery
      path: scripts/next_delivery.py
      runtime: python
      timeout_seconds: 30
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: retry_delivery
      path: scripts/retry_delivery.py
      runtime: python
      timeout_seconds: 30
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: confirm_delivery
      path: scripts/confirm_delivery.py
      runtime: python
      timeout_seconds: 180
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: cancel_task
      path: scripts/cancel_task.py
      runtime: python
      timeout_seconds: 30
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 1, max_items: 1, max_length: 128}
    - name: telegram_status
      path: scripts/telegram_status.py
      runtime: python
      timeout_seconds: 30
      max_output_bytes: 65536
      max_memory_mb: 256
      max_processes: 4
      arguments: {min_items: 0, max_items: 0, max_length: 128}
---

# 多平台媒体下载

只处理当前入站消息中的第一个受支持链接，不从历史摘要重建 URL。下载的是平台实际提供的最高可用原始媒体；不缩放、不降码率、不把原图转为 JPEG。

## 新任务

检测到抖音、TikTok、YouTube 或 Telegram 链接后，调用 `prepare_media`：

```json
{"skill_name":"social-media-downloader","entrypoint":"prepare_media","arguments":["<当前完整消息>","<提问人显示名>"]}
```

单作品必须省略第三项。YouTube 频道或播放列表可在第三项传 `1` 至 `20`；Telegram 只有用户明确要求连续消息范围时才传 `range:N`，例如 `range:5`，禁止为单条消息传裸数字 `5`。分享文案必须逐字传入，不要由模型先提取、改写或脱敏 URL。

- 返回 `status: ready` 且 `delivery_parts` 非空：立即调用 `next_delivery`，再把其 `file` 交给 `send`。发送说明使用入口返回的 `message`。只有 `send` 明确成功后才调用 `confirm_delivery`；发送失败时不得确认。
- 返回 `status: ready` 但没有 `delivery_parts`：媒体已下载，但群发阈值尚未实测，不调用 `send`。
- 返回 `status: download_pending`：说明本轮已保存断点，并告知任务 ID。用户可查询或继续。
- 返回失败：原样区分链接、依赖、Telegram 登录、磁盘、平台解析和下载错误，不调用 `send`。

默认一个频道或播放列表取 5 项，用户可指定 1 至 20 项。若用户要求整个频道、无限历史或超过 20 项，先要求缩小范围。

## 任务控制

- “查询下载进度”：用最近一次属于该提问人的任务 ID 调用 `task_status`，返回状态、百分比、已下载/总大小、速度、预计剩余时间和错误。
- “继续下载”：调用 `continue_download`。Runner 单轮上限为 600 秒，技能会在 540 秒主动保存断点；不得启动后台守护进程规避限制。
- “继续发送”：调用 `next_delivery`，每轮只将一个 `file` 交给 `send`。文件名和说明会标识提问人与第 X/N 段。
- “重发上一段”：调用 `retry_delivery`，再发送其 `file`。
- 每次 `send` 成功后立即调用 `confirm_delivery`。中间分段只记录确认并立即返回；最后一段确认后入口等待 120 秒，删除下载原文件和全部分段文件。不得在 `send` 失败时调用。
- “取消下载”：调用 `cancel_task`。取消会删除该媒体任务的临时和下载文件。

任务 ID 必须来自本次会话的入口返回结果，不猜测、不枚举其他用户任务。

## Telegram

先调用 `telegram_status`。未配置时只提示管理员按 `references/telegram-login.md` 在 LightAgent 宿主机或容器终端扫码登录；群成员不能触发安装、登录、退出或切换账号。

登录账号可下载它有权访问的公开频道、已加入私有频道、`t.me/c/...` 消息、受保护会话媒体和同一 `grouped_id` 媒体组。不得自动加入邀请、访问无权内容、自毁或付费内容，也不得无限遍历频道历史。

单个 Telegram 消息链接只下载该消息及其同一媒体组。只有用户明确提出连续消息数量时，才在 `prepare_media` 第三项传 `range:N`；不要把默认合集数量用于 Telegram。

## 画质、分段与群发

- YouTube 分离音视频由下载器调用 FFmpeg 合并；分段使用 `-c copy`，不重新编码。
- 下载完成后用 ffprobe 验证编码、宽高、帧率、音轨、码率和时长。分段总时长异常或任一文件超限时不发送。
- 群发硬上限来自 `<skill_data>/transport-profile.json` 的 `verified_max_send_bytes`，切段目标为其 95%。这只是当前部署的实测值，不是微信官方限制。
- 尚无成功实测档位时禁用自动群发。实测必须依次为 20、50、100、200、500、1000 MiB，每档连续成功接收和播放两次；首次失败立即停止，取此前最高成功档位。
- 技能默认并发上限是 3；LightAgent 的群消息线程或会话串行策略可能进一步降低实际并发。
- 当前 LightAgent 每次请求只发送一个文件，文件消息不能真正携带 @；因此多段媒体必须逐次“继续发送”，并由文件名标识提问人。

## 安全边界

- 仅接受声明平台的 HTTPS 地址和标准 443 端口，不接受内网、凭据 URL、任意下载地址或远程安装脚本。
- 只下载用户主动提供且其有权访问的内容。不得绕过账号权限、付费、地区、版权或平台访问控制。
- Telegram 会话、手机号、验证码和二次验证密码不得进入对话、日志、技能包或配置备份。
- 不使用 `browser`、`web_fetch` 或 shell 替代 Runner 下载，不把远程 URL 直接交给 `send`。

依赖来源、固定版本与校验值见 `references/upstreams.md`；Telegram 管理员配置见 `references/telegram-login.md`。
