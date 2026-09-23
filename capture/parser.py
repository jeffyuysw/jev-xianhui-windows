"""Turn OCR lines into "sender + latest message".

The chat area is a single column of left/right bubbles. Instead of hard-coding
WeChat's pixel layout (which breaks on every client update), we use relational
cues that survive reskins:

  * WeChat's window is split into a session list on the left and the chat pane on
    the right. Only the chat pane holds messages, so the first job is to throw
    away everything left of that divider. Without this the parser happily returns
    a session-list row ("折叠置顶聊天", a group name) as if it were a message.
  * Inside the chat pane, the right side holds our own bubbles; left-aligned text
    is someone else's, so we keep the left column.
  * Bubbles are separated by a larger vertical gap than the lines inside one
    bubble, so we split on gaps and take the LAST bubble as the newest message.
  * A short line sitting just above a bubble is that bubble's sender name.

We deliberately emit ONE message per poll: the newest incoming one. That matches
the product - we judge "do I need to reply now", not transcribe the whole chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .ocr import OcrLine

# Lines that are chrome, not message content.
_NOISE = {
    "微信", "WeChat", "发送", "表情", "文件", "截图", "聊天信息", "搜索",
    "通讯录", "收藏", "朋友圈", "设置", "看一看", "搜一搜", "视频号",
}

# Sidebar furniture. OCR is not exact, so these are matched loosely (substring)
# rather than by equality - "折叠置顶聊天" routinely comes back as
# "折叠置顶聊关" or "折叠置顶聊矢".
_SIDEBAR_MARKERS = (
    "折叠置顶聊天", "折叠的置顶聊天", "置顶聊天", "折叠置顶聊",
    "小程序面板", "通讯录", "朋友圈", "看一看", "搜一搜", "视频号",
)

# The chat header shows the conversation name followed by the member count, e.g.
# "jev-chat-JARVIS群(258)".
_HEADER_RE = re.compile(r"[（(]\s*\d+\s*[)）]\s*$")

# Typing area furniture. The composer sits in the bottom band of the window and
# holds a placeholder ("按住鼠标语音输入文字") plus the send button - none of
# which is an incoming message.
_COMPOSER_MARKERS = (
    "按住鼠标语音输入文字", "按住说话", "输入文字", "Alt+Enter", "发送",
)
# Fraction of window height occupied by the composer at the bottom.
_COMPOSER_TOP = 0.80

# Below this fraction of the window width we never look for the divider: that
# band is the nav rail / session-list text.
_DIVIDER_SEARCH_LO = 0.25
_DIVIDER_SEARCH_HI = 0.62
# How far right of the detected divider the chat pane is assumed to start. The
# divider line itself is ~12px wide; the pane's own padding follows it.
_DIVIDER_PAD = 10


@dataclass
class ParsedMessage:
    sender: str
    text: str


def _is_noise(line: OcrLine) -> bool:
    t = line.text.strip()
    if not t or t in _NOISE:
        return True
    if len(t) <= 1 and not t.isalnum():
        return True
    # Timestamp rows like "12:30" or "昨天".
    if t.replace(":", "").isdigit() and len(t) <= 5:
        return True
    for word in ("撤回", "以下为新消息"):
        if word in t:
            return True
    return False


def _is_sender_like(text: str, median_h: float) -> bool:
    """A sender/name line is short and physically smaller than message text."""
    t = text.strip()
    return len(t) <= 12 and " " not in t and "，" not in t and "。" not in t


def _is_sidebar_furniture(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    # OCR often mangles the tail of these labels, so a prefix match is needed.
    return any(m in t or t in m for m in _SIDEBAR_MARKERS)


def _is_composer_furniture(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return any(m in t or t in m for m in _COMPOSER_MARKERS)


def find_pane_divider(img: np.ndarray) -> int:
    """X of the session-list / chat-pane divider, or 0 if not found.

    WeChat draws the session list as a lighter card and the chat pane darker, so
    the boundary is a narrow dark vertical line. That line is far more stable
    than trying to infer the split from OCR text positions (a busy session list
    fills the gutter with timestamps, which defeats a whitespace search).

    Returns 0 when no confident divider exists - the caller then keeps the whole
    window, which still works if the user resized the window to chat-only.
    """
    if img is None or img.ndim != 3:
        return 0
    h, w = img.shape[:2]
    if w < 200 or h < 200:
        return 0

    # Mean luminance per column, ignoring the header and the input bar, both of
    # which are full-width chrome and would flatten the profile.
    band = img[int(h * 0.15): int(h * 0.85)].mean(axis=2)
    col = band.mean(axis=0)

    lo = int(w * _DIVIDER_SEARCH_LO)
    hi = int(w * _DIVIDER_SEARCH_HI)
    if hi - lo < 30:
        return 0

    # The divider is the sharpest left-to-right drop in luminance.
    span = 6
    best_x, best_drop = 0, 0.0
    for x in range(max(lo, span), min(hi, w - span)):
        drop = float(col[x - span] - col[x + span])
        if drop > best_drop:
            best_drop, best_x = drop, x

    # A real divider is a strong edge; letter spacing never produces this.
    if best_drop < 12.0:
        return 0
    return best_x + _DIVIDER_PAD


def _chat_pane_left(lines: list[OcrLine], window_width: int, img=None) -> int:
    """Left edge of the chat pane, used to throw away session-list rows."""
    if img is not None:
        x = find_pane_divider(img)
        if x:
            # Sanity: the pane must actually contain some recognised text.
            if any(ln.x >= x for ln in lines):
                return x
            return x
    return 0


def _group_bubbles(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Split a top-down column of lines into bubbles by vertical gaps.

    Within a bubble, consecutive lines are close together. Between bubbles there
    is a clearly larger gap (WeChat leaves room for the neighbour's name too).
    """
    if not lines:
        return []
    ordered = sorted(lines, key=lambda ln: ln.y)
    median_h = sorted(ln.h for ln in ordered)[len(ordered) // 2] or 20
    # A new bubble starts when the gap exceeds roughly 1.4 line heights.
    gap_threshold = max(int(median_h * 1.4), 26)

    groups: list[list[OcrLine]] = [[ordered[0]]]
    for ln in ordered[1:]:
        prev = groups[-1][-1]
        gap = ln.y - (prev.y + prev.h)
        if gap > gap_threshold:
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return groups


def parse(
    lines: list[OcrLine], window_width: int, img: np.ndarray | None = None
) -> ParsedMessage | None:
    """Extract the newest incoming message, or None if nothing usable."""
    usable = [ln for ln in lines if not _is_noise(ln)]
    if not usable:
        return None

    # Drop the session list before anything else, or its rows masquerade as
    # messages (this is what produced "好友" / "折叠置顶聊天" in the overlay).
    pane_left = _chat_pane_left(usable, window_width, img)
    if pane_left:
        usable = [ln for ln in usable if ln.x >= pane_left]
        if not usable:
            return None

    # Belt and braces: explicit sidebar furniture never counts as a message.
    usable = [ln for ln in usable if not _is_sidebar_furniture(ln.text)]
    if not usable:
        return None

    # The composer at the bottom of the pane holds a placeholder, not a message.
    # Its y is measured against the window, so derive the cut from the image.
    if img is not None:
        composer_y = img.shape[0] * _COMPOSER_TOP
    else:
        composer_y = max(ln.y + ln.h for ln in usable) * _COMPOSER_TOP
    if len(usable) > 1:
        trimmed = [ln for ln in usable if ln.y < composer_y]
        # Only apply when it leaves something behind - a chat whose only text
        # sits low (a short window) should still be read.
        if trimmed:
            usable = trimmed
    usable = [ln for ln in usable if not _is_composer_furniture(ln.text)]
    if not usable:
        return None

    left_edge = min(ln.x for ln in usable)
    right_edge = max(ln.x + ln.w for ln in usable)
    span = max(1, right_edge - left_edge)

    # Incoming bubbles hug the left; our own hug the right. Keep left 55%.
    incoming = [ln for ln in usable if (ln.x - left_edge) < span * 0.55]
    if not incoming:
        incoming = [ln for ln in usable if (ln.x - left_edge) < span * 0.75]
    if not incoming:
        incoming = usable

    groups = _group_bubbles(incoming)
    if not groups:
        return None

    # Newest bubble = the lowest one on screen.
    last = max(groups, key=lambda g: max(ln.y + ln.h for ln in g))

    median_h = sorted(ln.h for ln in incoming)[len(incoming) // 2] or 20
    group_top = min(ln.y for ln in last)
    group_h = max(ln.y + ln.h for ln in last) - group_top

    # Strip a leading sender line: only when the bubble holds more than one
    # line, the first line looks like a name, and it is not a wrapped sentence.
    sender = "对方"
    body = last
    if len(last) >= 2:
        first = last[0]
        rest = last[1:]
        rest_h = max(ln.y + ln.h for ln in rest) - min(ln.y for ln in rest)
        # Treat the first line as a sender only if it is short AND the remaining
        # lines still carry the message, AND there is a gap after the name.
        gap_after_name = rest[0].y - (first.y + first.h)
        if (
            _is_sender_like(first.text, median_h)
            and first.w < max(ln.w for ln in rest) * 0.9
            and gap_after_name >= 0
            and rest_h > 0
        ):
            sender = first.text.strip()
            body = rest

    message = " ".join(ln.text for ln in sorted(body, key=lambda ln: ln.y)).strip()
    if not message:
        return None
    # The chat header ("群名(258)") is not a message.
    if _HEADER_RE.search(message):
        return None
    return ParsedMessage(sender=sender, text=message)
