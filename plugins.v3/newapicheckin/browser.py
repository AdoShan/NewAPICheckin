"""
浏览器兜底流程。

当站点被 Cloudflare 拦截、或直接 API 登录失败时，使用宿主提供的浏览器上下文
完成登录与签到。浏览器工厂由插件层注入，便于单元测试替换。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .core import (
    build_auth_headers,
    checkin_path_candidates,
    extract_cookie_token,
    is_not_found_message,
    normalize_bearer_token,
)

CF_WAIT_TIMEOUT_MS = 30000
CF_POLL_INTERVAL_MS = 500


def _detect_challenge(page: Any) -> Dict[str, Any]:
    """
    在页面上下文内检测 Cloudflare 挑战特征。

    :param page: Playwright 页面对象
    :return: {is_challenge, score, strong, support}
    """
    script = """
    () => {
      let score = 0;
      const strong = [];
      const support = [];
      if (typeof window._cf_chl_opt !== 'undefined') { score += 3; strong.push('_cf_chl_opt'); }
      const scripts = Array.from(document.querySelectorAll('script[src]'));
      if (scripts.some(s => s.src.includes('challenge-platform'))) { score += 3; strong.push('challenge-platform'); }
      if (scripts.some(s => s.src.includes('trace-jsch'))) { score += 3; strong.push('trace-jsch'); }
      if (document.querySelector('form#challenge-form, form[action*="challenge"]')) { score += 3; strong.push('challenge-form'); }
      if (document.querySelector('#cf-content')) { score += 2; support.push('cf-content'); }
      if (document.querySelector('#cf-wrapper')) { score += 2; support.push('cf-wrapper'); }
      const bodyText = document.body ? (document.body.innerText || '') : '';
      if (bodyText.includes('Error 1020') || bodyText.includes('Access denied')) { score += 2; support.push('cf-error'); }
      if (window.location.href.includes('/cdn-cgi/')) { score += 2; support.push('cdn-cgi-url'); }
      const title = (document.title || '').toLowerCase();
      if (title.includes('just a moment') || title.includes('please wait') || title.includes('attention required')) {
        score += 1; support.push('cf-title');
      }
      if (document.querySelector('iframe[src*="turnstile"], .cf-turnstile, [data-sitekey]')) { score += 1; support.push('turnstile'); }
      return { isChallenge: strong.length > 0 || (score >= 3 && support.length > 0), score, strong, support };
    }
    """
    try:
        result = page.evaluate(script)
    except Exception:  # pylint: disable=broad-except
        return {"is_challenge": False, "score": 0, "strong": [], "support": []}
    if not isinstance(result, dict):
        return {"is_challenge": False, "score": 0, "strong": [], "support": []}
    return {
        "is_challenge": bool(result.get("isChallenge")),
        "score": result.get("score") or 0,
        "strong": result.get("strong") or [],
        "support": result.get("support") or [],
    }


def wait_for_challenge(page: Any, timeout_ms: int = CF_WAIT_TIMEOUT_MS) -> bool:
    """
    等待 Cloudflare 挑战自行通过。

    :param page: Playwright 页面对象
    :param timeout_ms: 最长等待时间
    :return: 挑战是否已解除
    """
    import time  # pylint: disable=import-outside-toplevel

    state = _detect_challenge(page)
    if not state["is_challenge"]:
        return True
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        time.sleep(CF_POLL_INTERVAL_MS / 1000)
        state = _detect_challenge(page)
        if not state["is_challenge"]:
            return True
    return False


def _extract_token(page: Any) -> Optional[str]:
    """
    从页面存储或 Cookie 中提取访问令牌。

    :param page: Playwright 页面对象
    :return: 裸令牌
    """
    try:
        token = page.evaluate(
            """
            () => {
              const fromLocal = localStorage.getItem('token') || localStorage.getItem('session');
              if (fromLocal) return fromLocal;
              try {
                const user = JSON.parse(localStorage.getItem('user') || '{}');
                if (user && user.token) return user.token;
              } catch (e) { /* ignore */ }
              return null;
            }
            """
        )
    except Exception:  # pylint: disable=broad-except
        token = None
    if token:
        return normalize_bearer_token(token)
    try:
        cookies = page.context.cookies()
    except Exception:  # pylint: disable=broad-except
        cookies = []
    for cookie in cookies or []:
        if cookie.get("name") in ("session", "token"):
            return normalize_bearer_token(cookie.get("value"))
    return None


def _login_with_password(page: Any, base_url: str, username: str, password: str, timeout_ms: int) -> None:
    """
    在浏览器中执行用户名口令登录。

    :param page: Playwright 页面对象
    :param base_url: 站点地址
    :param username: 用户名
    :param password: 口令
    :param timeout_ms: 超时时间
    """
    page.goto(f"{base_url}/login", wait_until="networkidle2", timeout=timeout_ms)
    wait_for_challenge(page, min(timeout_ms, CF_WAIT_TIMEOUT_MS))
    page.wait_for_selector("input", timeout=min(timeout_ms, 15000))
    username_input = page.locator(
        'input[name="username"], input[id="username"], input[autocomplete="username"]'
    ).first
    password_input = page.locator(
        'input[name="password"], input[id="password"], input[type="password"]'
    ).first
    if username_input.count() and password_input.count():
        username_input.fill(username)
        password_input.fill(password)
    else:
        inputs = page.locator("input")
        inputs.nth(0).fill(username)
        inputs.nth(1).fill(password)
    wait_for_challenge(page, min(timeout_ms, CF_WAIT_TIMEOUT_MS))
    submit = page.locator('button[type="submit"], button.btn-primary, button:not([type="button"])').first
    submit.click()
    try:
        page.wait_for_load_state("networkidle2", timeout=timeout_ms)
    except Exception:  # pylint: disable=broad-except
        pass


def _login_with_session(page: Any, base_url: str, token: str, timeout_ms: int) -> None:
    """
    在浏览器中注入会话 Cookie 与本地存储。

    :param page: Playwright 页面对象
    :param base_url: 站点地址
    :param token: 会话令牌
    :param timeout_ms: 超时时间
    """
    parsed = extract_cookie_token(token)
    value = parsed.get("token")
    if not value:
        raise ValueError("会话令牌为空")
    page.goto(base_url, wait_until="networkidle2", timeout=timeout_ms)
    wait_for_challenge(page, min(timeout_ms, CF_WAIT_TIMEOUT_MS))
    host = urlparse(base_url).hostname or ""
    cookie_names = {"session", "token"}
    if parsed.get("cookie_name"):
        cookie_names.add(parsed["cookie_name"])
    page.context.add_cookies(
        [
            {
                "name": name,
                "value": value,
                "domain": host,
                "path": "/",
                "secure": base_url.startswith("https://"),
            }
            for name in cookie_names
        ]
    )
    page.evaluate(
        """
        (token) => {
          localStorage.setItem('session', token);
          localStorage.setItem('token', token);
          try { localStorage.setItem('user', JSON.stringify({ token })); } catch (e) { /* ignore */ }
        }
        """,
        value,
    )
    page.reload(wait_until="networkidle2", timeout=timeout_ms)


def _page_fetch(
    page: Any,
    method: str,
    url: str,
    token: Optional[str],
    new_api_user: Optional[str],
) -> Dict[str, Any]:
    """
    在页面上下文中发起带 Cookie 的 fetch 请求。

    :param page: Playwright 页面对象
    :param method: 请求方法
    :param url: 请求地址
    :param token: 可选令牌
    :param new_api_user: New-Api-User 头取值
    :return: {ok, status, data, text}
    """
    headers = build_auth_headers(token, new_api_user)
    return page.evaluate(
        """
        async ({method, url, headers}) => {
          try {
            const res = await fetch(url, { method, credentials: 'include', headers });
            const text = await res.text();
            let data = null;
            try { data = JSON.parse(text); } catch (e) { data = null; }
            return { ok: res.ok, status: res.status, data, text: text.slice(0, 300) };
          } catch (e) {
            return { ok: false, status: 0, data: null, text: e.message || 'request failed' };
          }
        }
        """,
        {"method": method, "url": url, "headers": headers},
    )


def browser_checkin(
    launcher: Callable[..., Any],
    base_url: str,
    login_type: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    session_token: Optional[str] = None,
    checkin_path: str = "/api/user/self/checkin",
    new_api_user: Optional[str] = None,
    headless: bool = True,
    timeout_ms: int = 60000,
) -> Dict[str, Any]:
    """
    使用浏览器完成登录与签到。

    :param launcher: 浏览器上下文工厂，签名与 app.sdk.browser.launch_browser_context 一致
    :param base_url: 站点地址
    :param login_type: 登录方式，password 或 session
    :param username: 用户名
    :param password: 口令
    :param session_token: 会话令牌
    :param checkin_path: 签到路径
    :param new_api_user: New-Api-User 头取值
    :param headless: 是否无头运行
    :param timeout_ms: 超时时间
    :return: {success, message, token}
    """
    try:
        with launcher(headless=headless) as context:
            pages = getattr(context, "pages", None)
            page = pages[0] if pages else context.new_page()
            page.set_default_timeout(timeout_ms)
            if login_type == "session":
                if not session_token:
                    return {"success": False, "message": "缺少会话令牌", "token": None}
                _login_with_session(page, base_url, session_token, timeout_ms)
                token = normalize_bearer_token(session_token)
            else:
                if not username or not password:
                    return {"success": False, "message": "缺少用户名或口令", "token": None}
                _login_with_password(page, base_url, username, password, timeout_ms)
                token = _extract_token(page)

            candidates: List[str] = checkin_path_candidates(checkin_path)
            last_message = "签到失败"
            for path in candidates:
                url = path if path.lower().startswith(("http://", "https://")) else f"{base_url}{path}"
                response = _page_fetch(page, "POST", url, token, new_api_user)
                if response.get("ok") and (response.get("data") or {}).get("success") is not False:
                    message = (response.get("data") or {}).get("message") or "签到成功"
                    return {"success": True, "message": message, "token": token}
                message = (response.get("data") or {}).get("message") or response.get("text") or "签到失败"
                if response.get("status") in (404, 405) or is_not_found_message(message):
                    last_message = message
                    continue
                return {"success": False, "message": message, "token": token}
            return {"success": False, "message": last_message or "未找到可用的签到接口", "token": token}
    except Exception as error:  # pylint: disable=broad-except
        return {"success": False, "message": f"浏览器兜底失败：{error}", "token": None}
