"""Group chat: the conversation between PiE-inks, on the panel.

The last few lines, newest at the bottom, each with who said it — a person's
name, or "name's bot" when it was their AI. Lines from this Pi sit on the
right, lines from others on the left, like the page. While the bots are
talking a "…" line at the bottom says one of them is thinking.
"""
import logging

from .. import social
from ..text import font, line_height, text_width, wrap
from .base import Mode

log = logging.getLogger(__name__)


class GroupChatMode(Mode):
    name = "groupchat"
    label = "Group chat"

    @property
    def interval(self):
        return 2.0 if social.talking() else 10.0

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        T = max(1.0, min(1.6, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f11 = font(1, round(11 * T))
        f9 = font(1, round(9 * T))
        hdr = round(14 * T)
        d.text((3, 0), "GROUP CHAT", font=f11, fill=fg)
        st = social.status()
        right = "bots talking" if st["talking"] else ("bots stopped" if not st["ai_on"] else "")
        if right:
            d.text((f.width - 3 - text_width(d, right, f9), 2), right, font=f9, fill=fg)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        items = [m for m in social.history(40) if m.get("kind") in ("text", "ai", "postcard")]
        if not items:
            d.text((5, hdr + 16 * T), "Nothing yet.", font=font(1, round(14 * T)), fill=fg)
            d.text((5, hdr + 36 * T), "Say hello in Group chat on the page.", font=f9, fill=fg)
            return f.image

        # lay lines out from the bottom up, so the newest is always on screen
        lh = line_height(f11)
        gap = round(3 * T)
        max_w = int(f.width * 0.82)
        y = f.height - gap
        if st["talking"]:
            dots = "…"
            d.text((f.width - 6 - text_width(d, dots, f11), y - lh), dots, font=f11, fill=fg)
            y -= lh + gap
        for m in reversed(items):
            mine = bool(m.get("mine"))            # sent from this Pi → right, arrived → left
            who = (f"{m.get('from')}'s bot" if m.get("from_ai") else m.get("from")) or "?"
            body = "a postcard" if m.get("kind") == "postcard" else (m.get("text") or "")
            lines = wrap(d, body, f11, max_w - 8)
            block_h = len(lines) * lh + line_height(f9)
            if y - block_h < hdr + gap:
                break
            top = y - block_h
            width = max(text_width(d, ln, f11) for ln in lines) + 8 if lines else 20
            x0 = f.width - 3 - width if mine else 3
            # the bubble, dashed for a bot
            box = [x0, top + line_height(f9) - 1, x0 + width, y]
            if m.get("from_ai"):
                self._dashed(d, box, fg)
            else:
                d.rounded_rectangle(box, radius=round(4 * T), outline=fg, width=1)
            label_x = x0 + width - text_width(d, who, f9) if mine else x0
            d.text((label_x, top - 1), who, font=f9, fill=fg)
            ty = top + line_height(f9)
            for ln in lines:
                d.text((x0 + 4, ty), ln, font=f11, fill=fg)
                ty += lh
            y = top - gap
        return f.image

    @staticmethod
    def _dashed(d, box, fg, dash=3):
        x0, y0, x1, y1 = box
        for x in range(int(x0), int(x1), dash * 2):
            d.line([(x, y0), (min(x + dash, x1), y0)], fill=fg)
            d.line([(x, y1), (min(x + dash, x1), y1)], fill=fg)
        for y in range(int(y0), int(y1), dash * 2):
            d.line([(x0, y), (x0, min(y + dash, y1))], fill=fg)
            d.line([(x1, y), (x1, min(y + dash, y1))], fill=fg)
