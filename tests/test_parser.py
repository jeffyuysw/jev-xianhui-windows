"""Tests for capture.parser.

The parser's job is to read the chat pane and ignore everything else. These
tests pin the two failure modes we actually hit in the field:

  * the session list leaking in, so "折叠置顶聊天" or a group name was shown as
    if it were the incoming message;
  * the composer's placeholder ("按住鼠标语音输入文字") being read as a message.

Layouts are synthesised rather than captured, so the tests run anywhere.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capture.ocr import OcrLine
from capture.parser import find_pane_divider, parse


def L(text: str, x: int, y: int, w: int = 120, h: int = 16) -> OcrLine:
    return OcrLine(text, x, y, w, h, 0.95)


def fake_window(width: int = 948, height: int = 661, divider: int = 421):
    """Light session list | dark divider line | darker chat pane."""
    img = np.full((height, width, 3), 90, dtype=np.uint8)
    img[:, :divider] = 200
    img[:, divider : divider + 8] = 15
    img[:, divider + 8 :] = 60
    return img


SESSION_LIST = [
    L("折叠置顶聊天", 125, 619, 73, 12),
    L("公司主机codexAl", 126, 551, 117, 15),
    L("老妈十二月初七", 126, 421, 105, 15),
    L("汝湖城市综合治理", 124, 355, 117, 18),
]

COMPOSER = [
    L("按住鼠标语音输入文字", 434, 600, 145, 16),
    L("发送", 894, 620, 29, 16),
]


def test_divider_is_found():
    img = fake_window()
    assert 400 <= find_pane_divider(img) <= 440


def test_session_list_is_ignored():
    """A pane with real messages must yield the newest one, not a list row."""
    img = fake_window()
    lines = SESSION_LIST + [
        L("张伟", 434, 200, 40, 15),
        L("明天上午十点开会", 434, 226, 180, 18),
        L("李娜", 434, 330, 40, 15),
        L("方案我改好了，你review下", 434, 356, 230, 18),
    ] + COMPOSER
    got = parse(lines, img.shape[1], img)
    assert got is not None
    assert got.sender == "李娜"
    assert got.text == "方案我改好了，你review下"


def test_session_list_only_yields_nothing():
    """With no chat open, we must report nothing rather than the list."""
    img = fake_window()
    assert parse(SESSION_LIST, img.shape[1], img) is None


def test_composer_placeholder_is_not_a_message():
    img = fake_window()
    lines = SESSION_LIST + COMPOSER
    assert parse(lines, img.shape[1], img) is None


def test_single_bubble_without_sender_falls_back():
    img = fake_window()
    lines = SESSION_LIST + [L("您的账户支出100元", 434, 300, 200, 18)] + COMPOSER
    got = parse(lines, img.shape[1], img)
    assert got is not None
    assert got.sender == "对方"
    assert got.text == "您的账户支出100元"


def test_mangled_sidebar_label_is_still_dropped():
    """OCR commonly returns 聊关 for 聊天; the marker match must be loose."""
    img = fake_window()
    lines = SESSION_LIST + [L("折叠置顶聊关", 126, 619, 73, 12)]
    assert parse(lines, img.shape[1], img) is None
