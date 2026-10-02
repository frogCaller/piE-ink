"""A second screen: a Waveshare SPI LCD on the ST7789V2 — the 1.69"
(240x280) or the 1.9" (170x320), both colour.

It shares SPI0 with the e-ink but uses the other chip-select, so the two
never interfere: the e-ink stays on CE0 (/dev/spidev0.0) and the LCD goes on
CE1 (/dev/spidev0.1) with its own DC, reset and backlight pins. The e-ink
HAT occupies the header, so the LCD is wired with jumpers — and the Waveshare
default wiring (DC on 25, CE0, backlight on 18) clashes with the HAT, which
uses 25, CE0 and 18 (its PWR pin) itself; that is why the pins here default
to DC 22 / RST 27 / BL 12 / CE1. Both panels wire the same way.

The controller has 240x320 of RAM and each panel shows a window of it: the
1.69" the middle 280 rows, the 1.9" the middle 170 columns — the offsets in
MODELS. The frame is built portrait, rotated in software if you mount the
screen another way, and sent as RGB565. Nothing here raises on a missing
library or an unplugged screen: the loop keeps trying quietly.
"""
import logging
import threading
import time


log = logging.getLogger(__name__)

CHUNK = 4096

# each panel's own init sequence: (command, [data...]) — Waveshare's, per panel
INIT_1IN69 = [
    (0x36, [0x00]),
    (0x3A, [0x05]),                                   # RGB565
    (0xB2, [0x0B, 0x0B, 0x00, 0x33, 0x35]),
    (0xB7, [0x11]),
    (0xBB, [0x35]),
    (0xC0, [0x2C]),
    (0xC2, [0x01]),
    (0xC3, [0x0D]),
    (0xC4, [0x20]),
    (0xC6, [0x13]),
    (0xD0, [0xA4, 0xA1]),
    (0xD6, [0xA1]),
    (0xE0, [0xF0, 0x06, 0x0B, 0x0A, 0x09, 0x26, 0x29, 0x33, 0x41, 0x18, 0x16, 0x15, 0x29, 0x2D]),
    (0xE1, [0xF0, 0x04, 0x08, 0x08, 0x07, 0x03, 0x28, 0x32, 0x40, 0x3B, 0x19, 0x18, 0x2A, 0x2E]),
    (0xE4, [0x25, 0x00, 0x00]),
    (0x21, []),                                       # inversion on (these IPS panels want it)
    (0x11, []),                                       # sleep out
    (0x29, []),                                       # display on
]
INIT_1IN9 = [
    (0x36, [0x00]),
    (0x3A, [0x05]),                                   # RGB565
    (0xB2, [0x0C, 0x0C, 0x00, 0x33, 0x33]),
    (0xB7, [0x35]),
    (0xBB, [0x35]),
    (0xC0, [0x2C]),
    (0xC2, [0x01]),
    (0xC3, [0x13]),
    (0xC4, [0x20]),
    (0xC6, [0x0F]),
    (0xD0, [0xA4, 0xA1]),
    (0xD6, [0xA1]),
    (0xE0, [0xF0, 0x00, 0x04, 0x04, 0x04, 0x05, 0x29, 0x33, 0x3E, 0x38, 0x12, 0x12, 0x28, 0x30]),
    (0xE1, [0xF0, 0x07, 0x0A, 0x0D, 0x0B, 0x07, 0x28, 0x33, 0x3E, 0x36, 0x14, 0x14, 0x29, 0x32]),
    (0x21, []),
    (0x11, []),
    (0x29, []),
]

MODELS = {
    "1.69": {"label": "1.69\" · 240×280", "width": 240, "height": 280, "x_offset": 0, "y_offset": 20, "init": INIT_1IN69},
    "1.9": {"label": "1.9\" · 170×320", "width": 170, "height": 320, "x_offset": 35, "y_offset": 0, "init": INIT_1IN9},
}
DEFAULT_MODEL = "1.69"


def model(name):
    return MODELS.get(str(name), MODELS[DEFAULT_MODEL])


def panel_size(conf):
    """(width, height) of the chosen panel, portrait."""
    m = model(conf.get("model", DEFAULT_MODEL))
    return m["width"], m["height"]


def models():
    return [{"id": k, "label": v["label"], "width": v["width"], "height": v["height"]} for k, v in MODELS.items()]


