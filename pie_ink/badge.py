"""Small marks laid over whatever screen is showing.

Two little speech bubbles in a corner while the bots on two PiE-inks are talking to each other,
so you can tell at a glance without opening the page (kept off the group-chat screen itself,
where the conversation is the whole picture); and a toast — a few words in a box along the
bottom for a moment, saying what a button did when the screen alone wouldn't ("Photo saved",
"Dark") or what the bot just said. Drawn onto the finished frame, so no screen has to know.
"""
import logging
import threading
import time

from PIL import ImageDraw

log = logging.getLogger(__name__)


def _bubbles(draw, x, y, size, fg, bg):
    """Two overlapping speech bubbles, about `size` pixels tall."""
    w = int(size * 1.15)
    h = int(size * 0.72)
    # the back bubble, outlined
    draw.rounded_rectangle([x, y, x + w, y + h], radius=max(2, size // 4), outline=fg, width=max(1, size // 8), fill=bg)
    draw.polygon([(x + w * 0.3, y + h), (x + w * 0.42, y + h), (x + w * 0.25, y + h + size * 0.22)], fill=fg)
    # the front bubble, solid, offset down-right
    ox, oy = int(size * 0.45), int(size * 0.35)
    draw.rounded_rectangle([x + ox, y + oy, x + ox + w, y + oy + h], radius=max(2, size // 4), fill=fg)
    draw.polygon([(x + ox + w * 0.58, y + oy + h), (x + ox + w * 0.7, y + oy + h),
                  (x + ox + w * 0.78, y + oy + h + size * 0.22)], fill=fg)
    # three dots in the front bubble
    r = max(1, size // 10)
    cy = y + oy + h // 2
    for i in range(3):
        cx = x + ox + w * (0.28 + 0.22 * i)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bg)


_toast = {"text": "", "until": 0.0}


def toast(text, seconds=2.0, poke=None):
    """A few words along the bottom of the screen for `seconds`. `poke` (the display's) redraws now, and
    again when they've gone."""
    _toast.update(text=" ".join(str(text or "").split()) if "\n" not in str(text or "") else str(text).strip(),
                  until=time.time() + float(seconds))
    if poke:
        poke()
        t = threading.Timer(float(seconds) + 0.05, poke)
        t.daemon = True
        t.start()


def toasting():
    """The words showing now, or ""."""
    return _toast["text"] if time.time() < _toast["until"] else ""


def _draw_toast(image, text, dark):
    from .text import font, line_height, wrap
    image = image.copy()
    draw = ImageDraw.Draw(image)
    w, h = image.size
    base = min(w, h * 4 / 3) * 0.055
    for scale in (1.0, 0.82, 0.68):                     # smaller for a longer line (a joke, what the bot said)
        fnt = font("Font.ttc", max(9, round(base * scale)))
        pad = max(3, round(fnt.size * 0.45))
        lines = wrap(draw, text, fnt, int(w * 0.9) - 2 * pad) or [""]
        if len(lines) <= 3:
            break
    lines = lines[:5]
    lh = line_height(fnt)
    tw = max(draw.textbbox((0, 0), ln, font=fnt)[2] for ln in lines)
    bw, bh = min(w - 2 * pad, tw + 2 * pad), lh * len(lines) + 2 * pad
    x0, y1 = (w - bw) // 2, h - max(2, round(h * 0.04))
    y0 = y1 - bh
    box, ink, edge = (0, 255, 255) if image.mode != "RGB" else ((24, 26, 30), (255, 255, 255), (255, 255, 255))
    draw.rounded_rectangle([x0, y0, x0 + bw, y1], radius=max(2, pad), fill=box,
                           outline=edge if dark else None, width=max(1, pad // 3))
    for i, ln in enumerate(lines):
        lw = draw.textbbox((0, 0), ln, font=fnt)[2]
        draw.text((x0 + (bw - lw) // 2, y0 + pad + i * lh), ln, font=fnt, fill=ink)
    return image


def apply(image, config, mode_name=""):
    """Draw whatever marks apply onto this frame and hand it back."""
    image = _bubbles_on(image, config, mode_name)
    words = toasting()
    if words and mode_name != "off":
        try:
            image = _draw_toast(image, words, config.get("display", {}).get("dark_mode", False))
        except Exception:
            log.debug("couldn't draw the toast", exc_info=True)
    return image


def _bubbles_on(image, config, mode_name=""):
    """The two speech bubbles, while the bots talk."""
    try:
        from . import social
        if mode_name in ("groupchat", "off") or not social.talking():
            return image
        if not config.get("social", {}).get("indicator", True):
            return image
    except Exception:
        return image
    try:
        image = image.copy()
        draw = ImageDraw.Draw(image)
        w, h = image.size
        size = max(10, min(w, h) // 9)
        margin = max(2, size // 4)
        where = config.get("social", {}).get("indicator_corner", "top-right")
        x = w - int(size * 1.15) - int(size * 0.45) - margin if "right" in where else margin
        y = margin if "top" in where else h - int(size * 0.72) - int(size * 0.35) - int(size * 0.22) - margin
        dark = config.get("display", {}).get("dark_mode", False)
        fg, bg = (255, 0) if dark else (0, 255)
        if image.mode == "RGB":
            fg, bg = (fg,) * 3, (bg,) * 3
        # a little clear patch so it reads on top of anything
        pad = max(1, size // 6)
        draw.rectangle([x - pad, y - pad, x + int(size * 1.6) + pad, y + int(size * 1.3) + pad], fill=bg)
        _bubbles(draw, x, y, size, fg, bg)
    except Exception:
        log.debug("couldn't draw the badge", exc_info=True)
    return image
