"""Where it has been: the day's track from the GPS.

While the receiver has a fix, a point is kept every time the Pi has moved a
little way (twenty metres, by default) — so a drive is a trail of points and
an afternoon on the desk is one point. Each day goes in its own file under
data/journey, kept for a month. The Map tab draws the trail, the e-ink and
the LCD map can too, the bot can say where you have been, and /journey in
Telegram sends the day as a picture.
"""
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta

from .settings import DATA_DIR

log = logging.getLogger(__name__)

JOURNEY_DIR = os.path.join(DATA_DIR, "journey")
STOP_MINUTES = 5            # a gap this long between two points is a stop
SAVE_EVERY = 30             # seconds between writes while moving
MAX_POINTS = 5000           # a day at 20 m a point is nowhere near this


def distance_m(a, b):
    """Metres between two (lat, lon) pairs."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


def today():
    return datetime.now().strftime("%Y-%m-%d")


def _path(day):
    return os.path.join(JOURNEY_DIR, f"{day}.json")


class Journey:
    def __init__(self):
        self._lock = threading.Lock()
        self.conf = {}
        self._day = None
        self._points = []           # today's: [ts, lat, lon, speed_kmh, alt_m]
        self._dirty = False
        self._saved_at = 0.0
        self.last_point_at = 0.0

    def configure(self, conf):
        self.conf = dict(conf or {})

    def enabled(self):
        return bool(self.conf.get("enabled", True))

    def _min_metres(self):
        try:
            return max(5.0, float(self.conf.get("min_metres", 20) or 20))
        except (TypeError, ValueError):
            return 20.0

    # -- recording ---------------------------------------------------------------------------

    def _load_day(self, day):
        try:
            with open(_path(day)) as fh:
                pts = json.load(fh).get("points") or []
            return [p for p in pts if isinstance(p, list) and len(p) >= 3][-MAX_POINTS:]
        except (OSError, ValueError):
            return []

    def _open_day(self, day):
        """Switch the in-memory track to a day (loading what is on disk). Called with the lock held."""
        self._flush_locked()
        self._day = day
        self._points = self._load_day(day)
        self._prune()

    def record(self, state, now=None):
        """A fix from the receiver. Keeps a point when it has moved."""
        if not self.enabled():
            return False
        lat, lon = state.get("lat"), state.get("lon")
        if lat is None or lon is None:
            return False
        now = now or time.time()
        with self._lock:
            day = today()
            if day != self._day:
                self._open_day(day)
            last = self._points[-1] if self._points else None
            if last is not None:
                jump = distance_m((last[1], last[2]), (lat, lon))
                if jump < self._min_metres():
                    return False
                gap = max(1.0, now - last[0])
                if gap < 3600 and jump / gap * 3.6 > 1000:      # a receiver glitch, not a rocket
                    return False
            speed = state.get("speed_kmh")
            alt = state.get("alt_m")
            self._points.append([int(now), round(float(lat), 5), round(float(lon), 5),
                                 round(float(speed), 1) if speed is not None else None,
                                 round(float(alt)) if alt is not None else None])
            del self._points[:-MAX_POINTS]
            self._dirty = True
            self.last_point_at = now
            if now - self._saved_at > SAVE_EVERY:
                self._flush_locked()
        return True

    def flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self._dirty or not self._day:
            return
        try:
            os.makedirs(JOURNEY_DIR, exist_ok=True)
            tmp = _path(self._day) + ".part"
            with open(tmp, "w") as fh:
                json.dump({"points": self._points}, fh)
            os.replace(tmp, _path(self._day))
            self._dirty = False
            self._saved_at = time.time()
        except OSError as e:
            log.debug("couldn't save the journey: %s", e)

    def _prune(self):
        try:
            keep = int(self.conf.get("keep_days", 30) or 30)
        except (TypeError, ValueError):
            keep = 30
        cutoff = (datetime.now() - timedelta(days=keep)).strftime("%Y-%m-%d")
        for day in self._days_on_disk():
            if day < cutoff:
                try:
                    os.remove(_path(day))
                except OSError:
                    pass

    # -- reading -----------------------------------------------------------------------------

    @staticmethod
    def _days_on_disk():
        try:
            return sorted(f[:-5] for f in os.listdir(JOURNEY_DIR) if f.endswith(".json") and len(f) == 15)
        except OSError:
            return []

    def days(self):
        """The days there is a track for, oldest first."""
        names = self._days_on_disk()
        with self._lock:
            if self._day and self._points and self._day not in names:
                names.append(self._day)
        return sorted(names)

    def points(self, day=None):
        day = day or today()
        with self._lock:
            if day == self._day:
                return [list(p) for p in self._points]
        return self._load_day(day)

    def clear(self, day=None):
        day = day or today()
        with self._lock:
            if day == self._day:
                self._points, self._dirty = [], False
            try:
                os.remove(_path(day))
            except OSError:
                pass

    def status(self):
        with self._lock:
            n = len(self._points) if self._day == today() else 0
        return {"enabled": self.enabled(), "today_points": n, "days": len(self.days()),
                "last_point_ago": int(time.time() - self.last_point_at) if self.last_point_at else None}


JOURNEY = Journey()


# -- making sense of a track ------------------------------------------------------------------

def stops(points, min_minutes=STOP_MINUTES):
    """Where it stayed a while: [{lat, lon, ts, minutes}] — a gap between two
    points means it didn't move between them."""
    out = []
    for a, b in zip(points, points[1:]):
        gap = (b[0] - a[0]) / 60.0
        if gap >= min_minutes:
            out.append({"lat": a[1], "lon": a[2], "ts": a[0], "minutes": int(round(gap))})
    return out


