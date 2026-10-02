"""Small marks laid over whatever screen is showing.

The one that exists so far: two little speech bubbles in a corner while the
bots on two PiE-inks are talking to each other, so you can tell at a glance
without opening the page. It is drawn onto the finished frame, so no screen
has to know about it, and it stays off the group-chat screen itself, where
the conversation is the whole picture.
"""
import logging

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


def apply(image, config, mode_name=""):
    """Draw whatever marks apply onto this frame and hand it back."""
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
