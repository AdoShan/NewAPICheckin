"""
存储与加密测试。
"""

from newapicheckin.crypto import SecretBox
from newapicheckin.storage import AccountStore


def make_store(tmp_path):
    """创建临时存储实例。"""
    return AccountStore(tmp_path / "test.db")


def test_account_crud(tmp_path):
    """账号应可增删改查。"""
    store = make_store(tmp_path)
    account_id = store.create_account(
        name="站点一",
        base_url="https://demo.example.com",
        login_type="session",
        secret_encrypted="cipher",
    )
    accounts = store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "站点一"
    assert "secret_encrypted" not in accounts[0]

    assert store.update_account(account_id, name="站点二") is True
    assert store.get_account(account_id)["name"] == "站点二"

    assert store.delete_account(account_id) is True
    assert store.list_accounts() == []
    store.close()


def test_logs_and_statistics(tmp_path):
    """签到日志与统计应正确累加。"""
    store = make_store(tmp_path)
    account_id = store.create_account(
        name="站点一",
        base_url="https://demo.example.com",
        login_type="session",
        secret_encrypted="cipher",
    )
    store.add_log(account_id, "站点一", "success", "签到成功", 1.0, 2.0)
    store.add_log(account_id, "站点一", "failed", "签到失败", 2.0, None)
    logs = store.list_logs()
    assert len(logs) == 2
    statistics = store.statistics()
    assert statistics["total"] == 2
    assert statistics["success"] == 1
    assert statistics["success_rate"] == 50.0
    store.close()


def test_secret_box_roundtrip(tmp_path):
    """密文应可解密还原，且不同明文密文不同。"""
    box = SecretBox(tmp_path / "secret.key")
    cipher = box.encrypt("hello-token")
    assert cipher != "hello-token"
    assert box.decrypt(cipher) == "hello-token"
    assert box.decrypt("garbage") is None
    assert box.encrypt("") is None


def test_secret_box_persists_key(tmp_path):
    """密钥文件应持久化，重建后仍可解密。"""
    key_path = tmp_path / "secret.key"
    cipher = SecretBox(key_path).encrypt("value")
    assert key_path.exists()
    assert SecretBox(key_path).decrypt(cipher) == "value"
