"""Fonts and text layout shared by all modes.

Fonts are loaded once and cached; the old scripts re-read the TTF from disk
on every run.
"""
import functools
import os
import re

from PIL import ImageFont

from .settings import ASSETS_DIR

FONT_DIR = os.path.join(ASSETS_DIR, "fonts")
FONT_EXT = (".ttf", ".otf", ".ttc")

# Fonts referenced by number in older configs and inside the modes.
LEGACY = {1: "Font.ttc", 2: "DejaVuSansMono.ttf", 3: "FrederickatheGreat-Regular.ttf",
          4: "RubikDoodleShadow-Regular.ttf", 5: "GravitasOne-Regular.ttf",
          6: "SpecialElite-Regular.ttf", 7: "Gluten-Light.ttf", 8: "04B_08.ttf"}
NICE = {"Font.ttc": "Sans", "DejaVuSansMono.ttf": "Mono", "04B_08.ttf": "Pixel",
        "FrederickatheGreat-Regular.ttf": "Fredericka", "RubikDoodleShadow-Regular.ttf": "Rubik Doodle",
        "GravitasOne-Regular.ttf": "Gravitas", "SpecialElite-Regular.ttf": "Special Elite",
        "Gluten-Light.ttf": "Gluten"}
_scan = {"mtime": None, "files": []}


def _pretty(filename):
    if filename in NICE:
        return NICE[filename]
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"[-_](Regular|Book|Normal)$", "", stem, flags=re.I)
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem).replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip() or filename


def available():
    """Every font file in assets/fonts, as [{"id": filename, "name": label}]."""
    try:
        mtime = os.path.getmtime(FONT_DIR)
    except OSError:
        return []
    if mtime != _scan["mtime"]:
        files = sorted((f for f in os.listdir(FONT_DIR) if f.lower().endswith(FONT_EXT)), key=lambda f: _pretty(f).lower())
        _scan.update(mtime=mtime, files=files)
    return [{"id": f, "name": _pretty(f)} for f in _scan["files"]]


def resolve(key):
    """A font file for a config value: a filename, or an old-style number.
    Missing files fall back to Sans, then to anything present."""
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        key = LEGACY.get(int(key), "Font.ttc")
    if key and os.path.exists(os.path.join(FONT_DIR, str(key))):
        return str(key)
    files = [f["id"] for f in available()]
    if "Font.ttc" in files:
        return "Font.ttc"
    if files:
        return files[0]
    raise FileNotFoundError(f"no fonts in {FONT_DIR}")


@functools.lru_cache(maxsize=128)
def _load(filename, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, filename), int(size))


def font(key, size):
    return _load(resolve(key), size)


def text_width(draw, text, fnt):
    left, _, right, _ = draw.textbbox((0, 0), text, font=fnt)
    return right - left


_TOFU = {}
_DRAWS = {}


def draws(fnt, text):
    """Can this font actually draw all of these characters? A missing glyph
    comes out as an empty box, which looks like a bug on a face."""
    from PIL import Image, ImageDraw

    def shape(ch):
        im = Image.new("L", (48, 48), 255)
        ImageDraw.Draw(im).text((2, 2), ch, font=fnt, fill=0)
        return im.tobytes()

    key = id(fnt)
    seen = _DRAWS.get((key, text))
    if seen is not None:                        # asked every frame, so remember it
        return seen
    if key not in _TOFU:
        _TOFU[key] = shape("\uFFFF")            # nothing has a glyph for this
    answer = all(shape(ch) != _TOFU[key] for ch in set(text) if not ch.isspace())
    if len(_DRAWS) > 200:
        _DRAWS.clear()
    _DRAWS[(key, text)] = answer
    return answer


def line_height(fnt):
    ascent, descent = fnt.getmetrics()
    return ascent + descent


def wrap(draw, text, fnt, max_width, max_lines=None):
    """Word-wrap text to max_width pixels. Honours explicit newlines and
    starts a new line at "--" or "A:" (joke punchlines)."""
    text = text or ""
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            if word in ("--", "A:") and current:
                lines.append(current)
                current = word
                continue
            candidate = f"{current} {word}".strip()
            if text_width(draw, candidate, fnt) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    if max_lines:
        lines = lines[:max_lines]
    return lines


def draw_block(draw, text, fnt, box, fg, align="left", valign="top", pad=4):
    """Wrap and draw text inside box=(x0, y0, x1, y1)."""
    text = text or ""
    x0, y0, x1, y1 = box
    width = x1 - x0 - 2 * pad
    height = y1 - y0 - 2 * pad
    lh = line_height(fnt)
    room = max(1, int(height // lh))
    lines = wrap(draw, text, fnt, width)
    if len(lines) > room:                     # more than fits: say so on the last line
        lines = lines[:room]
        last = lines[-1].rstrip()
        while last and text_width(draw, last + "…", fnt) > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    total = len(lines) * lh

    if valign == "center":
        y = y0 + pad + (height - total) // 2
    elif valign == "bottom":
        y = y1 - pad - total
    else:
        y = y0 + pad

    for line in lines:
        w = text_width(draw, line, fnt)
        if align == "center":
            x = x0 + pad + (width - w) // 2
        elif align == "right":
            x = x1 - pad - w
        else:
            x = x0 + pad
        draw.text((x, y), line, font=fnt, fill=fg)
        y += lh
    return lines
