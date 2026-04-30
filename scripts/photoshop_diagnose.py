"""
Print how this repo resolves Photoshop.exe and whether COM script entry works.
Run from repo root: python scripts/photoshop_diagnose.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from env_local_loader import load_env_local  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
load_env_local(REPO_ROOT)

from xhs_image_autofix import (  # noqa: E402
    _photoshop_dispatch_jsx_via_vbscript_dofile,
    _photoshop_exe_common_install_paths,
    _photoshop_exe_from_registry_app_paths,
    _resolve_photoshop_exe_path,
)


def main() -> None:
    reg = _photoshop_exe_from_registry_app_paths()
    guessed = _photoshop_exe_common_install_paths()
    resolved = _resolve_photoshop_exe_path()
    env_exe = os.environ.get("REDBOOK_PHOTOSHOP_EXE", "").strip()
    payload = {
        "REDBOOK_PHOTOSHOP_EXE": env_exe or None,
        "registry_AppPaths": reg,
        "common_path_guess": guessed,
        "resolved_exe": resolved,
        "exe_exists": bool(resolved and os.path.isfile(resolved)),
    }

    if sys.platform == "win32":
        try:
            import pythoncom  # noqa: PLC0415
            import win32com.client  # noqa: PLC0415

            pythoncom.CoInitialize()
            try:
                ps = win32com.client.Dispatch("Photoshop.Application")
                payload["com_dispatch_ok"] = bool(ps)
                try:
                    payload["photoshop_version"] = str(ps.Version)
                except Exception as ver_exc:
                    payload["photoshop_version"] = None
                    payload["photoshop_version_error"] = repr(ver_exc)
                # Accessing DoJavaScript* via pywin32 getters can throw on Photoshop 26+;
                # real batch path uses VBScript + DoJavaScriptFile instead.
                payload["do_javascript_via_pywin32_note"] = (
                    "skipped_probe: pywin32 getattr(DoJavaScript*) may error on PS2025+; "
                    "repo uses cscript+VBScript bridge for DoJavaScriptFile"
                )
                jsx_smoke_path = Path(REPO_ROOT) / "tmp" / "ps_diagnose_ping.jsx"
                try:
                    jsx_smoke_path.parent.mkdir(parents=True, exist_ok=True)
                    jsx_smoke_path.write_text(
                        "#target photoshop\r\nvar __redbookskillsPing = 1;\r\n",
                        encoding="utf-8",
                        newline="\r\n",
                    )
                except Exception as write_ping_exc:
                    payload["jsx_vbscript_smoke_error"] = repr(write_ping_exc)
                else:
                    payload["jsx_vbscript_dofile_rc0"] = bool(
                        _photoshop_dispatch_jsx_via_vbscript_dofile(
                            jsx_smoke_path,
                            dialog_mode=3,
                            visible=False,
                            timeout_seconds=120,
                        )
                    )
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception as coinit_exc:
                    payload["coinit_cleanup_note"] = repr(coinit_exc)
        except Exception as exc:  # noqa: BLE001
            payload["com_error"] = repr(exc)

    payload["hint"] = (
        "路径优先：REDBOOK_PHOTOSHOP_EXE → 注册表 App Paths → WPSDrive 下 Adobe Photoshop* → "
        ".lnk（COM/cscript；cscript 无弹窗）。处理顺序：COM JSX，不行再 Photoshop.exe -r。"
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
