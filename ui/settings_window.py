"""The settings / control window.

Three status cards in the same spirit as the Android home screen: each shows
whether it is ready, and its button disappears once it is. A summary at the top
says how many steps remain, and a live queue counter sits under the run row.

An appearance block at the bottom drives the overlay's colours, font size and
width; it emits `appearance_changed` so the overlay can repaint live.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core import autostart
from core.appearance import (
    BG_PRESETS,
    DEFAULT_BG,
    DEFAULT_FG,
    DEFAULT_OPACITY,
    FG_PRESETS,
    MAX_FONT,
    MAX_OPACITY,
    MAX_WIDTH,
    MIN_FONT,
    MIN_OPACITY,
    MIN_WIDTH,
    contrast_ratio,
    parse_hex,
    to_hex,
)
from core.jev_client import JevClient
from core.msg_item import Bucket
from core.settings import DEFAULT_ENDPOINT, DEFAULT_MODEL, Settings
from core.store import store

# 先回官网，显示在设置窗口底部，点击用系统默认浏览器打开。
SITE_URL = "https://xianhui.xzaigf.dpdns.org"

from .theme import (
    ACCENT,
    BG,
    BG_SOFT,
    BORDER,
    BUCKET_COLOR,
    FONT_FAMILY,
    TEXT,
    TEXT_FAINT,
    TEXT_SOFT,
)


class StatusCard(QFrame):
    """A titled card with a status badge and an optional action button."""

    def __init__(self, title: str, hint: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(
            f"#card {{ background: {BG_SOFT}; border: 1px solid {BORDER};"
            f" border-radius: 12px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        name = QLabel(title)
        name.setStyleSheet(
            f"color: {TEXT}; font-size: 13.5px; font-weight: 600;"
            f" font-family: '{FONT_FAMILY}';"
        )
        self.badge = QLabel("未完成")
        self.badge.setStyleSheet(_badge_style(False))
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(self.badge)
        lay.addLayout(top)

        self.hint = QLabel(hint)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        lay.addWidget(self.hint)

        self.action = QPushButton("")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setFixedHeight(34)
        self.action.setStyleSheet(_button_style())
        self.action.hide()
        lay.addWidget(self.action)

    def set_done(self, done: bool, badge_text: str | None = None) -> None:
        self.badge.setText(badge_text or ("已就绪" if done else "未完成"))
        self.badge.setStyleSheet(_badge_style(done))
        self.action.setVisible(not done)


def _badge_style(done: bool) -> str:
    color = "#2E7D4F" if done else BUCKET_COLOR[Bucket.NOW]
    bg = "#E6F4EC" if done else "#FCEDEC"
    return (
        f"color: {color}; background: {bg}; border-radius: 9px;"
        f" padding: 2px 9px; font-size: 11px; font-weight: 600;"
        f" font-family: '{FONT_FAMILY}';"
    )


def _button_style() -> str:
    return (
        f"QPushButton {{ background: {ACCENT}; color: #FFFFFF; border: none;"
        f" border-radius: 9px; font-size: 12.5px; font-weight: 600;"
        f" font-family: '{FONT_FAMILY}'; }}"
        f"QPushButton:hover {{ background: #454542; }}"
        f"QPushButton:disabled {{ background: #C9C7C2; color: #F2F1EE; }}"
    )


class SettingsWindow(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    clear_requested = Signal()
    overlay_toggle_requested = Signal()
    history_requested = Signal()          # 顶部「分析记录」入口
    appearance_changed = Signal(object)   # emits the Settings object
    # Opacity travels separately: it is the one appearance control that can be
    # applied without rebuilding the overlay, and it fires on every drag step.
    opacity_changed = Signal(int)         # emits the percentage, 1..100

    # 测试连接的结果要从工作线程送回 GUI 线程。
    # 不能用 QTimer.singleShot —— 工作线程没有 Qt 事件循环，定时器不会触发，
    # 结果就永远不显示（这就是「点测试没反应」的原因）。信号会自动排队。
    test_finished = Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self.setWindowTitle("先回 · 消息优先级助手")
        self.setFixedSize(440, 700)
        self.setStyleSheet(f"background: {BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        body = QWidget()
        body.setStyleSheet(f"background: {BG};")
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(18, 18, 18, 18)
        self._lay.setSpacing(12)
        scroll.setWidget(body)
        root.addWidget(scroll)

        self._build_header()
        self._build_cards()
        self._build_appearance()
        self._build_runtime()
        self._lay.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sync_states)
        self._timer.start(1000)
        self.sync_states()

    # -- sections ----------------------------------------------------------
    def _build_header(self) -> None:
        # 标题行：左边「先回」，最右边是「分析记录」入口。
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("先回")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 19px; font-weight: 700;"
            f" font-family: '{FONT_FAMILY}';"
        )
        row.addWidget(title)
        row.addStretch(1)

        self.btn_history = QPushButton("分析记录")
        self.btn_history.setCursor(Qt.PointingHandCursor)
        self.btn_history.setToolTip("查看判断过的每一条消息，可按状态筛选")
        self.btn_history.setStyleSheet(
            f"QPushButton {{ background: {BG_SOFT}; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 9px;"
            f" padding: 5px 12px; font-size: 12.5px; font-family: '{FONT_FAMILY}'; }}"
            f"QPushButton:hover {{ background: #EDEBE6; }}"
        )
        self.btn_history.clicked.connect(self.history_requested.emit)
        row.addWidget(self.btn_history)
        self._lay.addLayout(row)

        sub = QLabel("看微信窗口，帮你判断哪条必须马上回。只判断，不代回。")
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(sub)

        self._summary = QLabel("")
        self._summary.setStyleSheet(
            f"color: {TEXT}; background: {BG_SOFT}; border: 1px solid {BORDER};"
            f" border-radius: 10px; padding: 9px 12px; font-size: 12.5px;"
            f" font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(self._summary)

    def _build_cards(self) -> None:
        # 1. API key
        self.card_key = StatusCard(
            "① 填 OpenRouter Key",
            "判断消息要用的密钥。只存在本机配置文件里，只在判断请求里发出。",
        )
        self.card_key.action.setText("填写密钥")
        self.card_key.action.clicked.connect(self._focus_key)
        self._lay.addWidget(self.card_key)

        key_box = QFrame()
        key_box.setStyleSheet("background: transparent;")
        kl = QVBoxLayout(key_box)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(6)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("sk-or-v1-…")
        self.key_input.setFixedHeight(34)
        self.key_input.setStyleSheet(_input_style())
        self.key_input.editingFinished.connect(self._save_key)
        kl.addWidget(self.key_input)
        self._lay.addWidget(key_box)

        # 2. WeChat window
        self.card_win = StatusCard(
            "② 打开微信聊天窗口",
            "程序截取微信窗口并本地 OCR 读取消息。截图只在内存，不落盘。",
        )
        self.card_win.action.setText("重新检测")
        self.card_win.action.clicked.connect(self.sync_states)
        self._lay.addWidget(self.card_win)

        # 3. Auto judge
        self.card_judge = StatusCard(
            "③ 开启自动判断",
            "开着就自动给每条新消息打分分档；关掉只收集不判断。",
        )
        self.card_judge.action.hide()
        self._lay.addWidget(self.card_judge)

        self.auto_check = QCheckBox("自动判断新消息")
        self.auto_check.setChecked(self._settings.auto_judge)
        self.auto_check.setStyleSheet(
            f"QCheckBox {{ color: {TEXT}; font-size: 12.5px;"
            f" font-family: '{FONT_FAMILY}'; }}"
        )
        self.auto_check.stateChanged.connect(self._on_auto_toggle)
        self._lay.addWidget(self.auto_check)

    def _build_appearance(self) -> None:
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {BORDER};")
        self._lay.addWidget(sep)

        head = QLabel("悬浮窗外观")
        head.setStyleSheet(
            f"color: {TEXT}; font-size: 13.5px; font-weight: 600;"
            f" font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(head)

        hint = QLabel("颜色改动立即生效。默认白底黑字。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11.5px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(hint)

        self.color_bg = ColorRow("背景色", BG_PRESETS, self._settings.overlay_bg)
        self.color_bg.changed.connect(self._on_bg)
        self._lay.addWidget(self.color_bg)

        self.color_fg = ColorRow("文字色", FG_PRESETS, self._settings.overlay_fg)
        self.color_fg.changed.connect(self._on_fg)
        self._lay.addWidget(self.color_fg)

        self.slider_font = SliderRow(
            "字号", MIN_FONT, MAX_FONT, self._settings.overlay_font_size, " px"
        )
        self.slider_font.changed.connect(self._on_font)
        self._lay.addWidget(self.slider_font)

        self.slider_width = SliderRow(
            "宽度", MIN_WIDTH, MAX_WIDTH, self._settings.overlay_width, " px"
        )
        self.slider_width.changed.connect(self._on_width)
        self._lay.addWidget(self.slider_width)

        self.slider_opacity = SliderRow(
            "不透明度", MIN_OPACITY, MAX_OPACITY, self._settings.overlay_opacity, "%"
        )
        self.slider_opacity.changed.connect(self._on_opacity)
        self._lay.addWidget(self.slider_opacity)

        opacity_hint = QLabel("调低后悬浮窗半透明，能看清底下的内容。最低 1%，不会完全看不见。")
        opacity_hint.setWordWrap(True)
        opacity_hint.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11.5px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(opacity_hint)

        self._warn = QLabel("")
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet(
            f"color: {BUCKET_COLOR[Bucket.NOW]}; font-size: 11.5px;"
            f" font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(self._warn)

        row = QHBoxLayout()
        row.setSpacing(8)
        btn_swap = QPushButton("黑白反转")
        btn_swap.setFixedHeight(32)
        btn_swap.setCursor(Qt.PointingHandCursor)
        btn_swap.setStyleSheet(_ghost_style())
        btn_swap.clicked.connect(self._swap_colors)
        row.addWidget(btn_swap)

        btn_reset = QPushButton("恢复默认")
        btn_reset.setFixedHeight(32)
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.setStyleSheet(_ghost_style())
        btn_reset.clicked.connect(self._reset_appearance)
        row.addWidget(btn_reset)
        row.addStretch(1)
        self._lay.addLayout(row)

        self._check_contrast(self._settings.overlay_bg, self._settings.overlay_fg)

    def _build_runtime(self) -> None:
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {BORDER};")
        self._lay.addWidget(sep)

        self._queue = QLabel("当前队列：0 条")
        self._queue.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(self._queue)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_run = QPushButton("开始监听")
        self.btn_run.setFixedHeight(40)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setStyleSheet(_button_style())
        self.btn_run.clicked.connect(self._on_run)
        row.addWidget(self.btn_run, 1)

        self.btn_overlay = QPushButton("显示悬浮窗")
        self.btn_overlay.setFixedHeight(40)
        self.btn_overlay.setCursor(Qt.PointingHandCursor)
        self.btn_overlay.setStyleSheet(_ghost_style())
        self.btn_overlay.clicked.connect(self.overlay_toggle_requested.emit)
        row.addWidget(self.btn_overlay, 1)
        self._lay.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setFixedHeight(36)
        self.btn_test.setCursor(Qt.PointingHandCursor)
        self.btn_test.setStyleSheet(_ghost_style())
        self.btn_test.clicked.connect(self._on_test)
        row2.addWidget(self.btn_test, 1)

        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.setFixedHeight(36)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(_ghost_style())
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        row2.addWidget(self.btn_clear, 1)
        self._lay.addLayout(row2)

        # 开机自动启动：写 HKCU 的 Run 项，不需要管理员权限、不弹 UAC。
        # 状态以注册表为准（用户也可能从任务管理器里自己关掉）。
        self.autostart_check = QCheckBox("开机自动启动")
        self.autostart_check.setCursor(Qt.PointingHandCursor)
        self.autostart_check.setStyleSheet(
            f"QCheckBox {{ color: {TEXT}; font-size: 12.5px;"
            f" font-family: '{FONT_FAMILY}'; spacing: 7px; }}"
        )
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        self._lay.addWidget(self.autostart_check)

        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        self._test_result.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 11.5px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(self._test_result)
        # 结果从工作线程发过来，自动排队到 GUI 线程执行。
        self.test_finished.connect(self._test_result.setText)

        from . import __version__ as _v  # local import avoids cycle

        ver = QLabel(f"先回 v{_v} · 由 Jev 判断模型驱动")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11px; font-family: '{FONT_FAMILY}';"
        )
        self._lay.addWidget(ver)

        # 官网入口：QLabel 打开外链交给系统默认浏览器，不需要额外依赖。
        site = QLabel(
            f'<a href="{SITE_URL}" style="color:{TEXT_SOFT};'
            f' text-decoration:none;">官网：{SITE_URL}</a>'
        )
        site.setAlignment(Qt.AlignCenter)
        site.setOpenExternalLinks(True)
        site.setCursor(Qt.PointingHandCursor)
        site.setToolTip("点击用浏览器打开先回官网")
        site.setStyleSheet(f"font-size: 11px; font-family: '{FONT_FAMILY}';")
        self._lay.addWidget(site)

    # -- state -------------------------------------------------------------
    def sync_states(self, window_found: bool | None = None) -> None:
        s = self._settings
        key_ok = bool(s.api_key)
        self.card_key.set_done(key_ok)
        if key_ok and not self.key_input.text():
            self.key_input.setText(s.api_key)

        win_ok = self._probe_window() if window_found is None else window_found
        self.card_win.set_done(win_ok, "已找到微信" if win_ok else "未检测到")

        self.card_judge.set_done(s.auto_judge, "自动判断已开" if s.auto_judge else "仅收集")

        done = sum([key_ok, win_ok, s.auto_judge])
        if done == 3:
            self._summary.setText("全部就绪，正在为你判断消息")
        else:
            self._summary.setText(f"还差 {3 - done} 步就能用了")

        self._queue.setText(
            f"当前队列：{store.count()} 条"
            f"（马上回 {store.count_bucket(Bucket.NOW)}"
            f" · 尽快 {store.count_bucket(Bucket.SOON)}）"
        )
        self.btn_run.setText("停止监听" if self._running else "开始监听")

    def _probe_window(self) -> bool:
        try:
            from capture.window import find_target_window

            return find_target_window(self._settings.watch_titles) is not None
        except Exception:
            return False

    _running = False

    def set_running(self, running: bool) -> None:
        self._running = running
        self.btn_run.setText("停止监听" if running else "开始监听")
        self.btn_overlay.setText("隐藏悬浮窗" if running else "显示悬浮窗")

    # -- actions -----------------------------------------------------------
    def _focus_key(self) -> None:
        self.key_input.setFocus()

    def _save_key(self) -> None:
        self._settings.api_key = self.key_input.text().strip()
        self._settings.save()
        self.sync_states()

    def _on_auto_toggle(self, state: int) -> None:
        self._settings.auto_judge = self.auto_check.isChecked()
        self._settings.save()
        self.sync_states()

    def _on_run(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self._save_key()
            self.start_requested.emit()

    # -- appearance --------------------------------------------------------
    def _apply_appearance(self) -> None:
        """Persist and publish the current appearance controls."""
        s = self._settings
        s.overlay_bg = self.color_bg.value()
        s.overlay_fg = self.color_fg.value()
        s.overlay_font_size = self.slider_font.value()
        s.overlay_width = self.slider_width.value()
        s.overlay_opacity = self.slider_opacity.value()
        s.save()
        self._check_contrast(s.overlay_bg, s.overlay_fg)
        self.appearance_changed.emit(s)

    def _on_bg(self, _color: str) -> None:
        # A background change can flip the whole palette (light <-> dark), so
        # re-evaluate the text colour warning but never override the user.
        self._apply_appearance()

    def _on_fg(self, _color: str) -> None:
        self._apply_appearance()

    def _on_font(self, _size: int) -> None:
        self._apply_appearance()

    def _on_width(self, _width: int) -> None:
        self._apply_appearance()

    def _on_opacity(self, percent: int) -> None:
        """Opacity takes a shortcut: it repaints nothing, so it must not go
        through `_apply_appearance()`.

        That path emits `appearance_changed`, which makes the overlay rebuild
        every row. While dragging the slider that fires dozens of times a
        second, and the list would flicker and lose its scroll position. Setting
        the window opacity is the whole effect, so only the settings and the
        live window need touching.
        """
        self._settings.overlay_opacity = percent
        self._settings.save()
        self.opacity_changed.emit(percent)

    def _swap_colors(self) -> None:
        bg, fg = self.color_bg.value(), self.color_fg.value()
        self.color_bg.set_value(fg)
        self.color_fg.set_value(bg)
        self._apply_appearance()

    def _reset_appearance(self) -> None:
        self.color_bg.set_value(DEFAULT_BG)
        self.color_fg.set_value(DEFAULT_FG)
        self.slider_font.set_value(12)
        self.slider_width.set_value(220)
        self.slider_opacity.set_value(DEFAULT_OPACITY)
        self._settings.overlay_opacity = DEFAULT_OPACITY
        self._apply_appearance()
        self.opacity_changed.emit(DEFAULT_OPACITY)

    def _check_contrast(self, bg: str, fg: str) -> None:
        ratio = contrast_ratio(bg, fg)
        if ratio < 3.0:
            self._warn.setText(
                f"⚠ 当前前景/背景对比度仅 {ratio:.1f}:1，文字几乎看不清，建议换一组。"
            )
        elif ratio < 4.5:
            self._warn.setText(
                f"对比度偏低（{ratio:.1f}:1），小字号下可能不好认，建议再拉开一点。"
            )
        else:
            self._warn.setText("")

    def set_appearance_enabled(self, enabled: bool) -> None:
        for w in (self.color_bg, self.color_fg, self.slider_font, self.slider_width):
            w.setEnabled(enabled)

    def _on_test(self) -> None:
        self._save_key()
        if not self._settings.api_key:
            self._test_result.setText("先填 API Key 再测试")
            return
        self._test_result.setText("正在测试…")

        def work() -> None:
            try:
                msg = JevClient(
                    self._settings.api_key, self._settings.model, self._settings.endpoint
                ).probe()
            except Exception as e:
                msg = f"连接失败：{e}"
            # 工作线程没有 Qt 事件循环，必须用信号回 GUI 线程；
            # 早先用 QTimer.singleShot 导致结果永远不显示。
            self.test_finished.emit(msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_autostart_toggled(self, checked: bool) -> None:
        """开关开机自启。写失败就把勾选状态改回去，不骗用户。"""
        if autostart.set_enabled(checked):
            self._test_result.setText(
                "已设为开机自动启动" if checked else "已取消开机自动启动"
            )
        else:
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not checked)
            self.autostart_check.blockSignals(False)
            self._test_result.setText("开机自启设置失败（可能被安全软件拦截）")


def _input_style() -> str:
    return (
        f"QLineEdit {{ background: #FFFFFF; color: {TEXT};"
        f" border: 1px solid {BORDER}; border-radius: 9px; padding: 0 10px;"
        f" font-size: 12.5px; font-family: '{FONT_FAMILY}'; }}"
        f"QLineEdit:focus {{ border: 1px solid {ACCENT}; }}"
    )


def _ghost_style() -> str:
    return (
        f"QPushButton {{ background: {BG_SOFT}; color: {TEXT};"
        f" border: 1px solid {BORDER}; border-radius: 9px; font-size: 12.5px;"
        f" font-family: '{FONT_FAMILY}'; }}"
        f"QPushButton:hover {{ background: #EDEBE6; }}"
    )


# -- appearance controls ---------------------------------------------------
class ColorRow(QWidget):
    """A label, a row of preset swatches and an 'other…' RGB picker.

    Emits the chosen hex colour whenever it changes. The selected preset is
    outline-highlighted; picking a custom colour via the dialog clears the
    preset selection and shows the hex in the pill.
    """

    changed = Signal(str)

    def __init__(self, label: str, presets: list[tuple[str, str]], value: str) -> None:
        super().__init__()
        self._value = value
        self._presets = presets
        self._swatches: dict[str, QPushButton] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        name = QLabel(label)
        name.setFixedWidth(52)
        name.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        top.addWidget(name)

        for text, color in presets:
            sw = QPushButton()
            sw.setFixedSize(24, 24)
            sw.setCursor(Qt.PointingHandCursor)
            sw.setToolTip(f"{text} {color}")
            sw.clicked.connect(lambda _=False, c=color: self._pick(c))
            self._swatches[color] = sw
            top.addWidget(sw)

        top.addStretch(1)
        self._pill = QPushButton()
        self._pill.setFixedHeight(24)
        self._pill.setCursor(Qt.PointingHandCursor)
        self._pill.setToolTip("自定义 RGB")
        self._pill.clicked.connect(self._open_picker)
        top.addWidget(self._pill)
        lay.addLayout(top)

        self._repaint()

    def value(self) -> str:
        return self._value

    def set_value(self, color: str) -> None:
        self._value = color
        self._repaint()

    def _pick(self, color: str) -> None:
        self._value = color
        self._repaint()
        self.changed.emit(color)

    def _open_picker(self) -> None:
        r, g, b = parse_hex(self._value)
        picked = QColorDialog.getColor(
            QColor(r, g, b), self, "选择颜色", QColorDialog.DontUseNativeDialog
        )
        if picked.isValid():
            self._pick(picked.name().upper())

    def _repaint(self) -> None:
        for color, sw in self._swatches.items():
            selected = color.upper() == self._value.upper()
            border = f"2px solid {ACCENT}" if selected else f"1px solid {BORDER}"
            sw.setStyleSheet(
                f"QPushButton {{ background: {color}; border: {border};"
                f" border-radius: 7px; }}"
            )
        r, g, b = parse_hex(self._value)
        # Text colour on the pill is chosen for contrast against the swatch.
        fg = "#FFFFFF" if (0.299 * r + 0.587 * g + 0.114 * b) < 140 else "#2C2C2A"
        self._pill.setText(self._value.upper())
        self._pill.setStyleSheet(
            f"QPushButton {{ background: {self._value}; color: {fg};"
            f" border: 1px solid {BORDER}; border-radius: 7px; padding: 0 9px;"
            f" font-size: 11px; font-family: '{FONT_FAMILY}'; }}"
        )


class SliderRow(QWidget):
    """A label, a slider and a live value readout."""

    changed = Signal(int)

    def __init__(self, label: str, lo: int, hi: int, value: int, suffix: str = "") -> None:
        super().__init__()
        self._suffix = suffix

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        name = QLabel(label)
        name.setFixedWidth(52)
        name.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        lay.addWidget(name)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(value)
        self._slider.setCursor(Qt.PointingHandCursor)
        self._slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {BORDER};"
            f" border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {ACCENT};"
            f" border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px;"
            f" margin: -5px 0; background: {ACCENT}; border-radius: 7px; }}"
        )
        self._slider.valueChanged.connect(self._on_change)
        lay.addWidget(self._slider, 1)

        self._readout = QLabel(self._text(value))
        self._readout.setFixedWidth(46)
        self._readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._readout.setStyleSheet(
            f"color: {TEXT}; font-size: 12px; font-family: '{FONT_FAMILY}';"
        )
        lay.addWidget(self._readout)

    def _text(self, v: int) -> str:
        return f"{v}{self._suffix}"

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, v: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._slider.blockSignals(False)
        self._readout.setText(self._text(v))

    def _on_change(self, v: int) -> None:
        self._readout.setText(self._text(v))
        self.changed.emit(v)
