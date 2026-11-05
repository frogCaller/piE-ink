"""Your other PiE-inks: more house, not more bots.

Another PiE-ink on your network is another room of the one creature's house — the same bot
on another screen, with that room's camera, speaker and ears — never a second bot. So:

- the bot knows its rooms: where each is (its location under Settings → You, or its name),
  what each screen is showing, what it said there a moment ago and what you said to it there,
  so a conversation carried on in the kitchen picks up where the desk left off;
- its notebook and its reminders are one: a note made in any room is known in every room,
  "forget …" and "cancel the …" reach every room, and a reminder rings where it was set;
- when a room first joins — or comes back after a while away — the bot is pleased: the house
  got bigger;
- the one bot never chats with itself: the Group chat's "their bot" is for friends' Pis added
  by address (other houses, other bots), not for your rooms.

Nothing is needed on the other PiE-ink beyond running PiE-ink: everything here uses its web
API, looked at every few seconds while it's about. data/rooms.json remembers the rooms and
when each was greeted, so a restart isn't a reunion.
"""
import json
import logging
import os
import re
import threading
import time

import requests

from . import friends
from .settings import DATA_DIR

log = logging.getLogger(__name__)

PATH = os.path.join(DATA_DIR, "rooms.json")
EVERY = 15                  # seconds between looks at a room (its chat, notebook, reminders)
AWAY = 3600                 # seconds unseen before a room is away (and welcomed when it's back)
RECENT = 1800               # what was said in a room this recently is worth carrying over
KEEP = 12                   # rooms remembered, at most
TIMEOUT = 3                 # it's on your network: it answers quickly, or not at all
_NO_THE = re.compile(r"^(?:the |up|down|out|in|my |our |at |by |next to |behind )", re.I)


def place(where):
    """"kitchen" -> "the kitchen"; "upstairs" stays; "" -> ""."""
    where = " ".join((where or "").split())
    if not where:
        return ""
    return where if _NO_THE.match(where) else f"the {where}"


