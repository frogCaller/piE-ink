"""Any screen, drawn for a screen of any size: laid out again for that shape, so it fills it
— no bars, nothing stretched.

The e-ink's screens already lay themselves out for the panel they're given (the 2.13", the
2.7", the 3.7", a Whisplay on its side or upright), so one is simply asked to draw for a
different panel: the second screen's 240×280, a 1.9"'s 170×320, a Pietato's TV. Where a
screen has a colour version (the System, Weather, Finance, Camera and little-friend screens
the LCDs have), that one is drawn instead, at the size asked for.

    render("me", (240, 280), config, light=False)       # an RGB picture, exactly 240×280

A screen keeps its state from one picture to the next (the joke on the Message screen
doesn't change with every frame): one instance per screen and size, given the new settings
when they change.
"""
import copy
import logging
import threading

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

COLOUR = {"system", "weather", "crypto", "camera", "buddy"}   # drawn in colour, from lcd_screens
NOT_HERE = {"off"}                                            # nothing to draw
_lock = threading.Lock()
_made = {}                                                    # (name, w, h, dark) -> [mode, the config it has]


def palette(light):
    """(background, ink) for a screen drawn light (black on white) or dark."""
    return ((255, 255, 255), (16, 18, 17)) if light else ((0, 0, 0), (240, 242, 240))


def _mode(name, size, config, dark):
    from .modes import MODES
    key = (name, size[0], size[1], dark)
    conf = copy.deepcopy(config)
    conf.setdefault("display", {})["dark_mode"] = dark
    with _lock:
        got = _made.get(key)
        if got is None:
            m = MODES[name](conf, {"width": size[0], "height": size[1], "kind": "lcd", "colour": True,
                                   "driver": "anysize", "label": "", "buttons": 0, "has_partial": False})
            got = _made[key] = [m, conf]
            if len(_made) > 24:                               # a few sizes of a few screens at most
                _made.pop(next(iter(_made)))
        elif got[1] != conf:
            got[0].update(conf)
            got[1] = conf
        return got[0]


def render(name, size, config, light=True, accent=None):
    """Screen `name` as an RGB picture exactly `size`. None when there's no such screen."""
    from . import lcd_screens
    from .modes import MODES
    w, h = int(size[0]), int(size[1])
    if name in NOT_HERE:
        return Image.new("RGB", (w, h), palette(light)[0])
    display = config.get("display", {})
    if name in COLOUR or (name == "image" and _painted()):
        conf = {"screen": "paint" if name == "image" else name, "light_mode": bool(light),
                "accent": accent or display.get("accent", "#62d2ff"), "crypto_days": display.get("crypto_days", 1)}
        try:
            return lcd_screens.render(conf, config=config, size=(w, h)).convert("RGB")
        except Exception:
            log.exception("the colour %s screen at %dx%d", name, w, h)
    if name not in MODES:
        return None
    mode = _mode(name, (w, h), config, dark=not light)
    try:
        if name == "camera":
            mode.start()
        img = mode.render()
    except Exception:
        log.exception("the %s screen at %dx%d", name, w, h)
        return None
    grey = img.convert("L")
    if grey.size != (w, h):
        grey = grey.resize((w, h), Image.BOX if grey.width >= w else Image.LANCZOS)
    bg, ink = palette(light)
    # (a dark screen draws white on black; a light one black on white: either way the ends of the grey map to the palette)
    return ImageOps.colorize(grey, black=bg if not light else ink, white=ink if not light else bg)


def _painted():
    try:
        from .paint import PAINT
        return PAINT.image() is not None
    except Exception:
        return False


def forget():
    """Every kept screen let go (settings that change what they are, a test)."""
    with _lock:
        _made.clear()
