"""Application entry point: wires windows, tray, capture worker together."""

from __future__ import annotations

import os
import sys

# 必须在导入 capture/core/ui 之前切工作目录：开机自启（注册表 Run）拉起时
# cwd 是 C:\Windows\System32，包就找不到了。切到自己所在目录后，无论从哪
# 里启动都能正常导入。
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from capture.worker import CaptureWorker
from capture.window import focus_app_window
from core import msg_history
from core.settings import Settings
from core.store import store
from resources import asset
from ui import __version__
from ui.history_window import HistoryWindow
from ui.overlay import PriorityOverlay
from ui.settings_window import SettingsWindow


# 点悬浮窗里某条消息时，要跳到哪个应用 —— 应用名 → 候选进程名。
# 应用名由 capture/worker.py 的 _app_label() 按进程名归一化，所以这里
# 能自动区分微信和 QQ，不需要用户额外配置。
APP_PROCESSES: dict[str, list[str]] = {
    "微信": ["weixin.exe", "wechat.exe"],
    "QQ": ["qq.exe", "qqnt.exe"],
    "TIM": ["tim.exe"],
    "企业微信": ["wework.exe", "wxwork.exe"],
    "钉钉": ["dingtalk.exe"],
    "飞书": ["feishu.exe", "lark.exe"],
}


def _app_icon() -> QIcon:
    """应用图标：优先加载 assets 下的真实图标，缺失时回退为内置绘制图形。"""
    for name in ("app.ico", "app_icon_256.png"):
        path = asset(name)
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return _fallback_icon()


def _fallback_icon() -> QIcon:
    """兜底图标：蓝底圆角方块 + 白色「先」字，无需外部资源。"""
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#0354FC"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 8, 240, 240, 54, 54)
    p.setPen(QColor("#FFFFFF"))
    f = p.font()
    f.setPixelSize(140)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "先")
    p.end()
    return QIcon(pm)


class App(QObject):
    status_message = Signal(str)

    # 把「要在 GUI 线程执行的可调用对象」排队过去。
    #
    # 不能用 QTimer.singleShot：它要求调用线程自己有 Qt 事件循环，而采集线程是
    # 普通 threading.Thread（没有事件循环），定时器永远不会触发 —— 表现为
    # 「测试连接没有结果」「状态不更新」。跨线程信号则会自动排队到接收者所在
    # 的线程，这才是正确做法。（已用最小用例实测确认两种方式的差别。）
    _dispatch = Signal(object)

    # 采集线程的状态消息（「没找到微信窗口」之类）经这个信号回到 GUI 线程，
    # 再由悬浮窗显示成提示条。
    worker_status = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()

        # The capture worker mutates the store from a background thread, but
        # every listener touches Qt widgets. Route notifications through the
        # GUI thread, otherwise paints get dropped or the app freezes.
        store.mark_gui_thread()
        store.set_dispatcher(self._dispatch_to_gui)
        self._dispatch.connect(self._run_on_gui)

        self.settings_window = SettingsWindow(self.settings)
        self.overlay = PriorityOverlay(self.settings)
        self.history_window = HistoryWindow()
        self.worker = CaptureWorker(self.settings, on_status=self.worker_status.emit)
        self.worker_status.connect(self._on_worker_status)

        self.settings_window.history_requested.connect(self._show_history)

        self.overlay.row_tapped.connect(self._on_row_tapped)
        self.overlay.closed.connect(self._on_overlay_closed)

        self.settings_window.start_requested.connect(self.start)
        self.settings_window.stop_requested.connect(self.stop)
        self.settings_window.clear_requested.connect(self.clear)
        self.settings_window.overlay_toggle_requested.connect(self.toggle_overlay)

        # Appearance changes must repaint the overlay immediately.
        self.settings_window.appearance_changed.connect(self.overlay.apply_appearance)
        # Opacity is a lighter signal on purpose: it must not rebuild the rows,
        # or dragging the slider would flicker the list.
        self.settings_window.opacity_changed.connect(self._on_opacity_changed)

        store.add_listener(self.settings_window.sync_states)
        # 分析记录窗口开着时，新判断结果要立刻出现在列表里
        # （refresh_if_visible 自己会判断窗口是否可见）。
        store.add_listener(self.history_window.refresh_if_visible)

        # Built once, at startup. (It used to live at the tail of
        # _dispatch_to_gui, which meant a brand-new tray icon was created on
        # every store notification — thousands of leaked icons and a frozen UI.)
        self._tray: QSystemTrayIcon | None = None
        self._build_tray()

    def _run_on_gui(self, fn) -> None:
        """信号槽的这一端永远在 GUI 线程，直接调用即可。"""
        try:
            fn()
        except Exception:
            pass  # 单个监听器出错不能拖垮界面

    def _on_worker_status(self, msg: str) -> None:
        """采集线程的状态 → 悬浮窗顶部提示条。"""
        self.overlay.set_status_hint(msg)

    def _dispatch_to_gui(self, fn) -> None:
        """Run fn on the GUI thread, from any thread."""
        self._dispatch.emit(fn)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon())
        self.tray.setToolTip(f"先回 v{__version__}")
        menu = QMenu()
        act_show = QAction("打开主界面", self)
        act_show.triggered.connect(self._show_settings)
        act_overlay = QAction("显示/隐藏悬浮窗", self)
        act_overlay.triggered.connect(self.toggle_overlay)
        act_toggle = QAction("开始/停止监听", self)
        act_toggle.triggered.connect(self._toggle_run)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit)
        for a in (act_show, act_overlay, act_toggle):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_settings()
            if reason == QSystemTrayIcon.DoubleClick
            else None
        )
        self.tray.show()

    # -- run control -------------------------------------------------------
    def start(self) -> None:
        self.worker.start()
        self.settings_window.set_running(True)
        self.overlay.show()

    def stop(self) -> None:
        self.worker.stop()
        self.settings_window.set_running(False)
        self.overlay.hide()

    def _toggle_run(self) -> None:
        if self.worker.running:
            self.stop()
        else:
            self.start()

    def clear(self) -> None:
        store.clear()

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()

    def _on_overlay_closed(self) -> None:
        pass

    def _on_row_tapped(self, key: str) -> None:
        """点某条消息 → 把对应的聊天应用切到前台，然后这条从列表里去掉。

        用户点它就是要马上去回，所以跳转即视为已处理。找不到对应应用
        （比如已经退出）时也照样移除，免得留一条点不动的死行。
        """
        item = store.get(key)
        if item is None:
            return
        procs = APP_PROCESSES.get(item.app_name)
        if procs:
            focus_app_window(procs)
        store.remove(key)

    def _on_opacity_changed(self, percent: int) -> None:
        """Live opacity, minus the row rebuild that `apply_appearance` does."""
        self.settings.overlay_opacity = percent
        self.overlay.apply_opacity(self.settings)

    def _show_settings(self) -> None:
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _show_history(self) -> None:
        """打开「分析记录」。每次打开都重新读，免得看到过期的列表。"""
        self.history_window.reload()
        self.history_window.show()
        self.history_window.raise_()
        self.history_window.activateWindow()

    def quit(self) -> None:
        self.worker.stop()
        self.tray.hide()
        # 关掉本机记录库的连接，别指望进程退出时系统替我们收尾。
        try:
            msg_history.get().close()
        except Exception:
            pass
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("先回")
    # 应用级图标：任务栏、窗口标题栏、Alt+Tab 均使用
    app.setWindowIcon(_app_icon())
    app.setQuitOnLastWindowClosed(False)

    a = App()
    a.settings_window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
