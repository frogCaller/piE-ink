"""What a panel looks like to the rest of the app.

Subclasses set width/height/kind and implement _open / _close / _push /
_clear. This base handles the frame diff (identical frames are never
re-sent), rotation, the preview, and locking.
"""
import io
import logging
import threading
import time

from PIL import Image

log = logging.getLogger(__name__)

PREVIEW_SCALE = 3
# a frame that takes this long means the controller never said it was done (the drivers give up
# waiting after 30 s): the wrong driver for the panel, or a panel that isn't there
UNANSWERED = 25.0
NO_ANSWER = ("The panel didn't answer — is the driver the right one for it (a 2.7\" is V1 or V2, "
             "a 2.13\" V3 or V4), and the HAT seated?")


class Panel:
    name = "base"
    label = "Base"
    kind = "eink"
    colour = False            # True for a panel that shows grey and colour frames as they are
    width, height = 250, 122
    min_interval = 1.0        # seconds between frames the hardware is happy with
    has_partial = True        # False = every update is a full (flashing) refresh
    buttons = []              # GPIOs of keys on the HAT, if any
    key_count = 0             # how many keys to show on the page (mocks have none wired)

    def __init__(self, rotation=0, full_refresh_every=60):
        self.rotation = int(rotation) % 360
        self.full_refresh_every = int(full_refresh_every)
        self._lock = threading.Lock()
        self._last = None          # 1-bit, what the panel holds
        self._last_src = None      # what was rendered, for the preview
        self._partials = 0
        self._is_open = False
        self.warning = ""          # set while the panel takes far too long to answer

    @property
    def size(self):
        return (self.width, self.height)

    # -- to implement ----------------------------------------------------------

    def _open(self): ...
    def _close(self, clear): ...
    def _push(self, image, full): ...
    def _clear(self, dark): ...

    # -- public ----------------------------------------------------------------

    def open(self):
        with self._lock:
            if self._is_open:
                return
            self._timed(self._open)
            self._is_open = True
            self._last = None
            self._partials = 0
            log.info("panel open: %s %dx%d, rotation %d", self.name, self.width, self.height, self.rotation)

    def _timed(self, fn, *args):
        """Run a step of hardware talk, and notice when it took so long that the
        controller must never have answered."""
        t0 = time.monotonic()
        try:
            return fn(*args)
        finally:
            took = time.monotonic() - t0
            if took >= UNANSWERED:
                if not self.warning:
                    log.warning("panel %s: %.0f s for one step — %s", self.name, took, NO_ANSWER)
                self.warning = NO_ANSWER
            else:
                self.warning = ""

    def close(self, clear=True):
        with self._lock:
            if not self._is_open:
                return
            try:
                self._close(clear)
            except Exception:
                log.exception("panel close failed")
            self._is_open = False
            self._last = None
            log.info("panel closed")

    @property
    def is_open(self):
        return self._is_open

    def show(self, image, full=False):
        """Push a frame (any size; supersampled frames are reduced). Skips
        identical frames; partial refresh normally, a full refresh every N
        frames or on demand. True if anything was sent."""
        src = image
        image = to_bilevel(image, self.size)
        with self._lock:
            if not self._is_open:
                return False
            if not full and self._last is not None and image.tobytes() == self._last.tobytes():
                return False
            need_full = full or self._last is None or not self.has_partial or self._partials >= self.full_refresh_every
            rotated = image.rotate(self.rotation, expand=False) if self.rotation else image
            self._timed(self._push, rotated, need_full)
            self._partials = 0 if need_full else self._partials + 1
            self._last = image.copy()
            self._last_src = src
            return True

    def clear(self, dark=False):
        with self._lock:
            if self._is_open:
                self._clear(dark)
            self._last = None
            self._partials = 0

    def force_full_next(self):
        with self._lock:
            self._partials = self.full_refresh_every

    def last_frame(self):
        """The frame last rendered (as drawn, before the panel's own
        conversion), or None before the first one."""
        with self._lock:
            return self._last_src.copy() if self._last_src is not None else None

    def last_frame_png(self):
        frame = self.last_frame()
        if frame is None:
            frame = Image.new("L", self.size, 255)
        return self.preview_png(frame)

    def preview_png(self, frame):
        """A frame as the page should show it."""
        return to_png(frame, self.size)

    def info(self):
        return {"driver": self.name, "label": self.label, "kind": self.kind, "colour": self.colour,
                "width": self.width, "height": self.height,
                "buttons": self.key_count or len(self.buttons), "has_partial": self.has_partial,
                "warning": self.warning}


def to_bilevel(image, size):
    """Panel-resolution 1-bit. Supersampled frames are box-filtered down and
    thresholded (no dithering: clean shapes); 1-bit images (dithered drawings)
    pass through."""
    if image.mode == "1" and image.size == size:
        return image
    img = image.convert("L")
    if img.size != size:
        img = img.resize(size, Image.BOX if img.width >= size[0] else Image.LANCZOS)
    return img.point(lambda v: 255 if v >= 128 else 0).convert("1")


def to_png(image, size, scale=PREVIEW_SCALE):
    """PNG for the web page at scale x panel size. Supersampled frames are
    used as-is (sharp); 1-bit images are enlarged pixel-for-pixel."""
    target = (size[0] * scale, size[1] * scale)
    if image.size != target:
        image = image.resize(target, Image.NEAREST if image.mode == "1" else Image.LANCZOS)
    buf = io.BytesIO()
    image.convert("RGB" if image.mode in ("RGB", "RGBA") else "L").save(buf, format="PNG", compress_level=3)
    return buf.getvalue()
