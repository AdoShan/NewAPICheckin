# NewAPICheckin

MoviePilot V3 插件：**New-API 自动签到**。

参考 [1L426/Newapi-check](https://github.com/1L426/Newapi-check) 重写，把原本独立的
Node.js + React + SQLite 应用，改造为 MoviePilot 原生插件形态：定时任务交给宿主调度器，
凭据加密落库，签到结果走 MoviePilot 通知渠道。

## 功能

- **多站点账号管理**：站点地址、用户名口令 / 会话令牌两种登录方式、`New-Api-User` 头
- **定时签到**：Cron 表达式调度、随机延迟防检测、失败自动重试
- **双层签到策略**：优先直接调用 `/api/user/self/checkin`，被 Cloudflare 拦截时回退
  Playwright 浏览器兜底（复用宿主 `app.sdk.browser.launch_browser_context`）
- **余额统计**：`/api/user/self` 配额换算，兼容 1 美元 = 500000 内部额度的常见实现
- **凭据加密**：AES-256-GCM 落库，密钥文件保存在插件数据目录（权限 600）
- **通知推送**：签到汇总通过 MoviePilot 通知渠道下发，支持远程命令 `/newapi_checkin`
- **仪表板**：账号余额与最近签到状态一览

## 目录结构

```text
NewAPICheckin/
├── package.v3.json                 # 插件市场索引
├── plugins.v3/newapicheckin/
│   ├── __init__.py                 # 插件主类（MoviePilot 生命周期、API、页面）
│   ├── core.py                     # 纯逻辑：地址/令牌/配额/Cloudflare 判定
│   ├── client.py                   # New-API HTTP 客户端（标准库实现，可注入替身）
│   ├── browser.py                  # 浏览器兜底流程
│   ├── crypto.py                   # 凭据加解密
│   ├── storage.py                  # SQLite 存储（账号 + 签到日志）
│   ├── service.py                  # 签到业务编排
│   └── notify.py                   # 通知文案组装
└── tests/v3/newapicheckin/         # 纯逻辑单元测试
```

## 安装

1. 在 MoviePilot 的「插件市场」中配置本仓库为插件源，或直接把本仓库作为插件目录挂载；
2. 安装插件并启用；
3. 通过插件 API 添加账号：

```bash
curl -X POST "http://<moviepilot>/api/v1/plugin/NewAPICheckin/accounts" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "我的 New-API 站点",
    "base_url": "https://demo.example.com",
    "login_type": "session",
    "session_token": "<浏览器 Cookie 中的 session 值>",
    "new_api_user": "",
    "enabled": true
  }'
```

## 插件 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/accounts` | 列出账号 |
| POST | `/accounts` | 新增账号 |
| PUT | `/accounts/{account_id}` | 更新账号 |
| DELETE | `/accounts/{account_id}` | 删除账号 |
| POST | `/accounts/{account_id}/test` | 测试连通性 |
| POST | `/accounts/{account_id}/checkin` | 单账号立即签到 |
| POST | `/accounts/{account_id}/balance` | 刷新余额 |
| POST | `/checkin` | 全部账号立即签到 |
| GET | `/logs` | 签到日志 |
| GET | `/stats` | 签到统计 |

## 配置项

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用插件 |
| `cron` | `0 8 * * *` | 定时签到 Cron（5 段） |
| `notify` | `true` | 是否发送签到通知 |
| `max_retries` | `3` | 失败重试次数 |
| `random_delay_max_seconds` | `300` | 定时任务随机延迟上限（秒） |
| `checkin_path` | `/api/user/self/checkin` | 签到接口路径 |
| `browser_headless` | `true` | 浏览器兜底是否无头 |
| `browser_timeout_seconds` | `60` | 浏览器超时时间 |
| `proxy` | 空 | 代理地址（可选） |

## 本地开发与测试

```bash
uv venv .venv
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest tests/v3/newapicheckin -q
```

纯逻辑模块（`core` / `client` / `crypto` / `storage` / `service`）不依赖宿主 SDK，
单元测试全部使用替身传输，不访问公网。

## 参考

- [MoviePilot 插件开发指南（V3）](https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Plugin_Development.md)
- [1L426/Newapi-check](https://github.com/1L426/Newapi-check)
