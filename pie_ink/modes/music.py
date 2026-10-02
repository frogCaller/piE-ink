"""Music: what's playing, how far through it is, and what follows."""
import logging

from .. import music
from ..text import font, text_width, wrap
from .base import Mode

log = logging.getLogger(__name__)


def _clock(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicMode(Mode):
    name = "music"
    label = "Music"

    @property
    def interval(self):
        return 1.0 if music.PLAYER.state == "playing" else 5.0

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        T = max(1.0, min(1.6, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f12 = font(1, round(12 * T))
        f11 = font(1, round(11 * T))
        hdr = round(14 * T)
        footer = f.height - round(14 * T)

        st = music.PLAYER.tick()
        tracks = music.PLAYER.tracks

        # header: where we are in the playlist, and the state
        left = "MUSIC" if not st["count"] else f"TRACK {st['index'] + 1} OF {st['count']}"
        right = {"playing": "PLAYING", "paused": "PAUSED", "stopped": "STOPPED"}[st["state"]]
        d.text((3, 0), left, font=f12, fill=fg)
        d.text((f.width - 3 - text_width(d, right, f12), 0), right, font=f12, fill=fg)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        if not st["count"]:
            d.text((5, hdr + 16 * T), "No music yet.", font=font(1, round(14 * T)), fill=fg)
            for i, line in enumerate(wrap(d, "Copy files into data/music on the Pi, or add them in the Music tab.",
                                          f11, f.width - 10, max_lines=2)):
                d.text((5, hdr + 36 * T + i * 13 * T), line, font=f11, fill=fg)
            d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
            return f.image

        # the title, as big as it can be while still fitting
        title = st["title"] or "—"
        size = round(22 * T)
        while size > round(12 * T) and text_width(d, title, font(1, size)) > f.width - 12:
            size -= 1
        title_font = font(1, size)
        lines = wrap(d, title, title_font, f.width - 12, max_lines=2)
        y = hdr + 6 * T
        for line in lines:
            d.text(((f.width - text_width(d, line, title_font)) // 2, y), line, font=title_font, fill=fg)
            y += size + 2

        if st["folder"]:
            folder = st["folder"]
            while text_width(d, folder, f11) > f.width - 12 and len(folder) > 4:
                folder = folder[:-2] + "…"
            d.text(((f.width - text_width(d, folder, f11)) // 2, y + 1), folder, font=f11, fill=fg)

        # progress
        bar_h = round(9 * T)
        bar_y = footer - round(20 * T)
        bar_w = int(f.width * 0.66)
        bar_x = (f.width - bar_w) // 2
        done = (st["position"] / st["duration"]) if st["duration"] else 0.0
        done = max(0.0, min(1.0, done))
        radius = bar_h / 2
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius, outline=fg, width=1)
        fill = (bar_w - 2) * done
        if fill >= 1:
            d.rounded_rectangle([bar_x + 1, bar_y + 1, bar_x + 1 + fill, bar_y + bar_h - 1],
                                min(radius - 1, fill / 2), fill=fg)
        d.text((bar_x - text_width(d, _clock(st["position"]), f11) - 5, bar_y - 1),
               _clock(st["position"]), font=f11, fill=fg)
        d.text((bar_x + bar_w + 5, bar_y - 1), _clock(st["duration"]), font=f11, fill=fg)

        # footer: what's next, and the mode flags
        d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
        nxt = ""
        if tracks and st["index"] >= 0:
            order = music.PLAYER.order or list(range(len(tracks)))
            at = order.index(st["index"]) if st["index"] in order else 0
            if at + 1 < len(order) or st["repeat"]:
                nxt = "Next: " + tracks[order[(at + 1) % len(order)]]["title"]
        flags = " ".join(x for x in ("shuffle" if st["shuffle"] else "", "repeat" if st["repeat"] else "") if x)
        room = f.width - text_width(d, flags, f11) - 12
        while nxt and text_width(d, nxt, f11) > room and len(nxt) > 8:
            nxt = nxt[:-2].rstrip() + "…"
        d.text((3, footer + 1), nxt or (st["error"] or ""), font=f11, fill=fg)
        if flags:
            d.text((f.width - 3 - text_width(d, flags, f11), footer + 1), flags, font=f11, fill=fg)
        return f.image

    def on_button(self, action):
        """The HAT keys work as transport controls on this screen."""
        if action in ("next", "next_screen"):
            music.PLAYER.step(1)
            return True
        if action in ("prev", "prev_screen"):
            music.PLAYER.step(-1)
            return True
        if action == "refresh":
            music.PLAYER.toggle()
            return True
        return False
