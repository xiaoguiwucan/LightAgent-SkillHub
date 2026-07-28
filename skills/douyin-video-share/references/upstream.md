# 上游参考

- 项目：`jiji262/douyin-downloader`
- 地址：https://github.com/jiji262/douyin-downloader
- 调研提交：`c8ddfeb88a035da4a9d2089b1c280339496b1969`
- 提交日期：2026-07-25
- 许可证：MIT
- 调研时 Star：约 9.1k

该项目证明当前抖音短链接、单视频分享页和无水印视频地址选择仍可维护，并提供了短链接解析、下载完整性校验和文件原子落盘的实现参考。

本技能没有复制其批量下载、Cookie、登录、签名、数据库、浏览器回退或用户主页采集代码。`scripts/download_video.py` 使用 Python 标准库独立实现 LightAgent 所需的单链接最小流程，并额外增加逐跳域名限制、200 MiB 上限和固定 workspace 输出目录。