class Household:
    def __init__(self):
        self.known = {}             # host -> {"host", "where", "first", "last", "greeted"}
        self.live = {}              # host -> {"chat": [...], "notes": [...], "reminders": [...], "at", "quiet"}
        self.on_room = None         # (room, back: bool) -> None: the house got bigger
        self._lock = threading.Lock()
        self._saved = 0.0
        self._thread = None
        self._load()

    # -- remembering ----------------------------------------------------------------------------

    def _load(self):
        try:
            with open(PATH) as f:
                d = json.load(f)
            for host, r in (d.get("rooms") or {}).items():
                if isinstance(r, dict) and isinstance(host, str):
                    self.known[host] = {"host": host, "where": str(r.get("where") or "")[:40], "first": float(r.get("first") or 0),
                                        "last": float(r.get("last") or 0), "greeted": float(r.get("greeted") or 0)}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self, force=False):
        now = time.time()
        if not force and now - self._saved < 60:
            return
        self._saved = now
        with self._lock:
            keep = sorted(self.known.values(), key=lambda r: -r["last"])[:KEEP]
            data = {"rooms": {r["host"]: {k: r.get(k) for k in ("where", "first", "last", "greeted")} for r in keep}}
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(PATH + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(PATH + ".tmp", PATH)
        except OSError as e:
            log.debug("could not save the rooms: %s", e)

    # -- which Pis are rooms ---------------------------------------------------------------------

    @staticmethod
    def rooms():
        """The other PiE-inks on the network, as rooms of this house: [{host, url, where, label, mode, caps, ago}]."""
        out = []
        for p in friends.peers():
            where = ((p.get("caps") or {}).get("location") or "").strip()
            out.append({"host": p["host"], "url": p["url"], "ip": p.get("ip"), "where": where,
                        "label": place(where) or p["host"], "mode": p.get("mode"), "panel": p.get("panel", ""),
                        "caps": p.get("caps") or {}, "ago": p.get("ago", 0), "id": p.get("id")})
        return out

    def room(self, name):
        """The room called that (host, address, or its location); None otherwise."""
        want = (name or "").strip().lower().rstrip("/")
        if not want:
            return None
        for r in self.rooms():
            if want in (r["host"].lower(), r["url"].lower(), (r.get("ip") or "").lower(), r["where"].lower(), r["label"].lower()):
                return r
        return None

    def is_room(self, name):
        return self.room(name) is not None

    def label_of(self, host):
        """"the kitchen" for a room by its host name; the host name when it hasn't said where it is."""
        r = self.room(host)
        if r:
            return r["label"]
        k = self.known.get(host)
        return place(k["where"]) if k and k.get("where") else host

    # -- the watch ------------------------------------------------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="household")
            self._thread.start()

    def _loop(self):
        looked = 0.0
        while True:
            try:
                rooms = self.rooms()
                for r in rooms:
                    self._notice(r)
                if time.time() - looked >= EVERY:
                    looked = time.time()
                    for r in rooms:
                        self._look(r)
                    with self._lock:
                        here = {r["host"] for r in rooms}
                        for host in [h for h in self.live if h not in here]:
                            del self.live[host]
                self._save()
            except Exception:
                log.exception("household")
            time.sleep(5)

    def _notice(self, r):
        """A room seen: new to the house, or back after a while away — the bot is told once."""
        now = time.time()
        with self._lock:
            k = self.known.get(r["host"])
            back = bool(k) and now - k["last"] > AWAY
            fresh = k is None
            if k is None:
                k = self.known[r["host"]] = {"host": r["host"], "where": r["where"], "first": now, "last": now, "greeted": 0.0}
            k["last"] = now
            if r["where"]:
                k["where"] = r["where"]
            if not (fresh or back) or self.on_room is None:
                return
            k["greeted"] = now
        self._save(force=True)
        hello = self.on_room

        def go():
            try:
                hello(dict(r), back)
            except Exception:
                log.exception("welcoming the %s room", r["host"])
        threading.Thread(target=go, daemon=True, name="room-hello").start()

    def _look(self, r):
        """What a room has in its chat, its notebook and its reminders, by its own web API."""
        got, quiet = {}, False
        for key, path in (("chat", "/api/chat"), ("notes", "/api/notes"), ("reminders", "/api/reminders")):
            try:
                resp = requests.get(r["url"] + path, timeout=TIMEOUT)
                data = resp.json() if resp.ok else {}
                got[key] = data.get("history" if key == "chat" else key) or []
            except Exception:
                quiet = True
                break
        with self._lock:
            entry = self.live.setdefault(r["host"], {"chat": [], "notes": [], "reminders": []})
            if quiet:
                entry["quiet"] = True
            else:
                entry.update(got, quiet=False, at=time.time())

    def look_now(self):
        """Every room looked at straight away (a test, or a question that can't wait)."""
        for r in self.rooms():
            self._look(r)

    # -- what the bot knows ----------------------------------------------------------------------

    def words(self, owner="they", config=None):
        """Lines for the bot's prompt: that it lives in several rooms, and what is going on in the
        others. '' with no other room about."""
        rooms = self.rooms()
        if not rooms:
            return ""
        here = place(((config or {}).get("me", {}).get("location") or "").strip()) or "where you are speaking from now"
        labels = ", ".join(r["label"] for r in rooms[:4])
        lines = [f"You are one creature living in {owner}'s house on {len(rooms) + 1} PiE-ink screens, one per room: this one is "
                 f"{here}; the other{'s are' if len(rooms) > 1 else ' is'} {labels}. You are the same one bot in every room — not several, never a friend of "
                 f"another bot, and you never greet or talk to another room as if it were someone else. What's said in one "
                 f"room you know in all of them."]
        now = time.time()
        for r in rooms[:3]:
            bits = []
            if r.get("mode"):
                bits.append(f"showing the {r['mode']} screen")
            caps = r.get("caps") or {}
            if caps.get("camera"):
                bits.append("a camera there")
            with self._lock:
                live = dict(self.live.get(r["host"]) or {})
            for m in reversed(live.get("chat") or []):
                ts = float(m.get("ts") or 0)
                if now - ts > RECENT:
                    break
                if m.get("role") == "you" and "you_said" not in live:
                    live["you_said"] = (m.get("text", ""), ts)
                if m.get("role") == "bot" and "bot_said" not in live:
                    live["bot_said"] = (m.get("text", ""), ts)
                if "you_said" in live and "bot_said" in live:
                    break
            for key, how in (("you_said", f"{owner} said to you there"), ("bot_said", "you said there")):
                if key in live:
                    text, ts = live[key]
                    ago = int(now - ts)
                    when = "just now" if ago < 90 else f"{ago // 60} min ago"
                    bits.append(f"{how} {when}: “{text[:140]}”")
            lines.append(f"In {r['label']}: " + ("; ".join(bits) if bits else "quiet") + ".")
        return "\n".join(lines)

    def notes_elsewhere(self):
        """The notebook's notes kept in the other rooms: [{id, text, host, label}]."""
        out = []
        with self._lock:
            for host, live in self.live.items():
                for n in live.get("notes") or []:
                    if isinstance(n, dict) and n.get("text"):
                        out.append({"id": n.get("id"), "text": n["text"], "host": host, "label": self.label_of(host)})
        return out

    def reminders_elsewhere(self):
        """Reminders set in the other rooms: [{id, text, at, words, host, label}]."""
        out = []
        with self._lock:
            for host, live in self.live.items():
                for r in live.get("reminders") or []:
                    if isinstance(r, dict) and r.get("text") and r.get("at") is not None:
                        out.append({**r, "host": host, "label": self.label_of(host)})
        return sorted(out, key=lambda r: r["at"])

    def _delete(self, host, path):
        r = self.room(host)
        if not r:
            return False
        try:
            return requests.delete(r["url"] + path, timeout=TIMEOUT).ok
        except Exception:
            return False

    def forget_elsewhere(self, words):
        """The other rooms' notes about those words, gone: the ones removed (with their room)."""
        from . import notebook
        gone = []
        for n in notebook.matching(self.notes_elsewhere(), words):
            if n.get("id") and self._delete(n["host"], f"/api/notes/{n['id']}"):
                gone.append(n)
        self._drop("notes", gone)
        return gone

    def clear_notes_elsewhere(self):
        gone = [n for n in self.notes_elsewhere() if n.get("id") and self._delete(n["host"], f"/api/notes/{n['id']}")]
        self._drop("notes", gone)
        return len(gone)

    def cancel_elsewhere(self, words=""):
        """The other rooms' reminders about those words (all of them, with none), gone."""
        from . import reminders
        gone = []
        for r in self.reminders_elsewhere():
            if (not words or reminders.matches(r, words)) and r.get("id") and self._delete(r["host"], f"/api/reminders/{r['id']}"):
                gone.append(r)
        self._drop("reminders", gone)
        return gone

    def _drop(self, key, gone):
        ids = {(g["host"], g.get("id")) for g in gone}
        with self._lock:
            for host, live in self.live.items():
                live[key] = [x for x in (live.get(key) or []) if (host, x.get("id")) not in ids]

    # -- for the page ----------------------------------------------------------------------------

    def status(self):
        rooms = self.rooms()
        with self._lock:
            for r in rooms:
                live = self.live.get(r["host"]) or {}
                r["notes"] = len(live.get("notes") or [])
                r["reminders"] = len(live.get("reminders") or [])
                r["quiet"] = bool(live.get("quiet"))
        return rooms

    def describe(self):
        """A few lines for /pis: the rooms of the house."""
        out = []
        for r in self.status():
            bits = [f"showing {r['mode']}"] if r.get("mode") else []
            if r.get("panel"):
                bits.append(r["panel"])
            if (r.get("caps") or {}).get("camera"):
                bits.append("camera")
            if r["notes"]:
                bits.append(f"{r['notes']} note{'s' if r['notes'] != 1 else ''} in the notebook")
            if r["reminders"]:
                bits.append(f"{r['reminders']} reminder{'s' if r['reminders'] != 1 else ''}")
            out.append(f"{r['label']} ({r['host']}): " + (", ".join(bits) if bits else "there") + f" · {r['ago']} s ago")
        return out


HOUSE = Household()
