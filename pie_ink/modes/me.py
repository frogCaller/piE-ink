"""Me: a screen about you. The schedule in config.yaml decides what you're
doing right now (only the activity is shown, never the timetable), paid
blocks make money tick up through the day, and the face and its comments
take in the weather, your coins and the time."""
import logging
import math
import time
from datetime import date, datetime

import psutil

from .. import coingecko, watchlist, weather
from ..text import font, text_width, wrap
from .base import Mode
from .crypto import FACES, _cpu_temp
from .system import icon as sys_icon
from .weather import QUOTES as WX_QUOTES, icon as wx_icon

log = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
QUOTE_SECONDS = 10
BOTTOM_SECONDS = 20
STAT_SECONDS = 12        # how long each stat is the headline
FACE_SECONDS = 30        # face, then the pay graph, then back
GRAPH_SECONDS = 20


def _hm(s):
    h, m = (s or "0:00").split(":")[:2]
    return int(h) * 60 + int(m)


def _money(v, sym="$"):
    if v is None:
        return "--"
    if abs(v) >= 1e6:
        return f"{sym}{v / 1e6:.2f}M"
    if abs(v) >= 1e5:
        return f"{sym}{v / 1e3:.0f}K"
    if abs(v) >= 1e4:
        return f"{sym}{v / 1e3:.1f}K"
    if abs(v) >= 100:
        return f"{sym}{v:,.0f}"
    return f"{sym}{v:,.2f}"


class Day:
    """Today's blocks, from the daily schedule in settings (overnight blocks
    are split in two)."""

    def __init__(self, settings, now=None):
        self.now = now or datetime.now()
        self.minute = self.now.hour * 60 + self.now.minute + self.now.second / 60
        self.workday = DAYS[self.now.weekday()] in [d.lower() for d in settings.get("workdays", DAYS[:5])]
        self.blocks = []            # (start_min, end_min, label, paid)
        for b in settings.get("blocks", settings.get("schedule", [])):
            try:
                s, e = _hm(b.get("start")), _hm(b.get("end"))
            except ValueError:
                continue
            label, paid = (b.get("activity") or "Busy").strip(), bool(b.get("paid"))
            if e > s:
                self.blocks.append((s, e, label, paid))
            else:                                 # crosses midnight
                self.blocks.append((s, 24 * 60, label, paid))
                self.blocks.append((0, e, label, paid))

    def active_blocks(self):
        """The blocks that apply today: paid (work) ones only on work days,
        everything else — sleep, gym, whatever — every day."""
        return [b for b in self.blocks if not b[3] or self.workday]

    def current(self):
        for s, e, label, paid in self.active_blocks():
            if s <= self.minute < e:
                return label, paid, e
        return None, False, None

    def paid_minutes_per_day(self):
        return sum(e - s for s, e, _, paid in self.blocks if paid)

    def paid_minutes_so_far(self):
        if not self.workday:
            return 0.0
        return sum(max(0.0, min(self.minute, e) - s) for s, e, _, paid in self.blocks if paid)


