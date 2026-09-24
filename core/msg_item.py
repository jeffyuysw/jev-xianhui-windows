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
    """One captured message.

    每条消息都是独立的一行 —— 同一个人连发多条也会分别显示，不再折叠。
    判断结果（urgency / needs_now / …）只属于这一条消息。
    """

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

    # 本机分析记录里的行号。消息一到就先落一条「判断中」，判断完成后回填结果。
    history_id: int = 0

    @property
    def key(self) -> str:
        """每条消息独立成行。

        早先只用了 app_name|sender，同一个人的第二条消息会把第一条覆盖掉，
        悬浮窗里永远只剩最新一条。现在把到达时间与文本指纹一起编进 key，
        同一发送者的多条消息就能并存。

        capture 层已经做过一轮「同会话同文本」去重，所以这里不会重复插入。
        """
        return f"{self.app_name}|{self.sender}|{self.time_ms}|{self.text[:32]}"

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
