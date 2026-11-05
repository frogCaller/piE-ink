"""Snake on the little screen, played by a decision model.

The snake glides through an open arena the size of the LCD and can steer toward any
compass heading, turning in a smooth curve. Every few hundred milliseconds the bot's
Ollama is asked one /v1/systemone "choice" question — which heading next? — with a
line of facts about each option: deadly or safe, closer to the food or farther, how
far it is clear, whether it leads into a dead end. The snake keeps gliding on its last
answer while the next one is on its way; the game never waits for the model, so a
late answer is the model's problem, as it should be.

Only decision models answer /v1/systemone (tev1, nimble, clef…): `ollama pull tev1`
on the machine the bot runs on. Scores are kept in data/snake.json.
"""
import json
import logging
import math
import os
import random
import threading
import time

import requests
from PIL import Image, ImageDraw

from .settings import DATA_DIR
from .text import font

log = logging.getLogger(__name__)

PATH = os.path.join(DATA_DIR, "snake.json")
DECISION_MODELS = ("tev1", "nimble", "clef")         # the ones that answer /v1/systemone
COLOURS = {"bg": (20, 23, 28), "dots": (42, 48, 58), "food": (226, 195, 91), "snake": (95, 179, 161),
           "tail": (47, 107, 96), "head": (159, 224, 209), "eye": (16, 32, 28), "bad": (217, 103, 95),
           "ink": (227, 230, 234), "dim": (139, 147, 160)}

ORDER16 = ["n", "nne", "ne", "ene", "e", "ese", "se", "sse", "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw"]
ANGLE = {k: math.radians(i * 22.5 - 90) for i, k in enumerate(ORDER16)}      # screen coordinates, y down
NAME = {"n": "north", "nne": "north-north-east", "ne": "north-east", "ene": "east-north-east", "e": "east",
        "ese": "east-south-east", "se": "south-east", "sse": "south-south-east", "s": "south",
        "ssw": "south-south-west", "sw": "south-west", "wsw": "west-south-west", "w": "west",
        "wnw": "west-north-west", "nw": "north-west", "nnw": "north-north-west", "food": "straight at the food"}


def is_decision_model(name):
    return str(name or "").split(":")[0].lower() in DECISION_MODELS


def _ang_diff(a, b):
    d = math.fmod(a - b, 2 * math.pi)
    if d > math.pi:
        d -= 2 * math.pi
    if d < -math.pi:
        d += 2 * math.pi
    return d


def _clamp(v, m):
    return max(-m, min(m, v))


