"""The PiSugar Whisplay HAT's screen as the main display.

The HAT puts a 1.69" 240x280 colour LCD (ST7789P3), a WM8960 sound card
with two microphones and a speaker, a button and an RGB LED on a Pi Zero.
This is the screen; the button and LED are in pie_ink/whisplay.py and the
sound card is just an ALSA device once PiSugar's driver is installed.

The LCD is portrait, glass-wise. PiE-ink's screens are landscape, so the
panel's "rotation" picks which way the picture goes on: 0 and 180 keep the
panel portrait (240x280), 90 and 270 turn it landscape (280x240) and the
screens draw for that shape. The wiring is fixed by the HAT (SPI0 CE0, DC on
GPIO 27, reset on 4, backlight on 22, active low), so there is nothing to
choose.

Frames arrive from the modes as greyscale (drawn three times over-size, for
crisp e-ink), or in colour from the colour screens; either is shrunk to the
panel with a box filter — so text is anti-aliased here rather than
thresholded — and sent as RGB565.
"""
import logging
import threading
import time

from PIL import Image

from .base import Panel
from ..lcd import rgb565

log = logging.getLogger(__name__)

# BCM numbers, from PiSugar's driver (they list them as board pins 13, 7, 15)
DC, RST, BACKLIGHT = 27, 4, 22
INIT = [
    (0x11, []),                                           # sleep out (then a pause)
    (0x36, [0xC0]),                                       # the way PiSugar's driver has the glass
    (0x3A, [0x05]),                                       # RGB565
    (0xB2, [0x0C, 0x0C, 0x00, 0x33, 0x33]),
    (0xB7, [0x35]),
    (0xBB, [0x32]),
    (0xC2, [0x01]),
    (0xC3, [0x15]),
    (0xC4, [0x20]),
    (0xC6, [0x0F]),
    (0xD0, [0xA4, 0xA1]),
    (0xE0, [0xD0, 0x08, 0x0E, 0x09, 0x09, 0x05, 0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34]),
    (0xE1, [0xD0, 0x08, 0x0E, 0x09, 0x09, 0x15, 0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34]),
    (0x21, []),                                           # inversion on
    (0x29, []),                                           # display on
]
GLASS = (240, 280)                                        # portrait, as wired
Y_OFFSET = 20
CHUNK = 4096


