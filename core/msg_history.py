"""分析记录：判断过的每一条消息都留在本机 SQLite。

与 Android 版 `core/MsgHistoryDb.kt` 对齐 —— 同一套状态分档、同样的 2000 条
上限、同样的默认筛选「马上回」。

数据库放在 `%APPDATA%\\JevPriority\\history.db`（与 config.json 同目录），
只在本机，不联网、不上传；「分析记录」窗口里可一键清空。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

from .msg_item import MsgItem

# 最多保留多少条（用户要求的默认值）。超出时裁掉最旧的。
MAX_RECORDS = 2000
# 一页多少条（用户要求的默认值）。
PAGE_SIZE = 25

# 判断状态。数值写进数据库，改动时要同步 SQL 里的字面量。
STATUS_PENDING = 0
STATUS_JUDGED = 1
STATUS_FAILED = 2


class Filter(str, Enum):
    """记录页的筛选。分档条件与 MsgItem.bucket 保持一致。"""

    NOW = "马上回"
    SOON = "尽快"
    LATER = "可晚点"
    PENDING = "判断中"
    FAILED = "失败"
    ALL = "全部"

    @property
    def where_sql(self) -> str | None:
        """对应的 SQL 条件；None 表示不过滤。

        注意分档顺序：先判 needs_now 或 urgency>=7 为「马上回」，再 >=4 为
        「尽快」，其余为「可晚点」—— 与 MsgItem.bucket 完全一致，否则会出现
        「记录页说马上回、悬浮窗说尽快」这种自相矛盾。
        """
        return {
            Filter.NOW: "status = 1 AND (needs_now = 1 OR urgency >= 7)",
            Filter.SOON: "status = 1 AND needs_now = 0 AND urgency >= 4 AND urgency < 7",
            Filter.LATER: "status = 1 AND needs_now = 0 AND urgency < 4",
            Filter.PENDING: "status = 0",
            Filter.FAILED: "status = 2",
            Filter.ALL: None,
        }[self]


@dataclass
class Record:
    id: int
    app_name: str
    sender: str
    text: str
    status: int
    urgency: int
    needs_now: bool
    category: str
    reason: str
    error: str
    time_ms: int

    @property
    def bucket_label(self) -> str:
        if self.status == STATUS_FAILED:
            return "判断失败"
        if self.status == STATUS_PENDING:
            return "判断中"
        if self.needs_now or self.urgency >= 7:
            return "马上回"
        if self.urgency >= 4:
            return "尽快"
        return "可晚点"


def db_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "JevPriority")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "history.db")


class MsgHistory:
    """线程安全的记录库。采集线程写、界面线程读，所以自己带锁。"""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_name  TEXT    NOT NULL DEFAULT '',
                    sender    TEXT    NOT NULL DEFAULT '',
                    text      TEXT    NOT NULL DEFAULT '',
                    status    INTEGER NOT NULL DEFAULT 0,
                    urgency   INTEGER NOT NULL DEFAULT 0,
                    needs_now INTEGER NOT NULL DEFAULT 0,
                    category  TEXT    NOT NULL DEFAULT '',
                    reason    TEXT    NOT NULL DEFAULT '',
                    error     TEXT    NOT NULL DEFAULT '',
                    time_ms   INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_history_time ON history(time_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_history_status ON history(status);
                """
            )
            self._conn.commit()

    # -- 写入 --------------------------------------------------------------
    def insert_pending(self, item: MsgItem) -> int:
        """消息一到就先落一条「判断中」，判断完成后回填结果。

        这样即使判断失败，记录页里也能看出这条消息来过。
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (app_name, sender, text, status, urgency, needs_now,"
                " category, reason, error, time_ms, created_at)"
                " VALUES (?,?,?,?,0,0,'','','',?,?)",
                (item.app_name, item.sender, item.text, STATUS_PENDING,
                 item.time_ms, int(time.time() * 1000)),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def fill_result(self, row_id: int, item: MsgItem) -> None:
        """判断完成后回填结果。"""
        if not row_id:
            return
        status = STATUS_FAILED if item.error else (
            STATUS_JUDGED if item.judged else STATUS_PENDING
        )
        with self._lock:
            self._conn.execute(
                "UPDATE history SET status=?, urgency=?, needs_now=?, category=?,"
                " reason=?, error=? WHERE id=?",
                (status, item.urgency, 1 if item.needs_now else 0,
                 item.category, item.reason, item.error or "", row_id),
            )
            self._conn.commit()

    def trim(self, keep: int = MAX_RECORDS) -> int:
        """只保留最近 keep 条，返回删掉多少。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM history WHERE id NOT IN"
                " (SELECT id FROM history ORDER BY time_ms DESC, id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()
            return cur.rowcount or 0

    # -- 读取 --------------------------------------------------------------
    def page(self, offset: int, limit: int, flt: Filter) -> list[Record]:
        where = flt.where_sql
        sql = ("SELECT id, app_name, sender, text, status, urgency, needs_now,"
               " category, reason, error, time_ms FROM history")
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY time_ms DESC, id DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._conn.execute(sql, (limit, offset)).fetchall()
        return [
            Record(
                id=r["id"], app_name=r["app_name"], sender=r["sender"], text=r["text"],
                status=r["status"], urgency=r["urgency"],
                needs_now=bool(r["needs_now"]), category=r["category"],
                reason=r["reason"], error=r["error"], time_ms=r["time_ms"],
            )
            for r in rows
        ]

    def count(self, flt: Filter) -> int:
        where = flt.where_sql
        sql = "SELECT COUNT(*) FROM history"
        if where:
            sql += " WHERE " + where
        with self._lock:
            row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_history: MsgHistory | None = None
_history_lock = threading.Lock()


def get() -> MsgHistory:
    """进程内单例。多开连接会抢 SQLite 的写锁，所以统一走这里。"""
    global _history
    with _history_lock:
        if _history is None:
            _history = MsgHistory()
        return _history
