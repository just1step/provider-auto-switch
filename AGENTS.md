# AGENTS.md — provider-auto-switch

## 项目概述

Hermes Dashboard 插件，全 profile 自动模型切换管理。当模型因限额/限速不可用时，按用户配置的优先级自动切换到备选方案。

## 关键文件

| 文件 | 做什么 |
|------|--------|
| `auto_switch_engine.py` | 核心：`find_next_combo()` 两轮遍历、`auto_switch()`、`handle_api_error()`、`_scan_provider_models()` |
| `auto_switch_db.py` | SQLite 四表 CRUD + migration。DB 路径 `~/.hermes/provider-auto-switch.db` |
| `__init__.py` | Agent 侧 `register()` → `post_api_request` 钩子，拦截 API 错误自动触发切换 |
| `dashboard/plugin_api.py` | FastAPI 9 端点（profiles/config/snapshot/scan/switch/history/stats/recovery） |
| `dashboard/dist/index.js` | React IIFE（无构建工具），Hermes Plugin SDK |

## 数据模型

- **switch_config**: profile 级别配置（strategy、model_priority、provider_priority、model_providers、provider_models）
- **scan_snapshot**: model×provider×profile 交叉矩阵 status (active/limited/unknown)
- **active_combo**: 当前活跃 combo，找不到时 fallback 到 config.yaml 读取
- **switch_history**: 切换记录

## 优先级系统

- 每个 model 可独立配置 provider 优先级（`model_providers`），未配置的 fallback 全局 `provider_priority`
- 两轮遍历：Pass 1 active → Pass 2 limited
- combo 不存在自动跳过（从 scan_snapshot 判断）

## 重要约定

- **禁止 relative import** — plugin_api.py 通过 `sys.path.insert` 引入 sibling module，所有 import 用绝对路径（`from auto_switch_db import ...`）
- **DB thread safety** — `check_same_thread=False`, WAL mode, thread-local connection
- **API 返回格式** — config 平铺字段（无 `.config` 包装），snapshot 平坦 entries，history 用 `.entries`
- **前端 snapshot 格式** — `{model_name: [{provider, status, error_reason}, ...]}`（从 API 平坦列表分组组装）
- **CURRENT banner** — 顶部横幅显示当前模型/provider，左侧 sidebar 只显示 profile 名 + 状态（不重复模型）

## 切换策略公式

```
model_first (model→provider 遍历):
  model_priority[0] → model_providers[model_priority[0]] or provider_priority → active?
  model_priority[1] → model_providers[model_priority[1]] or provider_priority → active?
  ... → 全部 limited? → 第二轮 limited fallback

provider_first (provider→model 遍历):
  provider_priority[0] → provider_models[provider_priority[0]] or model_priority → active?
  ...同上的对称逻辑
```
