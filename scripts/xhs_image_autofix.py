"""
Local heuristic processing for Xiaohongshu-downloaded references (no third-party APIs).

  1) OpenCV inpaint on a bottom-right rectangular mask (typical watermark area)
  2) Pillow: autocontrast + mild sharpness/color
  3) Optional Photoshop COM: Auto Tone / Auto Contrast / Auto Color equivalents via JSX
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from pipeline_debug import pipeline_debug_log

_REPO_ROOT = _scripts_dir.parent
_ENV_LOCAL_LOADED_FOR_PS = False


def _ensure_env_local_for_optional_vars() -> None:
    """So REDBOOK_PHOTOSHOP_EXE in .env.local / .env.portable applies even when callers forget to load."""
    global _ENV_LOCAL_LOADED_FOR_PS  # noqa: PLW0603
    if _ENV_LOCAL_LOADED_FOR_PS:
        return
    _ENV_LOCAL_LOADED_FOR_PS = True
    try:
        from env_local_loader import load_env_local  # noqa: PLC0415

        load_env_local(_REPO_ROOT)
    except Exception:
        pass


def _photoshop_exe_from_registry_app_paths() -> str | None:
    """
    Windows registers the default Photoshop.exe under App Paths (most stable).
    Prefer this when .lnk targets a cloud/sync path that os.path.isfile may miss offline.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return None
    roots = []
    try:
        roots.append(winreg.HKEY_LOCAL_MACHINE)
    except Exception:
        pass
    try:
        roots.append(winreg.HKEY_CURRENT_USER)
    except Exception:
        pass

    suffixes = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
    ]

    candidates: list[str] = []

    def _read_default(key_path: str) -> None:
        for hroot in roots:
            try:
                with winreg.OpenKey(hroot, key_path, 0, winreg.KEY_READ) as k:
                    exe, _ = winreg.QueryValueEx(k, "")
                    s = str(exe).strip().strip('"')
                    if s:
                        candidates.append(s)
            except OSError:
                continue

    for suf in suffixes:
        _read_default(suf)

    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _photoshop_exe_wpsdrive_candidates() -> str | None:
    """
    Typical cloud layout:
      %USERPROFILE%\\WPSDrive\\…\\Adobe Photoshop 2025\\Photoshop.exe
    Narrow glob avoids long rglob on huge synced trees.
    """
    base = Path.home() / "WPSDrive"
    if not base.is_dir():
        return None
    try:
        for year in ("2025", "2024", "2023", "2022"):
            pat = f"**/Adobe Photoshop {year}/Photoshop.exe"
            for cand in sorted(base.glob(pat)):
                if cand.is_file():
                    print(
                        "[xhs_image_autofix] 在 WPSDrive 找到 Photoshop.exe: "
                        + str(cand.resolve()),
                        file=sys.stderr,
                        flush=True,
                    )
                    return str(cand.resolve())
    except OSError:
        return None
    # Last resort on WPSDrive: capped walk for non-standard folders.
    try:
        scanned = 0
        for cand in base.rglob("Photoshop.exe"):
            scanned += 1
            if scanned > 6000:
                break
            if not cand.is_file():
                continue
            if cand.parent.name.startswith("Adobe Photoshop"):
                print(
                    "[xhs_image_autofix] 在 WPSDrive 回溯找到 Photoshop.exe: "
                    + str(cand.resolve()),
                    file=sys.stderr,
                    flush=True,
                )
                return str(cand.resolve())
    except OSError:
        return None
    return None


