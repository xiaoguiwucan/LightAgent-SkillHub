---
name: monitor-nas-status
schema_version: 2
version: 1.0.0
description: >
  通过只读 SSH 查看一台或多台 NAS 的运行健康状态，兼容飞牛 fnOS、群晖 DSM、极空间、绿联 UGOS Pro 和通用 Linux NAS。用户询问 NAS 是否在线、CPU、内存、负载、温度、运行时间、存储空间、RAID、Docker 或服务状态时使用。
author: 风
license: Apache-2.0
homepage: https://xiaoguiwucan.github.io/LightAgent-SkillHub/
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 2.2.7
max_lightagent_version: null
platforms: [linux, darwin]
category: monitoring
tags: [nas, monitoring, fnos, synology, zspace, ugreen, raid, docker]
status: active
publisher: community
release_notes: |
  首次发布。支持多 NAS、平台自动识别、系统资源、持久化存储、md RAID、Docker、常见服务和分级告警。
  使用固定只读采集脚本、SSH 密钥认证和主机密钥固定，不执行用户提供的远程命令。
breaking_changes: []
requirements:
  env:
    - NAS_MONITOR_TARGETS
    - NAS_MONITOR_HOST
    - NAS_MONITOR_NAME
    - NAS_MONITOR_USER
    - NAS_MONITOR_PORT
    - NAS_MONITOR_KEY_PATH
    - NAS_MONITOR_PLATFORM
    - NAS_MONITOR_TIMEOUT_SECONDS
    - SSH_AUTH_SOCK
  bins: [ssh]
  python: []
  npm: []
  downloads: []
  capabilities: []
lightagent:
  network_domains: [<configured-nas>]
  file_paths: [<skill_config>, <configured-key-path>]
  tools: [skill_run]
  docker_notes: lightagent-ipad 最新镜像已包含 OpenSSH 客户端；其他镜像需要预装 ssh 命令。技能以非 root 用户运行，不安装系统包。
  entrypoints:
    - name: status
      path: scripts/status.py
      runtime: python
      timeout_seconds: 90
      max_output_bytes: 131072
      max_memory_mb: 256
      max_processes: 8
      arguments: {min_items: 0, max_items: 1, max_length: 128}
---

# NAS 运行监控

使用固定的只读 SSH 采集器查看已配置 NAS，不执行用户提供的远程命令。

## 查询状态

查询全部 NAS 时调用：

```json
{"skill_name":"monitor-nas-status","entrypoint":"status","arguments":[]}
```

查询指定 NAS 时传入已经配置的设备名称：

```json
{"skill_name":"monitor-nas-status","entrypoint":"status","arguments":["home-nas"]}
```

将返回 JSON 整理为简洁中文报告，顺序如下：

1. 先显示严重和警告项目。
2. 显示整体健康状态、设备名称、平台、主机名、系统版本和运行时间。
3. 显示 CPU、单位核心负载、内存和最高可读温度。
4. 列出每个持久化文件系统的已用、总量和使用率。
5. 显示 RAID、磁盘、Docker 容器数量与常见服务状态。

聊天中不得展示 NAS IP、SSH 用户名、密钥路径、主机密钥或原始命令输出。报告显示 SMART 不可用时，不得推测硬盘 SMART 健康状态。

## 管理员配置

多设备优先配置 `NAS_MONITOR_TARGETS`：

```json
[
  {
    "name": "home-synology",
    "platform": "synology",
    "host": "192.168.1.20",
    "port": 22,
    "user": "nas-monitor",
    "key_path": "/absolute/path/to/home-synology-key"
  },
  {
    "name": "office-fnos",
    "platform": "fnos",
    "host": "192.168.1.30",
    "port": 22,
    "user": "nas-monitor",
    "key_path": "/absolute/path/to/office-fnos-key"
  }
]
```

单设备也可使用 `NAS_MONITOR_HOST`、`NAS_MONITOR_USER`、`NAS_MONITOR_PORT`、`NAS_MONITOR_KEY_PATH`、`NAS_MONITOR_NAME` 和 `NAS_MONITOR_PLATFORM`。平台可填 `auto`、`fnos`、`synology`、`zspace`、`ugreen` 或 `linux`。

使用只读 SSH 专用账号和无口令私钥，或由管理员配置 `SSH_AUTH_SOCK`。私钥应放入该技能的 `<skill_config>` 目录并设置为 `0600`；不得把 NAS 密码、私钥或 SSH Agent 地址发到群聊、写入技能文件或提交到仓库。

第一次连接会记录 NAS 主机密钥，后续主机密钥变化时拒绝连接。管理员核实设备身份前不得删除或替换记录来绕过检查。

## 平台说明

- 飞牛 fnOS 和绿联 UGOS Pro 通常使用 22 端口，若管理员修改过则以系统设置为准。
- 群晖 DSM 在“终端机和 SNMP”中启用 SSH。
- 极空间部分版本使用自定义高位 SSH 端口，必须填写系统界面显示的端口。
- 账号需要读取 `/proc`、文件系统统计和 `/proc/mdstat` 的权限；需要 Docker 指标时还需具备只读执行 Docker 状态命令的权限。
- 厂商权限不足时，RAID、温度或 SMART 可能不可用，应明确报告不可用。

状态查询不得提升权限、重启服务、安装软件、修改存储、调整 SSH 设置或改变 NAS 配置。
