"""
stability_guard.py — 自动化回归护栏

一键验证：语法 → 导入 → 函数存在性 → 接口契约 → CDP 连通性

用法:
    python scripts/stability_guard.py                  # 全量检查
    python scripts/stability_guard.py --offline        # 跳过 CDP 检查
    python scripts/stability_guard.py --contracts-only # 仅契约检查
    python scripts/stability_guard.py --level 1        # 仅指定层级 (1-5)

退出码: 0 全部通过, 1 有失败
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 配置 ──────────────────────────────────────────────────────────────────

SCRIPTS_DIR = SCRIPT_DIR
EXCLUDE_DIRS = {"labs", "tests", "__pycache__"}
EXCLUDE_FILES_PATTERN = re.compile(r"\.broken\.py$|^test_")

CORE_IMPORTS = [
    ("cdp_publish", "XiaohongshuPublisher"),
    ("publish_pipeline", "main"),
    ("chrome_launcher", "ensure_chrome"),
    ("douban_promo_copy", "main"),
    ("xhs_images_to_picset", "main"),
]

PROTECTED_FUNCTIONS: list[dict] = [
    # (模块, 类, 函数, 返回类型提示, 参数列表)
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "__init__",
     "return_hint": None,
     "params": ["host", "port", "timing_jitter", "account_name",
                "preserve_upload_paths", "context_key"]},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_send",
     "return_hint": "dict", "params": ["method"]},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_reconnect_cdp",
     "return_hint": "bool", "params": None},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_query_node_id",
     "return_hint": "int", "params": ["selector"]},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_click_image_text_tab",
     "return_hint": None, "params": None},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_upload_images",
     "return_hint": None, "params": ["image_paths"]},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "_click_mouse",
     "return_hint": None, "params": ["x", "y"]},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "click_add_product",
     "return_hint": "bool", "params": None},
    {"module": "cdp_publish", "class": "XiaohongshuPublisher", "func": "select_product_with_match",
     "return_hint": None, "params": ["product_name"]},
    # emit_product_select_evidence 是 publish_pipeline.main() 内的嵌套函数，
    # 通过源码文本匹配检测（非 import 可及）
    {"module": "publish_pipeline", "class": None, "func": "emit_product_select_evidence",
     "return_hint": None, "params": ["status"], "nested": True},
    {"module": "chrome_launcher", "class": None, "func": "ensure_chrome",
     "return_hint": "bool", "params": ["port"]},
]

# select_product_with_match 返回 dict 必须包含的 14 个字段
# （manual_review 是 emit_product_select_evidence 的 status 参数，不在返回 dict 中）
PRODUCT_SELECT_RETURN_FIELDS = [
    "ok", "mounted", "product_attached_on_publish_page",
    "rule", "matched", "target", "candidates",
    "checkbox_found", "checkbox_clicked", "selected_count_before",
    "selected_count_after", "save_button_found", "save_clicked",
    "modal_closed_after_save",
]

CDP_PORT = 9322  # runner 账号固定端口


# ── 工具函数 ──────────────────────────────────────────────────────────────

def _find_py_files() -> list[str]:
    """扫描 scripts/ 下所有 .py 文件（排除 labs/tests/.broken）。"""
    files = []
    for entry in os.listdir(SCRIPTS_DIR):
        path = os.path.join(SCRIPTS_DIR, entry)
        if not os.path.isfile(path):
            continue
        if not entry.endswith(".py"):
            continue
        if EXCLUDE_FILES_PATTERN.search(entry):
            continue
        # 排除子目录中的文件
        if os.path.dirname(path) != SCRIPTS_DIR:
            continue
        files.append(path)
    return sorted(files)


def _print_header(title: str) -> None:
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def _print_result(level: str, name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    padded = f"[{level}] {name:<40}"
    print(f"  {padded} {status}  {detail}")


def _print_summary(results: list[tuple[str, str, bool, str]]) -> tuple[int, int]:
    passed = sum(1 for _, _, ok, _ in results if ok)
    total = len(results)
    print()
    print("-" * 60)
    print(f"  通过: {passed}/{total}  ", end="")
    if passed == total:
        print("全部 PASS")
    else:
        print(f"失败: {total - passed}")
    print()
    return passed, total


# ── L1: 语法检查 ─────────────────────────────────────────────────────────

def check_syntax(files: list[str]) -> list[tuple[str, str, bool, str]]:
    results = []
    failed = 0
    for fpath in files:
        rel = os.path.relpath(fpath, SCRIPTS_DIR)
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", fpath],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0
        if not ok:
            failed += 1
        err = proc.stderr.strip() if proc.stderr else ""
        results.append(("L1", rel, ok, err))
    # 汇总为一条
    summary_ok = failed == 0
    return [("L1", f"语法检查 ({len(files)} 个文件)", summary_ok,
             f"{len(files)-failed}/{len(files)} 通过" if not summary_ok else "")]


# ── L2: 导入检查 ─────────────────────────────────────────────────────────

def check_imports() -> list[tuple[str, str, bool, str]]:
    results = []
    for mod_name, cls_name in CORE_IMPORTS:
        try:
            mod = importlib.import_module(mod_name)
            if cls_name and not hasattr(mod, cls_name):
                results.append(("L2", f"import {mod_name}.{cls_name}", False,
                                "class not found"))
                continue
            results.append(("L2", f"import {mod_name}.{cls_name}", True, ""))
        except Exception as e:
            results.append(("L2", f"import {mod_name}.{cls_name}", False,
                            str(e)))
    return results


# ── L3: 函数存在性 ───────────────────────────────────────────────────────

def _class_method_params(cls: type, func_name: str) -> list[str] | None:
    """获取类方法的参数名列表（不含 self）。"""
    try:
        fn = getattr(cls, func_name)
        sig = inspect.signature(fn)
        return [p.name for p in sig.parameters.values()
                if p.name != "self" and p.kind != p.VAR_KEYWORD
                and p.kind != p.VAR_POSITIONAL]
    except Exception:
        return None


def check_functions() -> list[tuple[str, str, bool, str]]:
    results = []
    for entry in PROTECTED_FUNCTIONS:
        mod_name = entry["module"]
        cls_name = entry["class"]
        func_name = entry["func"]
        expected_params = entry.get("params")
        return_hint = entry.get("return_hint")
        is_nested = entry.get("nested", False)

        # 嵌套函数（如 emit_product_select_evidence）通过源码文本搜索检测
        if is_nested:
            src_path = os.path.join(SCRIPTS_DIR, f"{mod_name}.py")
            if not os.path.isfile(src_path):
                results.append(("L3", f"{mod_name}.{func_name}", False,
                                f"{src_path} not found"))
                continue
            with open(src_path, encoding="utf-8") as f:
                source = f.read()
            pattern = rf"def\s+{re.escape(func_name)}\s*\("
            if re.search(pattern, source):
                # 检查参数名
                if expected_params:
                    # 获取 def 行并提取参数
                    for line in source.split("\n"):
                        m = re.match(rf"\s+def\s+{re.escape(func_name)}\s*\((.*)\)", line)
                        if m:
                            params_str = m.group(1)
                            found_all = all(p in params_str for p in expected_params)
                            detail = "" if found_all else f"缺参数: {expected_params}"
                            results.append(("L3", f"{mod_name}.{func_name}",
                                            found_all, detail))
                            break
                    else:
                        results.append(("L3", f"{mod_name}.{func_name}", True, ""))
                else:
                    results.append(("L3", f"{mod_name}.{func_name}", True, ""))
            else:
                results.append(("L3", f"{mod_name}.{func_name}", False,
                                "nested function definition not found in source"))
            continue

        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            results.append(("L3", f"{mod_name}.{func_name}", False,
                            f"import err: {e}"))
            continue

        if cls_name:
            cls = getattr(mod, cls_name, None)
            if cls is None:
                results.append(("L3", f"{cls_name}.{func_name}", False,
                                f"class {cls_name} not found"))
                continue
            fn = getattr(cls, func_name, None)
            if fn is None:
                results.append(("L3", f"{cls_name}.{func_name}", False,
                                f"method {func_name} not found"))
                continue
            actual_params = _class_method_params(cls, func_name)
        else:
            fn = getattr(mod, func_name, None)
            if fn is None:
                results.append(("L3", f"{mod_name}.{func_name}", False,
                                f"function not found"))
                continue
            try:
                sig = inspect.signature(fn)
                actual_params = [p.name for p in sig.parameters.values()
                                 if p.kind != p.VAR_KEYWORD
                                 and p.kind != p.VAR_POSITIONAL]
            except Exception:
                actual_params = None

        # 检查参数
        param_ok = True
        if expected_params and actual_params is not None:
            for p in expected_params:
                if p not in actual_params:
                    param_ok = False
                    detail = f"缺少参数: {p} (现有: {actual_params})"
                    results.append(("L3", f"{cls_name or mod_name}.{func_name}",
                                    False, detail))
                    break

        if param_ok:
            detail_parts = []
            if return_hint and actual_params is not None:
                try:
                    actual_fn = fn
                    if hasattr(fn, "__func__"):
                        actual_fn = fn.__func__
                    sig = inspect.signature(actual_fn)
                    ret = sig.return_annotation
                    if ret is inspect.Parameter.empty:
                        detail_parts.append(f"缺返回类型 (期望 {return_hint})")
                except Exception:
                    pass

            detail = "; ".join(detail_parts) if detail_parts else ""
            results.append(("L3", f"{cls_name or mod_name}.{func_name}", True,
                            detail))

    return results


# ── L4: 接口契约 ─────────────────────────────────────────────────────────

def check_return_fields() -> list[tuple[str, str, bool, str]]:
    """检查 select_product_with_match 的源码中是否包含所有 15 个返回字段。"""
    src_path = os.path.join(SCRIPTS_DIR, "cdp_publish.py")
    if not os.path.isfile(src_path):
        return [("L4", "cdp_publish.py 未找到", False, "")]

    # 找到 select_product_with_match 函数体，收集 dict key 字面量
    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    # 先用行号定位函数体
    func_start = None
    func_end = None
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\s+def select_product_with_match\(", line):
            func_start = i
            break

    if func_start is None:
        return [("L4", "select_product_with_match 未找到", False, "")]

    # 找到函数体结束（下一个 def 或文件尾）
    for i in range(func_start + 1, len(lines)):
        if re.match(r"^\s+def \w+", lines[i]):
            func_end = i
            break
    if func_end is None:
        func_end = len(lines)

    func_body = "\n".join(lines[func_start:func_end])

    # 收集 return 语句或 dict 构造中的 key 字面量
    found_keys = set()
    for m in re.finditer(r"""['"](\w+)['"]\s*:""", func_body):
        found_keys.add(m.group(1))
    for m in re.finditer(r"""\{\s*['"](\w+)['"]""", func_body):
        found_keys.add(m.group(1))

    missing = [f for f in PRODUCT_SELECT_RETURN_FIELDS if f not in found_keys]
    ok = len(missing) == 0
    detail = f"{len(PRODUCT_SELECT_RETURN_FIELDS) - len(missing)}/{len(PRODUCT_SELECT_RETURN_FIELDS)}"
    if not ok:
        detail += f" 缺: {', '.join(missing)}"
    return [("L4", "select_product_with_match 返回字段", ok, detail)]


