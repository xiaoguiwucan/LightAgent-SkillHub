# 注册表签名密钥

当前密钥：

| key_id | Ed25519 公钥（Base64 Raw） | 状态 |
| --- | --- | --- |
| `lightagent-skillhub-2026-01` | `ddZUto18e4bp5pRMgrHD8xJoCfFGxiXznA8G8ksyaMQ=` | active |

私钥只保存在 GitHub Actions Secret `SKILL_HUB_SIGNING_KEY` 中，不进入仓库。轮换时先发布同时信任新旧公钥的 LightAgent 版本，再切换发布密钥，最后在兼容窗口结束后移除旧公钥。
