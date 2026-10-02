"""What the second screen can show. Each renders a 240x280 RGB image.

  system   the Pi's vitals: IP across the top, then CPU, temperature, RAM
           and disk in a 2x2 grid with icons — the layout from the original
           LCD script, with a little colour now that there is some
  clock    the time, big, with the date
  weather  now and the next days, with the icons in colour
  crypto   a coin from the watchlist: price, change, a chart, the figures —
           the layout from the original LCD crypto script
  camera   the camera, live (about ten frames a second)
  drawing  what the Draw tab last sent to the e-ink, and what the bot said about it
  paint    the colour canvas the Draw tab paints on when its target is the LCD —
           live, as the strokes are made
  bot      the bot's face and its latest line
  chat     the conversation between PiE-inks
  mirror   whatever the e-ink is showing, scaled to fit

The LCD is colour, so a single accent colour picks out the numbers; set it
to "" for the all-white look.

Every screen draws on a canvas the size it is handed: portrait (240x280) as
the panel is, or landscape (280x240) when the rotation is 90 or 270 — the
driver turns the canvas to fit the glass afterwards.
"""
import logging
import os
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .settings import ROOT
from .text import font, wrap

log = logging.getLogger(__name__)
W, H = 240, 280
ICON_DIR = os.path.join(ROOT, "assets", "icons")
_icons = {}
CAMERA_INTERVAL = 0.1            # the camera screen redraws this often, whatever the fps setting


def interval(conf):
    """Seconds between redraws of the chosen screen."""
    screen = conf.get("screen")
    if screen == "camera":
        return CAMERA_INTERVAL
    if screen == "paint":
        from .paint import PAINT
        if PAINT.busy():                              # strokes are arriving: keep up with them
            return CAMERA_INTERVAL
    return 1.0 / max(0.2, float(conf.get("fps", 2) or 2))


def _colours(conf):
    light = bool(conf.get("light_mode"))
    bg = (255, 255, 255) if light else (0, 0, 0)
    fg = (0, 0, 0) if light else (255, 255, 255)
    accent = _hex(conf.get("accent", "#62d2ff")) or fg
    dim = (110, 110, 110) if light else (150, 150, 150)
    return bg, fg, accent, dim


def _hex(value):
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _icon(name, size, colour):
    """A project icon (black on transparent) recoloured, as an RGBA sprite."""
    key = (name, size, colour)
    if key not in _icons:
        try:
            src = Image.open(os.path.join(ICON_DIR, f"{name}.png")).convert("RGBA")
        except OSError:
            _icons[key] = None
            return None
        src = src.resize((size, size), Image.LANCZOS)
        tint = Image.new("RGBA", src.size, colour + (255,))
        _icons[key] = Image.merge("RGBA", (*tint.split()[:3], src.getchannel("A")))
    return _icons[key]


