"""Your Home Assistant, spoken to.

"turn on the living room tv", "go to channel 5452", "volume up", "mute",
"open netflix", "turn off the lamp": the words are matched here, before the
bot hears them, and become service calls over Home Assistant's REST API with
a long-lived access token (your profile → Security → Long-lived access
tokens). The TV is a media_player entity; brands that only take key presses
for some things (channel digits, arrows) use its remote entity as well.
Anything else with an on/off is found by the name Home Assistant gives it.

Nothing here raises at the caller: handle() returns what the bot should say,
or None when the words weren't a home command at all, so the bot answers
them instead.
"""
import logging
import re
import threading
import time

import requests

log = logging.getLogger(__name__)

# what the buttons on each brand's remote are called, in Home Assistant terms
PRESETS = {
    "samsung": {"label": "Samsung", "domain": "remote", "service": "send_command", "field": "command", "keys": {
        "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT", "enter": "KEY_ENTER",
        "back": "KEY_RETURN", "home": "KEY_HOME", "exit": "KEY_EXIT", "menu": "KEY_MENU", "guide": "KEY_GUIDE",
        "info": "KEY_INFO", "source": "KEY_SOURCE", "power": "KEY_POWER", "mute": "KEY_MUTE",
        "vol_up": "KEY_VOLUP", "vol_down": "KEY_VOLDOWN", "ch_up": "KEY_CHUP", "ch_down": "KEY_CHDOWN",
        "play": "KEY_PLAY", "pause": "KEY_PAUSE", "digit": "KEY_{d}"}},
    "webos": {"label": "LG webOS", "domain": "webostv", "service": "button", "field": "button", "keys": {
        "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "enter": "ENTER", "back": "BACK",
        "home": "HOME", "exit": "EXIT", "menu": "MENU", "guide": "GUIDE", "info": "INFO", "mute": "MUTE",
        "vol_up": "VOLUMEUP", "vol_down": "VOLUMEDOWN", "ch_up": "CHANNELUP", "ch_down": "CHANNELDOWN",
        "play": "PLAY", "pause": "PAUSE", "digit": "{d}"}},
    "androidtv": {"label": "Android / Google TV", "domain": "remote", "service": "send_command", "field": "command", "keys": {
        "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "enter": "DPAD_CENTER",
        "back": "BACK", "home": "HOME", "menu": "MENU", "guide": "GUIDE", "info": "INFO", "source": "TV_INPUT",
        "power": "POWER", "mute": "VOLUME_MUTE", "vol_up": "VOLUME_UP", "vol_down": "VOLUME_DOWN",
        "ch_up": "CHANNEL_UP", "ch_down": "CHANNEL_DOWN", "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "digit": "{d}"}},
    "roku": {"label": "Roku", "domain": "remote", "service": "send_command", "field": "command", "keys": {
        "up": "up", "down": "down", "left": "left", "right": "right", "enter": "select", "back": "back",
        "home": "home", "info": "info", "source": "input_tuner", "power": "power", "mute": "volume_mute",
        "vol_up": "volume_up", "vol_down": "volume_down", "ch_up": "channel_up", "ch_down": "channel_down",
        "play": "play", "pause": "play"}},
    "appletv": {"label": "Apple TV", "domain": "remote", "service": "send_command", "field": "command", "keys": {
        "up": "up", "down": "down", "left": "left", "right": "right", "enter": "select", "back": "menu",
        "home": "home", "menu": "top_menu", "vol_up": "volume_up", "vol_down": "volume_down",
        "ch_up": "channel_up", "ch_down": "channel_down", "play": "play", "pause": "pause"}},
}
DEFAULT_PRESET = "samsung"
DEFAULT_NAMES = "living room tv, tv, television"

# entities "turn on the …" may reach, and what turning them on means
DOMAINS = {"light": "homeassistant", "switch": "homeassistant", "fan": "homeassistant", "input_boolean": "homeassistant",
           "media_player": "homeassistant", "humidifier": "homeassistant", "scene": "scene", "script": "script",
           "cover": "cover"}

FILLERS = ("can you", "could you", "would you", "will you", "i want you to", "i'd like you to", "i would like you to",
           "go ahead and", "please", "hey", "okay", "ok", "just", "now", "for me")
ARTICLES = {"the", "a", "an", "my", "our", "this", "that"}
VERBS = {"turn", "switch", "power", "put", "flip", "set", "make", "activate", "run", "start", "toggle", "shut"}
NOISE = ARTICLES | VERBS | {"on", "off", "up", "down", "it", "tv", "and", "back", "again", "at", "in", "of"}

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

QUESTIONS = {"what", "why", "when", "where", "who", "how", "is", "are", "was", "were", "do", "does", "did",
             "should", "would", "could", "will", "if", "not", "never", "dont", "don", "isn", "aren", "which"}

KEY_WORDS = {"home": "home", "back": "back", "return": "back", "exit": "exit", "menu": "menu", "guide": "guide",
             "info": "info", "enter": "enter", "ok": "enter", "okay": "enter", "select": "enter", "up": "up",
             "down": "down", "left": "left", "right": "right", "source": "source", "input": "source"}

MAX_STEPS = 10


# -- words -----------------------------------------------------------------------------

def digits(tokens):
    """Number words become digits, one token per spoken number: "fifty four
    fifty two" → ["54", "52"], "five thousand four hundred fifty two" →
    ["5452"], "five four five two" → ["5", "4", "5", "2"]. Everything else
    passes through."""
    out, total, partial, last = [], 0, 0, None

    def close():
        nonlocal total, partial, last
        if last is not None:
            out.append(str(total + partial))
        total, partial, last = 0, 0, None

    for tok in tokens:
        if tok in UNITS:
            u = UNITS[tok]
            if last == "unit" or (last == "tens" and u >= 10):
                close()
            partial += u
            last = "unit"
        elif tok in TENS:
            if last in ("unit", "tens"):
                close()
            partial += TENS[tok]
            last = "tens"
        elif tok == "hundred" and last is not None:
            partial = (partial or 1) * 100
            last = "scale"
        elif tok == "thousand" and last is not None:
            total += (partial or 1) * 1000
            partial = 0
            last = "scale"
        elif tok == "and" and last == "scale":
            continue
        else:
            close()
            out.append(tok)
    close()
    return out


def words(text):
    """Lower-case tokens with the politeness and articles gone and the
    numbers as digits."""
    low = " " + re.sub(r"[^a-z0-9%. ]+", " ", (text or "").lower()) + " "
    low = re.sub(r"(?<=\d)\.(?=\d)", " point ", low)          # 5.1 → 5 point 1, so it survives the split
    for f in FILLERS:
        low = low.replace(f" {f} ", " ")
    toks = [t.strip(".") for t in low.split()]
    toks = [t for t in toks if t and t not in ARTICLES]
    return digits(toks)


def _singular(tok):
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def _norm(name):
    return " ".join(_singular(t) for t in re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split())


# -- the intents -----------------------------------------------------------------------

def parse(text, tv_names=(), others=True):
    """The words as a home command — a (kind, ...) tuple — or None when
    they are not one. tv_names are the things you call the TV."""
    toks = words(text)
    if not toks:
        return None
    joined = " " + " ".join(toks) + " "
    tv = False
    for name in sorted((_norm(n) for n in tv_names if n.strip()), key=len, reverse=True):
        if f" {name} " in joined:
            joined = joined.replace(f" {name} ", " tv ")
            tv = True
    toks = joined.split()
    if toks.count("tv") > 1:                              # "tv" said twice still means the one TV
        toks = [t for t in toks if t != "tv"] + ["tv"]
    s = set(toks)
    if s & {"music", "song", "songs", "playlist", "speaker", "yourself", "your"}:
        return None                                       # that's the Pi's own player, not the TV
    if s & QUESTIONS:                                     # "what's on tv tonight" is a question, not an order
        if toks[0] in ("is", "are") and s & {"on", "off"} and len(toks) <= 6:
            name = [t for t in toks[1:] if t not in ("on", "off", "tv", "still", "already", "now", "turned")]
            return ("query", "tv" if tv else " ".join(name)) if (tv or name) else None
        return None
    numbers = [t for t in toks if t.isdigit()]

    # mute
    if "unmute" in s or ("mute" in s and "off" in s) or (s & {"sound", "volume"} and "on" in s and not tv):
        return ("tv_mute", False)
    if s & {"mute", "silence", "muted"} or (s & {"sound", "volume"} and "off" in s):
        return ("tv_mute", True)

    # channels: "channel 5452", "go to channel 5 4 5 2", "channel up", "next channel"
    if s & {"channel", "channels"}:
        if s & {"up", "next", "forward"}:
            return ("tv_channel_step", "up")
        if s & {"down", "previous", "last", "back", "prev"}:
            return ("tv_channel_step", "down")
        i = toks.index("channel") if "channel" in toks else toks.index("channels")
        num = ""
        for t in toks[i + 1:]:
            if t.isdigit():
                num += t
            elif t in ("point", "dot", "dash") and num:
                num += "." if t != "dash" else "-"
            elif t == "to" and not num:
                continue
            else:
                break
        if not num:
            num = "".join(numbers)                        # "5452 on the tv channel": the digits are the channel
        return ("tv_channel", num.rstrip(".-")) if num else None

    # volume: "volume up", "turn it down 3", "louder", "volume to 30", "set volume 30"
    loud = {"louder", "raise", "increase", "higher"} & s
    quiet = {"quieter", "softer", "lower", "decrease", "reduce"} & s
    if "volume" in s or "sound" in s or loud or quiet or ("it" in s and s & {"up", "down"} and len(s) <= 4):
        steps = min(MAX_STEPS, int(numbers[0])) if numbers and not s & {"to", "set", "at"} else 1
        if "up" in s or loud:
            return ("tv_volume", "up", max(1, steps))
        if "down" in s or quiet:
            return ("tv_volume", "down", max(1, steps))
        if numbers and ("volume" in s or "sound" in s):
            return ("tv_volume_set", max(0, min(100, int(numbers[0]))))
        if s & {"max", "maximum", "full"}:
            return ("tv_volume_set", 100)
        return None

    # what's playing (only when the TV is named: "pause" alone may mean the Pi's music)
    if tv and s & {"pause", "play", "resume", "stop", "unpause"}:
        if "pause" in s:
            return ("tv_media", "pause")
        if "stop" in s:
            return ("tv_media", "stop")
        return ("tv_media", "play")

    # keys: "press home", "go back on the tv", "tv menu"
    if "press" in s or "push" in s:
        for t in toks:
            if t in KEY_WORDS:
                return ("tv_key", KEY_WORDS[t])
            if t.isdigit() and len(t) == 1:               # "press 5": the digit key, as on the remote
                return ("tv_key", t)
        return None
    if tv:
        if toks[:2] == ["go", "back"]:
            return ("tv_key", "back")
        rest = [t for t in toks if t not in NOISE and t not in ("go", "show", "open", "screen", "to")]
        if len(rest) == 1 and rest[0] in KEY_WORDS and rest[0] not in ("up", "down", "left", "right"):
            return ("tv_key", KEY_WORDS[rest[0]])

    # power: "turn on the tv", "tv off", "lamp on", "toggle the fan", "activate movie night"
    want = "on" if "on" in s else "off" if "off" in s else "toggle" if s & {"toggle", "flip"} else None
    if want is None and s & {"activate", "run", "wake"}:
        want = "on"
    ordered = toks[0] in VERBS or toks[0] in ("open", "go", "launch", "play", "wake") or toks[-1] in ("on", "off")
    if want is not None and ordered:
        rest = [t for t in toks if t not in NOISE and t not in ("toggle", "flip", "wake", "activate", "run")]
        if tv and not rest:
            return ("tv_power", want)
        if tv and want == "on":                           # "open netflix on the tv", "put youtube on"
            rest = [t for t in rest if t not in ("open", "go", "switch", "to", "launch", "play")]
            return ("tv_source", " ".join(rest)) if rest else ("tv_power", want)
        if tv:
            return None
        if rest == ["it"] or not rest:
            return ("device", "it", want)
        if others:
            return ("device", " ".join(rest), want)
        return None

    # a source or app on the TV: "open netflix", "switch to hdmi 1", "go to youtube"
    if toks[0] in ("open", "launch", "start", "show") or s & {"source", "input", "hdmi"} or \
            (len(toks) > 1 and toks[0] in ("switch", "go", "change") and toks[1] == "to"):
        rest = [t for t in toks if t not in NOISE and t not in ("open", "launch", "start", "show", "go", "switch",
                                                                   "change", "to", "source", "input", "app")]
        if "source" in s or "input" in s:
            rest = rest or ["source"]
        return ("tv_source", " ".join(rest)) if rest else None
    return None


# -- Home Assistant --------------------------------------------------------------------

class Home:
    def __init__(self):
        self.conf = {}
        self._states = ([], 0.0)
        self._lock = threading.Lock()
        self.error = None
        self.info = {}                    # version, location name — from the last check
        self.last = None                  # {"text", "reply", "when"} of the last command
        self._last_target = None          # what "it" means: "tv" or an entity id

    def configure(self, conf):
        self.conf = dict(conf or {})
        self._states = ([], 0.0)
        self.error = None

    def enabled(self):
        return bool(self.conf.get("enabled"))

    def ready(self, conf=None):
        conf = conf or self.conf
        return bool((conf.get("url") or "").strip() and (conf.get("token") or "").strip())

    # -- http ----------------------------------------------------------------------------

    def _base(self, conf):
        url = (conf.get("url") or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        if url.endswith("/api"):
            url = url[:-4]
        return url

    def _headers(self, conf):
        return {"Authorization": f"Bearer {(conf.get('token') or '').strip()}", "Content-Type": "application/json"}

    def _get(self, path, conf=None, wait=6):
        conf = conf or self.conf
        r = requests.get(self._base(conf) + "/api" + path, headers=self._headers(conf), timeout=wait)
        self._raise(r)
        return r.json()

    def _post(self, path, data, conf=None, wait=10):
        conf = conf or self.conf
        r = requests.post(self._base(conf) + "/api" + path, json=data, headers=self._headers(conf), timeout=wait)
        self._raise(r)
        try:
            return r.json()
        except ValueError:
            return None

    @staticmethod
    def _raise(r):
        if r.ok:
            return
        why = ""
        try:
            why = (r.json() or {}).get("message") or ""
        except ValueError:
            why = (r.text or "")[:120]
        if r.status_code == 401:
            why = "the token was refused"
        elif r.status_code == 404 and not why:
            why = "not found — is the address right?"
        raise RuntimeError(why or f"Home Assistant said {r.status_code}")

    def call(self, domain, service, data, conf=None):
        """A service call. (True, "") or (False, why)."""
        try:
            self._post(f"/services/{domain}/{service}", data, conf)
            self.error = None
            return True, ""
        except requests.ConnectionError:
            self.error = "can't reach Home Assistant"
        except requests.Timeout:
            self.error = "Home Assistant didn't answer in time"
        except Exception as e:
            self.error = str(e)[:140]
        log.info("home: %s.%s %s failed: %s", domain, service, data, self.error)
        return False, self.error

    # -- what it knows ---------------------------------------------------------------------

    def check(self, conf=None):
        """Reach it, and list the TVs and remotes it has."""
        conf = conf or self.conf
        out = {"ok": False, "error": None, "version": None, "name": None, "players": [], "remotes": []}
        if not self.ready(conf):
            out["error"] = "an address and a token are needed"
            return out
        try:
            info = self._get("/config", conf, wait=4)
            out["version"], out["name"] = info.get("version"), info.get("location_name")
            states = self._get("/states", conf, wait=6)
        except requests.ConnectionError:
            out["error"] = "can't reach it at that address"
            return out
        except requests.Timeout:
            out["error"] = "it didn't answer in time"
            return out
        except Exception as e:
            out["error"] = str(e)[:140]
            return out
        if conf is self.conf or conf == self.conf:
            self._states = (states, time.time())
            self.info = {"version": out["version"], "name": out["name"]}
            self.error = None
        out["ok"] = True
        out["players"] = _listed(states, "media_player")
        out["remotes"] = _listed(states, "remote")
        return out

    def states(self, force=False):
        """Everything Home Assistant has, cached for a minute."""
        items, when = self._states
        if not force and time.time() - when < (60 if items else 15):
            return items                      # fresh — or it failed a moment ago, and a status poll shouldn't wait on it again
        if not self.ready():
            return items
        try:
            items = self._get("/states")
            self._states = (items, time.time())
            self.error = None
        except Exception as e:
            self._states = (items, time.time())
            self.error = str(e)[:140]
            log.info("home: states: %s", self.error)
        return self._states[0]

    def state_of(self, entity_id):
        try:
            return self._get(f"/states/{entity_id}")
        except Exception as e:
            self.error = str(e)[:140]
            return None

    def tv_state(self, max_age=60):
        """The TV's state word ("on", "off", …), asked at most once a minute
        so a page polling it doesn't keep Home Assistant busy."""
        if not (self.enabled() and self.ready() and self._tv()):
            return None
        cached = getattr(self, "_tv_cache", None)
        if cached and time.time() - cached[1] < max_age:
            return cached[0]
        st = self.state_of(self._tv())
        word = _state_word(st) if st else None
        self._tv_cache = (word, time.time())
        return word

    def named(self):
        """(name, entity_id, domain) for everything with an on/off, the TV included."""
        out = []
        for st in self.states():
            eid = st.get("entity_id", "")
            domain = eid.split(".")[0]
            if domain not in DOMAINS:
                continue
            name = (st.get("attributes") or {}).get("friendly_name") or eid.split(".", 1)[-1].replace("_", " ")
            out.append((name, eid, domain))
        return out

    def find(self, spoken):
        """The entity whose name is what was said, or a list of candidates
        when several fit, or [] for none."""
        want = _norm(spoken)
        if not want:
            return []
        want_toks = set(want.split())
        exact, subset, superset = [], [], []
        for name, eid, domain in self.named():
            n = _norm(name)
            toks = set(n.split())
            if n == want:
                exact.append((name, eid, domain))
            elif toks and toks <= want_toks:
                subset.append((name, eid, domain))
            elif want_toks <= toks:
                superset.append((name, eid, domain))
        if exact:
            return exact[:1]
        if subset:
            subset.sort(key=lambda x: -len(_norm(x[0])))
            best = len(_norm(subset[0][0]))
            return [x for x in subset if len(_norm(x[0])) == best]
        superset.sort(key=lambda x: len(_norm(x[0])))
        if superset:
            best = len(_norm(superset[0][0]))
            return [x for x in superset if len(_norm(x[0])) == best]
        return []

    # -- the tv ------------------------------------------------------------------------------

    def _tv(self):
        return (self.conf.get("tv") or "").strip()

    def _remote(self):
        return (self.conf.get("remote") or "").strip()

    def _preset(self):
        return PRESETS.get(self.conf.get("preset") or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET])

    def tv_names(self):
        raw = self.conf.get("names") or DEFAULT_NAMES
        names = [n.strip() for n in str(raw).split(",") if n.strip()]
        return names or DEFAULT_NAMES.split(", ")

    def _need_tv(self):
        if not self._tv():
            return "No TV is picked — Settings → Home."
        return None

    def key(self, name, times=1, gap=0.4):
        """A remote button by our name for it — "home", "up", "enter", or a
        digit. (okay, why)."""
        p = self._preset()
        code = p["keys"]["digit"].format(d=name) if name.isdigit() and p["keys"].get("digit") else p["keys"].get(name)
        if not code:
            return False, f"the {p['label']} remote has no {name} button"
        if not self._remote():
            return False, "no remote entity is picked — Settings → Home"
        for i in range(times):
            okay, why = self.call(p["domain"], p["service"], {"entity_id": self._remote(), p["field"]: code})
            if not okay:
                return False, why
            if i < times - 1:
                time.sleep(gap)
        return True, ""

    def tv_power(self, want):
        service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[want]
        okay, why = self.call("media_player", service, {"entity_id": self._tv()})
        if not okay and self._remote() and want != "on":
            okay, why = self.key("power")
        return okay, why

    def tv_volume(self, direction, steps=1):
        service = "volume_up" if direction == "up" else "volume_down"
        for i in range(max(1, steps)):
            okay, why = self.call("media_player", service, {"entity_id": self._tv()})
            if not okay:
                okay, why = self.key("vol_up" if direction == "up" else "vol_down", times=steps - i)
                return okay, why
            if i < steps - 1:
                time.sleep(0.25)
        return True, ""

    def tv_volume_set(self, percent):
        return self.call("media_player", "volume_set", {"entity_id": self._tv(), "volume_level": round(percent / 100, 2)})

    def tv_mute(self, flag):
        """Mute (True), unmute (False), or flip it (None) the way the button
        on a remote does."""
        if flag is None:
            if self._remote() and self._preset()["keys"].get("mute"):
                return self.key("mute")
            st = self.state_of(self._tv())
            flag = not ((st or {}).get("attributes") or {}).get("is_volume_muted", False)
        okay, why = self.call("media_player", "volume_mute", {"entity_id": self._tv(), "is_volume_muted": bool(flag)})
        if not okay and self._remote():
            okay, why = self.key("mute")
        return okay, why

    def tv_channel(self, number):
        okay, why = (False, "no channel")
        if number.replace(".", "").replace("-", "").isdigit():
            okay, why = self.call("media_player", "play_media",
                                  {"entity_id": self._tv(), "media_content_type": "channel", "media_content_id": number})
        if not okay and self._remote() and number.isdigit() and self._preset()["keys"].get("digit"):
            for d in number:                              # the digits, one key at a time, then enter
                okay, why = self._digit(d)
                if not okay:
                    return okay, why
                time.sleep(0.4)
            okay, why = self.key("enter")
        return okay, why

    def _digit(self, d):
        p = self._preset()
        code = p["keys"]["digit"].format(d=d)
        return self.call(p["domain"], p["service"], {"entity_id": self._remote(), p["field"]: code})

    def tv_channel_step(self, direction):
        if self._remote() and self._preset()["keys"].get("ch_up"):
            return self.key("ch_up" if direction == "up" else "ch_down")
        return self.call("media_player", "media_next_track" if direction == "up" else "media_previous_track",
                         {"entity_id": self._tv()})

    def tv_media(self, action):
        return self.call("media_player", {"play": "media_play", "pause": "media_pause", "stop": "media_stop"}[action],
                         {"entity_id": self._tv()})

    def tv_sources(self):
        for st in self.states():
            if st.get("entity_id") == self._tv():
                return list((st.get("attributes") or {}).get("source_list") or [])
        return []

    def tv_source(self, spoken):
        want = _norm(spoken).replace(" ", "")
        if want == "source":
            return self.key("source")
        sources = self.tv_sources()
        if not sources:
            st = self.state_of(self._tv())
            sources = list(((st or {}).get("attributes") or {}).get("source_list") or [])
        hit = None
        for s in sources:
            n = _norm(s).replace(" ", "")
            if n == want:
                hit = s
                break
        if hit is None:
            for s in sorted(sources, key=len):
                n = _norm(s).replace(" ", "")
                if n and (want in n or n in want):
                    hit = s
                    break
        if hit is None:
            if sources:
                return False, f"the TV has no {spoken}. It has: " + ", ".join(sources[:8])
            return False, f"the TV doesn't list its sources, so I can't find {spoken}"
        okay, why = self.call("media_player", "select_source", {"entity_id": self._tv(), "source": hit})
        return (True, hit) if okay else (False, why)

    # -- other things ----------------------------------------------------------------------------

    def device(self, spoken, want):
        if spoken == "it":
            if self._last_target == "tv":
                return self._do(("tv_power", want))
            if not self._last_target:
                return "Turn what?"
            hits = [x for x in self.named() if x[1] == self._last_target]
        else:
            hits = self.find(spoken)
        if not hits:
            if self._tv() and want == "on" and spoken != "it":
                okay, why = self.tv_source(spoken)       # "turn on netflix"
                if okay:
                    self._last_target = "tv"
                    return f"{why} on the TV."
            return f"I don't know anything called {spoken}."
        if len(hits) > 1:
            return "Which one — " + " or ".join(h[0] for h in hits[:4]) + "?"
        name, eid, domain = hits[0]
        via = DOMAINS[domain]
        if domain == "cover":
            service = "open_cover" if want == "on" else "close_cover" if want == "off" else "toggle"
        elif domain in ("scene", "script"):
            if want == "off" and domain == "scene":
                return f"A scene only turns on — {name} is one."
            service = "turn_on" if want != "off" else "turn_off"
        else:
            service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[want]
        okay, why = self.call(via, service, {"entity_id": eid})
        if not okay:
            return f"Couldn't — {why}."
        self._last_target = eid
        did = {"on": "on", "off": "off", "toggle": "toggled"}[want]
        if domain == "cover":
            did = {"on": "opening", "off": "closing", "toggle": "toggled"}[want]
        elif domain == "scene":
            did = "set"
        elif domain == "script":
            did = "running" if want != "off" else "stopped"
        return f"{name} {did}."

    def query(self, what):
        if what == "tv":
            if not self._tv():
                return self._need_tv()
            st = self.state_of(self._tv())
            return f"The TV is {_state_word(st)}." if st else f"Couldn't ask — {self.error}."
        hits = self.find(what)
        if not hits:
            return None
        if len(hits) > 1:
            return "Which one — " + " or ".join(h[0] for h in hits[:4]) + "?"
        st = self.state_of(hits[0][1])
        return f"{hits[0][0]} is {_state_word(st)}." if st else f"Couldn't ask — {self.error}."

    # -- the whole thing -------------------------------------------------------------------------

    def handle(self, text):
        """Words in, what to say out — or None if they weren't a home command."""
        if not self.enabled():
            return None
        names, others = self.tv_names(), bool(self.conf.get("others", True))
        intents = []
        parts = [p.strip() for p in re.split(r"\b(?:and then|then|and)\b", text or "") if p.strip()]
        if len(parts) > 1:                                # "turn off the tv and the lights", "… then …"
            prev = None
            for part in parts:
                intent = parse(part, names, others)
                if intent is None and prev and prev[0] in ("tv_power", "device"):
                    intent = parse(f"turn {prev[-1]} {part}", names, others)
                if intent is None:
                    intents = []                          # one part we don't follow: try it as one sentence
                    break
                intents.append(intent)
                prev = intent
        if not intents:
            whole = parse(text, names, others)
            if whole is None:
                return None
            intents = [whole]
        if not self.ready():
            return "Home Assistant isn't set up — Settings → Home."
        replies = []
        for intent in intents:
            reply = self._do(intent)
            if reply is None:
                return None
            replies.append(reply)
        reply = " ".join(replies)
        self.last = {"text": text, "reply": reply, "when": time.time()}
        log.info("home: %r → %s", text, reply)
        return reply

    def act(self, intent, label=None):
        """One intent straight in — a button rather than words. What to say."""
        if not self.enabled():
            return "Home Assistant is switched off — Settings → Home."
        if not self.ready():
            return "Home Assistant isn't set up — Settings → Home."
        reply = self._do(tuple(intent)) or "Couldn't — that isn't something I know how to do."
        self.last = {"text": label or ":".join(str(x) for x in intent), "reply": reply, "when": time.time()}
        log.info("home: %s → %s", self.last["text"], reply)
        return reply

    def _do(self, intent):
        kind = intent[0]
        if kind == "device":
            return self.device(intent[1], intent[2])
        if kind == "query":
            return self.query(intent[1])
        if not self._tv():
            return self._need_tv()
        if kind == "tv_power":
            okay, why = self.tv_power(intent[1])
            said = {"on": "TV on.", "off": "TV off.", "toggle": "TV toggled."}[intent[1]]
        elif kind == "tv_volume":
            okay, why = self.tv_volume(intent[1], intent[2])
            said = f"Volume {intent[1]}" + (f" {intent[2]}." if intent[2] > 1 else ".")
        elif kind == "tv_volume_set":
            okay, why = self.tv_volume_set(intent[1])
            said = f"Volume {intent[1]}%."
        elif kind == "tv_mute":
            okay, why = self.tv_mute(intent[1])
            said = "Muted." if intent[1] else "Unmuted." if intent[1] is False else "Mute."
        elif kind == "tv_channel":
            okay, why = self.tv_channel(intent[1])
            said = f"Channel {intent[1]}."
        elif kind == "tv_channel_step":
            okay, why = self.tv_channel_step(intent[1])
            said = f"Channel {intent[1]}."
        elif kind == "tv_media":
            okay, why = self.tv_media(intent[1])
            said = {"play": "Playing.", "pause": "Paused.", "stop": "Stopped."}[intent[1]]
        elif kind == "tv_key":
            okay, why = self.key(intent[1])
            said = {"enter": "OK.", "back": "Back."}.get(intent[1]) or \
                (f"Pressed {intent[1]}." if intent[1].isdigit() else f"{intent[1].capitalize()}.")
        elif kind == "tv_source":
            okay, why = self.tv_source(intent[1])
            said = f"{why}." if okay else ""
        else:
            return None
        if okay:
            self._last_target = "tv"
            return said
        return f"Couldn't — {why}."

    def status(self):
        players = _listed(self._states[0], "media_player") if self._states[0] else []
        remotes = _listed(self._states[0], "remote") if self._states[0] else []
        return {"enabled": self.enabled(), "configured": self.ready(), "error": self.error, "info": self.info,
                "tv": self._tv(), "remote": self._remote(), "preset": self.conf.get("preset") or DEFAULT_PRESET,
                "names": self.tv_names(), "players": players, "remotes": remotes, "sources": self.tv_sources(),
                "last": self.last, "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]}


def _listed(states, domain):
    out = []
    for st in states or []:
        eid = st.get("entity_id", "")
        if eid.startswith(domain + "."):
            out.append({"id": eid, "name": (st.get("attributes") or {}).get("friendly_name") or eid,
                        "state": st.get("state")})
    out.sort(key=lambda x: x["name"].lower())
    return out


def _state_word(st):
    s = (st or {}).get("state") or "unknown"
    return {"playing": "on and playing", "paused": "on, paused", "idle": "on", "standby": "off",
            "unavailable": "not reachable"}.get(s, s)


HOME = Home()
