"""A Pietato's screen as one more screen: any of PiE-ink's screens, a drawing, a message or
the time, put on it — full screen, laid out for its shape, no bars.

Nothing new is needed on the Pietato: what's sent is a picture made for its screen (the shape
it reports in /api/pi/state: 4:3 on the GPi, 16:9 on a TV), put in its Pictures through its
page's own upload, opened the way its Files opens a picture (POST /api/pi/show), and made
full screen the way its Start button does in the picture viewer (POST /api/pi/press) — so
there's no footer under it either. It stays until someone presses a button on the Pietato
(the first press brings the viewer's footer back, the next puts the picture away).

    Pictures/pie-ink/now/screen.png    what's on its screen now (replaced each time)
    Pictures/pie-ink/drawings/         every drawing sent, kept

The time and the live screens (the weather, Finance, System, Me...) are drawn again each
minute while they're still the picture on its screen. A Pietato whose page has a password
can't be asked (it says so).
"""
import io
import logging
import re
import threading
import time
from datetime import datetime

import requests

from . import pitato

log = logging.getLogger(__name__)

TIMEOUT = 20
BG = (21, 25, 29)               # the card: a deep slate, quiet on a TV in the evening
INK = (238, 240, 236)
SOFT = (150, 158, 164)
PAPER = (246, 247, 244)         # a drawing sits on this, the way it does on the e-ink
CLOCK_FOR = 24 * 3600           # a screen is kept fresh this long at most
FIRST_LOOK = 1.5                # seconds after it's shown: what its screen says (to know it's still ours later)
NOW = "pie-ink/now/screen.png"  # the picture on its screen: alone in its folder, so its slideshow has only it to show
LAYOUT_H = 270                  # a screen is laid out this many lines high for the Pietato, then scaled up to fit it
STATIC = {"image", "postcard"}  # sent once; the rest are drawn again each minute while they're up
_kept = {}                      # ip -> {"token", "kind"}; a new one (or none) ends the old thread
_lock = threading.Lock()


# -- which Pietato -------------------------------------------------------------------------------

def screens():
    """The Pietatos about whose screen can be used: [{ip, name, locked, size}], unlocked first."""
    out = []
    for p in pitato.PITATO.around():
        if not p.get("web"):
            continue
        size = (p.get("state") or {}).get("size") or [640, 480]
        out.append({"ip": p["ip"], "name": p.get("name") or p["ip"], "locked": bool(p.get("locked")) or p.get("state_how") == "locked",
                    "size": size, "here": bool(p.get("here"))})
    return sorted(out, key=lambda s: s["locked"])


def pick(to=None):
    """The Pietato meant: by its address or name, or the first one about. None if there's none."""
    all_ = screens()
    if not all_:
        return None
    want = pitato.plain_name((to or "").strip()).lower()
    if want and want not in ("pitato", "the pitato", "all"):
        for s in all_:
            if want in (s["ip"].lower(), pitato.plain_name(s["name"]).lower()):
                return s
        return None
    return all_[0]


def _entry(s):
    with pitato.PITATO._lock:
        p = pitato.PITATO.seen.get(s["ip"])
        return dict(p) if p else None


# -- the pictures ----------------------------------------------------------------------------

def _aspect(s):
    try:
        rw, rh = int(s["size"][0]), int(s["size"][1])
    except (KeyError, TypeError, ValueError, IndexError):
        rw, rh = 640, 480
    return max(1, rw) / max(1, rh)


def card_size(s):
    """The picture's size for that screen: its own shape exactly (shown full screen, it fills it), as big
    as its picture viewer keeps a picture (1600×1200), so a TV gets it sharp."""
    a = _aspect(s)
    w = min(1600, round(1200 * a))
    return w, round(w / a)


def layout_size(s):
    """The size a screen is laid out at for that Pietato: its shape, at about the size the screens are made for."""
    a = _aspect(s)
    return max(160, round(LAYOUT_H * a)), LAYOUT_H


def _font(key, size):
    from .text import font
    return font(key, size)


def _fit_text(d, text, key, box, biggest, smallest=28):
    """The biggest size the words fit the box at: (font, lines)."""
    from .text import wrap, line_height
    w, h = box[2] - box[0], box[3] - box[1]
    size = biggest
    while True:
        f = _font(key, size)
        lines = wrap(d, text, f, w)
        if len(lines) * line_height(f) * 1.08 <= h or size <= smallest:
            return f, lines
        size = max(smallest, int(size * 0.92))


