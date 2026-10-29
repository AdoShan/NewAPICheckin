#!/usr/bin/env bash
# MoviePilot 容器内/宿主侧自检脚本（NewAPICheckin）
#
# 用法：
#   bash scripts/mp_smoke_test.sh <moviepilot容器名>
#   bash scripts/mp_smoke_test.sh mp           # 容器名含 mp 关键字时可用别名
#
# 该脚本只做只读检查，不修改 MoviePilot 配置，不触发签到。

set -uo pipefail

CONTAINER="${1:-}"
PASS=0
FAIL=0

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

if [ -z "$CONTAINER" ]; then
  echo "用法: bash scripts/mp_smoke_test.sh <moviepilot容器名>"
  echo "可用容器:"
  docker ps --format '{{.Names}}\t{{.Image}}' | sed 's/^/  /'
  exit 2
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "找不到容器: $CONTAINER"
  exit 2
fi

dexec() { docker exec "$CONTAINER" "$@"; }

# ---------------------------------------------------------------- 环境
info "1. 容器与运行环境"
image=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")
echo "  镜像: $image"
if dexec python -V >/dev/null 2>&1; then
  ok "容器内 Python: $(dexec python -V 2>&1)"
else
  bad "容器内没有可用的 python"
fi

if dexec test -d /config; then
  ok "存在 /config 挂载目录（插件与数据根）"
else
  bad "未找到 /config，请确认 compose 中已挂载配置卷"
fi

for cmd in curl wget; do
  if dexec sh -c "command -v $cmd" >/dev/null 2>&1; then
    ok "容器内可用命令: $cmd"
  fi
done

# ---------------------------------------------------------------- 插件代码
info "2. 插件代码与索引"
if dexec test -d /config/plugins.v3/newapicheckin || dexec sh -c "ls -d /config/plugins*/newapicheckin 2>/dev/null | head -1" | grep -q newapicheckin; then
  ok "插件目录已存在于容器内"
else
  bad "未找到插件目录（newapicheckin），插件尚未安装或安装路径不同"
fi

plugin_path=$(dexec sh -c "ls -d /config/plugins*/newapicheckin 2>/dev/null | head -1")
if [ -n "$plugin_path" ]; then
  if dexec test -f "$plugin_path/__init__.py"; then
    ok "主类文件存在: $plugin_path/__init__.py"
  else
    bad "缺少 __init__.py"
  fi
  if dexec test -f "$plugin_path/newapicheckin.png"; then
    ok "图标文件存在"
  else
    bad "缺少图标 newapicheckin.png"
  fi
fi

# ---------------------------------------------------------------- 宿主导入
info "3. 宿主 SDK 导入（最关键的一步）"
if dexec python - <<'PY' 2>/dev/null
import importlib
mod = importlib.import_module("app.plugins.newapicheckin")
cls = mod.NewAPICheckin
print("VERSION", cls.plugin_version)
PY
then
  ok "app.plugins.newapicheckin 导入成功，宿主 SDK 依赖齐全"
else
  bad "app.plugins.newapicheckin 导入失败：检查宿主的 app.sdk.browser / app.sdk.events 导入路径"
fi

# ---------------------------------------------------------------- 依赖
info "4. 运行时依赖"
if dexec python -c "import cryptography; print(cryptography.__version__)" >/dev/null 2>&1; then
  ok "cryptography 可用（AES-256-GCM 凭据加密）"
else
  bad "缺少 cryptography：凭据将退化为混淆模式，不建议上线"
fi
if dexec python -c "import playwright; print(playwright.__file__)" >/dev/null 2>&1; then
  ok "playwright 包可用"
else
  bad "缺少 playwright：Cloudflare 浏览器兜底不可用，仅保留直接 API 路径"
fi
browsers=$(dexec sh -c "ls -d /ms-playwright* /root/.cache/ms-playwright 2>/dev/null | head -1")
if [ -n "$browsers" ]; then
  ok "Playwright 浏览器运行时: $browsers"
else
  bad "未找到 Playwright 浏览器运行时目录"
fi

# ---------------------------------------------------------------- 数据目录
info "5. 插件数据目录"
data_dir=$(dexec sh -c "ls -d /config/plugins/NewAPICheckin 2>/dev/null | head -1")
if [ -n "$data_dir" ]; then
  ok "数据目录存在: $data_dir"
  if dexec test -f "$data_dir/newapicheckin.db"; then
    ok "SQLite 数据库已创建"
  else
    echo "  (提示) 数据库尚未创建，插件加载并访问账号后生成"
  fi
  if dexec test -f "$data_dir/secret.key"; then
    perm=$(dexec stat -c '%a' "$data_dir/secret.key" 2>/dev/null)
    if [ "$perm" = "600" ]; then
      ok "密钥文件权限 600"
    else
      bad "密钥文件权限为 $perm，应为 600"
    fi
  else
    echo "  (提示) 密钥文件尚未创建，插件初始化后生成"
  fi
else
  echo "  (提示) 数据目录尚未创建，插件初始化后生成"
fi

# ---------------------------------------------------------------- 调度
info "6. 定时服务注册"
jobs=$(dexec python - <<'PY' 2>/dev/null
from app.sdk import scheduler as s
try:
    jobs = s.list_scheduler_jobs() or []
except Exception as e:
    print("ERROR", e)
    raise SystemExit(0)
count = 0
for job in jobs:
    ident = str(job.get("id") if isinstance(job, dict) else getattr(job, "id", ""))
    if ident.startswith("NewAPICheckin"):
        count += 1
print("COUNT", count)
PY
)
count=$(echo "$jobs" | awk '/^COUNT/{print $2}')
if [ "$count" = "1" ]; then
  ok "NewAPICheckin.AutoCheckin 定时服务恰好注册 1 次"
elif [ "$count" = "0" ]; then
  echo "  (提示) 当前未注册定时服务：插件未启用或 cron 配置为空"
else
  bad "定时服务注册了 $count 次，存在重复注册"
fi

# ---------------------------------------------------------------- 日志
info "7. 插件日志"
dexec sh -c "grep -i 'NewAPICheckin' /config/logs/*.log 2>/dev/null | tail -20" || echo "  (提示) 日志目录中暂无插件日志"

echo
printf '结果: \033[32m%d PASS\033[0m / \033[31m%d FAIL\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
