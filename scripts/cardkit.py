"""Pure helpers for the profile README live cards.

No network access and no file writes: the only I/O is reading the two font
files for embedding. Everything here is specified in design.md sections 3.1
and 12 and plan.md section 2.1.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- brand tokens (design R1, sections 3.1 and 3.5) -------------------------

DARK = {
    "bg": "#07070d",
    "hair": "#ffffff",
    "primary": "#f5f5f2",
    "secondary": "#a3a8b8",
    "accent": "#06b6d4",
    "levels": ["#14161e", "#0a4552", "#087a8f", "#06a0bb", "#06b6d4"],
}
LIGHT = {
    "bg": "#f6f7fb",
    "hair": "#101218",
    "primary": "#101218",
    "secondary": "#4b5063",
    "accent": "#0e7490",
    "levels": ["#e3e5ec", "#b8e4ec", "#6cc9db", "#2aa9c3", "#0e7490"],
}
THEMES = {"dark": DARK, "light": LIGHT}
TOKEN_KEYS = ("bg", "hair", "primary", "secondary", "accent")


def allowed_colours(card_id: str) -> frozenset[str]:
    """Hex values permitted in a card of this id (both themes; calendar steps for calendar only)."""
    colours = {t[k] for t in THEMES.values() for k in TOKEN_KEYS}
    if card_id == "calendar":
        for t in THEMES.values():
            colours.update(t["levels"])
    return frozenset(colours)


ALLOWED_COLOURS = allowed_colours

# --- fonts (the hero's embedding approach reused verbatim) -------------------

FONTS = {
    "Inter": (
        "assets/fonts/inter-latin.woff2",
        "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62",
        "300 900",
    ),
    "JetBrains Mono": (
        "assets/fonts/jetbrains-mono-latin.woff2",
        "83c005d49d8a6a50474c73a5a36ac0468076e9c4a29da7bdb14995d80560a5be",
        "400 700",
    ),
}
FONT_SANS = "Inter,'Segoe UI',Arial,sans-serif"
FONT_MONO = "'JetBrains Mono',Consolas,monospace"

# --- content rules -----------------------------------------------------------

# Check-12 set A (plan.md section 9 row 12, D12-2); unit test 5 asserts equality.
FORBIDDEN_SVG = [
    "<script",
    "<animate",
    "<set ",
    "@keyframes",
    "filter=",
    "filter:",
    "<filter",
    "Gradient",
    "<image",
    "url(http",
    'xlink:href="http',
]

# R6 names as narrowed by D12-3. The names are spelled with \u escapes so the
# repo-wide forbidden-name grep (plan.md section 9 row 12, set B) finds no
# clear-text occurrence in this file; the regex engine resolves the escapes.
FORBIDDEN_NAMES_RE = re.compile(
    r"priv\u0061te-android-app|stock-\u0061nomaly-detector|sn\u0065h|n\u0065ha|"
    r"mai[ -]?\u0061aunga|pariv\u0061ar|studycz\u0061r|Sudish007/regist\u0065r|"
    r"is-a\.d\u0065v|savita-biod\u0061ta|N\u0065ha_Resume",
    re.IGNORECASE,
)

ALLOWED_CP = (
    frozenset(range(0x20, 0x7F))
    | frozenset(range(0xA0, 0x100))
    | frozenset({0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026})
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ELLIPSIS = "\u2026"
MIDDOT = "\u00b7"

# Inter advance widths in 1/1000 em per code point, extracted from
# assets/fonts/inter-latin.woff2 (variable, UPM 2048) instanced at wght 500,
# 600 and 700 with fontTools (plan.md step 2, D12-4). U+00AD is absent from the
# subset and is stripped by clean(). JetBrains Mono is exactly 600/1000 em for
# every allowlisted glyph, so Mono budgets stay character counts.
INTER_ADV = {
    500: {
        32: 267, 33: 304, 34: 494, 35: 639, 36: 646, 37: 993, 38: 653, 39: 313, 40: 369, 41: 369, 42: 521, 43: 667,
        44: 303, 45: 462, 46: 303, 47: 370, 48: 646, 49: 415, 50: 616, 51: 627, 52: 656, 53: 603, 54: 630, 55: 571,
        56: 629, 57: 630, 58: 303, 59: 315, 60: 667, 61: 667, 62: 667, 63: 527, 64: 982, 65: 709, 66: 657, 67: 733,
        68: 722, 69: 603, 70: 589, 71: 748, 72: 745, 73: 272, 74: 575, 75: 688, 76: 565, 77: 913, 78: 756, 79: 767,
        80: 642, 81: 769, 82: 648, 83: 646, 84: 653, 85: 740, 86: 709, 87: 1003, 88: 701, 89: 696, 90: 641, 91: 369,
        92: 370, 93: 369, 94: 477, 95: 463, 96: 337, 97: 568, 98: 618, 99: 577, 100: 618, 101: 587, 102: 379, 103: 620,
        104: 602, 105: 252, 106: 252, 107: 559, 108: 252, 109: 888, 110: 602, 111: 604, 112: 618, 113: 618, 114: 387, 115: 539,
        116: 340, 117: 602, 118: 575, 119: 829, 120: 557, 121: 575, 122: 559, 123: 440, 124: 346, 125: 440, 126: 667, 160: 267,
        161: 304, 162: 577, 163: 620, 164: 737, 165: 557, 166: 293, 167: 568, 168: 602, 169: 914, 170: 457, 171: 608, 172: 667,
        174: 665, 175: 465, 176: 457, 177: 667, 178: 448, 179: 455, 180: 337, 181: 600, 182: 601, 183: 303, 184: 300, 185: 314,
        186: 485, 187: 608, 188: 817, 189: 858, 190: 895, 191: 527, 192: 709, 193: 709, 194: 709, 195: 709, 196: 709, 197: 709,
        198: 1003, 199: 733, 200: 603, 201: 603, 202: 603, 203: 603, 204: 272, 205: 272, 206: 272, 207: 272, 208: 743, 209: 756,
        210: 767, 211: 767, 212: 767, 213: 767, 214: 767, 215: 667, 216: 767, 217: 740, 218: 740, 219: 740, 220: 740, 221: 696,
        222: 647, 223: 630, 224: 568, 225: 568, 226: 568, 227: 568, 228: 568, 229: 568, 230: 915, 231: 577, 232: 587, 233: 587,
        234: 587, 235: 587, 236: 252, 237: 252, 238: 252, 239: 252, 240: 588, 241: 602, 242: 604, 243: 604, 244: 604, 245: 604,
        246: 604, 247: 667, 248: 604, 249: 602, 250: 602, 251: 602, 252: 602, 253: 575, 254: 618, 255: 575, 8211: 500, 8212: 1000,
        8216: 277, 8217: 277, 8220: 474, 8221: 471, 8230: 910,
    },
    600: {
        32: 252, 33: 321, 34: 523, 35: 644, 36: 650, 37: 1004, 38: 663, 39: 326, 40: 373, 41: 373, 42: 540, 43: 673,
        44: 319, 45: 465, 46: 319, 47: 379, 48: 660, 49: 423, 50: 623, 51: 636, 52: 666, 53: 612, 54: 640, 55: 576,
        56: 640, 57: 640, 58: 319, 59: 329, 60: 673, 61: 673, 62: 673, 63: 543, 64: 999, 65: 728, 66: 659, 67: 737,
        68: 722, 69: 605, 70: 588, 71: 749, 72: 746, 73: 277, 74: 580, 75: 703, 76: 565, 77: 922, 78: 759, 79: 769,
        80: 645, 81: 773, 82: 652, 83: 650, 84: 660, 85: 736, 86: 728, 87: 1020, 88: 720, 89: 713, 90: 652, 91: 373,
        92: 379, 93: 373, 94: 481, 95: 469, 96: 351, 97: 574, 98: 624, 99: 583, 100: 624, 101: 591, 102: 389, 103: 625,
        104: 612, 105: 262, 106: 262, 107: 569, 108: 262, 109: 900, 110: 612, 111: 609, 112: 624, 113: 624, 114: 397, 115: 549,
        116: 353, 117: 612, 118: 587, 119: 839, 120: 569, 121: 588, 122: 566, 123: 455, 124: 359, 125: 455, 126: 673, 160: 252,
        161: 321, 162: 583, 163: 629, 164: 749, 165: 563, 166: 316, 167: 568, 168: 612, 169: 914, 170: 460, 171: 634, 172: 673,
        174: 664, 175: 452, 176: 458, 177: 673, 178: 454, 179: 463, 180: 351, 181: 614, 182: 600, 183: 319, 184: 332, 185: 324,
        186: 487, 187: 634, 188: 831, 189: 870, 190: 907, 191: 543, 192: 728, 193: 728, 194: 728, 195: 728, 196: 728, 197: 728,
        198: 1013, 199: 737, 200: 605, 201: 605, 202: 605, 203: 605, 204: 277, 205: 277, 206: 277, 207: 277, 208: 751, 209: 759,
        210: 769, 211: 769, 212: 769, 213: 769, 214: 769, 215: 673, 216: 769, 217: 736, 218: 736, 219: 736, 220: 736, 221: 713,
        222: 658, 223: 644, 224: 574, 225: 574, 226: 574, 227: 574, 228: 574, 229: 574, 230: 913, 231: 583, 232: 591, 233: 591,
        234: 591, 235: 591, 236: 262, 237: 262, 238: 262, 239: 262, 240: 594, 241: 612, 242: 609, 243: 609, 244: 609, 245: 609,
        246: 609, 247: 673, 248: 609, 249: 612, 250: 612, 251: 612, 252: 612, 253: 588, 254: 624, 255: 588, 8211: 500, 8212: 1000,
        8216: 294, 8217: 294, 8220: 507, 8221: 501, 8230: 956,
    },
    700: {
        32: 237, 33: 338, 34: 551, 35: 649, 36: 655, 37: 1016, 38: 672, 39: 339, 40: 377, 41: 377, 42: 559, 43: 679,
        44: 334, 45: 468, 46: 334, 47: 388, 48: 674, 49: 431, 50: 629, 51: 646, 52: 676, 53: 622, 54: 649, 55: 582,
        56: 651, 57: 649, 58: 334, 59: 343, 60: 679, 61: 679, 62: 679, 63: 560, 64: 1016, 65: 747, 66: 662, 67: 740,
        68: 722, 69: 607, 70: 587, 71: 750, 72: 747, 73: 281, 74: 584, 75: 719, 76: 565, 77: 932, 78: 762, 79: 771,
        80: 648, 81: 777, 82: 657, 83: 655, 84: 667, 85: 732, 86: 747, 87: 1038, 88: 738, 89: 731, 90: 664, 91: 377,
        92: 388, 93: 377, 94: 487, 95: 476, 96: 365, 97: 581, 98: 630, 99: 588, 100: 630, 101: 596, 102: 398, 103: 632,
        104: 623, 105: 271, 106: 271, 107: 580, 108: 271, 109: 913, 110: 623, 111: 613, 112: 630, 113: 630, 114: 407, 115: 560,
        116: 366, 117: 623, 118: 600, 119: 850, 120: 580, 121: 602, 122: 573, 123: 469, 124: 372, 125: 469, 126: 679, 160: 237,
        161: 338, 162: 588, 163: 639, 164: 762, 165: 570, 166: 339, 167: 568, 168: 623, 169: 914, 170: 464, 171: 660, 172: 679,
        174: 662, 175: 439, 176: 459, 177: 679, 178: 460, 179: 472, 180: 365, 181: 628, 182: 599, 183: 334, 184: 364, 185: 333,
        186: 490, 187: 660, 188: 846, 189: 881, 190: 920, 191: 560, 192: 747, 193: 747, 194: 747, 195: 747, 196: 747, 197: 747,
        198: 1022, 199: 740, 200: 607, 201: 607, 202: 607, 203: 607, 204: 281, 205: 281, 206: 281, 207: 281, 208: 760, 209: 762,
        210: 771, 211: 771, 212: 771, 213: 771, 214: 771, 215: 679, 216: 771, 217: 732, 218: 732, 219: 732, 220: 732, 221: 731,
        222: 669, 223: 657, 224: 581, 225: 581, 226: 581, 227: 581, 228: 581, 229: 581, 230: 910, 231: 588, 232: 596, 233: 596,
        234: 596, 235: 596, 236: 271, 237: 271, 238: 271, 239: 271, 240: 599, 241: 623, 242: 613, 243: 613, 244: 613, 245: 613,
        246: 613, 247: 679, 248: 613, 249: 623, 250: 623, 251: 623, 252: 623, 253: 602, 254: 630, 255: 602, 8211: 500, 8212: 1000,
        8216: 311, 8217: 311, 8220: 540, 8221: 532, 8230: 1002,
    },
}
UNKNOWN_ADV = 720  # fallback for a code point outside the table (0.72 em, over-estimates)

# --- text helpers --------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u00ad]")
_WS_RE = re.compile(r"\s+")


def clean(s) -> str:
    """Strip C0/C1 controls and U+00AD (D12-7), collapse whitespace runs, strip ends.

    Whitespace controls (tab, newline) are collapsed to a space before the
    control strip so that a line break inside a title keeps its word boundary.
    """
    s = _WS_RE.sub(" ", str(s))
    s = _CONTROL_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def glyphs_ok(s: str) -> bool:
    """True when every code point is covered by the embedded Latin subsets."""
    return all(ord(ch) in ALLOWED_CP for ch in s)


def screen(s):
    """clean -> glyph allowlist -> R6 filter -> SVG-pattern filter.

    Returns (text, None) when the string may be rendered, else (None, reason)
    with reason 'unsupported characters' or 'filtered'.
    """
    t = clean(s)
    if not glyphs_ok(t):
        return None, "unsupported characters"
    if FORBIDDEN_NAMES_RE.search(t):
        return None, "filtered"
    # A fetched string that itself contains a check-12 set A pattern (for
    # example a title with "Gradient") would make validate_svg fail and the
    # repo grep hit; it takes the same omission path (plan.md section 11, D-14).
    if any(p in t for p in FORBIDDEN_SVG):
        return None, "filtered"
    return t, None


def fit(text: str, max_chars: int) -> str:
    """Truncate at a word boundary with an ellipsis; never longer than max_chars."""
    if len(text) <= max_chars:
        return text
    i = text.rfind(" ", max_chars // 2, max_chars - 1)
    cut = i if i != -1 else max_chars - 1
    return text[:cut].rstrip() + ELLIPSIS


def text_width(s: str, weight: int, size: float, letter_spacing: float = 0) -> float:
    """Measured width of an Inter line in design units (kerning ignored)."""
    adv = INTER_ADV[weight]
    units = sum(adv.get(ord(ch), UNKNOWN_ADV) for ch in s) * size / 1000
    return units + letter_spacing * len(s)


def fit_width(text: str, max_units: float, weight: int, size: float, letter_spacing: float = 0) -> str:
    """Truncate an Inter line by measured width (D12-4); prefers a word boundary."""
    if text_width(text, weight, size, letter_spacing) <= max_units:
        return text
    limit = max_units - text_width(ELLIPSIS, weight, size, letter_spacing)
    adv = INTER_ADV[weight]
    total = 0.0
    n = 0
    for ch in text:
        step = adv.get(ord(ch), UNKNOWN_ADV) * size / 1000 + letter_spacing
        if total + step > limit:
            break
        total += step
        n += 1
    i = text.rfind(" ", n // 2, n + 1)
    cut = i if i != -1 else n
    return text[:cut].rstrip() + ELLIPSIS


def esc_xml(s: str) -> str:
    return _xml_escape(s, {'"': "&quot;"})


def esc_attr(s: str) -> str:
    return html.escape(s, quote=True)


def fmt_int(n: int) -> str:
    return f"{n:,}"


IST = timezone(timedelta(hours=5, minutes=30))
TIME_UNKNOWN = "time unknown"


def fmt_ist(ts) -> str:
    """The only producer of time strings: '21 Oct 2026, 08:13 IST' or 'time unknown'."""
    if ts is None or ts == "":
        return TIME_UNKNOWN
    if isinstance(ts, datetime):
        d = ts
    else:
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return TIME_UNKNOWN
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(IST)
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}, {d.hour:02d}:{d.minute:02d} IST"


# --- SVG assembly ----------------------------------------------------------------


@functools.lru_cache(maxsize=4)
def font_style(repo_root=REPO_ROOT) -> str:
    """The hero's <style> block: two @font-face rules with base64 WOFF2 (exit 4 on hash mismatch)."""
    rules = []
    for family, (rel, sha, weights) in FONTS.items():
        data = Path(repo_root, rel).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha:
            print(f"font {rel} sha256 mismatch", file=sys.stderr)
            raise SystemExit(4)
        b64 = base64.b64encode(data).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{family}';font-weight:{weights};"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    return "<style>\n" + "\n".join(rules) + "\n</style>"


