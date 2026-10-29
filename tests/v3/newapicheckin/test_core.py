"""
核心纯逻辑测试。
"""

from newapicheckin import core


def test_normalize_base_url_strips_frontend_paths():
    """前端路由路径应被剥离，API 子路径应保留。"""
    assert core.normalize_base_url("https://demo.example.com/console/token") == "https://demo.example.com"
    assert core.normalize_base_url("https://demo.example.com/login/") == "https://demo.example.com"
    assert core.normalize_base_url("https://demo.example.com/newapi/") == "https://demo.example.com/newapi"
    assert core.normalize_base_url("demo.example.com") == "https://demo.example.com"


def test_normalize_bearer_token_extracts_cookie_value():
    """应从 Cookie 串与 Bearer 前缀中提取裸令牌。"""
    assert core.normalize_bearer_token("Bearer abc123") == "abc123"
    assert core.normalize_bearer_token("session=xyz789; Path=/; HttpOnly") == "xyz789"
    assert core.normalize_bearer_token("  ") is None
    assert core.normalize_bearer_token(None) is None


def test_extract_cookie_token_returns_cookie_name():
    """应同时返回令牌与 Cookie 名。"""
    result = core.extract_cookie_token("session=abc; Path=/")
    assert result["token"] == "abc"
    assert result["cookie_name"] == "session"


def test_checkin_path_candidates_include_fallbacks():
    """候选签到路径应包含默认回退路径。"""
    candidates = core.checkin_path_candidates("/api/user/checkin")
    assert candidates[0] == "/api/user/checkin"
    assert "/api/user/self/checkin" in candidates


def test_cloudflare_and_not_found_detection():
    """应识别 Cloudflare 挑战与 404 类错误。"""
    assert core.is_cloudflare_body("Attention Required! | Cloudflare") is True
    assert core.is_cloudflare_body('{"success":true}') is False
    assert core.is_not_found_message("Invalid URL") is True
    assert core.is_token_invalid_message("无权访问") is True


def test_guess_quota_unit_detects_500000_scheme():
    """应识别 1 美元 = 500000 内部额度的实现。"""
    assert core.guess_quota_unit(500000, 0) == 500000
    assert core.guess_quota_unit(1250000, 0) == 500000
    assert core.guess_quota_unit(3.5, 1.2) is None


def test_normalize_quota_info_converts_units():
    """应按 quota_unit 把内部额度换算为货币单位。"""
    info = core.normalize_quota_info({"quota": 500000, "used_quota": 100000}, 500000)
    assert info["quota"] == 1
    assert info["used_quota"] == 0.2


def test_build_auth_headers_includes_new_api_user():
    """构造请求头时应带上 New-Api-User。"""
    headers = core.build_auth_headers("token", "42", {"X-Test": "1"})
    assert headers["Authorization"] == "Bearer token"
    assert headers["New-Api-User"] == "42"
    assert headers["X-Test"] == "1"
