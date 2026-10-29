"""
HTTP 客户端测试（使用替身传输，不访问公网）。
"""

from newapicheckin.client import NewAPIClient


class FakeTransport:
    """按请求签名返回预设响应的传输替身。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, headers=None, body=None, timeout=None):
        """返回预设响应。"""
        self.calls.append((method, url, headers, body))
        for key, response in self.routes.items():
            if key in url:
                status, text = response
                return type("Resp", (), {"status_code": status, "text": text, "ok": 200 <= status < 300})()
        return type("Resp", (), {"status_code": 404, "text": "not found", "ok": False})()


def test_test_connection_success():
    """合法令牌应返回连接成功。"""
    transport = FakeTransport({"/api/user/self": (200, '{"success":true,"data":{"username":"demo"}}')})
    client = NewAPIClient(transport=transport)
    result = client.test_connection("https://demo.example.com", "token")
    assert result["success"] is True


def test_test_connection_detects_cloudflare():
    """Cloudflare 挑战应被识别。"""
    transport = FakeTransport({"/api/user/self": (403, "<html>Just a moment... Cloudflare</html>")})
    client = NewAPIClient(transport=transport)
    result = client.test_connection("https://demo.example.com", "token")
    assert result["success"] is False
    assert result["is_cloudflare"] is True


def test_login_with_password_extracts_token():
    """登录成功后应提取访问令牌。"""
    transport = FakeTransport(
        {"/api/user/login": (200, '{"success":true,"data":{"access_token":"tok-123"}}')}
    )
    client = NewAPIClient(transport=transport)
    result = client.login_with_password("https://demo.example.com", "demo", "pwd")
    assert result["success"] is True
    assert result["token"] == "tok-123"


def test_login_failure_returns_message():
    """登录失败应返回接口消息。"""
    transport = FakeTransport({"/api/user/login": (200, '{"success":false,"message":"用户名或密码错误"}')})
    client = NewAPIClient(transport=transport)
    result = client.login_with_password("https://demo.example.com", "demo", "bad")
    assert result["success"] is False
    assert "用户名或密码错误" in result["message"]


def test_perform_checkin_success_and_not_found():
    """签到成功与接口不存在都应被区分。"""
    transport = FakeTransport({"/api/user/self/checkin": (200, '{"success":true,"message":"签到成功"}')})
    client = NewAPIClient(transport=transport)
    result = client.perform_checkin("https://demo.example.com", "token")
    assert result["success"] is True

    transport = FakeTransport({"/api/user/self/checkin": (404, "404 page not found")})
    client = NewAPIClient(transport=transport)
    result = client.perform_checkin("https://demo.example.com", "token")
    assert result["success"] is False
    assert result["is_not_found"] is True
