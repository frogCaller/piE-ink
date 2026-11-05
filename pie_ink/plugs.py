"""What's plugged into the Pi's USB ports — and the bot's face when that changes.

Every second and a half it looks at the bus (in /sys, which needs no special rights) and, when
something comes or goes, gives it a moment for the rest of a hub's worth to show up, then says so
in one line: "Ooh — a hub, with a keyboard and a USB drive on it!" The face goes wide-eyed for a
while (starry for a drive), and sad when something's pulled out; a drive gets a second line once
it's been looked through (the app does that: "On the USB drive SANDISK: 214 photos and 12 songs").
Plugged straight into the Pi or into a hub, it's the same. Whatever was there when PiE-ink started
isn't news, and something that drops off and comes straight back (a reset) is let be.
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

SYS_USB = "/sys/bus/usb/devices"
EVERY = 1.5                    # seconds between looks at the bus
SETTLE = 2.0                   # seconds given for the rest of a hub's worth to show up
FRESH = 40                     # seconds the face stays changed
DRIVE_WAIT = 15                # seconds a drive gets to be mounted before saying it hasn't been

# what a thing is: (with "a", plural, the bare noun)
KINDS = {
    "hub": ("a hub", "hubs", "hub"),
    "camera": ("a camera", "cameras", "camera"),
    "drive": ("a USB drive", "USB drives", "USB drive"),
    "reader": ("a card reader", "card readers", "card reader"),
    "sound": ("a speaker or mic", "speakers and mics", "speaker"),
    "midi": ("a MIDI keyboard", "MIDI keyboards", "MIDI keyboard"),
    "keyboard": ("a keyboard", "keyboards", "keyboard"),
    "mouse": ("a mouse", "mice", "mouse"),
    "pad": ("a game pad", "game pads", "game pad"),
    "controller": ("a controller", "controllers", "controller"),
    "bluetooth": ("a Bluetooth dongle", "Bluetooth dongles", "Bluetooth dongle"),
    "wifi": ("a Wi-Fi adapter", "Wi-Fi adapters", "Wi-Fi adapter"),
    "network": ("a network adapter", "network adapters", "network adapter"),
    "serial": ("a serial gadget", "serial gadgets", "serial gadget"),
    "phone": ("a phone", "phones", "phone"),
    "printer": ("a printer", "printers", "printer"),
    "gadget": ("a gadget", "gadgets", "gadget"),
}
NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}
PAD_WORDS = ("pad", "joy", "controller", "xbox", "gamepad", "8bitdo", "dualshock", "dualsense", "retroflag")
WIFI_WORDS = ("wlan", "wireless", "802.11", "wi-fi", "wifi", "rtl81", "rtl88", "mt76", "ralink")


def _read(folder, what):
    try:
        with open(os.path.join(folder, what), encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def devices(root=None):
    """What's on the USB bus: [{"bus", "vid", "pid", "product", "maker", "serial", "class", "ifaces", "ports"}],
    ifaces as (class, subclass, protocol) — the Pi's own root hubs left out."""
    root = root or SYS_USB
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for e in entries:
        if ":" in e or e.startswith("usb"):
            continue
        d = os.path.join(root, e)
        vid = _read(d, "idVendor").lower()
        if not vid or vid == "1d6b":
            continue
        ifaces = []
        for i in entries:
            if i.startswith(e + ":"):
                f = os.path.join(root, i)
                ifaces.append((_read(f, "bInterfaceClass").lower(), _read(f, "bInterfaceSubClass").lower(),
                               _read(f, "bInterfaceProtocol").lower()))
        try:
            ports = int(_read(d, "maxchild") or 0)
        except ValueError:
            ports = 0
        out.append({"bus": e, "vid": vid, "pid": _read(d, "idProduct").lower(), "product": _read(d, "product"),
                    "maker": _read(d, "manufacturer"), "serial": _read(d, "serial"),
                    "class": _read(d, "bDeviceClass").lower(), "ifaces": ifaces, "ports": ports})
    return out


def _key(d):
    return f"{d['bus']} {d['vid']}:{d['pid']} {d.get('serial', '')}"


