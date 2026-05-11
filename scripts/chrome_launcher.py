"""
Chrome launcher with CDP remote debugging support.

Manages a dedicated Chrome instance for Xiaohongshu publishing:
- Detects if Chrome is already listening on the debug port
- Launches Chrome with a dedicated user-data-dir for login persistence
- Waits for the debug port to become available
- Supports headless mode for automated publishing without GUI
- Supports switching between headless and headed mode (e.g. for login)
- Supports multiple accounts with separate profile directories
"""

import os
import sys
import time
import socket
import subprocess
import tempfile
import json
from typing import Optional

CDP_PORT = 9222
PROFILE_DIR_NAME = "XiaohongshuProfile"
STARTUP_TIMEOUT = 15  # seconds to wait for Chrome to start

# Track the Chrome process we launched so we can kill it later
_chrome_process: subprocess.Popen | None = None
# Track the current account being used
_current_account: Optional[str] = None


def _runner_config_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "runner.json"))


def _read_runner_config() -> dict:
    try:
        with open(_runner_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_default_account_and_port(
    account: Optional[str] = None,
    port: Optional[int] = None,
) -> tuple[Optional[str], int]:
    """Resolve the fixed runner account/port for local customer machines."""
    runner = _read_runner_config()
    resolved_account = account
    if resolved_account is None:
        configured_account = str(runner.get("runner_account") or "").strip()
        if configured_account:
            resolved_account = configured_account

    if port is not None:
        return resolved_account, int(port)

    configured_port = runner.get("runner_port")
    try:
        if configured_port:
            return resolved_account, int(configured_port)
    except Exception:
        pass

    try:
        from account_manager import get_account_port
        return resolved_account, int(get_account_port(resolved_account, fallback=CDP_PORT))
    except Exception:
        return resolved_account, CDP_PORT


def _port_marker_path(port: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"xhs_chrome_port_{int(port)}.json")


def _read_port_marker(port: int) -> dict:
    path = _port_marker_path(port)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_port_marker(port: int, account: Optional[str], profile_dir: str, pid: int | None) -> None:
    path = _port_marker_path(port)
    payload = {
        "port": int(port),
        "account": (account or "default"),
        "profile_dir": profile_dir,
        "pid": int(pid) if pid else None,
        "updated_at": time.time(),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _clear_port_marker(port: int) -> None:
    path = _port_marker_path(port)
    try:
        os.remove(path)
    except Exception:
        pass


def get_chrome_path() -> str:
    """Find Chrome executable on Windows/macOS/Linux."""
    # 0) explicit override by env
    override = (os.environ.get("XHS_BROWSER_PATH") or os.environ.get("CHROME_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override

    # 1) optional local config override: config/browser.json -> {"browser_path":"..."}
    try:
        cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "browser.json"))
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                path = str(cfg.get("browser_path") or "").strip()
                if path and os.path.isfile(path):
                    return path
    except Exception:
        pass

    candidates = []

    if sys.platform == "win32":
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if not base:
                continue
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
            candidates.append(os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"))
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/usr/bin/microsoft-edge",
                "/usr/bin/brave-browser",
            ]
        )

    for path in candidates:
        if os.path.isfile(path):
            return path

    import shutil
    found = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
        or shutil.which("chrome")
        or shutil.which("chrome.exe")
        or shutil.which("msedge")
        or shutil.which("msedge.exe")
        or shutil.which("brave")
        or shutil.which("brave.exe")
    )
    if found:
        return found

    raise FileNotFoundError(
        "Browser not found. Please install Chrome/Edge/Brave or set XHS_BROWSER_PATH."
    )

def get_user_data_dir(account: Optional[str] = None) -> str:
    """
    Return the Chrome profile directory path for a given account.

    Args:
        account: Account name. If None, uses the default account from account_manager.

    Returns:
        Path to the Chrome user-data-dir for this account.
    """
    try:
        from account_manager import get_profile_dir
        return get_profile_dir(account)
    except ImportError:
        # Fallback if account_manager not available
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            local_app_data = os.path.expanduser("~")
        return os.path.join(local_app_data, "Google", "Chrome", PROFILE_DIR_NAME)


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False


