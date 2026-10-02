"""Map: where the Pi is, drawn from OpenStreetMap tiles.

Centred on the GPS fix (or its last one, or the weather location), traced
as a line map or dithered as a shaded one, with a pin in the middle, the
other PiE-inks that said where they are, and a scale bar. The tiles come
from the cache; the first look at a new place shows "fetching" for a moment
while a background thread gets them.
"""
import time

from PIL import Image, ImageOps

from .. import friends, journey, maps
from ..text import font, text_width, draw_block
from .base import Mode, SCALE


class MapMode(Mode):
    name = "map"
    label = "Map"
    interval = 2.0

    def render(self):
        f = self.frame()
        d, fg, bg = f.draw, f.fg, f.bg
        T = max(1.0, min(1.9, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.6))
        f12 = font(1, round(12 * T))
        f9 = font(1, round(9 * T))
        hdr = round(14 * T)
        pos = maps.where(self.config)
        zoom = max(8, min(18, int(self.settings.get("zoom", 15) or 15)))
        style = self.settings.get("style", "lines")
        show_friends = self.settings.get("friends", True)

        # header: where, and how we know
        title = (pos.get("place") if pos else "") or "MAP"
        right = maps.source_words(pos).upper()
        rw = text_width(d, right, f12)
        d.text((3, 0), _fit(d, title, f12, f.width - rw - 12), font=f12, fill=fg)
        d.text((f.width - 3 - rw, 0), right, font=f12, fill=fg)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)
        bottom = f.height - hdr
        view = (f.width, bottom - hdr - 1)

        if not pos:
            draw_block(d, "No position yet.\nPlug the GPS in, or press Find me on the Weather tab.",
                       f12, (0, hdr, f.width, bottom), fg, align="center", valign="center")
            return self._footer(f, pos, f12, f9)

        img, missing = maps.compose(pos["lat"], pos["lon"], zoom, view, cached_only=True)
        wanted = len(maps.tiles_for(pos["lat"], pos["lon"], zoom, view))
        if missing:
            maps.fetch_async(pos["lat"], pos["lon"], zoom, view)
        if missing == wanted:
            draw_block(d, "Fetching the map…" if not _recently_failed(pos, zoom, view) else
                       "No map: can't reach OpenStreetMap.\nThe tiles it has seen are kept, so try again online.",
                       f12, (0, hdr, f.width, bottom), fg, align="center", valign="center")
            return self._footer(f, pos, f12, f9)

        # the map, at panel resolution, pasted crisp
        mono = maps.mono(img, style)
        if f.dark:
            mono = ImageOps.invert(mono)
        big = mono.resize((view[0] * SCALE, view[1] * SCALE), Image.NEAREST)
        f.image.paste(big, (0, (hdr + 1) * SCALE))

        top = hdr + 1
        # where it has been today, as a trail
        if self.settings.get("journey", True):
            pts = journey.JOURNEY.points()
            if len(pts) >= 2:
                maps.draw_trail(d, pts, (pos["lat"], pos["lon"]), zoom, view, top, fg, bg, width=2)
        # the other PiE-inks, if they are in the picture
        if show_friends:
            for host, lat, lon in maps.friends_positions(friends.peers()):
                x, y = maps.pixel_of(lat, lon, (pos["lat"], pos["lon"]), zoom, view)
                if 0 <= x < view[0] and 0 <= y < view[1]:
                    maps.draw_friend(d, x, top + y, host[:1].upper(), f9, fg, bg, r=round(6 * T))
        # us, in the middle
        maps.draw_pin(d, view[0] // 2, top + view[1] // 2, fg, bg, r=round(5 * T))
        # a scale bar, bottom left of the map
        px, label = maps.scale_bar(pos["lat"], zoom, max_px=round(70 * T))
        sy = top + view[1] - round(6 * T)
        lw = text_width(d, label, f9)
        d.rectangle((2, sy - round(11 * T), 6 + max(px, lw) + 2, top + view[1] - 1), fill=bg)
        d.line([(4, sy), (4 + px, sy)], fill=fg, width=1)
        d.line([(4, sy - 3), (4, sy)], fill=fg, width=1)
        d.line([(4 + px, sy - 3), (4 + px, sy)], fill=fg, width=1)
        d.text((5, sy - round(11 * T)), label, font=f9, fill=fg)
        return self._footer(f, pos, f12, f9)

    def _footer(self, f, pos, f12, f9):
        d, fg = f.draw, f.fg
        T = max(1.0, min(1.9, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.6))
        bottom = f.height - round(14 * T)
        d.line([(0, bottom), (f.width, bottom)], fill=fg, width=1)
        if pos:
            left = f"{abs(pos['lat']):.4f}° {'N' if pos['lat'] >= 0 else 'S'}  {abs(pos['lon']):.4f}° {'E' if pos['lon'] >= 0 else 'W'}"
        else:
            left = time.strftime("%-I:%M %p")
        d.text((5, bottom + 1), left, font=f12, fill=fg)
        credit = "(c) OpenStreetMap"
        d.text((f.width - 4 - text_width(d, credit, f9), bottom + round(3 * T)), credit, font=f9, fill=fg)
        return f.image


def _fit(d, text, fnt, max_w):
    if text_width(d, text, fnt) <= max_w:
        return text
    while len(text) > 1 and text_width(d, text + "…", fnt) > max_w:
        text = text[:-1]
    return text + "…"


def _recently_failed(pos, zoom, view):
    now = time.time()
    return any(now - maps._failed_at.get((z, x, y), 0) < 120 for z, x, y, _, _ in maps.tiles_for(pos["lat"], pos["lon"], zoom, view))
