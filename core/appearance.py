"""Overlay appearance: colours, font size, width — plus the derived shades.

The user picks two colours only (background + text). Everything else the overlay
needs — the row tint, the hairline border, the muted "why" line, the divider —
is derived from those two, so any combination stays coherent. Deriving rather
than asking keeps the settings to a single row.

Kept free of Qt on purpose, so the maths can be mirrored on Android.
"""

from __future__ import annotations

from core.msg_item import Bucket

# -- canonical bucket accents ---------------------------------------------
# 红=马上回, 黄=尽快, 灰=可以晚点, 蓝=判断中. One source of truth for both
# the overlay and the Android build.
BUCKET_COLOR = {
    Bucket.NOW: "#D6453D",
    Bucket.SOON: "#D98A1F",
    Bucket.LATER: "#9B9A94",
    Bucket.PENDING: "#3B7DD8",
}

# -- presets ---------------------------------------------------------------
# (label, colour). A deliberately small, legible set; the RGB sliders cover
# everything else.
BG_PRESETS: list[tuple[str, str]] = [
    ("白", "#FFFFFF"),
    ("米", "#F5F4F1"),
    ("浅灰", "#E9E8E4"),
    ("浅蓝", "#EAF1FC"),
    ("深灰", "#2C2C2A"),
    ("黑", "#141413"),
]

FG_PRESETS: list[tuple[str, str]] = [
    ("黑", "#2C2C2A"),
    ("白", "#FFFFFF"),
    ("深灰", "#4A4945"),
    ("蓝", "#1F4FA8"),
    ("红", "#B3372F"),
    ("绿", "#1E7A4B"),
]

DEFAULT_BG = "#FFFFFF"
DEFAULT_FG = "#2C2C2A"
MIN_FONT = 10
MAX_FONT = 20
MIN_WIDTH = 180
MAX_WIDTH = 340
# Opacity is a percentage. The floor is 1 rather than 0: a fully transparent
# card would still be clickable but impossible to see or drag back, which reads
# as "the app broke". Mirrors Prefs.MIN_OPACITY on Android.
DEFAULT_OPACITY = 100
MIN_OPACITY = 1
MAX_OPACITY = 100


# -- colour maths ----------------------------------------------------------
def parse_hex(value: str, fallback: str = DEFAULT_BG) -> tuple[int, int, int]:
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return parse_hex(fallback, "#FFFFFF")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return parse_hex(fallback, "#FFFFFF")


def to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def luminance(color: str) -> float:
    """WCAG relative luminance, 0 (black) .. 1 (white)."""
    parts = []
    for c in parse_hex(color):
        v = c / 255.0
        parts.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = parts
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_dark(color: str) -> bool:
    return luminance(color) < 0.45


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """Blend a -> b by t (0 = a, 1 = b)."""
    ra, ga, ba = parse_hex(a)
    rb, gb, bb = parse_hex(b)
    return to_hex(
        (
            round(ra + (rb - ra) * t),
            round(ga + (gb - ga) * t),
            round(ba + (bb - ba) * t),
        )
    )


def readable_on(bg: str, light: str = "#FFFFFF", dark: str = "#2C2C2A") -> str:
    """Whichever candidate has more contrast on bg."""
    return light if contrast_ratio(bg, light) >= contrast_ratio(bg, dark) else dark


# -- derived palette -------------------------------------------------------
class Palette:
    """Every colour the overlay paints, derived from just bg + fg.

    `dark` flips the whole derivation: on a dark card, tints are made by
    lightening toward the text colour, and the hairline border lightens too.
    """

    def __init__(self, bg: str, fg: str) -> None:
        self.bg = bg
        self.fg = fg
        self.dark = is_dark(bg)
        tint = 0.10 if self.dark else 0.06

        # Row tint: nudge the background toward the text colour, very slightly.
        self.row_bg = mix(bg, fg, tint)
        self.row_bg_hover = mix(bg, fg, tint + 0.05)
        self.border = mix(bg, fg, 0.26 if self.dark else 0.16)
        self.divider = mix(bg, fg, 0.18 if self.dark else 0.10)
        # Secondary text: text colour pulled back toward the background.
        self.text_soft = mix(fg, bg, 0.34 if self.dark else 0.42)
        self.text_faint = mix(fg, bg, 0.52 if self.dark else 0.62)
        self.title = fg
        self.scroll = mix(bg, fg, 0.34 if self.dark else 0.22)
        # Text/icon colour that survives on top of a saturated chip.
        self.on_bucket = {
            b: readable_on(c) for b, c in self.bucket.items()
        }

    @property
    def bucket(self) -> dict[Bucket, str]:
        """Accent colours, lightened on a dark card so they stay visible."""
        return {
            b: (mix(c, "#FFFFFF", 0.30) if self.dark else c)
            for b, c in BUCKET_COLOR.items()
        }

    @property
    def bucket_bg(self) -> dict[Bucket, str]:
        """Tinted chip behind each row's colour bar / tag."""
        return {
            b: mix(self.bg, c, 0.22 if self.dark else 0.13)
            for b, c in self.bucket.items()
        }

    def qss(self, font_family: str) -> str:
        return f"""
        QWidget {{ font-family: '{font_family}'; color: {self.fg}; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {self.scroll}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """
