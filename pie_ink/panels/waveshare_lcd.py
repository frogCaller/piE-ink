"""A Waveshare SPI LCD — the 1.69" (240×280) or the 1.9" (170×320), both colour — as the main
display, by itself (setup.sh: "An LCD").

The same screen as the second one beside an e-ink (pie_ink/lcd.py drives it), wired the same way:
SPI0 CE1, DC on GPIO 22, reset on 27, backlight on 12 — or the pins under Settings → Screen's
second-screen card, if you changed them there. It's colour, so the colour screens show here as
they do on a Whisplay, and it's portrait glass: "rotation" 90 or 270 (the default) lays the
screens out landscape, 0 and 180 upright. The 1.69" has rounded corners, so its screens sit a few
pixels in from the edge.
"""
import logging
import threading

from .whisplay import ColourPanel

log = logging.getLogger(__name__)


def _pins():
    """The LCD's wiring, as set for the second screen (the defaults: CE1, DC 22, RST 27, BL 12)."""
    try:
        from .. import settings
        conf = settings.load().get("lcd") or {}
    except Exception:
        conf = {}
    return {k: int(conf.get(k, d)) for k, d in (("cs", 1), ("dc", 22), ("rst", 27), ("bl", 12))}, \
        int(conf.get("speed_hz", 40_000_000) or 40_000_000)


class WaveshareLCD(ColourPanel):
    model = "1.69"
    buttons = []
    key_count = 0
    min_interval = 0.2

    def __init__(self, rotation=90, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        self._lcd = None

    def _open(self):
        from .. import lcd
        pins, speed = _pins()
        self._lcd = lcd.ST7789(self.model, cs=pins["cs"], dc=pins["dc"], rst=pins["rst"], bl=pins["bl"], speed=speed)
        self._lcd.open()
        self._backlight(self.brightness)

    def _close(self, clear):
        if self._lcd is None:
            return
        try:
            if clear:
                self._clear(False)
        except Exception:
            log.debug("clearing the LCD", exc_info=True)
        finally:
            self._lcd.close()
            self._lcd = None

    def _push(self, image, full):
        self._lcd.show(image)

    def _backlight(self, percent):
        if self._lcd is not None:
            self._lcd.brightness(percent)


class Lcd169(WaveshareLCD):
    name = "lcd169"
    label = 'Waveshare 1.69" LCD (240×280, colour)'
    model = "1.69"
    glass = (240, 280)
    inset = 10                                  # its rounded corners


class Lcd19(WaveshareLCD):
    name = "lcd19"
    label = 'Waveshare 1.9" LCD (170×320, colour)'
    model = "1.9"
    glass = (170, 320)
    inset = 4


class _Mock:
    min_interval = 0.5

    def __init__(self, rotation=90, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        self.pushed = 0
        self._lock2 = threading.Lock()

    def _open(self): pass
    def _close(self, clear): pass
    def _push(self, image, full): self.pushed += 1
    def _backlight(self, percent): pass


class MockLcd169(_Mock, Lcd169):
    name = "mock_lcd169"
    label = 'None (1.69" LCD size, colour)'


class MockLcd19(_Mock, Lcd19):
    name = "mock_lcd19"
    label = 'None (1.9" LCD size, colour)'
