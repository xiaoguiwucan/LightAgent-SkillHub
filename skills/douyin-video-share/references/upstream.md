# 上游参考

- 项目：`jiji262/douyin-downloader`
- 地址：https://github.com/jiji262/douyin-downloader
- 调研提交：`c8ddfeb88a035da4a9d2089b1c280339496b1969`
- 提交日期：2026-07-25
- 许可证：MIT
- 调研时 Star：约 9.1k

该项目证明当前抖音短链接、单视频分享页和无水印视频地址选择仍可维护，并提供了短链接解析、下载完整性校验和文件原子落盘的实现参考。

本技能没有复制其批量下载、Cookie、登录、签名、数据库、浏览器回退或用户主页采集代码。`scripts/download_video.py` 使用 Python 标准库独立实现 LightAgent 所需的单链接最小流程，并额外增加逐跳域名限制、200 MiB 上限和固定 workspace 输出目录。

## 微信文件大小兼容性依据

- Wechaty Issue：[`Support for send 25Mb+ size files`](https://github.com/wechaty/wechaty/issues/766)
- 该 Issue 记录的是 2017 年 Web 协议实现：超过 25 MB 时需要先调用 `API_checkupload` 并在发送请求中携带 `Signature`，否则发送失败。
- 这不是微信当前公开、稳定的官方文件大小承诺，也不能证明所有微信客户端或协议实现都具有相同边界。
- LightAgent 当前群聊发送最终委托 Wechaty/FileBox 链路，未确认完整实现上述大文件签名流程。因此本技能取消自身 20 MiB 压缩阈值后，只能保证发送的是原文件，不能保证超过约 25 MB 的文件一定成功。
