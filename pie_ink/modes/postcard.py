"""Postcard: a drawing another PiE-ink sent, with who it came from."""
import logging
import os
import time

from PIL import Image

from ..settings import DATA_DIR
from ..text import font, text_width
from .base import Mode

log = logging.getLogger(__name__)
PATH = os.path.join(DATA_DIR, "postcard.png")
META = {"from": "", "text": "", "ts": 0.0, "label": ""}


def keep(image, sender, text="", label="", ts=None):
    """Save the latest postcard so it survives a restart. label: the footer's
    words instead of "From <sender>" (a photo says what it is); ts: when it
    was made, if not now."""
    os.makedirs(DATA_DIR, exist_ok=True)
    image.convert("L").save(PATH)
    META.update({"from": sender, "text": text or "", "ts": ts or time.time(), "label": label or ""})


class PostcardMode(Mode):
    name = "postcard"
    label = "Postcard"

    @property
    def interval(self):
        return 30.0

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        T = max(1.0, min(1.6, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f12 = font(1, round(12 * T))
        footer = f.height - round(14 * T)

        try:
            card = Image.open(PATH).convert("L")
        except OSError:
            d.text((5, 14 * T), "No postcards yet.", font=font(1, round(14 * T)), fill=fg)
            d.text((5, 34 * T), "Draw something and send it to a friend.", font=font(1, round(11 * T)), fill=fg)
            return f.image

        # the drawing, as large as it goes above the footer
        scale = f.scale
        room_w, room_h = int(f.width * scale), int((footer - 2) * scale)
        card.thumbnail((room_w, room_h), Image.LANCZOS)
        card = card.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
        x = (room_w - card.width) // 2
        f.image.paste(card, (x, 0))

        d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
        sender = META.get("from") or "a friend"
        left = META.get("label") or f"From {sender}"
        when = time.strftime("%a %-I:%M %p", time.localtime(META.get("ts") or time.time()))
        d.text((3, footer + 1), left, font=f12, fill=fg)
        d.text((f.width - 3 - text_width(d, when, f12), footer + 1), when, font=f12, fill=fg)
        return f.image
