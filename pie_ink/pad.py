"""The GPi Case 2W's buttons, screen by screen.

Each screen has buttons of its own, shown along the bottom of the case's screen (the GPi panel
draws them): on Finance ◀ ▶ go through the coins and shares and ▲ ▼ through the chart's range,
on Music ◀ ▶ are the tracks and ▲ ▼ the volume, and so on. The same few do the same on every
screen:

    L  R      the previous / next screen
    B         home: every screen, to pick one (◀ ▶ ▲ ▼, A to open it, B back)
    X         ask the bot about this screen (its words over the screen, and out loud)
    START     the screen off / on
    SELECT    light / dark

What a button does that the screen alone wouldn't show is said over it for a moment (a toast).
The service hands every press here (press()) and asks what to show along the bottom (footer()).
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

RANGES = [(1, "1D"), (7, "1W"), (30, "1M"), (180, "6M"), (365, "1Y")]   # Finance's chart, ▲ ▼
SOURCES = [("message", "Message"), ("joke", "Joke"), ("fortune", "Fortune"), ("topic", "Topic"), ("bot", "Bot")]
CLOCK_SIZES = list(range(20, 77, 4))
READER_SIZES = [10, 12, 14, 16, 18, 20]
AUTO_TURN = 30                       # seconds a page, when Read turns them by itself
ASK_TOPICS = {"me": "day", "earnings": "day", "clock": "time", "weather": "weather", "crypto": "coins",
              "camera": "camera", "music": "music"}
WAIT_FOR_BOT = 120                   # seconds an answer asked for with X is waited for, to show it
MENU_COLS = 4

FIN = {"auto": True, "skip": 0, "hold": None}     # Finance: rotating by itself, stepped, or held on one
WX = {"day": 0}                                     # Weather: now (0), or one of the next days
SYS = {"units": None}                               # System: memory and storage as % or as GB (None: both, in turn)
MENU = {"open": False, "sel": 0}

_service = None


def attach(service):
    global _service
    _service = service


# -- small things --------------------------------------------------------------------------------

def toast(text, seconds=2.0):
    from . import badge
    badge.toast(text, seconds, poke=_service.poke if _service else None)


def _toast_for(text):
    return min(14.0, 4.0 + len(text) / 14)


def _set(patch):
    """A setting changed with a button: kept, as one changed on the page is."""
    return _service.apply_settings(patch)


def _say(text):
    from . import audio
    conf = (_service.config.get("audio") or {}) if _service else {}
    threading.Thread(target=audio.say, args=(text, conf), daemon=True, name="pad-say").start()


def _mode():
    return getattr(_service, "_mode", None)


def _poke():
    if _service:
        _service.poke()


def _joke():
    import json
    import os
    import random
    from .settings import ASSETS_DIR
    try:
        with open(os.path.join(ASSETS_DIR, "jokes.json")) as f:
            j = random.choice(json.load(f))
        return f"{j['setup']}\n{j['punchline']}"
    except Exception:
        return ""


def ask(topic=None):
    """X: the bot says something about this screen; its words go over the screen once they come, and it
    says them out loud (the bot's own voice setting). A joke instead, while the bot is off."""
    from . import llm, mascot
    conf = _service.config
    lconf = conf.get("llm", {})
    if not (lconf.get("enabled") and (lconf.get("model") or "").strip()):
        joke = _joke()
        if not joke:
            toast("The bot is off — the robot button on the page turns it on", 4)
            return "a joke (the bot's off)"
        toast(joke, _toast_for(joke))
        if conf.get("audio", {}).get("speak_bot"):
            _say(joke.replace("\n", ". "))
        return "a joke (the bot's off)"
    if llm.busy():
        toast("Thinking…", 3)
        return "the bot's thinking"
    before = llm.latest()["ts"]
    if not mascot.ask(conf, "idle", topic=topic):
        toast("The bot is busy", 2)
        return "the bot's busy"
    toast("Thinking…", 60)

    def wait():
        deadline = time.time() + WAIT_FOR_BOT
        while time.time() < deadline:
            said = llm.latest()
            if said["ts"] != before and said["text"]:
                toast(said["text"], _toast_for(said["text"]))
                return
            if not llm.busy():
                toast(("The bot couldn't answer: " + said["error"])[:110] if said.get("error") else "The bot said nothing", 4)
                return
            time.sleep(0.3)
    threading.Thread(target=wait, daemon=True, name="pad-bot").start()
    return "asked the bot"


# -- Finance --------------------------------------------------------------------------------------

def finance_index(count, now=None, per=90):
    """Which of `count` coins and shares Finance shows now: in turn (every `per` seconds), stepped
    with ◀ ▶ — or the one it's held on, with Auto off."""
    if count <= 0:
        return 0
    if not FIN["auto"] and FIN["hold"] is not None:
        return FIN["hold"] % count
    return (int((now or time.time()) // max(10, per)) + FIN["skip"]) % count


def _finance_items():
    from . import mining, watchlist
    return watchlist.visible() + [{"id": f"{m.kind}-mining"} for m in mining.enabled()]


def _per():
    return max(10, int((_service.config.get("crypto") or {}).get("coin_seconds", 90) or 90))


def days_now():
    return int((_service.config.get("display") or {}).get("crypto_days", 1) or 1) if _service else 1


def range_label(days):
    return dict(RANGES).get(days) or f"{days}D"


def _finance(button):
    if button in ("left", "right"):
        step = 1 if button == "right" else -1
        if FIN["auto"]:
            FIN["skip"] += step
        else:
            FIN["hold"] = (FIN["hold"] or 0) + step
        _poke()
        return "the next one" if step > 0 else "the one before"
    if button in ("up", "down"):
        days = [d for d, _ in RANGES]
        now = days_now()
        i = days.index(now) if now in days else 0
        new = days[(i + (1 if button == "up" else -1)) % len(days)]
        _set({"display": {"crypto_days": new}})
        return range_label(new)
    if button == "a":
        if FIN["auto"]:
            FIN["hold"] = finance_index(len(_finance_items()), per=_per())
            FIN["auto"] = False
            toast("Auto off: this one stays", 2)
        else:
            FIN["auto"], FIN["hold"], FIN["skip"] = True, None, 0
            toast("Auto: the next one every so often", 2)
        _poke()
        return "auto"
    return None


def _finance_hints():
    return [("◀▶", "Next"), ("▲▼", range_label(days_now())), ("A", "Auto", FIN["auto"]), ("X", "Ask")]


# -- Weather --------------------------------------------------------------------------------------

def _forecast_days():
    try:
        from .lcd_screens import _WEATHER
        data = _WEATHER.current()
        return (data or {}).get("days") or []
    except Exception:
        return []


def weather_day():
    return WX["day"]


def _day_label(i):
    if i == 0:
        return "Now"
    days = _forecast_days()
    try:
        from datetime import datetime
        return datetime.strptime(days[i]["date"], "%Y-%m-%d").strftime("%a")
    except (IndexError, KeyError, ValueError):
        return "Day"


def _find_me():
    from . import cache, locate, weather
    toast("Finding you…", 10)

    def go():
        cache.clear("ip_location")
        cache.clear("ip_location__fail")
        loc = locate.from_network()
        if not loc:
            toast("Couldn't tell where this Pi is", 3)
            return
        config = _set({"weather": {"lat": loc["lat"], "lon": loc["lon"], "location": loc["place"],
                                   "country": loc["country"], "source": "network"}})
        toast(loc["place"] or "Found you", 3)
        try:
            weather.fetch(loc["lat"], loc["lon"], config["weather"].get("units", "F"))
            _poke()
        except Exception as e:
            log.info("weather after finding you: %s", e)
    threading.Thread(target=go, daemon=True, name="pad-find").start()


def _weather(button):
    ws = _service.config.get("weather") or {}
    if button == "a" and ws.get("lat") is None:
        _find_me()
        return "finding you"
    if button in ("left", "right"):
        n = max(1, min(5, len(_forecast_days())))
        WX["day"] = (WX["day"] + (1 if button == "right" else -1)) % n
        _poke()
        return _day_label(WX["day"])
    if button in ("up", "down"):
        units = "C" if ws.get("units", "F") == "F" else "F"
        _set({"weather": {"units": units}})
        toast(f"°{units}", 1.5)
        return f"°{units}"
    return None


def _weather_hints():
    ws = _service.config.get("weather") or {}
    if ws.get("lat") is None:
        return [("A", "Find me"), ("X", "Ask")]
    return [("◀▶", _day_label(WX["day"])), ("▲▼", f"°{ws.get('units', 'F')}"), ("X", "Ask")]


# -- Clock ----------------------------------------------------------------------------------------

def _clock(button):
    from . import text
    s = _service.config.get("clock") or {}
    if button in ("left", "right"):
        fonts = [f["id"] for f in text.available()]
        if not fonts:
            return None
        cur = text.resolve(s.get("font", 1))
        i = fonts.index(cur) if cur in fonts else 0
        new = fonts[(i + (1 if button == "right" else -1)) % len(fonts)]
        _set({"clock": {"font": new}})
        name = next((f["name"] for f in text.available() if f["id"] == new), new)
        toast(name, 1.5)
        return name
    if button in ("up", "down"):
        size = int(s.get("size", 36) or 36)
        smaller = [v for v in CLOCK_SIZES if v < size]
        bigger = [v for v in CLOCK_SIZES if v > size]
        new = (bigger[0] if bigger else size) if button == "up" else (smaller[-1] if smaller else size)
        _set({"clock": {"size": new}})
        return f"size {new}"
    if button == "a":
        new = "12" if s.get("format") == "24" else "24"
        _set({"clock": {"format": new}})
        return f"{new}-hour"
    if button == "y":
        on = not s.get("show_seconds")
        _set({"clock": {"show_seconds": on}})
        return "seconds" if on else "no seconds"
    return None


def _clock_hints():
    s = _service.config.get("clock") or {}
    return [("◀▶", "Font"), ("▲▼", "Size"), ("A", "24h", s.get("format") == "24"),
            ("Y", "Seconds", bool(s.get("show_seconds"))), ("X", "Ask")]


# -- Message --------------------------------------------------------------------------------------

def _source():
    return (_service.config.get("message") or {}).get("source", "message")


def _message(button):
    mode = _mode()
    if button in ("left", "right") and mode is not None:
        if mode.on_button("next" if button == "right" else "prev"):
            _poke()
            return "the next one" if button == "right" else "the one before"
        return None
    if button in ("up", "down"):
        keys = [k for k, _ in SOURCES]
        cur = _source()
        i = keys.index(cur) if cur in keys else 0
        new = keys[(i + (1 if button == "down" else -1)) % len(keys)]
        _set({"message": {"source": new}})
        label = dict(SOURCES)[new]
        toast(label, 1.5)
        return label
    if button == "a":
        words = (getattr(mode, "_current", "") or "").strip()
        if not words:
            return None
        _say(words.replace("\n", ". "))
        return "read out"
    return None


def _message_hints():
    hints = [("◀▶", "Next")] if _source() != "message" else []
    return hints + [("▲▼", dict(SOURCES).get(_source(), "Message")), ("A", "Read"), ("X", "Ask")]


# -- Read -----------------------------------------------------------------------------------------

def _reader(button):
    from . import reader
    from .modes.reader import STATE
    s = _service.config.get("reader") or {}
    if button in ("left", "right"):
        STATE.step(1 if button == "right" else -1)
        _poke()
        return "page"
    if button in ("up", "down"):
        books = reader.library()
        if not books:
            toast("No books yet — add some on the page", 3)
            return None
        cur = (STATE.book or {}).get("id")
        ids = [b["id"] for b in books]
        i = ids.index(cur) if cur in ids else -1
        book = books[(i + (1 if button == "down" else -1)) % len(books)]
        STATE.open(book)
        toast(book.get("title") or "A book", 2.5)
        _poke()
        return "book"
    if button == "a":
        on = not int(s.get("auto_seconds", 0) or 0)
        _set({"reader": {"auto_seconds": AUTO_TURN if on else 0}})
        toast(f"A page every {AUTO_TURN} seconds" if on else "Pages turn when you say", 2)
        return "auto"
    if button == "y":
        size = int(s.get("size", 12) or 12)
        bigger = [v for v in READER_SIZES if v > size]
        new = bigger[0] if bigger else READER_SIZES[0]
        _set({"reader": {"size": new}})
        return f"size {new}"
    return None


def _reader_hints():
    s = _service.config.get("reader") or {}
    return [("◀▶", "Page"), ("▲▼", "Book"), ("A", "Auto", bool(int(s.get("auto_seconds", 0) or 0))),
            ("Y", f"Size {int(s.get('size', 12) or 12)}")]


# -- Camera ---------------------------------------------------------------------------------------

def _turns():
    try:
        from . import ptz
        return ptz.PTZ.available()
    except Exception:
        return False


def photo():
    from . import photos
    toast("Taking a photo…", 8)
    mirror = bool((_service.config.get("camera") or {}).get("mirror"))

    def snap():
        try:
            photos.take(mirror=mirror)
            toast("Photo saved", 2.5)
        except Exception as e:
            toast(f"No photo: {e}"[:110], 4)
    threading.Thread(target=snap, daemon=True, name="pad-photo").start()
    return "a photo"


def _camera(button):
    if button == "a":
        return photo()
    if _turns():
        from . import ptz
        try:
            if button in ("left", "right", "up", "down"):
                dpan = {"left": -10, "right": 10}.get(button, 0)
                dtilt = {"up": 8, "down": -8}.get(button, 0)
                ptz.PTZ.nudge(dpan=dpan, dtilt=dtilt)
                return "turned"
            if button == "y":
                z = ptz.PTZ.position().get("zoom", 0.0)
                ptz.PTZ.set(zoom=0.0 if z >= 0.99 else min(1.0, z + 0.25))
                return "zoom"
        except Exception as e:
            toast(str(e)[:100], 3)
            return None
        return None
    if button in ("left", "right"):
        from .camera import CAMERA
        CAMERA.next_camera()
        _poke()
        return "the next camera"
    return None


def _camera_hints():
    if _turns():
        return [("PAD", "Turn"), ("A", "Photo"), ("Y", "Zoom"), ("X", "Ask")]
    return [("◀▶", "Camera"), ("A", "Photo"), ("X", "Ask")]


# -- Music ----------------------------------------------------------------------------------------

def _volume():
    return int((_service.config.get("audio") or {}).get("volume", 80) or 80)


def _music(button):
    from . import audio, music
    p = music.PLAYER
    if button in ("left", "right"):
        p.step(1 if button == "right" else -1)
        _poke()
        return "track"
    if button in ("up", "down"):
        v = max(0, min(100, _volume() + (10 if button == "up" else -10)))
        config = _set({"audio": {"volume": v}})
        threading.Thread(target=audio.set_volume, args=(v, (config.get("audio") or {}).get("device", "")), daemon=True).start()
        toast(f"Volume {v}%", 1.5)
        return f"volume {v}"
    if button == "a":
        st = p.toggle() or p.status()
        word = {"playing": "Playing", "paused": "Paused"}.get((st or {}).get("state"), "No music yet — add some on the page")
        toast(word, 2)
        return word.lower()
    if button == "y":
        on = not p.shuffle
        with p.lock:
            p.shuffle = on
            p._reorder()
        _set({"music": {"shuffle": on}})
        toast("Shuffle" if on else "In order", 1.5)
        return "shuffle"
    return None


def _music_hints():
    from . import music
    p = music.PLAYER
    return [("◀▶", "Track"), ("▲▼", f"Vol {_volume()}%"), ("A", "Pause" if p.state == "playing" else "Play"),
            ("Y", "Shuffle", bool(p.shuffle)), ("X", "Ask")]


# -- the rest -------------------------------------------------------------------------------------

def _me(button):
    if button in ("left", "right"):
        from .modes.me import MeMode
        MeMode.nudge += 1 if button == "right" else -1
        _poke()
        return "the next fact"
    return None


def _groupchat(button):
    if button == "a":
        from . import social
        if social.status()["ai_on"]:
            social.stop_ai(_service.config)
            toast("The bots stop talking", 2)
        else:
            social.start_ai()
            toast("The bots can talk", 2)
        _poke()
        return "bots talking"
    return None


def _groupchat_hints():
    from . import social
    return [("A", "Bots talk", bool(social.status().get("ai_on"))), ("X", "Ask")]


def _system(button):
    if button in ("left", "right"):
        SYS["units"] = "size" if SYS["units"] != "size" else "pct"
        _poke()
        return "% / GB"
    if button == "a":
        from .modes import system as sysmode
        ip = (getattr(sysmode, "_last", {}) or {}).get("ip") or ""
        if not ip:
            toast("No network", 2)
            return None
        toast(ip, 5)
        _say("My address is " + ip.replace(".", " dot "))
        return "said the address"
    return None


def _buddy(button):
    if button == "a":
        from . import buddy
        if buddy.BUDDY.running():
            buddy.BUDDY.poke()
            return "looked"
        toast("The little friend is off (Settings → Camera)", 3)
        return None
    return None


CONTROLS = {
    "crypto": (_finance, _finance_hints),
    "weather": (_weather, _weather_hints),
    "clock": (_clock, _clock_hints),
    "message": (_message, _message_hints),
    "reader": (_reader, _reader_hints),
    "camera": (_camera, _camera_hints),
    "music": (_music, _music_hints),
    "me": (_me, lambda: [("◀▶", "Facts"), ("X", "Ask")]),
    "groupchat": (_groupchat, _groupchat_hints),
    "system": (_system, lambda: [("◀▶", "% / GB"), ("A", "Say IP"), ("X", "Ask")]),
    "buddy": (_buddy, lambda: [("A", "Look"), ("X", "Ask")]),
}


# -- home: every screen, to pick one (B) ----------------------------------------------------------

def screens():
    """[(name, label)]: the screens, in the order L and R go through them."""
    from .modes import MODES
    from .service import CYCLE
    return [(n, MODES[n].label) for n in CYCLE if n in MODES]


def menu_open():
    return MENU["open"]


def _menu(button):
    items = screens()
    n = len(items)
    sel = MENU["sel"] % max(1, n)
    if button in ("left", "right"):
        sel = (sel + (1 if button == "right" else -1)) % n
    elif button in ("up", "down"):
        sel = (sel + (MENU_COLS if button == "down" else -MENU_COLS)) % n
    elif button == "a":
        MENU["open"] = False
        _service.set_mode(items[sel][0])
        return f"opened {items[sel][1]}"
    elif button in ("b", "home"):
        MENU["open"] = False
        _poke()
        return "back"
    MENU["sel"] = sel
    _poke()
    return "moved"


def menu_frame(size, config):
    """Home: the screens as tiles, the one picked lit."""
    from PIL import Image, ImageDraw
    from .lcd_screens import _bold, _hex
    from .text import font
    w, h = size
    img = Image.new("RGB", (w, h), (16, 19, 23))
    d = ImageDraw.Draw(img)
    accent = _hex((config.get("display") or {}).get("accent", "#62d2ff")) or (98, 210, 255)
    items = screens()
    cur = getattr(_service, "mode_name", None)
    rows = max(1, -(-len(items) // MENU_COLS))
    pad, top = 8, 8
    tw, th = (w - pad * (MENU_COLS + 1)) / MENU_COLS, (h - top - pad * rows) / rows
    f = _bold(13 if w >= 300 else 11)
    small = font(1, 9)
    for i, (name, label) in enumerate(items):
        r, c = divmod(i, MENU_COLS)
        x0, y0 = pad + c * (tw + pad), top + r * (th + pad)
        on = i == MENU["sel"] % len(items)
        d.rounded_rectangle([x0, y0, x0 + tw, y0 + th], radius=8, fill=accent if on else (31, 37, 46))
        lf = f
        for size in (13, 12, 11, 10, 9):                 # a long name ("Group chat") a size down, to fit its tile
            if d.textlength(label, font=lf) <= tw - 8:
                break
            lf = _bold(size if w >= 300 else size - 2)
        tl = d.textlength(label, font=lf)
        d.text((x0 + (tw - tl) / 2, y0 + th / 2 - 9), label, font=lf, fill=(16, 19, 23) if on else (236, 239, 243))
        if name == cur:
            word = "now"
            d.text((x0 + (tw - d.textlength(word, font=small)) / 2, y0 + th / 2 + 7), word, font=small,
                   fill=(16, 19, 23) if on else (150, 158, 170))
    return img


# -- every press, and the footer ------------------------------------------------------------------

def press(button):
    """A button on the case: what it did, in a few words (for the page), or ""."""
    svc = _service
    if svc is None:
        return ""
    from . import attention
    attention.touch()
    if svc.mode_name in (None, "off"):
        svc.on_button_action("next")                     # (any button wakes the screen)
        return "woke the screen"
    if MENU["open"]:
        return _menu(button)
    if button in ("l", "l2"):
        svc.on_button_action("prev_mode")
        return "the screen before"
    if button in ("r", "r2"):
        svc.on_button_action("next_mode")
        return "the next screen"
    if button in ("b", "home"):
        names = [n for n, _ in screens()]
        MENU.update(open=True, sel=names.index(svc.mode_name) if svc.mode_name in names else 0)
        _poke()
        return "home"
    if button == "start":
        svc.on_button_action("off")
        return "screen off"
    if button == "select":
        dark = not (svc.config.get("display") or {}).get("dark_mode")
        _set({"display": {"dark_mode": dark}})
        toast("Dark" if dark else "Light", 1.5)
        return "dark" if dark else "light"
    mode = svc.mode_name
    handler = CONTROLS.get(mode, (None, None))[0]
    if handler is not None:
        try:
            did = handler(button)
        except Exception as e:
            log.exception("the %s button on %s", button, mode)
            toast(f"That didn't work: {e}"[:100], 3)
            return ""
        if did:
            return did
    if button == "x":
        return ask(ASK_TOPICS.get(mode))
    return ""


def footer(svc=None):
    """What the bottom of the case's screen shows: ([(key, label, lit)], the screen's name, the accent)."""
    svc = svc or _service
    from .lcd_screens import _hex
    accent = _hex(((svc.config if svc else {}).get("display") or {}).get("accent", "#62d2ff")) or (98, 210, 255)
    if MENU["open"]:
        return [("PAD", "Pick", False), ("A", "Open", False), ("B", "Back", False)], "Home", accent
    mode = getattr(svc, "_mode", None)
    name = getattr(svc, "mode_name", None)
    if name in (None, "off"):
        return [], "", accent
    hints_for = CONTROLS.get(name, (None, None))[1]
    try:
        hints = list(hints_for()) if hints_for else [("X", "Ask")]
    except Exception:
        log.debug("hints for %s", name, exc_info=True)
        hints = [("X", "Ask")]
    hints = [h if len(h) == 3 else (h[0], h[1], False) for h in hints]
    return hints + [("L R", "Screens", False), ("B", "Home", False)], getattr(mode, "label", "") or "", accent
