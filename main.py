"""Application entry point: wires windows, tray, capture worker together."""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from capture.worker import CaptureWorker
from core.settings import Settings
from core.store import store
from resources import asset
from ui import __version__
from ui.overlay import PriorityOverlay
from ui.settings_window import SettingsWindow


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

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()

        # The capture worker mutates the store from a background thread, but
        # every listener touches Qt widgets. Route notifications through the
        # GUI thread, otherwise paints get dropped or the app freezes.
        store.mark_gui_thread()
        store.set_dispatcher(self._dispatch_to_gui)

        self.settings_window = SettingsWindow(self.settings)
        self.overlay = PriorityOverlay(self.settings)
        self.worker = CaptureWorker(self.settings)

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

        # Built once, at startup. (It used to live at the tail of
        # _dispatch_to_gui, which meant a brand-new tray icon was created on
        # every store notification — thousands of leaked icons and a frozen UI.)
        self._tray: QSystemTrayIcon | None = None
        self._build_tray()

    def _dispatch_to_gui(self, fn) -> None:
        """Run fn on the GUI thread, from any thread."""
        # QTimer.singleShot queues the callable on the current thread's event
        # loop; since this runs on the GUI thread, fn lands there too.
        QTimer.singleShot(0, fn)

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
        """A tap drops the row; the user then answers in WeChat themselves."""
        store.remove(key)

    def _on_opacity_changed(self, percent: int) -> None:
        """Live opacity, minus the row rebuild that `apply_appearance` does."""
        self.settings.overlay_opacity = percent
        self.overlay.apply_opacity(self.settings)

    def _show_settings(self) -> None:
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def quit(self) -> None:
        self.worker.stop()
        self.tray.hide()
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
