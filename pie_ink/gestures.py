"""Hand gestures, through the camera: the TV and the lights, by your Home Assistant (Settings → Home).

The gestures are ha-gesture-remote's (github.com/frogCaller/ha-gesture-remote) — the same shapes and
the same feel — with the lights added:

    pinch thumb + index, twist            the volume, like turning a knob
    …with your other hand open            the lights' brightness, the same way
    fist, pushed up / down / left / right the arrows — held, they repeat like a remote's
    fist, quick tap                       OK            (other hand open: source)
    pinch thumb + index, quick tap,
      other hand open                     play / pause
    pinch thumb + middle / ring, hold     channel down / up
    pinch thumb + pinky                   back (quick) · mute (held a second)
    both fists, held                      the TV on or off
    both hands open, fingers spread, held the lights on or off

Any of them can do any of the others' jobs (Settings → Home → Hand gestures). The lights are the ones
ticked there, or — none ticked — the ones in the TV's room, as Home Assistant has the rooms.

MediaPipe's hand tracker does the seeing (21 points per hand; `pip install mediapipe`, which setup.sh
does on a 64-bit Pi, with its model, data/models/hand_landmarker.task). It looks at the camera's
picture a few times a second until a hand turns up, then as often as the Pi can manage. A Pi Zero 2 W
manages a handful of frames a second, so everything here is timed in seconds rather than counted in
frames: what takes two frames at 30 a second takes two at 5, and a tap is allowed the longer. Nothing
waits on Home Assistant: what a gesture does is sent from a thread of its own, and an arrow is never
queued behind one still on its way (a slow TV would otherwise keep scrolling after you've stopped).
While it sees a hand, the camera's little friend leaves the camera where it is.
"""
import io
import json
import logging
import math
import os
import platform
import threading
import time

from . import camera, home
from .settings import DATA_DIR

log = logging.getLogger(__name__)

