"""The floating priority list.

A frameless, always-on-top tool window holding a column of message rows. Drag
the header to move it; release and it snaps to the nearest screen edge, exactly
like the Android bubble. Clicking a row opens that conversation's app and drops
the row.

Appearance (background, text colour, font size, width) is user-configurable —
see core/appearance.py for how the derived shades are computed. The card is
fully repainted by `apply_appearance()`, so switching from a white card to a
black one recolours every child widget without rebuilding the tree.

Note on threading: `_on_store_changed` is invoked via the store's GUI-thread
dispatcher (see core/store.py), so it is always safe to touch widgets here.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.appearance import (
    DEFAULT_BG,
    DEFAULT_FG,
    Palette,
    is_dark,
    readable_on,
)
from core.msg_item import Bucket, MsgItem
from core.settings import Settings
from core.store import store

LIST_MAX_HEIGHT = 340
EDGE_MARGIN = 6
ROW_HEIGHT_BASE = 62
HEADER_HEIGHT = 34
RADIUS = 14
# Height of the list area when there is nothing to show. Kept small on purpose:
# an idle overlay should read as a slim strip, not as a big empty card. It grows
# to fit the rows as soon as messages arrive (up to LIST_MAX_HEIGHT, then scrolls).
EMPTY_LIST_HEIGHT = 40
EMPTY_PAD_V = 8


def _empty_height(font_size: int) -> int:
    """Idle list height, scaled so the one-line placeholder never clips.

    A flat 40px works at the default 12px font but the line needs ~43px at the
    maximum size of 20, so the placeholder would be cut off. Scaling keeps the
    strip slim at small sizes and correct at large ones.
    """
    return max(EMPTY_LIST_HEIGHT, int(font_size * 1.45) + 2 * EMPTY_PAD_V)


class MessageRow(QFrame):
    """One message line.

    Padding is deliberately tight (6px top/bottom) and the three labels are
    packed with 1px spacing so the sender, the message and the reason read as a
    single block rather than three detached lines.
    """

    tapped = Signal(str)

    def __init__(self, item: MsgItem, pal: Palette, font_size: int, width: int) -> None:
        super().__init__()
        self._key = item.key
        self._hover = False
        self._pal = pal
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(_row_height(font_size))
        self._paint()

        accent = pal.bucket[item.bucket]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(7)

        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {accent}; border-radius: 1px;")
        lay.addWidget(bar)

        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        sender = QLabel(item.sender or "对方")
        sender.setStyleSheet(
            f"color: {pal.fg}; font-size: {font_size + 0.5:.1f}px;"
            f" font-weight: 600; font-family: '{pal_font()}';"
        )
        tag = QLabel(BUCKET_LABEL[item.bucket])
        tag.setStyleSheet(
            f"color: {accent}; font-size: {font_size - 1:.1f}px;"
            f" font-weight: 600; font-family: '{pal_font()}';"
        )
        top.addWidget(sender)
        top.addStretch(1)
        top.addWidget(tag)
        col.addLayout(top)

        # Message body uses the user's text colour at full strength — this is
        # the line people actually read.
        msg = QLabel(_elide(item.text, _chars_for(width, font_size)))
        msg.setStyleSheet(
            f"color: {pal.fg}; font-size: {font_size}px;"
            f" font-family: '{pal_font()}';"
        )
        col.addWidget(msg)

        why = QLabel(item.reason_text)
        why.setStyleSheet(
            f"color: {pal.text_soft}; font-size: {font_size - 1:.1f}px;"
            f" font-family: '{pal_font()}';"
        )
        col.addWidget(why)

        lay.addLayout(col, 1)

    def _paint(self) -> None:
        bg = self._pal.row_bg_hover if self._hover else self._pal.row_bg
        self.setStyleSheet(f"MessageRow {{ background: {bg}; border-radius: 9px; }}")

    def enterEvent(self, e) -> None:
        self._hover = True
        self._paint()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._paint()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.tapped.emit(self._key)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        pass


def pal_font() -> str:
    from ui.theme import FONT_FAMILY

    return FONT_FAMILY


def _row_height(font_size: int) -> int:
    """Rows grow with the font so three stacked lines never get clipped."""
    return ROW_HEIGHT_BASE + max(0, font_size - 12) * 3


def _chars_for(width: int, font_size: int) -> int:
    """Roughly how many CJK characters fit on one body line."""
    usable = max(80, width - 46)
    per_char = max(7.5, font_size * 0.95)
    return max(8, int(usable / per_char) + 2)


def _elide(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


BUCKET_LABEL = {
    Bucket.NOW: "马上回",
    Bucket.SOON: "尽快",
    Bucket.LATER: "可晚点",
    Bucket.PENDING: "判断中",
}


class PriorityOverlay(QWidget):
    """Frameless always-on-top list with edge snapping and collapse."""

    row_tapped = Signal(str)
    closed = Signal()
    cleared = Signal()
    collapsed_changed = Signal(bool)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._collapsed = settings.overlay_collapsed
        self._pal = Palette(settings.overlay_bg, settings.overlay_fg)
        self._font_size = settings.overlay_font_size

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedWidth(settings.overlay_width)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._card = QFrame()
        self._card.setObjectName("card")
        self._card_lay = QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(0, 0, 0, 0)
        self._card_lay.setSpacing(0)
        self._card_lay.addWidget(self._build_header())
        self._card_lay.addWidget(self._build_list())
        self._root.addWidget(self._card)

        self._refresh_listener = self._on_store_changed
        store.add_listener(self._refresh_listener)
        self._apply_appearance(repaint=True)
        self._on_store_changed()
        self._apply_collapsed(self._collapsed, initial=True)

    # -- header ------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(HEADER_HEIGHT)
        header.setCursor(Qt.SizeAllCursor)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(11, 0, 7, 0)
        lay.setSpacing(6)

        self._title = QLabel("要马上回")
        lay.addWidget(self._title)
        self._badge = QLabel("")
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setFixedHeight(17)
        self._badge.hide()
        lay.addWidget(self._badge)
        lay.addStretch(1)

        self._btn_min = _HeaderButton("—")
        self._btn_min.clicked = self._toggle_collapsed
        lay.addWidget(self._btn_min)

        self._btn_close = _HeaderButton("×")
        self._btn_close.clicked = self._on_close
        lay.addWidget(self._btn_close)

        self._header = header
        header.mousePressEvent = self._header_press  # type: ignore[method-assign]
        header.mouseMoveEvent = self._header_move  # type: ignore[method-assign]
        header.mouseReleaseEvent = self._header_release  # type: ignore[method-assign]
        return header

    # -- list --------------------------------------------------------------
    def _build_list(self) -> QWidget:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(inner)
        # Tight top margin: the first row should sit right under the header,
        # not float away from it.
        self._list_lay.setContentsMargins(8, 5, 8, 6)
        self._list_lay.setSpacing(5)
        self._list_lay.addStretch(1)
        self._scroll.setWidget(inner)

        # One short line only. The old two-line copy needed ~46px of its own
        # height, which forced the idle card to be as tall as a real message
        # row. The list height is fixed at EMPTY_LIST_HEIGHT, so a second line
        # would just be clipped anyway.
        self._empty = QLabel("暂无待处理消息")
        self._empty.setAlignment(Qt.AlignCenter)
        self._list_lay.insertWidget(0, self._empty)
        return self._scroll

    def _on_store_changed(self) -> None:
        items = store.all()
        # Rebuild rows.
        while self._list_lay.count() > 2:  # stretch + the empty label
            w = self._list_lay.takeAt(1)
            if w and w.widget():
                w.widget().deleteLater()
        ordered = [i for i in items if i.bucket != Bucket.LATER] or items
        ordered = ordered[:8]
        for i, item in enumerate(ordered):
            row = MessageRow(item, self._pal, self._font_size, self.width())
            row.tapped.connect(self.row_tapped.emit)
            self._list_lay.insertWidget(i + 1, row)

        self._empty.setVisible(not ordered)
        now = store.count_bucket(Bucket.NOW)
        if now > 0:
            self._badge.setText(str(now))
            self._badge.show()
        else:
            self._badge.hide()
        self._title.setText("要马上回" if now else "先回")

        spacing = self._list_lay.spacing()
        rows_h = sum(_row_height(self._font_size) for _ in ordered) + max(0, len(ordered) - 1) * spacing
        idle_h = _empty_height(self._font_size)
        self._scroll.setFixedHeight(
            # `max(idle_h, ...)` never actually kicks in here, because one row is
            # already 62px. It guards against a row ever being shorter than the
            # idle strip, which would make the card shrink on the first message.
            min(LIST_MAX_HEIGHT, max(idle_h, rows_h + 12)) if ordered else idle_h
        )
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """Grow/shrink the window to exactly fit header + list.

        `adjustSize()` alone is unreliable here: the scroll area's height was
        just overridden with setFixedHeight, and Qt caches the old size hint for
        one layout pass, so the window ends up too short and the rows are
        clipped. Computing it directly avoids that.
        """
        if self._collapsed:
            height = HEADER_HEIGHT
        else:
            height = HEADER_HEIGHT + self._scroll.height() + 2  # +2 for card border
        self.setFixedHeight(height)
        self.adjustSize()

    # -- appearance --------------------------------------------------------
    def apply_appearance(self, settings: Settings) -> None:
        """Slot for the settings window's appearance_changed signal."""
        changed = (
            settings.overlay_bg != self._pal.bg
            or settings.overlay_fg != self._pal.fg
            or settings.overlay_font_size != self._font_size
        )
        self._pal = Palette(settings.overlay_bg, settings.overlay_fg)
        self._font_size = settings.overlay_font_size
        self._apply_appearance(repaint=changed)
        self.setWindowOpacity(settings.opacity())
        if settings.overlay_width != self.width():
            self.setFixedWidth(settings.overlay_width)
            self._snap_to_edge()
        # Rows are rebuilt anyway, which re-applies the new fonts/colours.
        self._on_store_changed()

    def apply_opacity(self, settings: Settings) -> None:
        """Cheap path for the opacity slider, which fires on every drag step.

        Deliberately avoids the full `apply_appearance()` round trip: rebuilding
        every row while the user drags would flicker and reset the scroll
        position. Setting the window opacity alone is all that is needed.
        """
        self.setWindowOpacity(settings.opacity())
        self._settings.overlay_opacity = settings.overlay_opacity

    def _apply_appearance(self, repaint: bool) -> None:
        pal = self._pal
        self._card.setStyleSheet(
            f"#card {{ background: {pal.bg}; border: 1px solid {pal.border};"
            f" border-radius: {RADIUS}px; }}"
        )
        # The header shares the card background on purpose: no separate block,
        # so the title sits visually attached to the first row.
        self._header.setStyleSheet(f"#header {{ background: transparent; }}")
        self._title.setStyleSheet(
            f"color: {pal.title}; font-size: {self._font_size + 0.5:.1f}px;"
            f" font-weight: 600; font-family: '{pal_font()}';"
        )
        now_accent = pal.bucket[Bucket.NOW]
        self._badge.setStyleSheet(
            f"color: {readable_on(now_accent)}; background: {now_accent};"
            f" border-radius: 8px; padding: 0 6px;"
            f" font-size: {self._font_size - 1.5:.1f}px; font-weight: 600;"
            f" font-family: '{pal_font()}';"
        )
        self._empty.setStyleSheet(
            f"color: {pal.text_faint}; font-size: {self._font_size}px;"
            f" font-family: '{pal_font()}'; padding: {EMPTY_PAD_V}px 0;"
        )
        self._btn_min.repaint_with(pal)
        self._btn_close.repaint_with(pal)

    # -- collapse ----------------------------------------------------------
    def _toggle_collapsed(self) -> None:
        self._apply_collapsed(not self._collapsed)

    def _apply_collapsed(self, collapsed: bool, initial: bool = False) -> None:
        self._collapsed = collapsed
        self._scroll.setVisible(not collapsed)
        self._btn_min.setText("+" if collapsed else "—")
        self._settings.overlay_collapsed = collapsed
        self._settings.save()
        self._resize_to_content()
        if not initial:
            self.collapsed_changed.emit(collapsed)

    # -- dragging + snapping ----------------------------------------------
    def _header_press(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _header_move(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def _header_release(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self._snap_to_edge()

    def _snap_to_edge(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        pos = self.pos()
        center_x = pos.x() + self.width() / 2
        if center_x < geo.left() + geo.width() / 2:
            new_x = geo.left() + EDGE_MARGIN
        else:
            new_x = geo.right() - self.width() - EDGE_MARGIN
        new_y = max(geo.top() + EDGE_MARGIN, min(pos.y(), geo.bottom() - self.height() - EDGE_MARGIN))
        self.move(new_x, new_y)
        self._settings.overlay_x, self._settings.overlay_y = new_x, new_y
        self._settings.save()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        s = self._settings
        # Qt silently drops setWindowOpacity() while the widget is still
        # hidden: the value is written but the native window does not exist yet,
        # so it never reaches Windows. The overlay is built at startup and
        # shown later, so without this re-apply a saved opacity would reset to
        # fully opaque on every launch. Re-assert it once the window is real.
        self.setWindowOpacity(s.opacity())
        if s.overlay_x >= 0 and s.overlay_y >= 0:
            self.move(s.overlay_x, s.overlay_y)
        else:
            self._snap_to_edge()

    def _on_close(self) -> None:
        store.remove_listener(self._refresh_listener)
        self.hide()
        self.closed.emit()


class _HeaderButton(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.clicked = None  # assigned by the header builder
        self._pal: Palette | None = None
        self._hover = False
        self.setFixedSize(22, 22)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

    def repaint_with(self, pal: Palette) -> None:
        self._pal = pal
        self._paint()

    def _paint(self) -> None:
        if self._pal is None:
            return
        pal = self._pal
        if self._hover:
            bg, fg = pal.row_bg_hover, pal.fg
        else:
            bg, fg = "transparent", pal.text_soft
        self.setStyleSheet(
            f"color: {fg}; background: {bg}; font-size: 14px;"
            f" font-family: '{pal_font()}'; border-radius: 11px;"
        )

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            if callable(self.clicked):
                self.clicked()
        e.accept()

    def enterEvent(self, e) -> None:
        self._hover = True
        self._paint()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._paint()
