"""The RetroFlag GPi Case 2W's screen as the main display.

The case takes a Pi Zero 2 W and puts a 3" 640×480 colour IPS screen on its pins (DPI: GPIO
0–17 and 20–25, which is why there's no room for an e-ink HAT or an SPI LCD as well), a
speaker on GPIO 18/19 and a power switch on 26/27. setup.sh (the GPi Case 2W picked as the
screen) writes the screen, speaker and switch into config.txt the way Pietato does; the screen
is then the Pi's framebuffer, /dev/fb0, and this draws on it.

The screen is small and sharp (about 270 pixels to the inch), so PiE-ink's screens are laid
out for 320×216 — the size they read well at on 3" — and drawn at twice that: the e-ink's
screens come three times over-size and are shrunk to 640×432 (smooth, sharp text), the colour
screens are doubled pixel for pixel (crisp). Either fills the glass above the bottom 48 pixels,
where the case's buttons for this screen are shown (pie_ink/pad.py says which), drawn at the
screen's own resolution: ◀▶ Next  ▲▼ 1D  A Auto … No bars.

While PiE-ink runs, the console is kept off the screen: its service switches to a console of
its own in graphics mode (the systemd drop-in setup.sh adds runs pie_ink/vt.py as root before
it starts and after it stops), so no login prompt or cursor draws over it. Run by hand, without
root, the console's cursor is hidden instead.
"""
import fcntl
import logging
import os
import struct

from PIL import Image

from .base import Panel, to_png, PREVIEW_SCALE

log = logging.getLogger(__name__)

FB = "/dev/fb0"
GLASS = (640, 480)            # the screen, as it is
FOOT = 48                     # the buttons along the bottom, at the glass's own pixels
LAYOUT = (320, 216)           # what the screens are laid out for: half the rest, both ways (it's that sharp)
WHOLE = (320, 240)            # all of it, at the layout's size: the page's preview
FOOT_BG, FOOT_LINE = (16, 19, 23), (40, 46, 55)
KEY_FILL, KEY_TEXT, LABEL, NOTE = (62, 68, 79), (236, 239, 243), (176, 184, 196), (120, 128, 140)
FBIOGET_VSCREENINFO = 0x4600


def fb_geometry(f):
    """(width, height, x offset, y offset, bits per pixel, [(offset, length)] for red, green, blue, stride)."""
    info = fcntl.ioctl(f, FBIOGET_VSCREENINFO, bytes(160))
    w, h, xv, _yv, xo, yo, bpp, _grey = struct.unpack_from("8I", info, 0)
    fields = [struct.unpack_from("2I", info, o) for o in (32, 44, 56)]
    try:
        with open(f"/sys/class/graphics/{os.path.basename(f.name)}/stride") as fs:
            stride = int(fs.read())
    except (OSError, ValueError):
        stride = xv * bpp // 8
    return w, h, xo, yo, bpp, fields, stride


