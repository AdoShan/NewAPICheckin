# MoviePilot 实例实机测试计划（NewAPICheckin）

日期：2026-10-29
目标环境：CloudCone 服务器上的 MoviePilot Docker 容器（`docker ps` 中定位容器名）
代码基线：`main` 分支 `0757010`（插件 v1.0.0）

## 0. 前提与前置动作

| 项 | 值 | 获取方式 |
| --- | --- | --- |
| 容器名 | `mp`（以实际为准） | `docker ps --format '{{.Names}}\t{{.Image}}'` |
| 配置卷 | `/config`（容器内路径） | `docker inspect <容器> --format '{{json .Mounts}}'` |
| 实例地址 | `http://<服务器IP>:<端口>` | compose 端口映射 |
| API Token | MoviePilot 设置 → API → 访问令牌 | UI 获取 |
| 真实站点 | New-API 站点地址 + 会话令牌 | 浏览器 F12 → Application → Cookies → `session` |

> 会话令牌等价于账号密码，只在本地脚本里临时使用，不要贴进公开渠道或仓库。

## 1. 阶段划分

| 阶段 | 内容 | 通过标准 | 回滚方式 |
| --- | --- | --- | --- |
| P0 | 环境勘察 | 拿到容器名、配置卷、MP 版本、Playwright 是否可用 | 只读，无需回滚 |
| P1 | 安装与加载 | 插件可被发现、可启用、`app.plugins.newapicheckin` 导入成功 | `docker exec ... rm -rf /config/plugins.v3/newapicheckin` + 重载 |
| P2 | 静态自检 | `scripts/mp_smoke_test.sh` 全 PASS | 只读 |
| P3 | API 黑盒 | `scripts/mp_api_test.py` 全 PASS | 删除测试账号（脚本自动清理） |
| P4 | 端到端签到 | 真实站点签到成功、余额回写、通知送达 | 停用插件 |
| P5 | 稳定性 | 启停/重载 3 轮无残留任务、无重复 API、日志无异常 | 停用插件 |

## 2. P0 环境勘察命令

```bash
ssh CloudCone
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect <容器名> --format '{{json .Mounts}}'
docker exec <容器名> python -V
docker exec <容器名> sh -c 'ls -d /config /config/plugins* 2>/dev/null'
```

## 3. P1 安装插件

方式 A（市场源，推荐）：在 MP 插件市场配置本仓库为插件源 → 安装 `New-API 自动签到`。

方式 B（直挂目录，适合快速验证）：

```bash
git clone --depth 1 https://github.com/AdoShan/NewAPICheckin.git /tmp/NewAPICheckin
docker cp /tmp/NewAPICheckin/plugins.v3/newapicheckin <容器名>:/config/plugins.v3/
docker cp /tmp/NewAPICheckin/package.v3.json <容器名>:/config/
docker restart <容器名>
```

安装后检查：

```bash
docker exec <容器名> python -c "
import importlib
m = importlib.import_module('app.plugins.newapicheckin')
print(m.NewAPICheckin.plugin_version, m.NewAPICheckin.plugin_icon)"
```

期望输出 `1.0.0 newapicheckin.png`。若报 `ModuleNotFoundError: app.sdk.browser`，
说明宿主版本早于插件声明的 `system_version: >=3.0.0`。

## 4. P2 容器内静态自检

```bash
bash scripts/mp_smoke_test.sh <容器名>
```

覆盖 7 组断言：

1. 容器与运行环境（Python、/config 挂载）
2. 插件目录、主类文件、图标文件
3. 宿主 SDK 导入（`app.plugins.newapicheckin`）
4. 运行时依赖（cryptography / playwright / 浏览器二进制目录）
5. 数据目录、SQLite 文件、密钥文件权限 600
6. 定时服务注册数量恰好为 1（`NewAPICheckin.AutoCheckin`）
7. 插件日志片段

## 5. P3 API 黑盒测试

```bash
python scripts/mp_api_test.py --url http://<服务器IP>:<端口> --token <API_TOKEN>
```

覆盖：已安装列表 / 插件详情 / 配置页 / 详情页 / 仪表板 / accounts CRUD / stats / logs /
单账号 test 与 checkin / 启停两轮回读 / reload 后 API 仍可用。测试账号在结束时自动删除。

## 6. P4 端到端签到

```bash
python scripts/mp_api_test.py --url http://<服务器IP>:<端口> --token <API_TOKEN> \
  --account-url https://你的newapi站点 --account-token '<浏览器 cookie 中的 session 值>'
```

通过标准：

