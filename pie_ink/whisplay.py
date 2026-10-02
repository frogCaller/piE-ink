"""The PiSugar Whisplay HAT's button and RGB LED.

The screen is a panel (panels/whisplay.py) and the sound card is an ALSA
device once PiSugar's driver is in; this is the rest of the board:

  the button  a short press does what a HAT key does (Settings → Screen);
              holding it is "talk to me": the bot listens as if it had heard
              the wake phrase, and if it was talking it stops first
  the LED     says what the bot is up to — blue while it listens, amber while
              it thinks, green while it speaks, purple while the bots chat
              among themselves, off when there is nothing to say

Pins are the HAT's (BCM): button 17, LED red 25 / green 24 / blue 23, all
driven low-side, so a channel is on when its pin is low.
"""
import logging
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

CARD = "whisplaysound"       # what PiSugar's driver calls the sound card
MIXER = {"speaker": 80, "mic": 80}


def sound_card():
    """The HAT's sound card as ALSA sees it: {"index", "playback", "capture"}
    or None when the driver isn't loaded. Its speaker is the only speaker
    the HAT has, so nothing is heard until it is picked."""
    if not shutil.which("aplay"):
        return None
    found = None
    for tool, key in (("aplay", "playback"), ("arecord", "capture")):
        try:
            listing = subprocess.run([tool, "-l"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in listing.splitlines():
            m = re.match(r"card (\d+): (\S+) \[", line)
            if m and m.group(2).lower().startswith("whisplay"):
                found = found or {"index": int(m.group(1)), "playback": None, "capture": None}
                found[key] = f"plughw:CARD={m.group(2)},DEV=0"     # by name: its number moves with USB sound
    return found


def set_levels(levels=None):
    """The driver's own controls, speaker and mic, 0-100 — the boot service
    sets them to 80, but they can be off after a fresh install."""
    if not shutil.which("amixer"):
        return False
    okay = True
    for name, value in (levels or MIXER).items():
        try:
            r = subprocess.run(["amixer", "-c", CARD, "cset", f"name={name}", str(int(value))],
                               capture_output=True, timeout=5)
            okay = okay and r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            okay = False
    return okay

BUTTON = 17
RED, GREEN, BLUE = 25, 24, 23
HOLD_SECONDS = 0.6           # how long a press has to last to mean "talk to me"
COLOURS = {
    "off": (0, 0, 0), "listening": (0, 80, 255), "thinking": (255, 120, 0), "speaking": (0, 200, 60),
    "chatting": (170, 0, 220), "held": (255, 255, 255), "error": (255, 0, 0),
}


class Board:
    def __init__(self):
        self.chip = None
        self._lgpio = None
        self.thread = None
        self.stop_flag = threading.Event()
        self.on_press = None                    # () -> None, a short press
        self.on_hold = None                     # () -> None, held long enough
        self.on_release = None                  # () -> None, after a hold ends
        self.mood = None                        # () -> one of COLOURS, asked ten times a second
        self.led_on = True
        self.error = None
        self._colour = None
        self.pressed = False

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.running():
            return True
        try:
            import lgpio
            from . import gpio
            self._lgpio = lgpio
            self.chip = gpio.open_header_chip(lgpio)
            lgpio.gpio_claim_input(self.chip, BUTTON)          # the HAT has its own pull-down
            for pin in (RED, GREEN, BLUE):
                lgpio.gpio_claim_output(self.chip, pin, 1)     # high = off
            self.error = None
        except Exception as e:
            self.error = f"{type(e).__name__}: {str(e)[:120]}"
            log.warning("whisplay board: %s", self.error)
            return False
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="whisplay")
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
        try:
            if self._lgpio and self.chip is not None:
                self._set((0, 0, 0))
                for pin in (BUTTON, RED, GREEN, BLUE):
                    try:
                        self._lgpio.gpio_free(self.chip, pin)
                    except Exception:
                        pass
                self._lgpio.gpiochip_close(self.chip)
        except Exception:
            pass
        self.chip = None

    def status(self):
        return {"running": self.running(), "error": self.error, "pressed": self.pressed, "led": self._colour}

    # -- the LED ----------------------------------------------------------------------------

    def _set(self, rgb):
        if rgb == self._colour or self._lgpio is None or self.chip is None:
            return
        self._colour = rgb
        for pin, level in zip((RED, GREEN, BLUE), rgb):
            pct = max(0, min(100, int(level) * 100 // 255))
            try:
                # low-side drive: the pin's high time is the LED's off time, so 0% is always high
                self._lgpio.tx_pwm(self.chip, pin, 200, 100 - pct)
            except Exception:
                log.debug("led", exc_info=True)

    # -- the loop ---------------------------------------------------------------------------

    def _run(self):
        g = self._lgpio
        down_at = None
        held = False
        tick = 0
        while not self.stop_flag.is_set():
            try:
                pressed = bool(g.gpio_read(self.chip, BUTTON))
            except Exception:
                pressed = False
            now = time.time()
            if pressed and not self.pressed:
                down_at, held = now, False
            elif pressed and down_at and not held and now - down_at >= HOLD_SECONDS:
                held = True
                self._fire(self.on_hold)
            elif not pressed and self.pressed:
                if held:
                    self._fire(self.on_release)
                elif down_at and now - down_at >= 0.03:
                    self._fire(self.on_press)
                down_at, held = None, False
            self.pressed = pressed
            tick += 1
            if tick % 5 == 0:                          # the LED follows the mood, ten times a second
                colour = (0, 0, 0)
                if self.led_on:
                    try:
                        colour = COLOURS.get(self.mood() if self.mood else "off", (0, 0, 0))
                    except Exception:
                        colour = (0, 0, 0)
                if held and self.led_on:
                    colour = COLOURS["held"]
                self._set(colour)
            time.sleep(0.02)
        self._set((0, 0, 0))

    def _fire(self, cb):
        if not cb:
            return
        try:
            threading.Thread(target=cb, daemon=True).start()
        except Exception:
            log.exception("whisplay button handler failed")


BOARD = Board()
