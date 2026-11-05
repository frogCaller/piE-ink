"""What a Pietato on your network has fetched already — the weather (now, the hours ahead and the
week), the US worded forecast, a coin's figures and its charts — used here rather than fetched again,
the way a Pietato uses what this Pi has.

A Pietato shares what it has fetched (GET /api/shared on its page: your network only, no password,
in the shape this Pi's own /api/shared has, with "from": "pietato"). It's asked only when something
here is due to be fetched (pie_ink/cache.py hands the key over first), at most once a minute (once
every ten when it isn't answering), and what it has is taken only when it's fresher than this Pi
keeps its own — with how old it really is, so nothing gets younger by going round, and when it's
too old, this Pi fetches it after all. Its weather is taken for a place within 25 km of yours (in
your units); a coin's figures go over the ones kept here (the longer changes — 7 days, 30 days, a
year — stay this Pi's, the Pietato doesn't keep those). Shares aren't taken: theirs are kept
another way.
"""
import logging
import math
import re
import threading
import time

import requests

from . import pitato

log = logging.getLogger(__name__)

ASK_EVERY = 60          # seconds an answer is used for before asking again
QUIET_FOR = 600         # ...and between tries when it isn't answering
SEEN_WITHIN = 300       # seconds: a Pietato heard from this lately is about
TIMEOUT = 3             # it's on your network: it answers quickly, or not at all
NEAR_KM = 25            # its weather is yours when it's for somewhere this close
WEATHER_KEY = re.compile(r"weather_(-?\d+\.\d{3})_(-?\d+\.\d{3})_([FC])$")
NWS_KEY = re.compile(r"nws_(-?\d+\.\d{3})_(-?\d+\.\d{3})$")
COIN_FIGURES = ("price", "ath", "market_cap", "rank", "circulating_supply", "sentiment_up", "high_24h", "low_24h")

_lock = threading.Lock()
_asking = threading.Lock()
_answers = {}           # ip -> {"at": when asked, "got": its answer or None}


def _num(v, lo=-1e18, hi=1e18):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi else None


def _pietatos():
    """The Pietatos about whose page answers (that's where it shares), the latest heard first."""
    now = time.time()
    try:
        return [p for p in pitato.PITATO.around() if p.get("web") and now - p.get("last", 0) <= SEEN_WITHIN]
    except Exception:
        return []


def _answer(p):
    """(what it has fetched, how long ago it said so) — or None."""
    ip, now = p["ip"], time.time()
    with _lock:
        a = _answers.get(ip)
    if a and now - a["at"] < (ASK_EVERY if a["got"] is not None else QUIET_FOR):
        return (a["got"], now - a["at"]) if a["got"] is not None else None
    if not _asking.acquire(timeout=TIMEOUT + 1):
        return None
    try:
        with _lock:
            a = _answers.get(ip)
        if a and time.time() - a["at"] < ASK_EVERY:            # (asked while this one waited)
            return (a["got"], time.time() - a["at"]) if a["got"] is not None else None
        got = None
        try:
            r = requests.get(f"http://{ip}:{pitato.PORT}/api/shared", timeout=TIMEOUT,
                             headers={"Accept": "application/json", "User-Agent": "pie-ink"})
            d = r.json() if r.status_code == 200 else None
            if isinstance(d, dict) and d.get("ok") is True and d.get("from") == "pietato":
                got = d
        except Exception as e:
            log.debug("nothing shared by the Pietato at %s: %s", ip, e)
        with _lock:
            _answers[ip] = {"at": time.time(), "got": got}
        return (got, 0.0) if got is not None else None
    finally:
        _asking.release()


def _answers_now():
    """Every Pietato's answer about: [(answer, seconds since)], the latest heard first."""
    out = []
    for p in _pietatos():
        a = _answer(p)
        if a:
            out.append(a)
    return out


def _km(a, b):
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def _near(w, lat, lon):
    wl, wo = _num(w.get("lat"), -90, 90), _num(w.get("lon"), -180, 180)
    return wl is not None and wo is not None and _km((wl, wo), (lat, lon)) <= NEAR_KM


