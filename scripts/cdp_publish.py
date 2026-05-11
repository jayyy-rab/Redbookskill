"""
CDP-based Xiaohongshu publisher.

Connects to a Chrome instance via Chrome DevTools Protocol to automate
publishing articles on Xiaohongshu (RED) creator center.

CLI usage:
    # Basic commands
    python cdp_publish.py [--host HOST] [--port PORT] check-login [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] fill --title "标题" --content "正文" --images img1.jpg [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] publish --title "标题" --content "正文" --images img1.jpg [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] click-publish [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] get-login-qrcode [--wait-seconds 20]
    python cdp_publish.py [--host HOST] [--port PORT] list-feeds
    python cdp_publish.py [--host HOST] [--port PORT] search-feeds --keyword "关键词" [--sort-by 综合|最新|最多点赞|最多评论|最多收藏]
    python cdp_publish.py [--host HOST] [--port PORT] get-feed-detail --feed-id FEED_ID --xsec-token TOKEN [--load-all-comments]
    python cdp_publish.py [--host HOST] [--port PORT] post-comment-to-feed --feed-id FEED_ID --xsec-token TOKEN --content "评论内容"
    python cdp_publish.py [--host HOST] [--port PORT] respond-comment --feed-id FEED_ID --xsec-token TOKEN --content "回复内容" [--comment-id ID]
    python cdp_publish.py [--host HOST] [--port PORT] note-upvote --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-unvote --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-bookmark --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-unbookmark --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] profile-snapshot [--profile-url URL | --user-id USER_ID]
    python cdp_publish.py [--host HOST] [--port PORT] notes-from-profile [--profile-url URL | --user-id USER_ID]
    python cdp_publish.py [--host HOST] [--port PORT] get-notification-mentions [--wait-seconds 18]
    python cdp_publish.py [--host HOST] [--port PORT] content-data [--page-num 1] [--page-size 10] [--type 0]

    # Account management
    python cdp_publish.py [--host HOST] [--port PORT] login [--account NAME]           # open browser for QR login
    python cdp_publish.py [--host HOST] [--port PORT] re-login [--account NAME]        # clear cookies and re-login same account
    python cdp_publish.py [--host HOST] [--port PORT] switch-account [--account NAME]  # clear cookies + open login for new account
    python cdp_publish.py [--host HOST] [--port PORT] list-accounts                    # list all configured accounts
    python cdp_publish.py [--host HOST] [--port PORT] add-account NAME [--alias ALIAS] # add a new account
    python cdp_publish.py [--host HOST] [--port PORT] remove-account NAME              # remove an account

Library usage:
    from cdp_publish import XiaohongshuPublisher

    publisher = XiaohongshuPublisher()
    publisher.connect()
    publisher.check_login()
    publisher.publish(
        title="Article title",
        content="Article body text",
        image_paths=["/path/to/img1.jpg", "/path/to/img2.jpg"],
    )
"""

import json
import os
import random
import time
import sys
import csv
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Any

# Add scripts dir to path so sibling modules can be imported in both
# "python scripts/cdp_publish.py" and "import scripts.cdp_publish" modes.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
import websockets.sync.client as ws_client
from feed_explorer import (
    SEARCH_BASE_URL,
    LOCATION_OPTIONS,
    NOTE_TYPE_OPTIONS,
    PUBLISH_TIME_OPTIONS,
    SEARCH_SCOPE_OPTIONS,
    SORT_BY_OPTIONS,
    FeedExplorer,
    FeedExplorerError,
    SearchFilters,
    make_feed_detail_url,
    make_search_url,
)
from run_lock import SingleInstanceError, single_instance

# ---------------------------------------------------------------------------
# Configuration - centralised selectors and URLs for easy maintenance
# ---------------------------------------------------------------------------

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

# Xiaohongshu URLs
XHS_CREATOR_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
XHS_HOME_URL = "https://www.xiaohongshu.com"
XHS_NOTIFICATION_URL = "https://www.xiaohongshu.com/notification"
XHS_CREATOR_LOGIN_CHECK_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
XHS_HOME_LOGIN_MODAL_KEYWORD = "登录后推荐更懂你的笔记"
XHS_CONTENT_DATA_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
XHS_CONTENT_DATA_API_PATH = "/api/galaxy/creator/datacenter/note/analyze/list"
XHS_NOTIFICATION_MENTIONS_API_PATH = "/api/sns/web/v1/you/mentions"
XHS_SEARCH_RECOMMEND_API_PATH = "/api/sns/web/v1/search/recommend"
XHS_FEED_INACCESSIBLE_KEYWORDS = (
    "当前笔记暂时无法浏览",
    "该内容因违规已被删除",
    "该笔记已被删除",
    "内容不存在",
    "笔记不存在",
    "已失效",
    "私密笔记",
    "仅作者可见",
    "因用户设置，你无法查看",
    "因违规无法查看",
)

# DOM selectors (update these when Xiaohongshu changes their page structure)
# Last verified against creator center changes landed by 2026-03.
SELECTORS = {
    # "上传图文" tab - must click before uploading images
    "image_text_tab": "div.creator-tab",
    "image_text_tab_text": "上传图文",
    # "上传视频" tab - must click before uploading video
    "video_tab": "div.creator-tab",
    "video_tab_text": "上传视频",
    # Upload area - the file input element for images (visible after clicking tab)
    "upload_input": ".upload-input",
    "upload_input_alt": 'input[type="file"]',
    # Title input field (visible after image upload)
    "title_input": "div.d-input input",
    "title_input_alt": 'input[placeholder*="填写标题"], input[placeholder*="标题"], input.d-text',
    # Content editor area - current creator center may expose TipTap, ProseMirror or Quill.
    "content_editor": "div.tiptap.ProseMirror",
    "content_editor_alt": 'div.ProseMirror[contenteditable="true"]',
    "content_editor_alt2": "div.ql-editor",
    "content_placeholder_text": "输入正文描述",
    # Publish button
    "publish_button": ".publish-page-publish-btn button.bg-red",
    "publish_button_text": "发布",
    "schedule_publish_button_text": "定时发布",
    "schedule_switch": ".post-time-wrapper .d-switch",
    "schedule_datetime_input": ".date-picker-container input",
    "image_preview_items": ".img-preview-area .pr",
    # Login indicator - URL-based check (redirect to /login if not logged in)
    "login_indicator": '.user-info, .creator-header, [class*="user"]',
}

# Timing
PAGE_LOAD_WAIT = 3  # seconds to wait after navigation
TAB_CLICK_WAIT = 2  # seconds to wait after clicking tab
UPLOAD_WAIT = 6  # seconds to wait after image upload for editor to appear
VIDEO_PROCESS_TIMEOUT = 120  # seconds to wait for video processing
VIDEO_PROCESS_POLL = 3  # seconds between video processing status checks
ACTION_INTERVAL = 1  # seconds between actions
MAX_TIMING_JITTER_RATIO = 0.7
CDP_COMMAND_TIMEOUT = 15.0
# Runtime.evaluate with awaitPromise can run multi-second page scripts (e.g. topic tag typing).
RUNTIME_EVALUATE_TIMEOUT_SECONDS = float(
    os.environ.get("REDBOOK_CDP_EVAL_TIMEOUT", "").strip() or "30"
)
DEFAULT_LOGIN_CACHE_TTL_HOURS = 12.0
LOGIN_CACHE_FILE = os.path.abspath(
    os.path.join(SCRIPT_DIR, "..", "tmp", "login_status_cache.json")
)


def _normalize_timing_jitter(value: float) -> float:
    """Clamp timing jitter to a safe range."""
    return max(0.0, min(MAX_TIMING_JITTER_RATIO, value))


def _is_local_host(host: str) -> bool:
    """Return True when host points to the local machine."""
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _resolve_account_name(account_name: str | None) -> str:
    """Resolve explicit or default account name for cache scoping."""
    if account_name and account_name.strip():
        return account_name.strip()
    try:
        from account_manager import get_default_account
        resolved = get_default_account()
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    except Exception:
        pass
    return "default"


def _build_search_filters_from_args(args) -> SearchFilters | None:
    """Build search filter object from parsed CLI arguments."""
    filters = SearchFilters(
        sort_by=getattr(args, "sort_by", None),
        note_type=getattr(args, "note_type", None),
        publish_time=getattr(args, "publish_time", None),
        search_scope=getattr(args, "search_scope", None),
        location=getattr(args, "location", None),
    )
    return filters if filters.selected_items() else None


