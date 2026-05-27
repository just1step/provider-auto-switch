# provider-auto-switch

> Hermes Dashboard 插件 — 全 Profile 自动模型切换管理

当模型因限额、限速等原因不可用时，自动按照用户配置的优先级规则切换到备选方案。支持**每个模型独立配置 Provider 优先级**（per-model provider priority），不绑定任何具体 model/provider 名字，完全由配置驱动。

## 功能

- **Profile 管理** — 自动发现 Hermes 所有 profile（`~/.hermes/profiles/`），每个 profile 独立配置
- **两种切换策略**：
  - **模型优先** — 固定模型排序，每个模型可独立配置 Provider 排序（per-model provider priority），未配置的 fallback 到全局 Provider 列表
  - **Provider 优先** — 固定 Provider 排序，每个 Provider 可独立配置模型排序
- **双排序表** — 模型排序 × Provider 排序交叉匹配，不存在的 combo 自动跳过
- **两轮遍历** — 优先选 active 的组合，全部不可用时 fallback 到 limited 组合
- **手动干预优先** — 用户手动选择后自动暂停自动切换，点击启用后恢复
- **实时错误拦截** — `post_api_request` 插件钩子运行时拦截 API 错误，自动触发切换
- **恢复检测** — 定时扫描已切换走的模型是否恢复可用，恢复后按优先级自动切回
- **Dashboard UI** — 显眼的当前模型横幅、可拖拽排序的优先级列表、per-model 子列表编辑器

## 架构

```
~/.hermes/plugins/provider-auto-switch/
├── __init__.py                # post_api_request 钩子注册 + profile 解析
├── plugin.yaml                # 插件清单
├── auto_switch_db.py           # SQLite 数据层（WAL 模式、自动 migration）
├── auto_switch_engine.py       # 核心跳转逻辑 + Provider 扫描 + 错误检测
├── dashboard/
│   ├── manifest.json           # 插件声明（标签页、API 入口）
│   ├── plugin_api.py           # FastAPI 后端路由（9 个端点）
│   └── dist/
│       ├── index.js            # React UI（Hermes Plugin SDK，IIFE，无构建）
│       └── style.css           # 响应式样式
├── README.md
├── AGENTS.md
├── LICENSE
└── .gitignore
```

### 数据流

```
用户操作 → Dashboard UI → plugin_api.py → SQLite（配置/状态）
                              → Profile config.yaml（切换执行）
Agent 请求报错 → post_api_request 钩子 → handle_api_error()
    → 标记 combo limited → auto_switch() → 查找下一最佳 combo
```

### 核心文件职责

| 文件 | 职责 |
|------|------|
| `auto_switch_db.py` | `switch_config` / `scan_snapshot` / `switch_history` / `active_combo` 四表 CRUD |
| `auto_switch_engine.py` | `find_next_combo()` 两轮遍历、`auto_switch()` 执行切换、`handle_api_error()` 错误处理、`_scan_provider_models()` Provider 扫描 |
| `__init__.py` | 插件注册 `register(ctx)` → `post_api_request` 钩子、profile 解析 |
| `plugin_api.py` | 9 个 REST API 端点（profiles / config / snapshot / scan / switch / history / stats / recovery） |

## 优先级系统

### 配置结构

```json
{
  "strategy": "model_first",
  "model_priority": ["deepseek-v4-flash-free", "deepseek-v4-flash"],
  "provider_priority": ["opencode-zen", "opencode-go"],
  "model_providers": {
    "deepseek-v4-flash-free": ["opencode-zen", "opencodezen_gmail"],
    "deepseek-v4-flash": ["opencode-go", "opencodego_gmail", "deepseek"]
  }
}
```

- `model_providers`：每个模型独立指定 Provider 优先级，未指定的模型自动 fallback 到全局 `provider_priority`
- `provider_models`：provider_first 策略时使用，同理
- 两轮遍历：Pass 1 找 active → Pass 2 找 limited → 无可用 combo 则返回 None

### 跳转示例

```
模型排序: flash-free → flash → deepseek-v4-pro
  flash-free 的 provider: opencode-zen → opencodezen_gmail
  flash 的 provider:     opencode-go → opencodego_gmail → deepseek

全部 active → flash-free × opencode-zen  ✓
flash-free × opencode-zen limited → flash-free × opencodezen_gmail  ✓
flash-free 都 limited → flash × opencode-go  ✓
flash × opencode-go limited → flash × opencodego_gmail  ✓
全部 limited → 5分钟后 check_recovery 自动切回最优组合  ✓
```

## 部署

```bash
# 1. 复制到 Hermes 插件目录
cp -r ~/projects/provider-auto-switch/* ~/.hermes/plugins/provider-auto-switch/

# 2. 重启 Dashboard
systemctl --user restart hermes-dashboard

# 3. 验证
# 浏览器 → Dashboard → "Auto-Switch" 标签页
# API: curl -s http://localhost:9119/api/plugins/provider-auto-switch/profiles \
#   -H "X-Hermes-Session-Token: $(curl -s http://localhost:9119/ | sed -n 's/.*__HERMES_SESSION_TOKEN__=\"\([^\"]*\)\".*/\1/p')"
```

## 开发

```bash
# 前端无构建工具，直接编辑 dashboard/dist/index.js（IIFE + React SDK）
# 修改后 Ctrl+Shift+R 刷新浏览器即可
# 后端修改后需重启 Dashboard: systemctl --user restart hermes-dashboard
```

## License

MIT
