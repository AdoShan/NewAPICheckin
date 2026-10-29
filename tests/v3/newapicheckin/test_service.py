"""
签到业务编排测试。
"""

from newapicheckin.client import NewAPIClient
from newapicheckin.crypto import SecretBox
from newapicheckin.service import CheckinService
from newapicheckin.storage import AccountStore

from test_client import FakeTransport


def build_service(tmp_path, routes, browser_launcher=None):
    """构造使用替身传输的签到服务。"""
    store = AccountStore(tmp_path / "svc.db")
    secrets = SecretBox(tmp_path / "secret.key")
    client = NewAPIClient(transport=FakeTransport(routes))
    service = CheckinService(
        store=store,
        secrets=secrets,
        client=client,
        browser_launcher=browser_launcher,
    )
    return service


def add_session_account(service, token="tok"):
    """新增一个使用会话令牌的账号。"""
    return service.store.create_account(
        name="站点一",
        base_url="https://demo.example.com",
        login_type="session",
        secret_encrypted=service.secrets.encrypt(token),
    )


def test_checkin_account_success_updates_balance(tmp_path):
    """签到成功后应写入日志并更新余额。"""
    service = build_service(
        tmp_path,
        {
            "/api/user/self/checkin": (200, '{"success":true,"message":"签到成功"}'),
            "/api/user/self": (200, '{"success":true,"data":{"quota":500000,"used_quota":100000}}'),
        },
    )
    account_id = add_session_account(service)
    account = service.store.get_account(account_id, include_secret=True)
    result = service.checkin_account(account)
    assert result["success"] is True
    assert result["quota_after"] == 1.0

    logs = service.store.list_logs()
    assert logs[0]["status"] == "success"
    assert service.store.get_account(account_id)["last_checkin_result"] == "success"
    service.store.close()


def test_checkin_account_falls_back_to_browser_on_cloudflare(tmp_path):
    """Cloudflare 拦截时应进入浏览器兜底流程。"""
    calls = {}

    def fake_launcher(headless=True, **kwargs):
        calls["headless"] = headless
        raise RuntimeError("浏览器不可用")

    service = build_service(
        tmp_path,
        {
            "/api/user/self/checkin": (403, "<html>Just a moment... Cloudflare</html>"),
        },
        browser_launcher=fake_launcher,
    )
    account_id = add_session_account(service)
    account = service.store.get_account(account_id, include_secret=True)
    result = service.checkin_account(account)
    assert result["success"] is False
    assert "浏览器兜底失败" in result["message"]
    assert calls.get("headless") is True
    service.store.close()


def test_checkin_all_skips_disabled_accounts(tmp_path):
    """停用的账号不应参与批量签到。"""
    service = build_service(
        tmp_path,
        {
            "/api/user/self/checkin": (200, '{"success":true,"message":"签到成功"}'),
            "/api/user/self": (200, '{"success":true,"data":{"quota":500000}}'),
        },
    )
    account_id = add_session_account(service)
    service.store.update_account(account_id, enabled=0)
    results = service.checkin_all()
    assert results == []
    service.store.close()


def test_test_account_uses_direct_api(tmp_path):
    """连通性测试应优先走直接 API。"""
    service = build_service(
        tmp_path,
        {"/api/user/self": (200, '{"success":true,"data":{"username":"demo"}}')},
    )
    account_id = add_session_account(service)
    account = service.store.get_account(account_id, include_secret=True)
    result = service.test_account(account)
    assert result["success"] is True
    service.store.close()
