"""The colour canvas for the LCD.

The Draw tab paints on it when its target is the LCD: the page sends the
whole picture, at the LCD's size, as it changes, and the LCD's Paint screen
shows it — so a stroke lands on the glass while it is being drawn. The
picture lives in memory and is written to data/paint.png a moment after the
last change, so it is still there after a restart and on another phone.
"""
import io
import logging
import os
import threading
import time

from PIL import Image

from .settings import DATA_DIR

log = logging.getLogger(__name__)
PATH = os.path.join(DATA_DIR, "paint.png")
SAVE_AFTER = 2.0                 # seconds of quiet before the picture goes to disk
BUSY_FOR = 5.0                   # how long after a change the LCD keeps redrawing quickly


class Paint:
    def __init__(self):
        self._lock = threading.Lock()
        self._image = None
        self._loaded = False
        self._timer = None
        self.version = 0
        self.changed_at = 0.0

    def image(self):
        """A copy of the picture, or None if nothing has been painted."""
        with self._lock:
            if not self._loaded:
                self._loaded = True
                if os.path.exists(PATH):
                    try:
                        img = Image.open(PATH)
                        img.load()
                        self._image = img.convert("RGB")
                    except OSError as e:
                        log.warning("could not read %s: %s", PATH, e)
            return self._image.copy() if self._image is not None else None

    def set(self, raw):
        """PNG/JPEG bytes from the page. Returns the new version number."""
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.width > 1024 or img.height > 1024:
            raise ValueError("that picture is too big for the LCD")
        with self._lock:
            self._image = img.convert("RGB")
            self._loaded = True
            self.version += 1
            self.changed_at = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(SAVE_AFTER, self._save)
            self._timer.daemon = True
            self._timer.start()
            return self.version

    def busy(self):
        """True for a few seconds after a change: the LCD redraws fast then."""
        return time.time() - self.changed_at < BUSY_FOR

    def status(self):
        with self._lock:
            size = list(self._image.size) if self._image is not None else None
        return {"version": self.version, "size": size}

    def _save(self):
        with self._lock:
            img = self._image.copy() if self._image is not None else None
        if img is None:
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = PATH + ".part"
            img.save(tmp, "PNG")
            os.replace(tmp, PATH)
        except OSError as e:
            log.warning("could not save the painting (%s); keeping it in memory", e)


PAINT = Paint()
