"""One triage item plus the bucketing rules.

The bucket thresholds are identical to core/MsgItem.kt on Android so the two
platforms colour the same message the same way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Bucket(str, Enum):
    PENDING = "pending"
    NOW = "now"
    SOON = "soon"
    LATER = "later"


# Bucket -> (display label, colour key). Colours are resolved by the UI layer.
BUCKET_LABEL = {
    Bucket.NOW: "要马上回",
    Bucket.SOON: "尽快",
    Bucket.LATER: "可以晚点",
    Bucket.PENDING: "判断中",
}


@dataclass
class MsgItem:
    """One conversation's latest message. Keyed by sender + conversation."""

    sender: str = ""
    text: str = ""
    app_name: str = "微信"
    time_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    # Filled in by the judge.
    urgency: int = 0
    needs_now: bool = False
    category: str = ""
    reason: str = ""
    confidence: float = 0.0
    judged: bool = False
    error: str | None = None

    @property
    def key(self) -> str:
        """Stable identity: same sender in same app folds into one row."""
        return f"{self.app_name}|{self.sender}"

    @property
    def bucket(self) -> Bucket:
        if not self.judged:
            return Bucket.PENDING
        if self.needs_now or self.urgency >= 7:
            return Bucket.NOW
        if self.urgency >= 4:
            return Bucket.SOON
        return Bucket.LATER

    @property
    def label(self) -> str:
        return BUCKET_LABEL[self.bucket]

    @property
    def reason_text(self) -> str:
        """Short human line: '紧急 8/9 · 对方在等'."""
        if not self.judged:
            return self.error or "正在判断…"
        why = REASON_ZH.get(self.reason, "无明确压力")
        return f"紧急 {self.urgency}/9 · {why}"


REASON_ZH = {
    "deadline": "有时限",
    "someone_waiting": "对方在等",
    "emotional": "情绪上需要回应",
    "money_risk": "涉及钱或安全",
    "none": "无明确压力",
}

CATEGORY_ZH = {
    "work_blocking": "工作",
    "personal": "私人",
    "logistics": "约定安排",
    "info_only": "信息",
    "promotion": "推广",
    "system": "系统",
    "money": "钱",
}