MODEL = os.path.join(DATA_DIR, "models", "hand_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"

# -- what it does ------------------------------------------------------------------------------------

HOLD_ACTIONS = {"volume": "Volume", "brightness": "Lights' brightness", "dpad": "Arrows", "none": "Nothing"}
PRESS_ACTIONS = {"play_pause": "Play / pause", "select": "OK", "back": "Back", "home": "Home", "source": "Source",
                 "exit": "Exit", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "mute": "Mute",
                 "volume_up": "Volume up", "volume_down": "Volume down", "next_track": "Channel up",
                 "prev_track": "Channel down", "power": "TV on / off", "lights": "Lights on / off",
                 "lights_on": "Lights on", "lights_off": "Lights off", "brighter": "Lights brighter",
                 "dimmer": "Lights dimmer", "none": "Nothing"}
# slot -> (what you do, hold | press, what it does unless changed)
SLOTS = {
    "pinch_index_hold": ("Pinch thumb and index, then twist", "hold", "volume"),
    "pinch_index_hold_open": ("…with your other hand open", "hold", "brightness"),
    "fist_move": ("Fist, pushed up, down, left or right", "hold", "dpad"),
    "pinch_index_tap": ("Pinch thumb and index, quick tap", "press", "none"),
    "pinch_index_tap_open": ("…quick tap with your other hand open", "press", "play_pause"),
    "pinch_middle_hold": ("Pinch thumb and middle, hold", "press", "prev_track"),
    "pinch_ring_hold": ("Pinch thumb and ring, hold", "press", "next_track"),
    "pinch_pinky_tap": ("Pinch thumb and pinky, quick", "press", "back"),
    "pinch_pinky_hold": ("Pinch thumb and pinky, hold a second", "press", "mute"),
    "fist_tap": ("Fist, quick tap", "press", "select"),
    "fist_tap_open": ("…with your other hand open", "press", "source"),
    "two_fists_hold": ("Both fists, hold", "press", "power"),
    "two_open_hands_hold": ("Both hands open, fingers spread, hold", "press", "lights"),
}
SAID = {"power", "lights", "lights_on", "lights_off", "mute", "play_pause"}   # these get a line on the bot's screen

# -- how it feels (ha-gesture-remote's, in seconds) ---------------------------------------------------

PINCH_ON = {"index": 0.20, "middle": 0.20, "ring": 0.24, "pinky": 0.26}   # fingertip to thumb, in hand sizes
PINCH_OFF = {"index": 0.34, "middle": 0.34, "ring": 0.40, "pinky": 0.44}
FIST_ON, FIST_OFF = 4, 3            # fingers curled to be a fist, and to stay one
OPEN_MIN_SPREAD = 0.5               # how far the thumb sits from every fingertip on an open hand
MIN_HAND_SIZE = 0.055               # wrist to index knuckle, in frame widths: smaller is too far to trust
NEAR_HAND_SIZE = 0.11
DEBOUNCE_NEAR, DEBOUNCE_FAR = 0.06, 0.17    # a new gesture must last this long (and two frames) to count
GRACE = 0.13                        # a hand lost for this long keeps its gesture
TAP_MIN, TAP_MAX = 0.10, 0.35       # a pinch this long, that didn't wander, is a tap
TAP_MAX_TRAVEL = 0.30               # hand widths
FIST_TAP_MAX, FIST_TAP_MAX_TRAVEL = 0.50, 0.40
TAP_COOLDOWN = 0.45
REPEAT_INTERVAL = 0.8               # channel up/down while a pinch is held
MUTE_HOLD, MUTE_COOLDOWN, BACK_MAX_HOLD = 1.0, 1.5, 0.55
POWER_HOLD, POWER_COOLDOWN = 1.2, 4.0
TWO_OPEN_HOLD, TWO_OPEN_COOLDOWN = 1.5, 2.5   # (longer than ha-gesture-remote's: an open hand is also the
                                              # other one's modifier, and a relaxed open hand beside it shouldn't be the lights)
DPAD_DEADZONE, DPAD_RELEASE = 0.75, 0.55       # hand widths
DPAD_REPEAT_FIRST, DPAD_REPEAT = 0.70, 0.45
TWIST_START_DEG = 4.0
PER_TURN = 1.0                      # a whole turn of the knob: all the way
VOLUME_LIMIT = 0.8                  # the loudest a gesture sets (a misread at full volume is unpleasant)
HAND_MINCUTOFF, HAND_BETA = 0.3, 1.0
TWIST_MINCUTOFF, TWIST_BETA = 0.5, 0.6
EVERY_IDLE = 0.35                   # seconds between looks while no hand is about
FASTEST = 1 / 15                    # and at most this often while one is
IDLE_AFTER = 3.0                    # seconds without a hand before it looks less often
WIDTH = 320                         # the picture is made this small first (the tracker works at 224 anyway)

# MediaPipe's 21 points, by number
WRIST, THUMB_TIP, INDEX_MCP, MIDDLE_MCP = 0, 4, 5, 9
TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
CURL_PAIRS = [(8, 6), (12, 10), (16, 14), (20, 18)]          # (fingertip, its middle joint)
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class OneEuro:
    """The One Euro filter: steady when still, quick when moving."""

    def __init__(self, mincutoff=1.0, beta=0.0, dcutoff=1.0):
        self.mincutoff, self.beta, self.dcutoff = mincutoff, beta, dcutoff
        self.reset()

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.x = self.dx = None

    def __call__(self, x, dt):
        if self.x is None:
            self.x, self.dx = x, 0.0
            return x
        dx = (x - self.x) / dt
        a_d = self._alpha(self.dcutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        a = self._alpha(self.mincutoff + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        return self.x


def analyze(hands, mirror=True):
    """The facts the gestures need, per hand: its size, how many fingers are curled, how far each
    fingertip is from the thumb (in hand sizes), and whether it's open. `hands`: [{"points": 21 (x, y)
    in 0..1, "score"}]; mirrored, your right is the picture's right."""
    out = []
    for h in hands or []:
        pts = [((1 - x) if mirror else x, y) for x, y in h["points"]]
        if len(pts) < 21:
            continue
        wrist, thumb = pts[WRIST], pts[THUMB_TIP]
        size = max(math.hypot(wrist[0] - pts[INDEX_MCP][0], wrist[1] - pts[INDEX_MCP][1]), 1e-6)
        curled = sum(1 for tip, pip in CURL_PAIRS
                     if math.hypot(pts[tip][0] - wrist[0], pts[tip][1] - wrist[1])
                     < math.hypot(pts[pip][0] - wrist[0], pts[pip][1] - wrist[1]))
        ratios = {n: math.hypot(pts[i][0] - thumb[0], pts[i][1] - thumb[1]) / size for n, i in TIPS.items()}
        out.append({"pts": pts, "wrist": wrist, "thumb": thumb, "size": size, "curled": curled, "ratios": ratios,
                    "conf": h.get("score", 0.8), "open": curled == 0 and min(ratios.values()) > OPEN_MIN_SPREAD})
    return out


def repeat_pinch(inf, held=None):
    """A thumb + middle or thumb + ring pinch being held: 'middle', 'ring' or None."""
    if inf["curled"] >= FIST_ON:
        return None                                    # a fist parks the thumb by every fingertip
    r, best = inf["ratios"], None
    for n in ("middle", "ring"):
        thr = PINCH_OFF[n] if held == n else PINCH_ON[n]
        if r[n] < thr and r[n] < r["index"] and (best is None or r[n] < r[best]):
            best = n
    return best


class Engine:
    """The gestures, frame by frame: step(infos, now) — the hands seen, and when — and what they mean goes
    to `act` (press(action) -> a label or None; level(what) -> 0..1; set_level(what, level); levelled(what,
    level); dpad(direction); dpad_busy(); dpad_stop(); limit(what))."""

    def __init__(self, act, slots=None, invert=False):
        self.act = act
        self.slots = {k: v[2] for k, v in SLOTS.items()}
        self.slots.update({k: v for k, v in (slots or {}).items() if k in SLOTS})
        self.invert = bool(invert)
        self.fx, self.fy = OneEuro(HAND_MINCUTOFF, HAND_BETA), OneEuro(HAND_MINCUTOFF, HAND_BETA)
        self.fang = OneEuro(TWIST_MINCUTOFF, TWIST_BETA)
        self.reset()

    def reset(self):
        self.gesture = self.pending = None
        self.pending_since, self.pending_count = 0.0, 0
        self.lost_since = None
        self.last_t, self.dt_avg = None, 0.1
        self.palm = self.pinch_pt = None
        self.pinch_hold = "volume"
        self.pinch_start_t = self.pinch_start_pt = None
        self.pinch_travel, self.pinch_size, self.pinch_had_open = 0.0, 0.12, False
        self.prev_raw_ang, self.angle_acc, self.hand_angle = None, 0.0, None
        self.knob, self.knob_ref, self.knob_level, self.knob_engaged, self.knob_deg = None, None, 0.0, False, 0.0
        self.knob_last = None
        self.last_tap = self.last_repeat = self.last_mute = self.last_power = self.last_two_open = -99.0
        self.mute_held, self.mute_armed, self.mute_fired = 0.0, True, False
        self.two_fist_held = self.two_open_held = 0.0
        self.fist_start_t, self.fist_travel = None, 0.0
        self.dpad_anchor, self.dpad_dir, self.dpad_next, self.dpad_off = None, None, 0.0, (0.0, 0.0)
        self.hand_size, self.other_open, self.too_far = 0.0, False, False
        self.mode, self.flash, self.flash_at = "READY", "", 0.0
        for f in (self.fx, self.fy, self.fang):
            f.reset()

    # what a few frames a second need: a tap may last longer than it would at thirty
    def _tap_max(self):
        return max(TAP_MAX, 3.0 * self.dt_avg)

    def _fist_tap_max(self):
        return max(FIST_TAP_MAX, 3.5 * self.dt_avg)

    def _back_max(self):
        return min(max(BACK_MAX_HOLD, 3.0 * self.dt_avg), MUTE_HOLD - 0.15)

    def _fire(self, action, now):
        label = self.act.press(action) if action and action != "none" else None
        if label:
            self.flash, self.flash_at = label, now
        return label

    def step(self, infos, now):
        dt = 1 / 15 if self.last_t is None else min(max(now - self.last_t, 1 / 120), 1.0)
        self.last_t = now
        self.dt_avg = 0.8 * self.dt_avg + 0.2 * dt
        new = None
        self.mode = "READY"
        self.too_far = False
        infos = list(infos or [])
        if infos:
            near = [i for i in infos if i["size"] >= MIN_HAND_SIZE]
            self.too_far = not near
            infos = near
        if infos:
            self.lost_since = None
            new = self._shape(infos, dt, now)
        else:
            if self.lost_since is None:
                self.lost_since = now
            if self.gesture is not None and now - self.lost_since <= max(GRACE, 1.5 * self.dt_avg):
                new = self.gesture                     # lost for a blink: the gesture holds
            else:
                self.palm = None
                self.prev_raw_ang = None
                for f in (self.fx, self.fy, self.fang):
                    f.reset()
                self.two_fist_held = self.two_open_held = 0.0
        new = self._debounce(new, now)
        self._act(new, dt, now)
        self.gesture = new
        if self.too_far:
            self.mode = "TOO FAR"
        return {"gesture": new, "mode": self.mode, "hands": len(infos),
                "flash": self.flash if now - self.flash_at < 1.5 else ""}

    def _shape(self, infos, dt, now):
        n_fists = sum(1 for i in infos if i["curled"] >= FIST_ON)
        n_open = sum(1 for i in infos if i["open"])
        # both hands open, fingers spread: deliberate — nobody holds two splayed hands up by accident
        if len(infos) >= 2 and n_open >= 2:
            self.two_open_held += dt
            if self.two_open_held >= TWO_OPEN_HOLD and now - self.last_two_open > TWO_OPEN_COOLDOWN:
                self.last_two_open = now
                self._fire(self.slots["two_open_hands_hold"], now)
        else:
            self.two_open_held = 0.0
        if len(infos) >= 2 and n_fists >= 2:
            self.two_fist_held += dt
            if self.two_fist_held >= POWER_HOLD and now - self.last_power > POWER_COOLDOWN:
                self.last_power = now
                self._fire(self.slots["two_fists_hold"], now)
        else:
            self.two_fist_held = 0.0
        # the hand that's doing it: one mid-skip-pinch, else a fist, else one that isn't just open
        act = next((i for i in infos if repeat_pinch(i, self.gesture)), None) \
            or next((i for i in infos if i["curled"] >= FIST_OFF), None) \
            or next((i for i in infos if not i["open"]), infos[0])
        self.other_open = any(i is not act and i["open"] for i in infos)
        ratios, curled = act["ratios"], act["curled"]
        self.hand_size = act["size"]
        mid = act["pts"][MIDDLE_MCP]
        self.palm = (self.fx(mid[0], dt), self.fy(mid[1], dt))
        raw = math.atan2(act["wrist"][1] - mid[1], act["wrist"][0] - mid[0])   # the hand's roll, for the knob
        if self.prev_raw_ang is None:
            self.angle_acc = raw
        else:
            self.angle_acc += (raw - self.prev_raw_ang + math.pi) % (2 * math.pi) - math.pi   # unwrapped first
        self.prev_raw_ang = raw
        self.hand_angle = self.fang(self.angle_acc, dt)
        self.pinch_pt = mid           # where the hand is — not the fingertips, which move as it opens: a pinch let
                                      # go between two frames (a few a second) would look like one that wandered
        if len(infos) >= 2 and n_fists >= 2:
            return None
        g = self.gesture
        # thumb + pinky before the fist: reaching it there curls the others
        if ratios["pinky"] < (PINCH_OFF["pinky"] if g == "pinky" else PINCH_ON["pinky"]) and ratios["pinky"] < ratios["index"] * 0.7:
            return "pinky"
        if curled >= (FIST_OFF if g == "fist" else FIST_ON):
            return "fist"
        if g in ratios and ratios[g] < PINCH_OFF[g]:
            return g
        near = [n for n, r in ratios.items() if n != "pinky" and r < PINCH_ON[n]]
        return min(near, key=lambda n: ratios[n]) if near else None

    def _debounce(self, new, now):
        """A change has to last (two frames, and longer for a far, jittery hand) before it counts."""
        if new == self.gesture:
            self.pending, self.pending_count = None, 0
            return new
        if self.hand_size and self.hand_size < NEAR_HAND_SIZE:
            far = min(max((NEAR_HAND_SIZE - self.hand_size) / (NEAR_HAND_SIZE - MIN_HAND_SIZE), 0.0), 1.0)
        else:
            far = 0.0
        need = DEBOUNCE_NEAR + far * (DEBOUNCE_FAR - DEBOUNCE_NEAR)
        if new == self.pending:
            self.pending_count += 1
        else:
            self.pending, self.pending_count, self.pending_since = new, 1, now
        if self.pending_count >= 2 and now - self.pending_since >= need - 1e-9:
            self.pending, self.pending_count = None, 0
            return new
        return self.gesture

    # -- what it means ----------------------------------------------------------------------------

    def _hold(self, action, fresh, now):
        if action in ("volume", "brightness"):
            return self._knob(action, fresh, now)
        if action == "dpad":
            return self._dpad(fresh, now)
        return "READY"

    def _knob(self, what, fresh, now):
        name = "VOLUME" if what == "volume" else "LIGHTS"
        if fresh or self.knob != what:
            self.knob, self.knob_ref = what, self.hand_angle
            self.knob_level = self.act.level(what)
            self.knob_engaged, self.knob_deg, self.knob_last = False, 0.0, None
        if self.knob_level is None:                    # not known yet (it's being asked): nothing moves till it is
            self.knob_level, self.knob_ref = self.act.level(what), self.hand_angle
            if self.knob_level is None:
                return f"{name} — a moment"
        if self.hand_angle is None or self.knob_ref is None:
            return name
        sign = -1 if self.invert else 1
        deg = sign * math.degrees(self.hand_angle - self.knob_ref)
        if not self.knob_engaged and abs(deg) > TWIST_START_DEG:
            self.knob_engaged = True                   # past the dead band: no jump by its width
            self.knob_ref += math.radians(TWIST_START_DEG * sign * (1 if deg > 0 else -1))
            deg = sign * math.degrees(self.hand_angle - self.knob_ref)
        self.knob_deg = deg
        if self.knob_engaged:
            level = min(max(self.knob_level + deg / 360.0 * PER_TURN, 0.0), self.act.limit(what))
            if self.knob_last is None or abs(level - self.knob_last) >= 0.005:
                self.knob_last = level
                self.act.set_level(what, level)
            return f"{name} {level * 100:.0f}%"
        return f"{name} — twist"

    def _knob_done(self):
        if self.knob and self.knob_engaged and self.knob_last is not None:
            self.act.levelled(self.knob, self.knob_last)
        self.knob, self.knob_ref, self.knob_engaged, self.knob_deg, self.knob_last = None, None, False, 0.0, None

    def _dpad(self, fresh, now):
        if fresh:
            self.dpad_anchor, self.dpad_dir, self.dpad_next = self.palm, None, 0.0
        self.dpad_off = (0.0, 0.0)
        if self.palm is not None and self.dpad_anchor is not None:
            scale = max(self.hand_size or self.pinch_size, 1e-6)
            self.dpad_off = ((self.palm[0] - self.dpad_anchor[0]) / scale, (self.palm[1] - self.dpad_anchor[1]) / scale)
            dist = math.hypot(*self.dpad_off)
            self.fist_travel = max(self.fist_travel, dist)
            if dist >= DPAD_DEADZONE:                  # an axis, not a diagonal: a remote has four buttons
                if abs(self.dpad_off[0]) > abs(self.dpad_off[1]):
                    d = "right" if self.dpad_off[0] > 0 else "left"
                else:
                    d = "down" if self.dpad_off[1] > 0 else "up"
            elif dist < DPAD_RELEASE:
                d = None                               # back in the middle: re-armed
            else:
                d = self.dpad_dir
            if d != self.dpad_dir:
                if self.dpad_dir and not d:
                    self.act.dpad_stop()               # let go: anything still queued is a press not wanted
                self.dpad_dir = d
                if d and not self.act.dpad_busy():
                    self.act.dpad(d)
                    self.dpad_next = now + DPAD_REPEAT_FIRST
                    self.flash, self.flash_at = d.upper(), now
            elif d and now >= self.dpad_next and not self.act.dpad_busy():
                self.act.dpad(d)                       # repeats only once the last press has gone through
                self.dpad_next = now + DPAD_REPEAT
                self.flash, self.flash_at = d.upper(), now
        return f"ARROWS {self.dpad_dir.upper()}" if self.dpad_dir else "ARROWS — push"

    def _act(self, new, dt, now):
        g = self.gesture
        if new == "index":
            self.fist_start_t = None
            if g != "index":
                self.pinch_start_t, self.pinch_start_pt = now, self.pinch_pt
                self.pinch_travel, self.pinch_size, self.pinch_had_open = 0.0, self.hand_size or 0.12, False
                self.pinch_hold = self.slots["pinch_index_hold_open" if self.other_open else "pinch_index_hold"]
            elif self.pinch_pt and self.pinch_start_pt:
                self.pinch_travel = max(self.pinch_travel, math.hypot(self.pinch_pt[0] - self.pinch_start_pt[0],
                                                                      self.pinch_pt[1] - self.pinch_start_pt[1]))
            if self.other_open:
                self.pinch_had_open = True             # latched: the hand may drop before you let go
            self.mode = self._hold(self.pinch_hold, g != "index", now)
        elif new in ("middle", "ring"):
            action = self.slots["pinch_ring_hold" if new == "ring" else "pinch_middle_hold"]
            self.mode = PRESS_ACTIONS.get(action, action).upper()
            self.fist_start_t = None
            if g != new:
                self.last_repeat = now
                self._fire(action, now)                # at once, then again while it's held
            elif now - self.last_repeat >= REPEAT_INTERVAL:
                self.last_repeat = now
                self._fire(action, now)
        elif new == "pinky":
            self.fist_start_t = None
            self.mute_held += dt
            if self.mute_armed and self.mute_held >= MUTE_HOLD and now - self.last_mute > MUTE_COOLDOWN:
                self.mute_armed, self.mute_fired, self.last_mute = False, True, now
                self.mode = self._fire(self.slots["pinch_pinky_hold"], now) or "-"
            elif self.mute_held < self._back_max():
                self.mode = f"{PRESS_ACTIONS.get(self.slots['pinch_pinky_tap'], '').upper()} if you let go now"
            else:
                self.mode = f"{PRESS_ACTIONS.get(self.slots['pinch_pinky_hold'], '').upper()} in {max(MUTE_HOLD - self.mute_held, 0):.1f}s"
        elif new == "fist":
            if g != "fist":
                self.fist_start_t, self.fist_travel = now, 0.0
            self.mode = self._hold(self.slots["fist_move"], g != "fist", now)
        else:
            # let go of something
            if g == "index":
                held = now - self.pinch_start_t if self.pinch_start_t is not None else 0.0
                travel = self.pinch_travel / max(self.pinch_size, 1e-6)
                tap = self.pinch_start_t is not None and TAP_MIN <= held <= self._tap_max() and travel <= TAP_MAX_TRAVEL \
                    and not self.knob_engaged
                action = self.slots["pinch_index_tap_open" if self.pinch_had_open else "pinch_index_tap"]
                if tap and not self.too_far and now - self.last_tap > TAP_COOLDOWN and self._fire(action, now):
                    self.last_tap = now
                self._knob_done()
            elif g == "fist":
                tap = self.fist_start_t is not None and now - self.fist_start_t <= self._fist_tap_max() \
                    and self.fist_travel <= FIST_TAP_MAX_TRAVEL
                action = self.slots["fist_tap_open" if self.other_open else "fist_tap"]
                if tap and not self.too_far and now - self.last_tap > TAP_COOLDOWN and self._fire(action, now):
                    self.last_tap = now
                self._knob_done()
            elif g == "pinky":
                if not self.mute_fired and TAP_MIN <= self.mute_held <= self._back_max() and not self.too_far \
                        and now - self.last_tap > TAP_COOLDOWN and self._fire(self.slots["pinch_pinky_tap"], now):
                    self.last_tap = now
            self.pinch_start_t = self.pinch_start_pt = None
            self.pinch_travel, self.fist_start_t, self.fist_travel = 0.0, None, 0.0
            if self.dpad_dir:
                self.act.dpad_stop()                   # opened your hand mid-arrow: stop now
            self.dpad_anchor, self.dpad_dir, self.dpad_off = None, None, (0.0, 0.0)
        if new != "pinky":
            self.mute_held, self.mute_armed, self.mute_fired = 0.0, True, False
        if new not in ("index", "fist") and self.knob:
            self._knob_done()


# -- through Home Assistant ----------------------------------------------------------------------------

class Remote:
    """What a gesture does, sent from a thread of its own: an arrow is dropped while one is still on its
    way, a knob sends its newest level (a few a second at most), anything else waits its turn."""

    KEYS = {"select": "enter", "back": "back", "home": "home", "source": "source", "exit": "exit",
            "up": "up", "down": "down", "left": "left", "right": "right"}
    LEVEL_EVERY = {"volume": 0.12, "brightness": 0.3}

    def __init__(self, house=None):
        self.house = house or home.HOME
        self.lights_conf = []
        self.volume_limit = VOLUME_LIMIT
        self.on_said = None              # (text, action): a line for the bot's screen
        self._cv = threading.Condition()
        self._jobs = []                  # (kind, fn)
        self._levels = {}                # what -> newest level wanted
        self._sent_at = {}
        self._busy = None                # the kind of the job on its way
        self._thread = None
        self._stop = False
        self.volume = None               # what the TV's at, as last seen (or set from here); None: not known yet
        self.brightness = None           # ...and the lights (0 when they're off)
        self.tv_features = 0
        self._set_at = {}                # what -> when a level was last set from here
        self._last_sent = {}
        self._group = ([], 0.0)          # the lights, and when they were worked out
        self.error = None
        self.sent = []                   # (what, when): the last few, for the page

    # the engine's side
    def limit(self, what):
        return self.volume_limit if what == "volume" else 1.0

    def level(self, what):
        return self.volume if what == "volume" else self.brightness

    def set_level(self, what, level):
        if what == "volume":
            self.volume = level
        else:
            self.brightness = level
        self._set_at[what] = time.time()
        with self._cv:
            self._levels[what] = level
            self._cv.notify()
        self._start()

    def levelled(self, what, level):
        if self.on_said:
            self.on_said(f"Volume {level * 100:.0f}%." if what == "volume" else
                         (f"The lights at {level * 100:.0f}%." if level > 0 else "The lights off."), what)

    def dpad(self, direction):
        self._queue("dpad", lambda: self.house.key(self.KEYS[direction]), direction)

    def dpad_busy(self):
        with self._cv:
            return self._busy == "dpad" or any(j[0] == "dpad" for j in self._jobs)

    def dpad_stop(self):
        with self._cv:
            self._jobs = [j for j in self._jobs if j[0] != "dpad"]

    def press(self, action):
        """Send one, unless it's nothing. The label to flash."""
        if not action or action == "none" or action not in PRESS_ACTIONS:
            return None
        if action in self.KEYS:
            self._queue("press", lambda: self.house.key(self.KEYS[action]), action)
        elif action == "play_pause":
            self._queue("press", lambda: self.house.call("media_player", "media_play_pause", {"entity_id": self.house._tv()}), action)
        elif action == "mute":
            self._queue("press", lambda: self.house.tv_mute(None), action)
        elif action in ("volume_up", "volume_down"):
            self._queue("press", lambda: self.house.tv_volume("up" if action == "volume_up" else "down"), action)
        elif action in ("next_track", "prev_track"):
            self._queue("press", lambda: self.house.tv_channel_step("up" if action == "next_track" else "down"), action)
        elif action == "power":
            self._queue("press", self._power, action)
        elif action in ("lights", "lights_on", "lights_off"):
            self._queue("press", lambda: self._lights({"lights": None, "lights_on": True, "lights_off": False}[action]), action)
        elif action in ("brighter", "dimmer"):
            self._queue("press", lambda: self._step_lights(0.2 if action == "brighter" else -0.2), action)
        label = PRESS_ACTIONS[action]
        if action in ("play_pause", "mute") and self.on_said:
            self.on_said(label + ".", action)          # (the TV and the lights say what they did once it's done)
        return label.upper()

    # the sending
    def _queue(self, kind, fn, what):
        with self._cv:
            self._jobs.append((kind, fn, what))
            self._cv.notify()
        self._start()

    def _start(self):
        with self._cv:
            self._stop = False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="gesture-send", daemon=True)
            self._thread.start()

    def stop(self):
        with self._cv:
            self._stop = True
            self._jobs.clear()
            self._levels.clear()
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._stop and not self._jobs and not self._due():
                    self._cv.wait(0.1 if self._levels else 2.0)
                if self._stop:
                    return
                level = self._due()
                if level:
                    what, value = level, self._levels.pop(level)
                    job = ("level", lambda: self._send_level(what, value), what)
                else:
                    job = self._jobs.pop(0)
                self._busy = job[0]
            try:
                got = job[1]()
                okay = got[0] if isinstance(got, tuple) else got is not False
                self.error = None if okay else (got[1] if isinstance(got, tuple) else "it didn't go")
                self.sent = (self.sent + [(job[2], time.time(), bool(okay))])[-8:]
            except Exception as e:
                self.error = str(e)[:120]
                log.info("gesture: %s: %s", job[2], e)
            finally:
                with self._cv:
                    self._busy = None

    def _due(self):
        now = time.time()
        for what in self._levels:
            if now - self._sent_at.get(what, 0) >= self.LEVEL_EVERY.get(what, 0.2):
                return what
        return None

    def _send_level(self, what, level):
        self._sent_at[what] = time.time()
        before = self._last_sent.get(what)
        self._last_sent[what] = level
        if what == "volume":
            tv = self.house._tv()
            if not tv:
                return False, "no TV is picked"
            if self.tv_features and not self.tv_features & 4:          # no volume_set on this one: a step at a time
                if before is not None and abs(level - before) < 0.02:
                    self._last_sent[what] = before                     # (not far enough for a step yet)
                    return True, ""
                return self.house.tv_volume("up" if before is None or level >= before else "down")
            return self.house.call("media_player", "volume_set", {"entity_id": tv, "volume_level": round(level, 3)})
        lights = self.group()
        if not lights:
            return False, "no lights"
        if level <= 0.005:
            return self.house.call("light", "turn_off", {"entity_id": lights})
        return self.house.call("light", "turn_on", {"entity_id": lights, "brightness_pct": max(1, round(level * 100))})

    def _power(self):
        st = self.house.state_of(self.house._tv()) if self.house._tv() else None
        on = (st or {}).get("state") in home.ON_STATES
        got = self.house.tv_power("off" if on else "on")
        if self.on_said and (got[0] if isinstance(got, tuple) else got):
            self.on_said("The TV off." if on else "The TV on.", "power")
        return got

    # the lights
    def group(self, fresh=False):
        """The lights it works: the ones ticked, or the ones in the TV's room (as Home Assistant has its
        rooms), or — the TV in no room — every light."""
        if self.lights_conf:
            return list(self.lights_conf)
        lights, when = self._group
        if not fresh and lights and time.time() - when < 300:
            return lights
        found = []
        tv = self.house._tv()
        try:
            if tv:
                raw = self.house._render("{{ area_entities(area_id('%s')) | select('match', 'light\\\\.') | list | tojson }}" % tv)
                found = [x for x in json.loads(raw or "[]") if isinstance(x, str) and x.startswith("light.")]
        except Exception as e:
            log.info("gesture: the TV's room: %s", e)
        if not found:
            found = [s.get("entity_id") for s in self.house.states() if str(s.get("entity_id", "")).startswith("light.")]
        self._group = (found, time.time())
        return found

    def _states(self, lights):
        out = []
        for eid in lights:
            st = self.house.state_of(eid)
            if st:
                out.append(st)
        return out

    def refresh(self):
        """What the TV's volume and the lights' brightness are now, for a knob to start from."""
        quiet = lambda what: time.time() - self._set_at.get(what, 0) < 2.0     # (just set from here: HA lags)
        tv = self.house._tv()
        if tv:
            st = self.house.state_of(tv)
            if st:
                self.tv_features = self.house.features(st=st) or 0
                v = (st.get("attributes") or {}).get("volume_level")
                if isinstance(v, (int, float)) and not quiet("volume"):
                    self.volume = float(v)
                elif self.volume is None and st.get("state") in home.ON_STATES:
                    self.volume = 0.3 if not isinstance(v, (int, float)) else float(v)
        lights = self.group()
        if lights and not quiet("brightness"):
            states = self._states(lights[:12])
            if states:
                on = [s for s in states if s.get("state") == "on"]
                levels = [((s.get("attributes") or {}).get("brightness") or 255) / 255 for s in on]
                self.brightness = (sum(levels) / len(levels)) if levels else 0.0

    def _lights(self, want):
        lights = self.group()
        if not lights:
            return False, "no lights found in Home Assistant"
        if want is None:
            want = not any(s.get("state") == "on" for s in self._states(lights[:12]))
        got = self.house.call("light", "turn_on" if want else "turn_off", {"entity_id": lights})
        if got[0]:
            self.brightness = (self.brightness or 1.0) if want else 0.0
            if self.on_said:
                self.on_said("The lights on." if want else "The lights off.", "lights")
        return got

    def _step_lights(self, delta):
        lights = self.group()
        if not lights:
            return False, "no lights found in Home Assistant"
        return self.house.call("light", "turn_on", {"entity_id": lights, "brightness_step_pct": int(delta * 100)})


# -- the camera's side --------------------------------------------------------------------------------

def why_not():
    """Why hand gestures can't run on this Pi, or ""."""
    if platform.machine() not in ("aarch64", "arm64", "x86_64", "AMD64"):
        return "Hand gestures need the 64-bit Raspberry Pi OS (MediaPipe has no 32-bit build)."
    try:
        import mediapipe  # noqa: F401
    except Exception:
        return "Hand gestures need MediaPipe — run setup.sh again to install it."
    return ""


class Tracker:
    """MediaPipe's hand tracker, in its video mode (it follows a hand from one frame to the next, so it's
    quicker once it has found one)."""

    def __init__(self, model=None):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
        self._mp = mp
        opts = vision.HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=model or MODEL),
                                            running_mode=vision.RunningMode.VIDEO, num_hands=2,
                                            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
                                            min_tracking_confidence=0.5)
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._t = 0

    def hands(self, img):
        """[{"points": 21 (x, y), "score"}] in a PIL RGB picture."""
        import numpy as np
        self._t = max(self._t + 1, int(time.monotonic() * 1000))
        res = self._lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                                                       data=np.ascontiguousarray(np.asarray(img))), self._t)
        out = []
        for i, hand in enumerate(res.hand_landmarks or []):
            try:
                score = res.handedness[i][0].score
            except Exception:
                score = 0.8
            out.append({"points": [(p.x, p.y) for p in hand], "score": score})
        return out

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass


