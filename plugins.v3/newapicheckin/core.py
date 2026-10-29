"""
New-API 站点通用处理逻辑。

该模块只依赖 Python 标准库，便于单元测试与宿主无关地复用：
- 地址规范化：去掉 /console、/panel、/dashboard、/login 等前端路径
- 令牌规范化：从 Cookie 串、Bearer 前缀中提取裸令牌
- 配额归一化：按 quota_unit 把内部额度换算成货币单位
- Cloudflare 挑战识别：基于响应体特征做基础判定
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_CHECKIN_PATH = "/api/user/self/checkin"
CHECKIN_PATH_FALLBACKS = ("/api/user/self/checkin", "/api/user/checkin")
DEFAULT_QUOTA_UNIT = 1
# New-API 系列常见换算：1 美元 = 500000 内部额度
GUESSED_QUOTA_UNIT = 500000

_FRONTEND_PATH_MARKERS = ("/console", "/panel", "/dashboard", "/login")

_CF_KEYWORDS = (
    "cloudflare",
    "challenge-platform",
    "cf-ray",
    "cf-chl",
    "turnstile",
    "just a moment",
    "checking your browser",
)

_NOT_FOUND_KEYWORDS = (
    "not found",
    "invalid url",
    "cannot post",
    "cannot get",
    "404",
    "405",
)

_TOKEN_INVALID_KEYWORDS = (
    "access token",
    "token invalid",
    "invalid token",
    "unauthorized",
    "forbidden",
    "无权",
    "权限",
    "无效",
)


def normalize_base_url(raw_url: str) -> str:
    """
    规范化站点地址。

    去掉 /console、/panel、/dashboard、/login 等前端路由，保留部署在子路径下的
    API 前缀，并统一去掉结尾斜杠。

    :param raw_url: 用户填写的站点地址
    :return: 规范化后的站点地址
    """
    value = str(raw_url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    value = re.sub(r"/+$", "", value)
    match = re.match(r"^(?P<origin>https?://[^/]+)(?P<path>/.*)?$", value, re.IGNORECASE)
    if not match:
        return value
    origin = match.group("origin")
    path = match.group("path") or ""
    lowered = path.lower()
    if lowered and any(marker in lowered for marker in _FRONTEND_PATH_MARKERS):
        return origin
    return origin + path


def normalize_checkin_path(path: Optional[str]) -> str:
    """
    规范化签到接口路径，允许配置完整 URL。

    :param path: 配置的签到路径
    :return: 规范化后的路径
    """
    value = str(path or "").strip() or DEFAULT_CHECKIN_PATH
    if value.lower().startswith(("http://", "https://")):
        return value
    return value if value.startswith("/") else f"/{value}"


def checkin_path_candidates(configured: Optional[str]) -> List[str]:
    """
    生成签到接口候选路径列表，按配置顺序尝试。

    :param configured: 用户配置的签到路径
    :return: 候选路径列表
    """
    primary = normalize_checkin_path(configured)
    candidates = [primary]
    if not primary.lower().startswith(("http://", "https://")):
        for fallback in CHECKIN_PATH_FALLBACKS:
            if fallback not in candidates:
                candidates.append(fallback)
    return candidates


def normalize_bearer_token(token: Optional[str]) -> Optional[str]:
    """
    从 Cookie 串或带前缀的令牌中提取裸令牌。

    :param token: 原始令牌文本
    :return: 裸令牌，未取到时返回 None
    """
    if not token or not isinstance(token, str):
        return None
    trimmed = token.strip()
    if not trimmed:
        return None
    cookie_match = re.search(r"(?:^|;\s*)(session|token)=([^;]+)", trimmed, re.IGNORECASE)
    if cookie_match and cookie_match.group(2):
        trimmed = cookie_match.group(2).strip()
    lowered = trimmed.lower()
    for prefix in ("bearer ", "session=", "token="):
        if lowered.startswith(prefix):
            trimmed = trimmed[len(prefix):].strip()
            lowered = trimmed.lower()
    return trimmed or None


def extract_cookie_token(raw_token: Optional[str]) -> Dict[str, Optional[str]]:
    """
    拆解令牌与 Cookie 名，供浏览器兜底流程注入 Cookie。

    :param raw_token: 原始令牌文本
    :return: 包含 token 与 cookie_name 的字典
    """
    raw = str(raw_token or "").strip()
    if not raw:
        return {"token": None, "cookie_name": None}
    cookie_name: Optional[str] = None
    cookie_value: Optional[str] = None
    preferred = re.search(r"(?:^|;\s*)(session|token)=([^;]+)", raw, re.IGNORECASE)
    if preferred and preferred.group(2):
        cookie_name = preferred.group(1)
        cookie_value = preferred.group(2).strip()
    else:
        any_cookie = re.search(r"([^=;]+)=([^;]+)", raw)
        if any_cookie and any_cookie.group(2):
            cookie_name = any_cookie.group(1)
            cookie_value = any_cookie.group(2).strip()
    token = normalize_bearer_token(raw) or cookie_value
    return {"token": token, "cookie_name": cookie_name}


def is_cloudflare_body(text: Optional[str]) -> bool:
    """
    判断响应体是否包含 Cloudflare 挑战特征。

    :param text: 响应体文本
    :return: 是否为 Cloudflare 挑战
    """
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in _CF_KEYWORDS)


def is_not_found_message(message: Optional[str]) -> bool:
    """
    判断错误消息是否为接口不存在。

    :param message: 错误消息
    :return: 是否为 404/405 类错误
    """
    lowered = str(message or "").lower()
    return any(keyword in lowered for keyword in _NOT_FOUND_KEYWORDS)


def is_token_invalid_message(message: Optional[str]) -> bool:
    """
    判断错误消息是否为令牌失效。

    :param message: 错误消息
    :return: 是否为令牌失效
    """
    lowered = str(message or "").lower()
    return any(keyword in lowered for keyword in _TOKEN_INVALID_KEYWORDS)


def guess_quota_unit(quota: Any, used_quota: Any) -> Optional[int]:
    """
    依据额度数值猜测 quota_unit，识别 1 美元 = 500000 内部额度的常见实现。

    :param quota: 剩余额度
    :param used_quota: 已用额度
    :return: 猜测到的单位，未识别时返回 None
    """
    values = []
    for value in (quota, used_quota):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            values.append(number)
    if not values:
        return None
    for number in values:
        scaled = number / GUESSED_QUOTA_UNIT
        rounded = round(scaled, 2)
        if rounded < 0.01 or rounded > 1_000_000:
            continue
        if abs(rounded - scaled) <= 0.05:
            return GUESSED_QUOTA_UNIT
    return None


def normalize_quota_info(info: Dict[str, Any], quota_unit: Optional[int] = None) -> Dict[str, Any]:
    """
    按 quota_unit 把内部额度换算为货币单位。

    :param info: 接口返回的配额信息
    :param quota_unit: 单位换算系数，未配置时按启发式猜测
    :return: 换算后的配额信息
    """
    if not info:
        return {}
    result = dict(info)
    unit = quota_unit if quota_unit and quota_unit > 0 else None
    if not unit:
        unit = guess_quota_info_unit(result)
    if not unit or unit <= 1:
        return result

    def convert(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number / unit

    result["quota"] = convert(result.get("quota"))
    result["used_quota"] = convert(result.get("used_quota"))
    result["quota_unit"] = unit
    return result


def guess_quota_info_unit(info: Dict[str, Any]) -> Optional[int]:
    """
    在缺少配置时根据额度数值猜测 quota_unit。

    :param info: 接口返回的配额信息
    :return: 猜测到的单位
    """
    unit = info.get("quota_unit")
    try:
        if unit is not None and float(unit) > 1:
            return int(float(unit))
    except (TypeError, ValueError):
        pass
    return guess_quota_unit(info.get("quota"), info.get("used_quota"))


def build_auth_headers(
    token: Optional[str],
    new_api_user: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    构造 New-API 请求头。

    :param token: 裸令牌
    :param new_api_user: New-Api-User 头取值（系统访问令牌场景）
    :param extra_headers: 额外请求头
    :return: 请求头字典
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if new_api_user and str(new_api_user).strip():
        headers["New-Api-User"] = str(new_api_user).strip()
    for key, value in (extra_headers or {}).items():
        if value is None or value == "":
            continue
        headers[key] = str(value)
    return headers


def parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """
    解析 JSON 响应体，失败时返回 None。

    :param text: 响应体文本
    :return: 解析后的字典
    """
    if not text:
        return None
    try:
        data = text.strip()
        if data.startswith("﻿"):
            data = data.lstrip("﻿")
        import json

        parsed = json.loads(data)
    except Exception:  # pylint: disable=broad-except
        return None
    return parsed if isinstance(parsed, dict) else None


def summarize_message(message: Optional[str], limit: int = 300) -> Optional[str]:
    """
    截断消息文本，避免超长内容写入数据库或通知。

    :param message: 原始消息
    :param limit: 最大长度
    :return: 截断后的消息
    """
    if message is None:
        return None
    text = str(message).strip()
    if not text:
        return None
    return text[:limit]


def merge_headers(*headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
    合并多个请求头字典，后面的覆盖前面的。

    :param headers: 请求头字典序列
    :return: 合并后的字典
    """
    result: Dict[str, str] = {}
    for item in headers:
        if not item:
            continue
        result.update({key: value for key, value in item.items() if value not in (None, "")})
    return result


def iter_enabled(accounts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    过滤出启用状态的账号。

    :param accounts: 账号列表
    :return: 启用账号列表
    """
    return [account for account in accounts if account.get("enabled", 1)]
