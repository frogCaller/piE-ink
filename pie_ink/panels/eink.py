"""Waveshare e-Paper HATs.

2.13" V3/V4 (250x122), 2.7" V1/V2 (264x176, four keys on GPIO 5/6/13/19)
and 3.7" (480x280). The service turns the keys into actions.

The 2.7" drivers as Waveshare ship them send a frame to the controller a
byte at a time — some 6,000 SPI transfers per frame, a second or more of
the Zero's time — and pack the bits in a Python loop; here the bits are
packed with numpy and the frame goes in one transfer, the controller
seeing exactly the same bytes.
"""
import importlib
import logging

from .base import Panel

log = logging.getLogger(__name__)


def _epdconfig():
    return importlib.import_module("waveshare_epd.epdconfig")


def packed(image, epd):
    """A 1-bit image as the driver's getbuffer() lays it out (one byte per
    eight pixels along the controller's rows, 1 = white), via numpy. The
    image may be the panel's way up (width x height) or the controller's own
    (height x width); the driver's own loop is the fallback without numpy."""
    try:
        import numpy as np
    except ImportError:
        return epd.getbuffer(image)
    native = image.convert("1")
    if native.size == (epd.height, epd.width):          # the panel is mounted sideways to the controller
        native = native.rotate(90, expand=True)
    elif native.size != (epd.width, epd.height):
        return epd.getbuffer(image)
    return np.packbits(np.array(native, dtype=np.uint8), axis=1).flatten().tolist()


class EInk(Panel):
    kind = "eink"
    module = ""
    has_partial = True

    # hooks for drivers whose init/clear take arguments
    def _init(self):
        self._epd.init()

    def _clear_hw(self, dark=False):
        if self.name.startswith("epd2in7"):
            self._epd.Clear()
        else:
            self._epd.Clear(0x00 if dark else 0xFF)

    def _open(self):
        try:
            importlib.import_module("lgpio")          # just checking it's there
        except ImportError:
            log.warning("python3-lgpio is not installed; falling back to gpiozero, "
                        "which will not work on a Pi 5. Run: sudo apt install python3-lgpio")
        try:
            self._epd = importlib.import_module(self.module).EPD()
        except RuntimeError as e:
            if "peripheral base" in str(e):
                raise RuntimeError("GPIO backend can't drive this Pi. Install python3-lgpio "
                                   "(sudo apt install python3-lgpio) and restart pie-ink.") from e
            raise
        self._init()

    def _release(self):
        """Let go of the SPI bus and the GPIO lines, so nothing is left claimed
        while the panel sleeps — and so the next driver can claim them."""
        epdconfig = _epdconfig()
        if hasattr(getattr(epdconfig, "implementation", None), "_claimed"):
            epdconfig.module_exit(cleanup=True)

    def _close(self, clear):
        try:
            if clear:
                self._init()
                self._clear_hw()
            self._epd.sleep()      # deep sleep + releases SPI
        finally:
            self._release()        # whatever the panel did, the lines are free afterwards
            self._epd = None

    def _clear(self, dark):
        self._init()
        self._clear_hw(dark)

    def _ram(self, command, buf):
        """A whole frame into one of the controller's RAMs in a single SPI
        transfer, the way the 2.13" driver's send_data2 does it."""
        epd, epdconfig = self._epd, _epdconfig()
        epd.send_command(command)
        epdconfig.digital_write(epd.dc_pin, 1)
        epdconfig.digital_write(epd.cs_pin, 0)
        epdconfig.spi_writebyte2(buf)
        epdconfig.digital_write(epd.cs_pin, 1)


# -- 2.13" ------------------------------------------------------------------

class EInk213(EInk):
    width, height = 250, 122

    def _push(self, image, full):
        buf = self._epd.getbuffer(image)
        if full:
            self._epd.init()
            self._epd.displayPartBaseImage(buf)
        else:
            self._epd.displayPartial(buf)


class EInk213V4(EInk213):
    name = "epd2in13_V4"
    label = '2.13" V4'
    module = "waveshare_epd.epd2in13_V4"


class EInk213V3(EInk213):
    name = "epd2in13_V3"
    label = '2.13" V3'
    module = "waveshare_epd.epd2in13_V3"


# -- 2.7" ------------------------------------------------------------------------

