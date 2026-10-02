"""The little friend.

With the camera on, it looks around the room by itself — a few spots left
and right of home, a little up, a little down, and never lower than the
camera's floor (an OBSBOT pointed down goes to sleep). It turns slowly,
easing in and out, and stays a while at each spot before the next. When it
sees you, or anyone, it says hello and its face lights up; when something
is new where it looked before, it's surprised and says so. With someone
there it stays with them, glances away now and then, and comes back. It
keeps still while you steer, while the camera follows you, and at night.

It sees two ways. OpenCV on the Pi checks for faces and shoulders every
couple of seconds, which costs little. A vision model on your Ollama box,
when there is one, takes a proper look at each new spot and whenever OpenCV
thinks someone's there — and writes what it says. Without a model it keeps
to short stock lines, and a face has to be there twice, and be alive, before
it counts (a face on a poster never moves).

What it does with a line — say it, show it, log it, put its face up — is
the app's business, through the hooks.
"""
import json
import logging
import math
import os
import random
import re
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

TICK = 2.0                 # seconds between glances (longer on a Pi that takes a while to look)
MODEL_GAP = 8.0            # at least this long between looks with the model
MODEL_EVERY = 120.0        # and one this often while someone's there, to know they still are
MODEL_REST = 300.0         # after the model fails, this long on OpenCV alone
DOUBT = 120.0              # the model said OpenCV's "face" was nobody: don't ask again about it for a while
AWAY = 90.0                # nobody seen this long: they've gone
HAPPY_FOR = 40.0           # the big smile after a hello
SURPRISED_FOR = 25.0
FACE_THEM_FOR = 20.0       # after a hello it turns a little toward them — this long, then it lets them be
SURPRISE_GAP = 240.0       # at most one "something new" this often
SPOT_SURPRISE_GAP = 1800.0  # and once per spot per half hour
FORGET = 7200.0            # a spot not seen for this long is learned again, not compared
ANIMAL_GAP = 1200.0
USER_PAUSE = 180.0         # you pointed the camera: it leaves it be this long
STAY_WITH_YOU = 3.0        # with someone there, it looks elsewhere this many times less often
HOLD = 6.0                 # however quick the settings, it stays this long at a spot after it has looked
REST_WINDOW = 600.0        # the motors' rest is reckoned over this long
REST_DEFAULT = 60.0        # …and by default they turn at most this many seconds of it
HFOV, VFOV = 70.0, 42.0    # about how much an OBSBOT Tiny 2 sees, zoomed out
NEW_CELL = 0.55            # an 8x8 cell of the small picture this different (brightness evened out) has changed
PRESENCE_EVERY = 60.0      # how often seeing someone is written down for the rhythm of the room

FACES = {
    "ahead": "(◕‿‿◕)", "left": "(☉_☉ )", "right": "( ⚆_⚆)",
    "happy": "(≧▽≦)", "glad_left": "(◕‿◕ )", "glad_right": "( ◕‿◕)",
    "surprised": "(⊙o⊙ )", "sleepy": "(⇀‿‿↼)", "off": "(-__-)", "paused": "(•_• )",
}
PLAIN = {"surprised": "(O_O )", "happy": "(^_^ )", "sleepy": "(-_- )"}   # for a font without the fancy ones

GREET = ["Hi there!", "Oh, hello!", "Hey, nice to see you.", "There you are!", "Hello, friend!",
         "Hi! I was just looking around."]
GREET_MORNING = ["Good morning!", "Morning! Sleep well?"]
GREET_EVENING = ["Good evening!", "Evening! Long day?"]
GREET_BACK = ["Welcome back!", "You're back!", "Oh, hi again!"]
GREET_MANY = ["Hello, everyone!", "Oh, visitors! Hi!", "Hi, all of you!"]
NEW = ["Ooh, what's that?", "Huh, that's new.", "Was that there before?", "Something's different over here."]
ANIMAL = ["Oh! A {a}!", "Hello, little {a}."]


def _thumb(frame):
    """The picture as 64x48 grey, with the overall brightness evened out, so
    a lamp going on isn't "something new"."""
    import numpy as np
    from PIL import Image
    a = np.asarray(frame.convert("L").resize((64, 48), Image.BILINEAR), dtype=np.float32)
    return (a - a.mean()) / (a.std() + 8.0)