def stats(points):
    """Distance, times, speeds and stops for a day's points."""
    if not points:
        return {"points": 0}
    dist = 0.0
    moving = 0.0
    top = 0.0
    furthest = 0.0
    first = (points[0][1], points[0][2])
    for a, b in zip(points, points[1:]):
        d = distance_m((a[1], a[2]), (b[1], b[2]))
        gap = b[0] - a[0]
        if gap > 0 and d / gap * 3.6 > 400:              # a glitch (or a flight): not part of the distance
            continue
        dist += d
        if 0 < gap < STOP_MINUTES * 60:
            moving += gap
            if gap >= 5:
                top = max(top, d / gap * 3.6)           # a sanity-checked speed from the track itself
    for p in points:
        if p[3] is not None:
            top = max(top, float(p[3]))
        furthest = max(furthest, distance_m(first, (p[1], p[2])))
    lats = [p[1] for p in points]
    lons = [p[2] for p in points]
    return {"points": len(points), "distance_km": round(dist / 1000.0, 2), "moving_minutes": int(moving // 60),
            "top_kmh": round(min(top, 400.0), 1), "furthest_km": round(furthest / 1000.0, 2),
            "start": points[0][0], "end": points[-1][0], "stops": stops(points),
            "bounds": [min(lats), min(lons), max(lats), max(lons)],
            "home_km": round(distance_m(first, (points[-1][1], points[-1][2])) / 1000.0, 2)}


def simplify(points, most=600):
    """Fewer points for the page: every n-th, keeping the ends and stops."""
    if len(points) <= most:
        return points
    step = max(2, int(math.ceil(len(points) / float(most))))
    keep = {0, len(points) - 1}
    for a, b in zip(range(len(points)), range(1, len(points))):
        if points[b][0] - points[a][0] >= STOP_MINUTES * 60:
            keep.update((a, b))
    return [p for i, p in enumerate(points) if i % step == 0 or i in keep]


def _clock(ts):
    return time.strftime("%-I:%M %p", time.localtime(ts))


def summary(day=None, units="F", points=None):
    """The day in a sentence, for the bot and Telegram."""
    pts = points if points is not None else JOURNEY.points(day)
    if not pts:
        return "" if day and day != today() else ""
    st = stats(pts)
    miles = units == "F"
    dist = f"{st['distance_km'] * 0.621:.1f} miles" if miles else f"{st['distance_km']:.1f} km"
    top = f"{st['top_kmh'] * 0.621:.0f} mph" if miles else f"{st['top_kmh']:.0f} km/h"
    far = f"{st['furthest_km'] * 0.621:.1f} miles" if miles else f"{st['furthest_km']:.1f} km"
    when = "Today" if not day or day == today() else datetime.strptime(day, "%Y-%m-%d").strftime("%A %-d %B")
    if st["points"] < 2 or st["distance_km"] < 0.05:
        return f"{when}: it hasn't gone anywhere — one spot since {_clock(st['start'])}."
    bits = [f"{when}: set off at {_clock(st['start'])}", f"{dist} in all",
            f"{st['moving_minutes']} min on the move"]
    if st["stops"]:
        longest = max(st["stops"], key=lambda s: s["minutes"])
        bits.append(f"{len(st['stops'])} stop{'s' if len(st['stops']) != 1 else ''} (longest {longest['minutes']} min at {_clock(longest['ts'])})")
    if st["top_kmh"] >= 3:
        bits.append(f"top speed {top}")
    bits.append(f"furthest {far} from the start")
    back = st["home_km"] < 0.15
    bits.append(f"back where it started by {_clock(st['end'])}" if back else f"last point {_clock(st['end'])}")
    return ", ".join(bits) + "."
