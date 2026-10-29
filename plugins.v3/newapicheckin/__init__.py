"""
New-API 自动签到插件。

参考 https://github.com/1L426/Newapi-check 重写为 MoviePilot V3 插件：
- 多站点账号管理（用户名口令 / 会话令牌两种登录方式）
- 定时签到、随机延迟、失败重试
- 直接 API 优先，Cloudflare 拦截时回退浏览器兜底
- 余额统计、签到日志、通知推送
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger

from app.sdk.browser import launch_browser_context
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.schemas.types import EventType

from .client import NewAPIClient, UrllibTransport
from .core import normalize_base_url
from .crypto import SecretBox
from .notify import build_statistics_text, build_summary
from .service import CheckinService
from .storage import AccountStore

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "cron": "0 8 * * *",
    "notify": True,
    "max_retries": 3,
    "random_delay_max_seconds": 300,
    "checkin_path": "/api/user/self/checkin",
    "browser_headless": True,
    "browser_timeout_seconds": 60,
    "proxy": "",
}


class NewAPICheckin(_PluginBase):
    """
    New-API 站点自动签到插件。
    """

    plugin_name = "New-API 自动签到"
    plugin_desc = "为多个 New-API 站点提供定时签到、余额统计与通知推送能力。"
    plugin_icon = "newapicheckin.png"
    plugin_version = "1.0.0"
    plugin_author = "AdoShan"
    author_url = "https://github.com/AdoShan/NewAPICheckin"
    plugin_config_prefix = "newapicheckin_"
    plugin_order = 50
    auth_level = 1

    def __init__(self) -> None:
        """
        初始化插件运行状态。
        """
        super().__init__()
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._store: Optional[AccountStore] = None
        self._secrets: Optional[SecretBox] = None
        self._service: Optional[CheckinService] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """
        生效配置并建立运行期资源。

        :param config: 插件配置
        """
        config = dict(config or {})
        merged = dict(DEFAULT_CONFIG)
        merged.update({key: value for key, value in config.items() if key in DEFAULT_CONFIG})
        merged["enabled"] = bool(merged.get("enabled"))
        merged["notify"] = bool(merged.get("notify"))
        merged["browser_headless"] = bool(merged.get("browser_headless"))
        self._config = merged

        self._ensure_storage()
        self._ensure_service()

        if config.get("onlyonce"):
            self.update_config({"onlyonce": False, **merged})
            self.run_checkin(trigger="手动执行")

    def get_state(self) -> bool:
        """
        返回插件是否启用。

        :return: 启用返回 True
        """
        return bool(self._config.get("enabled"))

    def stop_service(self) -> None:
        """
        释放插件持有的数据库连接。
        """
        if self._store:
            self._store.close()
            self._store = None
        self._service = None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _ensure_storage(self) -> AccountStore:
        """
        确保账号存储可用。

        :return: 账号存储
        """
        if self._store is None:
            data_path = self.get_data_path()
            self._store = AccountStore(data_path / "newapicheckin.db")
            self._secrets = SecretBox(data_path / "secret.key")
        return self._store

    def _ensure_service(self) -> CheckinService:
        """
        确保签到服务可用。

        :return: 签到服务
        """
        if self._service is None:
            store = self._ensure_storage()
            proxy = str(self._config.get("proxy") or "").strip()
            transport = UrllibTransport(timeout=30, proxy=proxy or None)
            self._service = CheckinService(
                store=store,
                secrets=self._secrets,
                client=NewAPIClient(transport=transport),
                browser_launcher=launch_browser_context,
                checkin_path=str(self._config.get("checkin_path") or "/api/user/self/checkin"),
                headless=bool(self._config.get("browser_headless", True)),
                browser_timeout_seconds=int(self._config.get("browser_timeout_seconds") or 60),
                retry_delay_seconds=60,
            )
        return self._service

    def _notify(self, results: List[Dict[str, Any]], trigger: str) -> None:
        """
        发送签到汇总通知。

        :param results: 签到结果
        :param trigger: 触发来源
        """
        if not self._config.get("notify"):
            return
        summary = build_summary(results, trigger=trigger)
        try:
            self.post_message(title=summary["title"], text=summary["text"])
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(f"[NewAPICheckin] 通知发送失败：{error}")

    # ------------------------------------------------------------------
    # 业务入口
    # ------------------------------------------------------------------
    def run_checkin(self, trigger: str = "定时任务") -> List[Dict[str, Any]]:
        """
        执行全部账号签到。

        :param trigger: 触发来源描述
        :return: 签到结果列表
        """
        service = self._ensure_service()
        max_delay = int(self._config.get("random_delay_max_seconds") or 0)
        if max_delay > 0 and trigger == "定时任务":
            delay = random.randint(0, max_delay)
            logger.info(f"[NewAPICheckin] 随机延迟 {delay} 秒后开始签到")
            time.sleep(delay)
        try:
            results = service.retry_checkin(int(self._config.get("max_retries") or 1))
        except Exception as error:  # pylint: disable=broad-except
            logger.error(f"[NewAPICheckin] 签到执行异常：{error}")
            results = []
        self._notify(results, trigger)
        return results

    # ------------------------------------------------------------------
    # 远程命令
    # ------------------------------------------------------------------
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册插件远程命令。

        :return: 命令声明
        """
        return [
            {
                "cmd": "/newapi_checkin",
                "event": EventType.PluginAction,
                "desc": "立即执行 New-API 站点签到",
                "category": "插件命令",
                "data": {"action": "newapi_checkin"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def newapi_checkin_command(self, event: Event) -> None:
        """
        处理远程签到命令。

        :param event: 插件事件
        """
        event_data = event.event_data or {}
        if event_data.get("action") != "newapi_checkin":
            return
        results = self.run_checkin(trigger="手动执行")
        summary = build_summary(results, trigger="手动执行")
        try:
            self.post_message(title=summary["title"], text=summary["text"])
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(f"[NewAPICheckin] 通知发送失败：{error}")

    # ------------------------------------------------------------------
    # 定时服务
    # ------------------------------------------------------------------
    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册定时签到服务。

        :return: 服务声明
        """
        cron = str(self._config.get("cron") or "").strip()
        if not self.get_state() or not cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(cron)
        except Exception as error:  # pylint: disable=broad-except
            logger.error(f"[NewAPICheckin] Cron 表达式无效：{cron}（{error}）")
            return []
        return [
            {
                "id": "NewAPICheckin.AutoCheckin",
                "name": "New-API 自动签到",
                "trigger": trigger,
                "func": self.run_checkin,
                "kwargs": {"trigger": "定时任务"},
            }
        ]

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def get_api(self) -> List[Dict[str, Any]]:
        """
        注册插件后端 API。

        :return: API 声明
        """
        return [
            {
                "path": "/accounts",
                "endpoint": self.list_accounts,
                "methods": ["GET"],
                "summary": "列出签到账号",
            },
            {
                "path": "/accounts",
                "endpoint": self.create_account,
                "methods": ["POST"],
                "summary": "新增签到账号",
            },
            {
                "path": "/accounts/{account_id}",
                "endpoint": self.update_account,
                "methods": ["PUT"],
                "summary": "更新签到账号",
            },
            {
                "path": "/accounts/{account_id}",
                "endpoint": self.delete_account,
                "methods": ["DELETE"],
                "summary": "删除签到账号",
            },
            {
                "path": "/accounts/{account_id}/test",
                "endpoint": self.test_account,
                "methods": ["POST"],
                "summary": "测试账号连通性",
            },
            {
                "path": "/accounts/{account_id}/checkin",
                "endpoint": self.checkin_account,
                "methods": ["POST"],
                "summary": "立即为单个账号签到",
            },
            {
                "path": "/accounts/{account_id}/balance",
                "endpoint": self.refresh_balance,
                "methods": ["POST"],
                "summary": "刷新账号余额",
            },
            {
                "path": "/checkin",
                "endpoint": self.checkin_all,
                "methods": ["POST"],
                "summary": "立即执行全部账号签到",
            },
            {
                "path": "/logs",
                "endpoint": self.list_logs,
                "methods": ["GET"],
                "summary": "查询签到日志",
            },
            {
                "path": "/stats",
                "endpoint": self.get_statistics,
                "methods": ["GET"],
                "summary": "查询签到统计",
            },
        ]

    def list_accounts(self) -> List[Dict[str, Any]]:
        """
        列出签到账号。

        :return: 账号列表
        """
        store = self._ensure_storage()
        return store.list_accounts()

    def create_account(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        新增签到账号。

        :param payload: 账号参数
        :return: 操作结果
        """
        store = self._ensure_storage()
        secrets = self._secrets
        name = str(payload.get("name") or "").strip()
        base_url = normalize_base_url(payload.get("base_url") or "")
        login_type = str(payload.get("login_type") or "password").strip()
        username = str(payload.get("username") or "").strip() or None
        password = payload.get("password")
        session_token = payload.get("session_token")
        if not name or not base_url:
            return {"success": False, "message": "名称和站点地址必填"}
        if login_type not in ("password", "session"):
            return {"success": False, "message": "登录方式只能是 password 或 session"}
        secret = password if login_type == "password" else session_token
        if not secret:
            return {"success": False, "message": "缺少登录凭据"}
        if login_type == "password" and not username:
            return {"success": False, "message": "用户名必填"}
        account_id = store.create_account(
            name=name,
            base_url=base_url,
            login_type=login_type,
            username=username,
            secret_encrypted=secrets.encrypt(str(secret)),
            new_api_user=str(payload.get("new_api_user") or "").strip() or None,
            quota_unit=payload.get("quota_unit"),
            enabled=bool(payload.get("enabled", True)),
        )
        return {"success": True, "message": "账号已添加", "id": account_id}

    def update_account(self, account_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        更新签到账号。

        :param account_id: 账号 ID
        :param payload: 更新字段
        :return: 操作结果
        """
        store = self._ensure_storage()
        account = store.get_account(account_id)
        if not account:
            return {"success": False, "message": "账号不存在"}
        fields: Dict[str, Any] = {}
        if payload.get("name"):
            fields["name"] = str(payload["name"]).strip()
        if payload.get("base_url"):
            fields["base_url"] = normalize_base_url(payload["base_url"])
        if payload.get("login_type") in ("password", "session"):
            fields["login_type"] = payload["login_type"]
        if "username" in payload:
            fields["username"] = str(payload["username"] or "").strip() or None
        if "new_api_user" in payload:
            fields["new_api_user"] = str(payload["new_api_user"] or "").strip() or None
        if "quota_unit" in payload:
            fields["quota_unit"] = payload["quota_unit"]
        if "enabled" in payload:
            fields["enabled"] = 1 if payload["enabled"] else 0
        secret = payload.get("password") or payload.get("session_token")
        if secret:
            fields["secret_encrypted"] = self._secrets.encrypt(str(secret))
            fields["cached_token_encrypted"] = None
        if not fields:
            return {"success": False, "message": "没有需要更新的字段"}
        store.update_account(account_id, **fields)
        return {"success": True, "message": "账号已更新"}

    def delete_account(self, account_id: int) -> Dict[str, Any]:
        """
        删除签到账号。

        :param account_id: 账号 ID
        :return: 操作结果
        """
        store = self._ensure_storage()
        if store.delete_account(account_id):
            return {"success": True, "message": "账号已删除"}
        return {"success": False, "message": "账号不存在"}

    def test_account(self, account_id: int) -> Dict[str, Any]:
        """
        测试账号连通性。

        :param account_id: 账号 ID
        :return: 测试结果
        """
        service = self._ensure_service()
        account = service.store.get_account(account_id, include_secret=True)
        if not account:
            return {"success": False, "message": "账号不存在"}
        return service.test_account(account)

    def checkin_account(self, account_id: int) -> Dict[str, Any]:
        """
        立即为单个账号签到。

        :param account_id: 账号 ID
        :return: 签到结果
        """
        service = self._ensure_service()
        account = service.store.get_account(account_id, include_secret=True)
        if not account:
            return {"success": False, "message": "账号不存在"}
        return service.checkin_account(account)

    def refresh_balance(self, account_id: int) -> Dict[str, Any]:
        """
        刷新账号余额。

        :param account_id: 账号 ID
        :return: 刷新结果
        """
        service = self._ensure_service()
        account = service.store.get_account(account_id, include_secret=True)
        if not account:
            return {"success": False, "message": "账号不存在"}
        return service.refresh_balance(account)

    def checkin_all(self) -> Dict[str, Any]:
        """
        立即执行全部账号签到。

        :return: 汇总结果
        """
        results = self.run_checkin(trigger="手动执行")
        summary = build_summary(results, trigger="手动执行")
        return {"success": True, "message": summary["text"], "results": results}

    def list_logs(self, limit: int = 50, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        查询签到日志。

        :param limit: 返回条数
        :param account_id: 按账号过滤
        :return: 日志列表
        """
        store = self._ensure_storage()
        return store.list_logs(limit=limit, account_id=account_id)

    def get_statistics(self) -> Dict[str, Any]:
        """
        查询签到统计。

        :return: 统计字典
        """
        store = self._ensure_storage()
        return store.statistics()

    # ------------------------------------------------------------------
    # 页面
    # ------------------------------------------------------------------
    def get_form(self) -> tuple:
        """
        构造配置页面。

        :return: 页面 JSON 与默认配置
        """
        form = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {"model": "enabled", "label": "启用插件"},
                    },
                    {
                        "component": "VSwitch",
                        "props": {"model": "notify", "label": "签到完成后发送通知"},
                    },
                    {
                        "component": "VSwitch",
                        "props": {"model": "browser_headless", "label": "浏览器兜底使用无头模式"},
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "cron",
                            "label": "签到时间（Cron，5 段）",
                            "placeholder": "0 8 * * *",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "checkin_path",
                            "label": "签到接口路径",
                            "placeholder": "/api/user/self/checkin",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "max_retries",
                            "label": "失败重试次数",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "random_delay_max_seconds",
                            "label": "定时任务随机延迟上限（秒）",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "browser_timeout_seconds",
                            "label": "浏览器超时时间（秒）",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {"model": "proxy", "label": "代理地址（可选）"},
                    },
                    {
                        "component": "VBtn",
                        "props": {"text": "保存并立即签到一次", "color": "primary"},
                    },
                ],
            }
        ]
        return form, dict(DEFAULT_CONFIG)

    def get_page(self) -> List[Dict[str, Any]]:
        """
        构造插件详情页。

        :return: 页面 JSON
        """
        store = self._ensure_storage()
        accounts = store.list_accounts()
        statistics = store.statistics()
        account_lines = [
            f"{'✅' if account.get('last_checkin_result') == 'success' else '❌'} "
            f"{account.get('name')}（{account.get('base_url')}）"
            for account in accounts
        ]
        content: List[Dict[str, Any]] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": build_statistics_text(statistics),
                },
            }
        ]
        if account_lines:
            content.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "success" if all(
                            account.get("last_checkin_result") == "success" for account in accounts
                        ) else "warning",
                        "variant": "tonal",
                        "text": "\n".join(account_lines),
                    },
                }
            )
        else:
            content.append(
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "variant": "tonal",
                        "text": "尚未添加账号，可通过插件 API 添加 New-API 站点账号。",
                    },
                }
            )
        return content

    def get_dashboard(self, key: str, **kwargs: Any) -> tuple:
        """
        构造仪表盘组件。

        :param key: 仪表盘标识
        :param kwargs: 宿主附加参数
        :return: 列配置、全局配置与页面 JSON
        """
        store = self._ensure_storage()
        accounts = store.list_accounts()
        statistics = store.statistics()
        rows = []
        for account in accounts:
            rows.append(
                {
                    "name": account.get("name"),
                    "base_url": account.get("base_url"),
                    "quota": account.get("quota"),
                    "last_checkin_at": account.get("last_checkin_at"),
                    "last_checkin_result": account.get("last_checkin_result"),
                }
            )
        page = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": build_statistics_text(statistics),
                },
            },
            {
                "component": "VTable",
                "props": {
                    "items": rows,
                    "headers": [
                        {"title": "账号", "key": "name"},
                        {"title": "站点", "key": "base_url"},
                        {"title": "余额", "key": "quota"},
                        {"title": "最近签到", "key": "last_checkin_at"},
                        {"title": "结果", "key": "last_checkin_result"},
                    ],
                },
            },
        ]
        columns = {"cols": 12, "md": 6}
        settings = {"refresh": 300, "border": True, "title": "New-API 签到"}
        return columns, settings, page
