"""The GPi Case 2W's buttons — or any game controller — as PiE-ink's keys.

The case's d-pad and buttons are a USB game controller inside it, so they're read the way any
controller is: the Linux input events from /dev/input (the user needs to be in the input group;
setup.sh adds it). Nothing to install: each device is asked what it is (its name and its buttons,
the EVIOCGNAME and EVIOCGBIT ioctls — the same answer on a 32-bit Pi OS and a 64-bit one), and
the events are read as they come, no evdev package.

Each press goes out as the button's name ("a", "left", "start"...); what it does is the screen's
(pie_ink/pad.py: each screen has buttons of its own, shown along the bottom of the case's screen).
A controller whose buttons were set up in Pietato (its Settings > Controller, kept in
/var/lib/pitato/pads.json) is read that way here too, so a case that sends something unusual for
a button needs setting up once, not twice.

A controller plugged in later is found within a few seconds; one unplugged is let go.

    python3 -m pie_ink.gamepad      # what's plugged in, and each button as it's pressed
"""
import errno
import fcntl
import json
import logging
import os
import re
import select
import struct
import sys
import threading
import time

log = logging.getLogger(__name__)

INPUT_DIR = "/dev/input"
EV_KEY, EV_ABS = 0x01, 0x03
EVENT = struct.Struct("llHHi")                       # struct input_event, the way this Pi's longs are
SCAN_EVERY = 3.0
NOT_PADS = ("motion sensors", "touchpad", "accelerometer", "consumer control", "system control")


def _ior(nr, size):
    """An evdev ioctl that reads `size` bytes: _IOR('E', nr, size)."""
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


KEY_BYTES = 0x300 // 8                                # KEY_MAX (0x2ff) + 1 bits
ABS_BYTES = 0x40 // 8                                 # ABS_MAX (0x3f) + 1 bits
EVIOCGNAME = _ior(0x06, 256)
EVIOCGID = _ior(0x02, 8)                              # struct input_id: bus, vendor, product, version
EVIOCGBIT_KEY = _ior(0x20 + EV_KEY, KEY_BYTES)
EVIOCGBIT_ABS = _ior(0x20 + EV_ABS, ABS_BYTES)


def EVIOCGABS(axis):
    return _ior(0x40 + axis, 24)                      # struct input_absinfo: six ints


# a standard pad (the GPi Case's, Xbox, PlayStation, 8BitDo...)
CODES = {0x130: "a", 0x131: "b", 0x133: "x", 0x134: "y", 0x136: "l", 0x137: "r", 0x138: "l2", 0x139: "r2",
         0x13a: "select", 0x13b: "start", 0x13c: "home",
         0x220: "up", 0x221: "down", 0x222: "left", 0x223: "right",
         0x2c0: "left", 0x2c1: "right", 0x2c2: "up", 0x2c3: "down"}       # (an Xbox-style pad with its d-pad as buttons)
PAD_KEYS = {0x130, 0x131, 0x13a, 0x13b, 0x220}       # any of these: a standard pad
# a plain USB pad that numbers its buttons (most SNES-style ones)
JOY_CODES = {0x120: "x", 0x121: "a", 0x122: "b", 0x123: "y", 0x124: "l", 0x125: "r", 0x126: "l", 0x127: "r",
             0x128: "select", 0x129: "start"}
