# New-API 自动签到 MoviePilot 插件设计文档

日期：2026-10-29
参考实现：https://github.com/1L426/Newapi-check
宿主规范：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Plugin_Development.md

## 1. 目标

把 Newapi-check（Node.js + Express + React + SQLite + Puppeteer 的独立应用）的能力
迁移为 MoviePilot V3 插件：复用宿主调度器、通知渠道、浏览器上下文与插件市场分发，
去掉自建前端与自建调度线程。

## 2. 功能映射

| Newapi-check | 插件实现 |
| --- | --- |
| React 管理面板 | 插件配置页（全局设置）+ 详情页 + 仪表板 + 插件 API |
| SQLite `accounts` / `checkin_logs` | 插件数据目录下的 SQLite（`storage.py`） |
| AES-256-GCM 凭据加密 | `crypto.py`，AES-256-GCM（cryptography），密钥文件 600 权限 |
| node-cron 定时任务 | `get_service()` + `CronTrigger`（宿主 APScheduler） |
| Puppeteer + Stealth 兜底 | `app.sdk.browser.launch_browser_context`（宿主 Playwright） |
| SSE 实时进度 | 改为通知汇总 + 详情页刷新（宿主不提供 SSE 插件通道） |
| 余额看板 / 14 天趋势 | 仪表板组件 + `/stats` 接口 |
| 导入导出 JSON | 账号通过 API 增删改查，暂不提供文件导入导出 |

## 3. 架构

```text
__init__.py  插件主类：配置、调度、API、页面、通知
   ├── service.py   业务编排（签到、余额、重试）
   │     ├── client.py   直接 API（标准库 urllib，传输可注入）
   │     ├── browser.py  浏览器兜底（注入浏览器工厂）
   │     ├── storage.py  SQLite 存储
   │     └── crypto.py   凭据加解密
   ├── core.py      纯逻辑：地址/令牌/配额/Cloudflare 判定
   └── notify.py    通知文案
```

分层原则：`core` / `client` / `crypto` / `storage` / `service` 不依赖宿主 SDK，
可独立单测；只有 `__init__.py` 负责对接 MoviePilot 生命周期。

## 4. 关键流程

### 4.1 单账号签到

1. 解析令牌：会话账号直接解密；口令账号优先使用缓存令牌，否则调用 `/api/user/login` 获取。
2. 直接签到：按 `checkin_path` 及回退路径依次 `POST /api/user/self/checkin`。
   - 404/405 → 尝试下一个候选路径；
   - 403/503 且命中 Cloudflare 特征 → 转浏览器兜底；
   - 会话账号的鉴权失败 → 转浏览器兜底。
3. 浏览器兜底：复用宿主浏览器上下文，等待 Cloudflare 挑战通过，注入 Cookie / 填写表单登录，
   在页面上下文中 `fetch` 签到接口。
4. 记录签到前后余额（`/api/user/self`，按 `quota_unit` 换算），写入日志与账号状态。

### 4.2 定时签到

`get_service()` 注册 `NewAPICheckin.AutoCheckin`，触发器来自配置的 5 段 Cron。
触发后先随机延迟（默认 0–300 秒），再执行批量签到；失败账号按 `max_retries` 重试；
结束后按配置推送汇总通知。

## 5. 决策记录

- **自建 SQLite 而非插件自有表**：账号、签到日志是插件私有数据，量级小（几十条账号），
  使用标准库 sqlite3 免去 SQLAlchemy 模型与宿主事务耦合，测试可直接落临时目录。
- **标准库 urllib 而非 httpx**：客户端需要可注入替身做单测；标准库实现零额外依赖，
  代理通过 `ProxyHandler` 支持。
- **凭据加密用 cryptography**：宿主核心依赖已包含 cryptography，AES-256-GCM 认证加密，
  密钥文件保存在插件数据目录并置 600 权限；缺失时降级为明确标记的混淆模式。
- **不提供 Vue 联邦前端**：核心交互（账号增删改查）通过插件 API 完成，
  详情页与仪表板用 Vuetify JSON 足够，避免引入前端构建链路。

## 6. 已知边界

- 浏览器兜底依赖宿主已安装 Playwright 浏览器运行时，未安装时只保留直接 API 路径。
- `/api/user/login` 为 New-API 系列的通用实现，部分站点可能改为验证码登录，
  这类站点应使用会话令牌方式添加账号。
- 插件未内置多用户隔离的账号权限模型，账号对所有可访问插件 API 的用户可见。

## 7. 验证

- 单元测试：21 条，覆盖地址/令牌/配额/Cloudflare 判定、凭据加解密、账号 CRUD、
  签到成功与 Cloudflare 回退路径；HTTP 全部使用替身传输，不访问公网。
- 真实宿主验证：需在 MoviePilot V3 实例中安装插件，确认市场可发现、启停无残留、
  API/服务只注册一次。
