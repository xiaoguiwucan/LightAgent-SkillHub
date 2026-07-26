---
name: av-meta
schema_version: 2
version: 1.1.0
description: >
  从 JavBus（主）/JavDB 查询单个番号的标题、演员、封面、磁力和剧情。当用户本轮消息出现明确番号（如 SSIS-001/IPX-177），或明确要求查询该番号的磁力、封面或剧情时必须使用。必须读取本技能并执行 scripts/fetch_meta.py，下载封面后用 send 发送；禁止改用 browser、web_fetch 或手工访问数据站点。不批量枚举番号，不扫描历史消息，无明确番号时不猜测查询。
author: 风
license: Apache-2.0
homepage: https://xiaoguiwucan.github.io/LightAgent-SkillHub/
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 2.1.0
max_lightagent_version: null
platforms: [linux, darwin]
category: media
tags: [metadata, cover, javbus, javdb, magnet]
status: active
publisher: community
release_notes: 使用结构化 Skill Runner 入口执行查询脚本，避免通过通用 Bash 直接启动技能代码。
breaking_changes: []
requirements:
  env: [AV_META_JAVBUS, AV_META_JAVDB_MIRRORS, AV_META_PLOT_BASE]
  bins: [python3]
  python: []
  npm: []
  downloads: []
  capabilities: []
lightagent:
  network_domains: [www.javbus.com, javdb.com, www.javdb.com, javdb36.com, javdb39.com, javdb48.com, javdb601.com, javtxt.com, pics.dmm.co.jp]
  file_paths: [<workspace>/images/av-meta]
  tools: [skill_run, send]
  docker_notes: 可在官方 Docker 非 root 用户环境运行；封面只能写入 LightAgent workspace 下的 images/av-meta 目录。
  entrypoints:
    - name: fetch_meta
      path: scripts/fetch_meta.py
      runtime: python
      timeout_seconds: 90
      max_output_bytes: 262144
      max_memory_mb: 256
      max_processes: 8
      arguments:
        min_items: 1
        max_items: 10
        max_length: 2048
---

# AV Meta

一次只查询用户本轮消息中明确给出的一个番号。只返回公开索引的元数据，不下载或传播视频内容；提醒用户遵守所在地法律与版权规则。

匹配到明确番号后，必须按本文档的命令执行 `scripts/fetch_meta.py`。不得用 `browser`、`web_fetch` 或手工访问 JavBus/JavDB 代替脚本，也不得因历史消息声称技能曾被卸载而跳过当前已加载的技能。

## 约束

1. 从本轮用户消息提取第一个形如 `SSIS-001` 或 `IPX-177` 的番号，并规范化为大写带横杠格式。
2. 用户未提供番号时，回复“请发送番号，例如：SSIS-001”，不要调用工具。
3. 用户一次提供多个番号时，只查询第一个；完成后提示“其余请分条发送”。
4. 不扫描历史消息，不枚举相邻番号，不循环查询，不根据模糊描述猜测番号。
5. 正常流程最多调用一次 `skill_run` 和一次 `send`；包括失败处理在内，工具调用总数不得超过 3 次。
6. 默认只使用 JavBus。只有用户明确要求 JavDB 时才使用 `--source both`。
7. 单次查询失败后直接报告错误，不切换镜像反复重试。
8. 最多返回 3 条磁力信息，不输出完整脚本 JSON。
9. 不调用视觉工具，不把本地封面路径嵌入 Markdown。
10. 禁止调用 `browser` 或 `web_fetch`；数据查询只通过本技能的 `fetch_meta.py` 完成。

## 执行

默认调用 `skill_run` 的 `fetch_meta` 入口，超时由入口固定为 90 秒。参数按以下顺序传入：

```json
{"skill_name":"av-meta","entrypoint":"fetch_meta","arguments":["<CODE>","--source","javbus","--limit-magnets","3","--output-root","<workspace>","--download-cover","<workspace>/images/av-meta/<CODE>.jpg"]}
```

- `<workspace>` 是 LightAgent workspace；官方 Docker 默认为 `/home/agent/lightagent`。
- `<CODE>` 是规范化后的单个番号。

用户明确要求 JavDB 时，仅把 `--source javbus` 改为 `--source both`，其他参数保持不变。

脚本只允许访问元数据声明中的 HTTPS 域名。环境变量只能在该白名单中选择来源；未声明主机、HTTP、带用户名或密码的 URL 会返回 `invalid_source`。

## 返回

脚本成功后：

1. 如果 JSON 中存在 `cover_file`，调用 `send(path=cover_file)` 发送封面一次。
2. 使用以下精简格式返回文本并结束本轮：

```text
番号：{code}
标题：{title_full}
日期：{date}　时长：{runtime}
演员：{actresses}
片商：{maker}

剧情：{plot}

磁力：
{best_magnet.magnet}
（{best_magnet.size} {best_magnet.tags} {best_magnet.name}）
```

最多再附加 `magnets[1]` 和 `magnets[2]`。字段为空时写“暂无”，不要编造。

脚本输出字段包括：`ok`、`code`、`title_full`、`date`、`runtime`、`actresses`、`maker`、`cover`、`cover_file`、`plot`、`best_magnet`、`magnets`、`sources`。

## 失败处理

- `invalid_code`：提示用户发送明确番号。
- `invalid_source`：说明数据源不在允许域名内，不要继续请求。
- `cover_path_outside_output_root`：停止写入并报告路径受限。
- 数据源不可用、未找到或解析失败：简短返回脚本中的 `error` 和 `sources`，不要重试。
- 元数据成功但封面下载失败：返回元数据，并说明封面暂时无法下载；不要再次下载。

多番号提示：

```text
本次只查询第一个番号：{第一个}。其余请分条发送。
```

禁止批量提示：

```text
不能批量扫描历史或枚举番号。请直接发送要查询的单个番号。
```
