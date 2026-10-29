"""
签到结果通知文案组装。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_result_line(item: Dict[str, Any]) -> str:
    """
    生成单账号签到结果行。

    :param item: 签到结果
    :return: 文本行
    """
    icon = "✅" if item.get("success") else "❌"
    name = item.get("name") or item.get("account_id") or "未知账号"
    message = item.get("message") or ""
    quota = item.get("quota_after")
    quota_text = ""
    if isinstance(quota, (int, float)):
        quota_text = f"（余额 {quota:.2f}）"
    return f"{icon} {name}{quota_text} {message}".strip()


def build_summary(
    results: List[Dict[str, Any]],
    title: str = "New-API 签到结果",
    trigger: str = "定时任务",
) -> Dict[str, Any]:
    """
    组装签到汇总通知。

    :param results: 签到结果列表
    :param title: 通知标题
    :param trigger: 触发来源
    :return: {title, text, success, total}
    """
    total = len(results or [])
    success = sum(1 for item in (results or []) if item.get("success"))
    lines = [format_result_line(item) for item in (results or [])]
    summary = f"{trigger}：成功 {success}/{total}"
    text = "\n".join(lines + ["", summary]) if lines else summary
    return {
        "title": title,
        "text": text,
        "success": success,
        "total": total,
        "all_success": success == total and total > 0,
    }


def build_account_line(account: Dict[str, Any]) -> str:
    """
    生成账号状态行，用于详情页与仪表板。

    :param account: 账号记录
    :return: 文本行
    """
    status = account.get("last_checkin_result")
    icon = "✅" if status == "success" else ("❌" if status == "failed" else "➖")
    quota = account.get("quota")
    quota_text = f"，余额 {quota:.2f}" if isinstance(quota, (int, float)) else ""
    return f"{icon} {account.get('name')}（{account.get('base_url')}）{quota_text}"


def build_statistics_text(statistics: Optional[Dict[str, Any]]) -> str:
    """
    生成统计数据文本。

    :param statistics: 统计字典
    :return: 文本
    """
    if not statistics:
        return "暂无签到记录"
    return (
        f"累计签到 {statistics.get('total', 0)} 次，"
        f"成功 {statistics.get('success', 0)} 次，"
        f"成功率 {statistics.get('success_rate', 0)}%"
    )