class Snake:
    """One game: the geometry, the movement, and the look-ahead the model is told about.
    Distances in the facts are body widths, the unit the model can picture."""

    def __init__(self, w, h, speed=70.0, decide=0.3, headings=8, safety=False):
        self.w, self.h = float(w), float(h)
        self.body = 10.0                               # body thickness in panel pixels — also the unit
        self.seg = 2.0                                 # spacing of the stored body points
        self.food_r = self.body * 0.45
        self.turn_r = self.body * 1.15                 # turning radius, the same at every speed
        self.neck = self.body * 1.8                    # body this close to the head (along it) can't be hit
        self.hit2 = (self.body * 0.82) ** 2
        self.tail_skip = math.ceil(self.body * 0.6 / self.seg)
        self.grow = self.body * 2
        self.post_eat = self.body * 2
        self.start_len = self.body * 6
        self.speed = float(speed)
        self.decide = float(decide)
        self.headings = 16 if int(headings) == 16 else 8
        self.safety = bool(safety)
        self.lat = []                                  # recent decision latencies, seconds
        self.reset()

    # -- the state ----------------------------------------------------------------------
    def reset(self):
        self.head = {"x": self.w * 0.4, "y": self.h / 2}
        self.heading = 0.0
        self.steer = "e"
        self.length = self.start_len
        self.pts = [{"x": self.head["x"] - i * self.seg, "y": self.head["y"]} for i in range(int(self.length // self.seg) + 1)]
        self.alive, self.crash, self.why = True, None, ""
        self.score, self.decisions = 0, 0
        self.game_t, self.since_food, self.eat_t = 0.0, 0.0, -math.inf
        self.last_decision_t = -math.inf
        self.need_after_eat = False
        self.last = None                               # the last decision, for the page and the drawing
        self.place_food()

    def bw(self, px):
        return max(0, int(round(px / self.body)))

    def options(self):
        order = ORDER16 if self.headings == 16 else [k for i, k in enumerate(ORDER16) if i % 2 == 0]
        return ["food"] + order

    def place_food(self):
        m = self.body * 1.5
        for _ in range(400):
            f = {"x": m + random.random() * (self.w - 2 * m), "y": m + random.random() * (self.h - 2 * m)}
            if all((p["x"] - f["x"]) ** 2 + (p["y"] - f["y"]) ** 2 > (self.body * 1.6) ** 2 for p in self.pts):
                self.food = f
                return
        self.food = {"x": self.w / 2, "y": self.h / 2}

    # -- steering and movement --------------------------------------------------------------
    def turn_rate(self):
        return self.speed / self.turn_r

    def steer_angle(self, k, h, hd):
        """Where to point right now for a steering choice. "food" homes in on the food, but
        if the food is inside the turning circle it goes straight first to make room
        (otherwise it would orbit the food for ever)."""
        if isinstance(k, dict):
            return k["straight"]
        if k != "food":
            return ANGLE[k]
        want = math.atan2(self.food["y"] - h["y"], self.food["x"] - h["x"])
        off = _ang_diff(want, hd)
        side = 1 if off >= 0 else -1
        cx = h["x"] - math.sin(hd) * side * self.turn_r
        cy = h["y"] + math.cos(hd) * side * self.turn_r
        inside = (self.food["x"] - cx) ** 2 + (self.food["y"] - cy) ** 2 < (self.turn_r * 0.95) ** 2
        return hd if inside else want

    @staticmethod
    def compass_of(a):
        return min(ORDER16, key=lambda k: abs(_ang_diff(ANGLE[k], a)))

    def advance(self, dt):
        if not self.alive:
            return
        self.heading += _clamp(_ang_diff(self.steer_angle(self.steer, self.head, self.heading), self.heading), self.turn_rate() * dt)
        v = self.speed * dt
        self.head = {"x": self.head["x"] + math.cos(self.heading) * v, "y": self.head["y"] + math.sin(self.heading) * v}
        pts = self.pts
        while len(pts) > 1 and math.dist((self.head["x"], self.head["y"]), (pts[1]["x"], pts[1]["y"])) >= self.seg:
            a = pts[1]
            d = math.dist((self.head["x"], self.head["y"]), (a["x"], a["y"]))
            pts.insert(1, {"x": a["x"] + (self.head["x"] - a["x"]) / d * self.seg, "y": a["y"] + (self.head["y"] - a["y"]) / d * self.seg})
        pts[0] = self.head
        keep = int(self.length // self.seg) + 1
        if len(pts) > keep:
            del pts[keep:]
        r = self.body / 2
        hx, hy = self.head["x"], self.head["y"]
        if hx < r or hy < r or hx > self.w - r or hy > self.h - r:
            return self.die("hit the wall")
        for i in range(math.ceil(self.neck / self.seg), len(pts) - self.tail_skip):
            p = pts[i]
            if (hx - p["x"]) ** 2 + (hy - p["y"]) ** 2 < self.hit2:
                return self.die("hit its own body")
        if math.dist((hx, hy), (self.food["x"], self.food["y"])) < r + self.food_r:
            self.score += 1
            self.length += self.grow
            self.since_food = 0.0
            self.place_food()
            self.eat_t = self.game_t
            self.need_after_eat = True                  # ask again right away: the food moved
            if self.steer == "food":
                self.steer = {"straight": self.heading}  # glide straight until that answer arrives
        self.since_food += dt
        if self.since_food > 60:
            self.die("starved (a minute without food)")

    def die(self, why):
        self.alive, self.crash, self.why = False, dict(self.head), why

    # -- the look-ahead for one heading -----------------------------------------------------------
    def analyse(self, k):
        sp, dt, tr = self.speed, 1 / 60, self.turn_rate()
        avg_lat = sum(self.lat) / len(self.lat) if self.lat else 0.2
        horizon = min(2.5, max(0.6, self.decide + 2 * avg_lat + 0.2))     # until the next answer is likely in
        h, hd, travelled, ate, ate_at = dict(self.head), self.heading, 0.0, False, 0.0
        fresh = []
        r = self.body / 2
        steps = math.ceil(horizon / dt)
        reach2 = (sp * horizon + self.body) ** 2
        near = [i for i, p in enumerate(self.pts) if (p["x"] - self.head["x"]) ** 2 + (p["y"] - self.head["y"]) ** 2 < reach2]
        for s in range(steps):
            if not ate:
                hd += _clamp(_ang_diff(self.steer_angle(k, h, hd), hd), tr * dt)
            h = {"x": h["x"] + math.cos(hd) * sp * dt, "y": h["y"] + math.sin(hd) * sp * dt}
            travelled += sp * dt
            if h["x"] < r or h["y"] < r or h["x"] > self.w - r or h["y"] > self.h - r:
                return {"k": k, "deadly": True, "why": "hits the wall", "within": self.bw(travelled)}
            reach = int((self.length - (0 if ate else travelled)) // self.seg)
            for i in near:
                if i > reach or i * self.seg + travelled < self.neck:
                    continue
                p = self.pts[i]
                if (h["x"] - p["x"]) ** 2 + (h["y"] - p["y"]) ** 2 < self.hit2:
                    return {"k": k, "deadly": True, "why": "hits its own body", "within": self.bw(travelled)}
            for q in fresh:
                if travelled - q["t"] >= self.neck and (h["x"] - q["x"]) ** 2 + (h["y"] - q["y"]) ** 2 < self.hit2:
                    return {"k": k, "deadly": True, "why": "loops into its own body", "within": self.bw(travelled)}
            if s % 2 == 0:
                fresh.append({"x": h["x"], "y": h["y"], "t": travelled})
            if not ate and math.dist((h["x"], h["y"]), (self.food["x"], self.food["y"])) < r + self.food_r:
                ate, ate_at = True, travelled
            if ate and travelled - ate_at > max(self.post_eat, sp * avg_lat * 1.5):
                break
        closer = math.dist((h["x"], h["y"]), (self.food["x"], self.food["y"])) < math.dist((self.head["x"], self.head["y"]), (self.food["x"], self.food["y"]))

        cut = int((self.length - (0 if ate else travelled)) // self.seg)
        body = list(reversed(fresh)) + self.pts[:max(0, cut)]

        # a coarse occupancy grid of the body: one cell is one body width
        gx, gy = max(1, int(self.w // self.body)), max(1, int(self.h // self.body))
        cw, ch = self.w / gx, self.h / gy

        def idx(p):
            return min(gy - 1, max(0, int(p["y"] // ch))) * gx + min(gx - 1, max(0, int(p["x"] // cw)))
        blocked = bytearray(gx * gy)
        for j, q in enumerate(body):
            if j * self.seg * 2 > self.neck:
                blocked[idx(q)] = 1
        start = idx(h)
        blocked[start] = 0

        # straight-line clearance in that heading, from where we'd end up
        a = hd if k == "food" else ANGLE[k]
        clear, stop, t = 0.0, "wall", self.body / 3
        while t < max(self.w, self.h) * 1.5:
            p = {"x": h["x"] + math.cos(a) * t, "y": h["y"] + math.sin(a) * t}
            if p["x"] < r or p["y"] < r or p["x"] > self.w - r or p["y"] > self.h - r:
                stop = "wall"
                break
            if blocked[idx(p)]:
                stop = "body"
                break
            clear = t
            t += self.body / 3

        # is the snake's own body lying across the straight line to the food?
        body_in_way = None
        if not ate:
            fd = math.dist((h["x"], h["y"]), (self.food["x"], self.food["y"]))
            t = self.body / 3
            while t < fd - self.food_r:
                c = idx({"x": h["x"] + (self.food["x"] - h["x"]) / fd * t, "y": h["y"] + (self.food["y"] - h["y"]) / fd * t})
                if c != start and blocked[c]:
                    body_in_way = self.bw(t) or 1
                    break
                t += self.body / 3

        # a coarse flood fill: how much room is reachable, and can we reach the tail?
        seen = bytearray(gx * gy)
        seen[start] = 1
        queue, room = [start], 0
        tail = idx(body[-1] if body else h)
        tx, ty = tail % gx, tail // gx
        tail_reachable = False
        while queue:
            c = queue.pop()
            x, y = c % gx, c // gx
            room += 1
            if abs(x - tx) + abs(y - ty) <= 1:
                tail_reachable = True
            for n in ((c - 1) if x > 0 else -1, (c + 1) if x < gx - 1 else -1, (c - gx) if y > 0 else -1, (c + gx) if y < gy - 1 else -1):
                if n >= 0 and not blocked[n] and not seen[n]:
                    seen[n] = 1
                    queue.append(n)
        need = math.ceil((self.length + (self.grow if ate else 0)) / self.body)
        trap = room < need and not tail_reachable
        return {"k": k, "deadly": False, "ate": ate, "closer": closer, "clear": self.bw(clear), "stop": stop,
                "body_in_way": body_in_way, "room": room, "need": need, "trap": trap}

    @staticmethod
    def describe(an):
        label = "Steer straight at the food" if an["k"] == "food" else f"Head {NAME[an['k']]}"
        if an["deadly"]:
            return f"{label}: deadly, {an['why']} within {an['within']} body widths."
        if an["trap"]:
            return (f"{label}: bad, leads into a dead end the snake cannot escape (only {an['room']} open spaces, "
                    f"snake needs {an['need']}). Avoid.")
        if an["ate"]:
            return f"{label}: safe, reaches the food."
        if an["k"] == "food":
            if an["body_in_way"]:
                return (f"{label}: bad, the snake's own body lies between the head and the food, {an['body_in_way']} body "
                        f"widths ahead. Homing straight in would run into it; go around instead.")
            return f"{label}: safe, nothing between the head and the food."
        ahead = f"clear for {an['clear']} body widths, then {'its own body' if an['stop'] == 'body' else 'the wall'}"
        food = ("closer to the food, but its own body is still in the way" if an["body_in_way"] else "closer to the food") if an["closer"] else "farther from the food"
        return f"{label}: safe, {food}, {ahead}."

    def request(self, model, opts, analyses):
        fd = self.bw(math.dist((self.head["x"], self.head["y"]), (self.food["x"], self.food["y"])))
        fdir = NAME[self.compass_of(math.atan2(self.food["y"] - self.head["y"], self.food["x"] - self.head["x"]))]
        state = (f"Snake game in an open arena {self.bw(self.w)} body widths wide and {self.bw(self.h)} tall (not a grid). "
                 "The snake glides continuously and can steer toward any compass heading, turning in a smooth curve. "
                 "It must not touch a wall or its own body, and should reach the food. "
                 f"Snake length: {self.bw(self.length)} body widths. Currently heading {NAME[self.compass_of(self.heading)]}. "
                 f"Food is {fd} body widths away to the {fdir}.")
        return {"model": model, "keep_alive": "10m", "state": state,
                "questions": {"move": {"type": "choice",
                                       "instructions": "Which heading should the snake steer toward next so it stays alive and gets the food?",
                                       "criteria": {an["k"]: self.describe(an) for an in analyses}}}}

    @staticmethod
    def read_answer(res, opts):
        """The answer, read defensively: keyed or a list, probabilities an object or a list."""
        a = res.get("answers", res.get("answer", res)) if isinstance(res, dict) else None
        if isinstance(a, list):
            a = a[0] if a else None
        elif isinstance(a, dict) and "move" in a:
            a = a["move"]
        elif isinstance(a, dict) and "choice" not in a:
            a = next(iter(a.values()), None)
        if not isinstance(a, dict):
            return None
        probs = {}
        pr = a.get("probabilities")
        if isinstance(pr, list):
            for o, p in zip(opts, pr):
                probs[o] = float(p or 0)
        elif isinstance(pr, dict):
            for o in opts:
                probs[o] = float(pr.get(o) or 0)
        choice = a.get("choice")
        if choice not in opts:
            choice = max(opts, key=lambda o: probs.get(o, 0))
        return {"choice": choice, "probs": probs, "confidence": a.get("confidence")}


class Game:
    """The player: runs the game on a thread of its own while the Snake screen is up,
    asks the model, draws the frames. One game at a time, the size of the screen."""

    def __init__(self):
        self.snake = None
        self.size = None
        self.conf = {}
        self.host = ""
        self.thread = None
        self._lock = threading.Lock()
        self._seen = 0.0                               # the last time a frame was wanted
        self._seen_arena = 0.0                         # ... at the arena's own size
        self._other = None                             # [size, frames in a row] asked at another size
        self._inflight = False
        self._req = 0
        self.error = ""
        self._said = ""
        self.best, self.games = 0, []
        self._bg = {}
        self._load()

    # -- the record ---------------------------------------------------------------------
    def _load(self):
        try:
            with open(PATH) as f:
                d = json.load(f)
            self.best, self.games = int(d.get("best", 0)), list(d.get("games", []))[-200:]
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"best": self.best, "games": self.games[-200:]}, f)
            os.replace(tmp, PATH)
        except OSError as e:
            log.warning("snake: couldn't save the scores (%s)", e)

    # -- running ------------------------------------------------------------------------
    ADOPT = 4            # frames in a row at another size before the arena moves there

    def ensure(self, conf, host, size):
        """Called for every frame the screen wants: keeps the game going, the size of the screen.

        The arena is the screen. The page's preview may ask at another size (another panel or
        rotation chosen but not saved yet) between the screen's own frames: that gets the
        picture scaled, not a new game. The arena moves to a new size only when that size is
        all that's asked for — the screen itself changed shape."""
        size = tuple(size)
        with self._lock:
            self.conf, self.host = dict(conf or {}), host or ""
            now = time.time()
            if self.snake is None:
                self._new(size)
            elif size == self.size:
                self._other = None
                self._seen_arena = now
            else:
                self._other = [size, self._other[1] + 1 if self._other and self._other[0] == size else 1]
                if self._other[1] >= self.ADOPT or now - self._seen_arena > 2:
                    self._new(size)
            self.snake.speed = float(self.conf.get("speed", 70) or 70)
            self.snake.decide = max(0.12, float(self.conf.get("decide_ms", 300) or 300) / 1000)
            self.snake.safety = bool(self.conf.get("safety"))
            self._seen = now
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, daemon=True, name="snake")
                self.thread.start()

    def _new(self, size):
        """A fresh game the size of the screen (under the lock). A game cut short by the
        screen changing shape isn't a finished game, but its score still counts as a best."""
        old = self.snake
        if old is not None and old.alive and old.score > self.best:
            self.best = old.score
            self._save()
        self.size = size
        self._other = None
        self._seen_arena = time.time()
        self.snake = Snake(size[0], size[1], speed=float(self.conf.get("speed", 70) or 70),
                           decide=max(0.12, float(self.conf.get("decide_ms", 300) or 300) / 1000),
                           headings=self.conf.get("headings", 8), safety=bool(self.conf.get("safety")))
        self._req += 1
        self._inflight = False

    def playing(self):
        """Is the game on a screen right now? (The thread lingers a few seconds after the
        last frame so a moment's gap doesn't restart the game; that isn't playing.)"""
        return bool(self.thread and self.thread.is_alive()) and time.time() - self._seen < 1.0

    def _run(self):
        last = time.monotonic()
        died_at = None
        log.info("snake: playing on a %dx%d screen with %s", self.size[0], self.size[1], self.model())
        while time.time() - self._seen < 5:          # nobody has asked for a frame for a while: the screen moved on
            now = time.monotonic()
            dt, last = min(0.05, now - last), now
            if time.time() - self._seen > 1.0:       # the screen is on something else: the game waits where it is
                time.sleep(1 / 30)
                continue
            with self._lock:
                s = self.snake
                if s.alive:
                    since = s.game_t - s.last_decision_t
                    waiting = s.last_decision_t == -math.inf        # each game holds still until its first decision
                    if not self._inflight and (since >= s.decide or s.need_after_eat):
                        s.need_after_eat = False
                        self._inflight = True
                        threading.Thread(target=self._decide, args=(self._req,), daemon=True).start()
                    if not waiting:
                        s.game_t += dt
                        s.advance(dt)
                        if not s.alive:
                            died_at = now
                            self._record(s)
                elif died_at is not None and now - died_at > 1.8:
                    s.reset()
                    self._req += 1
                    self._inflight = False
                    died_at = None
            time.sleep(1 / 30)
        log.info("snake: stopped")

    def model(self):
        return (self.conf.get("model") or "tev1").strip()

    def _record(self, s):
        self.best = max(self.best, s.score)
        self.games.append({"score": s.score, "model": self.model(), "at": time.time(), "decisions": s.decisions,
                           "avg_ms": int(sum(s.lat) / len(s.lat) * 1000) if s.lat else None, "why": s.why})
        self.games = self.games[-200:]
        self._save()

    def _decide(self, req):
        s = self.snake
        from . import llm
        name = llm.resolve_model(self.host, self.model())   # "tev1" with only tev1:0.8b pulled: that one (cached)
        with self._lock:
            opts = s.options()
            analyses = [s.analyse(k) for k in opts]
            body = s.request(name, opts, analyses)
            asked_at = s.game_t
        t0 = time.monotonic()
        try:
            r = requests.post(f"{self.host}/v1/systemone", json=body, timeout=20)
            ms = (time.monotonic() - t0)
            if r.status_code == 404:
                raise ValueError(f"{self.model()} isn't on {self.host.split('//')[-1]} — ollama pull {self.model()}")
            r.raise_for_status()
            res = r.json()
        except requests.exceptions.ConnectionError:
            self._fail(req, f"can't reach the bot's Ollama at {self.host.split('//')[-1]}")
            return
        except Exception as e:
            self._fail(req, str(e)[:140])
            return
        ans = s.read_answer(res, opts)
        if ans is None:
            self._fail(req, "couldn't read an answer from the model")
            return
        with self._lock:
            if req != self._req:                     # a new game started meanwhile
                return
            self._inflight = False
            self.error = ""
            if s.eat_t > asked_at:
                s.need_after_eat = True              # asked before the food moved: use this, and ask again
            if not s.alive:
                return
            by_k = {a["k"]: a for a in analyses}
            pick, overridden = ans["choice"], False
            if s.safety and (by_k[pick]["deadly"] or by_k[pick]["trap"]):
                prob = lambda a: ans["probs"].get(a["k"], 0)
                good = sorted([a for a in analyses if not a["deadly"] and not a["trap"]], key=prob, reverse=True)
                alive = sorted([a for a in analyses if not a["deadly"]], key=prob, reverse=True)
                alt = good[0] if good else (alive[0] if by_k[pick]["deadly"] and alive else None)
                if alt and alt["k"] != pick:
                    pick, overridden = alt["k"], True
            s.steer = pick
            s.last_decision_t = s.game_t
            s.decisions += 1
            s.lat.append(ms)
            s.lat = s.lat[-50:]
            s.last = {"opts": opts, "analyses": by_k, "probs": ans["probs"], "choice": ans["choice"], "pick": pick,
                      "overridden": overridden, "confidence": ans.get("confidence"), "ms": int(ms * 1000)}

    def _fail(self, req, why):
        with self._lock:
            if req == self._req:
                self.error = why
            said = why == self._said
            self._said = why
        if not said:
            log.warning("snake: %s", why)
        time.sleep(3)                                  # don't hammer a box that isn't answering
        with self._lock:
            if req == self._req and self.snake:
                self._inflight = False
                self.snake.need_after_eat = True       # try again

    # -- what the rest of the app sees -------------------------------------------------------------
    def status(self):
        with self._lock:
            s = self.snake
            out = {"playing": self.playing(), "model": self.model(), "best": self.best, "games": len(self.games),
                   "error": self.error or None, "arena": list(self.size) if self.size else None}
            if s:
                out.update({"score": s.score, "decisions": s.decisions, "alive": s.alive,
                            "avg_ms": int(sum(s.lat) / len(s.lat) * 1000) if s.lat else None,
                            "waiting": s.last_decision_t == -math.inf, "last": s.last})
            recent = [g["score"] for g in self.games[-10:]]
            out["recent"] = recent
            out["avg"] = round(sum(recent) / len(recent), 1) if recent else None
            return out

    def words(self):
        """A line for the bot's context while it plays."""
        if not self.playing() or not self.snake:
            return ""
        s = self.snake
        return f"It is playing Snake on the little screen ({self.model()} decides): score {s.score}, best ever {self.best}."

    # -- drawing -------------------------------------------------------------------------------
    def _background(self, size):
        if size not in self._bg:
            im = Image.new("RGB", size, COLOURS["bg"])
            d = ImageDraw.Draw(im)
            step = 20
            for x in range(step, size[0], step):
                for y in range(step, size[1], step):
                    d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=COLOURS["dots"])
            self._bg[size] = im
        return self._bg[size]

    def frame(self, size):
        """The game as it is now, for the LCD."""
        size = tuple(size)
        with self._lock:
            s = self.snake
            if s is None:
                return self._background(size).copy()
            arena = (int(s.w), int(s.h))
            im = self._background(arena).copy()
            d = ImageDraw.Draw(im)
            body = s.body
            # food
            d.ellipse((s.food["x"] - s.food_r, s.food["y"] - s.food_r, s.food["x"] + s.food_r, s.food["y"] + s.food_r), fill=COLOURS["food"])
            # the body: round segments, tapering and darkening toward the tail
            n = len(s.pts)
            for i in range(n - 1, 0, -1):
                t = i / n
                col = tuple(int(COLOURS["snake"][j] + (COLOURS["tail"][j] - COLOURS["snake"][j]) * t) for j in range(3))
                w = max(2, int(round(body * (1 - 0.45 * t))))
                p, q = s.pts[i], s.pts[i - 1]
                d.line((p["x"], p["y"], q["x"], q["y"]), fill=col, width=w)
                d.ellipse((p["x"] - w / 2, p["y"] - w / 2, p["x"] + w / 2, p["y"] + w / 2), fill=col)
            # the head, with eyes looking along the heading
            hr = body * 0.58
            d.ellipse((s.head["x"] - hr, s.head["y"] - hr, s.head["x"] + hr, s.head["y"] + hr), fill=COLOURS["head"])
            ex, ey = math.cos(s.heading), math.sin(s.heading)
            px, py = -ey, ex
            for side in (-1, 1):
                cx = s.head["x"] + ex * body * 0.22 + px * side * body * 0.26
                cy = s.head["y"] + ey * body * 0.22 + py * side * body * 0.26
                er = max(1.0, body * 0.1)
                d.ellipse((cx - er, cy - er, cx + er, cy + er), fill=COLOURS["eye"])
            if s.crash:
                cr = body * 0.95
                d.ellipse((s.crash["x"] - cr, s.crash["y"] - cr, s.crash["x"] + cr, s.crash["y"] + cr), outline=COLOURS["bad"], width=2)
            # the score, small, where the snake can pass underneath
            small = font(1, 12)
            d.text((5, 3), str(s.score), font=small, fill=COLOURS["ink"])
            right = f"best {self.best}" if self.best else ""
            if right:
                d.text((arena[0] - 5 - d.textlength(right, font=small), 3), right, font=small, fill=COLOURS["dim"])
            note = ""
            if self.error:
                note = self.error
            elif not s.alive:
                note = f"{s.why} · score {s.score}"
            elif s.last_decision_t == -math.inf:
                note = f"waiting for {self.model()}…"
            if note:
                self._note(d, note, arena)
        return im if arena == size else im.resize(size)

    def _note(self, d, text, size):
        fnt = font(1, 12)
        words, lines, line = text.split(), [], ""
        for w in words:
            trial = (line + " " + w).strip()
            if d.textlength(trial, font=fnt) > size[0] - 24 and line:
                lines.append(line)
                line = w
            else:
                line = trial
        if line:
            lines.append(line)
        lh = 15
        top = size[1] - 10 - lh * len(lines)
        d.rectangle((6, top - 6, size[0] - 6, size[1] - 4), fill=COLOURS["bg"])
        for i, ln in enumerate(lines[:4]):
            d.text(((size[0] - d.textlength(ln, font=fnt)) / 2, top + i * lh), ln, font=fnt, fill=COLOURS["ink"] if i == 0 else COLOURS["dim"])


GAME = Game()
