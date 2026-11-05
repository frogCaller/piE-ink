"""A Pietato on your network — your pocket media center (github.com/frogCaller/Pietato) — noticed,
greeted, and worked with.

It comes to PiE-ink: it asks for what this Pi has already fetched (GET /api/shared, so the
two don't both fetch the weather and the prices) and looks through this Pi's camera, and
every request it makes says who it is (User-Agent: pietato/…, or pie-tato/… and pitato/… from
before it was renamed). And it says it's there, on the UDP port the PiE-inks use, every 20
seconds ({"pietato": 1, "id", "port", "host", "accent"}: Settings > Share with other Pietatos,
on it), as its camera server does ("gpicam"). So a Pietato is known the moment it first asks
or speaks, and its cameras the moment they speak. Its name is its own web page's
(GET http://<it>:5432/api/info), asked once; a page with a password still says it's a Pietato.

The first time one shows up — and when it comes back after an hour away — PiE-ink is
thrilled: a line on the bot's screen with a starry-eyed face, out loud if it talks, in the
bot's own words when it has a model, and a line in the events list. From then on the bot
knows it's there ("is my Pietato around?"), the page has a tile for it, /pis and the Friends'
cameras include its cameras, and "show me the camera from <its name>" works. Nothing is sent
to it that it didn't ask for, and nothing is needed on its side. data/pitato.json remembers
the names and when each was greeted, so a restart doesn't say hello all over again.

While one is about, PiE-ink also keeps an eye on what's on its screen — the game being
played, the film or the station playing, which app is open — asking every few seconds what
its own web page asks it (GET /api/pi/state), or, for a Pietato on this very Pi, media mode's
own channel (127.0.0.1:5480, with the key it leaves for the page). So the bot can say "you're
playing Snake on the Pietato", the tile says so, and the agent sees it. A page with a password
keeps that to itself: then PiE-ink only knows the Pietato is there.
"""
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time

import requests

from .settings import DATA_DIR

log = logging.getLogger(__name__)

NAMES = ("pietato", "pie-tato", "pitato")   # what it has been called: its User-Agent, its page's password box
ACCENTS = {"Blue": "#60aae4", "Teal": "#40c4b4", "Green": "#6ec878", "Purple": "#a082e6",   # its colour, as its
           "Pink": "#e878aa", "Red": "#ec6464", "Gold": "#e6be50", "White": "#e4e8ee"}      # beacon names it
PORT = 5432                 # Pietato's web page
CAMERA_PORT = 5433          # ...and its camera server's
CONTROL_PORT = 5480         # its media mode, on its own Pi only: the channel its page uses
CONTROL_KEY = "/run/pitato/control-key"   # ...and the key it leaves there for the page (yours to read)
STATE_EVERY = 12            # seconds between looks at what's on its screen
LISTEN_KINDS = ("audio", "radio")        # what it plays that you listen to rather than watch
AWAY = 3600                 # seconds without a word before it's away (and greeted when it's back)
CAMERA_GONE = 30            # a camera beacon this old: the camera is off the list
EXCITED_FOR = 180           # seconds the mood lasts after a hello
KEEP = 8                    # Pietatos remembered, at most
PATH = os.path.join(DATA_DIR, "pitato.json")
TIMEOUT = 3                 # it's on your network: it answers quickly, or not at all


_SAID = re.compile(r"(?<![-\w])pie[- ]?tato(e?s)?(?![-\w])", re.I)     # (not inside a name: pietato-tv stays)


def plain_name(text):
    """What's said or typed, with Pietato's name as the commands below know it: "pietato", "pie-tato", "pie tato"
    (and their plurals) as "pitato" — the name it had first, still understood. A name with it in (a camera called
    "Pietato cam") is matched the same way, so both sides of a match go through this."""
    return _SAID.sub(lambda m: ("Pitato" if m.group(0)[0].isupper() else "pitato") + ("s" if m.group(1) else ""), text or "")


def _ip(text):
    try:
        return str(ipaddress.ip_address((text or "").strip()))
    except ValueError:
        return None


def _clean(name, limit=40):
    """A name from another machine: printable, one line, not long."""
    text = "".join(ch for ch in str(name or "") if ch.isprintable() and ch not in "<>\"'\\")
    return " ".join(text.split())[:limit]


_mine = {"at": 0.0, "ips": set()}


def _my_ips():
    """This Pi's own addresses: a Pietato at one of them lives on this very Pi."""
    if time.time() - _mine["at"] > 60:
        ips = {"127.0.0.1", "::1"}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        try:
            ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        _mine.update(at=time.time(), ips=ips)
    return _mine["ips"]


