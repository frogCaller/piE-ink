"""Where the Pi is, without being told.

The Pi's internet address says roughly — the town, most of the time, which
is all the weather needs. Asked once when it starts with no location set
(and again at each start while the location is the network's own, in case
the Pi has moved house), and whenever you press **Find me**. A location you
typed in is never replaced by the network's guess.
"""
import logging
import math
import time

from . import cache

log = logging.getLogger(__name__)

UA = "pie-ink/3.0 (https://github.com/frogCaller/piE-ink)"
NETWORK_TTL = 12 * 3600      # the network's answer is kept this long; the address rarely moves
MOVED_KM = 25.0              # the network's guess has to move this far before the weather follows it

# a few free lookups, tried in turn — each gives a town and a rough position for the address asking
SERVICES = (
    ("https://ipapi.co/json/", lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"), d.get("region"), d.get("country_code"))),
    ("https://ipwho.is/", lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"), d.get("region"), d.get("country_code"))
     if d.get("success", True) else (None, None, None, None, None)),
    ("http://ip-api.com/json/?fields=status,lat,lon,city,regionName,countryCode",
     lambda d: (d.get("lat"), d.get("lon"), d.get("city"), d.get("regionName"), d.get("countryCode"))
     if d.get("status") == "success" else (None, None, None, None, None)),
)


def from_network(cached_only=False):
    """{"lat", "lon", "place", "country", "at"} from the Pi's internet address, or None.
    Cached for a while (data/cache); with cached_only, only what is already known."""
    def fetch():
        import requests
        last = None
        for url, pick in SERVICES:
            try:
                r = requests.get(url, timeout=6, headers={"User-Agent": UA})
                r.raise_for_status()
                lat, lon, town, region, country = pick(r.json())
                if lat is None or lon is None:
                    raise ValueError("no position in the answer")
                place = ", ".join(x for x in (town, region) if x)
                out = {"lat": round(float(lat), 4), "lon": round(float(lon), 4), "place": place or f"{float(lat):.3f}, {float(lon):.3f}",
                       "country": (country or "").upper(), "at": time.time()}
                log.info("locate: the network puts this Pi near %s", out["place"])
                return out
            except Exception as e:
                last = e
                log.info("locate: %s didn't answer (%s)", url.split("/")[2], str(e)[:80])
        raise RuntimeError(f"no lookup answered ({last})")
    return cache.cached("ip_location", NETWORK_TTL, fetch, cached_only)


def where(config):
    """The position we have: the weather location, as {lat, lon, place,
    typed}. None when none is set."""
    w = (config or {}).get("weather", {})
    if w.get("lat") is not None and w.get("lon") is not None:
        return {"lat": float(w["lat"]), "lon": float(w["lon"]), "place": w.get("location") or "",
                "typed": w.get("source") == "typed"}
    return None


def distance_km(a, b):
    """Great-circle distance between two (lat, lon) pairs."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
