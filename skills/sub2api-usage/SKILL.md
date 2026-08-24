---
name: sub2api-usage
schema_version: 2
version: 1.0.1
description: >
  查询 Sub2API 总体及各账号的今日、本周、本月 Token 用量、今日请求、占比、用量高峰、预估和周池额度。用户询问 Sub2API 使用情况、成员用量排行、今天用了多少 Token、用量高峰或周池剩余时使用。
author: 风
license: Apache-2.0
homepage: https://xiaoguiwucan.github.io/LightAgent-SkillHub/
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 2.2.8
max_lightagent_version: null
platforms: [linux, darwin, windows]
category: monitoring
tags: [sub2api, token, usage, monitoring, quota, analytics]
status: active
publisher: community
release_notes: |
  修复 Skill Runner 128 MB 内存限制下并发创建请求线程失败的问题。
  管理员统计改为固定顺序读取，不再误报“无法读取 Sub2API 成员用量”。
  新增独立统计时区配置，部署主机日期异常时无需修改宿主机时钟。
breaking_changes: []
requirements:
  env:
    - SUB2API_STATUS_URL
    - SUB2API_STATUS_TIMEOUT_SECONDS
    - SUB2API_ADMIN_BASE_URL
    - SUB2API_ADMIN_API_KEY
    - SUB2API_REPORT_TIMEZONE
  bins: []
  python: []
  npm: []
  downloads: []
  capabilities: []
lightagent:
  network_domains: [hj.wwszxc.tax, <configured-sub2api-host>]
  file_paths: []
  tools: [skill_run]
  docker_notes: 纯 Python 非 root 运行。管理员统计地址和密钥必须通过容器环境变量注入，不写入技能文件、日志或群消息。
  entrypoints:
    - name: status
      path: scripts/status.py
      runtime: python
      timeout_seconds: 15
      max_output_bytes: 32768
      max_memory_mb: 128
      max_processes: 1
      arguments: {min_items: 0, max_items: 0, max_length: 1}
---

# Sub2API 用量查询

所有 Sub2API 用量请求均调用固定的结构化入口：

```json
{"skill_name":"sub2api-usage","entrypoint":"status","arguments":[]}
```

入口返回 JSON，其中 `text` 是可以直接发送给群聊的中文报告。不得由模型重新计算 Token、请求数、占比、排行、高峰时段、预估或周池重置时间。

## 报告内容

- 总体今日用量、本周用量、本月用量和今日请求。
- 当前全部 Sub2API 账号，统一使用用户管理中的当前用户名。
- 每个账号的今日用量、本周用量、本月用量、今日请求和占今日总量。
- 今日用量最高账号、请求最多账号、前三名集中度和今日用量高峰时段。
- 本周、本月预估、日均用量和 Sub2API 周池剩余额度。

账号某个周期没有调用记录时显示为零；总体字段采集失败时显示 `--`。分析不得输出“今日用量占本月累计量”的比例。

## 管理员配置

- `SUB2API_STATUS_URL`：只读公共状态 JSON。
- `SUB2API_ADMIN_BASE_URL`：Sub2API 管理端 API 根地址。
- `SUB2API_ADMIN_API_KEY`：只用于只读账号、排行和小时趋势查询。
- `SUB2API_STATUS_TIMEOUT_SECONDS`：单次请求超时，范围 1–30 秒。
- `SUB2API_REPORT_TIMEZONE`：统计日期与聚合时区，默认 `Asia/Shanghai`。

这些值只能由管理员通过宿主机或容器环境变量配置。群成员不能提供、覆盖或查询这些配置。

## 安全边界

群消息不得包含管理员 API Key、邮箱、费用、原始 API 响应或管理端地址。报告只展示 Sub2API 用量，不展示 GrokBot、CloudDrive2、机器、磁盘、备份、FRP、开发板或其他服务状态。