- `/test` 返回 `success: true`
- `/checkin` 返回 `success: true`，且消息为站点返回的签到文案
- `/accounts` 中该账号 `quota` 字段被刷新
- `/logs` 出现一条 `success` 记录
- MoviePilot 通知渠道收到一条「New-API 签到结果」

## 7. P5 稳定性与残留检查

```bash
# 启停三轮后，定时服务仍只有一个
for i in 1 2 3; do
  curl -X PUT -H "Authorization: Bearer <API_TOKEN>" -H 'Content-Type: application/json' \
    -d '{"enabled": false}' http://<服务器IP>:<端口>/api/v1/plugin/NewAPICheckin
  curl -X PUT -H "Authorization: Bearer <API_TOKEN>" -H 'Content-Type: application/json' \
    -d '{"enabled": true}' http://<服务器IP>:<端口>/api/v1/plugin/NewAPICheckin
done
bash scripts/mp_smoke_test.sh <容器名>   # 第 6 组断言应仍为 PASS

# 数据目录不产生重复实例
docker exec <容器名> sh -c 'ls -l /config/plugins/NewAPICheckin'
docker exec <容器名> sh -c "grep -ci 'NewAPICheckin' /config/logs/*.log | tail -5"
```

关注点：APScheduler 中 `NewAPICheckin.AutoCheckin` 只出现一次；容器线程数不随启停增长；
`/config/plugins/NewAPICheckin` 下只有一个 `newapicheckin.db` 与一个 `secret.key`。

## 8. 定时任务专项

把 cron 临时设为 `*/5 * * * *`，观察 5 分钟内：

- 调度日志出现一次「随机延迟 N 秒后开始签到」
- 通知只发一次（不重复）
- 失败账号按 `max_retries` 重试，间隔符合 `retry_delay_seconds`

## 9. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 宿主缺少 Playwright 浏览器 | 直接 API 路径仍可用；浏览器兜底会报错并写日志 |
| `/api/user/login` 与站点实现不一致 | 改用会话令牌方式添加账号 |
| 站点 quota 不是 500000 倍数 | 在账号上显式配置 `quota_unit` |
| 凭据加密降级为混淆模式 | 容器内确认 `cryptography` 可用 |
| 定时任务频繁触发站点风控 | 调大随机延迟上限、降低 cron 频率 |

## 10. 实机验证结果（2026-10-29，CloudCone + Docker）

环境：MP 容器 `moviepilot`（`jxxghp/moviepilot-v3:latest`，应用 v3.2.1，Python 3.14.7），
配置卷 `/config`，PostgreSQL 库，Playwright 浏览器挂载在 `/moviepilot/.cache/ms-playwright`。

安装路径（实测）：

1. `docker cp` 到 `/app/app/plugins/newapicheckin` **不会被加载**——该目录是已安装插件的
   落地位置，宿主只按数据库里的安装记录加载。
2. 可行做法：把仓库作为本地插件仓放到 `/config/local-plugins`（结构
   `plugins.v3/<id>/` + 根目录 `package.v3.json`），并在 `/config/app.env` 增加
   `PLUGIN_LOCAL_REPO_PATHS=/config/local-plugins`，重启后即可在插件目录中被发现，
   再调用 `GET /api/v1/plugin/install/NewAPICheckin` 完成安装。

实测发现的两个真实缺陷（离线单测无法发现）：

| 缺陷 | 现象 | 修复 |
| --- | --- | --- |
| 插件 API 未声明鉴权方式 | 全部端点 401 `apikey 校验不通过` | 每条 API 声明补 `"auth": "bear"` |
| 定时服务 kwargs 与调度器参数撞名 | `add_job() got multiple values for argument 'trigger'`，任务注册失败 | kwargs 改用 `source`，与宿主 `trigger` 参数区分 |

修复后实测：插件安装、启用、配置页/详情页/仪表板、账号 CRUD、统计日志、启停回读、
重载后 API 正常（25 项断言全 PASS）；`GET /scheduler` 显示本插件任务恰好 1 个；
对模拟 New-API 站点完成端到端签到（余额 500000→1.00 换算正确，日志与统计写入正确）。

遗留：容器内 `/app/app/plugins/newapicheckin` 位于镜像层，容器重建会丢失；持久来源应以
`/config/local-plugins` 为准。

## 11. 回归防护

每次改动后本地必跑：

```bash
.venv/bin/python -m pytest tests/v3/newapicheckin -q
```

GitHub Actions（`tests` workflow）覆盖同一套单测与 `compileall` 语法检查。
