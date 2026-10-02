"""Weather: the same shape as the crypto screen — header, big number, a face,
rotating details, a bottom band — but the face reacts to the sky."""
import functools
import logging
import os
import threading
import time
from datetime import datetime

from PIL import Image

from .. import weather
from ..settings import ASSETS_DIR
from ..text import font, text_width, wrap
from .base import Mode
from .crypto import FACES, _cpu_temp


@functools.lru_cache(maxsize=32)
def icon(name):
    """Greyscale mask of assets/icons/weather/<name>.png, cropped to the drawing."""
    img = Image.open(os.path.join(ASSETS_DIR, "icons", "weather", f"{name}.png")).convert("RGBA")
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] == alpha.getextrema()[1]:          # no transparency: dark pixels are the drawing
        alpha = Image.eval(img.convert("L"), lambda v: 255 - v)
    box = alpha.getbbox() or (0, 0, alpha.width, alpha.height)
    alpha = alpha.crop(box)
    side = max(alpha.size)
    square = Image.new("L", (side, side), 0)
    square.paste(alpha, ((side - alpha.width) // 2, (side - alpha.height) // 2))
    return square.resize((96, 96), Image.LANCZOS)

log = logging.getLogger(__name__)

EXTRA = {"cold": "(>_<)", "brr": "(x_x)", "rain": "(╥_╥)", "storm": "(°□°)", "fog": "(-_-)"}
QUOTES = {
    "hot": ["It's hot today!", "Stay in the shade", "Hydrate, hydrate", "Too hot to compute"],
    "warm": ["What a nice day", "Go outside!", "Perfect weather", "Not a cloud in sight"],
    "mild": ["Pleasant out there", "Good walking weather", "Nice and easy"],
    "cold": ["Brr, it's cold", "Grab a jacket", "Cocoa weather"],
    "freezing": ["Freezing!", "Stay warm", "Ice on the road"],
    "rain": ["Take an umbrella", "Rain today", "Cosy inside"],
    "storm": ["Thunder!", "Stay indoors", "Unplug the good stuff"],
    "snow": ["Snow!", "Bundle up", "Watch your step"],
    "fog": ["Can't see a thing", "Foggy out", "Drive slowly"],
    "cloud": ["Bit grey today", "Clouds about", "Might clear up"],
    "night": ["Good night", "Sleep well", "Stars out?"],
}
QUOTE_SECONDS = 10
BOTTOM_SECONDS = 20


def _t(v, units):
    return "--" if v is None else f"{round(v)}°"


class WeatherMode(Mode):
    name = "weather"
    label = "Weather"

    def __init__(self, config, panel=None):
        super().__init__(config, panel)
        self._data = None
        self._nws = []
        self._stop = threading.Event()
        self._look = 0

    @property
    def interval(self):
        return 5.0

    # -- background data ------------------------------------------------------------

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._loop, daemon=True, name="weather").start()

    def stop(self):
        self._stop.set()

    def _place(self):
        s = self.settings
        if s.get("lat") is None or s.get("lon") is None:
            return None
        return float(s["lat"]), float(s["lon"])

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._fetch()
            except Exception:
                log.exception("weather fetch failed")
            self._stop.wait(max(60, int(self.settings.get("refresh_minutes", 10)) * 60))

    def _fetch(self, cached_only=False):
        place = self._place()
        if not place:
            return
        d = weather.fetch(*place, self.settings.get("units", "F"), cached_only)
        if d:
            self._data = d
            self._data["age"] = weather.age(*place, self.settings.get("units", "F"))
        if str(self.settings.get("country", "")).upper() == "US":
            self._nws = weather.nws_forecast(*place, cached_only)

    def update(self, config):
        super().update(config)
        self._data = None
        self._nws = []
        self._fetch(cached_only=True)

    # -- mood -----------------------------------------------------------------------------

    def _mood(self, d):
        cat = weather.describe(d["code"])[1]
        t = d["temp"] or 0
        f = t if d["units"] == "F" else t * 9 / 5 + 32
        if cat == "storm":
            return "storm"
        if cat == "snow":
            return "snow"
        if cat == "rain":
            return "rain"
        if cat == "fog":
            return "fog"
        if not d["is_day"]:
            return "night"
        if f >= 88:
            return "hot"
        if f <= 32:
            return "freezing"
        if f <= 50:
            return "cold"
        if cat == "cloud":
            return "cloud"
        return "warm" if f >= 68 else "mild"

    def _face(self, mood):
        t = int(time.time())
        flip = (t // 3) % 2 == 0
        return {
            "hot": FACES["hot"] if flip else FACES["hot2"],
            "warm": FACES["cool"] if (t // 3) % 5 == 4 else (FACES["look_r_happy"] if flip else FACES["look_l_happy"]),
            "mild": FACES["happy"] if flip else FACES["awake"],
            "cold": EXTRA["cold"], "freezing": EXTRA["brr"] if flip else EXTRA["cold"],
            "rain": EXTRA["rain"], "storm": EXTRA["storm"], "snow": FACES["look_r"] if flip else FACES["look_l"],
            "fog": EXTRA["fog"], "cloud": FACES["look_r"] if flip else FACES["look_l"],
            "night": FACES["sleep"] if flip else FACES["sleep2"],
        }[mood]

    # -- render -----------------------------------------------------------------------------

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        now = time.time()
        T = max(1.0, min(1.6, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f12 = font(1, round(12 * T))
        hdr = round(14 * T)
        footer = f.height - round(14 * T)
        band_h = round(33 * T)
        band = footer - 2 - band_h
        s = self.settings
        data = self._data
        if data is None and self._place():
            self._fetch(cached_only=True)
            data = self._data

        def spread(items, y, icon_px):
            """Draw [(icon or None, text), ...] across the width with equal gaps."""
            widths = [text_width(d, t, f12) + (icon_px + 3 if ic else 0) for ic, t in items]
            gap = (f.width - 6 - sum(widths)) / max(1, len(items) - 1)
            x = 3
            for (ic, t), w in zip(items, widths):
                if ic:
                    f.stamp(icon(ic), (x, y + 1), icon_px)
                    x += icon_px + 3
                d.text((x, y), t, font=f12, fill=fg)
                x += w - (icon_px + 3 if ic else 0) + gap

        # header: place · sunrise · sunset · time
        place = (s.get("location") or "Set a location").split(",")[0].upper()
        stamp = datetime.now().strftime("%a %-I:%M %p").upper()
        head = [(None, place)]
        if data and data.get("days"):
            head += [("sunrise", data["days"][0]["sunrise"]), ("sunset", data["days"][0]["sunset"])]
        head.append((None, stamp))
        spread(head, 0, round(11 * T))
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        if not self._place():
            d.text((5, hdr + 20 * T), "No location set.", font=font(1, round(14 * T)), fill=fg)
            d.text((5, hdr + 40 * T), "Press Find me on the Weather tab, or type a town.", font=f12, fill=fg)
            return f.image
        if not data:
            d.text((5, hdr + 20 * T), "Fetching the weather…", font=font(1, round(14 * T)), fill=fg)
            return f.image

        mood = self._mood(data)
        cond, _ = weather.describe(data["code"])
        col = max(f.width // 2, int(text_width(d, "(◕‿‿◕)", font(2, round(32 * T))) + 12 * T))

        # prompt + big temperature
        prompt = f"{s.get('username') or self.config.get('crypto', {}).get('username', 'pi')}>"
        age = data.get("age")
        if age and age > 1800:
            prompt += f" {int(age // 3600) or 1}h old" if age < 86400 else f" {int(age // 86400)}d old"
        d.text((5, hdr + 1), prompt, font=f12, fill=fg)
        big = font(1, round(26 * T))
        temp = _t(data["temp"], data["units"]) + data["units"]
        d.text((f.width - 10 - text_width(d, temp, big), hdr + 2), temp, font=big, fill=fg)

        # face
        hot_pi = (_cpu_temp() or 0) >= 72
        face_y = (hdr + band) / 2 - 21 * T if (band - hdr) > 120 else max(hdr + 14 * T, (hdr + band) / 2 - 21 * T)
        d.text((3, face_y), FACES["hot"] if hot_pi else self._face(mood), font=font(2, round(32 * T)), fill=fg)

        # right column: condition (with its icon), then details and the quote
        ic = weather.icon_name(data["code"], data["is_day"])
        ipx = round(13 * T)
        feels = _t(data['feels'], data['units'])
        lines = [("humidity", f"Feels {feels}  ", f"{data['humidity']}%")]     # (icon, before, after)
        if data["days"] and data["days"][0].get("rain") is not None:
            lines.append(f"Rain chance {data['days'][0]['rain']}%")
        quotes = QUOTES["hot"] if hot_pi else QUOTES[mood]
        quote = quotes[int(now // QUOTE_SECONDS) % len(quotes)]
        top = hdr + 30 * T
        avail = band - 4 - top
        lh = 14 * T
        rows = int(avail // lh)

        def cond_line(y):
            f.stamp(icon(ic), (col, y + 1), ipx)
            d.text((col + ipx + 4, y), cond, font=f12, fill=fg)

        def draw_line(line, y):
            if isinstance(line, tuple):                    # text, then an icon, then more text
                ic2, before, after = line
                d.text((col, y), before, font=f12, fill=fg)
                x = col + text_width(d, before, f12)
                f.stamp(icon(ic2), (x, y + 1), ipx)
                d.text((x + ipx + 3, y), after, font=f12, fill=fg)
            else:
                d.text((col, y), line, font=f12, fill=fg)

        if rows >= 3:
            block = ["cond"] + lines[:rows - 2] + [quote]
            step = min(avail / len(block), lh * 1.5)
            y = top + 2 * T
            for line in block:
                if line == "cond":
                    cond_line(y)
                else:
                    draw_line(line, y)
                y += step
        else:
            step_i = int(now // QUOTE_SECONDS) % 2
            cond_line(hdr + 31 * T)
            draw_line(quote if step_i else lines[0], hdr + 45 * T)

        # bottom band: today's range with the current temperature, or the next days, or the NWS text
        panels = 2 + (1 if self._nws else 0)
        step_b = int(now % (BOTTOM_SECONDS * panels) // BOTTOM_SECONDS)
        if step_b == 0 and data["days"]:
            t0 = data["days"][0]
            lo, hi, cur = t0["lo"], t0["hi"], data["temp"]
            line_y = band + round(19 * T)
            line_w = int(f.width * 0.7)
            line_x = (f.width - line_w) // 2
            d.line([(line_x, line_y), (line_x + line_w, line_y)], fill=fg, width=2)
            for i in range(1, 4):
                tx = line_x + i * (line_w // 4)
                d.line([(tx, line_y - 5 * T), (tx, line_y + 5 * T)], fill=fg, width=1)
            if None not in (lo, hi, cur) and hi > lo:
                r = round(5 * T)
                cx = line_x + int(line_w * max(0.0, min(1.0, (cur - lo) / (hi - lo))))
                d.ellipse([(cx - r, line_y - r), (cx + r, line_y + r)], fill=fg)
                lab = _t(cur, data["units"])
                d.text((cx - text_width(d, lab, f12) // 2, line_y - 21 * T), lab, font=f12, fill=fg)
            d.text((line_x - text_width(d, _t(lo, data["units"]), f12) - 5, line_y - 9 * T), _t(lo, data["units"]), font=f12, fill=fg)
            d.text((line_x + line_w + 5, line_y - 9 * T), _t(hi, data["units"]), font=f12, fill=fg)
        elif step_b == 1 and data["days"]:
            days = data["days"][1:5]
            bold = font(2, round(10 * T))
            f11 = font(1, round(11 * T))
            colw = f.width / max(1, len(days))
            ipx2 = round(16 * T)
            for i, day in enumerate(days):
                cx = int(colw * i + colw / 2)
                name = datetime.strptime(day["date"], "%Y-%m-%d").strftime("%a").upper()
                val = f"{_t(day['hi'], data['units'])}/{_t(day['lo'], data['units'])}"
                w1, w2 = text_width(d, name, bold), text_width(d, val, f11)
                x = cx - (w1 + 4 + w2) // 2
                d.text((x, band + 1), name, font=bold, fill=fg)
                d.text((x + w1 + 4, band), val, font=f11, fill=fg)
                f.stamp(icon(weather.icon_name(day["code"], True)), (cx - ipx2 // 2, band + 13 * T), ipx2)
                if i:
                    d.line([(int(colw * i), band + 1), (int(colw * i), band + band_h - 2)], fill=fg, width=1)
        elif self._nws:
            period, text = self._nws[0]
            small = font(1, round(11 * T))
            for i, line in enumerate(wrap(d, f"{period}: {text}", small, f.width - 8, max_lines=2)):
                d.text((4, band + 4 * T + i * 13 * T), line, font=small, fill=fg)

        # footer: high/low · wind · rain chance, evenly spaced
        d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
        foot = []
        if data["days"]:
            t0 = data["days"][0]
            foot.append((None, f"H {_t(t0['hi'], data['units'])} L {_t(t0['lo'], data['units'])}"))
        foot.append(("wind", f"{weather.compass(data.get('wind_dir'))} {round(data['wind'] or 0)} {'mph' if data['units'] == 'F' else 'km/h'}".strip()))
        if data["days"] and data["days"][0].get("rain") is not None:
            foot.append((None, f"Rain {data['days'][0]['rain']}%"))
        spread(foot, footer + 1, round(11 * T))
        return f.image