def fetch_model(path=MODEL, url=MODEL_URL):
    """The tracker's model (about 8 MB), once."""
    import requests
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    if os.path.getsize(part) < 1_000_000:
        os.remove(part)
        raise RuntimeError("the model came back too small")
    os.replace(part, path)


class Gestures:
    """The loop: a picture, the hands in it, what they mean."""

    def __init__(self):
        self.conf = {}
        self.remote = Remote()
        self.engine = Engine(self.remote)
        self.make_tracker = Tracker            # (a test hands it hands of its own)
        self.state, self.why = "off", ""
        self.fps, self.hands, self.status = 0.0, 0, {}
        self.last = None                       # {"label", "when"}
        self._thread = None
        self._stop = threading.Event()
        self._view = None                      # the newest picture with the hands drawn on it, while it's wanted
        self._view_wanted = 0.0
        self._seen_at = 0.0
        self._refreshed = 0.0
        self._prev_t = 0.0
        self._lock = threading.Lock()

    def configure(self, conf):
        conf = dict(conf or {})
        self.conf = conf
        self.engine = Engine(self.remote, {k: conf[k] for k in SLOTS if k in conf}, conf.get("invert"))
        self.remote.lights_conf = [x for x in conf.get("lights") or [] if isinstance(x, str) and x.startswith("light.")]
        self.remote._group = ([], 0.0)
        try:
            self.remote.volume_limit = min(max(float(conf.get("volume_limit", VOLUME_LIMIT)), 0.05), 1.0)
        except (TypeError, ValueError):
            self.remote.volume_limit = VOLUME_LIMIT
        if conf.get("enabled"):
            self.start()
        else:
            self.stop()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gestures", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.state, self.why = "off", ""

    def want_view(self, seconds=4.0):
        """The page is showing the picture: draw the hands on it for a while."""
        self._view_wanted = time.time() + seconds
        with self._lock:
            return self._view

    def _ready(self):
        h = home.HOME
        if not (h.enabled() and h.ready()):
            return "Home Assistant isn't set up yet (above)."
        if not h._tv() and not self.remote.group():
            return "Pick the TV above (or have some lights in Home Assistant)."
        return ""

    def _run(self):
        tracker = None
        try:
            while not self._stop.is_set():
                why = why_not() if tracker is None and self.make_tracker is Tracker else ""
                if not why and tracker is None and self.make_tracker is Tracker and not os.path.exists(MODEL):
                    self.state, self.why = "starting", "Fetching the hand tracker's model (about 8 MB)…"
                    try:
                        fetch_model()
                    except Exception as e:
                        why = f"Couldn't fetch the hand tracker's model ({str(e)[:80]}) — setup.sh fetches it too."
                why = why or self._ready()
                if why:
                    self.state, self.why = "waiting", why
                    self._stop.wait(10)
                    continue
                if tracker is None:
                    try:
                        tracker = self.make_tracker()
                    except Exception as e:
                        self.state, self.why = "waiting", f"The hand tracker wouldn't start: {str(e)[:120]}"
                        log.warning("gestures: %s", e)
                        self._stop.wait(30)
                        continue
                started = time.time()
                camera.CAMERA.touch(6)
                img = camera.CAMERA.frame()
                if img is None:
                    self.state, self.why = "waiting", "No picture from the camera" + (
                        " (it's paused)." if camera.CAMERA.paused_for() else ".")
                    self._stop.wait(1.0)
                    continue
                self.state, self.why = "watching", ""
                self._look(tracker, img, started)
                spent = time.time() - started
                idle = started - self._seen_at > IDLE_AFTER
                self._stop.wait(max(0.0, (EVERY_IDLE if idle else FASTEST) - spent))
        finally:
            if tracker is not None and hasattr(tracker, "close"):
                tracker.close()
            self.remote.stop()
            self.state = "off"

    def _look(self, tracker, img, now):
        w, h = img.size
        if w >= camera.TWO_VIEWS * h:                  # two lenses side by side: one of them is enough
            img = img.crop((0, 0, w // 2, h))
            w //= 2
        if w > WIDTH:
            img = img.resize((WIDTH, max(1, round(h * WIDTH / w))))
        hands = tracker.hands(img)
        infos = analyze(hands, self.conf.get("mirror", True))
        if infos:
            if now - self._seen_at > IDLE_AFTER or now - self._refreshed > 5:
                self._refreshed = now                  # a hand: where the volume and the lights are, to start from
                threading.Thread(target=self._refresh, daemon=True).start()
            self._seen_at = now
            self._still()
        self.status = self.engine.step(infos, now)
        if self.status.get("flash") and (not self.last or self.last["label"] != self.status["flash"]
                                         or now - self.last["when"] > 1.5):
            self.last = {"label": self.status["flash"], "when": now}
        self.hands = len(infos)
        dt = max(now - self._prev_t, 1e-3) if self._prev_t else 0.0
        self._prev_t = now
        if dt:
            self.fps = round(0.8 * self.fps + 0.2 / dt, 1) if self.fps else round(1 / dt, 1)
        if time.time() < self._view_wanted:
            self._draw(img, hands)

    def _refresh(self):
        try:
            self.remote.refresh()
        except Exception as e:
            log.debug("gestures: refresh: %s", e)

    def _still(self):
        """The camera's little friend leaves the camera where it is while you're gesturing."""
        try:
            from . import ptz
            ptz.PTZ.user_moved()
        except Exception:
            pass

    def _draw(self, img, hands):
        from PIL import ImageDraw
        im = img.copy()
        if self.conf.get("mirror", True):
            from PIL import ImageOps
            im = ImageOps.mirror(im)
        d = ImageDraw.Draw(im)
        W, H = im.size
        mirror = self.conf.get("mirror", True)
        for hnd in hands:
            pts = [(((1 - x) if mirror else x) * W, y * H) for x, y in hnd["points"]]
            for a, b in BONES:
                d.line([pts[a], pts[b]], fill=(90, 200, 255), width=2)
            for i, p in enumerate(pts):
                r = 4 if i in TIPS.values() or i == THUMB_TIP else 2
                d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(255, 210, 90))
        mode = (self.status or {}).get("mode") or ""
        flash = (self.status or {}).get("flash") or ""
        d.rectangle([0, 0, W, 18], fill=(0, 0, 0))
        d.text((6, 3), mode + (f"   {flash}" if flash else ""), fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        with self._lock:
            self._view = buf.getvalue()

    def report(self):
        """For the page."""
        last = self.last
        sent = self.remote.sent[-1] if self.remote.sent else None
        return {"enabled": bool(self.conf.get("enabled")), "state": self.state, "why": self.why,
                "fps": self.fps, "hands": self.hands, "mode": (self.status or {}).get("mode", ""),
                "gesture": (self.status or {}).get("gesture"),
                "last": {"label": last["label"], "ago": round(time.time() - last["when"], 1)} if last else None,
                "sent": {"what": sent[0], "ok": sent[2], "ago": round(time.time() - sent[1], 1)} if sent else None,
                "error": self.remote.error,
                "slots": [{"id": k, "label": v[0], "kind": v[1], "default": v[2]} for k, v in SLOTS.items()],
                "holds": HOLD_ACTIONS, "presses": PRESS_ACTIONS}

    def words(self):
        """For the bot's context: that it watches for hands, and what they do."""
        if not self.conf.get("enabled") or self.state == "off":
            return ""
        slots = {k: v[2] for k, v in SLOTS.items()}
        slots.update({k: self.conf[k] for k in SLOTS if k in self.conf})
        bits = [f"{SLOTS[k][0].lower()}: {(HOLD_ACTIONS if SLOTS[k][1] == 'hold' else PRESS_ACTIONS).get(a, a).lower()}"
                for k, a in slots.items() if a != "none"]
        return ("Your camera watches for hand gestures that work the TV and the lights through Home Assistant — "
                + "; ".join(bits) + ".")


GESTURES = Gestures()
