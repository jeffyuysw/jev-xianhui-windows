"""The capture -> parse -> judge loop, on a background thread.

Design notes:
  * Change detection uses an edge/structure signature, not a brightness mean.
    A mean cannot see a shift: scrolling a chat by one bubble can leave the
    average pixel value identical while the content moved completely. Comparing
    a coarse per-tile "ink" profile catches insertions, edits and scrolling.
  * Only the newest incoming message is judged, and only when its text differs
    from the last one we judged for that conversation.
  * Screenshots stay in memory; the only thing that leaves the machine is the
    message text, inside the judgment request, and only when auto_judge is on.
  * The loop never touches Qt. It writes to the store, and the store hands
    notifications to the GUI thread (see core/store.py).
"""

from __future__ import annotations

import threading
import time

import numpy as np

from core import msg_history as history
from core.jev_client import JevClient
from core.msg_item import MsgItem
from core.settings import Settings
from core.store import store

from . import ocr
from .parser import parse
from .window import capture_window, find_target_window

# Structural-change threshold, as a fraction of the frame's ink profile.
#
# Measured on a synthetic 800x500 chat window (see tools/diag_change.py):
#   noise floor (per-pixel sigma 1..5)   -> 0.0008 .. 0.0038
#   0.15% of the frame changed           -> 0.0253
#   0.30% of the frame changed           -> 0.0501
#   0.60% of the frame changed           -> 0.0978
# 0.03 leaves an ~8x margin over the noise floor while still catching a short
# one-word bubble (~0.15% of the frame). The earlier 0.06 needed ~0.3% of the
# frame to move, so brief messages were silently skipped.
CHANGE_THRESHOLD = 0.03

# 进程名 → 应用显示名。悬浮窗里显示它，点击某条消息时也靠它决定跳到哪个
# 应用，所以这里要把常见聊天工具都列上（微信 4.x 是 weixin.exe）。
_APP_BY_PROCESS = {
    "weixin.exe": "微信",
    "wechat.exe": "微信",
    "qq.exe": "QQ",
    "qqnt.exe": "QQ",
    "tim.exe": "TIM",
    "wework.exe": "企业微信",
    "wxwork.exe": "企业微信",
    "dingtalk.exe": "钉钉",
    "feishu.exe": "飞书",
    "lark.exe": "飞书",
}


def _app_label(win) -> str:
    """这条消息来自哪个应用。优先认进程名，标题只作兜底。"""
    proc = (getattr(win, "process", "") or "").lower()
    if proc in _APP_BY_PROCESS:
        return _APP_BY_PROCESS[proc]
    title = getattr(win, "title", "") or ""
    if "微信" in title or "WeChat" in title:
        return "微信"
    if "QQ" in title:
        return "QQ"
    return title or "聊天"


class CaptureWorker:
    def __init__(self, settings: Settings, on_status=None) -> None:
        self._settings = settings
        self._on_status = on_status or (lambda _msg: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_sig: dict[str, np.ndarray] = {}
        self._last_text: dict[str, str] = {}
        self.running = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_sig.clear()
        self._last_text.clear()
        self._thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self._thread.start()
        self.running = True
        self._on_status("正在监听微信窗口…")

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._on_status("已停止监听")

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _signature(img: np.ndarray) -> np.ndarray:
        """Coarse 'where is the ink' profile of the frame.

        Downscales to a small grayscale grid, then keeps the deviation from the
        frame mean. Comparing these profiles as a vector catches localised
        changes (a new bubble, an edit) *and* rigid shifts (scrolling), both of
        which a single mean value misses.
        """
        if img.ndim == 3:
            gray = img.mean(axis=2)
        else:
            gray = img.astype(np.float32)
        h, w = gray.shape
        # Squeeze to roughly 24x24 tiles without depending on an exact size.
        th, tw = max(1, h // 24), max(1, w // 24)
        cropped = gray[: th * 24, : tw * 24]
        if cropped.size == 0:
            return np.zeros(1, dtype=np.float32)
        tiles = cropped.reshape(24, th, 24, tw).mean(axis=(1, 3)).astype(np.float32)
        return tiles - tiles.mean()

    def _changed(self, key: str, img: np.ndarray) -> bool:
        sig = self._signature(img)
        prev = self._last_sig.get(key)
        self._last_sig[key] = sig
        if prev is None or prev.shape != sig.shape:
            return True
        # Normalised mean absolute difference; scale-free via the RMS of both.
        diff = float(np.abs(sig - prev).mean())
        scale = float(np.sqrt((sig**2).mean() + (prev**2).mean())) / 2.0
        if scale <= 1e-6:
            return diff > 0.5                      # both frames essentially blank
        return (diff / scale) > CHANGE_THRESHOLD

    def _loop(self) -> None:
        titles = self._settings.watch_titles
        procs = list(getattr(self._settings, "watch_processes", []) or [])
        while not self._stop.is_set():
            try:
                win = find_target_window(titles, procs)
                if win is None:
                    # 这条提示会显示在悬浮窗顶部：截屏方案要求窗口真实可见，
                    # 最小化/关闭时读不到画面。
                    self._on_status("微信窗口需要打开（请不要最小化和关闭）")
                    time.sleep(1.5)
                    continue
                # 窗口回来了，清掉提示条
                self._on_status("")

                img = capture_window(win)
                if img is None:
                    time.sleep(0.5)
                    continue

                key = win.title
                if not self._changed(key, img):
                    time.sleep(self._settings.poll_ms / 1000)
                    continue

                lines = ocr.run_ocr(img)
                parsed = parse(lines, win.width, img)
                if parsed is None:
                    time.sleep(self._settings.poll_ms / 1000)
                    continue

                if self._last_text.get(key) == parsed.text:
                    time.sleep(self._settings.poll_ms / 1000)
                    continue
                self._last_text[key] = parsed.text

                app_name = _app_label(win)
                item = MsgItem(sender=parsed.sender, text=parsed.text, app_name=app_name)

                # 先落一条「判断中」的本机记录，判断完成后回填结果。这样即使
                # 判断失败，分析记录里也能看出这条消息来过、只是没判成功。
                try:
                    item.history_id = history.insert_pending(item)
                    history.trim()
                except Exception as e:
                    self._on_status(f"记录写入失败：{e}")

                is_new = store.upsert(item)
                if is_new:
                    self._on_status(f"新消息来自 {parsed.sender}")

                if self._settings.auto_judge and item.error is None and not item.judged:
                    self._judge(item)

            except Exception as e:  # never let the loop die
                self._on_status(f"采集异常：{e}")
            time.sleep(self._settings.poll_ms / 1000)

    def _judge(self, item: MsgItem) -> None:
        s = self._settings
        if not s.api_key:
            item.error = "还没填 API Key"
            store.put(item)
            self._save_history(item)
            return
        try:
            JevClient(s.api_key, s.model, s.endpoint).judge(item)
            item.error = None
        except Exception as e:
            item.error = f"判断失败：{e}"
        store.put(item)
        self._save_history(item)

    def _save_history(self, item: MsgItem) -> None:
        """把判断结果回填到本机记录。失败不影响主流程。"""
        try:
            history.fill_result(item.history_id, item)
        except Exception:
            pass
