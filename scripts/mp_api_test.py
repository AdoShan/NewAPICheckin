#!/usr/bin/env python3
"""
MoviePilot 插件 API 黑盒测试（NewAPICheckin）。

用法:
    python scripts/mp_api_test.py --url http://<moviepilot>:3000 --token ***
    python scripts/mp_api_test.py --url ... --token *** \
        --account-url https://你的newapi站点 --account-token '<session 值>'

需要 MoviePilot 的访问令牌（登录 /api/v1/login/access-token 或 UI 的 API 令牌）。
脚本只操作本插件自己的账号数据，测试结束会删除测试账号。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

PLUGIN_ID = "NewAPICheckin"
BASE_PATH = f"/api/v1/plugin/{PLUGIN_ID}"

PASSED: list[str] = []
FAILED: list[str] = []


def unwrap(body):
    """解包宿主统一响应结构 {"success": bool, "data": ...}。"""
    if isinstance(body, dict) and "success" in body and "data" in body:
        return body.get("data")
    return body


def check(name: str, condition: bool, detail: str = "") -> None:
    """记录一条检查结果。"""
    if condition:
        PASSED.append(name)
        print(f"  \033[32mPASS\033[0m {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  \033[31mFAIL\033[0m {name} :: {detail}")


def request(base: str, token: str, path: str, method: str = "GET", payload=None, timeout=60):
    """发起一次 API 请求。"""
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.getcode(), json.loads(body)
            except json.JSONDecodeError:
                return resp.getcode(), body
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        return err.code, body
    except Exception as err:  # pylint: disable=broad-except
        return 0, str(err)


def main() -> int:
    """执行测试流程。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="MoviePilot 根地址，如 http://127.0.0.1:9001")
    parser.add_argument("--token", required=True, help="访问令牌")
    parser.add_argument("--account-url", default="", help="真实 New-API 站点地址（可选，用于端到端签到）")
    parser.add_argument("--account-token", default="", help="真实站点的会话令牌（可选）")
    args = parser.parse_args()

    base, token = args.url, args.token

    print("\n\033[1m== 1. 插件安装与启用状态\033[0m")
    status, body = request(base, token, "/api/v1/plugin/installed")
    check("GET /plugin/installed 可访问", status == 200, f"HTTP {status}")
    installed = unwrap(body)
    if isinstance(installed, list):
        check("已安装插件列表包含 NewAPICheckin", PLUGIN_ID in installed, f"列表: {installed}")
    else:
        check("已安装插件列表可解析", False, str(body)[:200])

    status, body = request(base, token, "/api/v1/plugin/")
    catalogue = unwrap(body)
    entry = None
    if isinstance(catalogue, list):
        entry = next((item for item in catalogue if item.get("id") == PLUGIN_ID), None)
    check("插件出现在插件目录中", entry is not None, f"HTTP {status}")
    if entry:
        check("插件版本为 1.0.0", str(entry.get("plugin_version")) == "1.0.0", str(entry.get("plugin_version")))
        check("插件已安装", bool(entry.get("installed")), str(entry.get("installed")))
        check("插件已启用", bool(entry.get("state")), str(entry.get("state")))

    status, body = request(base, token, f"/api/v1/plugin/{PLUGIN_ID}")
    check("GET /plugin/NewAPICheckin 可访问", status == 200, f"HTTP {status}")

    print("\n\033[1m== 2. 页面与仪表板\033[0m")
    for path, label in [
        (f"/api/v1/plugin/form/{PLUGIN_ID}", "配置页"),
        (f"/api/v1/plugin/page/{PLUGIN_ID}", "详情页"),
        (f"/api/v1/plugin/dashboard/{PLUGIN_ID}", "仪表板"),
    ]:
        status, body = request(base, token, path)
        check(f"{label}接口返回 200", status == 200, f"HTTP {status} {str(body)[:120]}")

    print("\n\033[1m== 3. 插件 API\033[0m")
    status, body = request(base, token, f"{BASE_PATH}/accounts")
    accounts = unwrap(body)
    check("GET /accounts 返回 200 且为数组", status == 200 and isinstance(accounts, list), f"HTTP {status} {str(body)[:120]}")

    status, body = request(base, token, f"{BASE_PATH}/stats")
    stats = unwrap(body)
    check("GET /stats 返回统计字典", status == 200 and isinstance(stats, dict) and "total" in stats, f"HTTP {status}")

    status, body = request(base, token, f"{BASE_PATH}/logs?limit=5")
    logs = unwrap(body)
    check("GET /logs 返回数组", status == 200 and isinstance(logs, list), f"HTTP {status}")

    account_id = None
    print("\n\033[1m== 4. 账号增删改查\033[0m")
    status, body = request(
        base,
        token,
        f"{BASE_PATH}/accounts",
        "POST",
        {"name": "smoke-test", "base_url": "https://example.invalid", "login_type": "session", "session_token": "x"},
    )
    created = unwrap(body)
    check("POST /accounts 创建账号", status == 200 and isinstance(created, dict) and created.get("success"), f"HTTP {status} {str(body)[:200]}")
    if isinstance(created, dict) and created.get("id"):
        account_id = created["id"]
        status, listed = request(base, token, f"{BASE_PATH}/accounts")
        listed = unwrap(listed)
        check("新账号出现在列表中", isinstance(listed, list) and any(item.get("id") == account_id for item in listed))
        status, body = request(base, token, f"{BASE_PATH}/accounts/{account_id}", "PUT", {"name": "smoke-test-2"})
        updated = unwrap(body)
        check("PUT /accounts/{id} 更新账号", status == 200 and isinstance(updated, dict) and updated.get("success"), f"HTTP {status} {str(body)[:200]}")
        status, body = request(base, token, f"{BASE_PATH}/accounts/{account_id}/test", "POST")
        tested = unwrap(body)
        check("POST /accounts/{id}/test 有明确结果", status == 200 and isinstance(tested, dict), f"HTTP {status}")
        status, body = request(base, token, f"{BASE_PATH}/accounts/{account_id}/checkin", "POST")
        checked = unwrap(body)
        check("POST /accounts/{id}/checkin 失败但结构正确", status == 200 and isinstance(checked, dict), f"HTTP {status}")

    print("\n\033[1m== 5. 端到端签到（需提供真实站点）\033[0m")
    if args.account_url and args.account_token:
        status, body = request(
            base,
            token,
            f"{BASE_PATH}/accounts",
            "POST",
            {
                "name": "e2e",
                "base_url": args.account_url,
                "login_type": "session",
                "session_token": args.account_token,
            },
        )
        e2e = unwrap(body)
        e2e_id = e2e.get("id") if isinstance(e2e, dict) else None
        check("创建真实站点账号", bool(e2e_id), str(body)[:200])
        if e2e_id:
            status, body = request(base, token, f"{BASE_PATH}/accounts/{e2e_id}/test", "POST")
            tested = unwrap(body)
            check("真实站点连通性测试成功", status == 200 and bool(tested.get("success")), str(body)[:200])
            status, body = request(base, token, f"{BASE_PATH}/accounts/{e2e_id}/checkin", "POST")
            checked = unwrap(body)
            check("真实站点签到成功", status == 200 and bool(checked.get("success")), str(body)[:200])
            status, body = request(base, token, f"{BASE_PATH}/accounts/{e2e_id}", "DELETE")
            removed = unwrap(body)
            check("清理真实站点账号", status == 200 and bool(removed.get("success")), str(body)[:200])
    else:
        print("  (跳过) 未提供 --account-url / --account-token")

    if account_id:
        status, body = request(base, token, f"{BASE_PATH}/accounts/{account_id}", "DELETE")
        removed = unwrap(body)
        check("删除测试账号", status == 200 and bool(removed.get("success")), f"HTTP {status}")

    print("\n\033[1m== 6. 启停与重载无残留\033[0m")
    status, plugin = request(base, token, f"/api/v1/plugin/{PLUGIN_ID}")
    plugin = unwrap(plugin) or {}
    original = plugin.get("enabled") if isinstance(plugin, dict) else None
    for state in (False, True):
        status, body = request(base, token, f"/api/v1/plugin/{PLUGIN_ID}", "PUT", {"enabled": state})
        check(f"切换启用状态为 {state}", status == 200, f"HTTP {status} {str(body)[:150]}")
        time.sleep(2)
        status, plugin = request(base, token, f"/api/v1/plugin/{PLUGIN_ID}")
        plugin = unwrap(plugin) or {}
        check(f"启用状态回读为 {state}", isinstance(plugin, dict) and plugin.get("enabled") == state, str(plugin)[:150])
    status, body = request(base, token, f"/api/v1/plugin/reload/{PLUGIN_ID}", "POST")
    check("POST /plugin/reload/NewAPICheckin 成功", status == 200, f"HTTP {status}")
    time.sleep(3)
    status, body = request(base, token, f"{BASE_PATH}/accounts")
    check("重载后插件 API 仍可用且不重复", status == 200 and isinstance(unwrap(body), list), f"HTTP {status}")
    if original is not None:
        request(base, token, f"/api/v1/plugin/{PLUGIN_ID}", "PUT", {"enabled": original})

    print("\n\033[1m== 7. 定时服务与通知\033[0m")
    print("  提示: 定时服务数量需在容器内用 scripts/mp_smoke_test.sh 校验；")
    print("  通知需在 MoviePilot 通知渠道确认收到一条『New-API 签到结果』。")

    print(f"\n结果: \033[32m{len(PASSED)} PASS\033[0m / \033[31m{len(FAILED)} FAIL\033[0m")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