def pack(image, bpp, fields):
    """An RGB image as the framebuffer's own pixels: RGB565 (the case's), or 32-bit."""
    image = image.convert("RGB")
    if bpp == 32 and [tuple(x) for x in fields] == [(16, 8), (8, 8), (0, 8)]:
        return image.tobytes("raw", "BGRX")
    try:
        import numpy as np
        a = np.asarray(image, dtype=np.uint32)
        px = np.zeros(a.shape[:2], dtype=np.uint32)
        for c, (off, ln) in enumerate(fields):
            px |= (a[..., c] >> (8 - ln)) << off
        return px.astype("<u2" if bpp == 16 else "<u4").tobytes()
    except ImportError:                                   # (numpy is on every Pi; this is the slow way round)
        out = bytearray()
        for r, g, b in image.getdata():
            v = 0
            for c, (off, ln) in zip((r, g, b), fields):
                v |= (c >> (8 - ln)) << off
            out += v.to_bytes(bpp // 8, "little")
        return bytes(out)


class GPiCase(Panel):
    """The case's 640×480 screen through the framebuffer."""
    name = "gpicase"
    label = "RetroFlag GPi Case 2W (3\", 640×480, colour)"
    width, height = LAYOUT
    kind = "lcd"
    colour = True
    has_partial = True
    min_interval = 0.1
    glass = GLASS
    inset = 0

    footer = True                 # the service fills in .hints for each frame (pie_ink/pad.py)

    def __init__(self, rotation=0, full_refresh_every=60):
        super().__init__(rotation % 360 if rotation in (0, 180) else 0, full_refresh_every)
        self.width, self.height = LAYOUT
        self.canvas = LAYOUT
        self.brightness = 100
        self.hints = ([], "", (98, 210, 255))     # ([(key, label, lit)], the screen's name, the accent)
        self._f = None
        self._geo = None

    # -- the picture, at the screen's own size ------------------------------------------------------

    def _physical(self, image, turn=True):
        """A frame from the screens, made 640×480: the screen above, shrunk with a box filter when it
        comes bigger (the e-ink's screens, drawn three times over), doubled pixel for pixel when it comes
        at the layout size (the colour screens), smoothed otherwise; the buttons below. Turned round when
        the case is upside down (not for the page's preview)."""
        gw, gh = self.glass
        area = (gw, gh - FOOT)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB" if image.mode in ("RGBA", "P") else "L")
        if image.size != area:
            w, h = image.size
            if w >= area[0]:
                image = image.resize(area, Image.BOX)
            elif area[0] % w == 0 and area[1] % h == 0 and area[0] // w == area[1] // h:
                image = image.resize(area, Image.NEAREST)
            else:
                image = image.resize(area, Image.LANCZOS)
        whole = Image.new("RGB", (gw, gh), FOOT_BG)
        whole.paste(image.convert("RGB"), (0, 0))
        self._draw_footer(whole, gh - FOOT)
        return whole.rotate(180) if turn and self.rotation == 180 else whole

    def _draw_footer(self, img, top):
        """The buttons for this screen along the bottom, Pietato's way: a dot round a letter, a pill
        round a word, lit when what it turns on is on; the screen's name on the right. Hints that
        don't fit are left off from the end (the same-everywhere ones are last)."""
        from PIL import ImageDraw
        from ..lcd_screens import _bold
        from ..text import font
        hints, note, accent = self.hints if self.hints else ([], "", (98, 210, 255))
        d = ImageDraw.Draw(img)
        w = img.width
        d.line((0, top, w, top), fill=FOOT_LINE)
        y = top + FOOT // 2
        kf, lf = _bold(13), font(1, 15)
        right = w - 16
        if note:
            right -= d.textlength(note, font=lf) + 18
            d.text((w - 16, y), note, font=lf, fill=NOTE, anchor="rm")
        x = 16
        for key, label, lit in hints:
            kw = 22 if len(key) == 1 else max(24, d.textlength(key, font=kf) + 14)
            need = kw + 7 + d.textlength(label, font=lf)
            if x + need > right:
                break
            box = (x, y - 11, x + kw, y + 11)
            fill = accent if lit else KEY_FILL
            if len(key) == 1:
                d.ellipse(box, fill=fill)
            else:
                d.rounded_rectangle(box, radius=11, fill=fill)
            d.text((x + kw / 2, y), key, font=kf, fill=FOOT_BG if lit else KEY_TEXT, anchor="mm")
            x += kw + 7
            d.text((x, y), label, font=lf, fill=LABEL, anchor="lm")
            x += d.textlength(label, font=lf) + 18

    def show(self, image, full=False):
        physical = self._physical(image)
        with self._lock:
            if not self._is_open:
                return False
            data = physical.tobytes()
            if not full and self._last is not None and data == self._last:
                return False
            self._push(physical, full)
            self._last = data
            self._last_src = image
            return True

    def last_frame(self):
        with self._lock:
            return self._last_src.copy() if self._last_src is not None else None

    def preview_png(self, frame):
        return to_png(self._physical(frame, turn=False), WHOLE, PREVIEW_SCALE)

    def info(self):
        d = super().info()
        d["glass"] = list(WHOLE)             # (its shape, buttons and all, for the page's preview)
        d["inset"] = 0
        d["pixels"] = list(GLASS)
        return d

    def set_brightness(self, percent):
        self.brightness = max(0, min(100, int(percent)))       # (the case's backlight isn't dimmable)

    # -- the framebuffer -----------------------------------------------------------------------------

    def _open(self):
        if not os.path.exists(FB):
            raise OSError("no framebuffer (/dev/fb0): is this Pi set up for the GPi Case 2W? Run ./setup.sh --screen=gpi and reboot")
        try:
            self._f = open(FB, "r+b", buffering=0)
        except PermissionError:
            raise OSError("can't draw on /dev/fb0 — add yourself to the video group (setup.sh does) and log in again")
        self._geo = fb_geometry(self._f)
        w, h, _xo, _yo, bpp, _fields, _stride = self._geo
        if bpp not in (16, 32):
            raise OSError(f"the framebuffer is {bpp}-bit; the GPi Case's is 16-bit (framebuffer_depth in config.txt?)")
        if (w, h) != GLASS:
            log.warning("the framebuffer is %dx%d, not the GPi Case's 640x480 — drawing to fit it", w, h)
        self.glass = (w, h)
        from .. import settings, vt
        self._cursor = False
        if not vt.take(quiet=True):                         # (as root, by hand; the service has it done for it)
            self._cursor = vt.hide_cursor()                 # (not root: the console's cursor off, at least)
        try:
            if settings.pitato_running():
                log.warning("Pietato is running too, and draws on the case's screen as well: sudo systemctl disable --now pitato")
        except Exception:
            pass

    def _close(self, clear):
        if self._f is None:
            return
        try:
            if clear:
                self._clear(True)
            if getattr(self, "_cursor", False):
                from .. import vt
                vt.show_cursor()
                self._cursor = False
        finally:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None

    def _push(self, image, full):
        w, h, xo, yo, bpp, fields, stride = self._geo
        if image.size != (w, h):
            image = image.resize((w, h), Image.LANCZOS)
        data = pack(image, bpp, fields)
        row = w * bpp // 8
        if stride == row and not xo and not yo:
            self._f.seek(0)
            self._f.write(data)
            return
        for r in range(h):
            self._f.seek((r + yo) * stride + xo * bpp // 8)
            self._f.write(data[r * row:(r + 1) * row])

    def _clear(self, dark):
        if self._f is not None and self._geo is not None:
            w, h = self._geo[0], self._geo[1]
            self._push(Image.new("RGB", (w, h), (0, 0, 0) if dark else (255, 255, 255)), True)
        self._last = None


class MockGPiCase(GPiCase):
    """No case: its shape, for the preview."""
    name = "mock_gpicase"
    label = "None (GPi Case size, colour)"
    min_interval = 0.5

    def __init__(self, rotation=0, full_refresh_every=60):
        super().__init__(rotation, full_refresh_every)
        self.pushed = 0

    def _open(self): pass
    def _close(self, clear): pass
    def _push(self, image, full): self.pushed += 1
    def _clear(self, dark): self._last = None
