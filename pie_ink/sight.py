"""What the camera can see, kept as events: "Person detected", "Package
detected", "Door opened", "Cat entered room", "Nobody detected".

The watcher notices motion cheaply (a small frame compared with the last).
When something moves — and now and then when nothing does — a frame comes
here to be looked at properly. A vision model on your Ollama box, when you
have one, names what is in the picture as a small JSON record (people,
animals, packages, vehicles, the door, the lights, anything unusual); without
one, OpenCV's stock cascades find people and cats on the Pi itself. Only
changes between one look and the next become events, so a person sitting
still is one line, not one a minute. The log lives in data/events.json, on
the Camera tab, in Telegram (/events), and in the bot's memory of the day.
"""
import json
import logging
import os
import re
import threading
import time

from . import llm
from .settings import DATA_DIR

log = logging.getLogger(__name__)

EVENTS_PATH = os.path.join(DATA_DIR, "events.json")
KEEP = 400
ANIMALS = ("cat", "dog", "bird", "rabbit", "horse", "cow", "deer", "raccoon", "squirrel", "fox", "chicken",
           "duck", "goat", "sheep", "pig", "hamster", "lizard", "snake", "turtle", "fish", "mouse", "rat")
CASCADES = {"face": "haarcascade_frontalface_default.xml", "body": "haarcascade_upperbody.xml",
            "cat": "haarcascade_frontalcatface.xml"}

_lock = threading.Lock()
_events = None
_state = {"last": None, "last_at": 0.0, "detector": "", "error": None, "looks": 0, "took_ms": 0}
_cascades = {}


# -- the log ----------------------------------------------------------------------------------

def _load():
    global _events
    if _events is not None:
        return _events
    try:
        with open(EVENTS_PATH) as fh:
            data = json.load(fh)
        _events = [e for e in data if isinstance(e, dict) and e.get("ts") and e.get("text")][-KEEP:]
    except (OSError, ValueError):
        _events = []
    return _events


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = EVENTS_PATH + ".part"
        with open(tmp, "w") as fh:
            json.dump(_events[-KEEP:], fh)
        os.replace(tmp, EVENTS_PATH)
    except OSError as e:
        log.debug("couldn't save the events: %s", e)


def log_event(text, kind="seen", level="note", source="camera"):
    """Add a line to the log. Returns the entry."""
    entry = {"ts": time.time(), "text": text, "kind": kind, "level": level, "source": source}
    with _lock:
        items = _load()
        items.append(entry)
        del items[:-KEEP]
        _save()
    log.info("event: %s", text)
    return entry


def events(limit=100, since=0.0, kinds=None):
    """The log, newest first."""
    with _lock:
        items = list(_load())
    out = [e for e in items if e["ts"] > since and (not kinds or e.get("kind") in kinds)]
    return list(reversed(out))[:limit]


def clear():
    global _events
    with _lock:
        _events = []
        _save()


def summary(hours=24, limit=12, before=None):
    """A line about the day, for the bot and the agent: what happened, when.
    `before`: only events up to that time (the agent lists later ones itself)."""
    since = time.time() - hours * 3600
    items = [e for e in events(limit=KEEP, since=since) if before is None or e["ts"] < before]
    if not items:
        return ""
    items = list(reversed(items))[-limit:]
    return "; ".join(f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['text']}" for e in items)


def status():
    with _lock:
        st = dict(_state)
    st["events"] = len(_load())
    return st


def last_observation():
    with _lock:
        return dict(_state["last"]) if _state["last"] else None


# -- looking -----------------------------------------------------------------------------------

def _model_ready(config):
    conf = config.get("llm", {})
    return bool(conf.get("enabled") and ((conf.get("model") or "").strip() or (conf.get("vision_model") or "").strip()))


def _cascade_dir():
    try:
        import cv2
        d = getattr(getattr(cv2, "data", None), "haarcascades", "")
        if d and os.path.isdir(d):
            return d
    except ImportError:
        return ""
    for d in ("/usr/share/opencv4/haarcascades", "/usr/share/opencv/haarcascades", "/usr/local/share/opencv4/haarcascades"):
        if os.path.isdir(d):
            return d
    return ""


def _basic_ready():
    d = _cascade_dir()
    return bool(d) and os.path.exists(os.path.join(d, CASCADES["face"]))


def detector(config):
    """Which way of looking is in use: "model" (a vision model on Ollama),
    "basic" (OpenCV on the Pi: people and cats only) or "off"."""
    want = (config.get("camera_watch", {}).get("detector") or "auto").lower()
    if want == "off":
        return "off"
    if want == "model":
        return "model" if _model_ready(config) else "off"
    if want == "basic":
        return "basic" if _basic_ready() else "off"
    if _model_ready(config):
        return "model"
    return "basic" if _basic_ready() else "off"


def empty():
    return {"people": 0, "animals": [], "packages": 0, "vehicles": 0, "door": "unknown", "lights": "unknown",
            "unusual": "", "notes": "", "source": ""}


