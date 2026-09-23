"""先回 (XianHui) for Windows - floating priority list over WeChat.

Split so the judgment kernel stays platform-independent:
  core/     judgment only (same questions as the Android build)
  capture/  window screenshot + offline OCR + parsing
  ui/       PySide6 windows

See README.md for the second-development attribution.
"""

__version__ = "1.0.0"