class EInk27V2(EInk):
    """The V2 HAT (SSD1680). A full refresh is the driver's display_Base — the
    frame into both RAMs, so partial updates diff against it — and a partial
    update is its display_Partial over the whole screen; both with the frame
    sent in one transfer. Native orientation is 176x264."""
    name = "epd2in7_V2"
    label = '2.7" V2 (with keys)'
    module = "waveshare_epd.epd2in7_V2"
    width, height = 264, 176
    buttons = [5, 6, 13, 19]       # KEY1..KEY4, active low
    min_interval = 0.8

    def _clear_hw(self, dark=False):                 # the driver's Clear(), the frame in one transfer
        white = [0x00 if dark else 0xFF] * (self._epd.width // 8 * self._epd.height)
        self._ram(0x24, white)
        self._epd.TurnOnDisplay()

    def _push(self, image, full):
        epd = self._epd
        buf = packed(image, epd)
        if full:
            epd.init()
            self._ram(0x24, buf)
            self._ram(0x26, buf)
            epd.TurnOnDisplay()
            return
        epd.reset()
        epd.send_command(0x3C); epd.send_data(0x80)                       # border waveform
        epd.send_command(0x44); epd.send_data(0x00); epd.send_data(epd.width // 8 - 1)          # RAM x: the whole width
        epd.send_command(0x45); epd.send_data(0x00); epd.send_data(0x00)                        # RAM y: the whole height
        epd.send_data((epd.height - 1) & 0xFF); epd.send_data(((epd.height - 1) >> 8) & 0x01)
        epd.send_command(0x4E); epd.send_data(0x00)                                              # counters to the start
        epd.send_command(0x4F); epd.send_data(0x00); epd.send_data(0x00)
        self._ram(0x24, buf)
        epd.TurnOnDisplay_Partial()


class EInk27V1(EInk27V2):
    """The V1 HAT (IL91874): every update is a full refresh, about six seconds
    of flashing — the driver's display(), the two frames sent in one transfer
    each."""
    name = "epd2in7"
    label = '2.7" V1 (with keys, full refresh only)'
    module = "waveshare_epd.epd2in7"
    has_partial = False
    min_interval = 2.0

    def _clear_hw(self, dark=False):                 # the driver's Clear(colour), in two transfers
        colour = [0x00 if dark else 0xFF] * (self._epd.width // 8 * self._epd.height)
        self._ram(0x10, colour)
        self._ram(0x13, colour)
        self._epd.send_command(0x12)
        self._epd.ReadBusy()

    def _push(self, image, full):
        epd = self._epd
        buf = packed(image, epd)
        self._ram(0x10, [0xFF] * len(buf))     # the "old" frame: all white, as the driver sends it
        self._ram(0x13, buf)                   # the new one
        epd.send_command(0x12)                 # refresh
        epd.ReadBusy()


# -- 3.7" ------------------------------------------------------------------------

class EInk37(EInk):
    """3.7" HAT, 480x280, driven in 1-bit mode: the A2 waveform gives a ~0.4 s
    update, and the GC waveform (every N frames, or Refresh) clears ghosting."""
    name = "epd3in7"
    label = '3.7"'
    module = "waveshare_epd.epd3in7"
    width, height = 480, 280
    min_interval = 0.6

    def _init(self):
        self._epd.init(1)             # 1-bit mode (4-grey mode is much slower)

    def _clear_hw(self, dark=False):
        self._epd.Clear(0x00 if dark else 0xFF, 1)

    def _buffer(self, image):
        """The driver's per-pixel loop takes ~1 s on a Zero for 134k pixels;
        numpy packs the same bits in a few ms. Native orientation is 280x480."""
        try:
            import numpy as np
        except ImportError:
            return self._epd.getbuffer(image)
        native = image.convert("1").rotate(90, expand=True)
        return np.packbits(np.array(native, dtype=np.uint8), axis=1).flatten().tolist()

    def _show(self, buf, lut):
        epd = self._epd
        epd.send_command(0x4E); epd.send_data(0x00); epd.send_data(0x00)
        epd.send_command(0x4F); epd.send_data(0x00); epd.send_data(0x00)
        epd.send_command(0x24)
        epd.send_data2(buf)
        epd.load_lut(lut)
        epd.send_command(0x20)
        epd.ReadBusy()

    def _push(self, image, full):
        buf = self._buffer(image)
        if full:
            self._init()
            self._show(buf, self._epd.lut_1Gray_GC)      # slow, clean waveform
        else:
            self._show(buf, self._epd.lut_1Gray_A2)      # fast; ghosts a little, hence full every N
