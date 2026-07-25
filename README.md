# LightAgent Skill Hub

LightAgent 官方技能中心。技能通过 GitHub Pull Request 投稿，经自动校验和维护者审核后发布为可校验的静态注册表，不收集安装遥测。

- 源码仓库：https://github.com/xiaoguiwucan/LightAgent-SkillHub
- 技能目录：https://xiaoguiwucan.github.io/LightAgent-SkillHub/

## 使用

```bash
lightagent skill search hello
lightagent skill install hello-lightagent
lightagent skill outdated
lightagent skill update hello-lightagent
```

网页目录由 GitHub Pages 发布，LightAgent 会优先读取该目录，并在不可用时使用最后一次验证通过的缓存或旧技能广场。

## 投稿

1. 从 `templates/skill/` 复制模板到 `skills/<name>/`。
2. 完成 `SKILL.md` 的全部必填元数据，并将测试放入 `evaluations/<name>/`。
3. 运行 `python scripts/validate.py` 和 `python scripts/build_registry.py --output dist`。
4. 按中文 PR 模板提交；一个 PR 原则上只修改一个技能。

详细要求见 [CONTRIBUTING.md](CONTRIBUTING.md)、[DEVELOPING.md](DEVELOPING.md) 与 [REVIEWING.md](REVIEWING.md)。

## 安全边界

元数据中的网络、文件和工具权限用于自动检查与维护者审核，不构成运行时沙箱。需要 root、apt 或 brew 的技能可以收录，但必须明确标注需要自定义镜像或宿主机准备，不得宣称可在 LightAgent 官方 Docker 中无缝安装。

## 许可证

仓库基础设施采用 Apache-2.0。每个技能必须单独声明 SPDX 许可证，技能代码与素材以该技能声明为准。
