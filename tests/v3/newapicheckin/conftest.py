"""
测试环境准备。

插件源码位于 plugins.v3/newapicheckin，测试以包路径导入，
避免宿主 SDK 依赖影响纯逻辑测试。
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLUGINS_DIR = ROOT / "plugins.v3"
PACKAGE_DIR = PLUGINS_DIR / "newapicheckin"

if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

if "newapicheckin" not in sys.modules:
    spec = importlib.machinery.ModuleSpec("newapicheckin", None, is_package=True)
    spec.submodule_search_locations = [str(PACKAGE_DIR)]
    package = importlib.util.module_from_spec(spec)
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["newapicheckin"] = package
