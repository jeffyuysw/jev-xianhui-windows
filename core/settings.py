"""Settings persisted to %APPDATA%\\JevPriority\\config.json.

The directory keeps the old internal name on purpose: renaming it would orphan
any API key a user already saved. Only the user-facing product name changed
(先回). Mirrors 先回 (Android) `core/Prefs.kt`. The API key lives here and is
sent only inside the judgment request itself; it is never logged.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "JevPriority"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Settings:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT

    # Judge every captured message as it arrives. Off = capture only.
    auto_judge: bool = True

    # 0 = always on top of normal windows; the overlay is a tool window.
    overlay_x: int = -1          # -1 means "not placed yet" -> left edge
    overlay_y: int = -1
    overlay_collapsed: bool = False

    # Poll interval for the capture loop, milliseconds.
    poll_ms: int = 900

    # Only judge messages from these window titles (substring match, empty = any).
    watch_titles: list[str] = field(default_factory=lambda: ["微信", "WeChat"])

    # Preferred: match the owning executable instead of the title. WeChat's own
    # window title is just "微信", but so is any browser tab that mentions it -
    # and a maximised browser is bigger, so it used to win the "largest window"
    # tie-break and we ended up OCRing a web page.
    watch_processes: list[str] = field(
        default_factory=lambda: ["weixin.exe", "wechat.exe"]
    )

    # -- overlay appearance ------------------------------------------------
    # Defaults are white-on-nothing / black text, i.e. a plain light card.
    # Stored per-machine: the Android build keeps its own copy in Prefs.kt, so
    # the two platforms are deliberately not synced.
    overlay_bg: str = "#FFFFFF"
    overlay_fg: str = "#2C2C2A"
    overlay_font_size: int = 12
    overlay_width: int = 220

    # Card opacity as a percentage, 1..100. Applied as the window's opacity (not
    # per-widget), so card fill, text and bars fade as one and the card never
    # shows internal seams. 1 is the floor: at 0 the card would be invisible and
    # impossible to grab and raise again.
    overlay_opacity: int = 100

    # Snapshot of the appearance block, for dirty-checking in the settings UI.
    def appearance(self) -> tuple[str, str, int, int]:
        return (
            self.overlay_bg,
            self.overlay_fg,
            int(self.overlay_font_size),
            int(self.overlay_width),
        )

    def opacity(self) -> float:
        """`overlay_opacity` as the 0.01..1.0 that Qt's setWindowOpacity wants."""
        return max(1, min(100, int(self.overlay_opacity))) / 100.0

    def save(self) -> None:
        tmp = config_path().with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(config_path())

    @classmethod
    def load(cls) -> "Settings":
        p = config_path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
