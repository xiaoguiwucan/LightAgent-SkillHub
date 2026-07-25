# 技能开发规范

技能应聚焦单一工作流，`description` 必须准确说明触发条件。正文写清输入、步骤、失败处理和输出，不能诱导模型泄露密钥、跳过确认、关闭安全机制或无限制读取和上传文件。

脚本必须可重复执行、默认最小权限、错误时返回非零状态。运行数据写入 LightAgent 提供的技能数据目录；密钥只通过环境变量或 LightAgent 配置获取。更新和卸载不得删除用户配置与生成数据。

兼容性由 `min_lightagent_version`、`max_lightagent_version` 和 `platforms` 描述。无法以非 root 用户在官方 Docker 中完成依赖准备时，必须填写 `docker_notes`。

复杂技能应在 `evaluations/<name>/cases.json` 提供代表性输入与验收条件，测试答案不能写入发布包。
