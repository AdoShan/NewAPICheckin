"""
New-API 站点 HTTP 客户端。

只依赖标准库实现 HTTP 传输，便于单元测试注入替身；
浏览器兜底逻辑放在 browser.py，通过注入的浏览器工厂完成。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .core import (
    build_auth_headers,
    is_cloudflare_body,
    is_not_found_message,
    normalize_bearer_token,
    normalize_checkin_path,
    parse_json_response,
)


@dataclass
class HttpResponse:
    """
    统一的 HTTP 响应结构。
    """

    status_code: int
    text: str

    @property
    def ok(self) -> bool:
        """
        是否为 2xx 响应。

        :return: True 表示成功
        """
        return 200 <= self.status_code < 300


class UrllibTransport:
    """
    基于标准库 urllib 的 HTTP 传输实现。
    """

    def __init__(self, timeout: int = 30, proxy: Optional[str] = None) -> None:
        """
        初始化传输。

        :param timeout: 超时时间（秒）
        :param proxy: 代理地址
        """
        self.timeout = timeout
        self.proxy = proxy
        self._opener = None
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            self._opener = urllib.request.build_opener(handler)

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> HttpResponse:
        """
        发起 HTTP 请求。

        :param method: 请求方法
        :param url: 请求地址
        :param headers: 请求头
        :param body: JSON 请求体
        :param timeout: 覆盖默认超时
        :return: HTTP 响应
        """
        data = None
        request_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
        opener = self._opener or urllib.request.build_opener()
        try:
            with opener.open(request, timeout=timeout or self.timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
                return HttpResponse(status_code=response.getcode(), text=text)
        except urllib.error.HTTPError as error:  # 4xx/5xx 也需要读取响应体
            text = error.read().decode("utf-8", errors="replace") if error.fp else ""
            return HttpResponse(status_code=error.code, text=text)
        except Exception as error:  # pylint: disable=broad-except
            return HttpResponse(status_code=0, text=str(error))


class NewAPIClient:
    """
    New-API 站点接口封装。
    """

    def __init__(self, transport: Optional[Any] = None, timeout: int = 30) -> None:
        """
        初始化客户端。

        :param transport: HTTP 传输实现，默认使用标准库实现
        :param timeout: 默认超时时间
        """
        self.transport = transport or UrllibTransport(timeout=timeout)
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> HttpResponse:
        """
        发起一次带超时的请求。

        :param method: 请求方法
        :param url: 请求地址
        :param headers: 请求头
        :param body: JSON 请求体
        :return: HTTP 响应
        """
        return self.transport.request(method, url, headers=headers, body=body, timeout=self.timeout)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    def test_connection(
        self,
        base_url: str,
        token: Optional[str],
        new_api_user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        测试站点连通性与令牌有效性。

        :param base_url: 站点地址
        :param token: 裸令牌
        :param new_api_user: New-Api-User 头取值
        :return: {success, message, is_cloudflare}
        """
        url = f"{base_url}/api/user/self"
        headers = build_auth_headers(token, new_api_user)
        response = self._request("GET", url, headers)
        if response.status_code in (403, 503):
            if is_cloudflare_body(response.text):
                return {"success": False, "message": "Cloudflare 挑战拦截", "is_cloudflare": True}
            return {"success": False, "message": f"HTTP {response.status_code}: 访问被拒绝", "is_cloudflare": False}
        if response.status_code == 0:
            return {"success": False, "message": response.text or "网络请求失败", "is_cloudflare": False}
        if not response.ok:
            data = parse_json_response(response.text)
            message = (data or {}).get("message") or response.text[:200] or f"HTTP {response.status_code}"
            return {"success": False, "message": f"HTTP {response.status_code}: {message}", "is_cloudflare": False}
        data = parse_json_response(response.text)
        if not data:
            return {"success": False, "message": "接口返回不是合法 JSON", "is_cloudflare": False}
        if data.get("success") is False:
            return {
                "success": False,
                "message": data.get("message") or "令牌无效",
                "is_cloudflare": False,
            }
        return {"success": True, "message": "连接测试成功", "is_cloudflare": False}

    def fetch_user_info(
        self,
        base_url: str,
        token: Optional[str],
        new_api_user: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        获取用户信息。

        :param base_url: 站点地址
        :param token: 裸令牌
        :param new_api_user: New-Api-User 头取值
        :return: 用户数据，失败时返回 None
        """
        url = f"{base_url}/api/user/self"
        headers = build_auth_headers(token, new_api_user)
        response = self._request("GET", url, headers)
        if not response.ok:
            return None
        data = parse_json_response(response.text)
        if not data or data.get("success") is False:
            return None
        user = data.get("data") or data
        if not isinstance(user, dict):
            return None
        return {
            "quota": user.get("quota"),
            "used_quota": user.get("used_quota"),
            "username": user.get("username"),
            "display_name": user.get("display_name") or user.get("username"),
            "quota_unit": user.get("quota_unit"),
        }

    def login_with_password(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> Dict[str, Any]:
        """
        使用用户名口令登录，获取访问令牌。

        :param base_url: 站点地址
        :param username: 用户名
        :param password: 口令
        :return: {success, token, message}
        """
        url = f"{base_url}/api/user/login"
        headers = build_auth_headers(None)
        response = self._request("POST", url, headers, {"username": username, "password": password})
        data = parse_json_response(response.text)
        if not data:
            return {"success": False, "token": None, "message": response.text[:200] or "登录接口无响应"}
        if data.get("success") is False:
            return {"success": False, "token": None, "message": data.get("message") or "登录失败"}
        payload = data.get("data") or {}
        token = None
        if isinstance(payload, dict):
            user = payload.get("user")
            token = payload.get("access_token") or payload.get("token")
            if not token and isinstance(user, dict):
                token = user.get("access_token") or user.get("token")
        token = normalize_bearer_token(token)
        if not token:
            return {"success": False, "token": None, "message": "登录成功但未获取到访问令牌"}
        return {"success": True, "token": token, "message": "登录成功"}

    def perform_checkin(
        self,
        base_url: str,
        token: Optional[str],
        checkin_path: str = "/api/user/self/checkin",
        new_api_user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行签到。

        :param base_url: 站点地址
        :param token: 裸令牌
        :param checkin_path: 签到路径或完整 URL
        :param new_api_user: New-Api-User 头取值
        :return: {success, message, status, is_cloudflare, is_not_found}
        """
        path = normalize_checkin_path(checkin_path)
        url = path if path.lower().startswith(("http://", "https://")) else f"{base_url}{path}"
        headers = build_auth_headers(token, new_api_user)
        response = self._request("POST", url, headers)
        data = parse_json_response(response.text)
        message = (data or {}).get("message") or response.text[:200] or f"HTTP {response.status_code}"
        is_cloudflare = response.status_code in (403, 503) and is_cloudflare_body(response.text)
        is_not_found = response.status_code in (404, 405) or is_not_found_message(message)
        if response.ok and data is not None and data.get("success") is not False:
            return {"success": True, "message": message, "status": response.status_code}
        return {
            "success": False,
            "message": message,
            "status": response.status_code,
            "is_cloudflare": is_cloudflare,
            "is_not_found": is_not_found,
        }

    def fetch_checkin_status(
        self,
        base_url: str,
        token: Optional[str],
        checkin_path: str = "/api/user/self/checkin",
        new_api_user: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        查询当月签到状态。

        :param base_url: 站点地址
        :param token: 裸令牌
        :param checkin_path: 签到路径或完整 URL
        :param new_api_user: New-Api-User 头取值
        :return: 接口数据，失败时返回 None
        """
        from datetime import datetime  # pylint: disable=import-outside-toplevel

        path = normalize_checkin_path(checkin_path)
        month = datetime.now().strftime("%Y-%m")
        if path.lower().startswith(("http://", "https://")):
            url = f"{path}{'&' if '?' in path else '?'}month={month}"
        else:
            url = f"{base_url}{path}?month={month}"
        headers = build_auth_headers(token, new_api_user)
        response = self._request("GET", url, headers)
        if not response.ok:
            return None
        return parse_json_response(response.text)