def _reverse_name(ip):
    """The name your router knows the address by, if it does (pitato.lan → pitato)."""
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (OSError, ValueError):
        return ""
    host = host.split(".")[0]
    return "" if not host or host == ip or host.replace(".", "").replace("-", "").isdigit() else _clean(host)


class Pitato:
    def __init__(self):
        self.seen = {}               # ip -> {"ip", "name", "web", "locked", "accent", "first", "last", "asks", "greeted", "probed"}
        self.cameras = {}            # "ip:port" -> {"ip", "port", "name", "host", "turns", "seen", "id"}
        self.on_arrive = None        # (a Pietato's entry, back: bool) -> None: say hello
        self._lock = threading.Lock()
        self._probing = set()
        self._excited_until = 0.0
        self._saved = 0.0
        self._watching = False
        self._load()

    # -- remembering ----------------------------------------------------------------------------

    def _load(self):
        try:
            with open(PATH) as f:
                d = json.load(f)
            for ip, p in (d.get("pitatos") or {}).items():
                if _ip(ip) and isinstance(p, dict):
                    self.seen[ip] = {"ip": ip, "name": _clean(p.get("name")), "web": p.get("web"), "locked": bool(p.get("locked")),
                                     "accent": _clean(p.get("accent"), 12), "first": float(p.get("first") or 0),
                                     "last": float(p.get("last") or 0), "asks": int(p.get("asks") or 0),
                                     "greeted": float(p.get("greeted") or 0), "probed": 0.0}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self, force=False):
        now = time.time()
        if not force and now - self._saved < 60:
            return
        self._saved = now
        with self._lock:
            keep = sorted(self.seen.values(), key=lambda p: -p["last"])[:KEEP]
            data = {"pitatos": {p["ip"]: {k: p.get(k) for k in ("name", "web", "locked", "accent", "first", "last", "asks", "greeted")}
                                for p in keep}}
        try:
            with open(PATH + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(PATH + ".tmp", PATH)
        except OSError as e:
            log.debug("could not save the Pietatos: %s", e)

    # -- hearing from one ------------------------------------------------------------------------

    @staticmethod
    def is_pitato(user_agent):
        """Whether a request's User-Agent is a Pietato's (pietato/1.0 …; pie-tato/ and pitato/ before)."""
        return (user_agent or "").lower().startswith(tuple(n + "/" for n in NAMES))

    def heard(self, ip, path=""):
        """A request from a Pietato: it's there. Called for every request whose User-Agent says so."""
        ip = _ip(ip)
        if not ip:
            return
        now = time.time()
        with self._lock:
            p = self.seen.get(ip)
            new = p is None
            if new:
                p = self.seen[ip] = {"ip": ip, "name": "", "web": None, "locked": False, "accent": "", "first": now,
                                     "last": 0.0, "asks": 0, "greeted": 0.0, "probed": 0.0, "here": ip in _my_ips()}
            back = not new and now - p["last"] > AWAY            # (remembered from before a restart, and away since)
            p["last"] = now
            if path.startswith("/api/shared"):
                p["asks"] += 1
            p["what"] = path
            need_name = now - p["probed"] > AWAY
        if need_name:
            self._probe_later(ip)
        if new or back:
            threading.Thread(target=self._greet, args=(ip, back and not new), daemon=True, name="pitato-hello").start()
        self._watch_later()
        self._save()

    def beacon_heard(self, ip, msg):
        """A Pietato's own beacon ({"pietato": 1, "id", "port", "host", "accent"}, every 20 seconds while its
        Settings > Share with other Pietatos is on): it's there — by its name and its colour — whether or not it
        asks this Pi for anything."""
        ip = _ip(ip)
        if not ip or not isinstance(msg, dict) or not isinstance(msg.get("id"), str):
            return
        port = msg.get("port", PORT)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            return
        now = time.time()
        with self._lock:
            p = self.seen.get(ip)
            new = p is None
            if new:
                p = self.seen[ip] = {"ip": ip, "name": "", "web": None, "locked": False, "accent": "", "first": now,
                                     "last": 0.0, "asks": 0, "greeted": 0.0, "probed": 0.0, "here": ip in _my_ips()}
            back = not new and now - p["last"] > AWAY
            p["last"] = p["beacon"] = now
            if not p.get("name"):
                p["name"] = _clean(msg.get("host"))
            if msg.get("accent") in ACCENTS:
                p["accent"] = ACCENTS[msg["accent"]]
            need_name = now - p["probed"] > AWAY
        if need_name:
            self._probe_later(ip)
        if new or back:
            threading.Thread(target=self._greet, args=(ip, back and not new), daemon=True, name="pitato-hello").start()
        self._watch_later()
        self._save()

    def camera_heard(self, ip, msg):
        """Pietato's camera server (camera.py) said it's there: its beacon on the PiE-inks' port."""
        ip = _ip(ip)
        if not ip or not isinstance(msg, dict):
            return
        port = msg.get("port") or CAMERA_PORT
        if isinstance(port, str) and port.isdigit():
            port = int(port)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            return
        key = f"{ip}:{port}"
        now = time.time()
        with self._lock:
            self.cameras[key] = {"ip": ip, "port": port, "name": _clean(msg.get("name")) or _clean(msg.get("host")) or ip,
                                 "host": _clean(msg.get("host")) or ip, "turns": bool(msg.get("turns")),
                                 "id": _clean(msg.get("id"), 16) or key, "seen": now}
            for old in [k for k, c in self.cameras.items() if now - c["seen"] > CAMERA_GONE * 20]:
                del self.cameras[old]
            p = self.seen.get(ip)
            probe = p is None or now - p["probed"] > AWAY
        if probe:                                              # a Pi with Pietato's camera may be the Pietato itself
            self._probe_later(ip)

    # -- asking its page who it is --------------------------------------------------------------

    def _probe_later(self, ip):
        with self._lock:
            if ip in self._probing:
                return
            self._probing.add(ip)
        threading.Thread(target=self._probe, args=(ip,), daemon=True, name="pitato-who").start()

    def _probe(self, ip):
        """GET http://ip:5432/api/info — its name and colour; 401: a page with a password (still
        a Pietato); nothing on that port: a Pietato that only asks (or a Pi with just its camera)."""
        name, web, locked, accent = "", False, False, ""
        try:
            r = requests.get(f"http://{ip}:{PORT}/api/info", timeout=TIMEOUT, headers={"Accept": "application/json"})
            if r.status_code == 401 and any(n in (r.headers.get("WWW-Authenticate") or "").lower() for n in NAMES):
                web, locked = True, True
            elif r.status_code == 200:
                d = r.json()
                if isinstance(d, dict) and "accent" in d and "folders" in d:
                    web, name, accent = True, _clean(d.get("name")), _clean(d.get("accent"), 12)
        except Exception as e:
            log.debug("no Pietato page at %s: %s", ip, e)
        here = ip in _my_ips()
        if not name:
            name = socket.gethostname().split(".")[0] if here else _reverse_name(ip)
        now = time.time()
        with self._lock:
            self._probing.discard(ip)
            p = self.seen.get(ip)
            if p is None:
                if not web:                                    # only its camera speaks for it: not the Pietato itself
                    return
                p = self.seen[ip] = {"ip": ip, "name": "", "web": None, "locked": False, "accent": "", "first": now,
                                     "last": now, "asks": 0, "greeted": 0.0, "probed": 0.0, "here": here}
                new = True
            else:
                new = False
            p.update(web=web, locked=locked, probed=now, here=here)
            if name:
                p["name"] = name
            if accent:
                p["accent"] = accent
        self._save(force=True)
        self._watch_later()
        if new:
            threading.Thread(target=self._greet, args=(ip, False), daemon=True, name="pitato-hello").start()

    def _greet(self, ip, back):
        """Hello — once per arrival, and not again within the hour (a restart in between doesn't count)."""
        for _ in range(TIMEOUT * 4 + 4):                       # the name first, if its page is answering
            with self._lock:
                probing = ip in self._probing
            if not probing:
                break
            time.sleep(0.25)
        now = time.time()
        if not self.on_arrive:                                 # nobody to tell yet (starting up): next time
            return
        with self._lock:
            p = self.seen.get(ip)
            if not p or now - p["greeted"] < AWAY:
                return
            p["greeted"] = now
            self._excited_until = now + EXCITED_FOR
            entry = dict(p)
        self._save(force=True)
        try:
            self.on_arrive(entry, back)
        except Exception:
            log.exception("the hello to the Pietato")

    # -- what's on its screen -------------------------------------------------------------------

    def _watch_later(self):
        with self._lock:
            if self._watching:
                return
            self._watching = True
        threading.Thread(target=self._watch, daemon=True, name="pitato-screen").start()

    def _watch(self):
        """Every few seconds while a Pietato is about: what each has on its screen."""
        try:
            n = 0
            while True:
                around = self.around()
                if not around:
                    return
                for p in around:
                    state, how = self._state_of(p)
                    with self._lock:
                        q = self.seen.get(p["ip"])
                        if q is not None:
                            q.update(state=state, state_how=how, state_at=time.time())
                if n % 25 == 0:                                  # every five minutes or so: what's on its shelves
                    try:
                        from . import shelf
                        shelf.warm()
                    except Exception:
                        log.debug("the Pietato's shelves", exc_info=True)
                n += 1
                time.sleep(STATE_EVERY)
        finally:
            with self._lock:
                self._watching = False

    def _state_of(self, p):
        """(what's on its screen, how it was learnt): its page's own question, or on this Pi
        media mode's channel with the key it leaves for the page. (None, "locked") when its
        page has a password; (None, "quiet") when nothing answers."""
        tries = []
        if p.get("here"):
            key = ""
            try:
                with open(CONTROL_KEY) as f:
                    key = f.read().strip()
            except OSError:
                pass
            tries.append(("local", f"http://127.0.0.1:{CONTROL_PORT}/state", {"X-Pitato-Key": key} if key else {}))
        if p.get("web"):
            tries.append(("page", f"http://{p['ip']}:{PORT}/api/pi/state", {"X-Pitato-Page": "1", "Accept": "application/json"}))
        locked = False
        for how, url, headers in tries:
            try:
                r = requests.get(url, headers=headers, timeout=TIMEOUT)
            except Exception as e:
                log.debug("no answer from the Pietato's screen at %s: %s", url, e)
                continue
            if r.status_code == 401:
                locked = True
                continue
            if r.status_code != 200:
                continue
            try:
                d = r.json()
            except ValueError:
                continue
            if isinstance(d, dict) and "mode" in d:
                return self._tidy_state(d), how
        return None, "locked" if locked else "quiet"

    @staticmethod
    def _tidy_state(d):
        """Only what's wanted of its answer, as the right kinds of thing."""
        out = {"mode": "terminal" if d.get("mode") == "terminal" else "media", "view": _clean(d.get("view"), 20),
               "where": _clean(d.get("where"), 120), "video": bool(d.get("video")), "playing": None}
        pl = d.get("playing")
        if isinstance(pl, dict) and (pl.get("title") or pl.get("song")):
            def num(v):
                return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e7 else 0.0
            out["playing"] = {"title": _clean(pl.get("title"), 80), "sub": _clean(pl.get("sub"), 80), "song": _clean(pl.get("song"), 80),
                              "kind": _clean(pl.get("kind"), 12), "pos": num(pl.get("pos")), "length": num(pl.get("length")),
                              "paused": bool(pl.get("paused"))}
        size = d.get("size")
        if (isinstance(size, (list, tuple)) and len(size) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) and 64 <= v <= 8192 for v in size)):
            out["size"] = [int(size[0]), int(size[1])]       # its screen: what a picture sent to it is made to fit
        return out

    @staticmethod
    def activity(p):
        """What's being done on it, in a few words: "playing Snake", "watching Blade Runner, 42 of
        117 min in", "listening to Groove Salad", "on TV › Plex", "at its home screen"; "" if unknown."""
        st = (p or {}).get("state") or {}
        if not st:
            return ""
        if st.get("mode") == "terminal":
            return "in the terminal"
        where = st.get("where") or ""
        if st.get("view") in ("game", "gamemenu"):
            title = where.split(" › ")[-1] if " › " in where else where
            return f"playing {title}" if title and title != "Games" else "playing a game"
        pl = st.get("playing")
        if pl:
            name = pl.get("song") if pl.get("kind") == "radio" and pl.get("song") else (pl.get("title") or pl.get("song"))
            verb = "listening to" if pl.get("kind") in LISTEN_KINDS else "watching"
            text = f"{verb} {name}"
            if pl.get("kind") == "radio" and pl.get("song") and pl.get("title"):
                text += f" on {pl['title']}"
            if pl.get("paused"):
                text += " (paused)"
            elif pl.get("length") and pl.get("pos") and pl["length"] > 120:
                text += f", {int(pl['pos'] // 60)} of {int(pl['length'] // 60)} min in"
            return text
        if st.get("view") == "home":
            return "at its home screen"
        return f"on {where}" if where else ""

    # -- for the page, the bot, the mesh --------------------------------------------------------

    def excited(self):
        return time.time() < self._excited_until

    @staticmethod
    def _label(p):
        return p.get("name") or p["ip"]

    def around(self, within=AWAY):
        """The Pietatos heard from lately, newest first."""
        now = time.time()
        with self._lock:
            out = [dict(p) for p in self.seen.values() if now - p["last"] <= within]
        return sorted(out, key=lambda p: -p["last"])

    def status(self):
        now = time.time()
        pitatos = []
        for p in self.around():
            cams = [c for c in self.camera_list() if c["ip"] == p["ip"]]
            pitatos.append({"ip": p["ip"], "name": self._label(p), "url": f"http://{p['ip']}:{PORT}" if p.get("web") else "",
                            "locked": bool(p.get("locked")), "accent": p.get("accent") or "", "ago": int(now - p["last"]),
                            "asks": p.get("asks", 0), "asking": p.get("what", "").startswith("/api/shared"),
                            "here": bool(p.get("here")), "cameras": [c["name"] for c in cams],
                            "activity": self.activity(p), "screen": p.get("state_how") or ""})
        return {"any": bool(pitatos), "pitatos": pitatos, "cameras": self.camera_list(), "excited": self.excited()}

    def camera_list(self):
        now = time.time()
        with self._lock:
            out = [dict(c) for c in self.cameras.values() if now - c["seen"] <= CAMERA_GONE]
        for c in out:
            c["url"] = f"http://{c['ip']}:{c['port']}"
            c["ago"] = int(now - c["seen"])
        return sorted(out, key=lambda c: c["name"].lower())

    def peers(self):
        """Its cameras as the mesh sees a Pi: for "show me the camera from <name>" and the
        Friends' cameras (they answer the same calls as a PiE-ink's camera)."""
        out = []
        for c in self.camera_list():
            out.append({"id": f"pitato-{c['id']}", "host": c["name"], "ip": c["ip"], "port": c["port"], "url": c["url"],
                        "kind": "pitato", "version": "", "panel": "", "mode": None, "where": None, "ago": c["ago"],
                        "caps": {"camera": True, "turns": c["turns"], "location": ""}})
        return out

    def words(self, owner="they"):
        """A line for the bot (empty when there's no Pietato about): that it's there, and what
        {owner} is doing on it right now, when its screen can be seen."""
        around = self.around()
        if not around:
            return ""
        now = time.time()
        bits, doing = [], []
        for p in around[:3]:
            ago = int(now - p["last"])
            when = "just now" if ago < 90 else f"{ago // 60} min ago" if ago < 5400 else f"{ago // 3600} h ago"
            what = "asking you for the weather and prices" if p.get("asks") else "looking through your camera" \
                if (p.get("what") or "").startswith("/api/camera") else "talking to you" if p.get("what") else "saying it's there"
            cams = [c["name"] for c in self.camera_list() if c["ip"] == p["ip"]]
            bits.append(f"{self._label(p)} (" + ("it lives on this very Pi, beside you; " if p.get("here") else "")
                        + f"{what}, {when}" + (f"; it has a camera, {cams[0]}" if cams else "") + ")")
            act = self.activity(p)
            if act:
                doing.append(f"{owner} {'is' if owner != 'they' else 'are'} {act} on {'it' if len(around) == 1 else self._label(p)} right now")
            elif p.get("state_how") == "locked":
                doing.append(f"its page has a password, so what's on its screen stays its own")
        others = [c["name"] for c in self.camera_list() if c["ip"] not in {p["ip"] for p in around}]
        line = ("Pietato — " + ("their" if len(around) > 1 else "the") + " pocket media center, a friend of yours — is on the network: "
                + "; ".join(bits))
        if doing:
            line += ". " + "; ".join(doing)
        if others:
            line += f". Pietato cameras on the network too: {', '.join(others[:3])}"
        if self.excited():
            line += ". You're thrilled about it"
        return line

    def describe(self):
        """A few lines for /pis."""
        out = []
        now = time.time()
        for p in self.around():
            ago = int(now - p["last"])
            when = f"{ago} s ago" if ago < 90 else f"{ago // 60} min ago"
            what = f"asks me for the weather and prices ({p['asks']} times)" if p.get("asks") else "looks through my camera" \
                if (p.get("what") or "").startswith("/api/camera") else "talks to me" if p.get("what") else "says it's there"
            page = f", its page at http://{p['ip']}:{PORT}" if p.get("web") else ""
            act = self.activity(p)
            out.append(f"Pietato {self._label(p)}{' (on this Pi)' if p.get('here') else ''}: {what}{page} · {when}"
                       + (f" — {act}" if act else ""))
        return out


PITATO = Pitato()
