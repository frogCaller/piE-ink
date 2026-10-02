"""One long-running thread that owns the panel and runs the active mode.

Flask never touches the hardware; it calls set_mode()/apply_settings() here.
"""
import logging
import threading

from . import badge
from . import panels
from . import settings as cfg
from .modes import MODES

CYCLE = ["me", "clock", "weather", "message", "image", "reader", "camera", "music",
         "groupchat", "crypto", "system", "gps", "buddy"]
# on a colour panel these screens are drawn by their colour versions (pie_ink/lcd_screens.py)
COLOUR_SCREENS = {"system", "weather", "crypto", "gps", "camera", "map", "buddy"}

log = logging.getLogger(__name__)


class DisplayService(threading.Thread):
    def __init__(self):
        super().__init__(name="display", daemon=True)
        self.config = cfg.load()
        self.panel = panels.make(self.config["display"])
        self._size_the_words()
        self._mode = None
        self._pending_mode = self.config.get("startup_mode", "clock")
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._running = True
        self._last_error = None
        self._frames = 0
        self._buttons = None
        self._cycle_at = 0.0           # when the slideshow next moves on
        self._retired = None           # a panel swapped out, for the display thread to put to sleep
        self._attach_buttons()

    def _size_the_words(self):
        """Tell the bot how much the screen holds, so its answers are cut to
        fit this screen rather than the smallest one."""
        from . import llm
        w, h = self.panel.size
        chars = (w / 6.0) * (h / 14.0)                    # roughly, at the sizes the screens use
        llm.ROOM["factor"] = max(1.0, chars / ((250 / 6.0) * (122 / 14.0)))

    # -- the slideshow -----------------------------------------------------------------

    def apply_cycle(self):
        """Move on to the next chosen screen every so many seconds. Picking a
        screen by hand restarts the clock, so what you chose gets its full turn."""
        conf = self.config.get("cycle", {})
        if not conf.get("enabled"):
            self._cycle_at = 0.0
            return
        import time as _t
        now = _t.time()
        every = max(5, int(conf.get("seconds", 30) or 30))
        if not self._cycle_at:
            self._cycle_at = now + every
            return
        if now < self._cycle_at:
            return
        self._cycle_at = now + every
        screens = [m for m in conf.get("screens", []) if m in MODES or m == "off"]
        if len(screens) < 2:
            return
        current = self.mode_name
        after = screens[(screens.index(current) + 1) % len(screens)] if current in screens else screens[0]
        if after != current:
            log.info("cycle -> %s", after)
            self.set_mode(after, restart_cycle=False)

    # -- HAT keys ----------------------------------------------------------------------

    def _attach_buttons(self):
        if self._buttons:
            self._buttons.close()
            self._buttons = None
        pins = getattr(self.panel, "buttons", [])
        if not pins:
            return
        try:
            from .buttons import Buttons
            self._buttons = Buttons(pins, self.on_button)
        except Exception as e:
            log.warning("keys not available: %s", e)

    def on_button(self, index):
        """Key index 0..3 -> configured action."""
        action = self.config.get("buttons", {}).get(f"key{index + 1}", "none")
        log.info("key %d -> %s", index + 1, action)
        self.on_button_action(action)

    def on_button_action(self, action):
        """A named action from a HAT key."""
        if self.mode_name == "off" and action != "none":
            self.set_mode(self.config.get("startup_mode", "clock"))
            return
        if action in ("prev", "next"):
            if self._mode and self._mode.on_button(action):
                self._wake.set()
        elif action in ("next_mode", "prev_mode"):
            cur = self.mode_name if self.mode_name in CYCLE else CYCLE[0]
            i = (CYCLE.index(cur) + (1 if action == "next_mode" else -1)) % len(CYCLE)
            self.set_mode(CYCLE[i])
        elif action == "refresh":
            self.refresh()
        elif action == "off":
            self.set_mode("off")

    def modes_available(self):
        return list(MODES)

    # -- public API (called from Flask) ---------------------------------------

    @property
    def mode_name(self):
        return self._mode.name if self._mode else None

    def set_mode(self, name, restart_cycle=True):
        if name not in MODES:
            raise ValueError(f"unknown mode: {name}")
        with self._lock:
            self._pending_mode = name
        if restart_cycle:
            self._cycle_at = 0.0             # a hand-picked screen gets a full turn
        self._wake.set()

    def apply_settings(self, patch):
        """Merge a partial config into the running one, hot-apply it, and save
        it if the disk allows. The running config never depends on the disk."""
        config = cfg.merge(self.config, patch)
        cfg.save(config)
        with self._lock:
            old, new = self.config["display"], config["display"]
            self.config = config
            if old["driver"] != new["driver"]:
                # new panel, maybe a new shape: rebuild the mode for it. The old panel is put to
                # sleep by the display thread, which may be mid-frame on it (a driver that doesn't
                # match the HAT waits a long while for answers that never come) — the request
                # that changed the setting doesn't wait for that
                self._retired = self.panel
                self.panel = panels.make(new)
                self._attach_buttons()
                self._size_the_words()
                self._pending_mode = self.mode_name or config.get("startup_mode", "clock")
                if self._mode:
                    self._mode.stop()
                    self._mode = None
            else:
                self.panel.full_refresh_every = int(new["full_refresh_every"])
                if old["rotation"] != new["rotation"]:
                    if self.panel.colour and int(old["rotation"]) % 180 != int(new["rotation"]) % 180:
                        # portrait <-> landscape on an LCD: the screens draw for a new shape
                        self.panel.close(clear=False)
                        self.panel = panels.make(new)
                        self._attach_buttons()
                        self._size_the_words()
                        self._pending_mode = self.mode_name or config.get("startup_mode", "clock")
                        if self._mode:
                            self._mode.stop()
                            self._mode = None
                    else:
                        self.panel.rotation = int(new["rotation"]) % 360
                        self.panel.force_full_next()
                if old["dark_mode"] != new["dark_mode"]:
                    self.panel.force_full_next()
                if old.get("brightness") != new.get("brightness") and hasattr(self.panel, "set_brightness"):
                    self.panel.set_brightness(new.get("brightness", 70))
            if self._mode:
                self._mode.update(config)
        self._wake.set()
        return config

    def refresh(self):
        """Force a full refresh on the next frame (clears ghosting)."""
        self.panel.force_full_next()
        self._wake.set()

    def poke(self):
        """Render right now (a page turn arrived)."""
        self._wake.set()



    def status(self):
        return {
            "mode": self.mode_name,
            "panel_open": self.panel.is_open,
            "panel": self.panel.info(),
            "frames": self._frames,
            "error": self._last_error,
            "warning": self.panel.warning,         # the panel not answering: the driver doesn't match it
        }

    def preview_png(self):
        return self.panel.last_frame_png()

    def render_preview(self, mode_name, patch=None):
        """Render a frame for the UI preview without touching the panel."""
        config = cfg._deep_merge(self.config, patch or {})
        image = self._colour_frame(mode_name, config) if self.panel.colour else None
        if image is None:
            image = MODES[mode_name](config, self.panel.info()).render()
        return self.panel.preview_png(image)

    # -- colour ------------------------------------------------------------------------

    def _frame(self, mode):
        """What the active mode looks like now: on a colour panel, the colour
        version of its screen where there is one; otherwise the mode's own."""
        if self.panel.colour:
            image = self._colour_frame(mode.name, self.config)
            if image is not None:
                return image
        return mode.render()

    def _colour_frame(self, name, config):
        from . import lcd_screens
        display = config.get("display", {})
        conf = {"screen": name, "light_mode": not display.get("dark_mode"),
                "accent": display.get("accent", "#62d2ff"), "crypto_days": display.get("crypto_days", 1)}
        if name == "image":                      # a colour painting from the Draw tab beats the e-ink drawing
            from .paint import PAINT
            if PAINT.image() is None:
                return None
            conf["screen"] = "paint"
        elif name not in COLOUR_SCREENS:
            return None
        try:
            return lcd_screens.render(conf, config=config, size=self.panel.size)
        except Exception:
            log.exception("colour screen %s failed", name)
            return None

    def shutdown(self):
        self._running = False
        self._wake.set()

    # -- internals -----------------------------------------------------------

    def _switch(self, name):
        if self._mode:
            try:
                self._mode.stop()
            except Exception:
                log.exception("mode stop failed")
        try:
            self._mode = MODES[name](self.config, self.panel.info())
        except Exception as e:
            log.exception("could not create mode %s", name)
            self._last_error = f"{type(e).__name__}: {e}"
            self._mode = MODES["clock"](self.config, self.panel.info())
        log.info("mode -> %s on %s", self.mode_name, self.panel.name)
        if name == "off":
            self.panel.close(clear=True)
            return
        try:
            self.panel.open()
            self.panel.force_full_next()
            self._mode.start()
            self._last_error = None
        except Exception as e:
            log.exception("could not start mode %s", name)
            self._last_error = f"{type(e).__name__}: {e}"

    def run(self):
        while self._running:
            try:
                self._pass()
            except Exception as e:                # the one thread that draws must survive anything
                log.exception("display loop")
                self._last_error = f"{type(e).__name__}: {e}"
                self._wake.wait(5.0)
                self._wake.clear()
        if self._mode:
            self._mode.stop()
        self._retire()
        self.panel.close(clear=False)
        if self._buttons:
            self._buttons.close()

    def _retire(self):
        """A panel swapped out for another: to sleep, and off the bus and the
        GPIO lines, before the new one opens — they share both."""
        with self._lock:
            old, self._retired = self._retired, None
        if old is None:
            return
        try:
            old.close(clear=False)
        except Exception:
            log.exception("could not put the old panel to sleep")

    def _pass(self):
        """One turn of the loop: any mode change, then a frame, then a wait."""
        self.apply_cycle()
        self._retire()
        with self._lock:
            pending = self._pending_mode
            self._pending_mode = None
        if pending and pending != self.mode_name:
            self._switch(pending)
        elif pending and self._mode:
            self.panel.force_full_next()      # same mode chosen again: redraw

        wait = 5.0
        mode, panel = self._mode, self.panel          # settled for this pass: settings may swap them meanwhile
        if mode and mode.name != "off":
            try:
                if not panel.is_open:
                    panel.open()
                if panel.show(badge.apply(self._frame(mode), self.config, mode.name)):
                    self._frames += 1
                self._last_error = None
                wait = max(float(mode.interval), panel.min_interval)
            except Exception as e:
                log.exception("render failed")
                self._last_error = f"{type(e).__name__}: {e}"
                wait = 5.0
        self._wake.wait(wait)
        self._wake.clear()


_service = None


def get_service():
    global _service
    if _service is None:
        _service = DisplayService()
        _service.start()
    return _service
