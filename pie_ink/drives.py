"""USB drives: the ones plugged in and mounted, and what's on them — so the shelves (pie_ink/shelf.py)
find a photo, a song or a PDF on a USB stick the way they find one in data/.

A drive counts when it's a disk on the USB bus (by where it sits under /sys, straight into the Pi
or through a hub) and it's mounted somewhere: by setup.sh's rule (read-only, at /media/sda1 and the
like — pie_ink/usb-mount.sh), by a desktop (/media/<you>/<label>), or by hand. The disk the Pi
itself runs from (when it boots off USB) isn't one. Each is looked through once (a few folders
deep, a few thousand files at most, the system folders skipped) and then again only after a few
minutes, or when it's been taken out and put back.
"""
import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

MOUNTS = "/proc/mounts"
SYS_BLOCK = "/sys/class/block"
SYS_USB = "/sys/bus/usb/devices"
BY_LABEL = "/dev/disk/by-label"
OWN = ("/", "/boot", "/boot/firmware")      # the Pi's own disk is mounted here
KEEP_FOR = 300                 # seconds a drive's listing is good for
DEPTH = 6                      # folders within folders, this deep
MOST = 4000                    # files per drive, at most
TAKES = 8.0                    # seconds spent looking through one drive, at most
SKIP = {"system volume information", "lost+found", "lost.dir", "found.000", "android", "windows", "program files",
        "program files (x86)", "programdata", "$recycle.bin", "recycler"}
_USB_ID = re.compile(r"^\d+-[\d.]+$")        # 1-1, 1-1.3: a device's place on the bus

_cache = {}                    # (device, mount point, exts) -> {"at", "files"}
_lock = threading.Lock()


def _unescape(path):
    """/proc/mounts writes a space as \\040 (and a tab, a newline, a backslash the same way)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def _name(dev):
    """/dev/sda1, /dev/disk/by-uuid/… -> sda1."""
    return os.path.basename(os.path.realpath(dev))


def bus_of(name):
    """Where a disk (or a partition of one) sits on the USB bus — "1-1.3" — or "" when it isn't a USB one."""
    real = os.path.realpath(os.path.join(SYS_BLOCK, name))
    parts = real.split(os.sep)
    if not any(re.fullmatch(r"usb\d+", p) for p in parts):
        return ""
    ids = [p for p in parts if _USB_ID.match(p)]
    return ids[-1] if ids else ""


def label_of(name):
    """The filesystem's own name (as udev lists it under /dev/disk/by-label), or ""."""
    try:
        entries = os.listdir(BY_LABEL)
    except OSError:
        return ""
    for e in entries:
        if os.path.basename(os.path.realpath(os.path.join(BY_LABEL, e))) == name:
            return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), e).strip()
    return ""


def product_of(bus):
    """What the USB device calls itself: "SanDisk Cruzer Blade"."""
    def read(what):
        try:
            with open(os.path.join(SYS_USB, bus, what), encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""
    return " ".join(x for x in (read("manufacturer"), read("product")) if x)


def _rows():
    try:
        with open(MOUNTS, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 4 and parts[0].startswith("/dev/"):
            out.append((parts[0], _unescape(parts[1]), parts[2], parts[3].split(",")))
    return out


def mounted():
    """The USB drives mounted now, one per filesystem: [{"dev", "name", "path", "label", "fs", "bus", "ro"}]."""
    rows = _rows()
    own = {bus_of(_name(dev)) for dev, where, _fs, _o in rows if where in OWN} - {""}
    out, seen = [], set()
    for dev, where, fs, opts in rows:
        name = _name(dev)
        if name in seen or where in OWN:
            continue
        bus = bus_of(name)
        if not bus or bus in own:
            continue
        seen.add(name)
        label = label_of(name)
        if not label and not re.fullmatch(r"(sd[a-z]+\d*|usb.*|disk\d*)", os.path.basename(where)):
            label = os.path.basename(where)                     # /media/pi/HOLIDAY: its folder says
        out.append({"dev": "/dev/" + name, "name": name, "path": where, "label": label, "fs": fs, "bus": bus,
                    "ro": "ro" in opts})
    return out


def title(d):
    """What to call it: its label, or what the stick calls itself, or ""."""
    return d.get("label") or product_of(d.get("bus", "")) or ""


def disks_on(bus):
    """The disks and partitions the kernel has for the USB device at `bus` (and anything on a hub there),
    mounted or not: a drive that went in but that nothing mounted still shows up here."""
    try:
        names = sorted(os.listdir(SYS_BLOCK))
    except OSError:
        return []
    out = []
    for n in names:
        b = bus_of(n)
        if b and (b == bus or b.startswith(bus + ".")):
            out.append(n)
    return out


def files(d, exts=(), fresh=False):
    """[(path within the drive, size, modified)] on one mounted drive — only files ending in `exts`,
    when given — a few folders deep, hidden and system folders skipped. Kept a few minutes."""
    key = (d["dev"], d["path"], tuple(exts))
    with _lock:
        got = _cache.get(key)
    if got is not None and not fresh and time.time() - got["at"] < KEEP_FOR:
        return got["files"]
    root, out, started = d["path"], [], time.time()
    stack = [(root, 0)]
    while stack and len(out) < MOST and time.time() - started < TAKES:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        subs = []
        for e in entries:
            low = e.name.lower()
            if e.name.startswith((".", "$", "~")) or low in SKIP:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < DEPTH:
                        subs.append(e.path)
                elif e.is_file(follow_symlinks=False) and (not exts or low.endswith(tuple(exts))):
                    st = e.stat(follow_symlinks=False)
                    out.append((os.path.relpath(e.path, root), st.st_size, st.st_mtime))
                    if len(out) >= MOST:
                        break
            except OSError:
                continue
        stack.extend((s, depth + 1) for s in reversed(subs))
    still = {(m["dev"], m["path"]) for m in mounted()}
    with _lock:
        for k in [k for k in _cache if k[:2] not in still]:
            del _cache[k]                                   # (ones taken out since)
        _cache[key] = {"at": time.time(), "files": out}
    log.info("USB drive %s (%s): %d files", title(d) or d["name"], d["path"], len(out))
    return out


def path_in(root, rel):
    """A file on a drive by its path within it — never outside it."""
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        raise FileNotFoundError("that isn't on the drive")
    return full
