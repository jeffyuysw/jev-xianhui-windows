"""Windows capture layer.

Reads the WeChat window with Windows Graphics Capture (WGC) and runs offline
Chinese OCR over it. This route is forced, not chosen: WeChat for Windows 4.x
renders its UI onto a GPU-composited canvas, so the UIA tree exposes only a
couple of top-level nodes and no message text. Screenshot + OCR is the only
clean way to read what is on screen. The same conclusion is documented by
https://github.com/jev-chat/jev-chat-windows

Hard rules kept from the reference design:
  * We only ever capture our own target window, never the whole desktop.
  * A frame lives in memory only; nothing is written to disk.
  * We never type, send, or otherwise touch the other app.
"""

__all__ = ["find_target_window", "capture_window", "WindowInfo"]
