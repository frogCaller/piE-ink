"""The agent: goals of your own words, a look around every few minutes, a
decision, and — when a goal calls for it — an action.

    camera · GPS · Home Assistant · the Pi itself · the other PiE-inks
                              ↓
                        a report, in words
                              ↓
                       Ollama, with the goals
                              ↓
          quiet / a note / an alert — and the actions it may take:
          a message to your phone, a line out loud, a line on the
          screen, a Home Assistant command

"Keep an eye on the room." "Tell me if something unusual happens." "Watch
the printer on the camera and tell me when it stops." "Keep the lamp on
while someone is in the room after dark." "Tell me if the server goes down."

Every check is written to data/agent.json — what it saw, what it thought,
what it did — so you can read its mind on the page and in Telegram (/agent).
It only ever does what you have allowed under Settings → Agent, and a Home
Assistant command goes through the same parser as a spoken one, so it can't
do anything you couldn't say.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from . import llm
from .settings import DATA_DIR

log = logging.getLogger(__name__)

JOURNAL_PATH = os.path.join(DATA_DIR, "agent.json")
KEEP = 200
LEVELS = ("quiet", "note", "alert")
ACTIONS = ("notify", "say", "show", "home", "look")
DEFAULT_CAN = {"notify": True, "say": False, "show": True, "home": False, "look": True}
DEFAULT_GOALS = "Keep an eye on the room.\nTell me if something unusual happens."


class Agent:
    def __init__(self):
        self.conf = {}
        self._lock = threading.Lock()
        self.thread = None
        self.stop_flag = threading.Event()
        self._wake = threading.Event()
        self._journal = None
        self.last_run = 0.0
        self.next_run = 0.0
        self.busy = False
        self.error = None
        self.last = {"thought": "", "level": "quiet", "actions": [], "ts": 0.0}
        self._pending = []              # events since the last check
        self._notes = []                # what it asked to remember
        self._told = {}                 # notification text -> when, so it doesn't nag
        self._urgent = 0.0              # an alert asked for an early check
        # set by the app
        self.config = lambda: {}        # the live settings
        self.frame = lambda: None       # a picture from the camera, or None
        self.facts = lambda config: []  # the app's lines of context (time, weather, GPS, presence…)
        self.do = {}                    # action name -> callable(text) -> result text
        self.on_events = None           # (entries, frame) -> None: events from the agent's own looks
        self.look_fn = None             # (where) -> what it saw: turn the camera, look, describe
        self.survey_fn = None           # () -> what it saw looking around
        self.can_turn = lambda: False   # is there a camera that turns

    # -- lifecycle -----------------------------------------------------------------------------

    def configure(self, config_fn=None, frame_fn=None, facts_fn=None, do=None, on_events=None,
                  look_fn=None, survey_fn=None, can_turn=None):
        if look_fn:
            self.look_fn = look_fn
        if survey_fn:
            self.survey_fn = survey_fn
        if can_turn:
            self.can_turn = can_turn
        if config_fn:
            self.config = config_fn
        if frame_fn:
            self.frame = frame_fn
        if facts_fn:
            self.facts = facts_fn
        if do:
            self.do = dict(do)
        if on_events:
            self.on_events = on_events

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
        self.stop_flag.clear()
        self.next_run = time.time() + 20             # the first check soon after boot, once things are up
        self.thread = threading.Thread(target=self._loop, daemon=True, name="agent")
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()
        self._wake.set()

    def update(self, conf):
        self.conf = dict(conf or {})
        if not self.conf.get("enabled"):
            self.stop()
            return
        self.start(self.conf)

    def _every(self):
        try:
            return max(1.0, float(self.conf.get("every_minutes", 10) or 10)) * 60
        except (TypeError, ValueError):
            return 600.0

    def _loop(self):
        log.info("agent: on, checking every %.0f min", self._every() / 60)
        while not self.stop_flag.is_set():
            wait = max(1.0, self.next_run - time.time())
            self._wake.wait(wait)
            self._wake.clear()
            if self.stop_flag.is_set():
                break
            if time.time() < self.next_run and not self._urgent:
                continue
            try:
                self.tick("alert" if self._urgent else "schedule")
            except Exception as e:                      # the loop must outlive any one check
                log.exception("agent check")
                self.error = f"{type(e).__name__}: {str(e)[:120]}"
            self._urgent = 0.0
            self.next_run = time.time() + self._every()
        log.info("agent: off")

    def run_now(self):
        """A check as soon as the loop is free; started if it is off."""
        if not self.running():
            return False
        self.next_run = 0
        self._wake.set()
        return True

    def noticed(self, entry):
        """An event from the camera: kept for the next report; an alert
        brings the check forward."""
        with self._lock:
            self._pending.append(entry)
            del self._pending[:-30]
        if entry.get("level") == "alert" and self.running() and self.conf.get("enabled") \
                and time.time() - self.last_run > 60 and not self.busy:
            self._urgent = time.time()
            self.next_run = time.time() + 5
            self._wake.set()

    # -- the journal ---------------------------------------------------------------------------

    def _load(self):
        if self._journal is None:
            try:
                with open(JOURNAL_PATH) as fh:
                    self._journal = [e for e in json.load(fh) if isinstance(e, dict)][-KEEP:]
            except (OSError, ValueError):
                self._journal = []
            if self._journal:
                last = self._journal[-1]
                self.last = {"thought": last.get("thought", ""), "level": last.get("level", "quiet"),
                             "actions": last.get("actions", []), "ts": last.get("ts", 0.0)}
                self.last_run = last.get("ts", 0.0)
                self._notes = [e["remember"] for e in self._journal[-8:] if e.get("remember")][-5:]
        return self._journal

    def _write(self, entry):
        with self._lock:
            items = self._load()
            items.append(entry)
            del items[:-KEEP]
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                tmp = JOURNAL_PATH + ".part"
                with open(tmp, "w") as fh:
                    json.dump(items, fh)
                os.replace(tmp, JOURNAL_PATH)
            except OSError as e:
                log.debug("couldn't save the agent's journal: %s", e)

    def journal(self, limit=20):
        with self._lock:
            items = list(self._load())
        return list(reversed(items))[:limit]

    def clear(self):
        with self._lock:
            self._journal = []
            self._notes = []
            try:
                os.remove(JOURNAL_PATH)
            except OSError:
                pass

    def goals(self):
        text = self.conf.get("goals")
        if text is None or not str(text).strip():
            text = DEFAULT_GOALS
        return [g.strip(" -•\t") for g in str(text).splitlines() if g.strip(" -•\t")]

    def status(self):
        with self._lock:
            self._load()
            notes = list(self._notes)
            pending = len(self._pending)
        now = time.time()
        return {"enabled": bool(self.conf.get("enabled")), "running": self.running(), "busy": self.busy,
                "error": self.error, "last_run": self.last_run, "last_ago": int(now - self.last_run) if self.last_run else None,
                "next_in": max(0, int(self.next_run - now)) if self.running() else None,
                "last": self.last, "notes": notes, "pending_events": pending, "goals": self.goals(),
                "every_minutes": self.conf.get("every_minutes", 10), "camera_turns": bool(self._turns()),
                "can": {a: bool(self.conf.get(f"can_{a}", DEFAULT_CAN.get(a, False))) for a in ACTIONS}}

    def _turns(self):
        try:
            return bool(self.can_turn())
        except Exception:
            return False

    # -- one check -----------------------------------------------------------------------------

    def tick(self, reason="schedule"):
        """Gather, decide, act, write it down. Returns the journal entry."""
        config = self.config() or {}
        conf = config.get("agent", self.conf) or self.conf
        self.conf = dict(conf)
        llm_conf = config.get("llm", {})
        if not llm_conf.get("enabled") or not (llm_conf.get("model") or "").strip():
            self.error = "the bot isn't set up — the agent needs a model (Message tab → bot)"
            return None
        if llm.busy():
            self.error = None
            self.next_run = time.time() + 60          # the bot is answering someone; come back shortly
            return None
        self.busy = True
        started = time.time()
        entry = {"ts": started, "reason": reason, "level": "quiet", "thought": "", "actions": [], "saw": "",
                 "remember": "", "error": None, "took_s": 0}
        try:
            with self._lock:
                pending, self._pending = list(self._pending), []
            lines, image, saw = self._report(config, pending)
            entry["saw"] = saw
            decision = self._decide(config, lines, image)
            # it wants a closer look: turn the camera there, see, and decide again with that in hand
            look = next((a for a in decision["actions"] if a["do"] == "look"), None)
            if look and self.look_fn and self._turns() and self.conf.get("can_look", DEFAULT_CAN["look"]):
                try:
                    seen = self.look_fn(look["text"]) or "nothing it could make out"
                except Exception as e:
                    seen = f"the camera couldn't turn ({str(e)[:80]})"
                entry["looked"] = f"{look['text']}: {seen}"[:300]
                entry["first_thought"] = decision["thought"]
                lines = lines + [f"You turned the camera to look {look['text']} and saw: {seen}"]
                decision = self._decide(config, lines, None, allowed=[a for a in self._allowed() if a != "look"])
            decision["actions"] = [a for a in decision["actions"] if a["do"] != "look"]
            entry["level"] = decision["level"]
            entry["thought"] = decision["thought"]
            entry["remember"] = decision["remember"]
            entry["actions"] = self._act(decision["actions"], config)
            if decision["remember"]:
                with self._lock:
                    self._notes = (self._notes + [decision["remember"]])[-5:]
            self.error = None
        except Exception as e:
            entry["error"] = f"{str(e)[:160]}"
            self.error = entry["error"]
            log.info("agent: %s", entry["error"])
        finally:
            entry["took_s"] = int(time.time() - started)
            self.busy = False
            self.last_run = time.time()
        self.last = {"thought": entry["thought"], "level": entry["level"], "actions": entry["actions"], "ts": entry["ts"]}
        self._write(entry)
        if entry["thought"] and entry["level"] != "quiet":
            llm.set_speech(entry["thought"], "agent")
        return entry

    def _report(self, config, pending):
        """What the sensors show, as lines for the model. Returns (lines,
        image or None, a one-line summary of what the camera saw)."""
        from . import ptz, sight
        lines = list(self.facts(config) or [])
        saw = ""
        image = None
        # the camera: a fresh look when allowed, else the last one
        frame = self.frame() if self.conf.get("look", True) else None
        if frame is not None:
            obs, entries = sight.look(frame, config, _owner(config), view=ptz.PTZ.view_key())
            if entries and self.on_events:
                try:
                    self.on_events(entries, frame)
                except Exception:
                    log.debug("agent: events not passed on", exc_info=True)
            pending = pending + entries
            if obs is not None:
                saw = sight.describe(obs)
                lines.append(f"Camera, just now: {saw}" + (f" — {obs['notes']}" if obs.get("notes") else ""))
            elif config.get("llm", {}).get("see", True):
                from .mascot import as_base64
                image = as_base64(frame, longest=640)   # no detector: let the model see for itself
                lines.append("Camera: the picture is attached")
        else:
            obs = sight.last_observation()
            if obs:
                ago = int((time.time() - obs.get("at", 0)) / 60)
                saw = sight.describe(obs)
                lines.append(f"Camera, {ago} min ago: {saw}")
        # a camera that turns: look around the room too, when asked to
        if self.conf.get("sweep") and self.survey_fn and self._turns():
            try:
                around = self.survey_fn()
                if around:
                    lines.append(f"Looking around just now (the camera turned left, ahead and right): {around}")
                    saw = (saw + " · " if saw else "") + "around: " + around[:160]
            except Exception as e:
                lines.append(f"Couldn't look around: {str(e)[:80]}")
        if pending:
            lines.append("Events since the last check: " + "; ".join(
                f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['text']}" for e in pending[-12:]))
        earlier = sight.summary(hours=12, before=min(e["ts"] for e in pending) if pending else None)
        if earlier:
            lines.append(f"Events earlier today: {earlier}")
        lines += self._home_lines(config)
        lines += self._system_lines()
        lines += self._server_lines()
        lines += self._friends_lines()
        with self._lock:
            notes = list(self._notes)
        if notes:
            lines.append("Your notes from earlier: " + " | ".join(notes[-3:]))
        recent = [e for e in self.journal(4) if e.get("thought")]
        if recent:
            lines.append("Your last thoughts: " + " | ".join(
                f"{time.strftime('%H:%M', time.localtime(e['ts']))} ({e['level']}) {e['thought'][:120]}" for e in recent[:3]))
        return lines, image, saw

    @staticmethod
    def _home_lines(config):
        try:
            from .home import HOME
            if not (HOME.enabled() and HOME.ready()):
                return []
            named = HOME.named()
            if not named:
                return [f"Home Assistant: can't be reached ({HOME.error})" if HOME.error else "Home Assistant: nothing named"]
            states = {st.get("entity_id"): st.get("state") for st in HOME.states()}
            bits = [f"{name} {states.get(eid, '?')}" for name, eid, _ in named[:16]]
            return ["Home Assistant devices: " + "; ".join(bits)]
        except Exception as e:
            return [f"Home Assistant: {str(e)[:80]}"]

    @staticmethod
    def _system_lines():
        try:
            from .modes.system import _last as L
            if not L.get("ts"):
                return []
            gb = 1 << 30
            line = f"The Pi: CPU {L['cpu']:.0f}%"
            if L.get("temp") is not None:
                line += f", {L['temp']:.0f}°C"
            line += f", memory {L['mem_pct']:.0f}%, disk {L['disk_used'] / gb:.0f}/{L['disk_total'] / gb:.0f} GB"
            return [line]
        except Exception:
            return []

    def _server_lines(self):
        """Anything you asked it to keep an eye on by address."""
        import requests
        urls = [u.strip() for u in re.split(r"[,\s]+", str(self.conf.get("watch_urls") or "")) if u.strip()]
        out = []
        for url in urls[:6]:
            target = url if url.startswith(("http://", "https://")) else "http://" + url
            t0 = time.time()
            try:
                r = requests.get(target, timeout=6, allow_redirects=True)
                out.append(f"Server {url}: up ({r.status_code}, {int((time.time() - t0) * 1000)} ms)")
            except requests.exceptions.RequestException as e:
                why = type(e).__name__.replace("Error", "").replace("Exception", "") or "error"
                out.append(f"Server {url}: DOWN ({why.lower()})")
        return out

    @staticmethod
    def _friends_lines():
        try:
            from . import friends, mesh
            peers = friends.peers()
            if not peers:
                return []
            return ["Other PiE-inks on the network: " + "; ".join(mesh.describe(p) for p in peers[:8])]
        except Exception:
            return []

    def _allowed(self):
        out = [a for a in ACTIONS if self.conf.get(f"can_{a}", DEFAULT_CAN.get(a, False))]
        if "look" in out and not (self.look_fn and self._turns()):
            out.remove("look")                     # no camera that turns: nothing to look with
        return out

    def _decide(self, config, lines, image, allowed=None):
        conf = config.get("llm", {})
        owner = _owner(config)
        goals = "\n".join(f"- {g}" for g in self.goals())
        allowed = self._allowed() if allowed is None else allowed
        system = (f"You are the agent living in {owner}'s PiE-ink, a small Raspberry Pi with an e-ink screen and a few senses. "
                  "Every few minutes you get a report of what its sensors show. Your standing goals:\n" + goals + "\n"
                  "Judge the report against the goals and answer with ONE JSON object only — no prose, no markdown:\n"
                  '{"thought": "<one or two plain sentences: what is going on, judged against the goals>", '
                  '"level": "quiet" | "note" | "alert", '
                  '"actions": [' + ", ".join(_ACTION_SHAPES[a].format(owner=owner) for a in allowed) + '], '
                  '"remember": "<a short note to yourself for next time, or an empty string>"}\n'
                  "Rules: most checks are quiet, with an empty actions list. Act only when a goal calls for it. "
                  "Never repeat a message about the same thing you already said in your last thoughts. "
                  "Ordinary comings and goings are not alerts unless a goal says so. "
                  + ("The only actions that exist are: " + ", ".join(allowed) + ". " if allowed else
                     "You may not take actions this time — only think. ")
                  + "A home command is plain words naming a device, like 'turn off the lamp'. "
                  + ("To see something better — a sound, a shape at the edge of the picture, somewhere a goal "
                     "cares about — use look: the camera turns there, you see it, and you decide again. "
                     if "look" in allowed else ""))
        prompt = "Report at " + datetime.now().strftime("%A %-I:%M %p") + ":\n" + "\n".join(f"- {ln}" for ln in lines) + \
                 "\n\nYour JSON:"
        text = llm.generate(conf, system, prompt, images=[image] if image else None, limit=4000,
                            timeout=int(conf.get("timeout_seconds", 180) or 180))
        return parse_decision(text, allowed)

    def _act(self, actions, config):
        """Carry out what it decided, within what you allowed. Returns what
        was done, with results."""
        done = []
        for a in actions[:3]:
            kind, text = a["do"], a["text"]
            allowed = self.conf.get(f"can_{kind}", DEFAULT_CAN.get(kind, False))
            result = "not allowed" if not allowed else "no way to do that"
            if allowed and kind == "notify":
                last = self._told.get(text.lower())
                if last and time.time() - last < 2 * 3600:
                    result = "already said recently"
                    done.append({"do": kind, "text": text, "result": result})
                    continue
                self._told[text.lower()] = time.time()
                for k in [k for k, t in self._told.items() if time.time() - t > 6 * 3600]:
                    del self._told[k]
            if allowed and kind in self.do:
                try:
                    result = self.do[kind](text) or "done"
                except Exception as e:
                    result = f"failed: {str(e)[:100]}"
            done.append({"do": kind, "text": text, "result": str(result)[:160]})
            log.info("agent: %s '%s' -> %s", kind, text[:80], result)
        return done


_ACTION_SHAPES = {
    "notify": '{{"do": "notify", "text": "<a short message to {owner}\'s phone>"}}',
    "say": '{{"do": "say", "text": "<something to say out loud in the room>"}}',
    "show": '{{"do": "show", "text": "<up to twelve words for the little screen>"}}',
    "home": '{{"do": "home", "command": "<a plain command for Home Assistant, e.g. turn off the lamp>"}}',
    "look": '{{"do": "look", "text": "left | right | up | down | around | ahead"}}',
}


def _owner(config):
    name = ((config or {}).get("me", {}).get("name") or "").strip()
    return name or "the owner"


def parse_decision(text, allowed=ACTIONS):
    """The model's answer as a decision. Tolerant of the usual slips."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("the model gave no JSON: " + (text or "")[:80])
    raw = m.group()
    try:
        data = json.loads(raw)
    except ValueError:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("'", '"')))
    if not isinstance(data, dict):
        raise ValueError("the answer isn't an object")
    level = str(data.get("level") or "quiet").strip().lower()
    if level not in LEVELS:
        level = "alert" if "alert" in level else "note" if "note" in level else "quiet"
    actions = []
    raw_actions = data.get("actions") or []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    for a in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("do") or a.get("type") or a.get("action") or "").strip().lower()
        text = str(a.get("text") or a.get("command") or a.get("message") or "").strip()
        if kind in allowed and text:
            actions.append({"do": kind, "text": text[:300]})
    return {"thought": str(data.get("thought") or "").strip()[:400], "level": level, "actions": actions,
            "remember": str(data.get("remember") or "").strip()[:200]}


AGENT = Agent()