class ColourPanel(Panel):
    """A panel that takes grey or colour frames and keeps the antialiasing.
    Subclasses push RGB images the panel's physical size.

    The glass has rounded corners, so the screens get a slightly smaller
    canvas (`inset` pixels in from each edge) and sit inside a border in
    their own background colour — nothing is drawn where the corners would
    clip it."""
    kind = "lcd"
    colour = True
    has_partial = True
    min_interval = 0.2
    glass = GLASS                                          # what the hardware is, portrait
    inset = 10                                             # keeps clear of the rounded corners

    def __init__(self, rotation=0, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        gw, gh = self.glass
        self.canvas = (gh, gw) if self.rotation % 180 == 90 else (gw, gh)   # the glass, the way round it is used
        # the logical shape the screens draw for: the canvas minus the corners
        self.width, self.height = self.canvas[0] - 2 * self.inset, self.canvas[1] - 2 * self.inset
        self.brightness = 70

    def _framed(self, image, scale=1):
        """The frame on the canvas: fitted to the drawing area, inside a
        border of its own background colour. `scale` > 1 keeps the page's
        preview as sharp as the render."""
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB" if image.mode in ("RGBA", "P") else "L")
        target = (self.width * scale, self.height * scale)
        if image.size != target:
            image = image.resize(target, Image.BOX if image.width >= target[0] else Image.LANCZOS)
        image = image.convert("RGB")
        if not self.inset:
            return image
        edge = image.getpixel((0, 0))
        full = Image.new("RGB", (self.canvas[0] * scale, self.canvas[1] * scale), edge)
        full.paste(image, (self.inset * scale, self.inset * scale))
        return full

    def show(self, image, full=False):
        src = image
        framed = self._framed(image)
        with self._lock:
            if not self._is_open:
                return False
            data = framed.tobytes()
            if not full and self._last is not None and data == self._last:
                return False
            physical = framed.rotate(self.rotation, expand=True) if self.rotation else framed
            if physical.size != self.glass:
                physical = physical.resize(self.glass)
            self._push(physical, full)
            self._last = data
            self._last_src = src
            return True

    def last_frame(self):
        with self._lock:
            return self._last_src.copy() if self._last_src is not None else None

    def preview_png(self, frame):
        from .base import to_png, PREVIEW_SCALE
        return to_png(self._framed(frame, PREVIEW_SCALE), self.canvas)

    def info(self):
        d = super().info()
        d["glass"] = list(self.canvas)
        d["inset"] = self.inset
        return d

    def set_brightness(self, percent):
        self.brightness = max(0, min(100, int(percent)))
        with self._lock:
            if self._is_open:
                try:
                    self._backlight(self.brightness)
                except Exception:
                    log.debug("backlight", exc_info=True)

    def _backlight(self, percent): ...

    def _clear(self, dark):
        self._push(Image.new("RGB", self.glass, (0, 0, 0) if dark else (255, 255, 255)), True)
        self._last = None


class Whisplay(ColourPanel):
    name = "whisplay"
    label = 'PiSugar Whisplay HAT (1.69" LCD)'
    # the HAT's button (GPIO 17) and LED are pie_ink/whisplay.py's, not the generic HAT keys
    buttons = []
    key_count = 0

    def __init__(self, rotation=90, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        self.spi = None
        self.chip = None
        self._lgpio = None

    def _open(self):
        import lgpio
        import spidev
        from .. import gpio
        self._lgpio = lgpio
        self.chip = gpio.open_header_chip(lgpio)
        for pin in (DC, RST, BACKLIGHT):
            lgpio.gpio_claim_output(self.chip, pin)
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 40_000_000
        self.spi.mode = 0
        self._reset()
        for cmd, data in INIT:
            self._command(cmd)
            if data:
                self._data(bytes(data))
            if cmd == 0x11:
                time.sleep(0.12)
        self._backlight(self.brightness)

    def _close(self, clear):
        try:
            if self._lgpio and self.chip is not None:
                if clear:
                    self._clear(False)
                self._backlight(0)
                self._command(0x28)                       # display off
                for pin in (DC, RST, BACKLIGHT):
                    try:
                        self._lgpio.gpio_free(self.chip, pin)
                    except Exception:
                        pass
                self._lgpio.gpiochip_close(self.chip)
        except Exception:
            log.debug("closing the whisplay screen", exc_info=True)
        try:
            if self.spi:
                self.spi.close()
        except Exception:
            pass
        self.spi = self.chip = None

    def _reset(self):
        g = self._lgpio
        g.gpio_write(self.chip, RST, 1)
        time.sleep(0.01)
        g.gpio_write(self.chip, RST, 0)
        time.sleep(0.01)
        g.gpio_write(self.chip, RST, 1)
        time.sleep(0.12)

    def _command(self, cmd):
        self._lgpio.gpio_write(self.chip, DC, 0)
        self.spi.writebytes([cmd])

    def _data(self, data):
        self._lgpio.gpio_write(self.chip, DC, 1)
        for i in range(0, len(data), CHUNK):
            self.spi.writebytes2(data[i:i + CHUNK])

    def _window(self, x0, y0, x1, y1):
        y0, y1 = y0 + Y_OFFSET, y1 + Y_OFFSET
        self._command(0x2A)
        self._data(bytes([x0 >> 8, x0 & 0xFF, (x1 - 1) >> 8, (x1 - 1) & 0xFF]))
        self._command(0x2B)
        self._data(bytes([y0 >> 8, y0 & 0xFF, (y1 - 1) >> 8, (y1 - 1) & 0xFF]))
        self._command(0x2C)

    def _push(self, image, full):
        self._window(0, 0, *self.glass)
        self._data(rgb565(image))

    def _backlight(self, percent):
        # the HAT drives the backlight through a transistor: low is on
        percent = max(0, min(100, int(percent)))
        try:
            self._lgpio.tx_pwm(self.chip, BACKLIGHT, 1000, 100 - percent)
        except Exception:
            self._lgpio.gpio_write(self.chip, BACKLIGHT, 0 if percent > 0 else 1)


class MockWhisplay(ColourPanel):
    """No hardware: the Whisplay's shape, for the preview."""
    name = "mock_whisplay"
    label = "None (Whisplay size, colour)"
    min_interval = 0.5

    def __init__(self, rotation=90, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        self.pushed = 0
        self._lock2 = threading.Lock()

    def _open(self): pass
    def _close(self, clear): pass
    def _push(self, image, full): self.pushed += 1
    def _backlight(self, percent): pass
