"""Colour tokens and small shared widgets.

Both windows share one light palette, matching the Android build: 红=马上回,
黄=尽快, 灰=可以晚点, 蓝=判断中. Kept light in both Windows themes so the
overlay stays legible over any app behind it.
"""

from __future__ import annotations

from core.msg_item import Bucket

# -- palette ---------------------------------------------------------------
BG = "#FFFFFF"
BG_SOFT = "#F5F4F1"
BORDER = "#E4E2DD"

TEXT = "#2C2C2A"
TEXT_SOFT = "#6B6A65"
TEXT_FAINT = "#9B9A94"

ACCENT = "#2C2C2A"

BUCKET_COLOR = {
    Bucket.NOW: "#D6453D",
    Bucket.SOON: "#D98A1F",
    Bucket.LATER: "#9B9A94",
    Bucket.PENDING: "#3B7DD8",
}

BUCKET_BG = {
    Bucket.NOW: "#FCEDEC",
    Bucket.SOON: "#FBF1E0",
    Bucket.LATER: "#F2F1EE",
    Bucket.PENDING: "#EAF1FC",
}

BUCKET_NAME = {
    Bucket.NOW: "马上回",
    Bucket.SOON: "尽快",
    Bucket.LATER: "可晚点",
    Bucket.PENDING: "判断中",
}

FONT_FAMILY = "Microsoft YaHei UI"


def qss() -> str:
    return f"""
    QWidget {{ font-family: '{FONT_FAMILY}'; color: {TEXT}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #D8D6D1; border-radius: 3px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
