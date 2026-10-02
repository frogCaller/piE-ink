"""The keys on a HAT, read with lgpio (active low, internal pull-ups)."""
import logging
import threading
import time

log = logging.getLogger(__name__)


class Buttons:
    def __init__(self, pins, handler):
        import lgpio
        from .gpio import open_header_chip
        self.lgpio = lgpio
        self.pins = list(pins)
        self.handler = handler
        self.chip = open_header_chip(lgpio)
        for pin in self.pins:
            lgpio.gpio_claim_input(self.chip, pin, lgpio.SET_PULL_UP)
        self._stop = threading.Event()
        threading.Thread(target=self._poll, daemon=True, name="buttons").start()
        log.info("buttons on GPIO %s", self.pins)

    def _poll(self):
        last = [1] * len(self.pins)
        pressed_at = [0.0] * len(self.pins)
        while not self._stop.is_set():
            now = time.time()
            for i, pin in enumerate(self.pins):
                try:
                    v = self.lgpio.gpio_read(self.chip, pin)
                except self.lgpio.error:
                    continue
                if v == 0 and last[i] == 1 and now - pressed_at[i] > 0.25:   # falling edge, debounced
                    pressed_at[i] = now
                    try:
                        self.handler(i)
                    except Exception:
                        log.exception("button handler failed")
                last[i] = v
            time.sleep(0.03)

    def close(self):
        self._stop.set()
        for pin in self.pins:
            try:
                self.lgpio.gpio_free(self.chip, pin)
            except self.lgpio.error:
                pass
        try:
            self.lgpio.gpiochip_close(self.chip)
        except self.lgpio.error:
            pass