def _photoshop_exe_common_install_paths() -> str | None:
    """Last-resort guesses when registry and .lnk are empty or on unavailable cloud paths."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    roots = [program_files, program_files_x86, str(Path.home())]
    years = ("2025", "2024", "2023", "2022")
    for root in roots:
        for y in years:
            cand = os.path.join(root, "Adobe", f"Adobe Photoshop {y}", "Photoshop.exe")
            if os.path.isfile(cand):
                return cand
            cand2 = os.path.join(root, "Adobe Photoshop " + y, "Photoshop.exe")
            if os.path.isfile(cand2):
                return cand2
    return None


def _photoshop_exe_from_running_process_wmic() -> str | None:
    """
    When Photoshop is already running, WMIC often returns the real on-disk exe
    (e.g. WPS Cloud Files cachebackup\\...\\Photoshop.exe) while Start Menu .lnk
    may point at a Unicode path that fails os.path.isfile from automation.
    """
    if sys.platform != "win32":
        return None
    try:
        cp = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='Photoshop.exe'",
                "get",
                "ExecutablePath",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0 or not (cp.stdout or "").strip():
        return None
    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("ExecutablePath"):
            continue
        if ln.lower().endswith("photoshop.exe") and os.path.isfile(ln):
            print(
                "[xhs_image_autofix] 从已运行进程解析到 Photoshop.exe: " + ln,
                file=sys.stderr,
                flush=True,
            )
            return ln
    return None


def _photoshop_exe_wps_cloud_files_tree() -> str | None:
    """WPS 同步盘：在 %USERPROFILE%\\WPS Cloud Files 下浅层搜索 Adobe Photoshop*\\Photoshop.exe。"""
    base = Path.home() / "WPS Cloud Files"
    if not base.is_dir():
        return None
    scanned = 0
    try:
        for cand in base.rglob("Photoshop.exe"):
            scanned += 1
            if scanned > 12000:
                break
            try:
                parent = cand.parent.name
            except OSError:
                continue
            if parent.startswith("Adobe Photoshop") and cand.is_file():
                p = str(cand.resolve())
                print(
                    "[xhs_image_autofix] 在 WPS Cloud Files 找到 Photoshop.exe: " + p,
                    file=sys.stderr,
                    flush=True,
                )
                return p
    except OSError:
        return None
    return None


def _photoshop_startmenu_shortcut_default() -> str:
    """Start MenuPrograms .lnk; override with REDBOOK_PHOTOSHOP_STARTMENU_LNK."""
    env = os.environ.get("REDBOOK_PHOTOSHOP_STARTMENU_LNK", "").strip()
    if env:
        return env
    return (
        "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\"
        "Adobe Photoshop 2025.lnk"
    )


def _resolve_photoshop_exe_path() -> str | None:
    """
    Resolve Photoshop executable path for non-COM fallback.
    Priority:
      1) REDBOOK_PHOTOSHOP_EXE
      2) Registry: App Paths\\Photoshop.exe
      3) WPSDrive subtree: Adobe Photoshop yyyy\\Photoshop.exe
      4) COM: WScript.Shell shortcut target from start-menu .lnk
      5) cscript + .vbs (no popup; wscript/Echo dialogs are avoided)
      6) Common Program Files Adobe paths
    """
    _ensure_env_local_for_optional_vars()
    env_exe = os.environ.get("REDBOOK_PHOTOSHOP_EXE", "").strip()
    if env_exe and os.path.isfile(env_exe):
        return env_exe

    reg_exe = _photoshop_exe_from_registry_app_paths()
    if reg_exe:
        return reg_exe

    wps_exe = _photoshop_exe_wpsdrive_candidates()
    if wps_exe:
        return wps_exe

    lnk = _photoshop_startmenu_shortcut_default()
    if not os.path.isfile(lnk):
        return None
    try:
        import win32com.client  # noqa: PLC0415

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(lnk)
        target = str(getattr(shortcut, "Targetpath", "") or "").strip()
        if target and os.path.isfile(target):
            return target
    except Exception:
        pass
    # Fallback: resolve .lnk with cscript (console host). Using wscript + Echo shows a modal
    # "Windows Script Host" dialog — never use that here.
    helper = None  # populated when helper file is written
    try:
        # UTF-8 BOM breaks Windows Script Host parsing; use UTF-8 without BOM.
        helper = tempfile.NamedTemporaryFile(
            suffix=".vbs",
            delete=False,
            mode="w",
            encoding="utf-8",
        )
        # VBScript string: double quotes inside path must be doubled.
        lnk_for_vbs = str(lnk).replace('"', '""')
        vbs = (
            'Set sh = CreateObject("WScript.Shell")\n'
            f'Set sc = sh.CreateShortcut("{lnk_for_vbs}")\n'
            "WScript.Echo sc.TargetPath\n"
        )
        helper.write(vbs)
        helper.flush()
        helper.close()
        win_dir = os.environ.get("SystemRoot", "C:\\Windows")
        cscript = os.path.join(win_dir, "System32", "cscript.exe")
        if not os.path.isfile(cscript):
            cscript = os.path.join(win_dir, "SysWOW64", "cscript.exe")

        cp = subprocess.run(
            [cscript, "//nologo", helper.name],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if cp.returncode != 0:
            print(
                "[xhs_image_autofix] Photoshop 快捷方式解析失败 (cscript):"
                f" rc={cp.returncode} stderr={cp.stderr.strip()!r}",
                file=sys.stderr,
            )
        raw = (
            ((cp.stdout or "").strip())
            + "\n"
            + ((cp.stderr or "").strip())
        ).strip()
        if os.environ.get("REDBOOK_PS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
            print(
                f"[xhs_image_autofix] Photoshop 快捷方式解析(cscript): "
                f"rc={cp.returncode} raw={raw!r}",
                file=sys.stderr,
                flush=True,
            )
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if lines:
            t0 = lines[-1].strip()
            if t0.startswith('"') and t0.endswith('"') and len(t0) >= 2:
                t0 = t0[1:-1]
            # Some hosts echo full paths wrapped in stray quotes/unprintables.
            t0 = t0.strip()
            if t0 and os.path.isfile(t0):
                return t0
    except Exception:
        pass
    finally:
        try:
            if helper is not None and os.path.isfile(helper.name):
                os.remove(helper.name)
        except Exception:
            pass

    proc_exe = _photoshop_exe_from_running_process_wmic()
    if proc_exe:
        return proc_exe
    cloud_exe = _photoshop_exe_wps_cloud_files_tree()
    if cloud_exe:
        return cloud_exe

    guess = _photoshop_exe_common_install_paths()
    if guess:
        print(
            "[xhs_image_autofix] 使用常见安装路径推断 Photoshop.exe: " + guess,
            file=sys.stderr,
            flush=True,
        )
        return guess
    return None


def _photoshop_dispatch_jsx_via_vbscript_dofile(
    jsx_path: str | Path,
    *,
    dialog_mode: int = 3,
    visible: bool = False,
    timeout_seconds: int = 1800,
) -> bool:
    """
    Photoshop 2025+ / pywin32: invoking DoJavaScript/DoJavaScriptFile from Python
    often raises DISP_E_EXCEPTION ("missing required value"); Adobe's VBScript sample
    using DoJavaScriptFile(path, Array(), PSDialogModes) still works reliably via cscript.
    """
    if sys.platform != "win32":
        return False
    jsx_abs = str(Path(jsx_path).resolve())
    if not os.path.isfile(jsx_abs):
        return False
    jsx_esc = jsx_abs.replace('"', '""')
    vis_snip = ""
    if visible:
        vis_snip = "appRef.Visible = True\r\n"
    dm = max(0, min(15, int(dialog_mode)))
    vbs_body = (
        "Option Explicit\r\n"
        "Dim appRef, jsxPath\r\n"
        f'jsxPath = "{jsx_esc}"\r\n'
        'Set appRef = CreateObject("Photoshop.Application")\r\n'
        + vis_snip
        + f'call appRef.DoJavaScriptFile(jsxPath, Array(), {dm})\r\n'
        "Set appRef = Nothing\r\n"
    )
    vbf: str | None = None
    try:
        vf = tempfile.NamedTemporaryFile(suffix=".vbs", delete=False, mode="w", encoding="utf-8")
        vf.write(vbs_body)
        vf.close()
        vbf = vf.name
        win_dir = os.environ.get("SystemRoot", "C:\\Windows")
        cscript = os.path.join(win_dir, "System32", "cscript.exe")
        if not os.path.isfile(cscript):
            cscript = os.path.join(win_dir, "SysWOW64", "cscript.exe")
        if not os.path.isfile(cscript):
            return False
        cp = subprocess.run(
            [cscript, "//nologo", vbf],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(timeout_seconds)),
        )
        tail = ""
        _o = cp.stdout.strip() + "\n" + cp.stderr.strip()
        if _o.strip():
            tail = (_o.strip()[-800:])
        ok = cp.returncode == 0
        if not ok and tail:
            print(
                f"[xhs_image_autofix] VBScript→DoJavaScriptFile 退出码={cp.returncode} 输出尾部:\n{tail}",
                file=sys.stderr,
                flush=True,
            )
        return ok
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if vbf and os.path.isfile(vbf):
            try:
                os.remove(vbf)
            except OSError:
                pass


def _run_photoshop_jsx_via_exe(jsx: str, *, timeout_seconds: int = 600) -> bool:
    """
    Fallback runner: execute JSX through Photoshop.exe -r <script.jsx>.
    Returns True when process returns 0.
    """
    if sys.platform != "win32":
        return False
    exe = _resolve_photoshop_exe_path()
    if not exe:
        print(
            "[xhs_image_autofix] 未找到 Photoshop 可执行文件（可设置 REDBOOK_PHOTOSHOP_EXE）。",
            file=sys.stderr,
        )
        return False

    tf_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsx",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(jsx)
            tf_path = tf.name
        cmd = [exe, "-r", tf_path]
        cp = subprocess.run(
            cmd,
            check=False,
            timeout=max(120, int(timeout_seconds)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Photoshop.exe -r 的退出码不一定可靠；成功与否由上层对比输出目录文件数判定。
        if cp.returncode != 0:
            print(
                f"[xhs_image_autofix] Photoshop -r 进程退出码 {cp.returncode} "
                "(若输出目录已齐全仍视为成功)。",
                file=sys.stderr,
            )
        return True
    except Exception as exc:
        print(
            f"[xhs_image_autofix] Photoshop -r JSX 执行失败: {exc}",
            file=sys.stderr,
        )
        return False
    finally:
        if tf_path and os.path.isfile(tf_path):
            try:
                os.remove(tf_path)
            except OSError:
                pass


def mirror_photoshop_outputs_if_requested(folder_out: Path) -> None:
    """
    If REDBOOK_PHOTOSHOP_MIRROR_FINAL_TO is set, copy every file under folder_out there
    (e.g. a fixed folder you open from Explorer). Paths may use %USERNAME% etc.
    """
    raw = os.environ.get("REDBOOK_PHOTOSHOP_MIRROR_FINAL_TO", "").strip()
    if not raw:
        return
    folder_out = folder_out.resolve()
    dest = Path(os.path.expandvars(raw)).expanduser().resolve()
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"[xhs_image_autofix] 无法创建镜像目录 {dest}: {exc}",
            file=sys.stderr,
        )
        return
    n = 0
    for fp in sorted(folder_out.iterdir(), key=lambda p: p.name.lower()):
        if not fp.is_file():
            continue
        try:
            shutil.copy2(fp, dest / fp.name)
            n += 1
        except OSError as exc:
            print(
                f"[xhs_image_autofix] 复制到镜像目录失败 {fp.name}: {exc}",
                file=sys.stderr,
            )
    if n > 0:
        print(
            f"[xhs_image_autofix] 已将 {n} 个成品复制到镜像目录：{dest}",
            file=sys.stderr,
            flush=True,
        )


def open_photoshop_start_menu_shortcut_after_task() -> None:
    """
    After JSX/COM Photoshop batch succeeds: open the Windows Start Menu shortcut
    so Photoshop is easy to reach for manual follow-up. No-op on non-Windows.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("REDBOOK_PHOTOSHOP_NO_OPEN_AFTER_TASK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    path = _photoshop_startmenu_shortcut_default()
    if not os.path.isfile(path):
        print(
            f"[xhs_image_autofix] 未找到开始菜单 Photoshop 快捷方式（跳过）：{path}",
            file=sys.stderr,
        )
        return
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        print(
            f"[xhs_image_autofix] Photoshop 批处理完成，已打开快捷方式：{path}",
            file=sys.stderr,
            flush=True,
        )
    except OSError as exc:
        print(
            f"[xhs_image_autofix] 无法打开 Photoshop 快捷方式：{exc}",
            file=sys.stderr,
        )


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
        import numpy  # noqa: F401 PLC0415
    except ImportError as e:
        raise SystemExit(
            "Full-auto watermark step needs opencv-python-headless and numpy. "
            'Install: pip install "opencv-python-headless>=4.8" "numpy>=1.24"'
        ) from e
    import cv2  # noqa
    import numpy as np  # noqa
    return cv2, np


def _require_pil():
    try:
        from PIL import Image, ImageEnhance, ImageOps  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit(
            "Full-auto adjust step needs Pillow. Install: pip install Pillow>=10"
        ) from e
    return Image, ImageEnhance, ImageOps


def inpaint_bottom_right_corner(
    path_in: Path,
    path_out: Path,
    *,
    width_ratio: float = 0.36,
    height_ratio: float = 0.14,
    inpaint_radius: int = 4,
) -> None:
    """
    Paint a rectangular mask covering bottom-right `(width_ratio*W)` x `(height_ratio*H)`,
    run cv2 inpaint (TELEA). Good enough for logos in corner; content in that quadrant may soften.
    """
    cv2, np = _require_cv2()
    img = cv2.imread(str(path_in), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode image: {path_in}")

    h, w = img.shape[:2]
    rw = max(8, int(w * float(width_ratio)))
    rh = max(8, int(h * float(height_ratio)))
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[h - rh : h, w - rw : w] = 255

    healed = cv2.inpaint(img, mask, inpaint_radius, cv2.INPAINT_TELEA)

    path_out.parent.mkdir(parents=True, exist_ok=True)
    ext = path_in.suffix.lower()
    ok = cv2.imwrite(str(path_out), healed)
    if not ok:
        Image, _, _ = _require_pil()
        rgb = cv2.cvtColor(healed, cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(path_out)
    del ext


def pillow_post_adjust(path_in: Path, path_out: Path) -> None:
    Image, ImageEnhance, ImageOps = _require_pil()
    im = Image.open(path_in)
    im = ImageOps.exif_transpose(im)
    rgb = im.convert("RGB")

    adjusted = ImageOps.autocontrast(rgb, cutoff=0.5)
    adjusted = ImageEnhance.Color(adjusted).enhance(1.05)
    adjusted = ImageEnhance.Sharpness(adjusted).enhance(1.08)

    path_out.parent.mkdir(parents=True, exist_ok=True)
    ext = path_in.suffix.lower()
    save_kw: dict[str, Any] = {}
    if ext in (".jpg", ".jpeg"):
        save_kw["quality"] = 92
        save_kw["optimize"] = True
        # Make output more compatible with drawing apps (e.g. MS Paint).
        # Some apps are sensitive to progressive JPEG or unusual chroma subsampling.
        save_kw["progressive"] = False
        save_kw["subsampling"] = 0
    adjusted.save(path_out, **save_kw)


def run_photoshop_batch_auto_tcc(folder_in: Path, folder_out: Path) -> bool:
    """
    Windows + Photoshop installed: for each image in folder_in, open in Photoshop
    and apply the same commands as Image → Auto Tone, Auto Contrast, Auto Color
    (English string IDs: autoTone / autoContrast / autoColor via executeAction).

    Saves results under folder_out.
    Returns True when output file count matches input (normally via VBScript→DoJavaScriptFile).
    """
    _ensure_env_local_for_optional_vars()
    folder_in = folder_in.resolve()
    folder_out = folder_out.resolve()
    folder_out.mkdir(parents=True, exist_ok=True)
    ain = folder_in.as_posix()
    aout = folder_out.as_posix()

    _jsx_tpl = r"""#target photoshop
(function () {
    var DialogModes = { NO: 3 };
    function stringID(id) {
        try { return app.stringIDToTypeID(id); } catch (e) { return 0; }
    }
    var inFolder = new Folder("__AIN__");
    var outFolder = new Folder("__AOUT__");
    if (!inFolder.exists) {
        return;
    }
    var list = inFolder.getFiles(/\.(jpg|jpeg|png|webp)$/i);
    var saveOptsJPEG = new JPEGSaveOptions();
    saveOptsJPEG.quality = 12;
    saveOptsJPEG.embedColorProfile = true;
    var saveOptsPNG = new PNGSaveOptions();
    try {
      saveOptsPNG.compression = 6;
    } catch (e_png) {}

    function saveDoc(doc, f) {
      var nm = decodeURI(f.name);
      var ext = nm.substring(nm.lastIndexOf(".")).toLowerCase();
      var outFile = new File(outFolder.fsName + "/" + nm);
      if (ext === ".jpg" || ext === ".jpeg") {
        doc.saveAs(outFile, saveOptsJPEG, true);
      } else if (ext === ".png") {
        doc.saveAs(outFile, saveOptsPNG, true);
      } else {
        var opts = new JPEGSaveOptions();
        opts.quality = 12;
        outFile = new File(outFolder.fsName + "/" + nm.replace(/\.[^.]+$/, ".jpg"));
        doc.saveAs(outFile, opts, true);
      }
    }

    for (var i = 0; i < list.length; i++) {
        var doc = app.open(list[i]);
        try {
          var ids = ["autoTone", "autoContrast", "autoColor"];
          for (var k = 0; k < ids.length; k++) {
             var sid = stringID(ids[k]);
             if (sid) {
               try {
                 executeAction(sid, undefined, DialogModes.NO);
               } catch (e2) {}
             }
          }
          saveDoc(doc, list[i]);
        } finally {
          doc.close(SaveOptions.DONOTSAVECHANGES);
        }
    }
})();
"""

    jsx = _jsx_tpl.replace("__AIN__", ain).replace("__AOUT__", aout)

    visible = os.environ.get("REDBOOK_PHOTOSHOP_VISIBLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    dialog_off = 3  # Photoshop PSDialogModes.psDisplayNoDialogs
    print(
        "[xhs_image_autofix] Photoshop 批处理开始：会先通过 **VBScript→DoJavaScriptFile** "
        "(兼容 PS2025+；绕过 Python COM 封装缺陷)，按顺序执行：**图像 → 自动色调 → 自动对比度 → 自动颜色**。\n"
        f"  输入目录: {folder_in}\n"
        f"  输出目录: {folder_out}\n"
        f"  窗口可见: {visible}（设 REDBOOK_PHOTOSHOP_VISIBLE=1 便于观察）。",
        file=sys.stderr,
        flush=True,
    )

    tf_batch: str | None = None
    last_err: Exception | None = None
    try:
        tf = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsx",
            delete=False,
            encoding="utf-8",
        )
        tf.write(jsx)
        tf.flush()
        tf.close()
        tf_batch = tf.name
    except OSError as excw:
        last_err = excw
        tf_batch = None

    if not tf_batch:
        print(
            f"[xhs_image_autofix] 无法写入临时 JSX：{last_err}",
            file=sys.stderr,
            flush=True,
        )
        return False

    jsx_ok_dispatch = False
    try:
        if sys.platform == "win32":
            jsx_ok_dispatch = _photoshop_dispatch_jsx_via_vbscript_dofile(
                tf_batch,
                dialog_mode=dialog_off,
                visible=visible,
                timeout_seconds=1800,
            )

        legacy_pywin = os.environ.get("REDBOOK_PHOTOSHOP_TRY_PYWIN_COM", "").strip().lower()
        legacy_pywin = legacy_pywin in ("1", "true", "yes", "on")
        if legacy_pywin and sys.platform == "win32" and not jsx_ok_dispatch:
            try:
                import pythoncom  # noqa: PLC0415
                import win32com.client  # noqa: PLC0415

                pythoncom.CoInitialize()
                try:
                    ps = win32com.client.Dispatch("Photoshop.Application")
                    try:
                        ps.Visible = bool(visible)
                    except Exception:
                        pass
                    ps.DoJavaScriptFile(tf_batch, (), dialog_off)
                    jsx_ok_dispatch = True
                finally:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
            except Exception as exc_legacy:
                last_err = exc_legacy

        nin = sum(1 for _ in folder_in.iterdir() if _.is_file())
        nout = sum(1 for _ in folder_out.iterdir() if _.is_file())
        jsx_ok = jsx_ok_dispatch and nin > 0 and nin == nout
        pipeline_debug_log(
            "H2",
            "xhs_image_autofix.py:run_photoshop_batch_auto_tcc",
            "jsx vbscript dispatch and counts",
            {
                "jsx_ok_dispatch": jsx_ok_dispatch,
                "nin": nin,
                "nout": nout,
                "count_match": nin > 0 and nin == nout,
            },
        )

        if not jsx_ok:
            _run_photoshop_jsx_via_exe(jsx)
            nout_after = sum(1 for _ in folder_out.iterdir() if _.is_file())
            jsx_ok = bool(nin > 0 and nin == nout_after)
        if not jsx_ok:
            if last_err is not None:
                print(
                    f"[xhs_image_autofix] Photoshop JSX 仍未成功（可设 REDBOOK_PHOTOSHOP_TRY_PYWIN_COM=1 试用旧 COM）：{last_err}",
                    file=sys.stderr,
                    flush=True,
                )
            return False
        mirror_photoshop_outputs_if_requested(folder_out)
        open_photoshop_start_menu_shortcut_after_task()
        return True
    finally:
        if tf_batch and os.path.isfile(tf_batch):
            try:
                os.remove(tf_batch)
            except OSError:
                pass


def summarize_paths(log_path: Path, payload: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
