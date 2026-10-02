"""Photos: the Camera tab's shutter button, with a filter, kept on the Pi.

A photo is the camera's biggest picture (up to 1920x1080 — the capture loop
switches size for one frame and back), turned and mirrored the way the live
view shows it, with a filter on top. The colour filters are the colour
matrices a browser's CSS filters use, so the live view shows them exactly
as the photo comes out; the drawn ones (sketch, comic, pixel, e-ink) are
worked out here, and the live view streams them from here too.

They're kept in data/photos as JPEGs, newest first, with small copies for
the page's strip. "Take a picture" in words does the same.
"""
import io
import logging
import math
import os
import re
import threading
import time

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from .settings import DATA_DIR

log = logging.getLogger(__name__)

DIR = os.path.join(DATA_DIR, "photos")
THUMBS = os.path.join(DIR, "thumbs")
THUMB = 480                      # the strip's copies, this wide at most
_NAME = re.compile(r"^[0-9A-Za-z_-]{1,80}\.jpg$")
_PARTS = re.compile(r"^(\d{8})-(\d{6})(?:-\d+)?(?:-([a-z]+))?\.jpg$")
_lock = threading.Lock()

# id, label, and for a colour filter the CSS filter functions in order (the page builds the same
# string for the live view). A drawn one has css None: the page streams it from here.
FILTERS = [
    {"id": "none", "label": "Original", "css": []},
    {"id": "bw", "label": "B&W", "css": [["grayscale", 1], ["contrast", 1.15]]},
    {"id": "sepia", "label": "Sepia", "css": [["sepia", 0.9], ["contrast", 1.05]]},
    {"id": "warm", "label": "Warm", "css": [["sepia", 0.3], ["saturate", 1.35], ["contrast", 1.05]]},
    {"id": "vivid", "label": "Vivid", "css": [["saturate", 1.8], ["contrast", 1.15]]},
    {"id": "blue", "label": "Blue", "css": [["grayscale", 1], ["sepia", 0.9], ["hue-rotate", 175], ["saturate", 1.6]]},
    {"id": "sketch", "label": "Sketch", "css": None},
    {"id": "trace", "label": "Edge trace", "css": None},
    {"id": "comic", "label": "Comic", "css": None},
    {"id": "pixel", "label": "Pixel", "css": None},
    {"id": "eink", "label": "E-ink", "css": None},
]
BY_ID = {f["id"]: f for f in FILTERS}


# -- the colour filters: CSS's own matrices (Filter Effects, level 1) -----------------------------

def _matrix(fn, v):
    """One CSS filter function as a 3x3 matrix and an offset (0–255)."""
    if fn in ("grayscale", "sepia"):
        a = 1 - max(0.0, min(1.0, v))
        if fn == "grayscale":
            return ((0.2126 + 0.7874 * a, 0.7152 - 0.7152 * a, 0.0722 - 0.0722 * a),
                    (0.2126 - 0.2126 * a, 0.7152 + 0.2848 * a, 0.0722 - 0.0722 * a),
                    (0.2126 - 0.2126 * a, 0.7152 - 0.7152 * a, 0.0722 + 0.9278 * a)), 0.0
        return ((0.393 + 0.607 * a, 0.769 - 0.769 * a, 0.189 - 0.189 * a),
                (0.349 - 0.349 * a, 0.686 + 0.314 * a, 0.168 - 0.168 * a),
                (0.272 - 0.272 * a, 0.534 - 0.534 * a, 0.131 + 0.869 * a)), 0.0
    if fn == "saturate":
        s = max(0.0, v)
        return ((0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s),
                (0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s),
                (0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s)), 0.0
    if fn == "hue-rotate":
        c, s = math.cos(math.radians(v)), math.sin(math.radians(v))
        return ((0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928),
                (0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283),
                (0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072)), 0.0
    if fn == "brightness":
        return ((v, 0, 0), (0, v, 0), (0, 0, v)), 0.0
    if fn == "contrast":
        return ((v, 0, 0), (0, v, 0), (0, 0, v)), 127.5 * (1 - v)
    return ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.0


