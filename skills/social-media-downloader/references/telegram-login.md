# Telegram 管理员配置

Telegram 登录必须在 LightAgent 宿主机或容器终端完成。Runner 的标准输入被禁用，群成员和 Agent 不能完成二维码或二次验证交互。

## 数据目录

官方 Docker 默认使用：

```text
/home/agent/lightagent/skill-data/social-media-downloader
```

其他部署使用该实例 workspace 下的 `skill-data/social-media-downloader`。该目录必须持久化，并只允许运行 LightAgent 的系统用户读写。

## 安装与扫码

进入已安装技能目录后运行可执行辅助程序：

```text
./scripts/telegram_login.py install --data-root /home/agent/lightagent/skill-data/social-media-downloader
./scripts/telegram_login.py login --data-root /home/agent/lightagent/skill-data/social-media-downloader
```

辅助程序按系统和 CPU 选择 `tdl v0.20.3` 官方 Release 包，先验证固定 SHA-256，再安全解压到技能数据目录。登录命令使用 `tdl login -T qr`；用 Telegram 手机客户端扫码，并在终端内完成可能出现的二次验证。

辅助程序不记录手机号、验证码或二次验证密码。会话由 tdl 的 Bolt 存储保存在 `telegram/` 子目录。

## 状态与退出

```text
./scripts/telegram_login.py status --data-root /home/agent/lightagent/skill-data/social-media-downloader
./scripts/telegram_login.py logout --data-root /home/agent/lightagent/skill-data/social-media-downloader
```

`logout` 会删除本地 Telegram 会话。若需要在 Telegram 服务器侧撤销该会话，还应在 Telegram 客户端的“设备”页面终止对应登录。

不要把技能数据目录、tdl 数据库、终端二维码、手机号、验证码或二次验证密码提交到 Skill Hub、发送到群聊或放进截图。