def _wx_icon(name, size, colour):
    """A weather icon (assets/icons/weather) cropped to its drawing, squared
    and tinted, as an RGBA sprite."""
    key = ("wx", name, size, colour)
    if key not in _icons:
        try:
            img = Image.open(os.path.join(ICON_DIR, "weather", f"{name}.png")).convert("RGBA")
        except OSError:
            _icons[key] = None
            return None
        alpha = img.getchannel("A")
        if alpha.getextrema()[0] == alpha.getextrema()[1]:      # no transparency: dark pixels are the drawing
            alpha = Image.eval(img.convert("L"), lambda v: 255 - v)
        box = alpha.getbbox() or (0, 0, alpha.width, alpha.height)
        alpha = alpha.crop(box)
        side = max(alpha.size)
        square = Image.new("L", (side, side), 0)
        square.paste(alpha, ((side - alpha.width) // 2, (side - alpha.height) // 2))
        mask = square.resize((size, size), Image.LANCZOS)
        tint = Image.new("RGBA", (size, size), colour + (255,))
        _icons[key] = Image.merge("RGBA", (*tint.split()[:3], mask))
    return _icons[key]


# what colour each kind of weather icon gets, on dark and on light backgrounds
_WX_TINT = {
    "sunny": ((255, 200, 60), (230, 150, 0)), "moon": ((205, 215, 240), (80, 90, 140)),
    "partly_day": ((255, 205, 90), (220, 150, 20)), "partly_night": ((190, 200, 230), (90, 100, 150)),
    "cloud": ((195, 200, 210), (110, 115, 125)), "cloud_night": ((175, 185, 210), (100, 110, 140)),
    "overcast": ((170, 175, 185), (100, 105, 115)), "fog_day": ((175, 180, 190), (120, 125, 135)),
    "fog_night": ((160, 170, 190), (100, 110, 130)), "rain_day": ((90, 160, 255), (30, 100, 220)),
    "rain_night": ((80, 140, 230), (30, 90, 200)), "heavyrain_day": ((70, 130, 240), (20, 80, 200)),
    "heavyrain_night": ((60, 115, 220), (20, 70, 180)), "snow_day": ((215, 235, 255), (90, 160, 220)),
    "snow_night": ((190, 215, 245), (80, 140, 200)), "storm_day": ((245, 200, 80), (200, 140, 0)),
    "storm_night": ((235, 190, 80), (190, 130, 0)), "humidity": ((90, 160, 255), (30, 100, 220)),
    "wind": ((195, 200, 210), (110, 115, 125)), "sunrise": ((255, 200, 60), (230, 150, 0)),
    "sunset": ((255, 150, 80), (220, 100, 30)),
}


def _wx_tint(name, light):
    dark_c, light_c = _WX_TINT.get(name, ((195, 200, 210), (110, 115, 125)))
    return light_c if light else dark_c


def _centered(d, text, fnt, cx, y, fill):
    w = d.textlength(text, font=fnt)
    d.text((cx - w / 2, y), text, font=fnt, fill=fill)


def _right(d, text, fnt, rx, y, fill):
    d.text((rx - d.textlength(text, font=fnt), y), text, font=fnt, fill=fill)


_bold_cache = {}


def _bold(size):
    """DejaVu Sans Bold if the Pi has it (it normally does); else the Sans."""
    if size not in _bold_cache:
        try:
            _bold_cache[size] = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        except OSError:
            _bold_cache[size] = font(1, size)
    return _bold_cache[size]


def canvas_size(conf):
    """The chosen panel's size — portrait unless the screen is mounted sideways."""
    from .lcd import panel_size
    pw, ph = panel_size(conf)
    return (ph, pw) if int(conf.get("rotation", 0) or 0) % 180 == 90 else (pw, ph)


def _fit_font(d, text, max_w, size, floor=10):
    """The largest font at or under `size` that keeps `text` inside `max_w`."""
    while size > floor and d.textlength(text, font=font(1, size)) > max_w:
        size -= 1
    return font(1, size)


# -- the vitals, in the original layout ------------------------------------------------------

def system(conf, now=None, size=None):
    from .modes import system as sysmode
    sysmode._ensure_sampler()
    L = sysmode._last
    now = now or time.time()
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    narrow = w < 200
    f24 = font(1, 20 if narrow else 24)
    f13 = font(1, 12 if narrow else 13)

    # the grid, as it was: the IP across the top, four cells below
    head = 34 if narrow else 40
    cw, ch = w // 2, (h - head) // 2
    mid = head + ch
    d.line((0, head, w, head), fill=fg, width=1)
    d.line((0, mid, w, mid), fill=fg, width=1)
    d.line((cw, head, cw, h), fill=fg, width=1)

    ip = L.get("ip") or "no network"
    _centered(d, ip, _fit_font(d, ip, w - 8, 20 if narrow else 24), w / 2, 6 if narrow else 7, fg)

    # every 12 seconds the memory and disk cells swap between percent and size
    alt = int(now // 12) % 2 == 1
    mem_total = L.get("mem_total") or 0
    mem_text = f"{L.get('mem_pct', 0):.0f}%" if not alt else f"{mem_total / 1024 ** 3:.1f} GB"
    disk_total = L.get("disk_total") or 0
    disk_text = f"{L.get('disk_pct', 0):.0f}%" if not alt else f"{int(disk_total // 1024 ** 3)} GB"
    temp = L.get("temp")
    temp_text = f"{temp:.1f}°C" if temp is not None else "—"
    cpu_text = f"{L.get('cpu', 0):.0f}%"

    cells = [
        ("cpu", cpu_text, 0, head, f"{(L.get('mhz') or 0) / 1000:.1f} GHz" if L.get("mhz") else ""),
        ("temp", temp_text, cw, head, ""),
        ("ram", mem_text, 0, mid, "used" if not alt else "total"),
        ("storage", disk_text, cw, mid, "used" if not alt else "total"),
    ]
    # each cell stacks icon, value and a small label; the stack is centred in
    # the cell, so a short landscape cell just packs them closer together
    tight = ch < 90                                   # a short landscape cell
    icon_px, gap, sub_gap = (22, 4, 1) if tight else (32, 8, 4)
    value_h = 20 if narrow else 24
    sub_h = 13
    for name, value, qx, qy, sub in cells:
        block = icon_px + gap + value_h + (sub_h + sub_gap if sub else 0)
        y = qy + max(2, (ch - block) // 2)
        icon = _icon(name, icon_px, fg)
        if icon is not None:
            img.paste(icon, (qx + (cw - icon_px) // 2, int(y)), icon)
        y += icon_px + gap
        _centered(d, value, f24, qx + cw / 2, y, accent)
        if sub:
            _centered(d, sub, f13, qx + cw / 2, y + value_h + sub_gap, dim)
    return img


# -- the others -------------------------------------------------------------------------------

def clock(conf, now=None, size=None):
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    t = datetime.now()
    k = min(w, h) / 240                               # 1 on the 1.69"; smaller on narrow or short glass
    big = font(2, max(40, round(64 * k)))
    small = font(1, max(14, round(20 * k)))
    hhmm = t.strftime("%-I:%M")
    ampm = t.strftime("%p").lower()
    block = round(158 * k)
    y = (h - block) // 2 + round(6 * k)               # the block sits a touch above centre
    _centered(d, hhmm, big, w / 2, y, fg)
    _centered(d, ampm, small, w / 2, y + round(74 * k), accent)
    _centered(d, t.strftime("%A"), small, w / 2, y + round(112 * k), fg)
    _centered(d, t.strftime("%-d %B"), small, w / 2, y + round(138 * k), dim)
    return img


def bot(conf, now=None, size=None):
    from . import llm
    from .modes.simple import drawable_face
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    said = llm.latest()
    face_font = font(2, 44)
    face, _mood = drawable_face(face_font, said.get("text") or "", said.get("kind", "idle"))
    portrait = h > w
    _centered(d, face, face_font, w / 2, 40 if portrait else 22, accent)
    text = (said.get("text") or "").strip() or (said.get("error") or "")
    if text:
        f = font(1, 18)
        y = 130 if portrait else 96
        lines = wrap(d, text, f, w - 24, max_lines=(h - y) // 24)
        for ln in lines:
            _centered(d, ln, f, w / 2, y, fg)
            y += 24
    return img


def buddy(conf, now=None, size=None):
    """The little friend, big and in colour: its face, how it is, what it last said."""
    from . import buddy as friend
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    line = friend.BUDDY.recent_line(600)
    px = max(24, int(min(w * 0.24, h * (0.24 if line else 0.3))))
    face_font = font(2, px)
    face, mood, caption = friend.BUDDY.drawable_face(face_font)
    while px > 20 and d.textlength(face, font=face_font) > w * 0.9:
        px -= 2
        face_font = font(2, px)
    small = font(1, 15)
    words, f = [], font(1, 20 if len(line) < 60 else 17)
    if line:
        words = wrap(d, line, f, w - 28, max_lines=max(1, int((h - px * 1.35 - 60) // 26)))
    block = int(px * 1.35) + 20 + (10 + 26 * len(words) if words else 0)    # face, how it is, what it said
    y = max(6, (h - block) // 2)
    _centered(d, face, face_font, w / 2, y, dim if mood in ("sleepy", "off") else accent)
    y += int(px * 1.35)
    _centered(d, caption, small, w / 2, y, dim)
    y += 30
    for ln in words:
        _centered(d, ln, f, w / 2, y, fg)
        y += 26
    return img


def chat(conf, now=None, size=None):
    from . import social
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    f14 = font(1, 14)
    f11 = font(1, 11)
    st = social.status()
    d.text((8, 6), "GROUP CHAT", font=f11, fill=dim)
    if st["talking"]:
        d.text((w - 8 - d.textlength("bots talking", font=f11), 6), "bots talking", font=f11, fill=accent)
    d.line((0, 24, w, 24), fill=dim, width=1)
    items = [m for m in social.history(30) if m.get("kind") in ("text", "ai", "postcard")]
    y = h - 8
    if st["talking"]:
        d.text((w - 26, y - 18), "…", font=f14, fill=accent)
        y -= 22
    for m in reversed(items):
        mine = bool(m.get("mine"))
        who = f"{m.get('from')}'s bot" if m.get("from_ai") else (m.get("from") or "?")
        body = "a postcard" if m.get("kind") == "postcard" else (m.get("text") or "")
        lines = wrap(d, body, f14, w * 0.8 - 12)
        block = len(lines) * 18 + 20
        if y - block < 30:
            break
        top = y - block
        width = max(d.textlength(ln, font=f14) for ln in lines) + 12 if lines else 24
        x0 = w - 8 - width if mine else 8
        box = [x0, top + 13, x0 + width, y]
        colour = accent if m.get("from_ai") else fg
        d.rounded_rectangle(box, radius=8, outline=colour, width=1)
        d.text((x0 + (width - d.textlength(who, font=f11)) if mine else x0, top), who, font=f11, fill=dim)
        ty = top + 16
        for ln in lines:
            d.text((x0 + 6, ty), ln, font=f14, fill=fg)
            ty += 18
        y = top - 6
    if not items:
        _centered(d, "Nothing yet.", f14, w / 2, h / 2 - 10, dim)
    return img


# -- the weather ------------------------------------------------------------------------------

class _WeatherFeed:
    """Keeps the forecast fresh while the weather screen is up, on its own
    thread; the render only ever reads memory (or the disk cache once)."""

    def __init__(self):
        self.data = None
        self.place = None
        self.thread = None
        self.wanted = 0.0

    def want(self, place, units):
        self.wanted = time.time()
        if (place, units) != self.place:
            self.place, self.data = (place, units), None
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, daemon=True, name="lcd-weather")
            self.thread.start()

    def current(self):
        from . import weather
        if self.data is None and self.place:
            (lat, lon), units = self.place
            self.data = weather.fetch(lat, lon, units, cached_only=True)
        return self.data

    def _run(self):
        from . import weather
        while time.time() - self.wanted < 120:
            try:
                if self.place:
                    (lat, lon), units = self.place
                    d = weather.fetch(lat, lon, units)        # only hits the network when its cache is stale
                    if d:
                        self.data = d
            except Exception:
                log.exception("lcd weather fetch failed")
            for _ in range(60):
                if time.time() - self.wanted >= 120:
                    break
                time.sleep(1)


_WEATHER = _WeatherFeed()


def _deg(v):
    try:
        return f"{round(float(v))}°"
    except (TypeError, ValueError):
        return "—"


def weather(conf, now=None, size=None, config=None):
    from . import weather as wx
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    light = bool(conf.get("light_mode"))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    ws = (config or {}).get("weather") or {}
    f13, f12, f11 = font(1, 13), font(1, 12), font(1, 11)
    portrait = h > w
    place_name = (ws.get("location") or "").split(",")[0].strip()
    d.text((10, 8), place_name.upper() if place_name else "WEATHER", font=f11, fill=dim)
    if ws.get("lat") is None or ws.get("lon") is None:
        _centered(d, "No location set.", f13, w / 2, h / 2 - 18, fg)
        _centered(d, "Press Find me on the Weather tab, or type a town.", f12, w / 2, h / 2 + 4, dim)
        return img
    _WEATHER.want((float(ws["lat"]), float(ws["lon"])), ws.get("units", "F"))
    data = _WEATHER.current()
    if not data:
        _centered(d, "Waiting for the forecast…", f13, w / 2, h / 2, dim)
        return img

    # now: the big number, the icon, the condition — sized to the glass
    narrow, short = w < 200, h < 200
    name = wx.icon_name(data.get("code"), data.get("is_day", True))
    if short:
        big, icon_px, top = _bold(40), 60, 14
    elif narrow:
        big, icon_px, top = _bold(46), 64, 28
    elif portrait:
        big, icon_px, top = _bold(58), 92, 30
    else:
        big, icon_px, top = _bold(50), 78, 24
    icon = _wx_icon(name, icon_px, _wx_tint(name, light))
    if icon is not None:
        img.paste(icon, (w - 10 - icon_px, top - 4), icon)
    d.text((8, top), _deg(data.get("temp")), font=big, fill=fg)
    cond, _cat = wx.describe(data.get("code"))
    y = top + (46 if short else 54 if narrow else 66 if portrait else 58)
    d.text((10, y), cond, font=font(1, 14 if (narrow or short) else 15), fill=fg)
    y += 20
    feels = data.get("feels")
    if feels is not None and not short:
        d.text((10, y), f"feels like {_deg(feels)}", font=f12, fill=dim)
        y += 18

    # a row of details with their icons
    y += 2 if (short or not portrait) else 6
    row = []
    if data.get("humidity") is not None:
        row.append(("humidity", f"{round(float(data['humidity']))}%"))
    if data.get("wind") is not None:
        unit = "mph" if data.get("units", "F") == "F" else "km/h"
        row.append(("wind", f"{round(float(data['wind']))} {unit} {wx.compass(data.get('wind_dir'))}".strip()))
    today = (data.get("days") or [{}])[0]
    if today.get("sunrise"):
        row.append(("sunrise", today["sunrise"]))
    if today.get("sunset"):
        row.append(("sunset", today["sunset"]))
    x = 10
    for ic_name, text in row:
        ic = _wx_icon(ic_name, 16, _wx_tint(ic_name, light))
        if ic is not None:
            img.paste(ic, (int(x), int(y)), ic)
            x += 20
        d.text((x, y + 1), text, font=f12, fill=fg)
        x += d.textlength(text, font=f12) + 14
        if x > w - 50:                                 # the rest wraps to a second line
            x = 10
            y += 20

    # the next days across the bottom (on a short screen: packed, hi and lo side by side)
    days = (data.get("days") or [])[1:5]
    if days:
        strip_top = h - (56 if short else 74)
        d.line((10, strip_top - 6, w - 10, strip_top - 6), fill=(200, 200, 200) if light else (60, 60, 60), width=1)
        col = (w - 20) / len(days)
        ic_px = 20 if short else 26
        for i, day in enumerate(days):
            cx = 10 + col * i + col / 2
            try:
                label = datetime.strptime(day.get("date", ""), "%Y-%m-%d").strftime("%a")
            except ValueError:
                label = "—"
            _centered(d, label, f12, cx, strip_top, dim)
            dn = wx.icon_name(day.get("code"), True)
            ic = _wx_icon(dn, ic_px, _wx_tint(dn, light))
            if ic is not None:
                img.paste(ic, (int(cx - ic_px / 2), strip_top + 16), ic)
            if short:
                hi, lo = _deg(day.get("hi")), _deg(day.get("lo"))
                tw = d.textlength(hi, font=f13) + 4 + d.textlength(lo, font=f11)
                d.text((cx - tw / 2, strip_top + 38), hi, font=f13, fill=fg)
                d.text((cx - tw / 2 + d.textlength(hi, font=f13) + 4, strip_top + 40), lo, font=f11, fill=dim)
            else:
                _centered(d, _deg(day.get("hi")), f13, cx, strip_top + 44, fg)
                _centered(d, _deg(day.get("lo")), f11, cx, strip_top + 60, dim)
    return img


# -- the GPS ----------------------------------------------------------------------------------------

def gps(conf, now=None, size=None, config=None):
    from .gps import GPS, compass
    from .modes.gps import fmt_coord, fmt_speed, fmt_alt
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    light = bool(conf.get("light_mode"))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    narrow = w < 200
    f11, f12, f13 = font(1, 11), font(1, 12), font(1, 13)
    g = GPS.status()
    units_f = ((config or {}).get("weather") or {}).get("units", "F") == "F"
    good = (30, 150, 75) if light else (70, 220, 120)
    warn = (210, 130, 0) if light else (255, 190, 60)
    bad = (200, 0, 0) if light else (255, 90, 90)

    d.text((10, 8), "GPS", font=f11, fill=dim)
    if not g["enabled"]:
        tag, colour = "OFF", dim
    elif g["fix"] != "none":
        tag, colour = f"{g['fix']} FIX", good
    elif g["connected"]:
        tag, colour = "SEARCHING", warn
    else:
        tag, colour = "NO RECEIVER", bad
    _right(d, tag, f11, w - 10, 8, colour)
    d.line((0, 24, w, 24), fill=dim, width=1)

    if not g["enabled"]:
        _centered(d, "The GPS is off.", f13, w / 2, h / 2 - 18, fg)
        _centered(d, "Plug a USB GPS in: the Weather tab says how it is doing.", f12, w / 2, h / 2 + 4, dim)
        return img
    if not g["connected"] and g["fix"] == "none":
        _centered(d, "No receiver found.", f13, w / 2, h / 2 - 18, fg)
        _centered(d, "Plug the USB GPS in.", f12, w / 2, h / 2 + 4, dim)
        return img

    # the position
    big = _bold(17 if narrow else 20)
    y = 34
    if g["fix"] != "none":
        d.text((10, y), fmt_coord(g["lat"], True), font=big, fill=fg)
        d.text((10, y + 26), fmt_coord(g["lon"], False), font=big, fill=fg)
    else:
        d.text((10, y), "Searching for", font=big, fill=fg)
        d.text((10, y + 26), "satellites…", font=big, fill=dim)
    y += 60
    place = g.get("place")
    if place and g["fix"] != "none":
        d.text((10, y), place, font=f13, fill=accent)
        y += 22

    # the numbers, two per line
    rows = []
    if g["fix"] != "none":
        rows.append(("Altitude", fmt_alt(g.get("alt_m"), units_f)))
        rows.append(("Speed", fmt_speed(g.get("speed_kmh"), units_f)))
        if g.get("course") is not None and (g.get("speed_kmh") or 0) >= 2:
            rows.append(("Heading", f"{g['course']:.0f}° {compass(g['course'])}"))
        rows.append(("Satellites", f"{g['sats_used']} of {g['sats_view']}"))
        if g.get("hdop") is not None:
            rows.append(("Accuracy", f"HDOP {g['hdop']:.1f}"))
    else:
        rows.append(("In view", str(g["sats_view"])))
        rows.append(("Used", str(g["sats_used"])))
    short = h < 200                                   # sideways on the 1.9": the numbers go in a right-hand column
    if short:
        x0, y0, col, step, bar_box = w // 2 + 10, 34, (w // 2 - 20) / 2, 32, (10, h - 50, w // 2 - 10, h - 26)
    else:
        x0, y0, col, step, bar_box = 10, y, (w - 20) / 2, 34, (10, h - 60, w - 10, h - 26)
    for i, (label, value) in enumerate(rows):
        x = x0 + (i % 2) * col
        yy = y0 + (i // 2) * step
        if yy + 28 > (h - 20 if short else bar_box[1] - 6):
            break
        d.text((x, yy), label.upper(), font=f11, fill=dim)
        d.text((x, yy + 13), value, font=f13, fill=fg)

    # the satellites, strongest first, along the bottom
    snr = sorted((g.get("snr") or {}).values(), reverse=True)[:12]
    if snr:
        bx0, top, bx1, bottom = bar_box
        bw = (bx1 - bx0) / 12
        for i, sv in enumerate(snr):
            bh = max(2, round((bottom - top) * min(sv, 50) / 50))
            colour = good if sv >= 30 else warn if sv >= 20 else dim
            d.rectangle((bx0 + i * bw + 1, bottom - bh, bx0 + (i + 1) * bw - 2, bottom), fill=colour)
        d.line((bx0, bottom + 1, bx1, bottom + 1), fill=dim, width=1)
    foot = f"UTC {g['utc']}" if g.get("utc") else ""
    if foot:
        d.text((10, h - 20), foot, font=f11, fill=dim)
    if g.get("device") and g["source"] != "mock":
        _right(d, str(g["device"]).replace("/dev/", ""), f11, w - 10, h - 20, dim)
    return img


# -- a test card, for checking the glass ------------------------------------------------------------

def testcard(conf, now=None, size=None):
    """Fine patterns that show up a faulty panel: a one-pixel checkerboard
    (a strip that has lost columns goes flat grey there), single-pixel
    lines, colour bars, a numbered grid, and a one-pixel border right at the
    edge (missing on one side means the panel's offset is wrong)."""
    w, h = size or canvas_size(conf)
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    f11 = font(1, 11)
    bands = [int(h * 0.30), int(h * 0.18), int(h * 0.16)]
    y = 0
    # 1: a one-pixel checkerboard, then two-pixel, side by side
    try:
        import numpy as np
        yy, xx = np.mgrid[0:bands[0], 0:w]
        fine = ((xx + yy) % 2 == 0)
        coarse = (((xx // 2) + (yy // 2)) % 2 == 0)
        board = np.where(xx < w // 2, fine, coarse).astype(np.uint8) * 255
        img.paste(Image.fromarray(board, "L").convert("RGB"), (0, y))
    except ImportError:
        for py in range(bands[0]):
            for px in range(w):
                on = ((px + py) % 2 == 0) if px < w // 2 else (((px // 2) + (py // 2)) % 2 == 0)
                if on:
                    d.point((px, py), (255, 255, 255))
    d.text((4, y + 3), "1px", font=f11, fill=(255, 60, 60))
    d.text((w // 2 + 4, y + 3), "2px", font=f11, fill=(255, 60, 60))
    y += bands[0]
    # 2: single-pixel vertical lines on the left, horizontal on the right
    for px in range(0, w // 2, 2):
        d.line((px, y, px, y + bands[1] - 1), fill=(255, 255, 255))
    for py in range(y, y + bands[1], 2):
        d.line((w // 2, py, w - 1, py), fill=(255, 255, 255))
    y += bands[1]
    # 3: colour bars
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255), (255, 255, 0), (255, 255, 255), (128, 128, 128)]
    bw = w / len(colours)
    for i, c in enumerate(colours):
        d.rectangle((int(i * bw), y, int((i + 1) * bw) - 1, y + bands[2] - 1), fill=c)
    y += bands[2]
    # 4: a numbered grid every 20 px, and the diagonals
    for gx in range(0, w, 20):
        d.line((gx, y, gx, h - 1), fill=(90, 90, 90))
        if gx and gx + 14 < w:
            d.text((gx + 1, y + 1), str(gx), font=f11, fill=(180, 180, 180))
    for gy in range(y, h, 20):
        d.line((0, gy, w - 1, gy), fill=(90, 90, 90))
    d.line((0, y, w - 1, h - 1), fill=(255, 200, 0))
    d.line((w - 1, y, 0, h - 1), fill=(255, 200, 0))
    # the border: one pixel in from nothing
    d.rectangle((0, 0, w - 1, h - 1), outline=(0, 255, 0))
    d.text((6, h - 16), f"{w}×{h}", font=f11, fill=(0, 255, 0))
    return img


# -- the camera, live -----------------------------------------------------------------------------

def _fit(frame, w, h, mode):
    """A frame sized for the screen: 'cover' fills it (cropping the sides
    or the top and bottom), 'contain' shows all of it with bars."""
    if mode == "contain":
        return ImageOps.contain(frame, (w, h), Image.BILINEAR)
    return ImageOps.fit(frame, (w, h), Image.BILINEAR, centering=(0.5, 0.5))


def camera(conf, now=None, size=None, config=None):
    from .camera import CAMERA
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    cs = (config or {}).get("camera") or {}
    CAMERA.touch(15)                                   # keep it running while this screen is up
    frame = CAMERA.frame()
    if frame is None:
        img = Image.new("RGB", (w, h), bg)
        d = ImageDraw.Draw(img)
        st = CAMERA.status()
        msg = st.get("error") or "Starting the camera…"
        lines = wrap(d, msg, font(1, 13), w - 24, max_lines=4)
        y = h / 2 - 9 * len(lines)
        for ln in lines:
            _centered(d, ln, font(1, 13), w / 2, y, dim)
            y += 18
        return img
    if cs.get("mirror"):
        frame = ImageOps.mirror(frame)
    rot = int(cs.get("rotation", 0) or 0) % 360
    if rot:
        frame = frame.rotate(rot, expand=True)
    fitted = _fit(frame, w, h, cs.get("fit", "cover"))
    img = Image.new("RGB", (w, h), (0, 0, 0))
    img.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    return img


# -- the drawing ----------------------------------------------------------------------------------

def drawing(conf, now=None, size=None, config=None):
    from . import llm
    from .modes.simple import ImageMode
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    light = bool(conf.get("light_mode"))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    src = ImageMode._load()
    if src is None:
        _centered(d, "No drawing yet.", font(1, 13), w / 2, h / 2 - 18, fg)
        _centered(d, "Use the Draw tab to send one.", font(1, 12), w / 2, h / 2 + 4, dim)
        return img
    # the page draws black on white; on a dark screen that becomes white on black
    flat = Image.alpha_composite(Image.new("RGBA", src.size, (255, 255, 255, 255)), src).convert("L")
    if not light:
        flat = ImageOps.invert(flat)
    said = llm.latest()
    caption = (said.get("text") or "").strip() if said.get("kind") == "drawing" else ""
    f_cap = font(1, 13)
    cap_lines = wrap(d, caption, f_cap, w - 20, max_lines=6) if caption else []
    cap_h = len(cap_lines) * 17 + (12 if cap_lines else 0)
    pic = ImageOps.contain(flat, (w - 8, h - 8 - cap_h), Image.LANCZOS)
    top = (h - cap_h - pic.height) // 2
    img.paste(pic.convert("RGB"), ((w - pic.width) // 2, top))
    if cap_lines:
        y = top + pic.height + 12
        for ln in cap_lines:
            _centered(d, ln, f_cap, w / 2, y, dim)
            y += 17
    return img


# -- the painting ---------------------------------------------------------------------------------

def paint(conf, now=None, size=None):
    from .paint import PAINT
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    pic = PAINT.image()
    if pic is None:
        d = ImageDraw.Draw(img)
        _centered(d, "Nothing painted yet.", font(1, 13), w / 2, h / 2 - 18, fg)
        _centered(d, "Draw tab → LCD, and go.", font(1, 12), w / 2, h / 2 + 4, dim)
        return img
    if pic.size != (w, h):
        pic = ImageOps.contain(pic, (w, h), Image.LANCZOS)
    img.paste(pic, ((w - pic.width) // 2, (h - pic.height) // 2))
    return img


# -- the map, in colour --------------------------------------------------------------------------

def map_screen(conf, now=None, size=None, config=None):
    """Where the Pi is, on OpenStreetMap, in colour: a pin in the middle, the
    other PiE-inks as lettered dots, a scale bar, the place across the top."""
    from . import maps, friends, journey
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    f12, f10 = font(1, 12), font(1, 10)
    pos = maps.where(config or {})
    m = (config or {}).get("map", {})
    zoom = max(8, min(18, int(m.get("zoom", 15) or 15)))
    hdr = 18
    view = (w, h - hdr - 16)
    if not pos:
        _centered(d, "No position yet.", f12, w / 2, h / 2 - 14, fg)
        _centered(d, "GPS, or a town under Weather.", f10, w / 2, h / 2 + 4, dim)
        return img
    tiles, missing = maps.compose(pos["lat"], pos["lon"], zoom, view, cached_only=True)
    wanted = len(maps.tiles_for(pos["lat"], pos["lon"], zoom, view))
    if missing:
        maps.fetch_async(pos["lat"], pos["lon"], zoom, view)
    if missing == wanted:
        _centered(d, "Fetching the map…", f12, w / 2, h / 2, dim)
    else:
        img.paste(tiles, (0, hdr))
        if m.get("journey", True):
            pts = journey.JOURNEY.points()
            if len(pts) >= 2:
                maps.draw_trail(d, pts, (pos["lat"], pos["lon"]), zoom, view, hdr, accent, (255, 255, 255), width=3)
        if m.get("friends", True):
            for host, lat, lon in maps.friends_positions(friends.peers()):
                x, y = maps.pixel_of(lat, lon, (pos["lat"], pos["lon"]), zoom, view)
                if 0 <= x < view[0] and 0 <= y < view[1]:
                    maps.draw_friend(d, x, hdr + y, host[:1].upper(), f10, (40, 40, 40), (255, 255, 255), r=7)
        maps.draw_pin(d, view[0] // 2, hdr + view[1] // 2, accent, (255, 255, 255), r=6)
        px, label = maps.scale_bar(pos["lat"], zoom, max_px=70)
        sy = hdr + view[1] - 6
        d.rectangle((2, sy - 13, 8 + max(px, int(d.textlength(label, font=f10))), hdr + view[1] - 1), fill=(255, 255, 255))
        d.line([(4, sy), (4 + px, sy)], fill=(30, 30, 30), width=1)
        d.line([(4, sy - 3), (4, sy)], fill=(30, 30, 30), width=1)
        d.line([(4 + px, sy - 3), (4 + px, sy)], fill=(30, 30, 30), width=1)
        d.text((5, sy - 13), label, font=f10, fill=(30, 30, 30))
    # header and footer
    d.rectangle((0, 0, w, hdr - 1), fill=bg)
    title = pos.get("place") or "Map"
    while len(title) > 1 and d.textlength(title, font=f12) > w - 8:
        title = title[:-1]
    d.text((4, 1), title, font=f12, fill=fg)
    d.rectangle((0, h - 16, w, h), fill=bg)
    coords = f"{abs(pos['lat']):.4f}° {'N' if pos['lat'] >= 0 else 'S'} {abs(pos['lon']):.4f}° {'E' if pos['lon'] >= 0 else 'W'}"
    d.text((4, h - 14), coords, font=f10, fill=accent if pos["source"] == "gps" else dim)
    src = maps.source_words(pos)
    if d.textlength(coords, font=f10) + d.textlength(src, font=f10) + 14 < w:   # the narrow 1.9" has no room for it
        _right(d, src, f10, w - 4, h - 14, dim)
    return img


# -- a coin, in the original layout ------------------------------------------------------------

def _price_text(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "—"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    if p >= 0.01:
        return f"${p:,.4f}"
    return f"${p:.8f}"


def _big_number(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= cut:
            return f"{n / cut:.1f}{suffix}"
    return str(int(n))


class _CryptoFeed:
    """Keeps the watchlist's quotes fresh while the crypto screen is up, on a
    thread of its own, so the LCD loop never waits on the network. It idles
    out a couple of minutes after the screen stops asking."""

    def __init__(self):
        self.quotes = {}
        self.days = 1
        self.thread = None
        self.wanted = 0.0

    def want(self, days):
        self.wanted = time.time()
        self.days = days
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, daemon=True, name="lcd-crypto")
            self.thread.start()

    def quote(self, item, days):
        from . import watchlist
        q = self.quotes.get((item["id"], days))
        if q is None:                                 # the disk cache, or the bundled seed
            q = watchlist.quote(item, days, cached_only=True)
            if q:
                self.quotes[(item["id"], days)] = q
        return q

    def _run(self):
        from . import watchlist
        while time.time() - self.wanted < 120:
            try:
                days = self.days
                for item in watchlist.visible():        # quote() only fetches when its cache is stale
                    q = watchlist.quote(item, days)
                    if q:
                        self.quotes[(item["id"], days)] = q
                    time.sleep(0.5)
            except Exception:
                log.exception("lcd crypto fetch failed")
            for _ in range(30):
                if time.time() - self.wanted >= 120:
                    break
                time.sleep(1)


_CRYPTO = _CryptoFeed()


def crypto(conf, now=None, size=None, config=None):
    from . import mining, watchlist
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    light = bool(conf.get("light_mode"))
    up = (0, 160, 0) if light else (0, 255, 0)
    down = (200, 0, 0) if light else (255, 80, 80)
    now = now or time.time()
    days = int(conf.get("crypto_days", 1) or 1)
    items = watchlist.visible() + [{"id": f"{m.kind}-mining", "symbol": m.symbol, "name": m.title, "mining": m.kind}
                                   for m in mining.enabled()]
    per = max(10, int(((config or {}).get("crypto") or {}).get("coin_seconds", 90) or 90))
    item = items[int(now // per) % len(items)]          # the same coin the e-ink shows, at the same time
    if item.get("mining"):
        return _crypto_mining(conf, (w, h), (bg, fg, accent, dim, up, down), mining.MINERS[item["mining"]])
    _CRYPTO.want(days)
    q = _CRYPTO.quote(item, days)

    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    narrow = w < 200                                  # the 1.9" upright: everything a size down
    f_big, f_small, f_tiny = (_bold(17), font(1, 11), font(1, 9)) if narrow else (_bold(20), font(1, 13), font(1, 11))
    pad, top, header = 12, 10, 60
    d.text((pad, top), (q["name"] if q else item.get("name", item["symbol"])).upper(), font=f_small, fill=dim)
    if not q or q.get("price") is None:
        _centered(d, "Waiting for prices…", f_small, w / 2, h / 2, dim)
        return img

    # the chart: the window's prices, thinned to one per pixel
    values = list(q.get("graph") or [])
    price = float(q["price"])
    change = pct = None
    lo = hi = None
    if len(values) >= 2:
        step = max(1, len(values) // (w - pad * 2))
        vals = values[::step]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        start, last = vals[0], vals[-1]
        change = last - start                          # over the chart's window, like the chart
        pct = change / start * 100 if start else 0
        colour = up if change >= 0 else down
        chart_h = h - header - 50
        usable = chart_h - 20
        pts = [(pad + int(i * ((w - pad * 2) / len(vals))), header + 10 + int((1 - (v - lo) / span) * usable))
               for i, v in enumerate(vals)]
        d.line(pts, fill=colour, width=2)
    else:
        lo, hi = q.get("day_low"), q.get("day_high")
        pct = (q.get("change") or {}).get("24h")
        if pct is not None:
            change = price * pct / (100 + pct)

    # header: price and the change over the window
    d.text((pad, top + 18), _price_text(price), font=f_big, fill=fg)
    if change is not None and pct is not None:
        colour = up if change >= 0 else down
        dec = 0 if price >= 1000 else (2 if price >= 1 else 4)
        d.text((pad, top + 40), f"{change:+,.{dec}f} ({pct:+.2f}%)", font=f_small, fill=colour)
    if hi and lo:
        _right(d, f"H: {_price_text(hi)}", f_tiny, w - 10, top, dim)
        _right(d, f"L: {_price_text(lo)}", f_tiny, w - 10, top + 15, dim)

    # bottom left: the figures
    y = h - 45
    d.text((pad, y), f"MC: ${_big_number(q.get('market_cap'))}", font=f_tiny, fill=dim)
    d.text((pad, y + 12), f"SUPPLY: {_big_number(q.get('circulating_supply'))}", font=f_tiny, fill=dim)
    d.text((pad, y + 24), f"ATH: {_price_text(q.get('ath'))}", font=f_tiny, fill=dim)

    # bottom right: the mood — CoinGecko's community vote, the same number the e-ink shows
    # (counting the chart's up-ticks against its down-ticks, as this did, comes out near 50/50
    # for any coin, and 50/50 exactly when the chart hasn't arrived yet)
    vote = q.get("sentiment_up")
    if vote is not None:
        up_pct = int(round(float(vote)))
        down_pct = 100 - up_pct
        if up_pct > 55:
            label, colour = "BULLISH", up
        elif up_pct < 45:
            label, colour = "BEARISH", down
        else:
            label, colour = "NEUTRAL", dim
        _right(d, label, f_tiny, w - 10, h - 34, colour)
        _right(d, f"+{up_pct}% / -{down_pct}%", f_small, w - 10, h - 22, dim)
    else:
        _right(d, "SENTIMENT", f_tiny, w - 10, h - 34, dim)
        _right(d, "no vote yet", f_small, w - 10, h - 22, dim)
    return img


def _crypto_mining(conf, size, colours, miner):
    """Your miners on the colour screen: balance, hashrate against the
    line you set, what it earns, and the miners one by one."""
    from . import mining
    w, h = size
    bg, fg, accent, dim, up, down = colours
    d = miner.snapshot()
    sym, dec = miner.symbol, miner.decimals
    img = Image.new("RGB", (w, h), bg)
    dr = ImageDraw.Draw(img)
    narrow = w < 200
    f_big, f_mid, f_small, f_tiny = (_bold(17), _bold(13), font(1, 11), font(1, 9)) if narrow else (_bold(20), _bold(15), font(1, 13), font(1, 11))
    pad, top = 12, 10
    dr.text((pad, top), miner.title, font=f_small, fill=dim)
    _right(dr, miner.who_short()[:16], f_small, w - 10, top, dim)
    if not d:
        _centered(dr, miner.error or "Waiting for the numbers…", f_small, w / 2, h / 2, dim)
        return img
    bal = d["balance"]
    dr.text((pad, top + 18), f"{bal:,.{dec}f} {sym}" if bal < 1e5 else f"{bal:,.0f} {sym}", font=f_big, fill=fg)
    y = top + 44
    sub = []
    if miner.balance_label != "balance":
        sub.append(miner.balance_label)
    if d.get("price"):
        sub.append(f"${bal * d['price']:,.2f}")
        sub.append("$" + f"{d['price']:.6f}".rstrip("0").rstrip(".") + " each")
    if d.get("paid") is not None:
        sub.append(f"paid {d['paid']:,.2f}")
    if sub:
        while len(sub) > 1 and dr.textlength("  ·  ".join(sub), font=f_tiny) > w - pad * 2:
            sub.pop()                                    # the narrow 1.9" gets the first few
        dr.text((pad, y), "  ·  ".join(sub), font=f_tiny, fill=dim)
        y += 16
    # the hashrate, coloured against the line
    is_mining = d["hashrate"] > 0
    colour = down if (not is_mining or d.get("alert")) else up
    dr.text((pad, y + 4), d["hashrate_text"] if is_mining else "NOT MINING", font=f_mid, fill=colour)
    workers = f"{d['workers']} worker{'s' if d['workers'] != 1 else ''}"
    _right(dr, workers, f_small, w - 10, y + 6, dim)
    y += 26
    if d.get("line"):
        dr.text((pad, y), ("under" if d.get("alert") else "above") + f" your {mining.hashrate_text(d['line'])} line",
                font=f_tiny, fill=colour if d.get("alert") else dim)
        y += 14
    if d.get("per_day") is not None:
        dr.text((pad, y), f"~{d['per_day']:.{min(dec, 3)}f} {sym} a day", font=f_small, fill=accent)
        y += 18
    # the miners
    y += 4
    dr.line([(pad, y), (w - pad, y)], fill=dim, width=1)
    y += 6
    room = h - 34 - y
    rows = max(0, int(room // 15))
    for m in d["miners"][:rows]:
        label = m["identifier"]
        rate = mining.hashrate_text(m["hashrate"])
        while len(label) > 3 and dr.textlength(label, font=f_tiny) > w - pad * 2 - dr.textlength(rate, font=f_tiny) - 8:
            label = label[:-1]
        dr.text((pad, y), label, font=f_tiny, fill=fg)
        _right(dr, rate, f_tiny, w - pad, y, dim)
        y += 15
    if len(d["miners"]) > rows and rows > 0:
        dr.text((pad, y), f"+{len(d['miners']) - rows} more", font=f_tiny, fill=dim)
    elif not d["miners"]:
        dr.text((pad, y), "no miners connected", font=f_tiny, fill=dim)
    # footer: what the pool says about you, and the network
    dr.line([(0, h - 30), (w, h - 30)], fill=dim, width=1)
    if d.get("trust_score") is not None:
        dr.text((pad, h - 26), f"TRUST {d['trust_score']}", font=f_tiny, fill=dim)
    elif d.get("luck"):
        dr.text((pad, h - 26), f"LUCK {d['luck']}"[:18], font=f_tiny, fill=dim)
    if d.get("pools"):
        dr.text((pad, h - 14), f"POOL {d['pools'][0]}"[:22], font=f_tiny, fill=dim)
    if d.get("net_hashrate"):
        _right(dr, f"NET {d['net_hashrate']}", f_tiny, w - pad, h - 26, dim)
    elif d.get("avg_hashrate_24h"):
        _right(dr, f"24H AVG {mining.hashrate_text(d['avg_hashrate_24h'])}", f_tiny, w - pad, h - 26, dim)
    if d.get("age") is not None and d["age"] > 600:
        _right(dr, f"{d['age'] // 60} min old", f_tiny, w - pad, h - 14, down)
    return img


def mirror(conf, now=None, eink=None, size=None):
    """The e-ink's frame, letterboxed onto the LCD (turn the LCD sideways —
    rotation 90 or 270 — and the e-ink's landscape frame fills it)."""
    w, h = size or canvas_size(conf)
    bg, fg, accent, dim = _colours(conf)
    img = Image.new("RGB", (w, h), bg)
    frame = eink() if eink else None
    if frame is None:
        d = ImageDraw.Draw(img)
        _centered(d, "Nothing on the e-ink yet", font(1, 14), w / 2, h / 2 - 10, dim)
        return img
    frame = frame.convert("L")
    if conf.get("light_mode"):
        frame = ImageOps.invert(frame) if frame.getpixel((0, 0)) < 128 else frame
    else:
        frame = ImageOps.invert(frame) if frame.getpixel((0, 0)) > 128 else frame
    frame = ImageOps.contain(frame, (w - 8, h - 8), Image.LANCZOS)
    img.paste(frame.convert("RGB"), ((w - frame.width) // 2, (h - frame.height) // 2))
    return img


SCREENS = {"system": system, "clock": clock, "weather": weather, "crypto": crypto, "gps": gps, "camera": camera,
           "paint": paint, "drawing": drawing, "map": map_screen, "bot": bot, "chat": chat, "mirror": mirror,
           "testcard": testcard, "buddy": buddy}
LABELS = [("system", "System info"), ("clock", "Clock"), ("weather", "Weather"), ("crypto", "Crypto"),
          ("gps", "GPS"), ("map", "Map (where it is)"), ("camera", "Camera"), ("paint", "Paint (draw on it)"),
          ("drawing", "E-ink drawing"),
          ("bot", "The bot"), ("buddy", "The little friend's face"), ("chat", "Group chat"), ("mirror", "Mirror the e-ink"),
          ("testcard", "Test card (check the glass)")]
WITH_CONFIG = {crypto, weather, gps, camera, drawing, map_screen}  # screens that read other sections' settings


def render(conf, eink=None, config=None, size=None):
    """The chosen screen, drawn the way round the panel is mounted (see
    canvas_size) or at `size`; the driver rotates it onto the glass. `config`
    is the whole settings tree, for screens that share settings with the e-ink."""
    name = conf.get("screen", "system")
    fn = SCREENS.get(name, system)
    size = size or canvas_size(conf)
    if fn is mirror:
        return mirror(conf, eink=eink, size=size)
    if fn in WITH_CONFIG:
        return fn(conf, size=size, config=config)
    return fn(conf, size=size)