def _colour(img, chain):
    """The functions one after the other, each clamped, as a browser does."""
    for fn, v in chain:
        m, off = _matrix(fn, float(v))
        img = img.convert("RGB", (m[0][0], m[0][1], m[0][2], off,
                                  m[1][0], m[1][1], m[1][2], off,
                                  m[2][0], m[2][1], m[2][2], off))
    return img


def css(fid):
    """The CSS filter string for a colour filter ("" for none, None for a drawn one)."""
    chain = (BY_ID.get(fid) or BY_ID["none"])["css"]
    if chain is None:
        return None
    return " ".join(f"{fn}({v}deg)" if fn == "hue-rotate" else f"{fn}({v})" for fn, v in chain)


# -- the drawn ones ---------------------------------------------------------------------------

def _working(img, most=960):
    """Drawn filters work at a modest size (the same look on a big photo and
    the live view, and quick on a Pi Zero), then go back up to the photo's."""
    if img.width <= most:
        return img, None
    return img.resize((most, max(1, round(img.height * most / img.width))), Image.LANCZOS), img.size


def _sketch(img):
    work, size = _working(img)
    g = work.convert("L")
    blur = g.filter(ImageFilter.GaussianBlur(max(2.0, g.width / 80)))
    try:
        import numpy as np
        a, b = np.asarray(g, dtype=np.float32), np.asarray(blur, dtype=np.float32)
        dodge = np.clip(a * 255.0 / np.maximum(b, 1.0), 0, 255)        # pencil on paper: the colour dodge
        out = 255 - np.clip((255 - dodge) * 1.8, 0, 255)                # and a firmer hand
        s = Image.fromarray(out.astype("uint8"), "L")
    except ImportError:
        s = ImageOps.autocontrast(ImageOps.invert(g.filter(ImageFilter.FIND_EDGES)))
    s = ImageOps.colorize(s, black=(46, 42, 50), white=(248, 246, 238))
    return s.resize(size, Image.LANCZOS) if size else s


def _trace(img):
    """The edges traced, bright on black: how sharply the brightness changes
    at each point (a Sobel filter), the faint texture left dark."""
    work, size = _working(img)
    g = work.convert("L").filter(ImageFilter.GaussianBlur(max(0.8, work.width / 700)))
    try:
        import numpy as np
        p = np.pad(np.asarray(g, dtype=np.float32), 1, mode="edge")
        gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
        gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
        mag = np.hypot(gx, gy)
        top = max(float(np.percentile(mag, 98)), 90.0)      # a blank wall's grain stays dark, not static
        out = Image.fromarray((np.clip(mag / top, 0, 1) ** 1.3 * 255).astype("uint8"), "L")
    except ImportError:
        out = ImageOps.autocontrast(g.filter(ImageFilter.FIND_EDGES), cutoff=2)
    out = out.convert("RGB")
    return out.resize(size, Image.LANCZOS) if size else out