def launch_chrome(
    port: int = CDP_PORT,
    headless: bool = False,
    account: Optional[str] = None,
) -> subprocess.Popen | None:
    """
    Launch Chrome with remote debugging enabled.

    Args:
        port: CDP remote debugging port.
        headless: If True, launch Chrome in headless mode (no GUI window).
        account: Account name to use. If None, uses the default account.

    Returns the Popen object if a new process was started, or None if Chrome
    was already running on the target port.
    """
    global _chrome_process, _current_account

    if is_port_open(port):
        print(f"[chrome_launcher] Chrome already running on port {port}.")
        return None

    chrome_path = get_chrome_path()
    user_data_dir = get_user_data_dir(account)
    _current_account = account

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if headless:
        cmd.append("--headless=new")

    mode_label = "headless" if headless else "headed"
    account_label = account or "default"
    print(f"[chrome_launcher] Launching Chrome ({mode_label}, account: {account_label})...")
    print(f"  executable : {chrome_path}")
    print(f"  profile dir: {user_data_dir}")
    print(f"  debug port : {port}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _chrome_process = proc

    # Wait for the debug port to become available
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_port_open(port):
            print(f"[chrome_launcher] Chrome is ready on port {port}.")
            _write_port_marker(port=port, account=account, profile_dir=user_data_dir, pid=proc.pid)
            return proc
        time.sleep(0.5)

    print(
        f"[chrome_launcher] WARNING: Chrome started but port {port} not responding "
        f"after {STARTUP_TIMEOUT}s. It may still be initializing.",
        file=sys.stderr,
    )
    return proc


def kill_chrome(port: int = CDP_PORT):
    """
    Kill the Chrome instance on the given debug port.

    Tries multiple strategies:
    1. Send CDP Browser.close command via HTTP
    2. Terminate the tracked subprocess
    3. Kill by port on Windows (taskkill)
    """
    global _chrome_process

    # Strategy 1: CDP Browser.close
    try:
        import requests
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        if resp.ok:
            ws_url = resp.json().get("webSocketDebuggerUrl")
            if ws_url:
                import websockets.sync.client as ws_client
                ws = ws_client.connect(ws_url)
                ws.send('{"id":1,"method":"Browser.close"}')
                try:
                    ws.recv(timeout=2)
                except Exception:
                    pass
                ws.close()
                print("[chrome_launcher] Sent Browser.close via CDP.")
    except Exception:
        pass

    # Wait briefly for Chrome to shut down
    time.sleep(1)

    # Strategy 2: Terminate tracked subprocess
    if _chrome_process and _chrome_process.poll() is None:
        try:
            _chrome_process.terminate()
            _chrome_process.wait(timeout=5)
            print("[chrome_launcher] Terminated tracked Chrome process.")
        except Exception:
            try:
                _chrome_process.kill()
            except Exception:
                pass
    _chrome_process = None
    _clear_port_marker(port)

    # Strategy 3: Windows taskkill by port (fallback)
    if sys.platform == "win32" and is_port_open(port):
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5
                    )
                    print(f"[chrome_launcher] Killed process {pid} via taskkill.")
                    break
        except Exception:
            pass

    # Wait for port to be released
    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_port_open(port):
            return
        time.sleep(0.5)

    if is_port_open(port):
        print(f"[chrome_launcher] WARNING: port {port} still open after kill attempt.",
              file=sys.stderr)


def restart_chrome(
    port: int = CDP_PORT,
    headless: bool = False,
    account: Optional[str] = None,
) -> subprocess.Popen | None:
    """
    Kill the current Chrome instance and relaunch with the specified mode.

    Useful for switching between headless and headed mode (e.g. when login
    is needed during a headless session), or switching accounts.

    Args:
        port: CDP remote debugging port.
        headless: If True, relaunch in headless mode.
        account: Account name to use. If None, uses the default account.

    Returns the Popen object for the new Chrome process.
    """
    account_label = account or "default"
    mode_label = "headless" if headless else "headed"
    print(f"[chrome_launcher] Restarting Chrome ({mode_label}, account: {account_label})...")
    kill_chrome(port)
    time.sleep(1)
    return launch_chrome(port, headless=headless, account=account)


def ensure_chrome(
    port: int = CDP_PORT,
    headless: bool = False,
    account: Optional[str] = None,
) -> bool:
    """
    Ensure Chrome is running with remote debugging on the given port.

    Args:
        port: CDP remote debugging port.
        headless: If True, launch in headless mode when starting a new instance.
            If Chrome is already running, this parameter is ignored.
        account: Account name to use. If None, uses the default account.

    Returns True if Chrome is available, False otherwise.
    """
    requested_account = account or "default"
    if is_port_open(port):
        marker = _read_port_marker(port)
        bound_account = str(marker.get("account") or "").strip()
        if bound_account:
            if bound_account == requested_account:
                return True
            print(
                "[chrome_launcher] Port/account mismatch detected: "
                f"port={port} bound={bound_account} requested={requested_account}. "
                "Restarting Chrome with requested account profile..."
            )
            restart_chrome(port=port, headless=headless, account=account)
            return is_port_open(port)

        # Marker missing (Chrome may be started externally). Prefer deterministic account/profile.
        print(
            "[chrome_launcher] Port is open but account binding is unknown. "
            f"Restarting Chrome on port {port} for account={requested_account} to ensure login persistence."
        )
        restart_chrome(port=port, headless=headless, account=account)
        return is_port_open(port)
    try:
        launch_chrome(port, headless=headless, account=account)
        return is_port_open(port)
    except FileNotFoundError as e:
        print(f"[chrome_launcher] Error: {e}", file=sys.stderr)
        return False


def get_current_account() -> Optional[str]:
    """Get the name of the currently active account."""
    return _current_account


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Chrome Launcher for CDP")
    parser.add_argument("--port", type=int, default=CDP_PORT,
                        help=f"CDP remote debugging port (default: {CDP_PORT})")
    parser.add_argument("--headless", action="store_true", help="Launch in headless mode")
    parser.add_argument("--kill", action="store_true", help="Kill the running Chrome instance")
    parser.add_argument("--restart", action="store_true", help="Restart Chrome")
    parser.add_argument("--account", help="Account name to use (default: default account)")
    args = parser.parse_args()

    port_explicit = "--port" in sys.argv
    account_explicit = "--account" in sys.argv
    resolved_account, resolved_port = get_default_account_and_port(
        account=args.account if account_explicit else None,
        port=args.port if port_explicit else None,
    )
    args.account = resolved_account
    args.port = resolved_port

    if args.kill:
        kill_chrome(port=args.port)
        print("[chrome_launcher] Chrome killed.")
    elif args.restart:
        restart_chrome(port=args.port, headless=args.headless, account=args.account)
        print("[chrome_launcher] Chrome restarted.")
    elif ensure_chrome(port=args.port, headless=args.headless, account=args.account):
        print("[chrome_launcher] Chrome is ready for CDP connections.")
    else:
        print("[chrome_launcher] Failed to start Chrome.", file=sys.stderr)
        sys.exit(1)