def check_init_params() -> list[tuple[str, str, bool, str]]:
    """检查 XiaohongshuPublisher.__init__ 参数。"""
    # 直接从之前导入的模块检查
    import cdp_publish
    try:
        sig = inspect.signature(cdp_publish.XiaohongshuPublisher.__init__)
        actual = [p.name for p in sig.parameters.values()
                  if p.name != "self" and p.kind != p.VAR_KEYWORD]
    except Exception as e:
        return [("L4", "XiaohongshuPublisher.__init__ 参数", False, str(e))]

    expected = ["host", "port", "timing_jitter", "account_name",
                "preserve_upload_paths", "context_key"]
    missing = [p for p in expected if p not in actual]
    ok = len(missing) == 0
    detail = f"{len(actual)} 参数" if ok else f"缺: {', '.join(missing)}"
    return [("L4", "XiaohongshuPublisher.__init__ 参数", ok, detail)]


# ── L5: CDP 连通性 ──────────────────────────────────────────────────────

def check_cdp(port: int = CDP_PORT) -> list[tuple[str, str, bool, str]]:
    try:
        import urllib.request
        url = f"http://127.0.0.1:{port}/json/version"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read().decode())
        browser = data.get("Browser", "unknown")
        return [("L5", f"CDP 连通 (port {port})", True, browser)]
    except Exception as e:
        return [("L5", f"CDP 连通 (port {port})", False, str(e))]


