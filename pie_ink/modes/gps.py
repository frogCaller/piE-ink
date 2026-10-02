"""GPS: where the Pi is, from the USB receiver."""
import time

from ..gps import GPS, compass
from ..text import font, text_width, draw_block
from .base import Mode


def fmt_coord(value, lat=True):
    if value is None:
        return "—"
    hemi = ("N" if value >= 0 else "S") if lat else ("E" if value >= 0 else "W")
    return f"{abs(value):.5f}° {hemi}"


def fmt_speed(kmh, mph):
    if kmh is None:
        return "—"
    return f"{kmh * 0.621371:.0f} mph" if mph else f"{kmh:.0f} km/h"


def fmt_alt(metres, feet):
    if metres is None:
        return "—"
    return f"{metres * 3.28084:.0f} ft" if feet else f"{metres:.0f} m"


def draw_sats(d, snr, box, fg, bg, max_bars=12):
    """The satellites in view as a row of bars, strongest first."""
    x0, y0, x1, y1 = box
    bars = sorted(snr.values(), reverse=True)[:max_bars]
    if not bars:
        return
    w = (x1 - x0) / max_bars
    h = y1 - y0
    for i, s in enumerate(bars):
        bh = max(1, round(h * min(s, 50) / 50))
        bx = x0 + i * w
        d.rectangle([bx, y1 - bh, bx + w - 1, y1], fill=fg)


class GpsMode(Mode):
    name = "gps"
    label = "GPS"
    interval = 2.0

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        T = max(1.0, min(1.9, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.6))
        f12 = font(1, round(12 * T))
        label_f = font(8, round(8 * T))
        big = font(2, round(17 * T))
        small = font(1, round(11 * T))
        hdr = round(14 * T)
        g = GPS.status()
        units_f = self.config.get("weather", {}).get("units", "F") == "F"

        # header: what the receiver is doing
        d.text((3, 0), "GPS", font=f12, fill=fg)
        if not g["enabled"]:
            right = "off"
        elif g["fix"] != "none":
            right = f"{g['fix']} FIX · {g['sats_used']} SATS"
        elif g["connected"]:
            right = f"SEARCHING · {g['sats_view']} IN VIEW"
        else:
            right = "NO RECEIVER"
        d.text((f.width - 3 - text_width(d, right, f12), 0), right, font=f12, fill=fg)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)
        bottom = f.height - hdr

        if not g["enabled"]:
            draw_block(d, "The GPS is off.\nSet it to Auto on the Weather tab.", f12, (0, hdr, f.width, bottom), fg,
                       align="center", valign="center")
            return self._footer(f, g, f12)
        if not g["connected"] and g["fix"] == "none":
            msg = "No receiver found.\nPlug the USB GPS in." if not g.get("error") or "no receiver" in (g.get("error") or "") \
                else f"Receiver trouble:\n{g['error'][:60]}"
            draw_block(d, msg, f12, (0, hdr, f.width, bottom), fg, align="center", valign="center")
            return self._footer(f, g, f12)

        # the position, big, with the satellites beside it
        sat_w = round(64 * T)
        left = round(4 * T)
        y = hdr + round(4 * T)
        if g["fix"] != "none":
            d.text((left, y), fmt_coord(g["lat"], True), font=big, fill=fg)
            d.text((left, y + round(20 * T)), fmt_coord(g["lon"], False), font=big, fill=fg)
        else:
            d.text((left, y), "Searching for", font=big, fill=fg)
            d.text((left, y + round(20 * T)), "satellites…", font=big, fill=fg)
        sx0 = f.width - sat_w - round(4 * T)
        d.text((sx0, y), "SATS", font=label_f, fill=fg)
        draw_sats(d, g.get("snr") or {}, (sx0, y + round(9 * T), f.width - round(4 * T), y + round(38 * T)), fg, f.bg)

        # a row of the numbers
        y2 = y + round(44 * T)
        row = []
        if g["fix"] != "none":
            row.append(("ALT", fmt_alt(g.get("alt_m"), units_f)))
            row.append(("SPD", fmt_speed(g.get("speed_kmh"), units_f)))
            if g.get("course") is not None and (g.get("speed_kmh") or 0) >= 2:
                row.append(("HDG", f"{g['course']:.0f}° {compass(g['course'])}"))
            if g.get("hdop") is not None:
                row.append(("HDOP", f"{g['hdop']:.1f}"))
        else:
            row.append(("IN VIEW", str(g["sats_view"])))
            row.append(("USED", str(g["sats_used"])))
        x = left
        for label, value in row:
            d.text((x, y2 + round(3 * T)), label, font=label_f, fill=fg)
            lx = x + text_width(d, label, label_f) + round(4 * T)
            d.text((lx, y2), value, font=small, fill=fg)
            x = lx + text_width(d, value, small) + round(12 * T)

        # where that is, if the receiver has been told
        place = g.get("place")
        if place and g["fix"] != "none":
            d.text((left, y2 + round(16 * T)), place, font=f12, fill=fg)
        return self._footer(f, g, f12)

    def _footer(self, f, g, f12):
        d, fg = f.draw, f.fg
        bottom = f.height - round(14 * max(1.0, min(1.9, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.6)))
        d.line([(0, bottom), (f.width, bottom)], fill=fg, width=1)
        if g.get("utc") and g["connected"]:
            left = f"UTC {g['utc']}" + (f"  {g['date']}" if g.get("date") else "")
        else:
            left = time.strftime("%-I:%M %p")
        d.text((5, bottom + 1), left, font=f12, fill=fg)
        if g.get("fix_age") is not None and g["fix"] == "none" and g.get("lat") is not None:
            right = f"last fix {g['fix_age'] // 60} min ago"
        elif g.get("device") and g["source"] != "mock":
            right = str(g["device"]).replace("/dev/", "")
        else:
            right = ""
        if right:
            d.text((f.width - 5 - text_width(d, right, f12), bottom + 1), right, font=f12, fill=fg)
        return f.image