def _weather_data(d, have, want):
    """A Pietato's forecast, checked over, in this Pi's shape and units; None when it isn't one."""
    def deg(v):
        v = _num(v, -130, 140)
        if v is None or have == want:
            return v
        return round((v - 32) * 5 / 9 if want == "C" else v * 9 / 5 + 32, 1)

    def speed(v):
        v = _num(v, 0, 500)
        if v is None or have == want:
            return v
        return round(v * 1.609344 if want == "C" else v / 1.609344, 1)

    def code(v):
        v = _num(v, 0, 99)
        return None if v is None else int(v)

    def pct(v):
        v = _num(v, 0, 100)
        return None if v is None else int(v)

    def hhmm(v):
        return v if isinstance(v, str) and re.fullmatch(r"\d\d:\d\d", v) else ""
    if not isinstance(d, dict) or deg(d.get("temp")) is None:
        return None
    days = []
    for x in d.get("days")[:16] if isinstance(d.get("days"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("date"), str) and re.fullmatch(r"\d{4}-\d\d-\d\d", x["date"]):
            days.append({"date": x["date"], "code": code(x.get("code")), "hi": deg(x.get("hi")), "lo": deg(x.get("lo")),
                         "rain": pct(x.get("rain")), "sunrise": hhmm(x.get("sunrise")), "sunset": hhmm(x.get("sunset"))})
    hours = []
    for x in d.get("hours")[:48] if isinstance(d.get("hours"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("time"), str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", x["time"]):
            hours.append({"time": x["time"], "temp": deg(x.get("temp")), "code": code(x.get("code")),
                          "rain": pct(x.get("rain")), "is_day": x.get("is_day") is not False})
    return {"temp": deg(d.get("temp")), "feels": deg(d.get("feels")), "humidity": pct(d.get("humidity")),
            "wind": speed(d.get("wind")), "wind_dir": _num(d.get("wind_dir"), 0, 360), "code": code(d.get("code")),
            "is_day": d.get("is_day") is not False, "precip": None, "units": want, "hours": hours, "days": days,
            "tz": None, "from": "pietato"}


def _weather(m, ttl):
    lat, lon, units = float(m.group(1)), float(m.group(2)), m.group(3)
    best = None
    for got, since in _answers_now():
        w = got.get("weather")
        if not isinstance(w, dict) or not _near(w, lat, lon):
            continue
        age = _num(w.get("age"), 0)
        have = (w.get("data") or {}).get("units") if isinstance(w.get("data"), dict) else None
        have = have if have in ("F", "C") else w.get("units")
        if age is None or have not in ("F", "C") or age + since >= ttl:
            continue
        data = _weather_data(w.get("data"), have, units)
        if data and (best is None or age + since < best[1]):
            best = (data, age + since)
    return best


def _nws(m, ttl):
    lat, lon = float(m.group(1)), float(m.group(2))
    for got, since in _answers_now():
        w = got.get("weather")
        if not isinstance(w, dict) or not _near(w, lat, lon):
            continue
        age = _num(w.get("nws_age"), 0)
        nws = [[str(n[0])[:40], str(n[1])[:600]] for n in (w.get("nws") or [])[:4]
               if isinstance(n, (list, tuple)) and len(n) >= 2 and isinstance(n[0], str) and isinstance(n[1], str)]
        if age is not None and nws and age + since < ttl:
            return nws, age + since
    return None


def _coin_of(got, cid):
    for x in got.get("coins") or [] if isinstance(got.get("coins"), list) else []:
        if isinstance(x, dict) and x.get("id") == cid:
            return x
    return None


def _coin(cid, ttl, stale):
    """Its figures for a coin, over the ones kept here (the changes over 7 days and longer stay these)."""
    best = None
    for got, since in _answers_now():
        x = _coin_of(got, cid)
        d = x.get("data") if x else None
        age = _num(x.get("age"), 0) if x else None
        if not isinstance(d, dict) or age is None or age + since >= ttl or _num(d.get("price"), 0) is None:
            continue
        if best is None or age + since < best[1]:
            best = (d, age + since)
    if best is None:
        return None
    d, age = best
    out = dict(stale) if isinstance(stale, dict) else {"price": None, "ath": None, "ath_date": "", "from_ath": None,
                                                         "market_cap": None, "rank": None, "total_supply": None,
                                                         "circulating_supply": None, "sentiment_up": None,
                                                         "high_24h": None, "low_24h": None,
                                                         "change": {"24h": None, "7d": None, "30d": None, "200d": None,
                                                                    "1y": None}}
    out.pop("seed", None)
    for k in COIN_FIGURES:
        v = _num(d.get(k), 0)
        if v is not None:
            out[k] = int(v) if k == "rank" else v
    change = dict(out.get("change") or {})
    ch = _num((d.get("change") or {}).get("24h"), -100, 1e6) if isinstance(d.get("change"), dict) else None
    if ch is not None:
        change["24h"] = ch
    out["change"] = change
    if out.get("price") and out.get("ath"):
        out["from_ath"] = (out["price"] - out["ath"]) / out["ath"] * 100
    return out, age


def _hist(rest, ttl):
    """Its chart of a coin over so many days (one of the windows it keeps)."""
    cid, _, days = rest.rpartition("_")
    if not cid or not days.isdigit():
        return None
    days = int(days)
    best = None
    for got, since in _answers_now():
        x = _coin_of(got, cid)
        if not x:
            continue
        charts = [g for g in x.get("graphs") or [] if isinstance(g, dict)] if isinstance(x.get("graphs"), list) else []
        if x.get("graph_days") == days:
            charts.append({"days": days, "v": x.get("graph"), "age": x.get("graph_age")})
        for g in charts:
            age, v = _num(g.get("age"), 0), g.get("v")
            if g.get("days") != days or age is None or age + since >= ttl or not isinstance(v, list) or len(v) < 2:
                continue
            prices = [_num(p, 0) for p in v[:5000]]
            if None not in prices and (best is None or age + since < best[1]):
                best = (prices, age + since)
    return best


def lend(key, ttl, stale=None):
    """What a Pietato about has for a cache key that's due to be fetched here, fresher than `ttl`:
    (it, how old it really is), or None (then it's fetched as ever). Never raises."""
    try:
        if not key.startswith(("weather_", "nws_", "coin_", "hist_")) or not _pietatos():
            return None
        m = WEATHER_KEY.match(key)
        if m:
            return _weather(m, ttl)
        m = NWS_KEY.match(key)
        if m:
            return _nws(m, ttl)
        if key.startswith("coin_"):
            return _coin(key[5:], ttl, stale)
        return _hist(key[5:], ttl)
    except Exception:
        log.debug("borrowing %s", key, exc_info=True)
        return None