# ── 主流程 ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="stability_guard — 自动化回归护栏",
    )
    parser.add_argument("--offline", action="store_true",
                        help="跳过 CDP 连通性检查 (L5)")
    parser.add_argument("--contracts-only", action="store_true",
                        help="仅执行契约检查 (L4)")
    parser.add_argument("--level", type=int, default=0,
                        help="仅执行指定层级 (1-5)")
    args = parser.parse_args()

    _print_header("stability_guard v1  —  自动化回归护栏")

    all_results: list[tuple[str, str, bool, str]] = []
    only_level = args.level

    py_files = _find_py_files()

    if args.contracts_only:
        only_level = 4

    # L1: 语法
    if not only_level or only_level == 1:
        all_results.extend(check_syntax(py_files))

    # L2: 导入
    if not only_level or only_level == 2:
        all_results.extend(check_imports())

    # L3: 函数存在性 (依赖 L2 成功)
    if not only_level or only_level == 3:
        all_results.extend(check_functions())

    # L4: 接口契约
    if not only_level or only_level == 4:
        all_results.extend(check_return_fields())
        all_results.extend(check_init_params())

    # L5: CDP 连通
    if not args.offline and (not only_level or only_level == 5):
        all_results.extend(check_cdp())

    # 输出
    for level, name, ok, detail in all_results:
        _print_result(level, name, ok, detail)

    passed, total = _print_summary(all_results)

    if not all_results:
        print("  没有检查项被执行。")
        return 1

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
