# provider-auto-switch

> Hermes Dashboard 插件 — 全 profile 自动模型切换管理

当模型因限额、限速等原因不可用时，自动按用户配置的优先级规则切换到备选方案。

## 功能

- **Profile 管理**：发现并管理 Hermes 所有 profile 的模型配置
- **Provider 扫描**：调用 Provider API（`GET /v1/models`）实时扫描可用模型
- **两种切换策略**：
  - **模型优先**：固定模型名，在排序好的 Provider 列表中切换
  - **Provider 优先**：固定 Provider，在排序好的模型列表中切换
- **双排序表**：模型排序表 + Provider 排序表，交叉匹配自动跳过
- **自动触发**：`post_api_request` 插件钩子运行时拦截 API 错误（限额、限速等）
- **恢复检测**：定时扫描已切换走的模型是否恢复，恢复后自动切回
- **手动干预**：用户手动选择优先级最高，手动后自动切换暂停
- **Dashboard UI**：颜色高亮展示状态、切换历史、配置管理

## 架构

```
~/.hermes/plugins/provider-auto-switch/
├── __init__.py              # post_api_request 钩子注册
├── db.py                    # SQLite 数据层
├── switch_engine.py         # 核心切换逻辑 + Provider 扫描
├── dashboard/
│   ├── manifest.json        # 插件声明
│   ├── plugin_api.py        # FastAPI 后端路由
│   └── dist/
│       └── index.js         # React UI (Hermes Plugin SDK)
└── .gitignore
```

## 部署

```bash
# 1. 复制到 Hermes 插件目录
cp -r ~/.hermes/plugins/provider-auto-switch ~/.hermes/plugins/

# 2. 重启 Dashboard
systemctl --user restart hermes-dashboard

# 3. 验证
# 浏览器打开 Dashboard → 出现 "Auto-Switch" 标签页
# API 测试：
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9119/api/plugins/provider-auto-switch/profiles
```

## 需求来源

详见 [设计文档](https://zcngllv1g01f.feishu.cn/docx/NJAsdxpS0o28rVxSE4hcs0xon06)

## License

MIT
