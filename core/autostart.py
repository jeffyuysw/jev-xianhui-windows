"""开机自动启动（Windows 注册表 Run 项）。

用 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，**不需要管理员
权限**，也不会弹 UAC。写入的是启动命令本身：

  * 打包后（PyInstaller）：直接指向 XianHui.exe
  * 源码运行：指向 venv 里的 pythonw.exe + main.py 绝对路径（pythonw 不弹
    黑窗口）

main.py 启动时会把自己所在的目录设为工作目录，所以从注册表拉起时也能正常
找到 capture/、core/、ui/ 这些包。
"""

from __future__ import annotations

import os
import sys

try:
    import winreg
except ImportError:  # 非 Windows，仅用于让 lint / 测试不炸
    winreg = None  # type: ignore

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "XianHui"


def _launch_command() -> str:
    """开机时执行的命令行。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：sys.executable 就是 XianHui.exe 本身
        return '"%s"' % sys.executable

    exe = sys.executable or ""
    # 优先用同目录的 pythonw.exe（无控制台窗口）
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw

    main_py = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
    )
    return '"%s" "%s"' % (exe, main_py)


def is_enabled() -> bool:
    """当前是否已设置开机自启。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, _VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """开启 / 关闭开机自启，返回是否成功。"""
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            if enabled:
                winreg.SetValueEx(k, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(k, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def current_command() -> str:
    """注册表里记的启动命令，便于排查。"""
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            return str(winreg.QueryValueEx(k, _VALUE_NAME)[0])
    except OSError:
        return ""
