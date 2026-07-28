# 上游依赖与固定版本

核对日期：2026-07-28。技能不运行远程安装脚本。

## 抖音参考实现

- 项目：`jiji262/douyin-downloader`
- 地址：https://github.com/jiji262/douyin-downloader
- 许可证：MIT
- 用途：参考分享页解析、作品类型和最高可用媒体选择；技能不复制其批量账号功能。

## yt-dlp

- 项目：https://github.com/yt-dlp/yt-dlp
- 版本：`2026.07.04`，Python 依赖声明为 `yt-dlp==2026.7.4`
- 许可证：Unlicense
- 用途：YouTube、Shorts、TikTok 视频和无需账号的公共媒体解析、断点续传及音视频无损合并。
- 官方独立产物 SHA-256：
  - Linux x64：`6bbb3d314cde4febe36e5fa1d55462e29c974f63444e707871834f6d8cc210ae`
  - Linux arm64：`b6ce97646773070d7a7ffd6bbbdcaecb47c48483909c54c915bf08a7a9b5e0b1`
  - macOS：`498bd0dae17855c599d371d68ec5bafc439a9d8640e838be25c765a9792f261b`
  - Windows x64：`52fe3c26dcf71fbdc85b528589020bb0b8e383155cfa81b64dd447bbe35e24b8`

## gallery-dl

- 项目：https://github.com/mikf/gallery-dl
- 版本：`1.32.8`
- 许可证：GPL-2.0
- 用途：TikTok 照片帖按发布顺序下载原图。

## tdl

- 项目：https://github.com/iyear/tdl
- 版本：`v0.20.3`
- 许可证：AGPL-3.0
- 用途：Telegram 二维码登录、公开和私有消息、受保护会话、媒体组、断点续传及并行下载。
- 辅助程序只从 `https://github.com/iyear/tdl/releases/download/v0.20.3/` 获取匹配当前平台的官方包，解压前检查路径，安装前校验：
  - Linux x64：`f69fe06c17f74c30a3b894b5be05c57a1b082f56b346c994025a2301b269a718`
  - Linux arm64：`8398784d5b9390d26450e3e3528e2ffd0e9fe75d374f63273d0247e7ab0378b7`
  - macOS x64：`f66018736e446bd803872512519094b98bb4bde16a1c344271836061eba03561`
  - macOS arm64：`e6279b0679ebb96c8446b46e893f8671e52af64f7dad72b9ed0147955762a0e0`
  - Windows x64：`a908fe0e8aef387e50f3861ddcbd4f47b9c915153845ab05017a66478c0c530b`
  - Windows arm64：`b08f7d61b6bca66e2bc6540a221d189a044f09b90a7a6ffbf230be0f891ba719`

系统能力 `media-processing` 提供 FFmpeg 与 ffprobe。系统包只应在镜像构建或管理员部署阶段安装，技能运行时不调用 apt、brew、sudo 或其他提权工具。