def rgb565(image):
    """An RGB image as big-endian RGB565 bytes, the way the panel wants them."""
    image = image.convert("RGB")
    try:
        import numpy as np
        px = np.asarray(image, dtype=np.uint16)
        v = ((px[..., 0] & 0xF8) << 8) | ((px[..., 1] & 0xFC) << 3) | (px[..., 2] >> 3)
        return v.astype(">u2").tobytes()
    except ImportError:                               # slower, but numpy is on every Pi anyway
        raw = image.tobytes()
        out = bytearray(len(raw) // 3 * 2)
        j = 0
        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            out[j] = (r & 0xF8) | (g >> 5)
            out[j + 1] = ((g & 0x1C) << 3) | (b >> 3)
            j += 2
        return bytes(out)


class ST7789:
    """The real thing, through spidev and lgpio."""

    def __init__(self, model_name=DEFAULT_MODEL, cs=1, dc=22, rst=27, bl=12, bus=0, speed=40_000_000):
        self.model = model(model_name)
        self.size = (self.model["width"], self.model["height"])
        self.cs, self.dc, self.rst, self.bl, self.bus, self.speed = cs, dc, rst, bl, bus, speed
        self.spi = None
        self.chip = None
        self._lgpio = None
        self.name = f"{self.model['label'].split(' ')[0]} LCD on CE{cs}"

    def open(self):
        import lgpio
        import spidev
        from . import gpio
        self._lgpio = lgpio
        self.chip = gpio.open_header_chip(lgpio)
        for pin in (self.dc, self.rst, self.bl):
            lgpio.gpio_claim_output(self.chip, pin)
        self.spi = spidev.SpiDev()
        self.spi.open(self.bus, self.cs)
        self.spi.max_speed_hz = self.speed
        self.spi.mode = 0
        self._reset()
        for cmd, data in self.model["init"]:
            self._command(cmd)
            if data:
                self._data(bytes(data))
            if cmd == 0x11:
                time.sleep(0.12)
        self.brightness(100)

    def _reset(self):
        g = self._lgpio
        g.gpio_write(self.chip, self.rst, 1)
        time.sleep(0.01)
        g.gpio_write(self.chip, self.rst, 0)
        time.sleep(0.01)
        g.gpio_write(self.chip, self.rst, 1)
        time.sleep(0.12)

    def _command(self, cmd):
        self._lgpio.gpio_write(self.chip, self.dc, 0)
        self.spi.writebytes([cmd])

    def _data(self, data):
        self._lgpio.gpio_write(self.chip, self.dc, 1)
        for i in range(0, len(data), CHUNK):
            self.spi.writebytes2(data[i:i + CHUNK])

    def _window(self, x0, y0, x1, y1):
        x0, x1 = x0 + self.model["x_offset"], x1 + self.model["x_offset"]
        y0, y1 = y0 + self.model["y_offset"], y1 + self.model["y_offset"]
        self._command(0x2A)
        self._data(bytes([x0 >> 8, x0 & 0xFF, (x1 - 1) >> 8, (x1 - 1) & 0xFF]))
        self._command(0x2B)
        self._data(bytes([y0 >> 8, y0 & 0xFF, (y1 - 1) >> 8, (y1 - 1) & 0xFF]))
        self._command(0x2C)

    def show(self, image):
        """An RGB image the panel's size, portrait."""
        if image.size != self.size:
            image = image.resize(self.size)
        self._window(0, 0, *self.size)
        self._data(rgb565(image))

    def brightness(self, percent):
        percent = max(0, min(100, int(percent)))
        try:
            self._lgpio.tx_pwm(self.chip, self.bl, 1000, percent)
        except Exception:
            self._lgpio.gpio_write(self.chip, self.bl, 1 if percent > 0 else 0)

    def close(self):
        try:
            if self._lgpio and self.chip is not None:
                self.brightness(0)
                self._command(0x28)                   # display off
                for pin in (self.dc, self.rst, self.bl):
                    try:
                        self._lgpio.gpio_free(self.chip, pin)
                    except Exception:
                        pass
                self._lgpio.gpiochip_close(self.chip)
        except Exception:
            pass
        try:
            if self.spi:
                self.spi.close()
        except Exception:
            pass
        self.spi = self.chip = None


class MockLCD:
    """No hardware: keeps the last frame so the page can preview it."""
    name = "mock LCD"

    def __init__(self, model_name=DEFAULT_MODEL, **_):
        self.model = model(model_name)
        self.size = (self.model["width"], self.model["height"])
        self.last = None
        self.level = 100

    def open(self):
        pass

    def show(self, image):
        self.last = image.copy()

    def brightness(self, percent):
        self.level = percent

    def close(self):
        pass


class Second:
    """The LCD's own loop: draws the chosen screen a few times a second on a
    thread of its own, so the e-ink never waits on it and vice versa."""

    def __init__(self):
        self.panel = None
        self.thread = None
        self.stop_flag = threading.Event()
        self.wake = threading.Event()
        self.conf = {}
        self.render = None               # (conf) -> RGB image, portrait
        self.interval = None             # (conf) -> seconds between frames; None = 1/fps
        self.error = None
        self.frames = 0
        self._last_bytes = None
        self._last_image = None
        self._lock = threading.Lock()

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, conf, render, driver=None):
        self.conf = conf or {}
        self.render = render
        if not self.conf.get("enabled"):
            self.stop()
            return False, "the second screen is switched off"
        if self.running():
            self.wake.set()
            return True, "already running"
        self._driver = driver or self.conf.get("driver", "st7789")
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="lcd")
        self.thread.start()
        return True, "running"

    def stop(self):
        self.stop_flag.set()
        self.wake.set()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=3)

    def update(self, conf):
        """New settings: brightness and screen take effect at once; a pin
        change restarts the driver."""
        old, self.conf = self.conf, conf or {}
        if not self.conf.get("enabled"):
            self.stop()
            return
        pins_changed = any(old.get(k) != self.conf.get(k) for k in ("cs", "dc", "rst", "bl", "driver", "model", "speed_hz"))
        if pins_changed and self.running():
            self.stop()
            if self.running():                    # the old loop is stuck in the driver: leave it be
                self.error = "the screen didn't let go of the SPI bus — restart pie-ink for the new pins"
                log.warning("lcd: %s", self.error)
                return
            self._driver = self.conf.get("driver", "st7789")
            self._last_bytes = None
            self.stop_flag.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name="lcd")
            self.thread.start()
        elif self.running():
            self.wake.set()
        else:
            self.start(self.conf, self.render)

    def status(self):
        return {"running": self.running(), "error": self.error, "frames": self.frames,
                "panel": getattr(self.panel, "name", None), "screen": self.conf.get("screen", "system"),
                "model": self.conf.get("model", DEFAULT_MODEL), "size": list(panel_size(self.conf)),
                "speed_hz": int(self.conf.get("speed_hz", 40_000_000))}

    def preview(self):
        with self._lock:
            return self._last_image.copy() if self._last_image is not None else None

    def _open(self):
        conf = self.conf
        pins = {k: int(conf.get(k, d)) for k, d in (("cs", 1), ("dc", 22), ("rst", 27), ("bl", 12))}
        name = str(conf.get("model", DEFAULT_MODEL))
        if self._driver == "mock":
            self.panel = MockLCD(name, **pins)
        else:
            self.panel = ST7789(name, **pins, speed=int(conf.get("speed_hz", 40_000_000)))
        self.panel.open()
        self.panel.brightness(int(conf.get("brightness", 60)))

    def _run(self):
        backoff = 5
        self._brightness = None
        while not self.stop_flag.is_set():
            if self.panel is None:
                try:
                    self._open()
                    self.error = None
                    self._last_bytes = None           # a fresh panel needs the frame again
                    self._brightness = None
                    log.info("second screen: %s", self.panel.name)
                except Exception as e:
                    self.error = f"{type(e).__name__}: {str(e)[:120]}"
                    log.warning("second screen: %s", self.error)
                    self.stop_flag.wait(backoff)
                    backoff = min(60, backoff * 2)
                    continue
            backoff = 5
            try:
                want = int(self.conf.get("brightness", 60))
                if want != self._brightness:
                    self.panel.brightness(want)
                    self._brightness = want
                image = self.render(self.conf) if self.render else None
                if image is not None:
                    with self._lock:
                        self._last_image = image      # as drawn: what the page previews
                    rot = int(self.conf.get("rotation", 0)) % 360
                    if rot:
                        # the screens draw landscape for 90/270, so this lands on the panel's portrait size
                        image = image.rotate(rot, expand=True)
                    if image.size != self.panel.size:
                        image = image.resize(self.panel.size)
                    data = image.tobytes()
                    if data != self._last_bytes:
                        self.panel.show(image)
                        self._last_bytes = data
                        self.frames += 1
                self.error = None
            except Exception as e:
                self.error = f"{type(e).__name__}: {str(e)[:120]}"
                log.warning("second screen: %s", self.error)
                try:
                    self.panel.close()
                except Exception:
                    pass
                self.panel = None
                self.stop_flag.wait(3)
                continue
            try:
                wait = self.interval(self.conf) if self.interval else None
            except Exception:
                wait = None
            if not wait:
                try:
                    wait = 1.0 / max(0.2, float(self.conf.get("fps", 2) or 2))
                except (TypeError, ValueError):
                    wait = 0.5
            self.wake.wait(wait)
            self.wake.clear()
        try:
            if self.panel:
                self.panel.close()
        except Exception:
            pass
        self.panel = None
        log.info("second screen stopped")


SECOND = Second()
