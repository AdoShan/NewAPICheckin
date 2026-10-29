"""
插件自有 SQLite 存储。

数据库文件放在插件数据目录（get_data_path），插件更新不会覆盖。
所有写入都使用参数化 SQL，敏感字段（密码、令牌）以密文保存。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    login_type TEXT NOT NULL DEFAULT 'password',
    username TEXT,
    secret_encrypted TEXT,
    new_api_user TEXT,
    quota_unit REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    cached_token_encrypted TEXT,
    quota REAL,
    used_quota REAL,
    balance_updated_at TEXT,
    last_checkin_at TEXT,
    last_checkin_result TEXT,
    last_error_message TEXT,
    last_error_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    account_name TEXT,
    status TEXT NOT NULL,
    message TEXT,
    quota_before REAL,
    quota_after REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkin_logs_account ON checkin_logs(account_id);
CREATE INDEX IF NOT EXISTS idx_checkin_logs_created ON checkin_logs(created_at);
"""


def now() -> str:
    """
    返回本地时间字符串。

    :return: 形如 2026-10-29 12:00:00 的时间文本
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AccountStore:
    """
    账号与签到日志的 SQLite 存储封装。
    """

    def __init__(self, db_path: Path) -> None:
        """
        初始化存储。

        :param db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        """
        关闭数据库连接。
        """
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pylint: disable=broad-except
                pass

    # ------------------------------------------------------------------
    # 账号
    # ------------------------------------------------------------------
    def list_accounts(self, include_secret: bool = False) -> List[Dict[str, Any]]:
        """
        列出全部账号。

        :param include_secret: 是否返回密文字段
        :return: 账号字典列表
        """
        with self._lock:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()
        accounts = []
        for row in rows:
            account = dict(row)
            if not include_secret:
                account.pop("secret_encrypted", None)
                account.pop("cached_token_encrypted", None)
            accounts.append(account)
        return accounts

    def get_account(self, account_id: int, include_secret: bool = False) -> Optional[Dict[str, Any]]:
        """
        按 ID 读取账号。

        :param account_id: 账号 ID
        :param include_secret: 是否返回密文字段
        :return: 账号字典，不存在时返回 None
        """
        with self._lock:
            row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            return None
        account = dict(row)
        if not include_secret:
            account.pop("secret_encrypted", None)
            account.pop("cached_token_encrypted", None)
        return account

    def create_account(
        self,
        name: str,
        base_url: str,
        login_type: str,
        username: Optional[str] = None,
        secret_encrypted: Optional[str] = None,
        new_api_user: Optional[str] = None,
        quota_unit: Optional[float] = None,
        enabled: bool = True,
    ) -> int:
        """
        新增账号。

        :param name: 账号名称
        :param base_url: 站点地址
        :param login_type: 登录方式，password 或 session
        :param username: 用户名
        :param secret_encrypted: 密文口令或会话令牌
        :param new_api_user: New-Api-User 头取值
        :param quota_unit: 额度单位
        :param enabled: 是否启用
        :return: 新账号 ID
        """
        timestamp = now()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO accounts (
                    name, base_url, login_type, username, secret_encrypted, new_api_user,
                    quota_unit, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    base_url,
                    login_type,
                    username,
                    secret_encrypted,
                    new_api_user,
                    quota_unit,
                    1 if enabled else 0,
                    timestamp,
                    timestamp,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_account(self, account_id: int, **fields: Any) -> bool:
        """
        更新账号字段。

        :param account_id: 账号 ID
        :param fields: 待更新字段
        :return: 是否命中账号
        """
        allowed = {
            "name",
            "base_url",
            "login_type",
            "username",
            "secret_encrypted",
            "new_api_user",
            "quota_unit",
            "enabled",
            "cached_token_encrypted",
            "quota",
            "used_quota",
            "balance_updated_at",
            "last_checkin_at",
            "last_checkin_result",
            "last_error_message",
            "last_error_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return False
        updates["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [account_id]
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE accounts SET {assignments} WHERE id = ?", values
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_account(self, account_id: int) -> bool:
        """
        删除账号及其日志。

        :param account_id: 账号 ID
        :return: 是否删除成功
        """
        with self._lock:
            self._conn.execute("DELETE FROM checkin_logs WHERE account_id = ?", (account_id,))
            cursor = self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def set_accounts_enabled(self, enabled: bool) -> int:
        """
        批量启用或停用账号。

        :param enabled: 目标状态
        :return: 受影响账号数量
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE accounts SET enabled = ?, updated_at = ?",
                (1 if enabled else 0, now()),
            )
            self._conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def add_log(
        self,
        account_id: int,
        account_name: str,
        status: str,
        message: Optional[str] = None,
        quota_before: Optional[float] = None,
        quota_after: Optional[float] = None,
    ) -> int:
        """
        写入一条签到日志。

        :param account_id: 账号 ID
        :param account_name: 账号名称
        :param status: 状态，success 或 failed
        :param message: 结果消息
        :param quota_before: 签到前余额
        :param quota_after: 签到后余额
        :return: 日志 ID
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO checkin_logs (
                    account_id, account_name, status, message, quota_before, quota_after, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    account_name,
                    status,
                    message,
                    quota_before,
                    quota_after,
                    now(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_logs(self, limit: int = 50, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        查询签到日志。

        :param limit: 返回条数
        :param account_id: 按账号过滤
        :return: 日志字典列表
        """
        query = "SELECT * FROM checkin_logs"
        params: List[Any] = []
        if account_id is not None:
            query += " WHERE account_id = ?"
            params.append(account_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def statistics(self) -> Dict[str, Any]:
        """
        统计签到成功率与最近 14 天每日结果。

        :return: 统计字典
        """
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM checkin_logs").fetchone()[0]
            success = self._conn.execute(
                "SELECT COUNT(*) FROM checkin_logs WHERE status = 'success'"
            ).fetchone()[0]
            rows = self._conn.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                       COUNT(*) AS total
                FROM checkin_logs
                WHERE created_at >= datetime('now', '-14 days')
                GROUP BY day ORDER BY day ASC
                """
            ).fetchall()
        return {
            "total": total,
            "success": success,
            "failed": total - success,
            "success_rate": round(success / total * 100, 2) if total else 0.0,
            "daily": [dict(row) for row in rows],
        }