def changed(a, b):
    """How many of the 48 cells (8x8 pixels each) differ between two small
    pictures. A cell only counts if it differs however the second picture is
    nudged a pixel (the gimbal comes back to a spot to within a degree or so)."""
    if a is None or b is None:
        return 0
    import numpy as np
    best = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            moved = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            cells = np.abs(a - moved).reshape(6, 8, 8, 8).mean(axis=(1, 3))
            best = cells if best is None else np.minimum(best, cells)
    return int((best > NEW_CELL).sum())


def parse(text):
    """The model's look as {"people", "animals", "new", "notes", "line"}. Tolerant:
    the first {...} in the answer, the usual small-model slips mended."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    raw = m.group()
    try:
        data = json.loads(raw)
    except ValueError:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("'", '"')))
    if not isinstance(data, dict):
        raise ValueError("the answer isn't an object")
    try:
        people = max(0, min(20, int(float(data.get("people") or 0))))
    except (TypeError, ValueError):
        people = 0
    animals = data.get("animals") or []
    if isinstance(animals, str):
        animals = [a for a in re.split(r"[,\s]+", animals) if a]
    kinds = []
    for a in animals if isinstance(animals, list) else []:
        word = str(a).strip().lower()
        word = word[:-1] if word.endswith("s") and len(word) > 3 else word
        word = {"kitten": "cat", "kitty": "cat", "puppy": "dog"}.get(word, word)
        if word and word not in ("none", "no", "null") and word not in kinds:
            kinds.append(word)

    def words(key, most):
        v = " ".join(str(data.get(key) or "").split()).strip(' "\'“”')
        return "" if v.lower().rstrip(".") in ("none", "nothing", "no", "null", "n/a", "-", "empty") else v[:most]
    line = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", words("line", 110)).strip()   # it's spoken and drawn
    return {"people": people, "animals": kinds[:4], "new": words("new", 60), "notes": words("notes", 200), "line": line}


class Buddy:
    def __init__(self):
        self.conf = {}
        self.thread = None
        self.stop_flag = threading.Event()
        self._wake = threading.Event()
        self.config = lambda: {}               # the live settings, set by the app
        self.hooks = {}                        # show / say / log / popup / presence, set by the app
        self._cascades = {}
        self._reset()

    def _reset(self):
        self.state = "off"                     # off | looking | happy | glad | surprised | sleeping
        self.why = ""                          # why it's keeping the camera still, if it is
        self.gaze = "ahead"                    # ahead | left | right: where its eyes point on the screen
        self.line, self.line_at = "", 0.0      # the last thing it said
        self.reaction, self.reacted_at = "", 0.0
        self.person_at = 0.0                   # when it last saw someone
        self.person_spot = None                # where the camera pointed then
        self.greeted_at = 0.0
        self.surprised_at = 0.0
        self.animal_at = {}
        self.spots = {}                        # (pan, tilt) -> {"thumb", "notes", "at", "surprised"}
        self.at_spot = None                    # the spot it's looking at, when it's at one
        self.next_move = 0.0
        self.model_at = 0.0
        self.model_rest = 0.0
        self.doubts = []                       # [(x, y, w, h, until)]: "faces" the model said were nobody
        self.prev = None                       # the last small picture, for movement between glances
        self.hits = 0                          # glances in a row with a face (OpenCV)
        self.stirred = False                   # that face has moved: a person, not a poster
        self._crop = None
        self.back_to_them = False              # the next turn goes back to whoever's there
        self.faced = None                      # (when, how far off centre, which way it turned)
        self.flip = [1, 1]                     # learned: which way turns toward someone, pan and tilt
        self.moved_it = False                  # it has turned the camera since it woke
        self.presence_at = 0.0
        self.took = 0.0                        # how long OpenCV took to look, last time
        self.error = None
        self.looks = 0

    # -- lifecycle -----------------------------------------------------------------------------------

    def configure(self, config_fn=None, **hooks):
        if config_fn:
            self.config = config_fn
        self.hooks.update({k: v for k, v in hooks.items() if v})

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, conf):
        self.conf = dict(conf or {})
        if not self.conf.get("enabled"):
            self.stop()
            return False
        if self.running():
            self._wake.set()
            return True
        self._reset()
        self.state = "looking"
        self.stop_flag.clear()
        self.next_move = time.time() + 4
        self.thread = threading.Thread(target=self._loop, daemon=True, name="buddy")
        self.thread.start()
        return True

    def update(self, conf):
        self.conf = dict(conf or {})
        if self.conf.get("enabled"):
            self.start(self.conf)
        else:
            self.stop()

    def stop(self):
        self.stop_flag.set()
        self._wake.set()
        if not self.running():
            self.state = "off"

    def poke(self):
        """Look somewhere now (the page's button)."""
        self.next_move = 0.0
        self._wake.set()

    def _loop(self):
        log.info("friend: on")
        while not self.stop_flag.is_set():
            try:
                self.tick()
            except Exception as e:                    # the friend outlives any one bad glance
                self.error = str(e)[:160]
                log.warning("friend: %s", e, exc_info=log.isEnabledFor(logging.DEBUG))
                self.stop_flag.wait(8)
            # a Pi that takes a while to find faces glances less often, rather than looking all the time
            self._wake.wait(max(TICK, min(8.0, self.took * 4)))
            self._wake.clear()
        if self.moved_it:
            self._go_home()                           # leave the camera where you keep it
        self.state = "off"
        log.info("friend: off")

    # -- settings ------------------------------------------------------------------------------------

    def _num(self, key, default, least, most):
        try:
            return max(least, min(most, float(self.conf.get(key, default) or default)))
        except (TypeError, ValueError):
            return float(default)

    def _every(self):
        return self._num("every_seconds", 45, 10, 600)

    def _greet_after(self):
        return self._num("greet_minutes", 15, 1, 720) * 60

    def asleep(self, now=None):
        """Night: from sleep_from to sleep_to (across midnight is fine)."""
        if not self.conf.get("night", True):
            return False
        t = datetime.fromtimestamp(now or time.time()).strftime("%H:%M")
        a, b = str(self.conf.get("sleep_from") or "23:00"), str(self.conf.get("sleep_to") or "07:00")
        if a == b:
            return False
        return (a <= t < b) if a < b else (t >= a or t < b)

    # -- a glance ------------------------------------------------------------------------------------

    def tick(self, now=None):
        from . import camera, ptz
        started = time.time()
        now = now or started
        config = self.config() or {}
        P = ptz.PTZ
        if self.asleep(now):
            if self.state != "sleeping":
                self.state, self.why = "sleeping", ""
                if self.moved_it:
                    self._go_home()                   # to bed
                    self.moved_it = False
            return
        if self.state == "sleeping":
            self.state = "looking"
            self.next_move = now + 2
        if camera.CAMERA.paused_for():
            self.why = "the camera's paused"          # a speaker or mic is being plugged in: it waits
            return
        camera.CAMERA.touch(15)                       # its eyes stay open while it's on
        if camera.CAMERA.status().get("id") == "mock":
            self.why = "no camera"                    # the test pattern: nothing to look at, nothing to say
            return
        turns = P.available() and "pan" in P.ctrls
        frame, arrived = None, False
        self.why = ""
        if turns and P.asleep:                        # an OBSBOT asleep, lens down: it would look at the desk
            if not P.wake("the little friend"):
                P.read_follow()                       # (or someone lifted it by hand)
            if P.asleep:
                self.why = "the camera's asleep"
                return
            self.prev = None
        if turns:
            free, self.why = self._may_move(now)
            if free and now >= self.next_move:
                frame = self._wander(now, config)
                arrived = frame is not None
        if frame is None:
            st = camera.CAMERA.status()
            if st.get("age") is None or st["age"] > 3:
                return                                # no fresh picture (starting, or reopening)
            if P.looking or P.driving() or now < P.move_until + 0.5:
                self.prev = None                      # the picture moves because the camera does
                return
            frame = camera.CAMERA.frame()
            if frame is None:
                return
        self.glance(frame, now, config, arrived=arrived, turns=turns)
        if arrived:                                   # it has had its look here: it stays a moment, at least
            self.next_move = max(self.next_move, now + (time.time() - started) + HOLD)

    def glance(self, frame, now, config, arrived=False, turns=False):
        """Look at one picture: someone there? an animal? something new here?"""
        thumb = _thumb(frame)
        moving = 0 if (arrived or self.prev is None) else changed(thumb, self.prev)
        self.prev = thumb
        found = self._detect(frame, cats=arrived or moving >= 2)    # ([boxes], cat) with OpenCV, or None
        boxes, cat = found if found else ([], False)
        someone_here = now - self.person_at < AWAY
        model = self._model_ok(config, now)
        if moving >= 2:
            self.doubts = []                          # something moved: look again, even where it was nobody
        fresh = [b for b in boxes if not self._doubted(b, now)]
        seen = None
        if model and (arrived or now - self.model_at >= MODEL_GAP) and (
                arrived                                   # a new spot always gets a proper look
                or (fresh and not someone_here)
                or (moving >= 4 and not someone_here)
                or (someone_here and now - self.model_at > MODEL_EVERY)
                or (not turns and now - self.model_at > self._every())):
            seen = self._look(config, frame, now)
        if seen is not None:
            people, animals = seen["people"], seen["animals"]
            if boxes and not people:                  # OpenCV's face was a poster, or a screen: leave it be
                self.doubts = [(x, y, w, h, now + DOUBT) for x, y, w, h in boxes]
        else:
            # OpenCV alone: a face counts once it's been there twice and has moved —
            # a face on a poster is there every time, and never moves
            if boxes:
                self.hits += 1
                self.stirred = self.stirred or self._alive(frame, boxes)
            else:
                self.hits, self.stirred, self._crop = 0, False, None
            # with a model about, a newcomer waits for its proper look (a moment); OpenCV alone keeps
            # track of someone already there
            believe = someone_here or (not model and self.hits >= 2 and self.stirred)
            people = len(boxes) if boxes and believe else 0
            animals = ["cat"] if cat and not model else []
        if people:
            self._someone(people, boxes, seen, frame, now, config, turns)
        elif animals:
            self._animal(animals[0], seen, frame, now)
        spot = self.at_spot if turns else (0, 0)
        if spot is not None and not people and (arrived if turns else moving <= 1) and (seen is not None or not model):
            self._compare(spot, thumb, seen, frame, now)
        if self.state in ("happy", "surprised", "glad") and now - self.reacted_at > HAPPY_FOR \
                and now - self.person_at > AWAY:
            self.state = "looking"

    # -- someone's there -----------------------------------------------------------------------------

    def _someone(self, people, boxes, seen, frame, now, config, turns):
        from . import ptz
        just_came = now - self.person_at > AWAY
        new_visit = now - self.person_at > self._greet_after()
        self.person_at = now
        if turns:
            p = ptz.PTZ.position()
            self.person_spot = (p["pan"], p["tilt"])
            if just_came:                             # found someone: stay with them a while
                self.back_to_them = False
                self.next_move = max(self.next_move, now + self._every() * STAY_WITH_YOU)
        if now - self.presence_at > PRESENCE_EVERY:
            self.presence_at = now
            self._hook("presence")
        if self.state not in ("happy", "surprised"):
            self.state = "glad"
        if new_visit and now - self.greeted_at >= self._greet_after():
            line = (seen or {}).get("line") or ""
            if not line and seen is None and self._model_ok(config, now) and now - self.model_at >= 4:
                got = self._look(config, frame, now)       # a hello in its own words, if the model's there
                line = (got or {}).get("line") or ""
            if not line:
                line = self._stock_hello(people, now)
            self.greeted_at = now
            self._react("happy", line, frame, "Person detected" if people == 1 else f"{people} people detected", "person", now)
        if turns and boxes and now - self.greeted_at < FACE_THEM_FOR:
            self._face_them(boxes, now, config)       # just said hello: look at them

    def _stock_hello(self, people, now):
        hour = datetime.fromtimestamp(now).hour
        if people > 1:
            pool = GREET_MANY
        elif self.greeted_at and datetime.fromtimestamp(self.greeted_at).date() == datetime.fromtimestamp(now).date():
            pool = GREET_BACK
        elif hour < 11:
            pool = GREET_MORNING + GREET
        elif hour >= 18:
            pool = GREET_EVENING + GREET
        else:
            pool = GREET
        return random.choice(pool)

    def _doubted(self, box, now):
        """The same "face" the model already said was nobody (a poster's barely shifts)?"""
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        return any(until > now and abs(cx - (x + w / 2)) < w * 0.35 and abs(cy - (y + h / 2)) < h * 0.35
                   for x, y, w, h, until in self.doubts)

    def _alive(self, frame, boxes):
        """Did anything move where the face is, since the last glance? The same
        patch of both pictures is compared, so a person shifting in their seat
        counts, and a face on a poster never does."""
        import numpy as np
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        grey = frame.convert("L").resize((160, 120))
        box = (int(max(0.0, x - w * 0.25) * 160), int(max(0.0, y - h * 0.25) * 120),
               int(min(1.0, x + w * 1.25) * 160), int(min(1.0, y + h * 1.25) * 120))
        prev, self._crop = self._crop, grey
        if prev is None or box[2] - box[0] < 2 or box[3] - box[1] < 2:
            return False
        a = np.asarray(grey.crop(box).resize((24, 24)), dtype=np.float32)
        b = np.asarray(prev.crop(box).resize((24, 24)), dtype=np.float32)
        return float(np.abs(a - b).mean()) > 3.0

    def _face_them(self, boxes, now, config):
        """Turn a little toward the person OpenCV found, so they're in the
        middle. Which way is which depends on how the camera's mounted and
        mirrored, so it checks: if they ended up further off, it learns the
        other way round."""
        from . import ptz
        P = ptz.PTZ
        if P.follow_on or not self._may_move(now)[0]:
            return
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        cx, cy = x + w / 2, y + h * (0.5 if h < 0.35 else 0.3)       # a head-and-shoulders box: aim at the head
        if config.get("camera", {}).get("mirror"):
            cx = 1 - cx
        dx, dy = cx - 0.5, 0.5 - cy
        if self.faced and now - self.faced[0] < 12:
            _, (odx, ody), (opan, otilt) = self.faced
            if opan and dx * odx > 0 and abs(dx) > abs(odx) + 0.05:
                self.flip[0] = -self.flip[0]
                log.info("friend: turned the wrong way — the other way round from now on")
            if otilt and dy * ody > 0 and abs(dy) > abs(ody) + 0.05:
                self.flip[1] = -self.flip[1]
        if (abs(dx) < 0.15 and abs(dy) < 0.2) or (self.faced and now - self.faced[0] < 5):
            return
        zoom = 1 + 3 * P._zoom_frac()
        dpan = max(-20.0, min(20.0, dx * HFOV / zoom)) * self.flip[0] if abs(dx) >= 0.15 else 0.0
        dtilt = max(-12.0, min(12.0, dy * VFOV / zoom)) * self.flip[1] if abs(dy) >= 0.2 else 0.0
        try:
            P.nudge(dpan=dpan, dtilt=dtilt, gentle=True)
            self.moved_it = True
            self.faced = (now, (dx, dy), (dpan, dtilt))
            p = P.position()
            self.person_spot = (p["pan"], p["tilt"])
            self.at_spot = None
            self.prev = None
            self._crop = None
        except (OSError, ValueError) as e:
            log.info("friend: couldn't turn toward them: %s", e)

    def _animal(self, kind, seen, frame, now):
        if now - self.animal_at.get(kind, 0) < ANIMAL_GAP:
            return
        self.animal_at[kind] = now
        line = (seen or {}).get("line") or random.choice(ANIMAL).format(a=kind)
        self._react("happy", line, frame, f"{kind.capitalize()} spotted", "animal", now)

    # -- something new -------------------------------------------------------------------------------

    def _compare(self, spot, thumb, seen, frame, now):
        """Back at a spot it has looked at before: is something new here?"""
        mem = self.spots.get(spot)
        notes = (seen or {}).get("notes") or ""
        if mem is None or now - mem["at"] > FORGET:
            self.spots[spot] = {"thumb": thumb, "notes": notes, "at": now, "surprised": 0.0}
            return
        diff = changed(thumb, mem["thumb"])
        if seen is not None:
            new = seen.get("new") or ""
            surprising = bool(new) and diff >= 1          # the model says so, and the picture agrees
        else:
            new = ""
            surprising = 3 <= diff <= 30                  # a part of it changed, not all of it (the light)
        if surprising and now - self.surprised_at > SURPRISE_GAP and now - mem["surprised"] > SPOT_SURPRISE_GAP:
            line = (seen or {}).get("line") or (f"Ooh, {new}!" if new else random.choice(NEW))
            self.surprised_at = mem["surprised"] = now
            self._react("surprised", line, frame, f"Something new: {new}" if new else "Something changed", "new", now)
        mem.update(thumb=thumb, notes=notes or mem["notes"], at=now)

    # -- reacting ------------------------------------------------------------------------------------

    def _react(self, kind, line, frame, event, event_kind, now=None):
        self.reaction, self.reacted_at = kind, now or time.time()
        self.line, self.line_at = line, self.reacted_at
        self.state = kind
        log.info("friend: %s — %s", event, line)
        self._hook("log", event, event_kind, "note")
        self._hook("show", line, kind, frame)
        self._hook("say", line)
        self._hook("popup", kind)

    def _hook(self, name, *args):
        fn = self.hooks.get(name)
        if not fn:
            return None
        try:
            return fn(*args)
        except Exception as e:
            log.info("friend: the %s hook failed: %s", name, e)
            return None

    # -- seeing --------------------------------------------------------------------------------------

    def _cascade(self, name):
        """Its own copies of OpenCV's detectors: they aren't safe to share across threads."""
        if name not in self._cascades:
            from . import sight
            import cv2
            c = cv2.CascadeClassifier(os.path.join(sight._cascade_dir(), sight.CASCADES[name]))
            self._cascades[name] = None if c.empty() else c
        return self._cascades[name]

    def _detect(self, frame, cats=True):
        """People (a face, or head and shoulders) and a cat's face, with OpenCV:
        ([(x, y, w, h) as fractions of the picture], cat) — None without OpenCV."""
        from . import sight
        try:
            if not sight._basic_ready():
                return None
            import cv2
            import numpy as np
        except ImportError:
            return None
        started = time.time()
        img = np.array(frame.convert("L"))
        if img.shape[1] > 480:
            img = cv2.resize(img, (480, int(img.shape[0] * 480.0 / img.shape[1])))
        img = cv2.equalizeHist(img)
        H, W = img.shape[:2]
        boxes = []
        for name, neighbours, size in (("face", 6, 28), ("body", 4, 48)):
            c = self._cascade(name)
            if c is None:
                continue
            for (x, y, w, h) in c.detectMultiScale(img, scaleFactor=1.15, minNeighbors=neighbours, minSize=(size, size)):
                boxes.append((x / W, y / H, w / W, h / H))
        people = []
        for b in sorted(boxes, key=lambda b: -b[2] * b[3]):      # a face inside head-and-shoulders is one person
            cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
            if not any(o[0] <= cx <= o[0] + o[2] and o[1] <= cy <= o[1] + o[3] for o in people):
                people.append(b)
        cat = False
        if cats:
            c = self._cascade("cat")
            cat = c is not None and len(c.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))) > 0
        self.took = time.time() - started
        return people, cat

    @staticmethod
    def _can_see(config):
        lc = config.get("llm", {})
        return bool(lc.get("enabled") and lc.get("see", True)
                    and ((lc.get("model") or "").strip() or (lc.get("vision_model") or "").strip()))

    def _model_ok(self, config, now):
        return now >= self.model_rest and self._can_see(config)

    def _where(self):
        if self.gaze in ("left", "right"):
            return f"to the {self.gaze}"
        from . import ptz
        tilt = ptz.PTZ.position()["tilt"] if ptz.PTZ.fd is not None else 0
        return "up" if tilt > 8 else "down" if tilt < -8 else "straight ahead"

    def _look(self, config, frame, now):
        """A proper look with the vision model: who and what is there, what's new
        since it last looked here, and something to say."""
        from . import llm, mascot, presence
        self.model_at = now
        lc = config.get("llm", {})
        owner = mascot._owner(config)
        persona = (lc.get("persona") or "").strip().replace("{owner}", owner)
        system = (f"You are a small friendly robot on {owner}'s desk, with a camera on a gimbal for eyes. You look "
                  "around the room by yourself. You are curious, warm and a little playful — never creepy or nosy. "
                  + (f"Your character: {persona} " if persona else "")
                  + "You answer with one JSON object and nothing else: no prose, no markdown, no code fence.")
        mem = self.spots.get(self.at_spot) if self.at_spot is not None else None
        lines = [f"It is {datetime.now().strftime('%A %-I:%M %p')}.", f"You just looked {self._where()}."]
        if mem and mem.get("notes"):
            lines.append(f"Last time you looked here you saw: {mem['notes']}")
        elif self.at_spot is not None:
            lines.append("You haven't looked here before.")
        try:
            facts = presence.remarks()
        except Exception:
            facts = []
        if facts:
            lines.append("What you know: " + "; ".join(facts))
        lines.append('Fill in exactly this JSON: {"people": <how many people you can see>, '
                     '"animals": [<each animal you can see, one word each>], '
                     '"new": "<if something here is clearly new or different since last time, a few words naming it; '
                     'else empty>", '
                     '"notes": "<one plain sentence saying what is here, to remember for next time>", '
                     '"line": "<one short thing to say out loud, under 12 words, in your own voice: a warm hello if '
                     'you see a person, a surprised remark if something is new, a hello to an animal; else empty>"}')
        lines.append("Never guess who someone is or use a name. People in photos, on posters or on screens don't count.")
        try:
            text = llm.generate(lc, system, "\n".join(lines), images=[mascot.as_base64(frame, longest=640)],
                                timeout=min(90, int(lc.get("timeout_seconds", 90) or 90)), limit=1500)
            seen = parse(text)
        except Exception as e:
            self.model_rest = now + MODEL_REST
            self.error = f"the model didn't look: {str(e)[:120]}"
            log.info("friend: %s — on OpenCV alone for a while", self.error)
            return None
        self.error = None
        self.looks += 1
        return seen

    # -- turning ---------------------------------------------------------------------------------------

    def _may_move(self, now):
        from . import ptz
        P = ptz.PTZ
        if P.follow_on:
            return False, "following you"
        if P.looking:
            return False, "looking around"
        if P.driving() or now - P.touched_at < USER_PAUSE:
            return False, "you're steering"
        # its motors get a rest: however keen the settings, the camera turns at most so many seconds
        # in ten minutes (a gimbal's motors run hot when they're kept at it), and it waits its turn
        turned = P.turned_for(REST_WINDOW)
        if turned >= self._num("rest", REST_DEFAULT, 10, 600):
            return False, f"resting its motors ({turned:.0f} s of turning in the last {REST_WINDOW // 60:.0f} min)"
        return True, ""

    def _centre(self):
        from . import ptz
        P = ptz.PTZ
        home = P.conf.get("home") or {}
        p = P.position()
        (plo, phi), (tlo, thi) = p["pan_range"], p["tilt_range"]
        try:
            cp, ct = float(home.get("pan", 0) or 0), float(home.get("tilt", 0) or 0)
        except (TypeError, ValueError):
            cp, ct = 0.0, 0.0
        return max(plo, min(phi, cp)), max(tlo, min(thi, ct)), p

    def spots_to_visit(self):
        """Where it looks: around home, left to right as far as `reach`, at eye
        level, a little up and a little down — the floor keeps it from looking
        lower than the camera allows. ([(pan, tilt)], (home pan, home tilt))."""
        cp, ct, p = self._centre()
        (plo, phi), (tlo, thi) = p["pan_range"], p["tilt_range"]
        reach = self._num("reach", 70, 10, 130)
        pans = sorted({int(round(max(plo, min(phi, cp + reach * k)))) for k in (-1, -0.5, 0, 0.5, 1)})
        tilts = sorted({int(round(max(tlo, min(thi, ct + d)))) for d in (-12, 0, 15)})
        return [(a, b) for a in pans for b in tilts], (cp, ct)

    def _pick(self, now):
        from . import ptz
        spots, (_, ct) = self.spots_to_visit()
        here = ptz.PTZ.position()
        options, weights = [], []
        for s in spots:
            dist = math.hypot(s[0] - here["pan"], s[1] - here["tilt"])
            if dist < 3:
                continue
            w = 1 + min(4.0, (now - self.spots.get(s, {}).get("at", 0)) / 120)    # long unvisited: more likely
            w *= 1.0 if s[1] == int(round(ct)) else 0.3                            # mostly eye level
            w *= 1 / (1 + dist / 90)                                                # mostly nearby
            options.append(s)
            weights.append(w)
        if not options:
            return None
        return random.choices(options, weights)[0]

    def _wander(self, now, config):
        """Turn to look somewhere, wait for the picture to settle, and return it."""
        from . import ptz
        P = ptz.PTZ
        every = self._every()
        someone = now - self.person_at < AWAY and self.person_spot is not None
        if someone and self.back_to_them:
            target, spot = self.person_spot, None
        else:
            target = self._pick(now)
            spot = target
        if target is None:
            self.next_move = now + every
            return None
        self.back_to_them = someone and not self.back_to_them
        started = time.time()
        self._gaze(target[0], config)                 # its eyes go first, the camera follows
        try:
            P.glide(pan=target[0], tilt=target[1],    # slowly, easing in and out
                    stop=lambda: self.stop_flag.is_set() or P.looking or bool(P.follow_on))
        except (OSError, ValueError) as e:
            log.info("friend: couldn't turn: %s", e)
            self.next_move = now + every
            return None
        self.moved_it = True
        self.prev, self._crop, self.doubts = None, None, []
        self.hits, self.stirred = 0, False
        here = P.position()
        if abs(here["pan"] - target[0]) > 2 or abs(here["tilt"] - target[1]) > 2:
            self.at_spot = None                       # stopped on the way (you took over): not that spot
            self.next_move = now + every
            return None
        self.at_spot = spot
        frame = P.settle()
        stay = STAY_WITH_YOU if someone and not self.back_to_them else 1.0      # back with them: stay a while
        # counted from when it got there: the turn itself isn't time spent looking
        self.next_move = now + (time.time() - started) + max(HOLD, every * stay * random.uniform(0.7, 1.3))
        if self.state not in ("happy", "surprised", "glad"):
            self.state = "looking"
        return frame

    def _gaze(self, pan, config):
        """Which way its eyes go on the screen. The screen faces you, so when the
        camera turns to one side, the face looks that way as you see it."""
        from . import ptz
        cp, _, _ = self._centre()
        sx, _ = ptz.PTZ._swaps()
        off = (pan - cp) * sx                     # positive: the way the stick's right goes
        if abs(off) < 12:
            self.gaze = "ahead"
        else:
            mirrored = bool(config.get("camera", {}).get("mirror"))
            self.gaze = "right" if (off > 0) == mirrored else "left"

    def _go_home(self):
        from . import ptz
        P = ptz.PTZ
        try:
            if P.fd is not None and self._may_move(time.time())[0]:
                P.home(gentle=True)
        except (OSError, ValueError) as e:
            log.info("friend: couldn't go home: %s", e)

    # -- what the page and the screens show ----------------------------------------------------------

    def face(self, now=None):
        """(face, mood, caption) for the screens."""
        now = now or time.time()
        if not self.conf.get("enabled"):
            return FACES["off"], "off", "Off"
        if self.state == "sleeping" or self.asleep(now):
            return FACES["sleepy"], "sleepy", f"Asleep till {self.conf.get('sleep_to') or '07:00'}"
        side = "right" if self.gaze == "right" else "left"
        if self.reaction == "surprised" and now - self.reacted_at < SURPRISED_FOR:
            return FACES["surprised"], "surprised", "Something new!"
        if self.reaction == "happy" and now - self.reacted_at < HAPPY_FOR:
            return FACES["happy"], "happy", "Hello!"
        if now - self.person_at < AWAY:
            return FACES["glad_" + side], "glad", "With you"
        if self.why:
            return FACES["paused"], "paused", self.why[:1].upper() + self.why[1:]
        words = {"left": "Looking left", "right": "Looking right"}.get(self.gaze, "Looking around")
        return FACES[self.gaze], "looking", words

    def drawable_face(self, fnt, now=None):
        """The face, in a form this font can draw (a missing glyph is an empty box)."""
        from .text import draws
        face, mood, caption = self.face(now)
        if not draws(fnt, face):
            face = PLAIN.get(mood, "(•_• )")
            if not draws(fnt, face):
                face = ":-)"
        return face, mood, caption

    def recent_line(self, within=600):
        return self.line if self.line and time.time() - self.line_at < within else ""

    def status(self):
        from . import sight
        face, mood, caption = self.face()
        now = time.time()
        config = self.config() or {}
        sees = "model" if self._can_see(config) else "basic" if sight._basic_ready() else "movement"
        return {"enabled": bool(self.conf.get("enabled")), "running": self.running(), "state": self.state,
                "face": face, "mood": mood, "caption": caption, "why": self.why,
                "line": self.line, "line_ago": round(now - self.line_at) if self.line_at else None,
                "person_ago": round(now - self.person_at) if self.person_at else None,
                "spots": len(self.spots), "sees": sees, "looks": self.looks, "error": self.error,
                "asleep": self.asleep(now)}


BUDDY = Buddy()
