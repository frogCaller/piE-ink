"""System: what the Pi is up to, on the Pi's own screen."""
import functools
import os
import socket
import threading
import time

import psutil
from PIL import Image

from ..settings import ASSETS_DIR
from ..text import font, text_width
from .base import Mode


@functools.lru_cache(maxsize=16)
def icon(name, size):
    """Greyscale mask of assets/icons/<name>.png at the given size."""
    img = Image.open(os.path.join(ASSETS_DIR, "icons", f"{name}.png")).convert("RGBA")
    return img.getchannel("A").resize((size, size), Image.LANCZOS)

_sampler_lock = threading.Lock()
_sampler_started = False
_last = {"cpu": 0.0, "mhz": None, "mem_used": 0, "mem_total": 0, "mem_pct": 0.0,
         "disk_used": 0, "disk_total": 0, "disk_pct": 0.0, "temp": None,
         "load": (0.0, 0.0, 0.0), "ip": "n/a", "ts": 0}


def _cpu_mhz():
    try:   # the Pi reports its live clock here; psutil falls back to /proc/cpuinfo
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        freq = psutil.cpu_freq()
        return freq.current if freq else None


def _sample(interval=1.0):
    # a real one-second measurement window, not "since whoever asked last"
    _last["cpu"] = psutil.cpu_percent(interval=interval)
    _last["mhz"] = _cpu_mhz()
    vm = psutil.virtual_memory()
    _last.update(mem_used=vm.used, mem_total=vm.total, mem_pct=vm.percent)
    du = psutil.disk_usage("/")
    _last.update(disk_used=du.used, disk_total=du.total, disk_pct=du.percent)
    try:
        _last["load"] = os.getloadavg()
    except OSError:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            _last["temp"] = int(f.read()) / 1000
    except (OSError, ValueError):
        _last["temp"] = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        _last["ip"] = s.getsockname()[0]
        s.close()
    except OSError:
        _last["ip"] = "no network"
    _last["ts"] = time.time()


def _ensure_sampler():
    global _sampler_started
    with _sampler_lock:
        if _sampler_started:
            return
        _sampler_started = True

    def loop():
        while True:
            try:
                _sample()
            except Exception:
                pass
            time.sleep(4)
    threading.Thread(target=loop, daemon=True, name="sysinfo").start()


def _gb(n):
    return f"{n / 1e9:.1f}" if n < 100e9 else f"{n / 1e9:.0f}"


def _mb(n):
    return f"{n / 1e6:.0f}"


def _memory(used, total):
    """Megabytes on a small machine, gigabytes once there is a gigabyte or more."""
    if total >= 1e9:
        places = 2 if total < 10e9 else (1 if total < 100e9 else 0)
        return f"{used / 1e9:.{places}f}/{total / 1e9:.{places}f} GB"
    return f"{_mb(used)}/{_mb(total)} MB"


def _uptime():
    s = int(time.time() - psutil.boot_time())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m"


TEMP_MAX = 85.0      # the Pi throttles at 80-85 C; the temp bar is 0..85


class SystemMode(Mode):
    name = "system"
    label = "System"
    interval = 5.0

    def __init__(self, config, panel=None):
        super().__init__(config, panel)
        _ensure_sampler()
        if not _last["ts"]:
            _sample(interval=0.2)
        self._font12 = font(1, 12)
        self._label = font(8, 8)     # pixel font: crisp at small sizes
        self._value = font(2, 14)    # mono: figures line up

    @staticmethod
    def _bar(f, x, y, w, h, pct):
        """A track the full width of the cell, quarter ticks, and a fill whose
        length is the exact fraction of the track."""
        d, fg, bg = f.draw, f.fg, f.bg
        pct = max(0.0, min(100.0, float(pct)))
        d.rectangle([x, y, x + w, y + h], outline=fg, fill=bg, width=1)
        fill = (w - 2) * pct / 100.0
        if fill >= 0.5:
            d.rectangle([x + 1, y + 1, x + 1 + fill, y + h - 1], fill=fg)
        for q in (0.25, 0.5, 0.75):
            tx = x + w * q
            d.line([(tx, y + h + 1), (tx, y + h + 2)], fill=fg, width=1)

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        L = _last
        # type and icons grow with the panel (gently), and the grid fills it
        T = max(1.0, min(1.9, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.6))
        f12 = font(1, round(12 * T))
        label_f = font(8, round(8 * T))
        value_f = font(2, round(14 * T))
        hdr = round(14 * T)

        # header: hostname + IP
        d.text((3, 0), socket.gethostname(), font=f12, fill=fg)
        ip = L["ip"]
        d.text((f.width - 3 - text_width(d, ip, f12), 0), ip, font=f12, fill=fg)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        # 2x2 grid: icon + label on top, value below, thin bar for percentages
        mhz = L["mhz"]
        speed = f"{mhz / 1000:.1f}GHz" if mhz and mhz >= 1000 else (f"{mhz:.0f}MHz" if mhz else "")
        temp = f"{L['temp']:.1f}°C" if L["temp"] is not None else "n/a"
        temp_pct = None if L["temp"] is None else L["temp"] / TEMP_MAX * 100
        cells = [
            ("cpu", "CPU", f"{L['cpu']:.0f}%  {speed}".strip(), L["cpu"]),
            ("ram", "RAM", _memory(L["mem_used"], L["mem_total"]), L["mem_pct"]),
            ("temp", "TEMP", temp, temp_pct),
            ("storage", "DISK", f"{_gb(L['disk_used'])}/{_gb(L['disk_total'])} GB", L["disk_pct"]),
        ]
        top, bottom = hdr + 3, f.height - hdr
        cell_w, cell_h = f.width // 2, (bottom - top) // 2
        icon_px = round(18 * T)
        block = round(45 * T)                         # icon row + value + bar
        inner = max(0, (cell_h - block) // 2)
        pad_x = round(6 * T)
        for i, (name, label, value, pct) in enumerate(cells):
            x0 = (i % 2) * cell_w + pad_x
            y0 = top + (i // 2) * cell_h + inner
            f.stamp(icon(name, 64), (x0, y0 + 1), icon_px)
            d.text((x0 + icon_px + 6 * T, y0 + 6 * T), label, font=label_f, fill=fg)
            d.text((x0, y0 + 19 * T), value, font=value_f, fill=fg)
            if pct is not None:
                self._bar(f, x0, y0 + 36 * T, cell_w - 2 * pad_x - 2, round(5 * T), pct)
        d.line([(f.width // 2, top + 2), (f.width // 2, top + 2 * cell_h - 2)], fill=fg, width=1)
        d.line([(pad_x, top + cell_h - 1), (f.width - pad_x, top + cell_h - 1)], fill=fg, width=1)

        # footer
        d.line([(0, bottom), (f.width, bottom)], fill=fg, width=1)
        d.text((5, bottom + 1), f"up {_uptime()}", font=f12, fill=fg)
        clock = time.strftime("%-I:%M %p")
        d.text((f.width - 5 - text_width(d, clock, f12), bottom + 1), clock, font=f12, fill=fg)
        return f.image