def drawing_card(img, size):
    """A drawing, as big as it goes, the rest of the screen in the drawing's own paper — so nothing
    frames it. An e-ink drawing is scaled up by whole steps (its pixels stay crisp), a colour one smoothly."""
    from PIL import Image
    W, H = size
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        img = flat
    img = img.convert("RGB")
    paper = img.getpixel((0, 0))
    out = Image.new("RGB", (W, H), paper)
    margin = round(min(W, H) * 0.04)
    k = min((W - 2 * margin) / img.width, (H - 2 * margin) / img.height)
    colours = len(img.getcolors(1024) or range(1025)) > 16
    if not colours and k >= 1:
        k = max(1, int(k))
        img = img.resize((img.width * k, img.height * k), Image.NEAREST)
    else:
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    out.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return out


def screen_card(name, s, config):
    """One of PiE-ink's screens, laid out for that Pietato's shape and scaled up to fill it (None: no such screen)."""
    from PIL import Image, ImageFilter
    from . import anysize
    img = anysize.render(name, layout_size(s), config, light=False)
    if img is None:
        return None
    img = img.resize(card_size(s), Image.LANCZOS)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))


def message_card(text, size, font_key="Font.ttc", when=None):
    """The words, as large as they fit, with the time they were sent under them."""
    from PIL import Image, ImageDraw
    from .text import line_height
    W, H = size
    out = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(out)
    margin = round(W * 0.07)
    small = _font(font_key, 40)
    foot = (when or datetime.now()).strftime("%-I:%M %p").lower()
    box = (margin, margin, W - margin, H - margin - line_height(small) - 24)
    f, lines = _fit_text(d, " ".join((text or "").split()), font_key, box, biggest=170)
    lh = line_height(f) * 1.08
    y = box[1] + max(0, (box[3] - box[1] - lh * len(lines)) / 2)
    for line in lines:
        d.text((margin, y), line, font=f, fill=INK)
        y += lh
    d.text((margin, H - margin - line_height(small)), foot, font=small, fill=SOFT)
    return out


