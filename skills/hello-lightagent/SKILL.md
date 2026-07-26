---
name: hello-lightagent
schema_version: 2
version: 1.0.0
description: 用于检查 LightAgent Skill Hub 安装链路，并返回当前技能版本和运行环境摘要。
author: LightAgent
license: Apache-2.0
homepage: https://github.com/xiaoguiwucan/LightAgent-SkillHub
repository: https://github.com/xiaoguiwucan/LightAgent-SkillHub
min_lightagent_version: 1.0.0
max_lightagent_version: null
platforms: [linux, darwin, windows]
category: diagnostics
tags: [official, diagnostics]
status: active
publisher: official
requirements:
  env: []
  bins: []
  python: []
  npm: []
  downloads: []
  capabilities: []
lightagent:
  network_domains: []
  file_paths: []
  tools: []
  docker_notes: 可在官方 Docker 非 root 用户环境运行。
  entrypoints: []
---

# Hello LightAgent

当用户要求测试技能安装、确认 Skill Hub 是否工作或查看示例技能时使用。

返回以下内容：

1. 技能名称 `hello-lightagent`。
2. 技能版本 `1.0.0`。
3. 当前操作系统名称；无法确认时明确说明未知。

不得读取环境变量的值，不得访问网络，不得修改文件。