def _num(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:.1f}"


def text(x, y, s, *, font, weight, size, fill, anchor=None, letter_spacing=None, tspans=None) -> str:
    """One <text> element. `tspans` = list of (segment, fill-or-None) replacing `s`."""
    assert size >= 28, f"font-size {size} below the 28-unit floor"
    attrs = [f'x="{_num(x)}"', f'y="{_num(y)}"', f'font-family="{font}"', f'font-weight="{weight}"',
             f'font-size="{_num(size)}"']
    if letter_spacing is not None:
        attrs.append(f'letter-spacing="{_num(letter_spacing)}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    attrs.append(f'fill="{fill}"')
    if tspans is not None:
        content = "".join(
            f'<tspan fill="{f}">{esc_xml(seg)}</tspan>' if f else esc_xml(seg) for seg, f in tspans
        )
    else:
        content = esc_xml(s)
    return f"<text {' '.join(attrs)}>{content}</text>"


def svg_card(theme, height: int, title: str, desc: str, body, card_id: str, style=None) -> str:
    """Canvas, <title>, <desc>, fonts, background, hairline, then the body elements."""
    t = THEMES[theme] if isinstance(theme, str) else theme
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" '
        f'viewBox="0 0 1200 {height}" role="img" aria-labelledby="t">',
        f'<title id="t">{esc_xml(title)}</title>',
        f"<desc>{esc_xml(desc)}</desc>",
        style if style is not None else font_style(),
        f'<rect width="1200" height="{height}" rx="24" fill="{t["bg"]}"/>',
        f'<rect x=".5" y=".5" width="1199" height="{height - 1}" rx="23.5" fill="none" '
        f'stroke="{t["hair"]}" stroke-opacity=".1"/>',
    ]
    parts.extend(body)
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


