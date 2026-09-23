"""In-memory list of pending items with change notification.

Ported from 先回 (Android) `core/MsgStore.kt`, including its
multi-listener fix: Android originally used a single onChange slot which the
overlay and the main screen overwrote for each other. Here listeners are a
list, and notify() fans out to all of them.

Threading contract (important):
  The capture worker runs on a plain background thread, but every listener
  ultimately touches Qt widgets. Qt widgets may only be touched from the GUI
  thread - doing it from a worker causes dropped paints and hard freezes.
  So `_notify` never calls listeners directly on a foreign thread: it hands the
  work to a dispatcher installed by the UI layer (`set_dispatcher`), which
  marshals it onto the GUI thread. Without a dispatcher (tests, headless use)
  it falls back to calling inline.

Nothing is persisted - a restart starts empty, on purpose, so no chat history
is left behind.
"""

from __future__ import annotations

import threading
from typing import Callable

from .msg_item import Bucket, MsgItem

Listener = Callable[[], None]
Dispatcher = Callable[[Callable[[], None]], None]

_BUCKET_ORDER = {Bucket.NOW: 0, Bucket.SOON: 1, Bucket.LATER: 2, Bucket.PENDING: 3}

# 悬浮窗默认展示最近这么多条（用户要求 5 条）。
# 超出时挤掉最旧的一条，列表不会无限增长；判断结果属于各自那条消息，
# 所以同一发送者的多条会并排显示，不再互相覆盖。
MAX_ITEMS = 5


class MsgStore:
    def __init__(self) -> None:
        self._items: dict[str, MsgItem] = {}
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._dispatcher: Dispatcher | None = None
        self._gui_thread_id: int | None = None

    # -- thread marshalling -------------------------------------------------
    def set_dispatcher(self, dispatcher: Dispatcher | None) -> None:
        """Install the GUI-thread marshaller. Called once by the UI layer."""
        self._dispatcher = dispatcher

    def mark_gui_thread(self) -> None:
        """Record the GUI thread so we can tell when we are on it."""
        self._gui_thread_id = threading.get_ident()

    def _dispatch(self, fn: Callable[[], None]) -> None:
        if self._dispatcher is None or self._gui_thread_id == threading.get_ident():
            # No UI layer, or already on the GUI thread: just run it.
            fn()
        else:
            # On a worker thread: hand off to the GUI thread.
            self._dispatcher(fn)

    # -- listeners ---------------------------------------------------------
    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                self._dispatch(listener)
            except Exception:
                pass

    # -- data --------------------------------------------------------------
    def _trim_locked(self) -> None:
        """只保留最近 MAX_ITEMS 条。调用方必须已持锁。"""
        if len(self._items) <= MAX_ITEMS:
            return
        newest = sorted(self._items.values(), key=lambda i: i.time_ms, reverse=True)
        keep = {i.key for i in newest[:MAX_ITEMS]}
        self._items = {k: v for k, v in self._items.items() if k in keep}

    def upsert(self, item: MsgItem) -> bool:
        """Insert or refresh by key. Returns True if a new row appeared."""
        with self._lock:
            existing = self._items.get(item.key)
            if existing is None:
                self._items[item.key] = item
                is_new = True
            else:
                # Keep the row, refresh contents; preserve judgment only if
                # the text is unchanged (same text -> same verdict).
                if existing.text == item.text:
                    item.urgency = existing.urgency
                    item.needs_now = existing.needs_now
                    item.category = existing.category
                    item.reason = existing.reason
                    item.confidence = existing.confidence
                    item.judged = existing.judged
                    item.error = existing.error
                self._items[item.key] = item
                is_new = False
            self._trim_locked()
        self._notify()
        return is_new

    def put(self, item: MsgItem) -> None:
        """Replace without change detection (used after a judgment lands)."""
        with self._lock:
            self._items[item.key] = item
            self._trim_locked()
        self._notify()

    def remove(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)
        self._notify()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
        self._notify()

    def all(self) -> list[MsgItem]:
        """Sorted: most urgent bucket first, newest within a bucket."""
        with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda i: (_BUCKET_ORDER[i.bucket], -i.time_ms))
        return items

    def pending_judge(self) -> list[MsgItem]:
        with self._lock:
            return [i for i in self._items.values() if not i.judged and not i.error]

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def count_bucket(self, bucket: Bucket) -> int:
        with self._lock:
            return sum(1 for i in self._items.values() if i.bucket == bucket)

    def latest_text(self) -> str:
        """Last captured message text, for dedup against the capture layer."""
        with self._lock:
            if not self._items:
                return ""
            newest = max(self._items.values(), key=lambda i: i.time_ms)
            return newest.text


# Single shared instance for the whole process.
store = MsgStore()
