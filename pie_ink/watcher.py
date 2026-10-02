"""Keeping an eye on the room.

A thread of its own grabs a small frame every few seconds and compares it with
the last one. Comparing 64x48 greyscale pixels costs almost nothing, so the
display thread and the web server never wait on it. Only when enough has
changed does it hand a frame on — at most every `look_gap` seconds while
things keep moving, and once every `check_minutes` when nothing does, so a
room that has gone quiet is noticed too. What happens with the frame (a look
for events, a remark, a photo to your phone) is the app's business.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)
SIZE = (64, 48)          # what we compare: small enough to be free


class Watcher:
    def __init__(self):
        self.thread = None
        self.stop_flag = threading.Event()
        self.conf = {}
        self.on_change = None
        self.is_busy = lambda: False
        self.state = "off"           # off | watching | something | error
        self.level = 0.0             # how different the last frame was, 0–100
        self.last_change = 0.0
        self.last_told = 0.0
        self.last_look = 0.0         # when a frame was last handed on
        self.error = None
        self._previous = None
        self._quiet_since = 0.0

    # -- lifecycle ------------------------------------------------------------------

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, conf, on_change=None, is_busy=None):
        self.conf = conf or {}
        if on_change:
            self.on_change = on_change
        if is_busy:
            self.is_busy = is_busy
        if not self.conf.get("enabled"):
            self.stop()
            return False, "not watching"
        if self.running():
            return True, "already watching"
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="watcher")
        self.thread.start()
        return True, "watching"

    def stop(self):
        self.stop_flag.set()
        self.state = "off"
        self._previous = None

    def status(self):
        return {"state": self.state, "level": round(self.level, 1), "running": self.running(),
                "error": self.error, "seen_ago": round(time.time() - self.last_change) if self.last_change else None,
                "told_ago": round(time.time() - self.last_told) if self.last_told else None,
                "looked_ago": round(time.time() - self.last_look) if self.last_look else None}

    # -- the loop -------------------------------------------------------------------

    @staticmethod
    def _small(frame):
        return frame.convert("L").resize(SIZE)

    @staticmethod
    def _difference(a, b):
        """Mean absolute difference as a percentage — no numpy needed."""
        pa, pb = a.tobytes(), b.tobytes()
        if len(pa) != len(pb):
            return 100.0
        total = sum(abs(x - y) for x, y in zip(pa, pb))
        return total / len(pa) / 2.55 if pa else 0.0

    def _run(self):
        from . import camera, ptz

        def number(key, default, least):
            try:
                return max(least, float(self.conf.get(key, default) or default))
            except (TypeError, ValueError):
                return float(default)
        every = number("interval", 6, 0.5)
        sensitivity = number("sensitivity", 12, 1)
        look_gap = number("look_gap", 20, 3)                    # between looks while things keep moving
        check = number("check_minutes", 15, 0) * 60             # a look now and then regardless; 0 = never
        self.state, self.error = "watching", None
        log.info("watching the room every %ss", every)

        while not self.stop_flag.is_set():
            self.stop_flag.wait(every)
            if self.stop_flag.is_set():
                break
            try:
                camera.CAMERA.touch(int(every * 3))
                if ptz.PTZ.quiet():
                    self._previous = None               # the picture moves because the camera did: start afresh
                    continue
                frame = camera.CAMERA.frame()
                if frame is None:
                    self.state = "error"
                    self.error = camera.CAMERA.status().get("error") or "no picture from the camera"
                    continue
                self.state, self.error = "watching", None
                small = self._small(frame)
                now = time.time()
                if self._previous is None:
                    self._previous = small
                    self.last_look = now                    # the first look comes after a quiet start
                    continue
                self.level = self._difference(self._previous, small)
                self._previous = small
                moved = self.level >= sensitivity
                if moved:
                    self.last_change = now
                    self.state = "something"
                due = (moved and now - self.last_look >= look_gap) or (check > 0 and now - self.last_look >= check)
                if not due:
                    continue
                self.last_look = now
                if moved:
                    log.info("something moved (%.0f%% changed)", self.level)
                if self.on_change:
                    self.on_change(frame, self.level, moved)
            except Exception as e:
                log.warning("watcher: %s", e)
                self.state, self.error = "error", str(e)[:120]
                self.stop_flag.wait(10)

        if self.state != "error":
            self.state = "off"


WATCHER = Watcher()