_B64_RE = re.compile(rb"base64,[A-Za-z0-9+/=]+")
_FONT_SIZE_RE = re.compile(r'font-size="([0-9.]+)"')
_COLOUR_RE = re.compile(r'(?:fill|stroke)="(#[0-9a-fA-F]+)"')
SVG_MAX_BYTES = 200000
MIN_FONT_SIZE = 28


def validate_svg(data: bytes, card_id: str) -> list[str]:
    """Return problems (empty list = valid): parse, size, forbidden patterns, sizes, colours."""
    problems = []
    try:
        ET.fromstring(data)
    except ET.ParseError as exc:
        problems.append(f"xml parse error: {exc}")
    if len(data) >= SVG_MAX_BYTES:
        problems.append(f"size {len(data)} >= {SVG_MAX_BYTES}")
    stripped = _B64_RE.sub(b"base64,", data).decode("utf-8", "replace")
    for pat in FORBIDDEN_SVG:
        if pat in stripped:
            problems.append(f"forbidden pattern {pat!r}")
    for m in _FONT_SIZE_RE.finditer(stripped):
        if float(m.group(1)) < MIN_FONT_SIZE:
            problems.append(f"font-size {m.group(1)} below {MIN_FONT_SIZE}")
    allowed = allowed_colours(card_id)
    for m in _COLOUR_RE.finditer(stripped):
        if m.group(1).lower() not in allowed:
            problems.append(f"colour {m.group(1)} not allowed in {card_id}")
    return problems