def kind_of(d):
    """What it is, by the classes it says it has (and, for a vendor's own, its name)."""
    classes = {c for c, _s, _p in d.get("ifaces", ())}
    if d.get("class") not in ("", "00", "ef", "ff"):
        classes.add(d["class"])
    name = f"{d.get('maker', '')} {d.get('product', '')}".lower()
    if "09" in classes:
        return "hub"
    if "0e" in classes:
        return "camera"
    if "08" in classes:
        return "reader" if "reader" in name else "drive"
    if "01" in classes:
        audio = {s for c, s, _p in d.get("ifaces", ()) if c == "01"}
        return "midi" if audio == {"01", "03"} or audio == {"03"} else "sound"
    if "03" in classes:
        protocols = {p for c, _s, p in d.get("ifaces", ()) if c == "03"}
        if "01" in protocols:
            return "keyboard"
        if "02" in protocols:
            return "mouse"
        return "pad" if any(w in name for w in PAD_WORDS) else "controller"
    if "e0" in classes:
        return "wifi" if any(w in name for w in WIFI_WORDS) else "bluetooth"
    if "06" in classes:
        return "phone"
    if "07" in classes:
        return "printer"
    if any(w in name for w in PAD_WORDS):
        return "pad"
    if "bluetooth" in name:
        return "bluetooth"
    if any(w in name for w in WIFI_WORDS):
        return "wifi"
    if any(w in name for w in ("ethernet", " lan", "ax88", "r8152", "rtl815")):
        return "network"
    if "02" in classes or "0a" in classes or any(w in name for w in ("serial", "uart", "cp210", "ch34", "ft232", "pl2303", "arduino", "pico")):
        return "serial"
    return "gadget"


def _named(d):
    """What it calls itself, when that says something: "Cruzer Blade" (not "USB DISK", not 0781:5567)."""
    product = " ".join((d.get("product") or "").split())
    if not product or len(product) > 28 or product.lower() in ("usb", "usb device", "usb disk", "usb storage",
                                                                 "mass storage", "usb2.0 hub", "usb 2.0 hub", "usb hub"):
        return ""
    return product


def _join(parts):
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _some(devs, bare=False):
    """"a keyboard and two USB drives" (bare: "keyboard and two USB drives", for after "the")."""
    counts = {}
    for d in devs:
        k = kind_of(d)
        counts[k] = counts.get(k, 0) + 1
    out = []
    for k, n in counts.items():
        one, many, noun = KINDS[k]
        out.append((noun if bare else one) if n == 1 else f"{NUMBERS.get(n, str(n))} {many}")
    return _join(out)


def _under(child, hub):
    return child["bus"].startswith(hub["bus"] + ".")


def phrase(added):
    """What came, in a few words: "a hub, with a keyboard and a USB drive on it"; one by itself
    with its own name, when it has one: "a USB drive (Cruzer Blade)"."""
    hubs = [d for d in added if kind_of(d) == "hub"]
    top = [d for d in added if not any(_under(d, h) for h in hubs)]
    parts = []
    for d in top:
        if d in hubs:
            on = [c for c in added if _under(c, d) and kind_of(c) != "hub"]
            parts.append("a hub, with " + _some(on) + " on it" if on else "a hub")
    rest = [d for d in top if d not in hubs]
    if rest:
        if len(rest) == 1 and not parts and _named(rest[0]):
            parts.append(f"{KINDS[kind_of(rest[0])][0]} ({_named(rest[0])})")
        else:
            parts.append(_some(rest))
    return _join(parts)


def line_for(added, gone):
    """The bot's line for what came and went, and its kind (the face that goes with it:
    usb — wide-eyed, usb-drive — starry, usb-gone — sad)."""
    text, kind = "", "usb-gone"
    if added:
        text = f"Ooh — {phrase(added)}!"
        drive = any(kind_of(d) in ("drive", "reader") for d in added)
        kind = "usb-drive" if drive else "usb"
        if drive:
            text += " Let me look inside."
    if gone:
        hubs = [d for d in gone if kind_of(d) == "hub"]
        top = [d for d in gone if not any(_under(d, h) for h in hubs)]
        bits = []
        for h in [d for d in top if d in hubs]:
            on = [c for c in gone if _under(c, h) and kind_of(c) != "hub"]
            bits.append(("the hub's gone, and the " + _some(on, bare=True) + " with it") if on else "the hub's gone")
        rest = [d for d in top if d not in hubs]
        if rest:
            many = len(rest) > 1
            bits.append("the " + _some(rest, bare=True) + (" are gone" if many else "'s gone"))
        said = _join(bits)
        text = (text + " And " + said + ".") if text else ("Aw — " + said + ".")
    return text, kind