def _comic(img):
    """Flat colours with ink lines: a pixel darker than the ones around it is ink."""
    work, size = _working(img, most=800)
    smooth = work.filter(ImageFilter.MedianFilter(5))
    g = smooth.convert("L")
    around = g.filter(ImageFilter.BoxBlur(max(3, work.width // 110)))
    ink = ImageChops.subtract(around, g).point(lambda v: 0 if v > 10 else 255)
    ink = ink.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))   # specks out, lines kept
    base = ImageEnhance.Color(ImageOps.posterize(smooth, 3)).enhance(1.4)
    out = ImageChops.multiply(base, Image.merge("RGB", (ink, ink, ink)))
    return out.resize(size, Image.LANCZOS) if size else out


def _blocks(img, across):
    """(block size, blocks across, blocks down): whole blocks that cover the picture about as it is."""
    w, h = img.size
    block = max(1, round(w / max(8, min(across, w))))
    across = max(1, w // block)
    return block, across, max(1, round(h * across / w))


def _pixel(img, across=96, colours=24):
    block, across, down = _blocks(img, across)
    small = img.resize((across, down), Image.BOX)
    try:
        small = small.quantize(colors=colours, dither=getattr(getattr(Image, "Dither", Image), "NONE", 0))
    except (TypeError, ValueError):
        small = small.quantize(colors=colours)
    return small.convert("RGB").resize((across * block, down * block), Image.NEAREST)


def _eink(img, across=400):
    """Like the panel: grey, then black or white dots, on paper."""
    block, across, down = _blocks(img, across)
    g = ImageOps.autocontrast(img.convert("L").resize((across, down), Image.LANCZOS), cutoff=1)
    dots = g.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
    dots = dots.resize((across * block, down * block), Image.NEAREST)
    return ImageOps.colorize(dots, black=(34, 34, 38), white=(226, 224, 216))


DRAWN = {"sketch": _sketch, "trace": _trace, "comic": _comic, "pixel": _pixel, "eink": _eink}


def apply(img, fid):
    """The picture with the filter on (an RGB image)."""
    f = BY_ID.get(fid) or BY_ID["none"]
    img = img.convert("RGB")
    if f["css"] is None:
        return DRAWN[f["id"]](img)
    return _colour(img, f["css"]) if f["css"] else img


# -- taking and keeping them ------------------------------------------------------------------

def take(fid="none", rot=0, mirror=False):
    """Take one now: woken first if the camera's asleep, at its best size,
    turned and mirrored like the live view, filtered, kept. Returns its info."""
    from . import camera, ptz
    fid = fid if fid in BY_ID else "none"
    if camera.CAMERA.paused_for():
        raise ValueError(f"the camera is paused for {camera.CAMERA.paused_for()} s more (Settings → Sound)")
    try:
        if ptz.PTZ.wake("a photo"):                     # it was asleep: it lifted itself and turned back
            ptz.PTZ.settle()
        else:
            ptz.PTZ.wait_awake()
    except Exception as e:
        log.debug("photo: waking the camera: %s", e)
    jpg = camera.CAMERA.still()
    if not jpg:
        raise ValueError(camera.CAMERA.error or "no picture from the camera yet")
    img = Image.open(io.BytesIO(jpg))
    img.load()
    img = img.convert("RGB")
    if mirror:
        img = ImageOps.mirror(img)
    rot = int(rot or 0) % 360
    if rot in (90, 180, 270):
        img = img.rotate(-rot, expand=True)             # the page turns it clockwise; PIL turns the other way
    return save(apply(img, fid), fid)


def save(img, fid="none"):
    os.makedirs(THUMBS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tail = "" if fid in ("none", None) else f"-{fid}"
    with _lock:
        name, n = f"{stamp}{tail}.jpg", 2
        while os.path.exists(os.path.join(DIR, name)):
            name, n = f"{stamp}-{n}{tail}.jpg", n + 1
        img.convert("RGB").save(os.path.join(DIR, name), "JPEG", quality=90, optimize=True)
    _make_thumb(name, img)
    log.info("photo: %s (%dx%d)", name, img.width, img.height)
    return info(name)


def _make_thumb(name, img=None):
    try:
        if img is None:
            img = Image.open(os.path.join(DIR, name))
        small = img.convert("RGB")
        small.thumbnail((THUMB, THUMB), Image.LANCZOS)
        os.makedirs(THUMBS, exist_ok=True)
        small.save(os.path.join(THUMBS, name), "JPEG", quality=80)
    except OSError as e:
        log.info("photo: no small copy of %s: %s", name, e)


def valid(name):
    return bool(name) and bool(_NAME.match(name)) and os.path.isfile(os.path.join(DIR, name))


def path_of(name):
    return os.path.join(DIR, name) if valid(name) else None


def thumb_of(name):
    if not valid(name):
        return None
    p = os.path.join(THUMBS, name)
    if not os.path.isfile(p):
        _make_thumb(name)
    return p if os.path.isfile(p) else os.path.join(DIR, name)


def info(name):
    p = os.path.join(DIR, name)
    m = _PARTS.match(name)
    fid = (m.group(3) if m else None) or "none"          # a filter since retired keeps its name (Faded)
    try:
        ts = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else os.path.getmtime(p)
    except (ValueError, OSError):
        ts = 0.0
    try:
        with Image.open(p) as im:
            w, h = im.size
    except OSError:
        w = h = 0
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    label = BY_ID[fid]["label"] if fid in BY_ID else fid.capitalize()
    return {"name": name, "url": f"/api/photos/{name}", "thumb": f"/api/photos/{name}/thumb",
            "filter": fid, "label": label, "ts": ts, "w": w, "h": h, "bytes": size}


def listing(limit=60):
    """The newest first: ([info], how many there are)."""
    try:
        names = sorted((n for n in os.listdir(DIR) if _NAME.match(n) and os.path.isfile(os.path.join(DIR, n))),
                       reverse=True)
    except OSError:
        names = []
    return [info(n) for n in names[:limit]], len(names)


def delete(name):
    if not valid(name):
        return False
    for p in (os.path.join(DIR, name), os.path.join(THUMBS, name)):
        try:
            os.remove(p)
        except OSError:
            pass
    return True


def latest():
    items, _ = listing(1)
    return items[0] if items else None


# -- "take a picture" --------------------------------------------------------------------------

_WORDS = [
    (r"black(?: and |\s*&\s*)white|b\s*&\s*w|b&w|mono(?:chrome)?|gr[ae]y(?:scale)?", "bw"),
    (r"sepia|old[- ]fashioned|old[- ]timey|old|vintage|retro|faded", "sepia"),
    (r"warm(?:er)?", "warm"),
    (r"vivid|colou?rful|bright", "vivid"),
    (r"blue|cyanotype|cool", "blue"),
    (r"sketch(?:ed)?|pencil|drawing|drawn", "sketch"),
    (r"edge(?:s|[- ]trace[d]?|[- ]detect(?:ed|ion)?)?|traced?|outlines?|outlined", "trace"),
    (r"comic(?: book)?|cartoon", "comic"),
    (r"pixel(?:ated|ly)?|8[- ]?bit|pixel art", "pixel"),
    (r"e[- ]?ink|dithered|newspaper", "eink"),
]
_ANY = "|".join(f"(?:{w})" for w, _ in _WORDS)
_LEAD = r"^(?:(?:please|can you|could you|would you|will you|hey|now|ok|okay|go on|go ahead and|and|then|just|quick)[ ,]+)*"
_WHAT = r"(?:picture|photo|pic|selfie|snapshot|snap|photograph)"


def _which(word):
    for pattern, fid in _WORDS:
        if word and re.fullmatch(pattern, word):
            return fid
    return None


def parse(text):
    """"Take a picture", "snap a black and white photo of me", "take a photo
    in sepia": {"filter": id}, or None when it isn't one."""
    low = " ".join(re.sub(r"[^a-z0-9&' ,-]+", " ", (text or "").lower().replace("’", "'")).split())
    if not low:
        return None
    m = re.match(_LEAD + r"(?:take|snap|shoot|grab|make)(?: me| us)? (?:a |an |another |one more |my |our |a quick )?"
                 + rf"(?:(?P<f1>{_ANY})[ -])?" + _WHAT
                 + r"(?: of (?:me|us|this|that|the room|the view|everyone|everybody|you|yourself|it))?"
                 + rf"(?: (?:in|with|using)(?: the| a| an)? (?P<f2>{_ANY})(?: filter| effect| style| look| mode)?)?"
                 + r"(?:[ ,]+(?:please|now))?\s*$", low)
    if not m:
        return None
    return {"filter": _which(m.group("f1") or m.group("f2") or "") or "none"}