def _as_int(v, most=50):
    try:
        if isinstance(v, str):
            words = {"none": 0, "no": 0, "zero": 0, "one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "several": 3, "many": 5}
            v = words.get(v.strip().lower(), v)
        return max(0, min(most, int(float(v))))
    except (TypeError, ValueError):
        return 0


def _as_word(v, allowed, default="unknown"):
    v = str(v or "").strip().lower()
    for a in allowed:
        if v.startswith(a):
            return a
    return default


def parse(text):
    """The model's answer as an observation. Tolerant: the first {...} in
    the text is used, fields are coerced, anything missing is empty."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    raw = m.group()
    try:
        data = json.loads(raw)
    except ValueError:
        # single quotes, trailing commas: the usual small-model slips
        fixed = re.sub(r",\s*([}\]])", r"\1", raw.replace("'", '"'))
        data = json.loads(fixed)
    if not isinstance(data, dict):
        raise ValueError("the answer isn't an object")
    obs = empty()
    obs["people"] = _as_int(data.get("people"))
    animals = data.get("animals") or []
    if isinstance(animals, str):
        animals = [a for a in re.split(r"[,\s]+", animals) if a]
    kinds = []
    for a in animals:
        word = str(a).strip().lower().rstrip("s") if str(a).strip().lower() not in ("mouse",) else "mouse"
        word = {"kitten": "cat", "puppy": "dog", "kitty": "cat"}.get(word, word)
        if word and word not in ("none", "no", "null") and word not in kinds:
            kinds.append(word)
    obs["animals"] = kinds[:6]
    obs["packages"] = _as_int(data.get("packages"))
    obs["vehicles"] = _as_int(data.get("vehicles"))
    obs["door"] = _as_word(data.get("door"), ("open", "closed", "none"))
    obs["lights"] = _as_word(data.get("lights"), ("on", "off"))
    unusual = str(data.get("unusual") or "").strip()
    if unusual.lower() in ("none", "nothing", "no", "null", "n/a", "nothing unusual", "-"):
        unusual = ""
    obs["unusual"] = unusual[:160]
    obs["notes"] = str(data.get("notes") or "").strip()[:200]
    obs["source"] = "model"
    return obs


def _look_model(frame, config, owner="the owner"):
    from .mascot import as_base64
    conf = config.get("llm", {})
    system = ("You are the eyes of a small home monitor. You look at one picture from a fixed camera and answer "
              "with a single JSON object and nothing else: no prose, no markdown, no code fence.")
    prompt = ("Look at this picture from a camera in " + owner + "'s home and fill in exactly this JSON:\n"
              '{"people": <how many people are in the picture>, '
              '"animals": [<each animal you can see, one lowercase word each, e.g. "cat", "dog"; empty if none>], '
              '"packages": <how many parcels, boxes or deliveries>, '
              '"vehicles": <how many cars, vans, bikes>, '
              '"door": "open" or "closed" if a door is in the picture, else "none", '
              '"lights": "on" or "off" if you can tell, else "unknown", '
              '"unusual": "<one short sentence if something looks wrong or out of place — smoke, water, a fall, '
              'a stranger, something knocked over — else an empty string>", '
              '"notes": "<one plain sentence saying what the scene shows>"}\n'
              "Count only what you can actually see. Reflections, photos and screens don't count.")
    text = llm.generate(conf, system, prompt, images=[as_base64(frame, longest=640)], limit=4000,
                        timeout=int(conf.get("timeout_seconds", 180) or 180))
    return parse(text)


def _cascade(name):
    if name in _cascades:
        return _cascades[name]
    import cv2
    path = os.path.join(_cascade_dir(), CASCADES[name])
    c = cv2.CascadeClassifier(path)
    _cascades[name] = None if c.empty() else c
    return _cascades[name]


def _look_basic(frame):
    """OpenCV's stock cascades: faces and upper bodies count as people, cat
    faces as cats. Nothing else, and only when they face the camera."""
    import cv2
    import numpy as np
    img = np.array(frame.convert("L"))
    h, w = img.shape[:2]
    if w > 480:
        scale = 480.0 / w
        img = cv2.resize(img, (480, int(h * scale)))
    img = cv2.equalizeHist(img)
    obs = empty()
    people = 0
    for name, neighbours, size in (("face", 6, 28), ("body", 4, 48)):
        c = _cascade(name)
        if c is None:
            continue
        found = c.detectMultiScale(img, scaleFactor=1.15, minNeighbors=neighbours, minSize=(size, size))
        people = max(people, len(found))
    cats = _cascade("cat")
    if cats is not None:
        found = cats.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(found):
            obs["animals"] = ["cat"]
    obs["people"] = int(people)
    obs["source"] = "basic"
    return obs


def observe(frame, config, owner="the owner"):
    """One look at a frame with whichever detector is in use. None when
    there is none, or it failed (the reason is in status()['error'])."""
    how = detector(config)
    if how == "off" or frame is None:
        with _lock:
            _state["detector"] = how
        return None
    started = time.time()
    try:
        obs = _look_model(frame, config, owner) if how == "model" else _look_basic(frame)
        error = None
    except Exception as e:
        obs, error = None, f"{str(e)[:140]}"
        log.info("sight (%s): %s", how, error)
    with _lock:
        _state.update(detector=how, error=error, looks=_state["looks"] + 1, took_ms=int((time.time() - started) * 1000))
        if obs is not None:
            obs["at"] = time.time()
            _state["last"], _state["last_at"] = obs, obs["at"]
    return obs


def diff(before, now, returning=False):
    """What changed between two looks, as [(text, kind, level)]. `before`
    None means this is the first look: what is there is announced.
    `returning`: a "Nobody detected" went out since anyone was last seen,
    so a person now is one who came back."""
    out = []
    first = before is None
    b = before or empty()
    p0, p1 = b["people"], now["people"]
    if p1 and not p0:
        if returning and not first:
            out.append(("Person returned" if p1 == 1 else "People returned", "person", "note"))
        else:
            out.append(("Person detected" if p1 == 1 else f"{p1} people detected", "person", "note"))
    elif p0 and not p1:
        out.append(("Nobody detected", "person", "info"))
    elif p0 and p1 and p0 != p1:
        out.append((f"Now {p1} {'person' if p1 == 1 else 'people'}", "person", "info"))
    for kind in now["animals"]:
        if kind not in b["animals"]:
            name = kind.capitalize()
            out.append((f"{name} spotted" if first else f"{name} entered room", "animal", "note"))
    for kind in b["animals"]:
        if kind not in now["animals"]:
            out.append((f"{kind.capitalize()} left", "animal", "info"))
    k0, k1 = b["packages"], now["packages"]
    if k1 > k0:
        out.append(("Package detected" if k1 - k0 == 1 else f"{k1 - k0} packages detected", "package", "note"))
    elif k1 < k0:
        out.append(("Package taken" if k1 == 0 else "A package was taken", "package", "note"))
    if b["door"] in ("open", "closed") and now["door"] in ("open", "closed") and b["door"] != now["door"]:
        out.append(("Door opened" if now["door"] == "open" else "Door closed", "door", "note"))
    if b["lights"] in ("on", "off") and now["lights"] in ("on", "off") and b["lights"] != now["lights"]:
        out.append(("Lights on" if now["lights"] == "on" else "Lights off", "lights", "info"))
    v0, v1 = b["vehicles"], now["vehicles"]
    if v1 > v0:
        out.append(("Vehicle arrived" if not first else "Vehicle in view", "vehicle", "note"))
    elif v1 < v0 and v1 == 0:
        out.append(("Vehicle left", "vehicle", "info"))
    if now["unusual"] and now["unusual"].lower() != (b.get("unusual") or "").lower():
        out.append((f"Unusual: {now['unusual']}", "unusual", "alert"))
    return out


def look(frame, config, owner="the owner", view=None):
    """Look, compare with the last look, and log what changed. Returns
    (observation, [entries]) — both empty when nothing could be seen.
    `view` says where a camera that turns was pointing: a look from a
    different spot starts a new baseline quietly, since everything in it is
    "new" only because the camera moved."""
    before = last_observation()
    obs = observe(frame, config, owner)
    if obs is None:
        return None, []
    with _lock:
        moved = _state.get("view") != view
        _state["view"] = view
        if moved and before is not None:
            _state["nobody_since"] = 0
            return obs, []
        returning = _state.get("nobody_since", 0) > 0
    changes = diff(before, obs, returning)
    entries = [log_event(text, kind, level) for text, kind, level in changes]
    with _lock:                                    # "returned" is a person after a "nobody"
        if obs["people"]:
            _state["nobody_since"] = 0
        elif before and before["people"]:
            _state["nobody_since"] = time.time()
    return obs, entries


def describe(obs):
    """An observation as a short line, for the agent and the page."""
    if not obs:
        return ""
    bits = []
    if obs["people"]:
        bits.append("1 person" if obs["people"] == 1 else f"{obs['people']} people")
    bits += obs.get("animals") or []
    if obs["packages"]:
        bits.append("1 package" if obs["packages"] == 1 else f"{obs['packages']} packages")
    if obs["vehicles"]:
        bits.append("1 vehicle" if obs["vehicles"] == 1 else f"{obs['vehicles']} vehicles")
    if obs["door"] in ("open", "closed"):
        bits.append(f"door {obs['door']}")
    if obs["lights"] in ("on", "off"):
        bits.append(f"lights {obs['lights']}")
    line = ", ".join(bits) if bits else "nobody and nothing new"
    if obs.get("unusual"):
        line += f"; unusual: {obs['unusual']}"
    return line