def _dashed(d, points, fg, width=1, dash=4, gap=3):
    """A dashed polyline, walked by distance so the dashes stay even."""
    carry, on = 0.0, True
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while pos < seg:
            step = min((dash if on else gap) - carry, seg - pos)
            if on and step > 0:
                t0, t1 = pos / seg, (pos + step) / seg
                d.line([(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                        (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)], fill=fg, width=width)
            pos += step
            carry += step
            if carry >= (dash if on else gap) - 1e-9:
                carry, on = 0.0, not on


# -- little icons -------------------------------------------------------------------------
# Drawn with primitives so they scale with the panel; the weather and system
# sets are reused from their PNGs where one already exists.

def draw_icon(f, name, x, y, size):
    """Draw `name` at panel coords (x, y) in a size x size box."""
    d, fg, bg = f.draw, f.fg, f.bg
    s = size
    cx, cy = x + s / 2, y + s / 2
    if name in ("sunny", "moon", "cloud", "overcast", "partly_day", "partly_night", "rain_day",
                "rain_night", "heavyrain_day", "heavyrain_night", "snow_day", "snow_night",
                "storm_day", "storm_night", "fog_day", "fog_night", "sunrise", "sunset",
                "wind", "humidity"):
        f.stamp(wx_icon(name), (x, y), s)
        return
    if name in ("cpu", "ram", "temp", "storage"):
        f.stamp(sys_icon(name, 64), (x, y), s)
        return
    if name == "clock":
        d.ellipse([x + 0.5, y + 0.5, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
        d.line([(cx, cy), (cx, y + s * 0.24)], fill=fg, width=1)
        d.line([(cx, cy), (x + s * 0.74, cy + s * 0.08)], fill=fg, width=1)
    elif name == "calendar":
        d.rectangle([x + 0.5, y + s * 0.16, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
        d.line([(x + 0.5, y + s * 0.42), (x + s - 0.5, y + s * 0.42)], fill=fg, width=1)
        d.line([(x + s * 0.3, y), (x + s * 0.3, y + s * 0.3)], fill=fg, width=1)
        d.line([(x + s * 0.7, y), (x + s * 0.7, y + s * 0.3)], fill=fg, width=1)
    elif name == "work":                     # a briefcase
        d.rectangle([x + 0.5, y + s * 0.3, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
        d.line([(x + s * 0.33, y + s * 0.3), (x + s * 0.33, y + s * 0.12)], fill=fg, width=1)
        d.line([(x + s * 0.67, y + s * 0.3), (x + s * 0.67, y + s * 0.12)], fill=fg, width=1)
        d.line([(x + s * 0.33, y + s * 0.12), (x + s * 0.67, y + s * 0.12)], fill=fg, width=1)
        d.line([(x + 0.5, y + s * 0.58), (x + s - 0.5, y + s * 0.58)], fill=fg, width=1)
    elif name == "sleep":                    # a crescent
        d.ellipse([x + 0.5, y + 0.5, x + s - 0.5, y + s - 0.5], fill=fg)
        d.ellipse([x + s * 0.3, y - s * 0.12, x + s * 1.25, y + s * 0.85], fill=bg)
    elif name == "cake":
        d.rectangle([x + 0.5, y + s * 0.45, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
        d.line([(x + 0.5, y + s * 0.45), (x + s - 0.5, y + s * 0.45)], fill=fg, width=1)
        d.line([(cx, y + s * 0.45), (cx, y + s * 0.18)], fill=fg, width=1)
        d.ellipse([cx - s * 0.09, y + s * 0.02, cx + s * 0.09, y + s * 0.2], fill=fg)
    elif name == "book":
        d.rectangle([x + 0.5, y + s * 0.15, cx, y + s - 0.5], outline=fg, width=1)
        d.rectangle([cx, y + s * 0.15, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
    elif name == "people":
        d.ellipse([x + s * 0.08, y + s * 0.12, x + s * 0.42, y + s * 0.46], outline=fg, width=1)
        d.arc([x + s * 0.02, y + s * 0.45, x + s * 0.48, y + s * 1.0], 180, 360, fill=fg, width=1)
        d.ellipse([x + s * 0.55, y + s * 0.18, x + s * 0.85, y + s * 0.48], outline=fg, width=1)
        d.arc([x + s * 0.5, y + s * 0.48, x + s * 0.92, y + s * 1.0], 180, 360, fill=fg, width=1)
    elif name == "free":                     # a smiley
        d.ellipse([x + 0.5, y + 0.5, x + s - 0.5, y + s - 0.5], outline=fg, width=1)
        r = max(0.5, s * 0.07)
        d.ellipse([x + s * 0.3 - r, y + s * 0.36 - r, x + s * 0.3 + r, y + s * 0.36 + r], fill=fg)
        d.ellipse([x + s * 0.7 - r, y + s * 0.36 - r, x + s * 0.7 + r, y + s * 0.36 + r], fill=fg)
        d.arc([x + s * 0.22, y + s * 0.35, x + s * 0.78, y + s * 0.82], 20, 160, fill=fg, width=1)
    elif name == "chip":
        d.rectangle([x + s * 0.2, y + s * 0.2, x + s * 0.8, y + s * 0.8], outline=fg, width=1)
        for i in (0.35, 0.65):
            d.line([(x + s * i, y + s * 0.2), (x + s * i, y + s * 0.06)], fill=fg, width=1)
            d.line([(x + s * i, y + s * 0.8), (x + s * i, y + s * 0.94)], fill=fg, width=1)
            d.line([(x + s * 0.2, y + s * i), (x + s * 0.06, y + s * i)], fill=fg, width=1)
            d.line([(x + s * 0.8, y + s * i), (x + s * 0.94, y + s * i)], fill=fg, width=1)


def psutil_boot():
    return psutil.boot_time()


def _dur(minutes):
    minutes = max(0, int(minutes))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h < 24 else f"{h // 24}d {h % 24}h"


class MeMode(Mode):
    """A rotating handful of small facts about your day, with a face that
    reacts to the weather, your coins and the time."""
    name = "me"
    label = "Me"

    @property
    def interval(self):
        return 10.0

    # -- context ---------------------------------------------------------------------------

    def _schedule(self):
        return self.config.get("schedule", {})

    def _coin(self):
        items = watchlist.visible()
        item = items[0] if items else {"id": "bitcoin", "symbol": "BTC"}
        return item, coingecko.coin_data(item["id"], cached_only=True)

    def _weather(self):
        w = self.config.get("weather", {})
        if w.get("lat") is None:
            return None
        try:
            return weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"), cached_only=True)
        except Exception:
            return None

    # -- the stats ---------------------------------------------------------------------------

    def _pool(self, day, wx, now):
        """[(value, unit, label, icon)]. The headline shows value + unit, so it
        always reads on its own; the label goes on the line under it."""
        out = []
        activity, _, ends = day.current()

        # the day and the year
        start_of_year = date(now.year, 1, 1)
        day_of_year = (now.date() - start_of_year).days + 1
        days_in_year = 366 if (now.year % 4 == 0 and (now.year % 100 or now.year % 400 == 0)) else 365
        out.append((f"{day_of_year}", "days", f"into {now.year}", "calendar"))
        out.append((f"{round(day_of_year / days_in_year * 100)}%", "", f"of {now.year} gone", "calendar"))
        out.append((f"{days_in_year - day_of_year}", "days", f"left in {now.year}", "calendar"))
        out.append((f"{int(now.isocalendar()[1])}", "weeks", "into the year", "calendar"))
        mins_today = now.hour * 60 + now.minute
        out.append((f"{round(mins_today / 1440 * 100)}%", "", "through the day", "clock"))

        # what you're doing
        act_icon = "sleep" if activity and "sleep" in activity.lower() else "work" if activity else "free"
        if activity:
            since = next((st for st, e, lab, _ in day.active_blocks() if st <= day.minute < e), None)
            if since is not None:
                out.append((_dur(day.minute - since), "", f"into {activity.lower()}", act_icon))
            if ends is not None:
                out.append((_dur(ends - day.minute), "", f"left of {activity.lower()}", "clock"))
        wake = next((e for st, e, lab, _ in day.active_blocks() if "sleep" in lab.lower() and e <= day.minute), None)
        if wake is not None:
            out.append((_dur(day.minute - wake), "", "awake today", "free"))
        if day.workday:
            paid = day.paid_minutes_per_day()
            if paid:
                out.append((f"{round(min(100, day.paid_minutes_so_far() / paid * 100))}%", "", "of the work day", "work"))
                out.append((_dur(paid), "", "in a work day", "work"))
            out.append((f"{5 - min(4, now.weekday())}", "work days", "left this week", "work"))
        else:
            out.append(("Day off", "", "no work today", "free"))


        # the weather
        if wx:
            from .. import weather as wx_mod
            cond = wx_mod.describe(wx["code"])
            out.append((f"{round(wx['temp'])}°{wx['units']}", "", cond[0].lower(),
                        wx_mod.icon_name(wx["code"], wx["is_day"])))
            if wx.get("days"):
                out.append((wx["days"][0]["sunset"], "", "sunset tonight", "sunset"))
                out.append((wx["days"][0]["sunrise"], "", "sunrise was", "sunrise"))
                if wx["days"][0].get("rain") is not None:
                    out.append((f"{wx['days'][0]['rain']}%", "", "chance of rain", "humidity"))
            out.append((f"{wx['humidity']}%", "", "humidity", "humidity"))

        # you
        books = self._books()
        if books:
            out.append((f"{books}", "books", "on the shelf", "book"))
        birthday = (self.settings.get("birthday") or "").strip()
        if birthday:
            try:
                born = date.fromisoformat(birthday)
                days = (now.date() - born).days
                out.append((f"{int(days // 365.2425)}", "years", "old", "cake"))
                nxt_bd = born.replace(year=now.year)
                if nxt_bd < now.date():
                    nxt_bd = born.replace(year=now.year + 1)
                to_bd = (nxt_bd - now.date()).days
                if to_bd == 0:
                    out.append(("Today!", "", "is your birthday", "cake"))
                else:
                    out.append((f"{to_bd}", "days", "until your birthday", "cake"))
                out.append((f"{days:,}", "days", "on this planet", "cake"))
            except ValueError:
                pass
        return out

    @staticmethod
    def _books():
        try:
            from .. import reader
            return len(reader.library())
        except Exception:
            return 0

    @staticmethod
    def _friends():
        try:
            from .. import friends
            return len(friends.peers())
        except Exception:
            return 0

    # -- mood -----------------------------------------------------------------------------------

    def _occasion_notes(self, day, now):
        """Comments about the date itself, so it isn't always weather talk."""
        notes = []
        weekday = now.weekday()
        if not day.workday:
            notes += ["Enjoy the day off", "No alarms today"]
            if weekday == 6:
                notes.append("Sunday pace")
        elif weekday == 0:
            notes.append("Monday again")
        elif weekday == 4:
            notes.append("Friday at last")
        elif weekday == 3:
            notes.append("Nearly the weekend")
        day_of_year = (now.date() - date(now.year, 1, 1)).days + 1
        if day_of_year >= 350:
            notes.append(f"{now.year} is nearly done")
        elif day_of_year <= 14:
            notes.append(f"{now.year} is young")
        elif day_of_year in (182, 183):
            notes.append("Half the year gone")
        if now.day == 1:
            notes.append(f"New month: {now.strftime('%B')}")
        birthday = (self.settings.get("birthday") or "").strip()
        if birthday:
            try:
                born = date.fromisoformat(birthday)
                nxt = born.replace(year=now.year)
                if nxt < now.date():
                    nxt = born.replace(year=now.year + 1)
                days = (nxt - now.date()).days
                if days == 0:
                    notes.append("Happy birthday!")
                elif days <= 7:
                    notes.append(f"Birthday in {days} day{'' if days == 1 else 's'}")
            except ValueError:
                pass
        try:
            up_days = (time.time() - psutil_boot()) / 86400
            if up_days >= 7:
                notes.append(f"Up {int(up_days)} days without a reboot")
        except Exception:
            pass
        return notes

    MOODS = {                                  # what the dropdown offers
        "happy": ("happy", "awake"), "cool": ("cool",), "curious": ("look_r", "look_l"),
        "cheerful": ("look_r_happy", "look_l_happy"), "tired": ("sleep", "sleep2"),
        "bored": ("bored",), "worried": ("worried",), "sad": ("sad",), "hot": ("hot", "hot2"),
    }

    def _lines_from(self, name):
        """A few lines from an assets file, reshuffled now and then."""
        import os
        import random as rnd
        from ..settings import ASSETS_DIR
        key = f"_pool_{name}"
        stamp = int(time.time() // 600)        # a new selection every ten minutes
        cached = getattr(self, key, None)
        if cached and cached[0] == stamp:
            return cached[1]
        picks = []
        try:
            with open(os.path.join(ASSETS_DIR, name)) as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
            picks = rnd.sample(lines, min(3, len(lines)))
        except OSError:
            pass
        setattr(self, key, (stamp, picks))
        return picks

    def _jokes(self):
        import json
        import os
        import random as rnd
        from ..settings import ASSETS_DIR
        stamp = int(time.time() // 600)
        cached = getattr(self, "_pool_jokes", None)
        if cached and cached[0] == stamp:
            return cached[1]
        picks = []
        try:
            with open(os.path.join(ASSETS_DIR, "jokes.json")) as fh:
                jokes = json.load(fh)
            picks = [f"{j['setup']} — {j['punchline']}" for j in rnd.sample(jokes, min(2, len(jokes)))]
        except (OSError, ValueError, KeyError):
            pass
        self._pool_jokes = (stamp, picks)
        return picks

    def _mood(self, activity, wx, coin_q, extra_notes=()):
        score, notes = 0, list(extra_notes)
        hour = datetime.now().hour
        if wx:
            cat = weather.describe(wx["code"])[1]
            t = wx["temp"] or 0
            f = t if wx["units"] == "F" else t * 9 / 5 + 32
            if cat in ("rain", "storm"):
                score -= 1
                notes += WX_QUOTES["rain" if cat == "rain" else "storm"]
            elif f >= 88:
                score -= 1
                notes += WX_QUOTES["hot"]
            elif cat == "clear" and 60 <= f < 88 and wx["is_day"]:
                score += 1
                notes += ["Lovely out — a walk later?", "Sun's out"]
        if coin_q and coin_q.get("change", {}).get("24h") is not None:
            ch = coin_q["change"]["24h"]
            if ch >= 2:
                score += 1
                notes.append(f"Coins up {ch:+.1f}% today!")
            elif ch <= -2:
                score -= 1
                notes.append(f"Coins down {ch:+.1f}%. Hold on.")
        act = (activity or "").lower()
        if "sleep" in act or "bed" in act:
            return (FACES["sleep"] if int(time.time() // 3) % 2 else FACES["sleep2"]), ["Zzz", "Good night", "Rest up"]
        if hour < 10:
            notes.append("Good morning")
        elif hour >= 21:
            notes.append("Wind down soon")
        if "work" in act or "office" in act or "shift" in act:
            notes += ["Back to it", "Keep going", "Making progress", "One thing at a time",
                      "Nearly there", "Steady on", "Stretch your legs", "Look away from the screen",
                      "Drink some water", "Coffee break?"]
            face = (FACES["look_r"] if int(time.time() // 3) % 2 else FACES["look_l"]) if score >= 0 else FACES["sad"]
        elif activity:
            notes += [f"Enjoy: {activity}", "Nice", "Time well spent", "No rush"]
            face = FACES["cool"] if score > 0 else FACES["happy"] if score == 0 else FACES["awake"]
        else:
            notes += ["Free time", "Nothing planned", "Do as you like", "Whole day ahead"]
            face = FACES["cool"] if score > 0 else FACES["happy"] if score == 0 else FACES["sad"]
        if (_cpu_temp() or 0) >= 72:
            face = FACES["hot"]
        chosen = (self.settings.get("mood") or "auto").lower()
        if chosen in self.MOODS:               # you picked a mood: that one wins
            keys = self.MOODS[chosen]
            face = FACES[keys[int(time.time() // 3) % len(keys)]]
        if self.settings.get("show_fortunes", True):
            notes += self._lines_from("fortunes.txt") + self._lines_from("topics.txt")
        if self.settings.get("show_jokes", True):
            notes += self._jokes()
        return face, notes

    # -- render -------------------------------------------------------------------------------

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        s = self.settings
        now_t = time.time()
        now = datetime.now()
        T = max(1.0, min(1.6, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f12 = font(1, round(12 * T))
        hdr = round(14 * T)
        footer = f.height - round(14 * T)
        band_h = round(29 * T)
        band = footer - 1 - band_h

        def spread(items, y):
            widths = [text_width(d, t, f12) for t in items]
            gap = (f.width - 6 - sum(widths)) / max(1, len(items) - 1)
            x = 3
            for t, w in zip(items, widths):
                d.text((x, y), t, font=f12, fill=fg)
                x += w + gap

        day = Day(self._schedule())
        activity, _, ends = day.current()
        wx = self._weather()
        coin = self._coin()
        face, notes = self._mood(activity, wx, coin[1], self._occasion_notes(day, now))
        quote = notes[int(now_t // QUOTE_SECONDS) % len(notes)] if notes else ""
        pool = self._pool(day, wx, now)

        # header: name · job · time
        head = [(s.get("name") or "You").upper()]
        if s.get("job_title"):
            head.append(s["job_title"].upper())
        head.append(now.strftime("%a %-I:%M %p").upper())
        spread(head, 0)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        # the featured stat, big on the right; the rest in the column below it
        turn = int(now_t // STAT_SECONDS)
        window = [pool[(turn + i) % len(pool)] for i in range(6)] if pool else []
        d.text((5, hdr + 1), f"{self.config.get('crypto', {}).get('username', 'pi')}>", font=f12, fill=fg)
        if window:
            big = font(1, round(25 * T))
            unit_font = font(1, round(13 * T))
            value, unit = window[0][0], window[0][1]
            while text_width(d, value, big) > f.width * 0.55 and len(value) > 3:
                value = value[:-2].rstrip() + "…"
            unit_w = (text_width(d, unit, unit_font) + 4) if unit else 0
            x = f.width - 10 - text_width(d, value, big) - unit_w
            d.text((x, hdr + 2), value, font=big, fill=fg)
            if unit:
                d.text((x + text_width(d, value, big) + 4, hdr + 13 * T), unit, font=unit_font, fill=fg)

        col = max(f.width // 2, int(text_width(d, "(◕‿‿◕)", font(2, round(32 * T))) + 12 * T))
        face_y = (hdr + band) / 2 - 21 * T if (band - hdr) > 120 else max(hdr + 14 * T, (hdr + band) / 2 - 21 * T)
        d.text((3, face_y), face, font=font(2, round(32 * T)), fill=fg)

        top = hdr + 30 * T
        avail = band - 4 - top
        lh = 14 * T
        rows = int(avail // lh)
        room = f.width - col - 4

        def fit(line):
            while text_width(d, line, f12) > room and len(line) > 6:
                line = line[:-2].rstrip() + "…"
            return line

        ipx = round(11 * T)
        icon_gap = ipx + 4

        def row(line, y, icon=None, indent=0):
            """One column line: an icon, then the text."""
            x = col + indent
            if icon:
                draw_icon(f, icon, x, y + 1, ipx)
                x += icon_gap
            text = line
            room2 = f.width - x - 4
            if text_width(d, text, f12) > room2:
                words = text.split()
                while len(words) > 1 and text_width(d, " ".join(words) + "…", f12) > room2:
                    words.pop()
                text = " ".join(words) + "…"
                while text_width(d, text, f12) > room2 and len(text) > 4:
                    text = text[:-2].rstrip() + "…"
            d.text((x, y), text, font=f12, fill=fg)

        # the headline already carries the unit, so this line is just the label
        lines = [(window[0][2], window[0][3])] if window else [("Nothing to report", None)]
        lines += [(" ".join(x for x in (value, unit, label) if x), icon) for value, unit, label, icon in window[1:]]
        def wrapped(text, limit):
            """Wrap to at most `limit` lines, marking it if anything was cut."""
            if not text:
                return []
            full = wrap(d, text, f12, room)
            if len(full) <= limit:
                return full
            cut = full[:limit]
            cut[-1] = cut[-1].rstrip() + "…"
            return cut

        if rows >= 3:
            # the comment takes the lines it needs to finish, even if that
            # means showing fewer stats above it
            quote_lines = wrapped(quote, rows)
            keep = lines[:max(0, rows - len(quote_lines))]
            block = [(t, i) for t, i in keep] + [(q, None) for q in quote_lines]
            # on a taller screen the spare lines let a long fact run onto a
            # second line instead of being cut short
            spare = rows - len(block)
            items = []
            for text, icon in block:
                parts = wrap(d, text, f12, room - (icon_gap if icon else 0))
                if len(parts) > 1 and spare > 0:
                    items.append(([parts[0], " ".join(parts[1:])], icon))
                    spare -= 1
                else:
                    items.append(([text], icon))
            total = sum(len(ls) for ls, _ in items)
            step = min(avail / max(1, total), lh * 1.5)
            y = top + 2 * T
            for ls, icon in items:
                row(ls[0], y, icon)
                y += step
                for extra in ls[1:]:
                    row(extra, y, None, indent=icon_gap if icon else 0)
                    y += step
        else:
            # two lines: the stat's label then the comment — unless the comment
            # needs both, in which case it takes them (the headline reads alone)
            quote_lines = wrapped(quote, 2)
            if len(quote_lines) > 1:
                row(quote_lines[0], hdr + 31 * T)
                row(quote_lines[1], hdr + 45 * T)
            else:
                row(lines[0][0], hdr + 31 * T, lines[0][1])
                row(quote_lines[0] if quote_lines else "", hdr + 45 * T)

        # bottom band: how far through the day, the week and the year
        bars = [("day", (now.hour * 60 + now.minute) / 1440, "00:00", "24:00"),
                ("week", (now.weekday() + (now.hour * 60 + now.minute) / 1440) / 7, "Mon", "Sun"),
                ("year", ((now.date() - date(now.year, 1, 1)).days + 1) / 365, "Jan", "Dec")]
        label, pos, left_lab, right_lab = bars[int(now_t % (BOTTOM_SECONDS * len(bars)) // BOTTOM_SECONDS)]
        bar_h = round(9 * T)
        bar_y = band + round(14 * T)
        bar_w = int(f.width * 0.66)
        bar_x = (f.width - bar_w) // 2
        pos = max(0.0, min(1.0, pos))
        tag = f"{round(pos * 100)}% of the {label}"
        d.text(((f.width - text_width(d, tag, f12)) // 2, band), tag, font=f12, fill=fg)
        radius = bar_h / 2
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius, outline=fg, width=1)
        fill = (bar_w - 2) * pos
        if fill >= 1:
            d.rounded_rectangle([bar_x + 1, bar_y + 1, bar_x + 1 + fill, bar_y + bar_h - 1],
                                min(radius - 1, fill / 2), fill=fg)
        d.text((bar_x - text_width(d, left_lab, f12) - 5, bar_y - 1), left_lab, font=f12, fill=fg)
        d.text((bar_x + bar_w + 5, bar_y - 1), right_lab, font=f12, fill=fg)

        # footer: three or four things at a glance, each with its icon
        d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
        act_icon = "sleep" if activity and "sleep" in activity.lower() else "work" if activity else "free"
        foot = [(act_icon, activity or ("Day off" if not day.workday else "Free time"))]
        if ends is not None:
            foot.append(("clock", f"{_dur(ends - day.minute)} left"))
        if wx:
            from .. import weather as wx_mod
            foot.append((wx_mod.icon_name(wx["code"], wx["is_day"]), f"{round(wx['temp'])}°{wx['units']}"))
            if wx.get("days") and len(foot) < 4:
                foot.append(("sunset", wx["days"][0]["sunset"]))
        # fill any gap with something about you, not more dates
        birthday = (self.settings.get("birthday") or "").strip()
        spare = []
        if birthday:
            try:
                born = date.fromisoformat(birthday)
                nxt_bd = born.replace(year=now.year)
                if nxt_bd < now.date():
                    nxt_bd = born.replace(year=now.year + 1)
                to_bd = (nxt_bd - now.date()).days
                spare.append(("cake", "Birthday!" if to_bd == 0 else f"{to_bd} days"))
                spare.append(("cake", f"{int((now.date() - born).days // 365.2425)} years"))
            except ValueError:
                pass
        books = self._books()
        if books:
            spare.append(("book", f"{books} book{'' if books == 1 else 's'}"))
        if wx:
            spare.append(("humidity", f"{wx['humidity']}%"))
        spare.append(("free", (self.settings.get("name") or "You").split()[0]))
        while len(foot) < 3 and spare:
            foot.append(spare.pop(0))
        foot = foot[:4]

        widths = [ipx + 4 + text_width(d, t, f12) for _, t in foot]
        gap = (f.width - 6 - sum(widths)) / max(1, len(foot) - 1)
        x = 3
        for (icon, text), w in zip(foot, widths):
            draw_icon(f, icon, x, footer + 2, ipx)
            d.text((x + ipx + 4, footer + 1), text, font=f12, fill=fg)
            x += w + gap
        return f.image
