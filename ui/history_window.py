"""分析记录窗口：判断过的每条消息，可按状态筛选。

与 Android 版的「分析记录」页对齐：

  * 顶部一排状态筛选，**默认选中「马上回」**
  * 每行显示 来源（应用）/ 名称（联系人）/ 聊天内容 / 时间
  * 一页 25 条，滚到底自动续加载下一页
  * 数据来自本机 SQLite（core/msg_history.py），不联网、不上传，可一键清空
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import msg_history
from core.msg_history import Filter, PAGE_SIZE, Record

from .theme import BORDER, FONT_FAMILY, TEXT, TEXT_FAINT, TEXT_SOFT

# 状态徽章的配色，与悬浮窗的分档颜色保持一致
_BADGE_COLOR = {
    "马上回": "#D6453D",
    "尽快": "#D98A1F",
    "可晚点": "#9B9A94",
    "判断中": "#3B7DD8",
    "判断失败": "#A32D2D",
}


def _fmt_time(ms: int) -> str:
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000)
    today = datetime.fromtimestamp(time.time())
    if dt.date() == today.date():
        return dt.strftime("今天 %H:%M")
    if dt.year == today.year:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


class RecordRow(QWidget):
    """一行记录：来源 + 状态徽章 / 名称 / 内容 / 时间。"""

    def __init__(self, rec: Record, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(7)
        src = QLabel(rec.app_name or "聊天")
        src.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 11px; font-family: '{FONT_FAMILY}';"
            f" background: #F1EFE8; border-radius: 4px; padding: 1px 5px;"
        )
        top.addWidget(src)

        name = QLabel(rec.sender or "未知")
        name.setStyleSheet(
            f"color: {TEXT}; font-size: 12.5px; font-weight: 600;"
            f" font-family: '{FONT_FAMILY}';"
        )
        top.addWidget(name)
        top.addStretch(1)

        label = rec.bucket_label
        badge = QLabel(label)
        badge.setStyleSheet(
            f"color: #FFFFFF; background: {_BADGE_COLOR.get(label, '#9B9A94')};"
            f" border-radius: 4px; padding: 1px 6px; font-size: 10.5px;"
            f" font-family: '{FONT_FAMILY}';"
        )
        top.addWidget(badge)
        lay.addLayout(top)

        body = QLabel(rec.text or "")
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color: {TEXT}; font-size: 12.5px; font-family: '{FONT_FAMILY}';"
        )
        lay.addWidget(body)

        meta = _fmt_time(rec.time_ms)
        if rec.status == msg_history.STATUS_JUDGED:
            meta += f" · 紧急 {rec.urgency}/9"
        elif rec.error:
            meta += " · " + rec.error[:40]
        stamp = QLabel(meta)
        stamp.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11px; font-family: '{FONT_FAMILY}';"
        )
        lay.addWidget(stamp)


class HistoryWindow(QWidget):
    """独立窗口，从设置窗口顶部的「分析记录」进入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("先回 · 分析记录")
        self.resize(520, 660)

        self._history = msg_history.get()
        self._filter: Filter = Filter.NOW      # 默认「马上回」
        self._loading = False
        self._has_more = True
        self._rows: list[Record] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 标题行
        head = QHBoxLayout()
        title = QLabel("分析记录")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 17px; font-weight: 700;"
            f" font-family: '{FONT_FAMILY}';"
        )
        head.addWidget(title)
        head.addStretch(1)

        btn_clear = QPushButton("清空")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet(self._ghost_btn())
        btn_clear.clicked.connect(self._on_clear)
        head.addWidget(btn_clear)
        root.addLayout(head)

        hint = QLabel("只保存在本机，最多 2000 条。")
        hint.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11px; font-family: '{FONT_FAMILY}';"
        )
        root.addWidget(hint)

        # 筛选 chips
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chips: dict[Filter, QPushButton] = {}
        for f in Filter:
            b = QPushButton(f.value)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, flt=f: self._select_filter(flt))
            chips.addWidget(b)
            self._chips[f] = b
        chips.addStretch(1)
        root.addLayout(chips)
        self._refresh_chip_styles()

        # 列表
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: #FFFFFF; border: 1px solid {BORDER};"
            f" border-radius: 10px; outline: none; }}"
            f"QListWidget::item {{ border-bottom: 1px solid {BORDER}; }}"
        )
        self._list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self._list, 1)

        self._empty = QLabel("没有记录")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12.5px; font-family: '{FONT_FAMILY}';"
        )
        root.addWidget(self._empty)

        self._footer = QLabel("")
        self._footer.setAlignment(Qt.AlignCenter)
        self._footer.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11px; font-family: '{FONT_FAMILY}';"
        )
        root.addWidget(self._footer)

        self.reload()

    # -- 样式 --------------------------------------------------------------
    @staticmethod
    def _ghost_btn() -> str:
        return (
            f"QPushButton {{ background: #F1EFE8; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 8px;"
            f" padding: 4px 12px; font-size: 12px; font-family: '{FONT_FAMILY}'; }}"
            f"QPushButton:hover {{ background: #EDEBE6; }}"
        )

    @staticmethod
    def _chip_on() -> str:
        return (
            f"QPushButton {{ background: #2C2C2A; color: #FFFFFF; border: 0;"
            f" border-radius: 12px; padding: 4px 12px; font-size: 12px;"
            f" font-family: '{FONT_FAMILY}'; }}"
        )

    @staticmethod
    def _chip_off() -> str:
        return (
            f"QPushButton {{ background: #FFFFFF; color: {TEXT_SOFT};"
            f" border: 1px solid {BORDER}; border-radius: 12px;"
            f" padding: 4px 12px; font-size: 12px; font-family: '{FONT_FAMILY}'; }}"
            f"QPushButton:hover {{ background: #F7F6F3; }}"
        )

    def _refresh_chip_styles(self) -> None:
        for f, b in self._chips.items():
            b.setStyleSheet(self._chip_on() if f == self._filter else self._chip_off())

    # -- 数据 --------------------------------------------------------------
    def _select_filter(self, flt: Filter) -> None:
        if flt == self._filter:
            return
        self._filter = flt
        self._refresh_chip_styles()
        self.reload()

    def reload(self) -> None:
        """换筛选或清空后整体重来。"""
        self._rows.clear()
        self._list.clear()
        self._has_more = True
        self._loading = False
        self._footer.setText("")
        self.load_more()

    def load_more(self) -> None:
        if self._loading or not self._has_more:
            return
        self._loading = True
        flt = self._filter
        try:
            page = self._history.page(len(self._rows), PAGE_SIZE, flt)
            total = self._history.count(flt)
        except Exception as e:
            self._footer.setText(f"读取失败：{e}")
            self._loading = False
            return

        self._rows.extend(page)
        for rec in page:
            item = QListWidgetItem(self._list)
            row = RecordRow(rec)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

        self._has_more = len(page) == PAGE_SIZE
        self._loading = False

        self._empty.setVisible(not self._rows)
        if not self._rows:
            self._empty.setText(
                "还没有记录\n收到消息后，这里会留下判断过的每一条"
                if flt == Filter.ALL else f"没有「{flt.value}」的记录"
            )
            self._footer.setText("")
        elif self._has_more:
            self._footer.setText(f"继续下滑加载更多（共 {total} 条）")
        else:
            self._footer.setText(f"已经到底了，共 {total} 条")

    def _on_scroll(self, value: int) -> None:
        bar = self._list.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 40:
            self.load_more()

    def _on_clear(self) -> None:
        try:
            self._history.clear()
        except Exception:
            pass
        self.reload()

    def refresh_if_visible(self) -> None:
        """有新判断结果时被调用（注册为 store 监听器）。

        窗口没开就什么都不做；开着的话重载列表，并**保住滚动位置** —— 否则
        用户正翻看旧记录时来了新消息，列表会突然跳回顶部。
        """
        if not self.isVisible():
            return
        bar = self._list.verticalScrollBar()
        pos = bar.value()
        self.reload()
        # reload 会重建列表，滚动条最大值要等布局跑完才更新，所以延后恢复。
        QTimer.singleShot(0, lambda: bar.setValue(min(pos, bar.maximum())))