def _note(added, gone):
    """A few words for the Me screen's column."""
    if added:
        top = [d for d in added if not any(_under(d, h) for h in added if kind_of(h) == "hub" and h is not d)]
        first = KINDS[kind_of(top[0] if top else added[0])][0]
        return first[0].upper() + first[1:] + ("!" if len(added) == 1 else f" + {len(added) - 1}!")
    first = KINDS[kind_of(gone[0])][2]
    return f"Bye, {first}" + ("…" if len(gone) > 1 else "")


class Plugs:
    """Watches the bus; on_change(added, gone, text, kind) is called (on its thread) when something came or went."""

    def __init__(self):
        self.root = SYS_USB
        self.on_change = None
        self.known = None
        self.last = None               # {"text", "kind", "ts", "added", "gone"}
        self.mood, self.note, self.until = "", "", 0.0
        self._added, self._gone, self._settle = {}, {}, 0.0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plugs", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.look()
            except Exception:
                log.debug("plugs: looking at the bus", exc_info=True)
            self._stop.wait(EVERY)

    def look(self, now=None):
        """One look at the bus: the reaction, once a change has settled (else None)."""
        now = time.time() if now is None else now
        cur = {_key(d): d for d in devices(self.root)}
        with self._lock:
            if self.known is None:                       # what was there at the start isn't news
                self.known = cur
                return None
            came = {k: d for k, d in cur.items() if k not in self.known}
            went = {k: d for k, d in self.known.items() if k not in cur}
            self.known = cur
            if came or went:
                for k, d in came.items():
                    if k in self._gone:
                        del self._gone[k]                # dropped off and straight back: let be
                    else:
                        self._added[k] = d
                for k, d in went.items():
                    if k in self._added:
                        del self._added[k]               # came and went in a blink
                    else:
                        self._gone[k] = d
                self._settle = now + SETTLE
                return None
            if not (self._added or self._gone) or now < self._settle:
                return None
            added = sorted(self._added.values(), key=lambda d: d["bus"])
            gone = sorted(self._gone.values(), key=lambda d: d["bus"])
            self._added, self._gone = {}, {}
        return self._react(added, gone, now)

    def _react(self, added, gone, now):
        from .modes.simple import KIND_MOODS
        text, kind = line_for(added, gone)
        self.mood, self.note, self.until = KIND_MOODS.get(kind, "surprised"), _note(added, gone), now + FRESH
        self.last = {"text": text, "kind": kind, "ts": now, "added": added, "gone": gone}
        log.info("USB: %s", text)
        if self.on_change:
            try:
                self.on_change(added, gone, text, kind)
            except Exception:
                log.warning("plugs: passing it on", exc_info=True)
        return self.last

    def more(self, text, kind, note=None, now=None):
        """A second line about the same thing (what's on the drive): the face follows it a while longer."""
        from .modes.simple import KIND_MOODS
        now = time.time() if now is None else now
        self.mood, self.until = KIND_MOODS.get(kind, "happy"), now + FRESH
        if note:
            self.note = note
        self.last = {**(self.last or {}), "text": text, "kind": kind, "ts": now}

    def showing(self, now=None):
        """While it's news: {"face", "mood", "note", "text"} — the face for the screens that have one."""
        now = time.time() if now is None else now
        if now >= self.until or not self.mood:
            return None
        from .modes.simple import BOT_MOODS
        pair = BOT_MOODS.get(self.mood, BOT_MOODS["surprised"])
        return {"face": pair[int(now // 3) % 2], "mood": self.mood, "note": self.note,
                "text": (self.last or {}).get("text", "")}


PLUGS = Plugs()
