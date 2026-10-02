"""Watching the USB bus: what the Pi makes of a device the moment it's plugged in.

"Speaker or mic missing?" reads the kernel's log after the fact. This reads
it as it happens — the page asks every second or two for what's new since it
last asked — and says each line in plain words: "port 4 of the hub: something
plugged in (full speed)", "port 4 of the hub: it wouldn't answer (error -32:
no proper answer)", "port 4 of the hub: the Pi gave up on it". So you can plug
the speaker in, alone or beside the camera, and see from your phone whether
the Pi could bring it up, without a terminal.
"""
import re
import subprocess

from . import audio, camera

# what the kernel's error numbers mean here, in plain words
ERRORS = {32: "no proper answer from it", 71: "a garbled answer", 110: "no answer at all", 62: "it took too long",
          19: "it vanished", 5: "the link failed", 108: "it went away", 2: "not there"}
_TS = re.compile(r"^\[\s*(\d+\.\d+)\]\s*(.*)$")
_PATTERNS = [
    (re.compile(r"^usb (\S+): new (\w+)-speed USB device number (\d+) using (\w+)"),
     lambda m: (m.group(1), f"something plugged in ({m.group(2)} speed)")),
    (re.compile(r"^usb (\S+): New USB device found, idVendor=(\w+), idProduct=(\w+)"),
     lambda m: (m.group(1), f"came up ({m.group(2)}:{m.group(3)})")),
    (re.compile(r"^usb (\S+): Product: (.+)"), lambda m: (m.group(1), f"it's {m.group(2).strip()}")),
    (re.compile(r"^usb (\S+): device descriptor read/(\d+), error (-?\d+)"),
     lambda m: (m.group(1), f"it wouldn't answer (error {m.group(3)}: {_error(m.group(3))})")),
    (re.compile(r"^usb (\S+): device not accepting address \d+, error (-?\d+)"),
     lambda m: (m.group(1), f"it wouldn't take an address (error {m.group(2)}: {_error(m.group(2))})")),
    (re.compile(r"^usb (\S+): can't set config #\d+, error (-?\d+)"),
     lambda m: (m.group(1), f"it wouldn't take its settings (error {m.group(2)}: {_error(m.group(2))})")),
    (re.compile(r"^usb (\S+): reset (\w+)-speed USB device"), lambda m: (m.group(1), "reset")),
    (re.compile(r"^usb (\S+)-port(\d+): unable to enumerate USB device"),
     lambda m: (f"{m.group(1)}.{m.group(2)}", "the Pi gave up on it")),
    (re.compile(r"^usb (\S+)-port(\d+): attempt power cycle"),
     lambda m: (f"{m.group(1)}.{m.group(2)}", "its power switched off and on to try again")),
    (re.compile(r"^usb (\S+)-port(\d+): over-current"), lambda m: (f"{m.group(1)}.{m.group(2)}", "over-current")),
    (re.compile(r"^usb (\S+): USB disconnect"), lambda m: (m.group(1), "unplugged, or dropped off")),
    (re.compile(r"^hub (\S+?):[\d.]+: port (\d+) disabled by hub \(EMI\?\)"),
     lambda m: (f"{m.group(1)}.{m.group(2)}", "switched off by the hub for interference, then on again")),
    (re.compile(r"^cdc_acm (\S+):[\d.]+: (ttyACM\d+): USB ACM device"),
     lambda m: (m.group(1), f"a serial port, /dev/{m.group(2)} (a GPS receiver talks on one)")),
    (re.compile(r"^(?:usb )?(\S+):[\d.]+: (\d+:\d+): cannot get (?:freq|min/max values|ctl value)"),
     lambda m: (m.group(1), "the sound driver couldn't read one of its settings (usually harmless)")),
]


def _error(code):
    try:
        return ERRORS.get(abs(int(code)), "an error")
    except ValueError:
        return "an error"


def where(bus):
    """"1-1.4" -> "port 4 of the hub"; "1-1" -> "the Pi's port" (or "the hub" when a hub sits there)."""
    if not bus:
        return ""
    if "." in bus:
        head, _, port = bus.rpartition(".")
        deeper = f" (the hub on port {head.rpartition('.')[2]})" if "." in head else ""
        return f"port {port} of the hub{deeper}"
    return "the Pi's port"


def _kernel_raw():
    """dmesg with its own timestamps (seconds since boot): [lines], or None when it isn't readable."""
    for cmd in (["dmesg"], ["journalctl", "-k", "-n", "600", "--no-pager", "-o", "short-monotonic"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return r.stdout.splitlines()
    return None


def _parse(line):
    """(timestamp, message) from a dmesg line, or None."""
    m = _TS.match(line)
    if not m:
        return None
    return float(m.group(1)), m.group(2).strip()


def read(msg):
    """One kernel line in plain words: {"port", "where", "text"}, or None for one that's not about USB."""
    for pattern, say in _PATTERNS:
        m = pattern.match(msg)
        if m:
            port, text = say(m)
            return {"port": port, "where": where(port), "text": text}
    low = msg.lower()
    if "undervoltage detected" in low or "under-voltage detected" in low:
        return {"port": "", "where": "", "text": "the Pi's own power sagged (under-voltage)"}
    if "voltage normalised" in low or "voltage normalized" in low:
        return {"port": "", "where": "", "text": "the Pi's power is back to normal"}
    if "snd-usb-audio" in low or "snd_usb_audio" in low:
        return {"port": "", "where": "", "text": "the USB sound driver is loaded"}
    if low.startswith(("usb ", "hub ", "usbcore", "cdc_acm", "usb-storage", "uvcvideo")) or "over-current" in low:
        return {"port": "", "where": "", "text": msg}
    return None


def events(since=-1.0):
    """What happened on the USB bus since `since` (a kernel timestamp), in plain
    words, and the bus as it is now. since -1: nothing old, just where the
    log is now, so the page sees only what happens from here on."""
    lines = _kernel_raw()
    out, latest = [], since
    if lines is None:
        note = "the kernel log isn't readable by this user (add it to the adm group, or run sudo dmesg)"
    else:
        note = None
        for line in lines:
            got = _parse(line)
            if got is None:
                continue
            ts, msg = got
            latest = max(latest, ts)
            if since < 0 or ts <= since:
                continue
            said = read(msg)
            if said is not None:
                out.append({"ts": ts, **said, "raw": msg})
    devices = camera.usb_devices()
    by_id = {f"{u['vid']}:{u['pid']}": u for u in devices}
    cards = []
    for c in audio.sound_cards():
        if c["driver"] != "USB-Audio":
            continue
        dev = by_id.get((c["usb"] or "").lower())
        cards.append({"id": c["id"], "name": c["name"], "camera": bool(dev and "video" in dev["classes"])})
    return {"now": latest, "events": out, "note": note,
            "devices": [{"bus": u["bus"], "name": u["name"], "classes": u["classes"], "where": where(u["bus"])}
                        for u in devices],
            "cards": cards}