def clock_card(size, conf=None, now=None):
    """The time, big, in the clock screen's own font, and the date under it."""
    from PIL import Image, ImageDraw
    from .text import text_width
    conf = conf or {}
    now = now or datetime.now()
    W, H = size
    out = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(out)
    key = conf.get("font") or "Font.ttc"
    twelve = str(conf.get("format", "12")) != "24"
    hm = now.strftime("%-I:%M" if twelve else "%H:%M")
    ampm = now.strftime("%p").lower() if twelve else ""
    date = now.strftime("%A, %-d %B")

    def fonts(n):
        return _font(key, n), _font(key, max(30, n // 4))
    n = round(H * 0.5)
    while n > 60:
        big, small = fonts(n)
        if text_width(d, hm, big) + (text_width(d, " " + ampm, small) if ampm else 0) <= W * 0.84:
            break
        n = int(n * 0.94)
    big, small = fonts(n)
    dfont = _font(key, max(32, round(H * 0.075)))
    hw = text_width(d, hm, big)
    aw = text_width(d, " " + ampm, small) if ampm else 0
    top, bottom = d.textbbox((0, 0), hm, font=big, anchor="ls")[1], 0      # the digits above the baseline
    dt, db = d.textbbox((0, 0), date, font=dfont, anchor="lt")[1::2]
    gap = round(H * 0.07)
    block = -top + gap + (db - dt)
    base = (H - block) / 2 - top                                             # the digits' baseline
    x = (W - hw - aw) / 2
    d.text((x, base), hm, font=big, fill=INK, anchor="ls")
    if ampm:
        d.text((x + hw, base), " " + ampm, font=small, fill=SOFT, anchor="ls")
    d.text(((W - text_width(d, date, dfont)) / 2, base + bottom + gap), date, font=dfont, fill=SOFT, anchor="lt")
    return out


# -- putting one on its screen ----------------------------------------------------------------

def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue()


def _put(s, path, data, replace):
    """Into its Pictures: (the path it's kept at, or None, why not)."""
    base = f"http://{s['ip']}:{pitato.PORT}"
    try:
        r = requests.put(f"{base}/api/upload", params={"folder": "pictures", "path": path, "exists": "replace" if replace else "rename"},
                         data=data, timeout=TIMEOUT, headers={"Content-Type": "application/octet-stream"})
    except Exception as e:
        return None, f"{s['name']} isn't answering ({str(e)[:60]})"
    if r.status_code == 401:
        return None, f"{s['name']}'s page has a password, so I can't put things on its screen"
    if r.status_code != 200:
        try:
            why = r.json().get("error") or ""
        except ValueError:
            why = ""
        return None, f"{s['name']} wouldn't take it ({why or r.status_code})"
    try:
        kept = r.json().get("path") or path
    except ValueError:
        kept = path
    return kept, ""


def _show(s, path):
    base = f"http://{s['ip']}:{pitato.PORT}"
    try:
        r = requests.post(f"{base}/api/pi/show", json={"folder": "pictures", "path": path}, timeout=TIMEOUT)
    except Exception as e:
        return False, f"{s['name']} isn't answering ({str(e)[:60]})"
    if r.status_code == 401:
        return False, f"{s['name']}'s page has a password, so I can't put things on its screen"
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200 or d.get("error"):
        return False, f"{s['name']} couldn't show it: {d.get('error') or r.status_code}"
    return True, ""


def _press(s, button):
    base = f"http://{s['ip']}:{pitato.PORT}"
    try:
        r = requests.post(f"{base}/api/pi/press", json={"button": button}, timeout=TIMEOUT)
        return r.status_code == 200 and not (r.json() or {}).get("error")
    except Exception:
        return False


def _full_screen(s, waited=2.0):
    """The picture just opened, made full screen (its viewer's Start: no footer). Only pressed once its
    screen says the picture viewer is up — Start means something else everywhere else."""
    end = time.time() + waited
    while True:
        state = _where_now(s)
        if (state or {}).get("view") == "photo":
            return _press(s, "start")
        if time.time() >= end or state is None:
            return False
        time.sleep(0.3)


def put(s, img, path=NOW, replace=True):
    """A picture on that Pietato's screen, full screen: (done, why not, the path it's kept at)."""
    kept, why = _put(s, path, _png(img), replace)
    if not kept:
        return False, why, None
    okay, why = _show(s, kept)
    if okay:
        _full_screen(s)
    return okay, why, kept


EVERY_ONE = ("all", "both", "pitatos", "the pitatos", "both pitatos", "all pitatos", "all the pitatos", "every pitato",
             "each pitato", "everywhere", "both of them", "all of them")


def _names(ss):
    names = [x["name"] for x in ss]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def targets(to=None):
    """The Pietatos meant — ([one or more], "") — or ([], what to say): by name or address, "both" / "all"
    of them, or "the Pietato" when there's only the one about (with two, it asks which)."""
    all_ = screens()
    if not all_:
        return [], "There's no Pietato on the network to show it on."
    usable = [x for x in all_ if not x["locked"]]
    want = " ".join(pitato.plain_name((to or "").strip()).lower().split())
    if want in EVERY_ONE:
        return (usable or all_), ""
    if want and want not in ("pitato", "the pitato", "my pitato"):
        for x in all_:
            if want in (x["ip"].lower(), pitato.plain_name(x["name"]).lower()):
                return [x], ""
        return [], f"I don't know a Pietato called '{to}' — there's {_names(all_)}."
    if len(usable) > 1:
        return [], f"Which Pietato — {_names(usable).replace(' and ', ' or ')}? Say 'on {usable[0]['name']}', or 'on both pietatos'."
    return [(usable or all_)[0]], ""


def _each(to, one, done):
    """`one` done on each Pietato meant: (any done, what to say). one(s) -> (done, why not); done(names) -> the words."""
    ts, why = targets(to)
    if not ts:
        return False, why
    results = [(x, *one(x)) for x in ts]
    good = [x for x, okay, _ in results if okay]
    bad = [why for _, okay, why in results if not okay]
    if not good:
        return False, "; ".join(bad)
    said = done(_names(good))
    if len(good) > 1:
        said = said.replace("'s screen", "'s screens", 1)
    return True, said + ((" (" + "; ".join(bad) + ")") if bad else "")


def send_drawing(img, to=None):
    """(done, what to say). The drawing is kept in its Pictures too."""
    def one(x):
        stop_clock(x["ip"])
        card = drawing_card(img, card_size(x))
        kept, why = _put(x, datetime.now().strftime("pie-ink/drawings/drawing %Y-%m-%d %H.%M.png"), _png(card), replace=False)
        if not kept:
            return False, why
        okay, why, _ = put(x, card)
        return okay, why
    return _each(to, one, lambda names: f"The drawing is on {names}'s screen.")


def send_message(text, to=None, font_key="Font.ttc"):
    text = " ".join((text or "").split())
    if not text:
        return False, "Say what to put on it."

    def one(x):
        stop_clock(x["ip"])
        okay, why, _ = put(x, message_card(text[:400], card_size(x), font_key))
        return okay, why
    return _each(to, one, lambda names: f"On {names}'s screen: {text[:80]}")


def _keep(s, kind, make):
    """`make` drawn again each minute on that Pietato while it's still the picture there."""
    token = object()
    with _lock:
        _kept[s["ip"]] = {"token": token, "kind": kind}
    threading.Thread(target=_keep_fresh, args=(s, make, token), daemon=True, name="pitato-screen").start()


def send_clock(conf=None, to=None):
    """The time on its screen, kept to the minute while it stays there."""
    def one(x):
        stop_clock(x["ip"])
        make = lambda: clock_card(card_size(x), conf)    # noqa: E731
        okay, why, _ = put(x, make())
        if okay:
            _keep(x, "clock", make)
        return okay, why
    return _each(to, one, lambda names: f"The time is on {names}'s screen; it keeps going until someone presses a button there.")


def send_screen(name, config, to=None, label=None):
    """One of PiE-ink's screens on its screen, full screen; a live one kept fresh each minute while it's up."""
    if name == "clock":
        return send_clock(config.get("clock", {}), to)

    def one(x):
        stop_clock(x["ip"])
        make = lambda: screen_card(name, x, config)      # noqa: E731
        card = make()
        if card is None:
            return False, f"There's no '{name}' screen."
        okay, why, _ = put(x, card)
        if okay and name not in STATIC:
            _keep(x, name, make)
        return okay, why
    return _each(to, one, lambda names: f"{label or name.capitalize()} is on {names}'s screen"
                 + ("." if name in STATIC else ", kept up to date while it's there."))


def stop_clock(ip=None):
    """Nothing kept fresh on that Pietato any more (on every one, with none): True if something was."""
    with _lock:
        if ip is None:
            had = bool(_kept)
            _kept.clear()
            return had
        return _kept.pop(ip, None) is not None


stop_keeping = stop_clock


def keeping(ip):
    """What's being kept fresh on that Pietato's screen ("clock", "weather"...), or None."""
    with _lock:
        k = _kept.get(ip)
        return k["kind"] if k else None


def clock_on(ip):
    """Something kept fresh on that Pietato's screen (the time, a live screen)."""
    return keeping(ip) is not None


def _where_now(s):
    p = _entry(s)
    if not p:
        return None
    state, _how = pitato.PITATO._state_of(p)
    return state


def _till_next_minute():
    now = datetime.now()
    return max(1.0, 60.5 - now.second - now.microsecond / 1e6)


def _keep_fresh(s, make, token):
    """Each minute: the picture drawn again, while it's still the one on its screen."""
    def mine():
        with _lock:
            k = _kept.get(s["ip"])
            return k is not None and k["token"] is token
    time.sleep(FIRST_LOOK)
    state = _where_now(s)
    where = (state or {}).get("where")
    started = time.time()
    while mine() and time.time() - started < CLOCK_FOR:
        time.sleep(_till_next_minute())
        if not mine():
            return
        state = _where_now(s)
        if not state or state.get("view") != "photo" or (where is not None and state.get("where") != where):
            log.info("the screen on %s: put away there, so it stops", s["name"])
            break
        try:
            img = make()
        except Exception:
            log.exception("drawing the screen for %s again", s["name"])
            break
        okay, why, _ = put(s, img)
        if not okay:
            log.info("the screen on %s: %s", s["name"], why)
            break
    with _lock:
        k = _kept.get(s["ip"])
        if k is not None and k["token"] is token:
            del _kept[s["ip"]]


# -- in words ------------------------------------------------------------------------------------

_WHO = r"(?P<who>both(?:\s+(?:of\s+the\s+)?pitatos)?|all(?:\s+(?:of\s+)?(?:the\s+)?pitatos)?|every\s+pitato|pitatos|pitato|[\w.-]+?)"
_ON = r"\s+(?:on|to|onto)\s+(?:the\s+|my\s+)?" + _WHO + r"(?:'s)?(?:\s+(?:screens?|tvs?|telly))?\s*[.!?]*$"
_CLOCK = re.compile(r"^(?:please\s+)?(?:show|put|display|send|throw)\s+(?:me\s+)?(?:the\s+)?(?:time|clock)" + _ON, re.I)
_STOP = re.compile(r"^(?:please\s+)?(?:stop|take\s+(?:off|away)|clear|remove)\s+(?:the\s+)?(?:time|clock|screen|updates|updating)(?:\s+(?:on|from|off)\s+(?:the\s+|my\s+)?" + _WHO + r")?(?:'s)?(?:\s+screens?)?\s*[.!?]*$", re.I)
_SCREEN = re.compile(r"^(?:please\s+)?(?:show|put|display|send|throw)\s+(?:me\s+)?(?:the\s+|my\s+)?(?P<what>[a-z][a-z ]{1,24}?)(?:\s+screen)?" + _ON, re.I)
_DRAW = re.compile(r"^(?:please\s+)?(?:send|show|put|display|throw)\s+(?:this|the|my|that)\s+(?:drawing|sketch|doodle|painting|canvas)" + _ON, re.I)
_MSG = re.compile(r"^(?:please\s+)?(?:show|put|write|display|send)\s+(?:a\s+message\s+(?:saying\s+)?|the\s+words\s+|a\s+note\s+(?:saying\s+)?)?"
                  r"[\"“'‘](?P<text>.+?)[\"”'’]" + _ON, re.I)
_MSG2 = re.compile(r"^(?:please\s+)?(?:send|write|put)\s+(?:a\s+)?(?:message|note)\s+(?:on|to)\s+(?:the\s+|my\s+)?" + _WHO + r"(?:'s)?(?:\s+screens?)?"
                   r"\s*(?:saying|that says|:|,|—|-)\s*(?P<text>.+?)\s*$", re.I)


def _is_pitato(who):
    w = " ".join(pitato.plain_name((who or "").strip()).lower().split())
    if not w or w in ("pitato", "pitatos") or w in EVERY_ONE or w.startswith(("both", "all")) or w == "every pitato":
        return True
    return any(w == pitato.plain_name(s["name"]).lower() for s in screens())


def _who(w):
    """The words for which Pietato, as targets() takes them."""
    w = " ".join((w or "pitato").strip().lower().split())
    return "all" if (w.startswith(("both", "all", "every")) or w == "pitatos") else w


def parse(text):
    """What was asked of a Pietato's screen, or None: {"kind": "clock"|"stop"|"drawing"|"message", "to", "text"}."""
    t = pitato.plain_name(" ".join((text or "").split()))
    if not t or ("pitato" not in t.lower() and not any(pitato.plain_name(s["name"]).lower() in t.lower() for s in screens())):
        return None
    for rx, kind in ((_STOP, "stop"), (_CLOCK, "clock"), (_DRAW, "drawing"), (_MSG2, "message"), (_MSG, "message"), (_SCREEN, "screen")):
        m = rx.match(t)
        if m and _is_pitato(m.group("who")):
            return {"kind": kind, "to": _who(m.group("who")), "text": (m.groupdict().get("text") or "").strip(),
                    "what": (m.groupdict().get("what") or "").strip().lower()}
    return None


def words(owner="they"):
    """A line for the bot: what it can put on a Pietato's screen. '' with none about."""
    all_ = [s for s in screens() if not s["locked"]]
    if not all_:
        return ""
    names = ", ".join(s["name"] for s in all_[:2])
    on = [(s["name"], keeping(s["ip"])) for s in all_ if keeping(s["ip"])]
    line = (f"You can put things on the Pietato's screen ({names}), full screen: any of your screens ('show the weather on the "
            f"pietato'), a drawing ('send this drawing to the pietato'), a message ('show \"dinner is ready\" on the pietato') or "
            f"the time ('show the time on the pietato'); it's done the moment {owner} asks")
    if len(all_) > 1:
        line += (f". There are {len(all_)} Pietatos: things go on one by its name ('… on {all_[0]['name']}') or on both "
                 f"('… on both pietatos'); 'the pietato' alone is asked which")
    for name, kind in on:
        line += f". The time is on {name}'s screen now" if kind == "clock" else f". Your {kind} screen is on {name}'s screen now"
    return line
