"""Where the Pi is, on a map.

The position comes from the USB GPS when it has a fix (or its last one), and
from the weather location otherwise. The web page draws its map itself; the
e-ink and the LCD get one drawn here from OpenStreetMap tiles, which the Pi
fetches a few at a time and keeps for a week under data/cache/tiles, so a
view it has shown before costs nothing and works without the network.

Tiles are (c) OpenStreetMap contributors, used under the ODbL, from the
public tile server under its usage policy: an identifying User-Agent, a
handful of tiles per view, cached.
"""
import logging
import math
import os
import threading
import time

import requests
from PIL import Image, ImageFilter, ImageOps

from . import __version__
from .settings import DATA_DIR

log = logging.getLogger(__name__)

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_DIR = os.path.join(DATA_DIR, "cache", "tiles")
TILE_TTL = 7 * 86400
USER_AGENT = f"PiE-ink/{__version__} (+https://github.com/frogCaller/piE-ink)"
TILE = 256
MAX_TILES = 12                     # per view; a 250x122 panel at any zoom needs at most 6

_fetching = set()
_fetch_lock = threading.Lock()
_failed_at = {}                    # tile key -> when it last failed, so a dead network isn't hammered


# -- where we are ------------------------------------------------------------------------------

def where(config):
    """The best position we have: the GPS's (current or last) fix, else the
    weather location. None if there is neither."""
    from .gps import GPS
    g = GPS.status()
    if g.get("enabled") and g.get("lat") is not None and g.get("lon") is not None:
        fresh = g.get("fix") != "none"
        return {"lat": float(g["lat"]), "lon": float(g["lon"]), "source": "gps" if fresh else "gps_last",
                "place": g.get("place") or "", "fix": g.get("fix"), "fix_age": g.get("fix_age"),
                "sats": g.get("sats_used", 0), "alt_m": g.get("alt_m"), "speed_kmh": g.get("speed_kmh"),
                "course": g.get("course"), "connected": bool(g.get("connected"))}
    w = (config or {}).get("weather", {})
    if w.get("lat") is not None and w.get("lon") is not None:
        return {"lat": float(w["lat"]), "lon": float(w["lon"]), "source": "weather", "place": w.get("location") or "",
                "fix": "none", "fix_age": None, "sats": 0, "alt_m": None, "speed_kmh": None, "course": None,
                "connected": bool(g.get("connected")) if g.get("enabled") else False}
    return None


def source_words(pos):
    """A short description of where the position came from."""
    if not pos:
        return "no position"
    return {"gps": f"GPS {pos.get('fix') or ''} fix · {pos.get('sats', 0)} sats".replace("  ", " "),
            "gps_last": "GPS · last fix", "weather": "weather location"}.get(pos["source"], pos["source"])


# -- web mercator --------------------------------------------------------------------------------

def to_world(lat, lon, zoom):
    """Pixel coordinates in the world map at this zoom."""
    n = 2 ** zoom * TILE
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def metres_per_pixel(lat, zoom):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


# -- tiles ---------------------------------------------------------------------------------------

def _tile_path(z, x, y):
    return os.path.join(TILE_DIR, str(z), str(x), f"{y}.png")


def tile_cached(z, x, y):
    """The tile from the cache (an Image), or None. Stale tiles still count:
    a week-old map is a map."""
    p = _tile_path(z, x, y)
    try:
        img = Image.open(p)
        img.load()
        return img.convert("RGB")
    except (OSError, ValueError):
        return None


def tile_fresh(z, x, y):
    try:
        return time.time() - os.path.getmtime(_tile_path(z, x, y)) < TILE_TTL
    except OSError:
        return False


def fetch_tile(z, x, y):
    """One tile from OpenStreetMap into the cache. True if it is there now."""
    key = (z, x, y)
    if time.time() - _failed_at.get(key, 0) < 120:
        return False
    n = 2 ** z
    x %= n
    if not 0 <= y < n:
        return False
    try:
        r = requests.get(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT}, timeout=12)
        r.raise_for_status()
        p = _tile_path(z, x, y)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".part", "wb") as fh:
            fh.write(r.content)
        os.replace(p + ".part", p)
        return True
    except Exception as e:
        _failed_at[key] = time.time()
        log.info("map tile %s/%s/%s: %s", z, x, y, str(e)[:100])
        return False


def tiles_for(lat, lon, zoom, size):
    """The tiles a view of `size` centred on lat/lon needs, with where each
    one lands: [(z, x, y, px, py)]."""
    w, h = size
    cx, cy = to_world(lat, lon, zoom)
    left, top = cx - w / 2.0, cy - h / 2.0
    n = 2 ** zoom
    out = []
    for ty in range(int(math.floor(top / TILE)), int(math.floor((top + h - 1) / TILE)) + 1):
        for tx in range(int(math.floor(left / TILE)), int(math.floor((left + w - 1) / TILE)) + 1):
            if not 0 <= ty < n:
                continue
            out.append((zoom, tx % n, ty, int(round(tx * TILE - left)), int(round(ty * TILE - top))))
    return out[:MAX_TILES]


