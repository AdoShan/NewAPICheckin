"""
签到业务编排。

把存储、加密、HTTP 客户端与浏览器兜底组合成可复用的业务流程。
本模块不依赖宿主 SDK，方便单元测试。
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, List, Optional

from .client import NewAPIClient
from .core import (
    checkin_path_candidates,
    normalize_base_url,
    normalize_bearer_token,
    normalize_quota_info,
    summarize_message,
)
from .crypto import SecretBox
from .storage import AccountStore


class CheckinService:
    """
    多账号签到服务。
    """

    def __init__(
        self,
        store: AccountStore,
        secrets: SecretBox,
        client: Optional[NewAPIClient] = None,
        browser_launcher: Optional[Callable[..., Any]] = None,
        checkin_path: str = "/api/user/self/checkin",
        headless: bool = True,
        browser_timeout_seconds: int = 60,
        retry_delay_seconds: int = 5,
        inter_account_delay_ms: int = 1500,
    ) -> None:
        """
        初始化服务。

        :param store: 账号存储
        :param secrets: 敏感信息加密容器
        :param client: HTTP 客户端
        :param browser_launcher: 浏览器上下文工厂
        :param checkin_path: 签到接口路径
        :param headless: 浏览器是否无头运行
        :param browser_timeout_seconds: 浏览器超时时间
        :param retry_delay_seconds: 重试间隔
        :param inter_account_delay_ms: 账号之间的随机间隔上限
        """
        self.store = store
        self.secrets = secrets
        self.client = client or NewAPIClient()
        self.browser_launcher = browser_launcher
        self.checkin_path = checkin_path
        self.headless = headless
        self.browser_timeout_seconds = browser_timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.inter_account_delay_ms = inter_account_delay_ms

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _decrypt(self, cipher_text: Optional[str]) -> Optional[str]:
        """
        解密敏感字段。

        :param cipher_text: 密文
        :return: 明文
        """
        return self.secrets.decrypt(cipher_text)

    def _resolve_token(self, account: Dict[str, Any]) -> Optional[str]:
        """
        解析账号当前可用的令牌。

        :param account: 账号记录
        :return: 裸令牌
        """
        if account.get("login_type") == "session":
            return normalize_bearer_token(self._decrypt(account.get("secret_encrypted")))
        cached = normalize_bearer_token(self._decrypt(account.get("cached_token_encrypted")))
        if cached:
            return cached
        username = account.get("username")
        password = self._decrypt(account.get("secret_encrypted"))
        if not username or not password:
            return None
        base_url = normalize_base_url(account.get("base_url") or "")
        login = self.client.login_with_password(base_url, username, password)
        if login.get("success") and login.get("token"):
            self.store.update_account(
                account["id"], cached_token_encrypted=self.secrets.encrypt(login["token"])
            )
            return login["token"]
        return None

    def _query_quota(self, account: Dict[str, Any], token: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        查询账号余额。

        :param account: 账号记录
        :param token: 访问令牌
        :return: 归一化后的配额信息
        """
        if not token:
            return None
        base_url = normalize_base_url(account.get("base_url") or "")
        info = self.client.fetch_user_info(base_url, token, account.get("new_api_user"))
        if not info:
            return None
        return normalize_quota_info(info, account.get("quota_unit"))

    def _record(self, account: Dict[str, Any], status: str, message: Optional[str]) -> None:
        """
        记录账号最近一次签到状态。

        :param account: 账号记录
        :param status: 状态
        :param message: 消息
        """
        from .storage import now  # pylint: disable=import-outside-toplevel

        self.store.update_account(
            account["id"],
            last_checkin_at=now(),
            last_checkin_result=status,
            last_error_message=summarize_message(message) if status == "failed" else None,
            last_error_at=now() if status == "failed" else None,
        )

    def _browser_fallback(self, account: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
        """
        调用浏览器兜底流程。

        :param account: 账号记录
        :param token: 已获取的令牌
        :return: {success, message, token}
        """
        if not self.browser_launcher:
            return {"success": False, "message": "未配置浏览器兜底能力", "token": token}
        from .browser import browser_checkin  # pylint: disable=import-outside-toplevel

        base_url = normalize_base_url(account.get("base_url") or "")
        secret = self._decrypt(account.get("secret_encrypted"))
        return browser_checkin(
            self.browser_launcher,
            base_url=base_url,
            login_type=account.get("login_type") or "password",
            username=account.get("username"),
            password=secret if account.get("login_type") != "session" else None,
            session_token=secret if account.get("login_type") == "session" else None,
            checkin_path=self.checkin_path,
            new_api_user=account.get("new_api_user"),
            headless=self.headless,
            timeout_ms=self.browser_timeout_seconds * 1000,
        )

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    def test_account(self, account: Dict[str, Any]) -> Dict[str, Any]:
        """
        测试账号连通性，必要时回退浏览器流程。

        :param account: 账号记录
        :return: {success, message}
        """
        base_url = normalize_base_url(account.get("base_url") or "")
        token = self._resolve_token(account)
        if token:
            result = self.client.test_connection(base_url, token, account.get("new_api_user"))
            if result.get("success") or not result.get("is_cloudflare"):
                self._record(account, "success" if result.get("success") else "failed", result.get("message"))
                return result
        fallback = self._browser_fallback(account, token)
        self._record(account, "success" if fallback.get("success") else "failed", fallback.get("message"))
        return {"success": bool(fallback.get("success")), "message": fallback.get("message")}

    def checkin_account(self, account: Dict[str, Any]) -> Dict[str, Any]:
        """
        为单个账号执行签到。

        :param account: 账号记录
        :return: {success, message, quota_before, quota_after}
        """
        base_url = normalize_base_url(account.get("base_url") or "")
        token = self._resolve_token(account)
        quota_before = self._query_quota(account, token)
        before_value = quota_before.get("quota") if quota_before else None

        result: Dict[str, Any] = {"success": False, "message": "未执行"}
        if token:
            for path in checkin_path_candidates(self.checkin_path):
                direct = self.client.perform_checkin(
                    base_url, token, path, account.get("new_api_user")
                )
                if direct.get("success"):
                    result = {"success": True, "message": direct.get("message") or "签到成功"}
                    if path != self.checkin_path:
                        self.checkin_path = path
                    break
                if direct.get("is_not_found"):
                    continue
                if direct.get("is_cloudflare") or account.get("login_type") == "session":
                    result = direct
                    break
                result = direct
                break

        if not result.get("success"):
            fallback = self._browser_fallback(account, token)
            if fallback.get("success"):
                result = {"success": True, "message": fallback.get("message") or "签到成功"}
                if fallback.get("token") and fallback["token"] != token:
                    self.store.update_account(
                        account["id"],
                        cached_token_encrypted=self.secrets.encrypt(fallback["token"]),
                    )
                    token = fallback["token"]
            else:
                result = {
                    "success": False,
                    "message": fallback.get("message") or result.get("message") or "签到失败",
                }

        quota_after = self._query_quota(account, token)
        after_value = quota_after.get("quota") if quota_after else None
        if quota_after:
            self.store.update_account(
                account["id"],
                quota=quota_after.get("quota"),
                used_quota=quota_after.get("used_quota"),
                quota_unit=quota_after.get("quota_unit") or account.get("quota_unit"),
                balance_updated_at=quota_after.get("updated_at"),
            )

        message = summarize_message(result.get("message"))
        status = "success" if result.get("success") else "failed"
        self.store.add_log(
            account["id"],
            account.get("name") or "",
            status,
            message,
            before_value,
            after_value,
        )
        self._record(account, status, message)
        return {
            "success": status == "success",
            "message": message,
            "quota_before": before_value,
            "quota_after": after_value,
        }

    def checkin_all(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        """
        为所有账号执行签到，账号之间加入随机延迟。

        :param only_enabled: 是否只处理启用账号
        :return: 每个账号的结果列表
        """
        accounts = self.store.list_accounts(include_secret=True)
        if only_enabled:
            accounts = [account for account in accounts if account.get("enabled", 1)]
        results: List[Dict[str, Any]] = []
        for index, account in enumerate(accounts):
            outcome = self.checkin_account(account)
            results.append(
                {
                    "account_id": account.get("id"),
                    "name": account.get("name"),
                    "success": outcome.get("success"),
                    "message": outcome.get("message"),
                    "quota_after": outcome.get("quota_after"),
                }
            )
            if index < len(accounts) - 1:
                time.sleep(random.uniform(0.5, max(self.inter_account_delay_ms, 500) / 1000))
        return results

    def refresh_balance(self, account: Dict[str, Any]) -> Dict[str, Any]:
        """
        刷新账号余额。

        :param account: 账号记录
        :return: {success, message, quota, used_quota}
        """
        token = self._resolve_token(account)
        info = self._query_quota(account, token)
        if not info:
            return {"success": False, "message": "余额查询失败"}
        self.store.update_account(
            account["id"],
            quota=info.get("quota"),
            used_quota=info.get("used_quota"),
            quota_unit=info.get("quota_unit") or account.get("quota_unit"),
        )
        return {
            "success": True,
            "message": "余额已更新",
            "quota": info.get("quota"),
            "used_quota": info.get("used_quota"),
        }

    def retry_checkin(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """
        批量签到并在失败时重试。

        :param max_retries: 最大尝试次数
        :return: 最后一次批量签到结果
        """
        results: List[Dict[str, Any]] = []
        for attempt in range(1, max(int(max_retries), 1) + 1):
            results = self.checkin_all()
            if not results or all(item.get("success") for item in results):
                return results
            if attempt < max_retries:
                time.sleep(self.retry_delay_seconds)
        return results
