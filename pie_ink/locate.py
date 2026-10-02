"""Where the Pi is, without being told.

The GPS receiver says exactly, when there is one with a fix. Without one, the
Pi's internet address says roughly — the town, most of the time, which is
all the weather needs. Asked once when it starts with no location set (and
again at each start while the location is the network's own, in case the Pi
has moved house), and whenever you press **Find me**. A location you typed
in is never replaced by the network's guess; a GPS fix replaces either, if
you let it (the GPS's *Move the weather to wherever it is* switch).
"""
import logging
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


def distance_km(a, b):
    from .gps import distance_km as d
    return d(a, b)