def compose(lat, lon, zoom, size, cached_only=True):
    """The map for a view, in colour: (image, missing). Tiles not in the
    cache are left as blank paper and counted; with cached_only False they
    are fetched first (slow — only from a background thread)."""
    w, h = size
    img = Image.new("RGB", (w, h), (242, 239, 233))
    missing = 0
    for z, x, y, px, py in tiles_for(lat, lon, zoom, size):
        t = tile_cached(z, x, y)
        if t is None and not cached_only and fetch_tile(z, x, y):
            t = tile_cached(z, x, y)
        if t is None:
            missing += 1
            continue
        img.paste(t, (px, py))
    return img, missing


def fetch_async(lat, lon, zoom, size, on_done=None):
    """Fetch what a view is missing, once, in the background."""
    key = (round(lat, 3), round(lon, 3), zoom, tuple(size))
    with _fetch_lock:
        if key in _fetching:
            return False
        _fetching.add(key)

    def go():
        try:
            for z, x, y, _, _ in tiles_for(lat, lon, zoom, size):
                if tile_cached(z, x, y) is None or not tile_fresh(z, x, y):
                    fetch_tile(z, x, y)
            if on_done:
                on_done()
        finally:
            with _fetch_lock:
                _fetching.discard(key)
    threading.Thread(target=go, daemon=True, name="map-tiles").start()
    return True


def cache_status():
    count, size = 0, 0
    for root, _, files in os.walk(TILE_DIR):
        for f in files:
            if f.endswith(".png"):
                count += 1
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return {"tiles": count, "bytes": size}


# -- drawing on it -------------------------------------------------------------------------------

def pixel_of(lat, lon, centre, zoom, size):
    """Where a position lands in a view centred on `centre`."""
    cx, cy = to_world(centre[0], centre[1], zoom)
    x, y = to_world(lat, lon, zoom)
    return int(round(x - cx + size[0] / 2.0)), int(round(y - cy + size[1] / 2.0))


def scale_bar(lat, zoom, max_px=70):
    """(pixels, label) for a round distance that fits in max_px."""
    mpp = metres_per_pixel(lat, zoom)
    for metres in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000):
        px = metres / mpp
        if px > max_px:
            break
        best = (int(round(px)), f"{metres // 1000} km" if metres >= 1000 else f"{metres} m")
    else:
        best = (int(round(100000 / mpp)), "100 km")
    return best


def mono(img, style="lines"):
    """A colour map as a 1-bit-friendly greyscale image for e-ink: "lines"
    traces the edges (roads, buildings, water's edge — a line map);
    "shaded" keeps the tones as a dither."""
    grey = ImageOps.grayscale(img)
    if style == "shaded":
        grey = ImageOps.autocontrast(grey, cutoff=1)
        return grey.convert("1").convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    edges = ImageOps.autocontrast(edges, cutoff=1)
    lines = edges.point(lambda v: 0 if v > 48 else 255)         # black lines on white
    # the water and the parks keep a light dot pattern so they read as areas
    return lines


def draw_trail(d, points, centre, zoom, size, top=0, fg=0, bg=255, width=2):
    """The day's track on a view: a line with a light edge so it reads over
    the map, and a small ring where it started. `points` are the journey's
    [ts, lat, lon, ...]; whatever falls outside the view is left out."""
    w, h = size
    margin = 40
    xy = []
    for p in points:
        x, y = pixel_of(p[1], p[2], centre, zoom, size)
        xy.append((x, top + y, -margin <= x < w + margin and -margin <= y < h + margin))
    if len(xy) < 2:
        return
    segments = [((a[0], a[1]), (b[0], b[1])) for a, b in zip(xy, xy[1:]) if a[2] or b[2]]
    for a, b in segments:
        d.line([a, b], fill=bg, width=width + 2)
    for a, b in segments:
        d.line([a, b], fill=fg, width=width)
    sx, sy, inside = xy[0]
    if inside:
        d.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=bg, outline=fg, width=2)


def draw_pin(d, x, y, fg, bg, r=5):
    """A target: a ring and a dot."""
    d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=bg)
    d.ellipse((x - r, y - r, x + r, y + r), outline=fg, width=2)
    d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=fg)


def draw_friend(d, x, y, initial, fnt, fg, bg, r=6):
    d.ellipse((x - r, y - r, x + r, y + r), fill=bg, outline=fg, width=1)
    if initial:
        try:
            tw = d.textlength(initial, font=fnt)
        except AttributeError:
            tw = r
        d.text((x - tw / 2, y - r + 1), initial, font=fnt, fill=fg)


def friends_positions(peers):
    """[(host, lat, lon)] for the PiE-inks that told us where they are."""
    out = []
    for p in peers or []:
        wh = p.get("where") or {}
        if wh.get("lat") is not None and wh.get("lon") is not None:
            out.append((p.get("host") or "?", float(wh["lat"]), float(wh["lon"])))
    return out