AXES = {0x10: ("left", "right"), 0x11: ("up", "down"), 0x00: ("left", "right"), 0x01: ("up", "down")}   # hat, then stick
# L and R as triggers that go from let go to pressed (an Xbox-style pad's: Z and RZ beside a right stick), or a
# pad's brake and gas
TRIGGERS = {0x02: "l2", 0x05: "r2", 0x0a: "l2", 0x09: "r2"}
BUTTONS = ("up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "l2", "r2", "select", "start", "home")
NO_ACCESS = "can't read the controllers in /dev/input: add yourself to the input group (setup.sh does), then restart PiE-ink"
PITATO_PADS = "/var/lib/pitato/pads.json"
AB = {"a": "b", "b": "a"}


def ident(fd, name):
    """A controller's model, the way Pietato keys its buttons: "vendor:product:name"."""
    try:
        _bus, vendor, product, _version = struct.unpack("4H", fcntl.ioctl(fd, EVIOCGID, bytes(8)))
    except OSError:
        return name
    return f"{vendor:04x}:{product:04x}:{name}"


def learned(model, path=None):
    """The buttons set up for this model of controller in Pietato, if they were:
    {"keys": {code: button}, "axes": {axis: {-1/1: button}}, "swap": bool} — or None."""
    try:
        with open(path or PITATO_PADS) as f:
            data = json.load(f)
        entry = data.get(model) if isinstance(data, dict) else None
        if not isinstance(entry, dict):
            return None
        keys = {int(c): n for c, n in (entry.get("keys") or {}).items() if n in BUTTONS}
        axes = {int(c): {int(w): n for w, n in dirs.items() if n in BUTTONS and int(w) in (-1, 1)}
                for c, dirs in (entry.get("axes") or {}).items()}
        swap = bool(entry.get("swap"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not keys and not axes and not swap:
        return None
    return {"keys": keys, "axes": {c: dirs for c, dirs in axes.items() if dirs}, "swap": swap}


def key_bits(raw):
    """A capability bitmap, the bytes EVIOCGBIT gives, as the set of codes in it. Read as bytes rather
    than as the kernel's longs, it's the same on a 32-bit Pi OS as on a 64-bit one."""
    return {i * 8 + b for i, byte in enumerate(raw) for b in range(8) if byte >> b & 1}


def _caps(fd):
    """(name, the button codes it has) of an open input device, asked of the device itself."""
    name = fcntl.ioctl(fd, EVIOCGNAME, bytes(256)).split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return name, key_bits(fcntl.ioctl(fd, EVIOCGBIT_KEY, bytes(KEY_BYTES)))


def _axes(fd):
    """The axes an open input device has (EVIOCGBIT for EV_ABS); none when it won't say."""
    try:
        return key_bits(fcntl.ioctl(fd, EVIOCGBIT_ABS, bytes(ABS_BYTES)))
    except OSError:
        return set()


def triggers(axes):
    """Its axes that are triggers: Z and RZ only beside a right stick (RX, RY) — on a plain USB pad they're
    the right stick itself — and brake and gas wherever they are."""
    out = {c: n for c, n in TRIGGERS.items() if c in (0x09, 0x0a) and c in axes}
    if {0x02, 0x05, 0x03, 0x04} <= axes:
        out.update({0x02: "l2", 0x05: "r2"})
    return out


def kind(name, keys):
    """"pad" for a standard layout, "joy" for a plain USB pad that numbers its buttons, None for
    keyboards, mice, the power button and the rest."""
    if any(w in (name or "").lower() for w in NOT_PADS):
        return None
    if keys & PAD_KEYS:
        return "pad"
    if any(0x120 <= k <= 0x12f for k in keys):
        return "joy"
    return None


def event_paths(folder=None):
    """/dev/input/event0, event1...: every input device, in order."""
    folder = folder or INPUT_DIR
    try:
        names = [n for n in os.listdir(folder) if re.fullmatch(r"event\d+", n)]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, key=lambda n: int(n[5:]))]


def look(path):
    """What an input device is: {"name", "event", "kind", "keys"}; {"error"} when it can't be opened."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        return {"event": path, "error": e.errno}
    try:
        name, keys = _caps(fd)
    except OSError as e:
        return {"event": path, "error": e.errno}
    finally:
        os.close(fd)
    return {"name": name, "event": path, "kind": kind(name, keys), "keys": keys}


def controllers(folder=None):
    """The game controllers plugged in: [{"name", "event": "/dev/input/eventN", "joy": bool}]."""
    out = []
    for path in event_paths(folder):
        d = look(path)
        if d.get("kind"):
            out.append({"name": d["name"], "event": path, "joy": d["kind"] == "joy"})
    return out


def _absinfo(fd, axis):
    """(minimum, maximum) of an axis (EVIOCGABS)."""
    try:
        data = fcntl.ioctl(fd, EVIOCGABS(axis), bytes(24))
        _value, lo, hi, _fuzz, _flat, _res = struct.unpack("6i", data)
        return lo, hi
    except OSError:
        return -1, 1


class Pads:
    """Every controller plugged in, read on a thread of its own: each press, the button's name."""

    def __init__(self):
        self.on_press = None               # (button) -> what it did, in a few words (pie_ink/pad.py)
        self.on_other = None               # (code) -> None: a button pressed that isn't one of these
        self._thread = None
        self._stop = threading.Event()
        self.found = []                    # the controllers being read, by name
        self.last = None                   # (button, what it did, when): for the page
        self.problem = ""                  # why none can be read, when that's it

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running():
            if not self._stop.is_set():
                return True
            self._thread.join(SCAN_EVERY + 2)              # (told to stop a moment ago: let it finish first)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gamepad")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def press(self, button):
        did = ""
        if self.on_press:
            try:
                did = self.on_press(button) or ""
            except Exception:
                log.exception("the %s button", button)
        self.last = (button, did, time.time())

    def _scan(self, open_, passed):
        """Controllers plugged in since the last look, opened and added to open_ (fd -> its state). Devices
        that aren't controllers are remembered (by node) and not asked again."""
        have = {d["path"] for d in open_.values()}
        denied = False
        for path in event_paths():
            if path in have:
                continue
            try:
                node = os.stat(path).st_ino
            except OSError:
                continue
            if passed.get(path) == node:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                denied = denied or e.errno in (errno.EACCES, errno.EPERM)
                continue
            try:
                name, keys = _caps(fd)
                k = kind(name, keys)
            except OSError:
                k = None
            if not k:
                os.close(fd)
                passed[path] = node
                continue
            mine = learned(ident(fd, name))
            open_[fd] = {"path": path, "name": name, "joy": k == "joy", "axes": {}, "held": {}, "triggers": triggers(_axes(fd)),
                         "learned": mine}
            log.info("buttons: %s (%s)%s", name, path, " — with the buttons set up in Pietato" if mine else "")
        for path in list(passed):
            if not os.path.exists(path):
                del passed[path]
        problem = NO_ACCESS if denied and not open_ else ""
        if problem and problem != self.problem:
            log.warning(problem)
        self.problem = problem
        self.found = [d["name"] for d in open_.values()]

    def _loop(self):
        open_ = {}                          # fd -> {"path", "name", "joy", "axes": {axis: (lo, hi)}, "held": {axis: dir}}
        passed = {}                         # path -> its node: looked at, not a controller
        scanned = 0.0
        try:
            while not self._stop.is_set():
                if time.time() - scanned >= SCAN_EVERY:
                    scanned = time.time()
                    self._scan(open_, passed)
                if not open_:
                    self._stop.wait(SCAN_EVERY)
                    continue
                ready, _, _ = select.select(list(open_), [], [], 1.0)
                for fd in ready:
                    d = open_[fd]
                    try:
                        data = os.read(fd, EVENT.size * 32)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:
                        os.close(fd)
                        del open_[fd]                   # unplugged
                        log.info("buttons: %s unplugged", d["name"])
                        self.found = [x["name"] for x in open_.values()]
                        continue
                    for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                        _s, _us, typ, code, value = EVENT.unpack_from(data, off)
                        self._event(fd, d, typ, code, value)
        finally:
            for fd in open_:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.found = []

    def _button(self, d, code):
        """The button a code is on this controller: as set up in Pietato, if it was (the rest keep
        their usual codes), else the usual."""
        mine = d.get("learned")
        if mine and code in mine["keys"]:
            button = mine["keys"][code]
        else:
            button = (JOY_CODES.get(code) if d["joy"] else None) or CODES.get(code)
            if mine and button and button in mine["keys"].values():
                button = None                               # (that one's on another code on this controller)
        if mine and mine["swap"]:
            button = AB.get(button, button)
        return button

    def _event(self, fd, d, typ, code, value):
        mine = d.get("learned")
        if typ == EV_KEY and value == 1:                    # pressed (not held, not let go)
            button = self._button(d, code)
            if button:
                self.press(button)
            elif self.on_other:
                self.on_other(code)
        elif typ == EV_ABS and mine and code in mine["axes"]:
            if code not in d["axes"]:
                d["axes"][code] = _absinfo(fd, code)
            lo, hi = d["axes"][code]
            mid, span = (lo + hi) / 2, max(1, (hi - lo) / 2)
            way = -1 if value < mid - span * 0.5 else 1 if value > mid + span * 0.5 else 0
            if way and d["held"].get(code) != way and mine["axes"][code].get(way):
                button = mine["axes"][code][way]
                self.press(AB.get(button, button) if mine["swap"] else button)
            d["held"][code] = way
        elif typ == EV_ABS and code in d.get("triggers", {}):
            if code not in d["axes"]:
                d["axes"][code] = _absinfo(fd, code)
            lo, hi = d["axes"][code]
            on = value > lo + (hi - lo) / 2
            if on and not d["held"].get(code):
                self.press(d["triggers"][code])
            d["held"][code] = on
        elif typ == EV_ABS and code in AXES:
            if code not in d["axes"]:
                d["axes"][code] = _absinfo(fd, code)
            lo, hi = d["axes"][code]
            mid, span = (lo + hi) / 2, max(1, (hi - lo) / 2)
            way = -1 if value < mid - span * 0.5 else 1 if value > mid + span * 0.5 else 0
            if way and d["held"].get(code) != way:
                self.press(AXES[code][0 if way < 0 else 1])
            d["held"][code] = way


PADS = Pads()


def main():
    """What's plugged in, and each button as it's pressed (Ctrl+C to stop). Fine while PiE-ink runs:
    both read the same presses."""
    paths = event_paths()
    if not paths:
        print(f"Nothing in {INPUT_DIR}: no input devices at all.")
        return 1
    pads = 0
    for path in paths:
        d = look(path)
        if "error" in d:
            why = "no permission (not in the input group? `groups` to see)" if d["error"] in (errno.EACCES, errno.EPERM) \
                else os.strerror(d["error"])
            print(f"  {path:20} can't open it: {why}")
            continue
        what = {"pad": "a game controller", "joy": "a game controller (numbered buttons)"}.get(d["kind"], "not a controller")
        pads += bool(d["kind"])
        print(f"  {path:20} {d['name']!r}: {what}")
    if not pads:
        print("No game controller found.")
        return 1
    print("Press the buttons (Ctrl+C to stop):")
    reader = Pads()
    reader.on_press = lambda button: print(f"  {button.upper()}", flush=True)
    reader.on_other = lambda code: print(f"  a button PiE-ink doesn't know: code {code:#x}", flush=True)
    reader.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        reader.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