def _format_post_time(post_time_ms: Any) -> str:
    """Format note publish time in Asia/Shanghai timezone."""
    if not isinstance(post_time_ms, (int, float)):
        return "-"
    try:
        dt = datetime.fromtimestamp(post_time_ms / 1000, tz=ZoneInfo("Asia/Shanghai"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"

def validate_schedule_post_time(dt_str: str | None) -> bool:
    """
    Validate a datetime string in the format 'yyyy-MM-dd HH:mm'.
    
    Rules:
    1. If input is None or empty, return False.
    2. The datetime format must strictly match '%Y-%m-%d %H:%M'.
    3. The datetime must fall within the range:
       [ current_time , current_time + 14 days ).
       
    Returns:
        bool: True if valid, False otherwise.
    """
    if not dt_str:
        return False
    
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return False

    now = datetime.now().replace(second=0, microsecond=0)
    upper_bound = now + timedelta(days=14)
    return now <= dt < upper_bound

def _format_cover_click_rate(value: Any) -> str:
    """Format cover click rate as percentage text."""
    if not isinstance(value, (int, float)):
        return "-"
    normalized = value * 100 if 0 <= value <= 1 else value
    return f"{normalized:.2f}%"


def _format_view_time_avg(value: Any) -> str:
    """Format average view duration in seconds."""
    if not isinstance(value, (int, float)):
        return "-"
    return f"{int(value)}s"


def _metric_or_dash(note: dict[str, Any], field: str) -> Any:
    """Return field value if present, otherwise '-'."""
    value = note.get(field)
    return "-" if value is None else value


def _map_note_infos_to_content_rows(note_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map note_infos payload to content table rows."""
    rows: list[dict[str, Any]] = []
    for note in note_infos:
        rows.append({
            "标题": note.get("title") or "-",
            "发布时间": _format_post_time(note.get("post_time")),
            "曝光": _metric_or_dash(note, "imp_count"),
            "观看": _metric_or_dash(note, "read_count"),
            "封面点击率": _format_cover_click_rate(note.get("coverClickRate")),
            "点赞": _metric_or_dash(note, "like_count"),
            "评论": _metric_or_dash(note, "comment_count"),
            "收藏": _metric_or_dash(note, "fav_count"),
            "涨粉": _metric_or_dash(note, "increase_fans_count"),
            "分享": _metric_or_dash(note, "share_count"),
            "人均观看时长": _format_view_time_avg(note.get("view_time_avg")),
            "弹幕": _metric_or_dash(note, "danmaku_count"),
            "操作": "详情数据",
            "_id": note.get("id") or "",
        })
    return rows


def _write_content_data_csv(csv_file: str, rows: list[dict[str, Any]]) -> str:
    """Write content rows to a UTF-8 CSV file and return absolute path."""
    abs_path = os.path.abspath(csv_file)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    columns = [
        "标题",
        "发布时间",
        "曝光",
        "观看",
        "封面点击率",
        "点赞",
        "评论",
        "收藏",
        "涨粉",
        "分享",
        "人均观看时长",
        "弹幕",
        "操作",
        "_id",
    ]
    with open(abs_path, "w", encoding="utf-8-sig", newline="") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return abs_path


class CDPError(Exception):
    """Error communicating with Chrome via CDP."""


class XiaohongshuPublisher:
    """Automates publishing to Xiaohongshu via CDP."""

    def __init__(
        self,
        host: str = CDP_HOST,
        port: int = CDP_PORT,
        timing_jitter: float = 0.25,
        account_name: str | None = None,
        preserve_upload_paths: bool = False,
        context_key: str | None = None,
    ):
        self.host = host
        self.port = port
        self.ws = None
        self._msg_id = 0
        self.timing_jitter = _normalize_timing_jitter(timing_jitter)
        self.account_name = (account_name or "default").strip() or "default"
        self.preserve_upload_paths = bool(preserve_upload_paths)
        self.context_key = context_key
        self.command_timeout_seconds = CDP_COMMAND_TIMEOUT
        self.login_cache_ttl_hours = DEFAULT_LOGIN_CACHE_TTL_HOURS
        self.login_cache_ttl_seconds = self.login_cache_ttl_hours * 3600
        self.login_cache_file = LOGIN_CACHE_FILE

    def _prepare_upload_file_path(self, file_path: str) -> str:
        """Return the file path to send to DOM.setFileInputFiles."""
        if self._should_preserve_upload_path(file_path):
            return file_path
        return file_path.replace("\\", "/")

    def _looks_like_windows_drive_path(self, file_path: str) -> bool:
        """Return True when the path looks like a Windows drive-letter path."""
        return len(file_path) >= 3 and file_path[0].isalpha() and file_path[1] == ":" and file_path[2] in ("\\", "/")

    def _looks_like_unc_path(self, file_path: str) -> bool:
        """Return True when the path looks like a UNC path."""
        return file_path.startswith("\\\\") or file_path.startswith("//")

    def _looks_like_windows_backslash_path(self, file_path: str) -> bool:
        """Return True when the path likely follows Windows-style backslash syntax."""
        if "\\" not in file_path or "/" in file_path:
            return False
        if file_path.startswith("\\"):
            return True
        parts = [part for part in file_path.split("\\") if part]
        return len(parts) >= 2

    def _should_preserve_upload_path(self, file_path: str) -> bool:
        """Return True when upload path should be preserved as-is."""
        if self.preserve_upload_paths:
            return True
        return (
            self._looks_like_windows_drive_path(file_path)
            or self._looks_like_unc_path(file_path)
            or self._looks_like_windows_backslash_path(file_path)
        )

    def _login_cache_key(self, scope: str) -> str:
        """Build a unique cache key for one login scope."""
        return f"{self.host}:{self.port}:{self.account_name}:{scope}"

    def _load_login_cache(self) -> dict[str, Any]:
        """Load login cache payload from local JSON file."""
        if not os.path.exists(self.login_cache_file):
            return {"entries": {}}

        try:
            with open(self.login_cache_file, "r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except Exception:
            return {"entries": {}}

        if not isinstance(payload, dict):
            return {"entries": {}}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            payload["entries"] = {}
        return payload

    def _save_login_cache(self, payload: dict[str, Any]):
        """Persist login cache payload to local JSON file."""
        parent = os.path.dirname(self.login_cache_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.login_cache_file, "w", encoding="utf-8") as cache_file:
            json.dump(payload, cache_file, ensure_ascii=False, indent=2)

    def _get_cached_login_status(self, scope: str) -> bool | None:
        """Return cached login status when cache is still fresh."""
        if self.login_cache_ttl_seconds <= 0:
            return None

        payload = self._load_login_cache()
        entries = payload.get("entries", {})
        entry = entries.get(self._login_cache_key(scope))
        if not isinstance(entry, dict):
            return None

        checked_at = entry.get("checked_at")
        logged_in = entry.get("logged_in")
        if not isinstance(checked_at, (int, float)) or not isinstance(logged_in, bool):
            return None

        age_seconds = time.time() - float(checked_at)
        if age_seconds < 0 or age_seconds > self.login_cache_ttl_seconds:
            return None

        if not logged_in:
            return None

        age_minutes = int(age_seconds // 60)
        print(
            "[cdp_publish] Using cached login status "
            f"({scope}, age={age_minutes}m, ttl={self.login_cache_ttl_hours:g}h)."
        )
        return logged_in

    def _set_login_cache(self, scope: str, logged_in: bool):
        """Save positive login status cache for a specific scope."""
        if not logged_in:
            self._clear_login_cache(scope=scope)
            return

        payload = self._load_login_cache()
        entries = payload.setdefault("entries", {})
        entries[self._login_cache_key(scope)] = {
            "logged_in": True,
            "checked_at": int(time.time()),
        }
        self._save_login_cache(payload)

    def _clear_login_cache(self, scope: str | None = None):
        """Clear login cache entries for current host/port/account."""
        payload = self._load_login_cache()
        entries = payload.get("entries", {})
        if not isinstance(entries, dict) or not entries:
            return

        changed = False
        if scope:
            key = self._login_cache_key(scope)
            if key in entries:
                entries.pop(key, None)
                changed = True
        else:
            prefix = self._login_cache_key("")
            for key in list(entries.keys()):
                if key.startswith(prefix):
                    entries.pop(key, None)
                    changed = True

        if changed:
            payload["entries"] = entries
            self._save_login_cache(payload)

    def _sleep(self, base_seconds: float, minimum_seconds: float = 0.05):
        """Sleep with optional randomized jitter to avoid rigid timing patterns."""
        base = max(minimum_seconds, float(base_seconds))
        if self.timing_jitter <= 0:
            time.sleep(base)
            return

        delta = base * self.timing_jitter
        low = max(minimum_seconds, base - delta)
        high = max(low, base + delta)
        time.sleep(random.uniform(low, high))

    # ------------------------------------------------------------------
    # CDP connection management
    # ------------------------------------------------------------------

    def _get_targets(self) -> list[dict]:
        """Get list of available browser targets (tabs). Retries once on failure."""
        url = f"http://{self.host}:{self.port}/json"
        for attempt in range(2):
            try:
                resp = requests.get(
                    url,
                    timeout=5,
                    proxies={"http": None, "https": None} if _is_local_host(self.host) else None,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 0:
                    if _is_local_host(self.host):
                        print(f"[cdp_publish] CDP connection failed ({e}), restarting Chrome...")
                        from chrome_launcher import ensure_chrome
                        ensure_chrome(port=self.port, account=self.account_name)
                    else:
                        print(
                            f"[cdp_publish] CDP connection failed ({e}), retrying remote endpoint "
                            f"{self.host}:{self.port}..."
                        )
                    self._sleep(2, minimum_seconds=1.0)
                else:
                    raise CDPError(f"Cannot reach Chrome on {self.host}:{self.port}: {e}")

    @staticmethod
    def _looks_like_picset_tab(url: str) -> bool:
        """Avoid hijacking Picset / picset.ai workspace tabs for generic CDP reconnects."""
        return "picsetai" in (url or "").lower()

    def _find_or_create_tab(
        self,
        target_url_prefix: str = "",
        reuse_existing_tab: bool = False,
    ) -> str:
        """
        Find a tab to connect.

        Default behavior is backward-compatible: create a new tab first.
        When `reuse_existing_tab` is enabled, prefer reusing an existing page tab
        to reduce focus switching in headed mode.
        """
        targets = self._get_targets()
        pages = [
            t for t in targets
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
        ]

        if target_url_prefix:
            for t in pages:
                url = t.get("url", "")
                if target_url_prefix in url:
                    return t["webSocketDebuggerUrl"]

        if reuse_existing_tab and pages:
            # Prefer any Xiaohongshu tab — never attach `reuse` to Picset first, or a later
            # `search-feeds` / reconnect will navigate THAT tab away and kill the Picset session
            # (looks like Picset closes and XHS "searches again", especially after Step-A retries).
            for t in pages:
                curl = str(t.get("url") or "")
                if "xiaohongshu.com" in curl.lower():
                    print(
                        "[cdp_publish] Reusing existing Xiaohongshu tab to reduce focus switching: "
                        f"{curl}"
                    )
                    return t["webSocketDebuggerUrl"]

            front = str(pages[0].get("url") or "")
            if self._looks_like_picset_tab(front):
                print(
                    "[cdp_publish] No Xiaohongshu tab to reuse; opening a new home tab so Picset "
                    f"workspace is not hijacked (first tab was: {front}).",
                    flush=True,
                )
                resp = requests.put(
                    f"http://{self.host}:{self.port}/json/new?{XHS_HOME_URL}",
                    timeout=5,
                    proxies=(
                        {"http": None, "https": None} if _is_local_host(self.host) else None
                    ),
                )
                if resp.ok:
                    ws_new = resp.json().get("webSocketDebuggerUrl", "")
                    if ws_new:
                        return ws_new

            print(
                "[cdp_publish] Reusing existing tab to reduce focus switching: "
                f"{front}"
            )
            return pages[0]["webSocketDebuggerUrl"]

        # Create a new tab
        resp = requests.put(
            f"http://{self.host}:{self.port}/json/new?{XHS_CREATOR_URL}",
            timeout=5,
            proxies={"http": None, "https": None} if _is_local_host(self.host) else None,
        )
        if resp.ok:
            ws_url = resp.json().get("webSocketDebuggerUrl", "")
            if ws_url:
                return ws_url

        # Fallback: use first available page
        if pages:
            return pages[0]["webSocketDebuggerUrl"]

        raise CDPError("No browser tabs available.")

    def connect(self, target_url_prefix: str = "", reuse_existing_tab: bool = False):
        """Connect to a Chrome tab via WebSocket."""
        ws_url = self._find_or_create_tab(
            target_url_prefix=target_url_prefix,
            reuse_existing_tab=reuse_existing_tab,
        )
        if not ws_url:
            raise CDPError("Could not obtain WebSocket URL for any tab.")

        print(f"[cdp_publish] Connecting to {ws_url}")
        self.ws = ws_client.connect(ws_url)
        print("[cdp_publish] Connected to Chrome tab.")

    def disconnect(self):
        """Close the WebSocket connection."""
        if self.ws:
            self.ws.close()
            self.ws = None

    def _reconnect_cdp(self, _retry_count: int = 0) -> bool:
        """CDP 断开后重新获取 target 并建立新 WebSocket 连接。成功返回 True。"""
        print("[cdp_publish] CDP 连接断开，尝试重新连接...")
        old_ws = self.ws
        self.ws = None
        if old_ws:
            try:
                old_ws.close()
            except Exception:
                pass
        try:
            import requests
            # 1) 优先查找小红书发布页 tab
            ws_url = self._find_or_create_tab(
                target_url_prefix="creator.xiaohongshu.com/publish",
                reuse_existing_tab=True,
            )
            # 2) 无发布页时找任意小红书 tab（排除 chrome://）
            if not ws_url:
                targets = self._get_targets()
                pages = [
                    t for t in targets
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                ]
                for t in pages:
                    curl = str(t.get("url") or "")
                    if "xiaohongshu.com" in curl.lower() and "chrome://" not in curl:
                        ws_url = t["webSocketDebuggerUrl"]
                        print(f"[cdp_publish] 使用小红书 tab: {curl[:60]}")
                        break
            # 3) 无任何小红书 tab，创建新发布页 tab
            if not ws_url:
                resp = requests.put(
                    f"http://{self.host}:{self.port}/json/new?{XHS_CREATOR_URL}",
                    timeout=5,
                )
                if resp.ok:
                    ws_url = resp.json().get("webSocketDebuggerUrl", "")
                    print(f"[cdp_publish] 创建发布页 tab")
            if not ws_url:
                print("[cdp_publish] 重连失败：无法找到或创建小红书发布页 tab")
                return False

            self.ws = ws_client.connect(ws_url)
            print(f"[cdp_publish] CDP 重连成功")

            # 4) 确认 URL 是小红书发布页，否则导航
            try:
                current_url = str(self._evaluate("window.location.href") or "")
                if "/publish" not in current_url or "xiaohongshu.com" not in current_url:
                    print(f"[cdp_publish] 重连后 URL 不是发布页: {current_url}，导航到发布页")
                    self._navigate(XHS_CREATOR_URL)
            except Exception:
                print(f"[cdp_publish] 重连后无法读取 URL，导航到发布页")
                self._navigate(XHS_CREATOR_URL)
            return True
        except Exception as e:
            print(f"[cdp_publish] CDP 重连异常: {e}")
            if _retry_count < 1 and "No browser tabs available" in str(e):
                print("[cdp_publish] 2 秒后重试...")
                time.sleep(2.0)
                return self._reconnect_cdp(_retry_count=1)
            self.ws = None
            return False

    # ------------------------------------------------------------------
    # CDP command helpers
    # ------------------------------------------------------------------

    def _send(
        self,
        method: str,
        params: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Send a CDP command and return the result with a bounded wait。

        如果 WebSocket 连接断开，自动重连一次后重试。
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._msg_id += 1
        message_id = self._msg_id
        msg = {"id": message_id, "method": method}
        if params:
            msg["params"] = params

        try:
            self.ws.send(json.dumps(msg))
        except Exception as exc:
            # 连接断开时尝试重连 + 重试一次
            if self._reconnect_cdp():
                print(f"[cdp_publish] 重连后重试 CDP 命令: {method}")
                self._msg_id += 1
                msg["id"] = self._msg_id
                self.ws.send(json.dumps(msg))
            else:
                raise CDPError(
                    f"CDP send failed for {method}: {exc}"
                ) from exc

        timeout = float(timeout_seconds or self.command_timeout_seconds)
        deadline = time.monotonic() + max(0.1, timeout)

        # Wait for the matching response
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError(
                    f"Timed out waiting for CDP response to {method} "
                    f"after {timeout:.1f}s."
                )

            try:
                raw = self.ws.recv(timeout=max(0.1, remaining))
            except (TimeoutError, Exception) as exc:
                # 超时或连接断开时尝试重连 + 递归重试一次
                if self._reconnect_cdp():
                    print(f"[cdp_publish] 重连后递归重试 CDP 命令: {method}")
                    return self._send(
                        method, params=params, timeout_seconds=timeout_seconds
                    )
                raise CDPError(
                    f"CDP receive failed while waiting for {method}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CDPError(
                    f"Received invalid CDP JSON while waiting for {method}: {exc}"
                ) from exc

            if data.get("id") == message_id:
                if "error" in data:
                    raise CDPError(f"CDP error: {data['error']}")
                return data.get("result", {})
            # else: it's an event, skip it

    def _build_content_data_result(
        self,
        payload: dict[str, Any],
        request_url: str,
        page_num: int,
        page_size: int,
        note_type: int,
        capture_mode: str,
    ) -> dict[str, Any]:
        """Normalize content-data API payload into CLI output."""
        data = payload.get("data")
        note_infos = data.get("note_infos") if isinstance(data, dict) else []
        if not isinstance(note_infos, list):
            note_infos = []
        rows = _map_note_infos_to_content_rows(note_infos)

        query = parse_qs(urlparse(request_url).query)

        def _query_int(name: str, default: int) -> int:
            raw = (query.get(name) or [str(default)])[0]
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        return {
            "request_url": request_url,
            "requested_page_num": page_num,
            "requested_page_size": page_size,
            "requested_type": note_type,
            "resolved_page_num": _query_int("page_num", page_num),
            "resolved_page_size": _query_int("page_size", page_size),
            "resolved_type": _query_int("type", note_type),
            "total": data.get("total") if isinstance(data, dict) else None,
            "count_returned": len(rows),
            "rows": rows,
            "capture_mode": capture_mode,
        }

    def _fetch_content_data_via_page_fetch(
        self,
        page_num: int,
        page_size: int,
        note_type: int,
    ) -> dict[str, Any]:
        """Fetch content-data API from browser page context using explicit params."""
        query = urlencode(
            {
                "page_num": page_num,
                "page_size": page_size,
                "type": note_type,
            }
        )
        request_path = f"{XHS_CONTENT_DATA_API_PATH}?{query}"
        result = self._evaluate(f"""
            (async () => {{
                try {{
                    const response = await fetch({json.dumps(request_path)}, {{
                        method: "GET",
                        credentials: "include",
                        cache: "no-store",
                        headers: {{
                            "Accept": "application/json, text/plain, */*"
                        }}
                    }});
                    const body = await response.text();
                    return {{
                        ok: response.ok,
                        status: response.status,
                        url: response.url,
                        body,
                    }};
                }} catch (error) {{
                    return {{
                        ok: false,
                        status: 0,
                        url: {json.dumps(request_path)},
                        error: String(error),
                        body: "",
                    }};
                }}
            }})()
        """)

        if not isinstance(result, dict):
            raise CDPError("Unexpected page-fetch result for content data API.")

        if not result.get("ok"):
            raise CDPError(
                "Content data page fetch failed: "
                f"status={result.get('status')}, error={result.get('error') or 'unknown'}"
            )

        body_text = result.get("body", "")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise CDPError(
                "Failed to decode content data API JSON from page fetch: "
                f"{exc}; preview={body_text[:300]}"
            ) from exc

        if not isinstance(payload, dict):
            raise CDPError("Unexpected content data payload structure.")

        return self._build_content_data_result(
            payload=payload,
            request_url=str(result.get("url") or request_path),
            page_num=page_num,
            page_size=page_size,
            note_type=note_type,
            capture_mode="page_fetch",
        )

    def _capture_content_data_from_page_request(
        self,
        page_num: int,
        page_size: int,
        note_type: int,
    ) -> dict[str, Any]:
        """Fallback: capture the page-triggered content-data request via CDP."""
        self._send("Page.enable")
        self._send("Network.enable", {"maxPostDataSize": 65536})
        self._send("Page.navigate", {"url": XHS_CONTENT_DATA_URL})

        request_url_by_id: dict[str, str] = {}
        target_request_id = ""
        target_request_url = ""
        deadline = time.time() + 18

        while time.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv(timeout=timeout)
            except TimeoutError:
                continue

            message = json.loads(raw)
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                request = params.get("request", {})
                if isinstance(request_id, str):
                    request_url_by_id[request_id] = request.get("url", "")
                continue

            if method == "Network.responseReceived":
                request_id = params.get("requestId")
                if not isinstance(request_id, str):
                    continue

                request_url = request_url_by_id.get(request_id, "")
                if XHS_CONTENT_DATA_API_PATH not in request_url:
                    continue

                status = params.get("response", {}).get("status")
                if status != 200:
                    raise CDPError(
                        "Content data API responded with non-200 status: "
                        f"{status}, url={request_url}"
                    )

                target_request_id = request_id
                target_request_url = request_url
                break

        if not target_request_id:
            raise CDPError(
                "Timed out waiting for content data request. "
                "Please open data-analysis page manually and retry."
            )

        body_result = self._send("Network.getResponseBody", {"requestId": target_request_id})
        body_text = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body_text = base64.b64decode(body_text).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise CDPError(
                "Failed to decode content data API JSON: "
                f"{exc}; preview={body_text[:300]}"
            ) from exc

        if not isinstance(payload, dict):
            raise CDPError("Unexpected content data payload structure.")

        return self._build_content_data_result(
            payload=payload,
            request_url=target_request_url,
            page_num=page_num,
            page_size=page_size,
            note_type=note_type,
            capture_mode="network_capture",
        )

    def _evaluate(self, expression: str) -> Any:
        """Execute JavaScript in the page and return the result value."""
        ev_to = max(
            float(self.command_timeout_seconds),
            float(RUNTIME_EVALUATE_TIMEOUT_SECONDS),
        )
        result = self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout_seconds=ev_to,
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "error":
            raise CDPError(f"JS error: {remote_obj.get('description', remote_obj)}")
        return remote_obj.get("value")

    def _navigate(self, url: str):
        """Navigate the current tab to the given URL and wait for load."""
        print(f"[cdp_publish] Navigating to {url}")
        self._send("Page.enable")
        self._send("Page.navigate", {"url": url})
        self._sleep(PAGE_LOAD_WAIT, minimum_seconds=1.0)

    def _current_page_url(self) -> str:
        """Best-effort URL of the connected tab (avoid redundant navigations)."""
        try:
            href = self._evaluate(
                "typeof location !== 'undefined' ? location.href : ''"
            )
            return href.strip() if isinstance(href, str) else ""
        except Exception:
            return ""

    @staticmethod
    def _url_is_picset_origin(url: str) -> bool:
        u = (url or "").lower()
        return "picsetai.cn" in u or "picsetai.com" in u

    def _navigate_picset_preserving_session(self, picset_url: str) -> None:
        """
        Go to Picset entry URL without forcing a full reload when already on Picset.

        Automating Picset often uses connect(reuse_existing_tab=True) then Page.navigate.
        Navigating again on the same SPA tab can reset client-side session and show the
        login screen even though cookies are still valid.
        """
        cur = self._current_page_url()
        if (
            cur
            and self._url_is_picset_origin(cur)
            and self._url_is_picset_origin(picset_url)
        ):
            print(
                "[cdp_publish] Already on Picset; skipping navigation to preserve login/session."
            )
            return
        self._navigate(picset_url)

    # ------------------------------------------------------------------
    # Public navigation helper
    # ------------------------------------------------------------------

    def navigate(self, url: str) -> bool:
        """Navigate to URL and wait for load.  Public wrapper around _navigate."""
        try:
            self._navigate(url)
            return True
        except Exception as exc:
            print(f"[cdp_publish] navigate failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Public screenshot helper
    # ------------------------------------------------------------------

    def capture_screenshot(self, output_path: str) -> bool:
        """Capture full-viewport screenshot and save to PNG file."""
        try:
            result = self._send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            img_b64 = result.get("data", "")
            if not isinstance(img_b64, str) or not img_b64:
                print("[cdp_publish] capture_screenshot: no data returned")
                return False
            import base64
            img_data = base64.b64decode(img_b64)
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_data)
            print(f"[cdp_publish] Screenshot saved: {output_path}")
            return True
        except Exception as exc:
            print(f"[cdp_publish] capture_screenshot failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Login check
    # ------------------------------------------------------------------

    def check_login(self) -> bool:
        """
        Navigate to Xiaohongshu creator center and check if the user is logged in.

        Returns True if logged in. If not logged in, prints instructions
        and returns False.
        """
        scope = "creator"
        cached_status = self._get_cached_login_status(scope)
        if cached_status is not None:
            if cached_status:
                print("[cdp_publish] Login confirmed (cached).")
            return cached_status

        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(2, minimum_seconds=1.0)

        # Check if we got redirected to a login page
        current_url = self._evaluate("window.location.href")
        print(f"[cdp_publish] Current URL: {current_url}")

        if "login" in current_url.lower():
            self._set_login_cache(scope, logged_in=False)
            print(
                "\n[cdp_publish] NOT LOGGED IN.\n"
                "  Please scan the QR code in the Chrome window to log in,\n"
                "  then run this script again.\n"
            )
            return False

        self._set_login_cache(scope, logged_in=True)
        print("[cdp_publish] Login confirmed.")
        return True

    def _home_login_prompt_visible(self, keyword: str) -> bool:
        """Return True when home page login prompt modal is visible."""
        keyword_literal = json.dumps(keyword)
        visible = self._evaluate(f"""
            (() => {{
                const keyword = {keyword_literal};
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const containsKeyword = (text) => normalize(text).includes(keyword);

                const modalSelectors = [
                    "[class*='login']",
                    "[class*='modal']",
                    "[class*='popup']",
                    "[class*='dialog']",
                    "[class*='mask']",
                ];

                for (const selector of modalSelectors) {{
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (!(node instanceof HTMLElement)) {{
                            continue;
                        }}
                        if (node.offsetParent === null) {{
                            continue;
                        }}
                        if (containsKeyword(node.textContent) || containsKeyword(node.innerText)) {{
                            return true;
                        }}
                    }}
                }}

                if (document.body && containsKeyword(document.body.innerText)) {{
                    return true;
                }}
                return false;
            }})()
        """)
        return bool(visible)

    def check_home_login(
        self,
        keyword: str = XHS_HOME_LOGIN_MODAL_KEYWORD,
        wait_seconds: float = 8.0,
    ) -> bool:
        """
        Check login state on Xiaohongshu home page.

        Login prompt modal keyword (default: "登录后推荐更懂你的笔记") indicates
        unauthenticated state for the xiaohongshu.com home/feed domain.
        """
        scope = "home"
        cached_status = self._get_cached_login_status(scope)
        if cached_status is not None:
            if cached_status:
                print("[cdp_publish] Home login confirmed (cached).")
            return cached_status

        self._navigate(XHS_HOME_URL)
        self._sleep(2, minimum_seconds=1.0)

        current_url = self._evaluate("window.location.href")
        print(f"[cdp_publish] Home URL: {current_url}")
        if isinstance(current_url, str) and "login" in current_url.lower():
            self._set_login_cache(scope, logged_in=False)
            print(
                "\n[cdp_publish] NOT LOGGED IN (HOME).\n"
                "  Please log in on xiaohongshu.com and run this command again.\n"
            )
            return False

        deadline = time.time() + max(1.0, wait_seconds)
        while time.time() < deadline:
            if self._home_login_prompt_visible(keyword):
                self._set_login_cache(scope, logged_in=False)
                print(
                    "\n[cdp_publish] NOT LOGGED IN (HOME).\n"
                    f"  Detected login prompt keyword: {keyword}\n"
                    "  Please log in on xiaohongshu.com and run this command again.\n"
                )
                return False
            self._sleep(0.7, minimum_seconds=0.2)

        self._set_login_cache(scope, logged_in=True)
        print("[cdp_publish] Home login confirmed.")
        return True

    def clear_cookies(self, domain: str = ".xiaohongshu.com"):
        """
        Clear all cookies for the given domain to force re-login.

        Used when switching accounts.
        """
        print(f"[cdp_publish] Clearing cookies for {domain}...")
        self._send("Network.enable")
        self._send("Network.clearBrowserCookies")
        # Also clear storage
        self._send("Storage.clearDataForOrigin", {
            "origin": "https://www.xiaohongshu.com",
            "storageTypes": "cookies,local_storage,session_storage",
        })
        self._send("Storage.clearDataForOrigin", {
            "origin": "https://creator.xiaohongshu.com",
            "storageTypes": "cookies,local_storage,session_storage",
        })
        self._clear_login_cache()
        print("[cdp_publish] Cookies and storage cleared.")

    def open_login_page(self):
        """
        Navigate to the Xiaohongshu login page for QR code scanning.

        Used for initial login or after clearing cookies for account switch.
        """
        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(2, minimum_seconds=1.0)
        current_url = self._evaluate("window.location.href")
        if "login" not in current_url.lower():
            # Already logged in, navigate to login page explicitly
            self._navigate("https://creator.xiaohongshu.com/login")
            self._sleep(2, minimum_seconds=1.0)
        self._clear_login_cache()
        print(
            "\n[cdp_publish] Login page is open.\n"
            "  Please scan the QR code in the Chrome window to log in.\n"
        )

    def _capture_clip_png_base64(self, rect: dict[str, Any], padding: int = 8) -> str:
        """Capture a clipped PNG screenshot and return base64 payload."""
        x = max(0.0, float(rect.get("x", 0.0)) - padding)
        y = max(0.0, float(rect.get("y", 0.0)) - padding)
        width = max(1.0, float(rect.get("width", 0.0)) + padding * 2)
        height = max(1.0, float(rect.get("height", 0.0)) + padding * 2)

        self._send("Page.enable")
        result = self._send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "scale": 1,
                },
                "captureBeyondViewport": True,
            },
        )
        image_base64 = result.get("data", "")
        if not isinstance(image_base64, str) or not image_base64:
            raise CDPError("Failed to capture QR code screenshot.")
        return image_base64

    def _locate_login_qrcode(self) -> dict[str, Any]:
        """Return visible QR code metadata from current login page when possible."""
        result = self._evaluate(r"""
            (() => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width >= 24 &&
                    node.getBoundingClientRect().height >= 24
                );
                const selectors = [
                    ".login-container .qrcode-img",
                    ".login-container img",
                    "img.qrcode-img",
                    "img[src*='qrcode']",
                    "[class*='qrcode'] img",
                    "[class*='qr'] img",
                    "[class*='qrcode'] canvas",
                    "[class*='qr'] canvas",
                    ".login-container canvas",
                ];

                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!visible(node)) {
                            continue;
                        }
                        const rect = node.getBoundingClientRect();
                        const src = node instanceof HTMLImageElement ? (node.currentSrc || node.src || "") : "";
                        const dataUrl = node instanceof HTMLCanvasElement ? node.toDataURL("image/png") : "";
                        const parentText = normalize(
                            node.parentElement ? (node.parentElement.innerText || node.parentElement.textContent) : ""
                        );
                        return {
                            ok: true,
                            tag_name: String(node.tagName || "").toLowerCase(),
                            selector,
                            src,
                            data_url: dataUrl,
                            rect: {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                            },
                            hint_text: parentText,
                        };
                    }
                }
                return { ok: false, reason: "qrcode_not_found" };
            })()
        """)
        return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected_result"}

    def get_login_qrcode(self, wait_seconds: float = 20.0) -> dict[str, Any]:
        """Open login page and return QR code image payload for remote display."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(1.5, minimum_seconds=0.6)
        current_url = self._evaluate("window.location.href")
        if isinstance(current_url, str) and "login" not in current_url.lower():
            self._navigate("https://creator.xiaohongshu.com/login")
            self._sleep(1.5, minimum_seconds=0.6)
            current_url = self._evaluate("window.location.href")

        if isinstance(current_url, str) and "login" not in current_url.lower():
            return {
                "logged_in": True,
                "current_url": current_url,
                "qrcode_base64": "",
                "qrcode_data_url": "",
                "mime_type": "image/png",
                "message": "Already logged in.",
            }

        deadline = time.time() + max(3.0, float(wait_seconds))
        qrcode_meta: dict[str, Any] | None = None
        while time.time() < deadline:
            qrcode_meta = self._locate_login_qrcode()
            if qrcode_meta.get("ok"):
                break
            self._sleep(0.6, minimum_seconds=0.2)

        if not qrcode_meta or not qrcode_meta.get("ok"):
            reason = qrcode_meta.get("reason", "qrcode_not_found") if isinstance(qrcode_meta, dict) else "qrcode_not_found"
            raise CDPError(f"Failed to locate login QR code: {reason}")

        data_url = qrcode_meta.get("data_url")
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            header, _, encoded = data_url.partition(",")
            mime_type = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/png"
            image_base64 = encoded
            qrcode_data_url = data_url
        else:
            rect = qrcode_meta.get("rect")
            if not isinstance(rect, dict):
                raise CDPError("QR code rect is missing.")
            image_base64 = self._capture_clip_png_base64(rect)
            mime_type = "image/png"
            qrcode_data_url = f"data:{mime_type};base64,{image_base64}"

        return {
            "logged_in": False,
            "current_url": current_url,
            "qrcode_base64": image_base64,
            "qrcode_data_url": qrcode_data_url,
            "mime_type": mime_type,
            "selector": qrcode_meta.get("selector", ""),
            "tag_name": qrcode_meta.get("tag_name", ""),
            "hint_text": qrcode_meta.get("hint_text", ""),
        }

    # ------------------------------------------------------------------
    # Feed discovery actions
    # ------------------------------------------------------------------

    def _prepare_search_input_keyword(self, keyword: str) -> dict[str, Any]:
        """Focus search input and type keyword without submitting."""
        keyword_literal = json.dumps(keyword, ensure_ascii=False)
        result = self._evaluate(f"""
            (async () => {{
                const keyword = {keyword_literal};
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

                const isVisible = (node) => {{
                    if (!(node instanceof HTMLElement)) {{
                        return false;
                    }}
                    if (node.offsetParent === null) {{
                        return false;
                    }}
                    const rect = node.getBoundingClientRect();
                    return rect.width >= 8 && rect.height >= 8;
                }};

                const selectors = [
                    "#search-input",
                    "input.search-input",
                    "input[type='search']",
                    "input[placeholder*='搜索']",
                    "[class*='search'] input",
                ];

                let inputEl = null;
                for (const selector of selectors) {{
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement)) {{
                            continue;
                        }}
                        if (node.disabled || !isVisible(node)) {{
                            continue;
                        }}
                        inputEl = node;
                        break;
                    }}
                    if (inputEl) {{
                        break;
                    }}
                }}

                if (!inputEl) {{
                    return {{ ok: false, reason: "search_input_not_found" }};
                }}

                const setValue = (value) => {{
                    const proto = inputEl instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
                    if (descriptor && typeof descriptor.set === "function") {{
                        descriptor.set.call(inputEl, value);
                    }} else {{
                        inputEl.value = value;
                    }}
                    inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                    inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));
                }};

                inputEl.focus();
                await sleep(120);
                setValue("");
                await sleep(80);

                let typed = "";
                for (const ch of Array.from(keyword)) {{
                    typed += ch;
                    setValue(typed);
                    await sleep(55 + Math.floor(Math.random() * 70));
                }}
                await sleep(220);
                return {{ ok: true, reason: "" }};
            }})()
        """)
        if not isinstance(result, dict):
            return {"ok": False, "reason": "unexpected_result"}
        reason = result.get("reason")
        return {
            "ok": bool(result.get("ok")),
            "reason": reason if isinstance(reason, str) else "unknown",
        }

    def _extract_recommend_keywords_from_payload(
        self,
        payload: dict[str, Any],
        keyword: str,
        max_suggestions: int,
    ) -> list[str]:
        """Extract recommendation keywords from search recommend API payload."""
        ignored_texts = {
            "历史记录",
            "猜你想搜",
            "相关搜索",
            "热门搜索",
            "大家都在搜",
            "清空历史",
            "删除历史",
        }

        def normalize_text(value: str) -> str:
            return " ".join(value.split()).strip()

        def push_text(output: list[str], seen: set[str], value: str):
            normalized = normalize_text(value)
            if not normalized or normalized == keyword:
                return
            if normalized in ignored_texts:
                return
            if len(normalized) < 2 or len(normalized) > 36:
                return
            if normalized in seen:
                return
            seen.add(normalized)
            output.append(normalized)

        ordered: list[str] = []
        seen: set[str] = set()
        stack: list[Any] = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str):
                        key_lc = key.lower()
                        if any(
                            hint in key_lc
                            for hint in (
                                "word",
                                "query",
                                "keyword",
                                "text",
                                "title",
                                "name",
                                "suggest",
                            )
                        ):
                            push_text(ordered, seen, value)
                        continue
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, str):
                        push_text(ordered, seen, item)
                        continue
                    if isinstance(item, (dict, list)):
                        stack.append(item)

        keyword_prefix = keyword[:2]
        ranked: list[tuple[int, int, str]] = []
        for idx, text in enumerate(ordered):
            score = 0
            if keyword and (keyword in text or text in keyword):
                score += 3
            elif keyword_prefix and keyword_prefix in text:
                score += 1
            ranked.append((score, idx, text))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[: max(1, max_suggestions)]]

    def _capture_search_recommendations_via_network(
        self,
        keyword: str,
        wait_seconds: float = 8.0,
        max_suggestions: int = 12,
    ) -> dict[str, Any]:
        """Capture recommend API response from real page traffic."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._send("Network.enable", {"maxPostDataSize": 65536})
        self._send("Network.setCacheDisabled", {"cacheDisabled": True})

        typed = self._prepare_search_input_keyword(keyword)
        if not typed.get("ok"):
            reason = typed.get("reason") or "type_keyword_failed"
            return {"ok": False, "reason": str(reason), "suggestions": []}

        deadline = time.time() + max(2.0, float(wait_seconds))
        request_meta_by_id: dict[str, dict[str, str]] = {}
        exact_match: tuple[str, str] | None = None
        fallback_match: tuple[str, str] | None = None

        while time.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv(timeout=timeout)
            except TimeoutError:
                continue

            message = json.loads(raw)
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                request = params.get("request", {})
                if isinstance(request_id, str):
                    request_meta_by_id[request_id] = {
                        "url": request.get("url", ""),
                        "method": str(request.get("method", "")).upper(),
                    }
                continue

            if method != "Network.responseReceived":
                continue

            request_id = params.get("requestId")
            if not isinstance(request_id, str):
                continue

            request_meta = request_meta_by_id.get(request_id, {})
            request_url = request_meta.get("url", "")
            if XHS_SEARCH_RECOMMEND_API_PATH not in request_url:
                continue
            if request_meta.get("method") == "OPTIONS":
                continue

            status = int(params.get("response", {}).get("status") or 0)
            if status != 200:
                continue

            fallback_match = (request_id, request_url)
            try:
                query = parse_qs(urlparse(request_url).query)
                request_keyword = (query.get("keyword") or [""])[0].strip()
            except Exception:
                request_keyword = ""

            if request_keyword == keyword:
                exact_match = (request_id, request_url)
                break

        target = exact_match or fallback_match
        if not target:
            return {"ok": False, "reason": "recommend_request_timeout", "suggestions": []}

        request_id, request_url = target
        body_result = self._send("Network.getResponseBody", {"requestId": request_id})
        body_text = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body_text = base64.b64decode(body_text).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "recommend_invalid_json", "suggestions": []}
        if not isinstance(payload, dict):
            return {"ok": False, "reason": "recommend_invalid_payload", "suggestions": []}

        suggestions = self._extract_recommend_keywords_from_payload(
            payload=payload,
            keyword=keyword,
            max_suggestions=max_suggestions,
        )
        return {
            "ok": True,
            "reason": "",
            "request_url": request_url,
            "suggestions": suggestions,
        }

    def search_feeds(
        self,
        keyword: str,
        filters: SearchFilters | None = None,
    ) -> dict[str, Any]:
        """
        Search Xiaohongshu feeds by keyword and optional filters.

        Returns:
            {
                "keyword": str,
                "recommended_keywords": list[str],  # dropdown related terms
                "feeds": list[dict[str, Any]],      # extracted from __INITIAL_STATE__
            }
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        keyword = keyword.strip()
        if not keyword:
            raise CDPError("Keyword cannot be empty.")

        self._navigate(SEARCH_BASE_URL)
        self._sleep(2, minimum_seconds=1.0)

        explorer = FeedExplorer(
            self._evaluate,
            self._sleep,
            move_mouse=self._move_mouse,
            click_mouse=self._click_mouse,
        )

        recommendation_result = self._capture_search_recommendations_via_network(keyword=keyword)
        recommended_keywords = recommendation_result.get("suggestions", [])

        if not recommendation_result.get("ok"):
            reason = recommendation_result.get("reason") or "recommend_api_failed"
            print(
                "[cdp_publish] Warning: failed to capture search recommendations via API. "
                f"reason={reason}"
            )

        # Always navigate with keyword URL to keep feed extraction stable.
        search_url = make_search_url(keyword)
        self._navigate(search_url)
        self._sleep(2, minimum_seconds=1.0)

        try:
            feeds = explorer.search_feeds(keyword=keyword, filters=filters)
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        print(
            f"[cdp_publish] Search completed. keyword={keyword}, "
            f"recommended_keywords={len(recommended_keywords)}, feeds={len(feeds)}"
        )
        return {
            "keyword": keyword,
            "recommended_keywords": recommended_keywords,
            "feeds": feeds,
        }

    def list_feeds(self) -> dict[str, Any]:
        """Get home recommendation feed list from current logged-in home page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._navigate(XHS_HOME_URL)
        self._sleep(2, minimum_seconds=1.0)

        explorer = FeedExplorer(self._evaluate, self._sleep)
        try:
            feeds = explorer.list_feeds()
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        print(f"[cdp_publish] Home feeds loaded. count={len(feeds)}")
        return {
            "count": len(feeds),
            "feeds": feeds,
        }

    def _extract_feed_comments_state(self) -> dict[str, Any]:
        """Read current comment loading state from feed detail page DOM."""
        result = self._evaluate(r"""
            (() => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );

                const countVisible = (selector) => {
                    const nodes = document.querySelectorAll(selector);
                    let count = 0;
                    for (const node of nodes) {
                        if (visible(node)) {
                            count += 1;
                        }
                    }
                    return count;
                };

                let parentCommentCount = 0;
                const parentSelectors = [
                    ".parent-comment",
                    "[class*='parent-comment']",
                    ".comments-container [class*='comment-item']",
                ];
                for (const selector of parentSelectors) {
                    parentCommentCount = countVisible(selector);
                    if (parentCommentCount > 0) {
                        break;
                    }
                }

                let totalComments = 0;
                const totalSelectors = [
                    ".comments-container .total",
                    ".comments-container [class*='total']",
                ];
                for (const selector of totalSelectors) {
                    const node = document.querySelector(selector);
                    if (!(node instanceof HTMLElement)) {
                        continue;
                    }
                    const text = normalize(node.innerText || node.textContent);
                    const match = text.match(/共\s*(\d+)\s*条评论/);
                    if (match) {
                        totalComments = Number.parseInt(match[1], 10) || 0;
                        break;
                    }
                }

                const noCommentSelectors = [
                    ".no-comments-text",
                    "[class*='no-comments']",
                    "[class*='empty']",
                ];
                let noComments = false;
                for (const selector of noCommentSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!visible(node)) {
                            continue;
                        }
                        const text = normalize(node.innerText || node.textContent);
                        if (text.includes("这是一片荒地")) {
                            noComments = true;
                            break;
                        }
                    }
                    if (noComments) {
                        break;
                    }
                }

                const endSelectors = [
                    ".end-container",
                    "[class*='end-container']",
                ];
                let endDetected = false;
                let endText = "";
                for (const selector of endSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!visible(node)) {
                            continue;
                        }
                        const text = normalize(node.innerText || node.textContent).toUpperCase();
                        if (text.includes("THE END") || text.includes("THEEND")) {
                            endDetected = true;
                            endText = text;
                            break;
                        }
                    }
                    if (endDetected) {
                        break;
                    }
                }

                return {
                    parent_comment_count: parentCommentCount,
                    total_comments: totalComments,
                    no_comments: noComments,
                    end_detected: endDetected,
                    end_text: endText,
                    scroll_top: window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0,
                };
            })()
        """)
        return result if isinstance(result, dict) else {
            "parent_comment_count": 0,
            "total_comments": 0,
            "no_comments": False,
            "end_detected": False,
            "end_text": "",
            "scroll_top": 0,
        }

    def _scroll_feed_comments_area(
        self,
        speed: str = "normal",
        large_mode: bool = False,
        push_count: int = 1,
    ):
        """Scroll feed detail comments area to trigger lazy loading."""
        speed_key = (speed or "normal").strip().lower()
        delta_map = {
            "slow": 260,
            "normal": 520,
            "fast": 860,
        }
        delta = delta_map.get(speed_key, delta_map["normal"])
        if large_mode:
            delta = int(delta * 1.9)
        push_count = max(1, int(push_count))

        for _ in range(push_count):
            self._evaluate(f"""
                (() => {{
                    const commentRoot = document.querySelector('.comments-container');
                    if (commentRoot instanceof HTMLElement) {{
                        try {{
                            commentRoot.scrollIntoView({{ behavior: 'instant', block: 'start' }});
                        }} catch (error) {{}}
                    }}

                    const parents = document.querySelectorAll('.parent-comment, [class*="parent-comment"]');
                    if (parents.length) {{
                        const last = parents[parents.length - 1];
                        if (last instanceof HTMLElement) {{
                            try {{
                                last.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                            }} catch (error) {{}}
                        }}
                    }}

                    const target = document.querySelector('.note-scroller')
                        || document.querySelector('.interaction-container')
                        || document.querySelector('.comments-container')
                        || document.documentElement;

                    const event = new WheelEvent('wheel', {{
                        deltaY: {delta},
                        deltaMode: 0,
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }});
                    target.dispatchEvent(event);
                    window.scrollBy(0, {delta});
                    return true;
                }})()
            """)
            self._sleep(0.45 if speed_key == "fast" else 0.75 if speed_key == "normal" else 1.05, minimum_seconds=0.15)

    def _click_more_reply_buttons(
        self,
        reply_limit: int = 10,
        max_clicks: int = 6,
    ) -> dict[str, int]:
        """Click visible 'more replies' buttons on feed detail page."""
        threshold = max(0, int(reply_limit))
        max_clicks = max(1, int(max_clicks))
        result = self._evaluate(rf"""
            (() => {{
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );
                const root = document.querySelector('.comments-container') || document.body;
                const selectors = [
                    '.show-more',
                    '[class*="show-more"]',
                    'button',
                    '[role="button"]',
                    'span',
                    'div',
                    'a',
                ];
                const seen = new Set();
                let clicked = 0;
                let skipped = 0;
                const replyRegex = /展开\s*(\d+)\s*条回复/;

                for (const selector of selectors) {{
                    const nodes = root.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (clicked >= {max_clicks}) {{
                            return {{ clicked, skipped }};
                        }}
                        if (!visible(node)) {{
                            continue;
                        }}
                        const text = normalize(node.textContent || node.innerText);
                        if (!text) {{
                            continue;
                        }}
                        const looksLikeReplyExpand = (
                            text.includes('回复') &&
                            (text.includes('展开') || text.includes('更多') || text.includes('查看'))
                        );
                        if (!looksLikeReplyExpand) {{
                            continue;
                        }}
                        const rect = node.getBoundingClientRect();
                        const key = `${{Math.round(rect.x)}}:${{Math.round(rect.y)}}:${{text}}`;
                        if (seen.has(key)) {{
                            continue;
                        }}
                        seen.add(key);

                        const match = text.match(replyRegex);
                        if ({threshold} > 0 && match && Number.parseInt(match[1], 10) > {threshold}) {{
                            skipped += 1;
                            continue;
                        }}

                        try {{
                            node.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                        }} catch (error) {{}}
                        node.click();
                        clicked += 1;
                    }}
                }}
                return {{ clicked, skipped }};
            }})()
        """)
        if not isinstance(result, dict):
            return {"clicked": 0, "skipped": 0}
        return {
            "clicked": int(result.get("clicked", 0) or 0),
            "skipped": int(result.get("skipped", 0) or 0),
        }

    def _load_feed_detail_comments(
        self,
        limit: int = 20,
        click_more_replies: bool = False,
        reply_limit: int = 10,
        scroll_speed: str = "normal",
    ) -> dict[str, Any]:
        """Scroll and optionally expand replies to load more comments into page state."""
        target_limit = max(1, int(limit))
        speed = (scroll_speed or "normal").strip().lower()
        if speed not in {"slow", "normal", "fast"}:
            speed = "normal"

        self._evaluate("""
            (() => {
                const root = document.querySelector('.comments-container');
                if (root instanceof HTMLElement) {
                    try {
                        root.scrollIntoView({ behavior: 'instant', block: 'start' });
                    } catch (error) {}
                }
                return true;
            })()
        """)
        self._sleep(0.8, minimum_seconds=0.25)

        initial_state = self._extract_feed_comments_state()
        if initial_state.get("no_comments"):
            return {
                "attempts": 0,
                "target_limit": target_limit,
                "loaded_parent_comments": 0,
                "total_comments": int(initial_state.get("total_comments", 0) or 0),
                "clicked_more_replies": 0,
                "skipped_more_replies": 0,
                "end_detected": bool(initial_state.get("end_detected")),
                "no_comments": True,
                "scroll_speed": speed,
            }

        last_count = int(initial_state.get("parent_comment_count", 0) or 0)
        stagnant_checks = 0
        clicked_total = 0
        skipped_total = 0
        attempts = 0
        max_attempts = max(10, target_limit * 3)

        while attempts < max_attempts:
            attempts += 1
            state = self._extract_feed_comments_state()
            current_count = int(state.get("parent_comment_count", 0) or 0)
            total_comments = int(state.get("total_comments", 0) or 0)
            end_detected = bool(state.get("end_detected"))
            if end_detected or current_count >= target_limit:
                break

            if click_more_replies and attempts % 2 == 1:
                click_result = self._click_more_reply_buttons(reply_limit=reply_limit)
                clicked_total += click_result["clicked"]
                skipped_total += click_result["skipped"]
                if click_result["clicked"] > 0:
                    self._sleep(0.9, minimum_seconds=0.25)
                    click_result_round2 = self._click_more_reply_buttons(reply_limit=reply_limit)
                    clicked_total += click_result_round2["clicked"]
                    skipped_total += click_result_round2["skipped"]
                    if click_result_round2["clicked"] > 0:
                        self._sleep(0.7, minimum_seconds=0.2)

            large_mode = stagnant_checks >= 3
            push_count = 3 if large_mode else 1
            self._scroll_feed_comments_area(speed=speed, large_mode=large_mode, push_count=push_count)
            state_after = self._extract_feed_comments_state()
            updated_count = int(state_after.get("parent_comment_count", 0) or 0)
            if updated_count > last_count:
                last_count = updated_count
                stagnant_checks = 0
            else:
                stagnant_checks += 1
                if stagnant_checks >= 6:
                    self._scroll_feed_comments_area(speed=speed, large_mode=True, push_count=6)
                    self._sleep(1.0, minimum_seconds=0.25)
                    stagnant_checks = 0

            if bool(state_after.get("end_detected")) or updated_count >= target_limit:
                state = state_after
                current_count = updated_count
                total_comments = int(state_after.get("total_comments", 0) or 0)
                end_detected = bool(state_after.get("end_detected"))
                break

        final_state = self._extract_feed_comments_state()
        return {
            "attempts": attempts,
            "target_limit": target_limit,
            "loaded_parent_comments": int(final_state.get("parent_comment_count", 0) or 0),
            "total_comments": int(final_state.get("total_comments", 0) or 0),
            "clicked_more_replies": clicked_total,
            "skipped_more_replies": skipped_total,
            "end_detected": bool(final_state.get("end_detected")),
            "no_comments": bool(final_state.get("no_comments")),
            "scroll_speed": speed,
        }

    def get_feed_detail(
        self,
        feed_id: str,
        xsec_token: str,
        load_all_comments: bool = False,
        limit: int = 20,
        click_more_replies: bool = False,
        reply_limit: int = 10,
        scroll_speed: str = "normal",
    ) -> dict[str, Any]:
        """
        Get feed detail from note page initial state.

        Returns a payload containing the detail object and optional comment loading summary.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        detail_url = make_feed_detail_url(feed_id, xsec_token)
        self._navigate(detail_url)
        self._sleep(2, minimum_seconds=1.0)
        self._check_feed_page_accessible()

        comment_loading = None
        if load_all_comments:
            comment_loading = self._load_feed_detail_comments(
                limit=limit,
                click_more_replies=click_more_replies,
                reply_limit=reply_limit,
                scroll_speed=scroll_speed,
            )

        explorer = FeedExplorer(self._evaluate, self._sleep)
        try:
            detail = explorer.get_feed_detail(feed_id=feed_id)
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        print(f"[cdp_publish] Feed detail loaded. feed_id={feed_id}")
        return {
            "detail": detail,
            "comment_loading": comment_loading,
        }

    def _resolve_profile_url(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Resolve user profile URL from explicit URL or user_id."""
        if isinstance(profile_url, str) and profile_url.strip():
            return profile_url.strip()
        if isinstance(user_id, str) and user_id.strip():
            return f"https://www.xiaohongshu.com/user/profile/{user_id.strip()}"
        raise CDPError("Either --profile-url or --user-id is required.")

    def get_profile_snapshot(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a user profile snapshot from profile page state + DOM."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        target_url = self._resolve_profile_url(profile_url=profile_url, user_id=user_id)
        self._navigate(target_url)
        self._sleep(2.0, minimum_seconds=0.8)

        snapshot = self._evaluate("""
            (() => {
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const state = window.__INITIAL_STATE__ || {};

                const getByKeys = (obj, keys) => {
                    if (!obj || typeof obj !== "object") {
                        return null;
                    }
                    for (const key of keys) {
                        const value = obj[key];
                        if (value !== undefined && value !== null && String(value).trim()) {
                            return value;
                        }
                    }
                    return null;
                };

                const queue = [state];
                const seen = new Set();
                let userNode = null;
                let scanCount = 0;

                while (queue.length && scanCount < 2400) {
                    scanCount += 1;
                    const node = queue.shift();
                    if (!node || typeof node !== "object") {
                        continue;
                    }
                    if (seen.has(node)) {
                        continue;
                    }
                    seen.add(node);

                    if (!Array.isArray(node)) {
                        const idVal = getByKeys(node, [
                            "userId", "user_id", "userid", "uid", "redId", "red_id",
                        ]);
                        const nameVal = getByKeys(node, [
                            "nickname", "nickName", "name", "userName", "username",
                        ]);
                        const avatarVal = getByKeys(node, [
                            "avatar", "avatarUrl", "headUrl", "image", "images",
                        ]);
                        if (nameVal && (idVal || avatarVal)) {
                            userNode = node;
                            break;
                        }
                    }

                    if (Array.isArray(node)) {
                        for (const item of node) {
                            if (item && typeof item === "object") {
                                queue.push(item);
                            }
                        }
                        continue;
                    }

                    for (const key of Object.keys(node).slice(0, 120)) {
                        const value = node[key];
                        if (value && typeof value === "object") {
                            queue.push(value);
                        }
                    }
                }

                const nameNode = document.querySelector(
                    "h1, [class*='name'], [class*='nickname'], [class*='user-name']"
                );
                const bioNode = document.querySelector(
                    "[class*='desc'], [class*='bio'], [class*='signature'], [class*='intro']"
                );
                const avatarNode = document.querySelector(
                    "img[src*='avatar'], [class*='avatar'] img, img[alt*='头像']"
                );
                const statNodes = document.querySelectorAll(
                    "[class*='fans'], [class*='follow'], [class*='like'], [class*='count']"
                );
                const statTexts = [];
                for (const node of statNodes) {
                    if (!(node instanceof HTMLElement) || node.offsetParent === null) {
                        continue;
                    }
                    const text = normalize(node.innerText || node.textContent);
                    if (text && text.length <= 40) {
                        statTexts.push(text);
                    }
                }

                return {
                    url: window.location.href,
                    page_title: document.title || "",
                    profile: {
                        user_id: getByKeys(userNode, [
                            "userId", "user_id", "userid", "uid", "redId", "red_id",
                        ]),
                        nickname: getByKeys(userNode, [
                            "nickname", "nickName", "name", "userName", "username",
                        ]) || normalize(nameNode ? nameNode.textContent : ""),
                        avatar: getByKeys(userNode, [
                            "avatar", "avatarUrl", "headUrl", "image", "images",
                        ]) || (avatarNode instanceof HTMLImageElement ? avatarNode.src : ""),
                        desc: getByKeys(userNode, [
                            "desc", "description", "bio", "signature", "introduction",
                        ]) || normalize(bioNode ? bioNode.textContent : ""),
                        followers: getByKeys(userNode, [
                            "fans", "fansCount", "followerCount", "followers", "fans_count",
                        ]),
                        following: getByKeys(userNode, [
                            "follows", "followCount", "followingCount", "following",
                        ]),
                        liked: getByKeys(userNode, [
                            "likes", "likedCount", "totalLikes", "likeCount", "like_count",
                        ]),
                    },
                    dom_stat_texts: Array.from(new Set(statTexts)).slice(0, 12),
                };
            })()
        """)
        if not isinstance(snapshot, dict):
            raise CDPError("Could not extract profile snapshot from current page.")
        return snapshot

    def _extract_note_cards_from_profile_dom(self, limit: int) -> dict[str, Any]:
        """Extract note cards from current profile page DOM."""
        safe_limit = max(1, int(limit))
        script = """
            (() => {
                const limit = __LIMIT__;
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const toAbs = (href) => {
                    try {
                        return new URL(href, window.location.href).href;
                    } catch (error) {
                        return "";
                    }
                };
                const parseLink = (href) => {
                    const abs = toAbs(href);
                    if (!abs) {
                        return null;
                    }
                    let parsed;
                    try {
                        parsed = new URL(abs);
                    } catch (error) {
                        return null;
                    }
                    const match = parsed.pathname.match(
                        /\\/(?:explore|discovery\\/item)\\/([0-9a-zA-Z]{24})/
                    );
                    if (!match) {
                        return null;
                    }
                    return {
                        id: match[1],
                        xsec_token: parsed.searchParams.get("xsec_token") || "",
                        url: parsed.toString(),
                    };
                };

                const selectorList = [
                    "a[href*='/explore/']",
                    "a[href*='/discovery/item/']",
                ];
                const links = document.querySelectorAll(selectorList.join(","));
                const seen = new Set();
                const notes = [];

                for (const link of links) {
                    if (!(link instanceof HTMLAnchorElement)) {
                        continue;
                    }
                    const parsed = parseLink(link.getAttribute("href") || link.href || "");
                    if (!parsed) {
                        continue;
                    }
                    if (seen.has(parsed.id)) {
                        continue;
                    }
                    seen.add(parsed.id);

                    const card = link.closest(
                        "[class*='note-item'], [class*='card'], [class*='cover'], li, article, div"
                    ) || link;
                    const titleNode = card.querySelector(
                        "[class*='title'], [class*='name'], h3, h2, img[alt]"
                    );
                    const coverNode = card.querySelector("img");
                    const titleText = normalize(
                        (titleNode && (titleNode.getAttribute("alt") || titleNode.textContent)) ||
                        link.getAttribute("title") ||
                        link.textContent
                    );
                    const cover = coverNode instanceof HTMLImageElement ? coverNode.src : "";

                    notes.push({
                        id: parsed.id,
                        xsec_token: parsed.xsec_token,
                        note_url: parsed.url,
                        title: titleText,
                        cover,
                    });
                    if (notes.length >= limit) {
                        break;
                    }
                }

                return {
                    ok: true,
                    notes,
                    count: notes.length,
                    page_url: window.location.href,
                };
            })()
        """
        result = self._evaluate(script.replace("__LIMIT__", str(safe_limit)))

        if not isinstance(result, dict):
            return {"ok": False, "reason": "invalid_dom_payload", "notes": []}
        return result

    def list_profile_notes(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        max_scrolls: int = 3,
    ) -> dict[str, Any]:
        """List notes from a user profile page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        safe_limit = max(1, min(100, int(limit)))
        safe_scrolls = max(0, min(12, int(max_scrolls)))
        target_url = self._resolve_profile_url(profile_url=profile_url, user_id=user_id)

        self._navigate(target_url)
        self._sleep(2.0, minimum_seconds=0.8)

        best_notes: list[dict[str, Any]] = []
        page_url = target_url

        for _ in range(safe_scrolls + 1):
            extracted = self._extract_note_cards_from_profile_dom(limit=safe_limit)
            notes = extracted.get("notes", []) if isinstance(extracted, dict) else []
            if isinstance(extracted, dict) and extracted.get("page_url"):
                page_url = str(extracted["page_url"])
            if isinstance(notes, list) and len(notes) > len(best_notes):
                best_notes = notes
            if len(best_notes) >= safe_limit:
                break
            self._evaluate("window.scrollTo(0, document.body.scrollHeight); true;")
            self._sleep(1.2, minimum_seconds=0.4)

        return {
            "profile_url": page_url,
            "count": len(best_notes),
            "limit": safe_limit,
            "notes": best_notes[:safe_limit],
        }

    def _activate_reply_target_for_comment(
        self,
        comment_id: str | None = None,
        comment_author: str | None = None,
        comment_snippet: str | None = None,
    ) -> dict[str, Any]:
        """Find a comment target and click its reply control."""
        id_literal = json.dumps((comment_id or "").strip(), ensure_ascii=False)
        author_literal = json.dumps((comment_author or "").strip(), ensure_ascii=False)
        snippet_literal = json.dumps((comment_snippet or "").strip(), ensure_ascii=False)

        script = """
            (() => {
                const targetId = __TARGET_ID__;
                const targetAuthor = __TARGET_AUTHOR__;
                const targetSnippet = __TARGET_SNIPPET__;
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );
                const extractCommentId = (node) => {
                    if (!(node instanceof HTMLElement)) {
                        return "";
                    }
                    const attrs = [
                        "data-comment-id",
                        "data-id",
                        "comment-id",
                        "id",
                    ];
                    for (const key of attrs) {
                        const value = node.getAttribute(key);
                        if (value && normalize(value)) {
                            return normalize(value);
                        }
                    }
                    if (node.dataset) {
                        const values = [
                            node.dataset.commentId,
                            node.dataset.id,
                            node.dataset.commentid,
                        ];
                        for (const value of values) {
                            if (value && normalize(value)) {
                                return normalize(value);
                            }
                        }
                    }
                    return "";
                };
                const findReplyControl = (container) => {
                    const selectors = [
                        "button",
                        "[role='button']",
                        "a",
                        "span",
                        "div",
                    ];
                    for (const selector of selectors) {
                        const nodes = container.querySelectorAll(selector);
                        for (const node of nodes) {
                            if (!visible(node)) {
                                continue;
                            }
                            const text = normalize(node.textContent || node.innerText);
                            if (!text) {
                                continue;
                            }
                            if (
                                text === "回复" ||
                                text.startsWith("回复") ||
                                text === "Reply" ||
                                text.startsWith("Reply")
                            ) {
                                return node;
                            }
                        }
                    }
                    return null;
                };

                const containers = [];
                const containerSelectors = [
                    "[class*='comment-item']",
                    "li[class*='comment']",
                    "div[class*='comment']",
                    "article[class*='comment']",
                ];
                for (const selector of containerSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!(node instanceof HTMLElement) || !visible(node)) {
                            continue;
                        }
                        containers.push(node);
                    }
                }
                if (!containers.length) {
                    return { ok: false, reason: "comment_not_found" };
                }

                let best = null;
                for (let idx = 0; idx < containers.length; idx++) {
                    const container = containers[idx];
                    const id = extractCommentId(container);
                    const authorNode = container.querySelector(
                        "[class*='author'], [class*='name'], [class*='user']"
                    );
                    const author = normalize(authorNode ? authorNode.textContent : "");
                    const text = normalize(container.innerText || container.textContent);
                    let score = 0;
                    if (!targetId && !targetAuthor && !targetSnippet) {
                        score = 1;
                    }
                    if (targetId && id && id === targetId) {
                        score += 100;
                    }
                    if (targetAuthor && author && author.includes(targetAuthor)) {
                        score += 30;
                    }
                    if (targetSnippet && text && text.includes(targetSnippet)) {
                        score += 20;
                    }
                    if (!best || score > best.score) {
                        best = {
                            score,
                            index: idx,
                            id,
                            author,
                            text_preview: text.slice(0, 160),
                            container,
                        };
                    }
                }

                if (!best || best.score <= 0) {
                    return { ok: false, reason: "target_comment_not_matched" };
                }

                const replyControl = findReplyControl(best.container);
                if (!replyControl) {
                    return {
                        ok: false,
                        reason: "reply_button_not_found",
                        matched_comment_id: best.id,
                        matched_author: best.author,
                    };
                }
                replyControl.click();
                return {
                    ok: true,
                    matched_comment_id: best.id,
                    matched_author: best.author,
                    matched_text_preview: best.text_preview,
                };
            })()
        """
        result = self._evaluate(
            script
            .replace("__TARGET_ID__", id_literal)
            .replace("__TARGET_AUTHOR__", author_literal)
            .replace("__TARGET_SNIPPET__", snippet_literal)
        )
        if not isinstance(result, dict):
            return {"ok": False, "reason": "unexpected_reply_target_result"}
        return result

    def respond_comment(
        self,
        feed_id: str,
        xsec_token: str,
        content: str,
        comment_id: str | None = None,
        comment_author: str | None = None,
        comment_snippet: str | None = None,
    ) -> dict[str, Any]:
        """Reply to an existing comment on a feed detail page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        content = content.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")
        if not content:
            raise CDPError("content cannot be empty.")

        detail_url = make_feed_detail_url(feed_id, xsec_token)
        self._navigate(detail_url)
        self._sleep(2, minimum_seconds=1.0)
        self._check_feed_page_accessible()

        target_result = self._activate_reply_target_for_comment(
            comment_id=comment_id,
            comment_author=comment_author,
            comment_snippet=comment_snippet,
        )
        if not target_result.get("ok"):
            raise CDPError(
                "Failed to locate reply target comment: "
                f"{target_result.get('reason', 'unknown')}"
            )

        self._sleep(0.6, minimum_seconds=0.2)
        filled_len = self._fill_comment_content(content)
        self._sleep(0.6, minimum_seconds=0.2)

        submit_rect_js = """
            (function() {
                const selectors = [
                    "div.bottom button.submit",
                    "div.bottom button[class*='submit']",
                    "button.submit",
                    "button[class*='submit']",
                    "button[type='submit']",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLButtonElement) || el.offsetParent === null) {
                        continue;
                    }
                    if (el.disabled) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                const fallbackTexts = new Set(["发送", "提交", "评论", "回复"]);
                const buttons = document.querySelectorAll("button");
                for (const button of buttons) {
                    if (!(button instanceof HTMLButtonElement) || button.offsetParent === null) {
                        continue;
                    }
                    if (button.disabled) {
                        continue;
                    }
                    const text = (button.textContent || "").replace(/\\s+/g, " ").trim();
                    if (!fallbackTexts.has(text)) {
                        continue;
                    }
                    const r = button.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        self._click_element_by_cdp("comment reply submit button", submit_rect_js)
        self._sleep(1.0, minimum_seconds=0.4)

        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "content_length": filled_len,
            "matched_comment_id": target_result.get("matched_comment_id", ""),
            "matched_author": target_result.get("matched_author", ""),
            "matched_text_preview": target_result.get("matched_text_preview", ""),
            "success": True,
        }

    def _set_note_toggle_state(
        self,
        selectors: list[str],
        desired_active: bool,
        active_class_keywords: list[str],
        active_text_keywords: list[str],
    ) -> dict[str, Any]:
        """Toggle a note action button to desired active/inactive state."""
        selectors_literal = json.dumps(selectors, ensure_ascii=False)
        desired_literal = "true" if desired_active else "false"
        class_keywords_literal = json.dumps(active_class_keywords, ensure_ascii=False)
        text_keywords_literal = json.dumps(active_text_keywords, ensure_ascii=False)

        script = """
            (async () => {
                const selectors = __SELECTORS__;
                const desired = __DESIRED__;
                const activeClassKeywords = __CLASS_KEYWORDS__;
                const activeTextKeywords = __TEXT_KEYWORDS__;
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim().toLowerCase();
                const isVisible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );
                const isActive = (node) => {
                    if (!(node instanceof HTMLElement)) {
                        return false;
                    }
                    const classText = normalize(node.className || "");
                    if (activeClassKeywords.some((kw) => classText.includes(String(kw).toLowerCase()))) {
                        return true;
                    }
                    const ariaPressed = normalize(node.getAttribute("aria-pressed"));
                    if (ariaPressed === "true") {
                        return true;
                    }
                    const dataState = normalize(node.getAttribute("data-state"));
                    if (dataState && ["active", "on", "selected", "checked", "true"].includes(dataState)) {
                        return true;
                    }
                    const text = normalize(node.innerText || node.textContent || "");
                    return activeTextKeywords.some((kw) => text.includes(normalize(String(kw))));
                };
                const resolveClickable = (node) => {
                    if (!(node instanceof HTMLElement)) {
                        return null;
                    }
                    // Avoid promoting to arbitrary container <div>, which often
                    // causes "click happened but state unchanged" on XHS pages.
                    return node.closest("button, [role='button'], a, [tabindex]") || node;
                };
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

                const candidates = [];
                const seen = new Set();
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const raw of nodes) {
                        const clickable = resolveClickable(raw);
                        if (!(clickable instanceof HTMLElement) || !isVisible(clickable)) {
                            continue;
                        }
                        if (seen.has(clickable)) {
                            continue;
                        }
                        seen.add(clickable);
                        candidates.push(clickable);
                    }
                }
                if (!candidates.length) {
                    return { ok: false, reason: "action_button_not_found" };
                }

                for (const target of candidates) {
                    const before = isActive(target);
                    if (before === desired) {
                        return {
                            ok: true,
                            changed: false,
                            state_before: before,
                            state_after: before,
                        };
                    }

                    target.click();
                    await sleep(320);
                    const after = isActive(target);
                    if (after === desired) {
                        return {
                            ok: true,
                            changed: true,
                            state_before: before,
                            state_after: after,
                        };
                    }
                }
                return { ok: false, reason: "action_state_not_reached" };
            })()
        """
        result = self._evaluate(
            script
            .replace("__SELECTORS__", selectors_literal)
            .replace("__DESIRED__", desired_literal)
            .replace("__CLASS_KEYWORDS__", class_keywords_literal)
            .replace("__TEXT_KEYWORDS__", text_keywords_literal)
        )
        if not isinstance(result, dict) or not result.get("ok"):
            reason = "unknown"
            if isinstance(result, dict):
                reason = str(result.get("reason", reason))
            raise CDPError(f"Failed to set note action state: {reason}")
        return result

    def set_note_upvote_state(
        self,
        feed_id: str,
        xsec_token: str,
        upvoted: bool,
    ) -> dict[str, Any]:
        """Set note upvote (like) state."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        detail_url = make_feed_detail_url(feed_id, xsec_token)
        self._navigate(detail_url)
        self._sleep(2, minimum_seconds=1.0)
        self._check_feed_page_accessible()

        result = self._set_note_toggle_state(
            selectors=[
                ".like-button",
                "button[aria-label*='like']",
                "button[aria-label*='赞']",
                "[class*='like']",
                "[class*='heart']",
                "[data-testid*='like']",
                "[data-testid*='heart']",
            ],
            desired_active=upvoted,
            active_class_keywords=["liked", "active", "on", "selected"],
            active_text_keywords=["已赞", "取消赞"],
        )
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "target_state": "upvoted" if upvoted else "not_upvoted",
            "changed": bool(result.get("changed")),
            "state_before": bool(result.get("state_before")),
            "state_after": bool(result.get("state_after")),
            "success": True,
        }

    def set_note_bookmark_state(
        self,
        feed_id: str,
        xsec_token: str,
        bookmarked: bool,
    ) -> dict[str, Any]:
        """Set note bookmark (favorite/collect) state."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        detail_url = make_feed_detail_url(feed_id, xsec_token)
        self._navigate(detail_url)
        self._sleep(2, minimum_seconds=1.0)
        self._check_feed_page_accessible()

        result = self._set_note_toggle_state(
            selectors=[
                ".collect-button",
                "button[aria-label*='collect']",
                "button[aria-label*='收藏']",
                "[class*='collect']",
                "[class*='bookmark']",
                "[data-testid*='collect']",
                "[data-testid*='bookmark']",
            ],
            desired_active=bookmarked,
            active_class_keywords=["collected", "bookmarked", "active", "on", "selected"],
            active_text_keywords=["已收藏", "取消收藏"],
        )
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "target_state": "bookmarked" if bookmarked else "not_bookmarked",
            "changed": bool(result.get("changed")),
            "state_before": bool(result.get("state_before")),
            "state_after": bool(result.get("state_after")),
            "success": True,
        }

    def _check_feed_page_accessible(self):
        """
        Check whether the currently opened feed detail page is accessible.

        Raises:
            CDPError: If page is inaccessible due to privacy/deletion/violation.
        """
        keyword_list_literal = json.dumps(
            list(XHS_FEED_INACCESSIBLE_KEYWORDS),
            ensure_ascii=False,
        )
        issue = self._evaluate(f"""
            (() => {{
                const wrappers = document.querySelectorAll(
                    ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"
                );
                if (!wrappers.length) {{
                    return "";
                }}

                let text = "";
                for (const el of wrappers) {{
                    const chunk = (el.innerText || el.textContent || "").trim();
                    if (chunk) {{
                        text += (text ? " " : "") + chunk;
                    }}
                }}
                const fullText = text.trim();
                if (!fullText) {{
                    return "";
                }}

                const keywords = {keyword_list_literal};
                for (const kw of keywords) {{
                    if (fullText.includes(kw)) {{
                        return kw;
                    }}
                }}
                return fullText.slice(0, 180);
            }})()
        """)
        if isinstance(issue, str) and issue.strip():
            raise CDPError(f"Feed page is not accessible: {issue.strip()}")

    def _fill_comment_content(self, content: str) -> int:
        """
        Fill comment content into feed detail page input.

        Returns:
            Filled character length.
        """
        content_literal = json.dumps(content, ensure_ascii=False)
        result = self._evaluate(f"""
            (() => {{
                const commentText = {content_literal};
                const candidates = [
                    "div.input-box div.content-edit p.content-input",
                    "div.input-box div.content-edit [contenteditable='true']",
                    "div.input-box .content-input",
                    "p.content-input",
                    "[class*='content-edit'] [contenteditable='true']",
                ];

                let inputEl = null;
                for (const selector of candidates) {{
                    const node = document.querySelector(selector);
                    if (!(node instanceof HTMLElement)) {{
                        continue;
                    }}
                    if (node.offsetParent === null) {{
                        continue;
                    }}
                    inputEl = node;
                    break;
                }}

                if (!inputEl) {{
                    return {{ ok: false, reason: "comment_input_not_found" }};
                }}

                inputEl.focus();

                if (inputEl instanceof HTMLInputElement || inputEl instanceof HTMLTextAreaElement) {{
                    inputEl.value = commentText;
                    inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                    inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));
                    return {{
                        ok: true,
                        length: inputEl.value.trim().length,
                    }};
                }}

                const asEditable = inputEl;
                if (!asEditable.isContentEditable && asEditable.tagName.toLowerCase() !== "p") {{
                    const nested = asEditable.querySelector("[contenteditable='true'], p.content-input");
                    if (nested instanceof HTMLElement) {{
                        nested.focus();
                        inputEl = nested;
                    }}
                }}

                if (inputEl.tagName.toLowerCase() === "p") {{
                    inputEl.textContent = commentText;
                }} else {{
                    const lines = commentText.split("\\n");
                    const escapeHtml = (text) => text
                        .replaceAll("&", "&amp;")
                        .replaceAll("<", "&lt;")
                        .replaceAll(">", "&gt;");
                    const html = lines.map((line) => {{
                        if (!line.trim()) {{
                            return "<p><br></p>";
                        }}
                        return "<p>" + escapeHtml(line) + "</p>";
                    }}).join("");
                    inputEl.innerHTML = html || "<p><br></p>";
                }}

                inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));

                const finalText = (
                    inputEl.innerText ||
                    inputEl.textContent ||
                    ""
                ).trim();
                return {{
                    ok: true,
                    length: finalText.length,
                }};
            }})()
        """)
        if not isinstance(result, dict) or not result.get("ok"):
            reason = "unknown"
            if isinstance(result, dict):
                reason = str(result.get("reason", reason))
            raise CDPError(f"Failed to fill comment content: {reason}")

        return int(result.get("length", 0))

    def post_comment_to_feed(self, feed_id: str, xsec_token: str, content: str) -> dict[str, Any]:
        """
        Post a top-level comment to a feed detail page.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        content = content.strip()

        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")
        if not content:
            raise CDPError("content cannot be empty.")

        detail_url = make_feed_detail_url(feed_id, xsec_token)
        self._navigate(detail_url)
        self._sleep(2, minimum_seconds=1.0)
        self._check_feed_page_accessible()

        input_rect_js = """
            (function() {
                const selectors = [
                    "div.input-box div.content-edit span",
                    "div.input-box div.content-edit p.content-input",
                    "div.input-box div.content-edit",
                    "div.input-box",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLElement) || el.offsetParent === null) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        try:
            self._click_element_by_cdp("comment input box", input_rect_js)
            self._sleep(0.4, minimum_seconds=0.15)
        except CDPError:
            print(
                "[cdp_publish] Warning: Could not click comment input via CDP. "
                "Falling back to direct focus."
            )

        filled_len = self._fill_comment_content(content)
        self._sleep(0.6, minimum_seconds=0.2)

        submit_rect_js = """
            (function() {
                const selectors = [
                    "div.bottom button.submit",
                    "div.bottom button[class*='submit']",
                    "button.submit",
                    "button[class*='submit']",
                    "button[type='submit']",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLButtonElement) || el.offsetParent === null) {
                        continue;
                    }
                    if (el.disabled) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                const fallbackTexts = new Set(["发送", "提交", "评论"]);
                const buttons = document.querySelectorAll("button");
                for (const button of buttons) {
                    if (!(button instanceof HTMLButtonElement) || button.offsetParent === null) {
                        continue;
                    }
                    if (button.disabled) {
                        continue;
                    }
                    const text = (button.textContent || "").replace(/\\s+/g, " ").trim();
                    if (!fallbackTexts.has(text)) {
                        continue;
                    }
                    const r = button.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        self._click_element_by_cdp("comment submit button", submit_rect_js)
        self._sleep(1.0, minimum_seconds=0.4)

        print(f"[cdp_publish] Comment posted. feed_id={feed_id}, length={filled_len}")
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "content_length": filled_len,
            "success": True,
        }

    def _schedule_click_notification_mentions_tab(self) -> str:
        """Schedule a click on mentions tab after evaluate returns."""
        clicked_text = self._evaluate("""
            (() => {
                const keywordSet = new Set([
                    "评论和@",
                    "评论和 @",
                    "评论与@",
                    "提到我的",
                    "@我的",
                    "mentions",
                ]);
                const selectors = [
                    "[role='tab']",
                    "button",
                    "a",
                    "div[class*='tab']",
                    "div[class*='menu-item']",
                    "li[class*='tab-item']",
                    "li[class*='tab']",
                ];
                const seen = new Set();
                const candidates = [];
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!(node instanceof HTMLElement)) {
                            continue;
                        }
                        if (node.offsetParent === null) {
                            continue;
                        }
                        if (seen.has(node)) {
                            continue;
                        }
                        seen.add(node);
                        candidates.push(node);
                    }
                }

                for (const node of candidates) {
                    const text = (node.innerText || node.textContent || "")
                        .replace(/\\s+/g, " ")
                        .trim();
                    if (!text) {
                        continue;
                    }
                    if (text.length > 24) {
                        continue;
                    }
                    const normalized = text.replace(/\\d+/g, "").replace(/\\s+/g, "");
                    const exactMatches = [
                        normalized,
                        text.replace(/\\d+/g, "").trim(),
                    ];
                    if (!exactMatches.some((candidate) => keywordSet.has(candidate))) {
                        continue;
                    }
                    window.setTimeout(() => {
                        try {
                            node.click();
                        } catch (error) {
                            // ignored
                        }
                    }, 80);
                    return text;
                }
                return "";
            })()
        """)
        if isinstance(clicked_text, str):
            return clicked_text.strip()
        return ""

    def _fetch_notification_mentions_via_page(self) -> dict[str, Any] | None:
        """Fetch mentions API directly in page context using logged-in cookies."""
        result = self._evaluate("""
            (() => fetch(
                "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor=",
                {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "Accept": "application/json, text/plain, */*",
                    },
                }
            ).then(async (resp) => {
                const text = await resp.text();
                return {
                    ok: resp.ok,
                    status: resp.status,
                    url: resp.url,
                    body: text,
                };
            }).catch((error) => {
                return {
                    ok: false,
                    error: String(error),
                };
            }))()
        """)
        if not isinstance(result, dict):
            return None
        if not result.get("ok"):
            return None
        if int(result.get("status", 0)) != 200:
            return None
        body = result.get("body")
        if not isinstance(body, str) or not body.strip():
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        data = payload.get("data")
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("message_list", "items", "mentions", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break

        return {
            "request_url": result.get("url") or (
                "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor="
            ),
            "count": len(items),
            "has_more": data.get("has_more") if isinstance(data, dict) else None,
            "cursor": data.get("cursor") if isinstance(data, dict) else None,
            "items": items,
            "raw_payload": payload,
            "capture_mode": "page_fetch",
        }

    def get_notification_mentions(self, wait_seconds: float = 18.0) -> dict[str, Any]:
        """
        Capture notification mentions API payload from notification page requests.

        The API is captured from real browser traffic to preserve platform
        signatures/cookies generated by page scripts.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        wait_seconds = max(5.0, float(wait_seconds))

        self._send("Page.enable")
        self._send("Network.enable", {"maxPostDataSize": 65536})
        self._send("Network.setCacheDisabled", {"cacheDisabled": True})
        self._send("Page.navigate", {"url": XHS_NOTIFICATION_URL})
        self._sleep(1.2, minimum_seconds=0.5)

        direct_payload = self._fetch_notification_mentions_via_page()
        if direct_payload is not None:
            return direct_payload

        clicked_tab = self._schedule_click_notification_mentions_tab()
        if clicked_tab:
            print(f"[cdp_publish] Notification tab clicked: {clicked_tab}")

        request_meta_by_id: dict[str, dict[str, str]] = {}
        target_request_id = ""
        target_request_url = ""
        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv(timeout=timeout)
            except TimeoutError:
                continue

            message = json.loads(raw)
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                request = params.get("request", {})
                if isinstance(request_id, str):
                    request_meta_by_id[request_id] = {
                        "url": request.get("url", ""),
                        "method": str(request.get("method", "")).upper(),
                    }
                continue

            if method == "Network.responseReceived":
                request_id = params.get("requestId")
                if not isinstance(request_id, str):
                    continue

                request_meta = request_meta_by_id.get(request_id, {})
                request_url = request_meta.get("url", "")
                if XHS_NOTIFICATION_MENTIONS_API_PATH not in request_url:
                    continue

                if request_meta.get("method") == "OPTIONS":
                    continue

                status = params.get("response", {}).get("status")
                if status != 200:
                    raise CDPError(
                        "Notification mentions API responded with non-200 status: "
                        f"{status}, url={request_url}"
                    )

                target_request_id = request_id
                target_request_url = request_url
                break

        if not target_request_id:
            raise CDPError(
                "Timed out waiting for notification mentions request. "
                "Please open notification page manually and retry."
            )

        body_result = self._send("Network.getResponseBody", {"requestId": target_request_id})
        body_text = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body_text = base64.b64decode(body_text).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as e:
            raise CDPError(
                "Failed to decode notification mentions API JSON: "
                f"{e}; preview={body_text[:300]}"
            ) from e

        if not isinstance(payload, dict):
            raise CDPError("Unexpected notification mentions payload structure.")

        data = payload.get("data")
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("message_list", "items", "mentions", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break

        return {
            "request_url": target_request_url,
            "count": len(items),
            "has_more": data.get("has_more") if isinstance(data, dict) else None,
            "cursor": data.get("cursor") if isinstance(data, dict) else None,
            "items": items,
            "raw_payload": payload,
            "capture_mode": "network_capture",
        }

    def get_content_data(
        self,
        page_num: int = 1,
        page_size: int = 10,
        note_type: int = 0,
    ) -> dict[str, Any]:
        """
        Fetch creator content data table from data-analysis API.

        Args:
            page_num: Page number (1-based).
            page_size: Rows per page.
            note_type: API type filter value (default: 0).
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        if page_num < 1:
            raise CDPError("--page-num must be >= 1.")
        if page_size < 1:
            raise CDPError("--page-size must be >= 1.")
        self._navigate(XHS_CONTENT_DATA_URL)
        try:
            return self._fetch_content_data_via_page_fetch(
                page_num=page_num,
                page_size=page_size,
                note_type=note_type,
            )
        except CDPError as exc:
            print(
                "[cdp_publish] Explicit content-data fetch failed, "
                f"falling back to network capture: {exc}"
            )
            return self._capture_content_data_from_page_request(
                page_num=page_num,
                page_size=page_size,
                note_type=note_type,
            )

    # ------------------------------------------------------------------
    # Publishing actions
    # ------------------------------------------------------------------

    def _query_node_id(self, selector: str) -> int:
        """Return the first DOM node id matching selector, or 0 when absent."""
        self._send("DOM.enable")
        doc = self._send("DOM.getDocument", {"depth": 1})
        root_id = doc["root"]["nodeId"]
        result = self._send("DOM.querySelector", {
            "nodeId": root_id,
            "selector": selector,
        })
        return int(result.get("nodeId", 0) or 0)

    def _count_uploaded_images(self, expected_count: int = 1) -> int:
        """Multi-evidence estimation of uploaded image count.

        Uses multiple signals to detect whether images uploaded successfully,
        since XHS page DOM changes frequently and no single selector is reliable.
        """
        result = self._evaluate(f"""
            (() => {{
                const expected = {expected_count};

                // Evidence 1: Direct image preview elements (legacy selectors)
                const selectors = [
                    {json.dumps(SELECTORS["image_preview_items"])},
                    ".img-preview-area [class*='preview']",
                    ".draggable-item",
                    "[class*='img-preview'] .pr",
                    "[class*='cropper']",
                    "[class*='upload-preview'] img",
                    "[class*='preview-container'] img",
                    "[class*='image-item']",
                ];
                for (const sel of selectors) {{
                    try {{ const n = document.querySelectorAll(sel).length; if (n > 0) return n; }} catch(e) {{}}
                }}

                // Evidence 2: Any img elements inside publish area
                const area = document.querySelector(
                    '[class*="publish-form"],[class*="creator-form"],.publish-page,.main-content'
                );
                if (area) {{
                    const imgs = area.querySelectorAll('img');
                    if (imgs.length > 0) return imgs.length;
                }}

                // Evidence 3: File inputs hidden + add-product button = upload confirmed
                const fileInputs = document.querySelectorAll('input[type="file"]');
                if (fileInputs.length > 0) {{
                    const allHidden = Array.from(fileInputs).every(function(f) {{
                        const s = window.getComputedStyle(f);
                        return s.display === 'none';
                    }});
                    if (allHidden) {{
                        const btns = document.querySelectorAll(
                            'button,.multi-good-select-empty-btn,[class*="good"],[class*="add"]'
                        );
                        for (var i = 0; i < btns.length; i++) {{
                            const t = (btns[i].innerText || '').trim();
                            if (t.indexOf('添加商品') >= 0) return Math.max(expected, 1);
                        }}
                        const dels = document.querySelectorAll(
                            '[class*="delete"],[class*="remove"],[class*="icon-close"],[class*="del-btn"]'
                        );
                        if (dels.length > 0) return Math.max(expected, dels.length);
                    }}
                }}

                // Evidence 4: No file inputs but page in editor state
                const bodyText = document.body.innerText || '';
                if (fileInputs.length === 0 && bodyText.indexOf('添加商品') >= 0) {{
                    return Math.max(expected, 1);
                }}

                return 0;
            }})()
        """)
        return int(result or 0)

    def _wait_for_uploaded_images(self, expected_count: int, timeout_seconds: float = 60.0):
        """Wait until image upload is confirmed via multi-evidence detection."""
        deadline = time.time() + max(5.0, float(timeout_seconds))
        last_count = -1
        while time.time() < deadline:
            current_count = self._count_uploaded_images(expected_count)
            if current_count != last_count:
                print(
                    "[cdp_publish] Waiting for uploaded image previews: "
                    f"{current_count}/{expected_count}"
                )
                last_count = current_count
            if current_count >= expected_count:
                return
            self._sleep(0.5, minimum_seconds=0.15)

        # Timeout — output diagnostics before fallback
        print("[cdp_publish] Preview count timeout, collecting diagnostic evidence...")
        diag = self._evaluate("""(function() {
            var info = {};
            var imgs = document.querySelectorAll('img');
            info.imgCandidates = [];
            for (var i = 0; i < Math.min(imgs.length, 15); i++) {
                var r = imgs[i].getBoundingClientRect();
                info.imgCandidates.push({src:(imgs[i].src||'').slice(-30), w:r.width, h:r.height, v:r.width>0});
            }
            var fis = document.querySelectorAll('input[type="file"]');
            info.fileInputs = [];
            for (var i = 0; i < fis.length; i++) {
                var s = window.getComputedStyle(fis[i]);
                info.fileInputs.push({d:s.display, a:(fis[i].accept||'').slice(0,20)});
            }
            var btns = document.querySelectorAll('button,.multi-good-select-empty-btn');
            info.buttons = [];
            for (var i = 0; i < btns.length; i++) {
                var t = (btns[i].innerText||'').trim();
                if (t) info.buttons.push({tag:btns[i].tagName, t:t.slice(0,30)});
            }
            return JSON.stringify(info);
        })()""")
        if diag:
            print(f"[cdp_publish] Diagnostic: {str(diag)[:800]}")

        ss = self._send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        if ss and "data" in ss:
            dump_dir = os.path.join(os.path.dirname(__file__) or ".", "tmp", "upload_diag")
            os.makedirs(dump_dir, exist_ok=True)
            path = os.path.join(dump_dir, f"timeout_{int(time.time())}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(ss["data"]))
            print(f"[cdp_publish] Screenshot saved: {path}")

        dom = self._evaluate("document.documentElement.outerHTML.slice(0, 10000)")
        if dom:
            dump_dir = os.path.join(os.path.dirname(__file__) or ".", "tmp", "upload_diag")
            os.makedirs(dump_dir, exist_ok=True)
            path = os.path.join(dump_dir, f"timeout_{int(time.time())}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(dom))
            print(f"[cdp_publish] DOM dump saved: {path}")

        # Fallback: check page state
        print("[cdp_publish] Checking page state fallback...")
        fallback = self._evaluate("""
            (function() {
                var fis = document.querySelectorAll('input[type="file"]');
                var allHidden = true; var hasImageAccept = false;
                for (var i = 0; i < fis.length; i++) {
                    var st = window.getComputedStyle(fis[i]);
                    if (st.display !== 'none') allHidden = false;
                    if ((fis[i].accept || '').indexOf('jpg') >= 0 || (fis[i].accept || '').indexOf('image') >= 0)
                        hasImageAccept = true;
                }
                if (allHidden && hasImageAccept) return 'accepted';
                var t = document.body.innerText || '';
                if (t.indexOf('添加商品') >= 0) return 'editor_ready';
                return '';
            })()
        """)
        if fallback:
            print(f"[cdp_publish] Upload confirmed via page state ({fallback}), continuing.")
            return

        raise CDPError(
            f"Timed out waiting for image upload preview {expected_count}. "
            "Diagnostic evidence saved to tmp/upload_diag/."
        )

    def _find_content_editor_selector(self) -> str | None:
        """Return the best available content editor selector for the current page."""
        placeholder_literal = json.dumps(SELECTORS["content_placeholder_text"])
        selector = self._evaluate(f"""
            (() => {{
                const directSelectors = [
                    {json.dumps(SELECTORS["content_editor"])},
                    {json.dumps(SELECTORS["content_editor_alt"])},
                    {json.dumps(SELECTORS["content_editor_alt2"])},
                    "[role='textbox']",
                ];
                for (const selector of directSelectors) {{
                    const node = document.querySelector(selector);
                    if (
                        node instanceof HTMLElement &&
                        node.offsetParent !== null &&
                        node.getBoundingClientRect().width > 0 &&
                        node.getBoundingClientRect().height > 0
                    ) {{
                        return selector;
                    }}
                }}

                const placeholder = {placeholder_literal};
                const candidates = document.querySelectorAll("p[data-placeholder], div[data-placeholder]");
                for (const node of candidates) {{
                    const value = (node.getAttribute("data-placeholder") || "").trim();
                    if (!value.includes(placeholder)) {{
                        continue;
                    }}
                    let current = node;
                    for (let depth = 0; depth < 5 && current; depth += 1) {{
                        current = current.parentElement;
                        if (
                            current instanceof HTMLElement &&
                            current.getAttribute("role") === "textbox"
                        ) {{
                            return "[role='textbox']";
                        }}
                    }}
                }}
                return null;
            }})()
        """)
        return selector if isinstance(selector, str) and selector.strip() else None

    def _get_publish_button_rect(self) -> dict[str, Any] | None:
        """Locate the current publish button using current and legacy selectors."""
        return self._evaluate(f"""
            (() => {{
                const buttonSelector = {json.dumps(SELECTORS["publish_button"])};
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 0 &&
                    node.getBoundingClientRect().height > 0
                );
                const toRect = (node) => {{
                    const rect = node.getBoundingClientRect();
                    return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                }};

                const button = document.querySelector(buttonSelector);
                if (visible(button)) {{
                    return toRect(button);
                }}

                const keywords = [
                    {json.dumps(SELECTORS["publish_button_text"])},
                    {json.dumps(SELECTORS["schedule_publish_button_text"])},
                ];
                const buttons = document.querySelectorAll("button, [role='button'], .d-button");
                for (const node of buttons) {{
                    if (!visible(node)) {{
                        continue;
                    }}
                    const text = (node.innerText || node.textContent || "").trim();
                    if (keywords.includes(text)) {{
                        return toRect(node);
                    }}
                }}
                return null;
            }})()
        """)

    def _is_publish_button_ready(self) -> bool:
        """Return True when the publish button is present, visible and not disabled."""
        ready = self._evaluate(f"""
            (() => {{
                const selectors = [
                    {json.dumps(SELECTORS["publish_button"])},
                    "button.publishBtn",
                ];
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 0 &&
                    node.getBoundingClientRect().height > 0
                );
                for (const selector of selectors) {{
                    const button = document.querySelector(selector);
                    if (!visible(button)) {{
                        continue;
                    }}
                    if (button.hasAttribute("disabled")) {{
                        continue;
                    }}
                    const className = String(button.className || "");
                    if (className.includes("disabled")) {{
                        continue;
                    }}
                    return true;
                }}
                return false;
            }})()
        """)
        return bool(ready)

    def _wait_for_publish_button_ready(self, timeout_seconds: float = VIDEO_PROCESS_TIMEOUT):
        """Wait until the publish button becomes interactive."""
        deadline = time.time() + max(5.0, float(timeout_seconds))
        while time.time() < deadline:
            if self._is_publish_button_ready():
                print("[cdp_publish] Publish button is ready.")
                return
            self._sleep(VIDEO_PROCESS_POLL, minimum_seconds=0.4)

        raise CDPError(
            f"Publish button did not become ready within {int(timeout_seconds)}s."
        )

    def _click_tab(self, tab_selector: str, tab_text: str, timeout_seconds: float = 15.0):
        """Click a publish-mode tab by selector and text content (with retry)."""
        print(f"[cdp_publish] Clicking '{tab_text}' tab...")
        selector_alt = (
            "div.creator-tab, .creator-tab, [class*='creator-tab'], [role='tab'], button, div"
        )
        selector_alt_literal = json.dumps(selector_alt)
        tab_text_literal = json.dumps(tab_text)

        deadline = time.time() + max(1.0, float(timeout_seconds))
        last_clicked = False
        while time.time() < deadline:
            clicked = self._evaluate(f"""
                (function() {{
                    var targetText = {tab_text_literal};
                    var fuzzyKeywords = [targetText];
                    if (targetText.indexOf('图文') !== -1) {{
                        fuzzyKeywords.push('图文', '上传图文');
                    }}
                    if (targetText.indexOf('视频') !== -1) {{
                        fuzzyKeywords.push('视频', '上传视频');
                    }}

                    function matches(text) {{
                        var t = (text || '').trim();
                        if (!t) return false;
                        if (t === targetText) return true;
                        for (var i = 0; i < fuzzyKeywords.length; i++) {{
                            var keyword = fuzzyKeywords[i];
                            if (keyword && t.indexOf(keyword) !== -1) {{
                                return true;
                            }}
                        }}
                        return false;
                    }}

                    var tabs = document.querySelectorAll('{tab_selector}');
                    for (var i = 0; i < tabs.length; i++) {{
                        if (matches(tabs[i].textContent)) {{
                            tabs[i].click();
                            return true;
                        }}
                    }}

                    var allTabs = document.querySelectorAll({selector_alt_literal});
                    for (var j = 0; j < allTabs.length; j++) {{
                        if (matches(allTabs[j].textContent)) {{
                            allTabs[j].click();
                            return true;
                        }}
                    }}
                    return false;
                }})();
            """)

            last_clicked = bool(clicked)
            if last_clicked:
                break

            # When switching tabs is not required (or UI differs),
            # allow continuing if the upload input element is already present.
            if "图文" in tab_text:
                upload_ready = self._evaluate(
                    f"!!document.querySelector('{SELECTORS['upload_input']}') || "
                    f"!!document.querySelector('{SELECTORS['upload_input_alt']}')"
                )
                if upload_ready:
                    print(
                        "[cdp_publish] '上传图文' tab not found, but upload input is ready. "
                        "Continuing..."
                    )
                    return

            # Wait for SPA UI to render; then retry.
            self._sleep(0.8, minimum_seconds=0.25)

        if not last_clicked:
            raise CDPError(
                f"Could not find '{tab_text}' tab. "
                "The page structure may have changed."
            )

        print(f"[cdp_publish] Tab '{tab_text}' clicked, waiting for upload area...")
        self._sleep(TAB_CLICK_WAIT, minimum_seconds=0.8)

    def _click_image_text_tab(self):
        """Click the '上传图文' tab to switch to image+text publish mode."""
        # 检查页面是否已有发布内容（已上传图片/已挂载商品）
        _existing = self._evaluate("""
            (function() {
                var t = document.body.innerText || '';
                if (t.indexOf('商品ID') >= 0) return 'has_product_card';
                var imgs = document.querySelectorAll('img');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].width > 80 && imgs[i].height > 80) return 'has_uploaded_images';
                }
                return '';
            })()
        """)
        if _existing:
            # 有遗留内容（之前运行的产物），导航到干净 URL 重新加载
            print(f"[cdp_publish] 页面有遗留内容 ({_existing})，导航到干净发布页")
            clean_url = "https://creator.xiaohongshu.com/publish/publish?source=official"
            self._navigate(clean_url)
            self._sleep(3, minimum_seconds=1.5)
            # 导航后重新尝试点击 tab
            try:
                self._click_tab(SELECTORS["image_text_tab"], SELECTORS["image_text_tab_text"])
            except CDPError:
                image_url = clean_url + "&from=tab_switch"
                print(f"[cdp_publish] 导航到图文模式 URL")
                self._navigate(image_url)
                self._sleep(2, minimum_seconds=1.0)
            return

        try:
            self._click_tab(SELECTORS["image_text_tab"], SELECTORS["image_text_tab_text"])
        except CDPError:
            current_url = str(self._evaluate("window.location.href") or "")
            if "from=tab_switch" not in current_url:
                image_url = "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
                print(f"[cdp_publish] 导航到图文模式 URL")
                self._navigate(image_url)
                self._sleep(2, minimum_seconds=1.0)
            else:
                raise

    def _click_video_tab(self):
        """Click the '上传视频' tab to switch to video publish mode."""
        self._click_tab(SELECTORS["video_tab"], SELECTORS["video_tab_text"])

    def _upload_images(self, image_paths: list[str]):
        """Upload images via the file input element."""
        if not image_paths:
            print("[cdp_publish] No images to upload, skipping.")
            return

        preserve_flags = [self._should_preserve_upload_path(path) for path in image_paths]
        prepared_paths = [self._prepare_upload_file_path(path) for path in image_paths]

        print(f"[cdp_publish] Uploading {len(image_paths)} image(s)...")
        if self.preserve_upload_paths:
            print("[cdp_publish] Upload path normalization disabled; preserving original paths.")
        elif any(preserve_flags):
            print("[cdp_publish] Auto-detected Windows/UNC upload paths; preserving original paths.")

        for index, file_path in enumerate(prepared_paths, start=1):
            node_id = 0
            selectors = (
                (SELECTORS["upload_input"], SELECTORS["upload_input_alt"])
                if index == 1
                else (SELECTORS["upload_input_alt"], SELECTORS["upload_input"])
            )
            for selector in selectors:
                node_id = self._query_node_id(selector)
                if node_id:
                    break

            if not node_id:
                raise CDPError(
                    "Could not find file input element.\n"
                    "The page structure may have changed. Check references/publish-workflow.md."
                )

            self._send("DOM.setFileInputFiles", {
                "nodeId": node_id,
                "files": [file_path],
            })
            print(f"[cdp_publish] Image {index}/{len(prepared_paths)} submitted: {file_path}")
            # 触发 change/input 事件确保 React 检测到文件上传
            self._evaluate("""(function() {
                var inp = document.querySelector('input[type=\"file\"]');
                if (inp) {
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                }
                return true;
            })()""")
            self._wait_for_uploaded_images(index)
            self._sleep(0.9, minimum_seconds=0.25)

        print("[cdp_publish] Images uploaded. Waiting for editor to appear...")
        self._sleep(UPLOAD_WAIT, minimum_seconds=2.0)

    def _upload_video(self, video_path: str):
        """Upload a video file via the file input element."""
        preserve_path = self._should_preserve_upload_path(video_path)
        prepared_path = self._prepare_upload_file_path(video_path)
        print(f"[cdp_publish] Uploading video: {prepared_path}")
        if self.preserve_upload_paths:
            print("[cdp_publish] Upload path normalization disabled; preserving original paths.")
        elif preserve_path:
            print("[cdp_publish] Auto-detected Windows/UNC upload path; preserving original path.")

        node_id = 0
        for selector in (SELECTORS["upload_input"], SELECTORS["upload_input_alt"]):
            node_id = self._query_node_id(selector)
            if node_id:
                break

        if not node_id:
            raise CDPError(
                "Could not find file input element for video upload.\n"
                "The page structure may have changed."
            )

        # Set the video file
        self._send("DOM.setFileInputFiles", {
            "nodeId": node_id,
            "files": [prepared_path],
        })

        print("[cdp_publish] Video file submitted. Waiting for processing...")

    def _wait_video_processing(self):
        """Wait for the video to finish processing after upload.

        The Xiaohongshu creator page shows a progress/processing indicator
        while the video is being uploaded and transcoded. We wait until the
        publish button becomes clickable, which is more reliable on the
        current creator center than checking title/editor presence alone.
        """
        print("[cdp_publish] Waiting for video processing to complete...")
        deadline = time.time() + VIDEO_PROCESS_TIMEOUT
        last_pct = ""

        while time.time() < deadline:
            if self._is_publish_button_ready():
                print("[cdp_publish] Video processing complete - publish button is ready.")
                self._sleep(1.0, minimum_seconds=0.25)
                return

            # Try to read progress text for user feedback
            pct = self._evaluate("""
                (function() {
                    // Look for progress percentage text
                    var els = document.querySelectorAll(
                        '[class*="progress"], [class*="percent"], [class*="upload"]'
                    );
                    for (var i = 0; i < els.length; i++) {
                        var t = els[i].textContent.trim();
                        if (t && /\\d+%/.test(t)) return t;
                    }
                    return '';
                })()
            """) or ""
            if pct and pct != last_pct:
                print(f"[cdp_publish] Video processing: {pct}")
                last_pct = pct

            time.sleep(VIDEO_PROCESS_POLL)

        raise CDPError(
            f"Video processing did not complete within {VIDEO_PROCESS_TIMEOUT}s. "
            "The video may be too large or processing is slow."
        )

    def _fill_title(self, title: str):
        """Fill in the article title."""
        print(f"[cdp_publish] Setting title: {title[:40]}...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        for selector in (SELECTORS["title_input"], SELECTORS["title_input_alt"]):
            found = self._evaluate(f"!!document.querySelector('{selector}')")
            if found:
                escaped_title = json.dumps(title)
                self._evaluate(f"""
                    (function() {{
                        var el = document.querySelector('{selector}');
                        el.focus();
                        el.value = {escaped_title};
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.blur();
                    }})();
                """)
                print("[cdp_publish] Title set.")
                return

        raise CDPError("Could not find title input element.")

    def _fill_content(self, content: str):
        """Fill in the article body content using the current creator editor."""
        print(f"[cdp_publish] Setting content ({len(content)} chars)...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)
        selector = self._find_content_editor_selector()
        if not selector:
            raise CDPError("Could not find content editor element.")

        escaped = json.dumps(content)
        placeholder_literal = json.dumps(SELECTORS["content_placeholder_text"])
        result = self._evaluate(f"""
            (() => {{
                const selector = {json.dumps(selector)};
                const placeholder = {placeholder_literal};
                let el = document.querySelector(selector);
                if (!(el instanceof HTMLElement) || el.offsetParent === null) {{
                    const candidates = document.querySelectorAll("p[data-placeholder], div[data-placeholder]");
                    for (const node of candidates) {{
                        const value = (node.getAttribute("data-placeholder") || "").trim();
                        if (!value.includes(placeholder)) {{
                            continue;
                        }}
                        let current = node;
                        for (let depth = 0; depth < 5 && current; depth += 1) {{
                            current = current.parentElement;
                            if (
                                current instanceof HTMLElement &&
                                current.getAttribute("role") === "textbox"
                            ) {{
                                el = current;
                                break;
                            }}
                        }}
                        if (el instanceof HTMLElement) {{
                            break;
                        }}
                    }}
                }}

                if (!(el instanceof HTMLElement)) {{
                    return false;
                }}

                const text = {escaped};
                const parts = text.split("\\n");
                const lines = parts.length ? parts : [""];

                el.focus();
                while (el.firstChild) {{
                    el.removeChild(el.firstChild);
                }}

                for (const line of lines) {{
                    const paragraph = document.createElement("p");
                    if (line) {{
                        paragraph.textContent = line;
                    }} else {{
                        paragraph.appendChild(document.createElement("br"));
                    }}
                    el.appendChild(paragraph);
                }}

                el.dispatchEvent(new Event("input", {{ bubbles: true }}));
                el.dispatchEvent(new Event("change", {{ bubbles: true }}));
                return true;
            }})()
        """)
        if not result:
            raise CDPError("Could not set content into creator editor.")

        print(f"[cdp_publish] Content set via selector: {selector}")

    def _set_schedule_post_time(self, post_time: str | None):
        """Set schedle publish time if necessary"""
        if post_time == None:
            return
        
        print(f"[cdp_publish] Setting schedule publish time: {post_time}")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        post_time_enabled = self._evaluate(f"""
            (async function() {{
                try {{
                    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                    const visible = (node) => (
                        node instanceof HTMLElement &&
                        node.offsetParent !== null &&
                        node.getBoundingClientRect().width > 0 &&
                        node.getBoundingClientRect().height > 0
                    );

                    // Click scheduled publish switch if needed
                    const switchSelector = {json.dumps(SELECTORS["schedule_switch"])};
                    const switchElement = document.querySelector(switchSelector);
                    if (!(switchElement instanceof HTMLElement) || !visible(switchElement)) {{
                        return 'Schedule publish switch is missing.';
                    }}
                    const isChecked = switchElement.getAttribute('aria-checked');
                    if (isChecked !== 'true') {{
                        switchElement.click();
                        await sleep(300);
                    }}
                    
                    // Set publish time
                    const el = document.querySelector({json.dumps(SELECTORS["schedule_datetime_input"])});
                    if (!(el instanceof HTMLInputElement)) {{
                        return 'Schedule publish date-picker input is missing.';
                    }}
                    var nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    el.focus();
                    el.select();
                    nativeSetter.call(el, {json.dumps(post_time)});
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    return 'ok';
                }} catch (err) {{
                    return String(err);
                }}
            }})();
        """)

        if not post_time_enabled == 'ok':
            raise CDPError("Could not set scheduled publish time. Reason:" + post_time_enabled)
        
        print("[cdp_publish] Schedule publish time set.")
        return

    def _like_note(self):
        """Like the current note."""
        print("[cdp_publish] Liking note...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        liked = self._evaluate("""
            (function() {{
                // Try various like button selectors
                var selectors = [
                    '.like-button, [class*="like"], [class*="heart"]',
                    'button[aria-label*="like"], button[aria-label*="赞"]',
                    '[data-testid*="like"], [data-testid*="heart"]',
                    'svg[class*="like"], svg[class*="heart"]'
                ];

                for (var sel of selectors) {{
                    var elements = document.querySelectorAll(sel);
                    for (var el of elements) {{
                        // Check if it's not already liked
                        if (!el.classList.contains('liked') && !el.classList.contains('active')) {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})();
        """)

        if liked:
            print("[cdp_publish] Note liked.")
        else:
            print("[cdp_publish] Could not find like button or already liked.")

        return liked

    def _collect_note(self):
        """Collect the current note."""
        print("[cdp_publish] Collecting note...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        collected = self._evaluate("""
            (function() {{
                // Try various collect button selectors
                var selectors = [
                    '.collect-button, [class*="collect"], [class*="bookmark"]',
                    'button[aria-label*="collect"], button[aria-label*="收藏"]',
                    '[data-testid*="collect"], [data-testid*="bookmark"]',
                    'svg[class*="collect"], svg[class*="bookmark"]'
                ];

                for (var sel of selectors) {{
                    var elements = document.querySelectorAll(sel);
                    for (var el of elements) {{
                        // Check if it's not already collected
                        if (!el.classList.contains('collected') && !el.classList.contains('active')) {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})();
        """)

        if collected:
            print("[cdp_publish] Note collected.")
        else:
            print("[cdp_publish] Could not find collect button or already collected.")

        return collected

    def _move_mouse(self, x: float, y: float):
        """Move mouse cursor via CDP to support hover-driven UI."""
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": float(x),
            "y": float(y),
        })

    def _click_mouse(self, x: float, y: float):
        """Perform a real left-click via CDP at the given coordinates.

        Sends mouseMoved to position the cursor, then mousePressed and
        mouseReleased with clickCount=1 for a full click gesture that
        React SPAs recognize as a trusted user click.
        """
        x, y = float(x), float(y)
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        time.sleep(0.02)
        self._send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        time.sleep(0.05)
        self._send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    def _click_element_by_cdp(self, description: str, js_get_rect: str):
        """Click an element using CDP Input.dispatchMouseEvent for reliable clicks.

        Modern web frameworks (Vue/React) often ignore JS .click() calls.
        Dispatching real mouse events via CDP always works.

        Args:
            description: Human-readable description for logging.
            js_get_rect: JavaScript expression that returns {x, y, width, height}
                         of the element to click, or null if not found.
        """
        rect = self._evaluate(js_get_rect)
        if not rect:
            raise CDPError(
                f"Could not find {description}. "
                "Please click it manually in the browser."
            )

        # Compute center of the element
        cx = rect["x"] + rect["width"] / 2
        cy = rect["y"] + rect["height"] / 2
        print(f"[cdp_publish] Clicking {description} at ({cx:.0f}, {cy:.0f})...")

        self._click_mouse(cx, cy)

    def _click_publish(self, scheduled: bool = False):
        """Click the publish button using CDP mouse events."""
        print("[cdp_publish] Clicking publish button...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)
        self._wait_for_publish_button_ready(timeout_seconds=20.0)
        rect = self._get_publish_button_rect()
        if not rect:
            raise CDPError(
                "Could not find publish button. "
                "The creator center page structure may have changed."
            )

        cx = rect["x"] + rect["width"] / 2
        cy = rect["y"] + rect["height"] / 2
        print(f"[cdp_publish] Clicking publish button at ({cx:.0f}, {cy:.0f})...")
        self._click_mouse(cx, cy)
        print("[cdp_publish] Publish button clicked.")

        # Wait for publish success and get note link
        self._sleep(5, minimum_seconds=2.0)
        note_link = self._evaluate("""
            (function() {
                // Try to find note link in success message
                var links = document.querySelectorAll('a[href*="xiaohongshu.com/explore"]');
                if (links.length > 0) {
                    return links[0].href;
                }
                // Try to find note ID in page
                var noteId = document.body.textContent.match(/\\b[0-9a-fA-F]{24}\\b/);
                if (noteId) {
                    return 'https://www.xiaohongshu.com/explore/' + noteId[0];
                }
                return null;
            })();
        """)

        return note_link

    def click_add_product(self, strict: bool = False) -> bool:
        """查找并点击"添加商品"按钮，等待商品弹窗打开。

        查找策略：先精确匹配 button 文本"添加商品"，再尝试文本包含匹配。
        点击成功后轮询检测弹窗（最多等 10 秒），弹窗出现返回 True。

        Args:
            strict: True 时失败抛 CDPError，False 时只返回 False。

        Returns:
            bool: 弹窗是否成功打开。
        """
        print("[cdp_publish] Clicking add-product button...")
        try:
            current_url = self._evaluate("window.location.href") or ""
        except Exception:
            current_url = ""
        print(f"[cdp_publish] Current URL: {current_url[:100]}")

        # === 多策略查找按钮 ===
        def _find_btn() -> dict | None:
            """在页面中查找添加商品按钮，返回 {x, y} 或 None。"""
            js = r"""(function() {
                function normAddProductText(s) {
                    return String(s || '').replace(/\s+/g, ' ').trim().replace(/^\++\s*/, '').trim();
                }
                var candidates = [];
                // 策略1: button 文本匹配（兼容 [+] 添加商品）
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].innerText || '').trim();
                    var st = window.getComputedStyle(btns[i]);
                    if (normAddProductText(t) === '添加商品' && st.display !== 'none' && st.visibility !== 'hidden') {
                        candidates.push({el: btns[i], text: t, score: 3});
                    }
                }
                // 策略2: .multi-good-select-empty-btn
                var extras = document.querySelectorAll('.multi-good-select-empty-btn');
                for (var j = 0; j < extras.length; j++) {
                    var t2 = (extras[j].innerText || '').trim();
                    var st2 = window.getComputedStyle(extras[j]);
                    if (t2.indexOf('添加商品') >= 0 && st2.display !== 'none' && st2.visibility !== 'hidden') {
                        candidates.push({el: extras[j], text: t2, score: 2});
                    }
                }
                // 策略3: 任意可见元素文本包含"添加商品"
                if (candidates.length === 0) {
                    var all = document.querySelectorAll('span,div,[class*="add"],[class*="btn"]');
                    for (var k = 0; k < all.length; k++) {
                        var t3 = (all[k].innerText || '').trim();
                        var st3 = window.getComputedStyle(all[k]);
                        if (normAddProductText(t3) === '添加商品' && st3.display !== 'none' && st3.visibility !== 'hidden') {
                            candidates.push({el: all[k], text: t3, score: 1});
                        }
                    }
                }
                if (candidates.length === 0) return null;
                candidates.sort(function(a,b){return b.score - a.score;});
                var best = candidates[0].el;
                var bestText = candidates[0].text;
                best.scrollIntoView({block: 'center'});
                var r = best.getBoundingClientRect();
                return {x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height, text: bestText};
            })()"""
            raw = self._evaluate(js)
            if isinstance(raw, dict) and raw.get("x", 0) > 0:
                return raw
            return None

        btn = _find_btn()
        if not btn:
            print("[cdp_publish] 添加商品按钮未找到")
            if strict:
                raise CDPError("添加商品按钮未找到")
            return False

        x, y = float(btn["x"]), float(btn["y"])
        btn_text = str(btn.get("text", ""))
        print(f"[cdp_publish] 添加商品按钮: text='{btn_text}' pos=({x:.0f},{y:.0f})")

        # === CDP 鼠标点击 ===
        self._click_mouse(x, y)

        # === 轮询检测弹窗（最多 10 秒）===
        print("[cdp_publish] 等待商品弹窗...")
        dialog_indicators = [
            r"""document.body.innerText.indexOf('选择商品') >= 0""",
            r"""document.querySelectorAll('[class*="modal"][class*="goods"],[class*="dialog"][class*="goods"],[class*="goods-select"]').length > 0""",
            r"""document.querySelectorAll('.goods-list-normal,[class*="goods-list"]').length > 0""",
            r"""document.querySelectorAll('input[type="checkbox"]').length > 1""",
        ]
        for i in range(20):
            self._sleep(0.5, minimum_seconds=0.3)
            for idx, indicator_js in enumerate(dialog_indicators):
                try:
                    val = self._evaluate(indicator_js)
                    if val:
                        print(f"[cdp_publish] 弹窗已打开 (指示器[{chr(65+idx)}])")
                        return True
                except Exception:
                    continue
            try:
                body_len = self._evaluate("(document.body.innerText||'').length")
                if body_len and body_len > 450:
                    print(f"[cdp_publish] 弹窗可能已打开 (文本长度={body_len})")
                    return True
            except Exception:
                pass

        # === 超时失败 ===
        print("[cdp_publish] 添加商品弹窗未在 10 秒内打开")
        try:
            img = self._send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            if img and "data" in img:
                import base64
                path = "tmp/click_add_product_fail.png"
                with open(path, "wb") as f:
                    f.write(base64.b64decode(img["data"]))
                print(f"[cdp_publish] 失败截图: {path}")
        except Exception as e:
            print(f"[cdp_publish] 截图失败: {e}")

        if strict:
            raise CDPError("添加商品弹窗未打开")
        return False

    # ------------------------------------------------------------------
    # Product selection workflow
    # ------------------------------------------------------------------

    def select_product_with_match(self, product_name="", product_id="", strict=False):
        """打开商品弹窗 → 匹配商品 → CDP鼠标点击复选框 → 验证 → CDP鼠标点击保存 → 验证弹窗关闭"""
        target_name = product_name
        target_id = product_id

        # === Phase 1: JS 匹配商品，获取复选框坐标（不执行 .click()） ===
        result = self._evaluate(
            f"""
            (async () => {{
                function norm(s) {{
                    return String(s || '').replace(/\\s+/g, '').trim().toLowerCase();
                }}
                function isVisible(el) {{
                    if (!el) return false;
                    var r = el.getBoundingClientRect();
                    var st = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                }}
                function pickText(root, selectors) {{
                    for (var i = 0; i < selectors.length; i++) {{
                        var n = root.querySelector(selectors[i]);
                        if (n && isVisible(n)) {{
                            var t = String(n.innerText || n.textContent || '').trim();
                            if (t) return t;
                        }}
                    }}
                    return '';
                }}
                function extractId(el) {{
                    var attrs = ['data-id', 'data-spu-id', 'data-sku-id', 'data-goods-id', 'data-product-id'];
                    for (var i = 0; i < attrs.length; i++) {{
                        var v = el.getAttribute(attrs[i]);
                        if (v && String(v).trim()) return String(v).trim();
                    }}
                    if (el.dataset) {{
                        var keys = ['id', 'spuId', 'skuId', 'goodsId', 'productId'];
                        for (var j = 0; j < keys.length; j++) {{
                            var dv = el.dataset[keys[j]];
                            if (dv && String(dv).trim()) return String(dv).trim();
                        }}
                    }}
                    var txt = String(el.innerText || el.textContent || '');
                    var m = txt.match(/ID\\s*[:?]\\s*([0-9a-zA-Z_-]+)/i);
                    if (m && m[1]) return String(m[1]).trim();
                    return '';
                }}
                function firstPrice(text) {{
                    var m = String(text || '').match(/¥\\s*\\d+(?:\\.\\d+)?|\\d+(?:\\.\\d+)?元/);
                    return m ? m[0] : '';
                }}
                var cardSelectors = [
                    '.goods-item', '.product-item',
                    '[class*="goods-item"]', '[class*="product-item"]',
                    '[class*="goods"]', '[class*="product"]',
                    'li', '.semi-table-row', '.goods-list-normal'
                ];
                const sleep = (ms) => new Promise(function(resolve) {{ setTimeout(resolve, ms); }});
                for (var poll = 0; poll < 12; poll++) {{
                    var testCards = [];
                    for (var si = 0; si < cardSelectors.length; si++) {{
                        var tn = Array.from(document.querySelectorAll(cardSelectors[si])).filter(isVisible);
                        if (tn.length) {{ testCards = tn; break; }}
                    }}
                    var hasReal = false;
                    for (var tc = 0; tc < testCards.length; tc++) {{
                        var tt = String(testCards[tc].innerText || testCards[tc].textContent || '').trim();
                        if (tt.length > 5 && tt.indexOf('加载') < 0 && tt.indexOf('选择我') < 0) {{ hasReal = true; break; }}
                    }}
                    if (hasReal) break;
                    await sleep(1000);
                }}
                var cards = [];
                for (var i = 0; i < cardSelectors.length; i++) {{
                    var nodes = Array.from(document.querySelectorAll(cardSelectors[i])).filter(isVisible);
                    if (nodes.length) {{ cards = nodes; break; }}
                }}
                var candidates = [];
                var seenKey = {{}};
                for (var c = 0; c < cards.length; c++) {{
                    var card = cards[c];
                    var text = String(card.innerText || card.textContent || '').trim();
                    if (!text || text.length < 2) continue;
                    var name = pickText(card, [
                        '[class*="name"]', '[class*="title"]', '[class*="goods-name"]',
                        '[class*="product-name"]', 'h3','h4'
                    ]);
                    if (!name) {{
                        var lines = text.split(/\\n+/).map(function(x){{return x.trim();}}).filter(Boolean);
                        name = lines.length ? lines[0] : '';
                    }}
                    var shop = pickText(card, ['[class*="shop"]','[class*="store"]','[class*="merchant"]']);
                    var idv = extractId(card);
                    var price = firstPrice(text);
                    var key = norm(name) + '|' + idv + '|' + price;
                    if (seenKey[key]) continue;
                    seenKey[key] = true;
                    candidates.push({{
                        index: candidates.length, name: name || '', shop: shop || '',
                        price: price || '', id: idv || '', text: text.slice(0, 200),
                    }});
                    if (candidates.length >= 30) break;
                }}
                var targetName = norm({json.dumps(target_name)});
                var targetId = String({json.dumps(target_id)}).trim();
                var chosen = null;
                var rule = '';
                if (targetId) {{
                    for (var a = 0; a < candidates.length; a++) {{
                        if (String(candidates[a].id || '').trim() === targetId) {{ chosen = candidates[a]; rule = 'product_id_exact'; break; }}
                    }}
                }}
                if (!chosen && targetName) {{
                    for (var b = 0; b < candidates.length; b++) {{
                        if (norm(candidates[b].name) === targetName) {{ chosen = candidates[b]; rule = 'product_name_exact'; break; }}
                    }}
                }}
                if (!chosen && targetName) {{
                    for (var d = 0; d < candidates.length; d++) {{
                        var cn = norm(candidates[d].name);
                        if (cn && (cn.indexOf(targetName) >= 0 || targetName.indexOf(cn) >= 0)) {{ chosen = candidates[d]; rule = 'product_name_fuzzy_contains'; break; }}
                    }}
                }}
                if (!chosen) {{
                    return {{ ok: false, reason: 'no_match_candidate', target: {{ name: {json.dumps(target_name)}, id: {json.dumps(target_id)} }}, candidates: candidates }};
                }}
                var chosenNode = cards[chosen.index] || null;
                if (!chosenNode) {{
                    return {{ ok: false, reason: 'chosen_node_missing', target: {{ name: {json.dumps(target_name)}, id: {json.dumps(target_id)} }}, candidates: candidates, matched: chosen, rule: rule }};
                }}
                var checkbox = chosenNode.querySelector('input[type="checkbox"],input[type="radio"],.semi-checkbox,.d-checkbox');
                var clickTarget = checkbox || chosenNode;
                clickTarget.scrollIntoView({{block: 'center'}});
                await sleep(400);
                var rect = clickTarget.getBoundingClientRect();
                var selectedCountBefore = (document.body.innerText.match(/已选择\\s*\\d+\\s*项/) || [''])[0];
                return {{
                    ok: true, phase: 'checkbox_ready',
                    checkbox_found: !!checkbox,
                    checkbox_rect: {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}},
                    selected_count_before: selectedCountBefore,
                    candidates: candidates, matched: chosen, rule: rule,
                }};
            }})()
            """
        )
        if not isinstance(result, dict):
            result = {"ok": False, "reason": "invalid_result_type"}
        _phase1_ok = bool(result.get("ok"))
        _cands = result.get("candidates") or []
        _matched = result.get("matched") or {}
        _rule = str(result.get("rule") or "")

        if not _phase1_ok:
            print(f"[cdp_publish] 商品匹配失败: {result.get('reason', '未知')}")
            if strict:
                raise CDPError(f"商品匹配失败: {result.get('reason', '未知')}")
            return result

        # === Phase 2: CDP鼠标事件点击复选框 ===
        _selected_before = str(result.get("selected_count_before") or "")
        if "1项" in _selected_before:
            print(f"[cdp_publish] 复选框已选中，跳过 CDP 点击")
        else:
            cr = result["checkbox_rect"]
            cx, cy = cr["x"] + cr["w"] / 2, cr["y"] + cr["h"] / 2
            print(f"[cdp_publish] CDP点击复选框 ({cx:.0f}, {cy:.0f})")
            self._click_mouse(cx, cy)
            self._sleep(0.5, minimum_seconds=0.3)

        # === Phase 3: 验证复选框选中状态，查找保存按钮坐标 ===
        verify = self._evaluate(
            """
            (function() {
                var t = document.body.innerText || '';
                var m = t.match(/已选择\\s*\\d+\\s*项/);
                var after = m ? m[0] : '';
                var checkedEl = document.querySelector('input[type="checkbox"]:checked, .semi-checkbox.checked, [class*="checkbox-checked"], [class*="semi-checkbox-checked"]');
                var isChecked = !!checkedEl;
                var btns = document.querySelectorAll('button,[role="button"],.semi-button,.d-button');
                var saveBtn = null;
                for (var i = 0; i < btns.length; i++) {
                    var txt = (btns[i].innerText || '').trim();
                    if (txt.indexOf('保存') >= 0) {
                        var st = window.getComputedStyle(btns[i]);
                        if (st.display !== 'none' && st.visibility !== 'hidden' && parseInt(st.width) > 10) {
                            saveBtn = btns[i]; break;
                        }
                    }
                }
                if (!saveBtn) {
                    return JSON.stringify({ok: false, selected_count_after: after, checkbox_checked: isChecked, save_found: false});
                }
                saveBtn.scrollIntoView({block: 'center'});
                var sr = saveBtn.getBoundingClientRect();
                return JSON.stringify({
                    ok: true, selected_count_after: after, checkbox_checked: isChecked,
                    save_found: true,
                    save_rect: {x: sr.x, y: sr.y, w: sr.width, h: sr.height}
                });
            })()
            """
        )
        if not isinstance(verify, str):
            verify = '{"ok": false, "selected_count_after": "", "checkbox_checked": false, "save_found": false}'
        verify = json.loads(verify)
        _after = str(verify.get("selected_count_after") or "")
        _checked = bool(verify.get("checkbox_checked"))
        _save_found = bool(verify.get("save_found"))

        # 如果复选框未选中且计数未变，返回失败
        if not _checked and "1项" not in _after:
            print(f"[cdp_publish] 复选框CDP点击失败: count='{_after}' checked={_checked}")
            return {
                "ok": False, "reason": "checkbox_cdp_click_failed",
                "target": {"name": target_name, "id": target_id},
                "candidates": _cands, "matched": _matched, "rule": _rule,
                "checkbox_found": bool(result.get("checkbox_found")),
                "checkbox_clicked": _checked,
                "selected_count_before": str(result.get("selected_count_before") or ""),
                "selected_count_after": _after,
                "save_button_found": _save_found, "save_clicked": False,
            }

        _save_success = False

        if not _save_found:
            # 弹窗中找不到"保存"按钮 -> 检查商品是否已挂载（幂等情况）
            _matched_name = str(_matched.get("name") or "")
            already_mounted = self._evaluate(
                f"""
                (function() {{
                    var section = document.querySelector(
                        '[class*="publish-form"],[class*="creator-form"],' +
                        '[class*="publish-container"],[class*="publish-content"]'
                    );
                    var text = section ? (section.innerText || '') : (document.body.innerText || '');
                    if (!text) return false;
                    var names = {json.dumps([_matched_name]) if _matched_name else '[]'};
                    for (var i = 0; i < names.length; i++) {{
                        if (names[i] && text.indexOf(names[i]) >= 0) return true;
                    }}
                    return false;
                }})()
                """
            )
            if already_mounted:
                _save_success = True
                _modal_closed = True
                _has_product = True
                print(f"[cdp_publish] 商品已挂载，无需重新保存")
            else:
                return {
                    "ok": False, "reason": "save_button_not_found",
                    "target": {"name": target_name, "id": target_id},
                    "candidates": _cands, "matched": _matched, "rule": _rule,
                    "checkbox_found": True, "checkbox_clicked": True,
                    "selected_count_before": str(result.get("selected_count_before") or ""),
                    "selected_count_after": _after,
                    "save_button_found": False, "save_clicked": False,
                }

        # === Phase 4: CDP鼠标事件点击保存 + 分层 fallback ===
        if _save_success:
            print(f"[cdp_publish] 保存已无需执行（商品已挂载）")
        else:
            sr = verify["save_rect"]
            sx, sy = sr["x"] + sr["w"] / 2, sr["y"] + sr["h"] / 2
            # 点击前重新滚动并获取最新坐标（防止弹窗内部滚动导致坐标偏移）
            save_btn_refresh = self._evaluate(
            """
            (function() {
                var btns = document.querySelectorAll('button,[role="button"],.semi-button,.d-button');
                for (var i = 0; i < btns.length; i++) {
                    var txt = (btns[i].innerText || '').trim();
                    if (txt.indexOf('保存') >= 0) {
                        var st = window.getComputedStyle(btns[i]);
                        if (st.display !== 'none' && st.visibility !== 'hidden' && parseInt(st.width) > 10) {
                            btns[i].scrollIntoView({block: 'center'});
                            var r = btns[i].getBoundingClientRect();
                            var cx = r.x + r.width/2, cy = r.y + r.height/2;
                            var hit = document.elementFromPoint(cx, cy);
                            var hitTag = hit ? (hit.tagName || '') : '';
                            var hitText = hit ? (hit.innerText || '').trim().slice(0, 30) : '';
                            var isHit = hit === btns[i] || btns[i].contains(hit);
                            return JSON.stringify({
                                x: cx, y: cy, w: r.width, h: r.height,
                                elementFromPoint: {tag: hitTag, text: hitText, isTarget: isHit}
                            });
                        }
                    }
                }
                return '{}';
            })()
            """
        )
            if isinstance(save_btn_refresh, str):
                try:
                    save_btn_refresh = json.loads(save_btn_refresh)
                except Exception:
                    save_btn_refresh = None

            _save_success = False

            if save_btn_refresh and save_btn_refresh.get("x", 0) > 0:
                sx, sy = float(save_btn_refresh["x"]), float(save_btn_refresh["y"])
                element_info = save_btn_refresh.get("elementFromPoint", {})
                hit_target = bool(element_info.get("isTarget"))
                print(f"[cdp_publish] 保存按钮坐标 ({sx:.0f}, {sy:.0f}) "
                      f"elementFromPoint: tag={element_info.get('tag','?')} isTarget={hit_target}")

                # 第一层: 键盘 Enter（最有效，优先尝试）
                print(f"[cdp_publish] 保存: 尝试键盘 Enter")
                self._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
                self._send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
                self._sleep(0.5, minimum_seconds=0.3)
                for _ in range(6):
                    still_open = self._evaluate(
                        "!!document.querySelector('.d-modal-mask,[class*=\"goods-select\"],[class*=\"multi-goods\"]')"
                    )
                    if not still_open:
                        _save_success = True
                        break
                    self._sleep(0.5, minimum_seconds=0.3)

                # 第二层: CDP 鼠标点击回退
                if not _save_success:
                    print(f"[cdp_publish] 保存: Enter 未关闭弹窗，尝试 CDP 鼠标点击")
                    self._click_mouse(sx, sy)
                    self._sleep(0.5, minimum_seconds=0.3)
                    # 轮询弹窗是否关闭
                    for _ in range(6):
                        still_open = self._evaluate(
                            "!!document.querySelector('.d-modal-mask,[class*=\"goods-select\"],[class*=\"multi-goods\"]')"
                        )
                        if not still_open:
                            _save_success = True
                            break
                        self._sleep(0.5, minimum_seconds=0.3)

                # 第三层: JS .click() 回退
                if not _save_success:
                    js_ok = self._evaluate(
                        r"""(function() {
                            var btns = document.querySelectorAll('button,[role="button"],.semi-button,.d-button');
                            for (var i = 0; i < btns.length; i++) {
                                var txt = (btns[i].innerText || '').trim();
                                if (txt.indexOf('保存') >= 0) {
                                    var st = window.getComputedStyle(btns[i]);
                                    if (st.display !== 'none' && st.visibility !== 'hidden') {
                                        btns[i].scrollIntoView({block: 'center'});
                                        btns[i].click();
                                        return true;
                                    }
                                }
                            }
                            return false;
                        })()"""
                    )
                    if js_ok:
                        self._sleep(0.5, minimum_seconds=0.3)
                        for _ in range(6):
                            still_open = self._evaluate(
                                "!!document.querySelector('.d-modal-mask,[class*=\"goods-select\"],[class*=\"multi-goods\"]')"
                            )
                            if not still_open:
                                _save_success = True
                                break
                            self._sleep(0.5, minimum_seconds=0.3)
        if _save_success:
            print(f"[cdp_publish] 保存成功，弹窗已关闭")
            self._sleep(0.5, minimum_seconds=0.3)
        else:
            print(f"[cdp_publish] 保存失败，弹窗未关闭")
            try:
                img = self._send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
                if img and "data" in img:
                    import base64
                    path = "tmp/save_btn_fail.png"
                    with open(path, "wb") as f:
                        f.write(base64.b64decode(img["data"]))
                    print(f"[cdp_publish] 保存失败截图: {path}")
            except Exception:
                pass

        # === Phase 5: 验证弹窗关闭 + 发布页商品区域 ===
        _matched_name = str(_matched.get("name") or "")
        _product_check_names = json.dumps([_matched_name] if _matched_name else [])
        final = self._evaluate(
            f"""
            (function() {{
                var t = document.body.innerText || '';
                var selectedMatch = (t.match(/已选择\\s*\\d+\\s*项/) || [''])[0];
                // 检查弹窗/覆盖层
                var overlays = document.querySelectorAll(
                    '[class*="dialog"],[class*="modal"],[class*="overlay"],'
                    + '[class*="popup"],[class*="drawer"],[aria-modal="true"]'
                );
                var modalFound = false;
                for (var i = 0; i < overlays.length; i++) {{
                    var st = window.getComputedStyle(overlays[i]);
                    var w = parseInt(st.width) || 0;
                    var h = parseInt(st.height) || 0;
                    if (st.display !== 'none' && st.visibility !== 'hidden' && w > 100 && h > 50) {{
                        modalFound = true; break;
                    }}
                }}
                // 检查保存按钮是否还在
                var saveStillVisible = false;
                var btns = document.querySelectorAll('button,[role="button"]');
                for (var i = 0; i < btns.length; i++) {{
                    var txt = (btns[i].innerText || '').trim();
                    if (txt === '保存') {{
                        var st = window.getComputedStyle(btns[i]);
                        if (st.display !== 'none' && st.visibility !== 'hidden') {{
                            saveStillVisible = true; break;
                        }}
                    }}
                }}
                // 只在发布页商品区域检查商品名（不在弹窗内）
                var publishSection = document.querySelector(
                    '[class*="publish-form"],[class*="creator-form"],'
                    + '[class*="publish-container"],[class*="publish-content"]'
                );
                var sectionText = publishSection ? (publishSection.innerText || '') : '';
                // 如果没找到指定区域，用 body 文本并排除弹窗区域
                if (!sectionText) {{
                    var modalEl = overlays.length > 0 ? overlays[0] : null;
                    var bodyText = document.body.innerText || '';
                    sectionText = bodyText;
                    if (modalEl) {{
                        var modalText = modalEl.innerText || '';
                        if (modalText && bodyText.indexOf(modalText) >= 0 && bodyText.length - modalText.length < 200) {{
                            sectionText = '';
                        }}
                    }}
                }}
                var productNames = {_product_check_names};
                var foundProduct = '';
                for (var i = 0; i < productNames.length; i++) {{
                    if (sectionText.indexOf(productNames[i]) >= 0) {{
                        foundProduct = productNames[i]; break;
                    }}
                }}
                return JSON.stringify({{
                    modal_closed: !modalFound,
                    no_save_button_visible: !saveStillVisible,
                    selected_count: selectedMatch,
                    has_product_in_section: !!foundProduct,
                    product_name_found: foundProduct,
                }});
            }})()
            """
        )
        if not isinstance(final, str):
            final = '{"modal_closed": false, "no_save_button_visible": false}'
        final = json.loads(final)
        _modal_closed = bool(final.get("modal_closed"))
        _no_save = bool(final.get("no_save_button_visible"))
        _final_selected = str(final.get("selected_count") or "")
        _has_product = bool(final.get("has_product_in_section"))

        # 成功条件：弹窗关闭 + 保存按钮消失 + 发布页区域有商品名
        product_ok = _modal_closed and _no_save and _has_product
        # mounted 独立于 product_ok：只要商品卡片出现在发布页就算挂载
        mounted = _has_product
        result = {
            "ok": product_ok,
            "target": {"name": target_name, "id": target_id},
            "candidates": _cands, "matched": _matched, "rule": _rule,
            "mounted": mounted,
            "checkbox_found": bool(result.get("checkbox_found")),
            "checkbox_clicked": _checked,
            "selected_count_before": str(result.get("selected_count_before") or ""),
            "selected_count_after": _final_selected or _after,
            "save_button_found": _save_found,
            "save_clicked": _no_save,
            "modal_closed_after_save": _modal_closed,
            "product_attached_on_publish_page": _has_product,
        }
        _verify_fields = {k: result.get(k) for k in [
            "checkbox_found", "checkbox_clicked",
            "selected_count_before", "selected_count_after",
            "save_button_found", "save_clicked",
            "modal_closed_after_save", "product_attached_on_publish_page",
        ]}
        print(f"[cdp_publish] 商品选择验证: {json.dumps(_verify_fields, ensure_ascii=False)}")
        if not product_ok and strict:
            raise CDPError(f"商品选择失败: {_verify_fields}")
        return result

    def attach_product_link(self, product_link: str):
        """兼容包装：处理 --product-link 参数，防止 AttributeError。

        【警告】此函数是 API 兼容占位，当前不会实际挂载任何商品。
        商品选择请使用 click_add_product + select_product_with_match（--product-name / --product-id）。
        --product-link 功能尚未接入，此处仅做日志记录。
        """
        print(f"[cdp_publish] attach_product_link called (product_link={product_link})")
        print("[cdp_publish] ⚠️  兼容占位：attach_product_link 不会实际挂载商品。")
        print("[cdp_publish] ⚠️  如需商品选择，请使用 --product-name / --product-id。")
        print("[cdp_publish] Note: --product-link is logged but not yet implemented.")

    def select_product_once(self, strict=False):
        """兼容旧调用：选择第一个可见商品（用于 legacy 模式）"""
        result = self._evaluate(
            """
            (function() {
                var cards = document.querySelectorAll('[class*="goods"],[class*="product"],[class*="item"]');
                for (var i = 0; i < cards.length; i++) {
                    var cb = cards[i].querySelector('input[type="checkbox"],.semi-checkbox,[class*="checkbox"]');
                    if (cb) {
                        var r = cb.getBoundingClientRect();
                        return JSON.stringify({found: true, x: r.x + r.width/2, y: r.y + r.height/2});
                    }
                }
                return JSON.stringify({found: false});
            })()
            """
        )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        if result and result.get("found"):
            self._click_mouse(result["x"], result["y"])
            self._sleep(0.5, minimum_seconds=0.3)
            print("[cdp_publish] select_product_once: 已选择第一个商品")
            return True
        print("[cdp_publish] select_product_once: 未找到商品")
        return False

    # ------------------------------------------------------------------
    # Main publish workflow
    # ------------------------------------------------------------------

    def publish(
        self,
        title: str,
        content: str,
        image_paths: list[str] | None = None,
        post_time: str | None = None,
    ):
        """
        Execute the full publish workflow:
        1. Navigate to creator publish page
        2. Click '上传图文' tab
        3. Upload images (this triggers the editor to appear)
        4. Fill title
        5. Fill content
        6. Set schedule publish time (if necessary)

        Args:
            title: Article title
            content: Article body text (paragraphs separated by newlines)
            image_paths: List of local file paths to images to upload
            post_time: Optional scheduled publish time (e.g. "2026-03-01 10:00")
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        if not image_paths:
            raise CDPError("At least one image is required to publish on Xiaohongshu.")
        
        if post_time and not validate_schedule_post_time(post_time):
            raise CDPError(
                "Scheduled publish time is invalid. "
                "It must follow the format 'yyyy-MM-dd HH:mm' and fall within the next 14 days."
            )

        # Step 1: Navigate to publish page
        self._navigate(XHS_CREATOR_URL)
        self._sleep(2, minimum_seconds=1.0)

        # Step 2: Click '上传图文' tab
        self._click_image_text_tab()

        # Step 3: Upload images (editor appears after upload)
        self._upload_images(image_paths)

        # Step 4: Fill title
        self._fill_title(title)

        # Step 5: Fill content
        self._fill_content(content)

        # Step 6: Set schedule publish time (if provided)
        self._set_schedule_post_time(post_time)

        print(
            "\n[cdp_publish] Content has been filled in.\n"
            "  Please review in the browser before publishing.\n"
        )

    def publish_video(
        self,
        title: str,
        content: str,
        video_path: str,
    ):
        """
        Execute the full video publish workflow:
        1. Navigate to creator publish page
        2. Click '上传视频' tab
        3. Upload video file and wait for processing
        4. Fill title
        5. Fill content

        Args:
            title: Article title
            content: Article body text (paragraphs separated by newlines)
            video_path: Local file path to the video to upload
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        if not video_path:
            raise CDPError("A video file is required to publish video on Xiaohongshu.")

        # Step 1: Navigate to publish page
        self._navigate(XHS_CREATOR_URL)
        time.sleep(2)

        # Step 2: Click '上传视频' tab
        self._click_video_tab()

        # Step 3: Upload video and wait for processing
        self._upload_video(video_path)
        self._wait_video_processing()

        # Step 4: Fill title
        self._fill_title(title)

        # Step 5: Fill content
        self._fill_content(content)

        print(
            "\n[cdp_publish] Video content has been filled in.\n"
            "  Please review in the browser before publishing.\n"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    from chrome_launcher import ensure_chrome, restart_chrome

    parser = argparse.ArgumentParser(description="Xiaohongshu CDP Publisher")
    parser.add_argument(
        "--host",
        default=CDP_HOST,
        help=f"CDP host (default: {CDP_HOST})",
    )
    parser.add_argument("--port", type=int, default=CDP_PORT,
                        help=f"CDP remote debugging port (default: {CDP_PORT})")
    parser.add_argument("--headless", action="store_true",
                        help="Use headless Chrome (no GUI window)")
    parser.add_argument("--account", help="Account name to use (default: default account)")
    parser.add_argument(
        "--timing-jitter",
        type=float,
        default=0.25,
        help=(
            "Timing jitter ratio for operation delays (default: 0.25). "
            "Set 0 to disable random jitter."
        ),
    )
    parser.add_argument(
        "--reuse-existing-tab",
        action="store_true",
        help=(
            "Prefer reusing an existing tab before creating a new one. "
            "Useful in headed mode to reduce foreground focus switching."
        ),
    )
    parser.add_argument(
        "--preserve-upload-paths",
        action="store_true",
        help=(
            "Force preserving original upload file paths instead of converting "
            "backslashes to forward slashes before DOM.setFileInputFiles. "
            "Windows/UNC paths are auto-detected by default."
        ),
    )
    parser.add_argument(
        "--disconnect-cdp",
        action="store_true",
        help=(
            "Close the DevTools WebSocket when exiting. "
            "Default leaves the browser tab unchanged (recommended for manual follow-up)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check-login
    sub.add_parser("check-login", help="Check login status (exit 0=logged in, 1=not)")

    p_qrcode = sub.add_parser(
        "get-login-qrcode",
        aliases=["get_login_qrcode"],
        help="Get login QR code image payload for remote display",
    )
    p_qrcode.add_argument(
        "--wait-seconds",
        type=float,
        default=20.0,
        help="Seconds to wait for QR code to appear (default: 20)",
    )

    # fill - fill form without clicking publish
    p_fill = sub.add_parser("fill", help="Fill title/content/images or video without publishing")
    p_fill.add_argument("--title", required=True)
    p_fill.add_argument("--content", default=None)
    p_fill.add_argument("--content-file", default=None, help="Read content from file")
    p_fill_media = p_fill.add_mutually_exclusive_group(required=True)
    p_fill_media.add_argument("--images", nargs="+", help="Local image file paths")
    p_fill_media.add_argument("--video", help="Local video file path")

    # publish - fill form and click publish
    p_pub = sub.add_parser("publish", help="Fill form and click publish")
    p_pub.add_argument("--title", required=True)
    p_pub.add_argument("--content", default=None)
    p_pub.add_argument("--content-file", default=None, help="Read content from file")
    p_pub_media = p_pub.add_mutually_exclusive_group(required=True)
    p_pub_media.add_argument("--images", nargs="+", help="Local image file paths")
    p_pub_media.add_argument("--video", help="Local video file path")

    # click-publish - just click the publish button on current page
    sub.add_parser("click-publish", help="Click publish button on already-filled page")

    # screenshot - capture full-viewport screenshot
    p_ss = sub.add_parser("screenshot", help="Capture full-viewport screenshot to PNG")
    p_ss.add_argument("--output", required=True, help="Output PNG file path")

    # navigate - navigate to URL
    p_nav = sub.add_parser("navigate", help="Navigate to a URL")
    p_nav.add_argument("--url", required=True, help="Target URL")

    p_list_feeds = sub.add_parser(
        "list-feeds",
        aliases=["list_feeds"],
        help="Get home recommendation feeds",
    )

    # search-feeds - search note feeds by keyword
    p_search = sub.add_parser(
        "search-feeds",
        aliases=["search_feeds"],
        help="Search Xiaohongshu feeds by keyword",
    )
    p_search.add_argument("--keyword", required=True, help="Search keyword")
    p_search.add_argument("--sort-by", choices=SORT_BY_OPTIONS, help="Sort by option")
    p_search.add_argument("--note-type", choices=NOTE_TYPE_OPTIONS, help="Note type filter")
    p_search.add_argument(
        "--publish-time",
        choices=PUBLISH_TIME_OPTIONS,
        help="Publish time filter",
    )
    p_search.add_argument(
        "--search-scope",
        choices=SEARCH_SCOPE_OPTIONS,
        help="Search scope filter",
    )
    p_search.add_argument("--location", choices=LOCATION_OPTIONS, help="Location filter")

    # get-feed-detail - get note detail by feed id and token
    p_detail = sub.add_parser(
        "get-feed-detail",
        aliases=["get_feed_detail"],
        help="Get feed detail by feed id and xsec token",
    )
    p_detail.add_argument("--feed-id", required=True, help="Feed id")
    p_detail.add_argument("--xsec-token", required=True, help="xsec token")
    p_detail.add_argument(
        "--load-all-comments",
        action="store_true",
        help="Scroll to load more top-level comments before extracting detail",
    )
    p_detail.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Target max number of top-level comments to load (default: 20)",
    )
    p_detail.add_argument(
        "--click-more-replies",
        action="store_true",
        help="Try to expand visible reply groups while loading comments",
    )
    p_detail.add_argument(
        "--reply-limit",
        type=int,
        default=10,
        help="Skip expanding reply groups above this size when possible (default: 10)",
    )
    p_detail.add_argument(
        "--scroll-speed",
        choices=("slow", "normal", "fast"),
        default="normal",
        help="Comment loading scroll speed (default: normal)",
    )

    # post-comment-to-feed - post top-level comment to feed detail
    p_comment = sub.add_parser(
        "post-comment-to-feed",
        aliases=["post_comment_to_feed"],
        help="Post a top-level comment to feed detail",
    )
    p_comment.add_argument("--feed-id", required=True, help="Feed id")
    p_comment.add_argument("--xsec-token", required=True, help="xsec token")
    p_comment_content = p_comment.add_mutually_exclusive_group(required=True)
    p_comment_content.add_argument("--content", help="Comment content")
    p_comment_content.add_argument("--content-file", help="Read comment content from file")

    # respond-comment - reply to an existing comment on feed detail
    p_reply = sub.add_parser(
        "respond-comment",
        aliases=["respond_comment"],
        help="Reply to an existing comment on feed detail",
    )
    p_reply.add_argument("--feed-id", required=True, help="Feed id")
    p_reply.add_argument("--xsec-token", required=True, help="xsec token")
    p_reply_content = p_reply.add_mutually_exclusive_group(required=True)
    p_reply_content.add_argument("--content", help="Reply content")
    p_reply_content.add_argument("--content-file", help="Read reply content from file")
    p_reply.add_argument("--comment-id", help="Target comment id")
    p_reply.add_argument("--comment-author", help="Target comment author name (fuzzy match)")
    p_reply.add_argument("--comment-snippet", help="Target comment text snippet (fuzzy match)")

    # profile-snapshot - read user profile summary
    p_profile = sub.add_parser(
        "profile-snapshot",
        aliases=["profile_snapshot"],
        help="Get user profile snapshot from profile page",
    )
    p_profile_target = p_profile.add_mutually_exclusive_group(required=True)
    p_profile_target.add_argument("--profile-url", help="Full profile URL")
    p_profile_target.add_argument("--user-id", help="User id for profile URL composition")

    # notes-from-profile - list notes from user profile page
    p_profile_notes = sub.add_parser(
        "notes-from-profile",
        aliases=["notes_from_profile"],
        help="List notes from profile page",
    )
    p_profile_notes_target = p_profile_notes.add_mutually_exclusive_group(required=True)
    p_profile_notes_target.add_argument("--profile-url", help="Full profile URL")
    p_profile_notes_target.add_argument("--user-id", help="User id for profile URL composition")
    p_profile_notes.add_argument("--limit", type=int, default=20, help="Max notes to return (default: 20)")
    p_profile_notes.add_argument(
        "--max-scrolls",
        type=int,
        default=3,
        help="Extra scroll rounds for lazy-loaded notes (default: 3)",
    )

    # note-upvote / note-unvote - toggle like state
    p_upvote = sub.add_parser(
        "note-upvote",
        aliases=["note_upvote"],
        help="Set note to upvoted state",
    )
    p_upvote.add_argument("--feed-id", required=True, help="Feed id")
    p_upvote.add_argument("--xsec-token", required=True, help="xsec token")

    p_unvote = sub.add_parser(
        "note-unvote",
        aliases=["note_unvote"],
        help="Set note to not-upvoted state",
    )
    p_unvote.add_argument("--feed-id", required=True, help="Feed id")
    p_unvote.add_argument("--xsec-token", required=True, help="xsec token")

    # note-bookmark / note-unbookmark - toggle favorite state
    p_bookmark = sub.add_parser(
        "note-bookmark",
        aliases=["note_bookmark"],
        help="Set note to bookmarked state",
    )
    p_bookmark.add_argument("--feed-id", required=True, help="Feed id")
    p_bookmark.add_argument("--xsec-token", required=True, help="xsec token")

    p_unbookmark = sub.add_parser(
        "note-unbookmark",
        aliases=["note_unbookmark"],
        help="Set note to not-bookmarked state",
    )
    p_unbookmark.add_argument("--feed-id", required=True, help="Feed id")
    p_unbookmark.add_argument("--xsec-token", required=True, help="xsec token")

    # get-notification-mentions - capture notification mentions API response
    p_mentions = sub.add_parser(
        "get-notification-mentions",
        aliases=["get_notification_mentions"],
        help="Capture notification mentions API payload from /notification page",
    )
    p_mentions.add_argument(
        "--wait-seconds",
        type=float,
        default=18.0,
        help="Max seconds to wait for mentions API request (default: 18)",
    )

    # content-data - fetch creator content data table
    p_content_data = sub.add_parser(
        "content-data",
        aliases=["content_data"],
        help="Fetch creator content data table from statistics page",
    )
    p_content_data.add_argument(
        "--page-num",
        type=int,
        default=1,
        help="Page number (default: 1)",
    )
    p_content_data.add_argument(
        "--page-size",
        type=int,
        default=10,
        help="Page size (default: 10)",
    )
    p_content_data.add_argument(
        "--type",
        dest="note_type",
        type=int,
        default=0,
        help="Type filter value used by API (default: 0)",
    )
    p_content_data.add_argument(
        "--csv-file",
        help="Optional CSV output path",
    )

    # login - open browser for QR code login (always headed)
    sub.add_parser("login", help="Open browser for QR code login (always headed mode)")

    # re-login - clear cookies and re-login the same account (always headed)
    sub.add_parser("re-login", help="Clear cookies and re-login same account (always headed)")

    # switch-account - clear cookies and open login page (always headed)
    sub.add_parser("switch-account",
                   help="Clear cookies and open login page for new account (always headed)")

    # list-accounts - list all configured accounts
    sub.add_parser("list-accounts", help="List all configured accounts")

    # add-account - add a new account
    p_add = sub.add_parser("add-account", help="Add a new account")
    p_add.add_argument("name", help="Account name (unique identifier)")
    p_add.add_argument("--alias", help="Display name / description")
    p_add.add_argument("--proxy", help="Proxy URL, e.g. http://user:pass@host:port")
    p_add.add_argument("--port", type=int, help="Preferred local CDP port for this account")
    p_add.add_argument("--group", help="Optional group tag for batch scheduling")

    # remove-account - remove an account
    p_rm = sub.add_parser("remove-account", help="Remove an account")
    p_rm.add_argument("name", help="Account name to remove")
    p_rm.add_argument("--delete-profile", action="store_true",
                      help="Also delete the Chrome profile directory")

    # set-default-account - set default account
    p_def = sub.add_parser("set-default-account", help="Set the default account")
    p_def.add_argument("name", help="Account name to set as default")

    args = parser.parse_args()
    host = args.host
    port = args.port
    port_explicit = "--port" in sys.argv
    headless = args.headless
    account = args.account
    cache_account_name = _resolve_account_name(account)
    reuse_existing_tab = args.reuse_existing_tab
    timing_jitter = _normalize_timing_jitter(args.timing_jitter)
    local_mode = _is_local_host(host)

    if timing_jitter != args.timing_jitter:
        print(
            "[cdp_publish] Warning: --timing-jitter out of range. "
            f"Clamped to {timing_jitter:.2f}."
        )
    # Account management commands that don't need Chrome
    if args.command == "list-accounts":
        from account_manager import list_accounts
        accounts = list_accounts()
        if not accounts:
            print("No accounts configured.")
            return
        print(f"{'Name':<20} {'Alias':<20} {'Default':<8} {'Port':<6} {'Proxy':<7} {'Group':<12}")
        print("-" * 90)
        for acc in accounts:
            default_mark = "*" if acc["is_default"] else ""
            port_mark = str(acc.get("port") or "-")
            proxy_mark = "yes" if acc.get("proxy") else "no"
            group_mark = acc.get("group") or "-"
            print(
                f"{acc['name']:<20} {acc['alias']:<20} {default_mark:<8} "
                f"{port_mark:<6} {proxy_mark:<7} {group_mark:<12}"
            )
        return

    elif args.command == "add-account":
        from account_manager import add_account, get_profile_dir
        if add_account(args.name, args.alias, args.proxy, args.port, args.group):
            print(f"Account '{args.name}' added.")
            print(f"Profile dir: {get_profile_dir(args.name)}")
            print("\nTo log in to this account, run:")
            print(f"  python cdp_publish.py --account {args.name} login")
        else:
            print(f"Error: Account '{args.name}' already exists.", file=sys.stderr)
            sys.exit(1)
        return

    elif args.command == "remove-account":
        from account_manager import remove_account
        if remove_account(args.name, args.delete_profile):
            print(f"Account '{args.name}' removed.")
        else:
            print(f"Error: Cannot remove account '{args.name}'.", file=sys.stderr)
            sys.exit(1)
        return

    elif args.command == "set-default-account":
        from account_manager import set_default_account
        if set_default_account(args.name):
            print(f"Default account set to '{args.name}'.")
        else:
            print(f"Error: Account '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)
        return

    if account and not port_explicit:
        try:
            from chrome_launcher import get_account_runtime

            rp = get_account_runtime(account).get("port")
            if isinstance(rp, int) and rp > 0:
                port = rp
                print(f"[cdp_publish] Using account preferred port: {port} ({account})")
        except Exception:
            pass

    # Commands that require Chrome - login/re-login/switch-account always headed
    if args.command in ("login", "re-login", "switch-account"):
        headless = False

    if local_mode:
        if not ensure_chrome(port=port, headless=headless, account=account):
            print("Failed to start Chrome. Exiting.")
            sys.exit(1)
    else:
        print(
            f"[cdp_publish] Remote CDP mode enabled: {host}:{port}. "
            "Skipping local Chrome launch/restart."
        )

    print(f"[cdp_publish] Timing jitter ratio: {timing_jitter:.2f}")
    print(f"[cdp_publish] Login cache: enabled (ttl={DEFAULT_LOGIN_CACHE_TTL_HOURS:g}h).")
    if reuse_existing_tab:
        print("[cdp_publish] Tab selection mode: prefer reusing existing tab.")

    publisher = XiaohongshuPublisher(
        host=host,
        port=port,
        timing_jitter=timing_jitter,
        account_name=cache_account_name,
        preserve_upload_paths=args.preserve_upload_paths,
    )
    try:
        if args.command == "check-login":
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            logged_in = publisher.check_login()
            if not logged_in and headless:
                print(
                    "[cdp_publish] Headless mode: cannot scan QR code.\n"
                    "  Run with 'login' command or without --headless to log in."
                )
            sys.exit(0 if logged_in else 1)

        elif args.command in ("get-login-qrcode", "get_login_qrcode"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            payload = publisher.get_login_qrcode(wait_seconds=args.wait_seconds)
            print("GET_LOGIN_QRCODE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("fill", "publish"):
            content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    content = f.read().strip()
            if not content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if getattr(args, "video", None):
                publisher.publish_video(
                    title=args.title, content=content, video_path=args.video
                )
            else:
                publisher.publish(
                    title=args.title, content=content, image_paths=args.images
                )
            print("FILL_STATUS: READY_TO_PUBLISH")

            if args.command == "publish":
                publisher._click_publish()
                print("PUBLISH_STATUS: PUBLISHED")

        elif args.command == "click-publish":
            publisher.connect(
                target_url_prefix="https://creator.xiaohongshu.com/publish",
                reuse_existing_tab=reuse_existing_tab,
            )
            publisher._click_publish()
            print("PUBLISH_STATUS: PUBLISHED")

        elif args.command == "screenshot":
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            ok = publisher.capture_screenshot(args.output)
            sys.exit(0 if ok else 1)

        elif args.command == "navigate":
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            ok = publisher.navigate(args.url)
            sys.exit(0 if ok else 1)

        elif args.command in ("list-feeds", "list_feeds"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.list_feeds()
            print("LIST_FEEDS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("search-feeds", "search_feeds"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            filters = _build_search_filters_from_args(args)
            search_result = publisher.search_feeds(keyword=args.keyword, filters=filters)
            feeds = search_result.get("feeds", [])
            recommended_keywords = search_result.get("recommended_keywords", [])
            payload = {
                "keyword": args.keyword,
                "recommended_keywords_count": len(recommended_keywords),
                "recommended_keywords": recommended_keywords,
                "count": len(feeds),
                "feeds": feeds,
            }
            print("SEARCH_FEEDS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("get-feed-detail", "get_feed_detail"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            detail_result = publisher.get_feed_detail(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                load_all_comments=args.load_all_comments,
                limit=args.limit,
                click_more_replies=args.click_more_replies,
                reply_limit=args.reply_limit,
                scroll_speed=args.scroll_speed,
            )
            payload = {
                "feed_id": args.feed_id,
                "xsec_token": args.xsec_token,
                "load_all_comments": args.load_all_comments,
                "comment_loading": detail_result.get("comment_loading"),
                "detail": detail_result.get("detail"),
            }
            print("GET_FEED_DETAIL_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("post-comment-to-feed", "post_comment_to_feed"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            comment_content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    comment_content = f.read().strip()
            if not comment_content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            payload = publisher.post_comment_to_feed(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                content=comment_content,
            )
            print("POST_COMMENT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("respond-comment", "respond_comment"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            reply_content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    reply_content = f.read().strip()
            if not reply_content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            payload = publisher.respond_comment(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                content=reply_content,
                comment_id=args.comment_id,
                comment_author=args.comment_author,
                comment_snippet=args.comment_snippet,
            )
            print("RESPOND_COMMENT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("profile-snapshot", "profile_snapshot"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_profile_snapshot(
                profile_url=args.profile_url,
                user_id=args.user_id,
            )
            print("PROFILE_SNAPSHOT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("notes-from-profile", "notes_from_profile"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.list_profile_notes(
                profile_url=args.profile_url,
                user_id=args.user_id,
                limit=args.limit,
                max_scrolls=args.max_scrolls,
            )
            print("PROFILE_NOTES_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-upvote", "note_upvote"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_upvote_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                upvoted=True,
            )
            print("NOTE_UPVOTE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-unvote", "note_unvote"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_upvote_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                upvoted=False,
            )
            print("NOTE_UNVOTE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-bookmark", "note_bookmark"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_bookmark_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                bookmarked=True,
            )
            print("NOTE_BOOKMARK_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-unbookmark", "note_unbookmark"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_bookmark_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                bookmarked=False,
            )
            print("NOTE_UNBOOKMARK_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("get-notification-mentions", "get_notification_mentions"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_notification_mentions(wait_seconds=args.wait_seconds)
            print("GET_NOTIFICATION_MENTIONS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("content-data", "content_data"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_content_data(
                page_num=args.page_num,
                page_size=args.page_size,
                note_type=args.note_type,
            )
            print("CONTENT_DATA_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

            if args.csv_file:
                csv_path = _write_content_data_csv(
                    csv_file=args.csv_file,
                    rows=payload.get("rows", []),
                )
                print(f"CONTENT_DATA_CSV: {csv_path}")

        elif args.command == "login":
            # Ensure headed mode for QR scanning
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.open_login_page()
            print("LOGIN_READY")

        elif args.command == "re-login":
            # Ensure headed mode, clear cookies, re-open login page for same account
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.clear_cookies()
            publisher._sleep(1, minimum_seconds=0.5)
            publisher.open_login_page()
            print("RE_LOGIN_READY")

        elif args.command == "switch-account":
            # Ensure headed mode, clear cookies, open login page
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.clear_cookies()
            publisher._sleep(1, minimum_seconds=0.5)
            publisher.open_login_page()
            print("SWITCH_ACCOUNT_READY")

    finally:
        if args.disconnect_cdp:
            publisher.disconnect()


if __name__ == "__main__":
    try:
        with single_instance("post_to_xhs_publish"):
            main()
    except SingleInstanceError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
