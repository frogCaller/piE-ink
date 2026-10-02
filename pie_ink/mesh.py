"""The PiE-inks as a mesh: what each one has, and asking one for something.

Every Pi's beacon (friends.py) now carries what it has — a camera, a GPS,
which screen, a speaker, a microphone, a bot, Home Assistant, the agent — and
where it is, in your words ("workshop", "upstairs", set under Settings → You).
So you can say, type or Telegram:

    which pi has a camera            find the pi with a gps
    show me the camera from upstairs show the workshop camera
    send this drawing to the workshop pi
    say hello on the upstairs pi     show the clock on the workshop pi
    where is the workshop pi         list the pis

The parsing is here; what needs the panel or the drawing is done in the app,
which asks parse() what you meant and carries it out with the helpers below.
"""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)

CAP_WORDS = {"camera": "camera", "cam": "camera", "webcam": "camera", "eyes": "camera",
             "gps": "gps", "location": "gps", "receiver": "gps",
             "screen": "screen", "display": "screen", "panel": "screen", "e-ink": "screen", "eink": "screen",
             "lcd": "lcd", "second screen": "lcd", "colour screen": "lcd", "color screen": "lcd",
             "speaker": "speaker", "voice": "speaker", "sound": "speaker",
             "mic": "mic", "microphone": "mic", "ears": "mic",
             "bot": "llm", "brain": "llm", "llm": "llm", "model": "llm", "ollama": "llm",
             "home": "home", "home assistant": "home", "agent": "agent", "battery": "battery"}
CAP_LABELS = {"camera": "camera", "gps": "GPS", "screen": "screen", "lcd": "LCD", "speaker": "speaker", "mic": "mic",
              "llm": "bot", "home": "Home Assistant", "agent": "agent", "battery": "battery"}
PI_WORDS = r"(?:\s+(?:pi|pie|pie-ink|pi e-ink|pieink|raspberry pi))?"

_caps_cache = {"ts": 0.0, "caps": {}}


# -- what this Pi has --------------------------------------------------------------------------

def my_caps(config, fresh=False):
    """This Pi's abilities, for the beacon. Worked out at most once a minute
    — probing for a camera isn't free."""
    if not fresh and time.time() - _caps_cache["ts"] < 60:
        return dict(_caps_cache["caps"])
    caps = {"location": (config.get("me", {}).get("location") or "").strip()[:40]}
    try:
        from . import camera
        cams = [c for c in camera.available() if c["id"] != "mock"]
        caps["camera"] = bool(cams) or (camera.CAMERA.source_name not in (None, "", "test pattern (no camera found)")
                                        and camera.CAMERA.status().get("running", False))
    except Exception:
        caps["camera"] = False
    try:
        from .ptz import PTZ
        caps["turns"] = bool(caps.get("camera") and PTZ.available())
    except Exception:
        caps["turns"] = False
    try:
        from .gps import GPS
        g = GPS.status()
        caps["gps"] = bool(g.get("enabled") and (g.get("connected") or g.get("fix") != "none"))
    except Exception:
        caps["gps"] = False
    try:
        from . import audio
        a = config.get("audio", {})
        caps["speaker"] = bool(audio.have("aplay") and (audio.piper_ready(a)[0] or audio.have("espeak-ng")))
    except Exception:
        caps["speaker"] = False
    try:
        from . import listen
        caps["mic"] = bool(config.get("listen", {}).get("enabled")) and bool([d for d in listen.mic_devices() if d["id"]])
    except Exception:
        caps["mic"] = False
    lc = config.get("llm", {})
    caps["llm"] = bool(lc.get("enabled") and (lc.get("model") or "").strip())
    caps["home"] = bool(config.get("home", {}).get("enabled") and (config.get("home", {}).get("token") or "").strip())
    caps["agent"] = bool(config.get("agent", {}).get("enabled"))
    lcd = config.get("lcd", {})
    caps["lcd"] = str(lcd.get("model") or "") if lcd.get("enabled") else ""
    caps["battery"] = battery_percent()
    _caps_cache.update(ts=time.time(), caps=dict(caps))
    return caps


def battery_percent():
    """A battery's charge, when the Pi has one the kernel knows about (a
    PiSugar or a UPS HAT with a driver); None otherwise."""
    import glob
    for path in glob.glob("/sys/class/power_supply/*/capacity"):
        try:
            with open(path) as fh:
                return max(0, min(100, int(fh.read().strip())))
        except (OSError, ValueError):
            continue
    return None


# -- reading the others ------------------------------------------------------------------------

def describe(peer):
    """One Pi in a line: pie-shop (workshop): camera, GPS, 2.13" e-ink, speaker · 4 s ago."""
    caps = peer.get("caps") or {}
    bits = []
    if caps.get("camera"):
        bits.append("camera that turns" if caps.get("turns") else "camera")
    if caps.get("gps"):
        bits.append("GPS")
    if peer.get("panel"):
        bits.append(peer["panel"])
    if caps.get("lcd"):
        bits.append(f'LCD {caps["lcd"]}"')
    if caps.get("speaker"):
        bits.append("speaker")
    if caps.get("mic"):
        bits.append("mic")
    if caps.get("llm"):
        bits.append("bot")
    if caps.get("home"):
        bits.append("Home Assistant")
    if caps.get("agent"):
        bits.append("agent")
    if caps.get("battery") is not None:
        bits.append(f"battery {caps['battery']}%")
    where = caps.get("location") or ""
    name = f"{peer.get('host', '?')}" + (f" ({where})" if where else "")
    ago = peer.get("ago")
    tail = f" · {ago} s ago" if ago is not None else ""
    return f"{name}: {', '.join(bits) if bits else 'nothing it told me about'}{tail}"


def has(peer, cap):
    caps = peer.get("caps") or {}
    if cap == "screen":
        return bool(peer.get("panel"))
    if cap == "battery":
        return caps.get("battery") is not None
    return bool(caps.get(cap))


def find(peers, cap):
    return [p for p in peers if has(p, cap)]


def _words(text):
    return re.sub(r"[^a-z0-9' -]+", " ", (text or "").lower()).split()


def resolve(peers, name):
    """The Pi you meant by a word or two: its location ("upstairs"), its host
    name ("pie-shop"), or a bit of either. None if nothing matches."""
    want = " ".join(_words(name)).replace("the ", "").strip()
    want = re.sub(r"\b(pi|pie|pie-ink|pieink|raspberry)\b", "", want).strip()
    want = re.sub(r"'s?$", "", want).strip()
    if not want:
        return None
    for p in peers:
        loc = ((p.get("caps") or {}).get("location") or "").lower()
        if loc and loc == want:
            return p
    for p in peers:
        if (p.get("host") or "").lower() == want:
            return p
    for p in peers:
        loc = ((p.get("caps") or {}).get("location") or "").lower()
        host = (p.get("host") or "").lower()
        if (loc and (want in loc or loc in want)) or want in host or host in want or want.replace(" ", "-") in host:
            return p
    return None


def _pronoun(who):
    """"what can you see" is the bot's own camera, not another Pi's."""
    return " ".join(_words(who)).strip("' ") in ("you", "your", "it", "this", "here", "me", "my", "yourself", "the room", "room")


def _cap_of(words):
    for phrase, cap in sorted(CAP_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(phrase) + r"s?\b", words):
            return cap
    return None


def parse(text):
    """What you asked of the mesh, or None if it isn't a mesh thing:
    {"kind": "list" | "find" | "camera" | "drawing" | "say" | "screen" | "where", ...}."""
    low = " ".join(_words(text))
    if not low:
        return None
    if re.search(r"\b(list|which|what|show|who)\b.*\b(pis|pies|pie-inks|pi e-inks|pieinks|other pis|raspberry pis)\b", low) \
            or re.search(r"\b(who|what)('s| is| are)? (on|in) the (mesh|network)\b", low) or low in ("pis", "the pis", "list pis"):
        if not re.search(r"\b(with|has|have)\b", low):
            return {"kind": "list"}
    m = re.search(r"\b(?:find|which|what|who|is there)\b.*?\b(?:pi|pie|pie-ink|pieink|one|friend)s?\b.*?\b(?:with|has|have|that has)\b\s+(?:a |an |the )?([a-z -]+?)\s*(?:\?|$)", low)
    if m and _cap_of(m.group(1)):
        return {"kind": "find", "cap": _cap_of(m.group(1))}
    m = re.search(r"\b(?:who|which)\b.*\b(?:has|have)\b\s+(?:a |an |the )?([a-z -]+?)\s*(?:\?|$)", low)
    if m and _cap_of(m.group(1)) and not re.search(r"\b(camera from|camera on)\b", low):
        return {"kind": "find", "cap": _cap_of(m.group(1))}
    m = re.search(r"\b(?:show|see|look at|get|view|open)\b(?: me)?(?: the)?\s+(?:camera|cam|webcam|picture|photo|view)\s+(?:from|on|of|in|at)\s+(?:the )?(.+?)" + PI_WORDS + r"\s*$", low) \
        or re.search(r"\b(?:show|see|look at|get|view|open)\b(?: me)?(?: the)?\s+(.+?)(?:'s)?" + PI_WORDS + r"\s+(?:camera|cam|webcam)\s*$", low) \
        or re.search(r"\bwhat (?:does|can) (?:the )?(.+?)" + PI_WORDS + r" see\b", low) \
        or re.search(r"\blook through (?:the )?(.+?)(?:'s)?" + PI_WORDS + r"(?: camera| eyes)?\s*$", low)
    if m and not _pronoun(m.group(1)):
        return {"kind": "camera", "who": m.group(1).strip()}
    m = re.search(r"\b(?:send|post|give|push)\b(?: this| the| my)? (?:drawing|picture|sketch|postcard|canvas) to (?:the )?(.+?)" + PI_WORDS + r"\s*$", low)
    if m:
        return {"kind": "drawing", "who": m.group(1).strip()}
    m = re.search(r"^(?:say|announce|speak) (.+?) (?:on|through|via|at) (?:the )?(.+?)" + PI_WORDS + r"\s*$", low)
    if m:
        return {"kind": "say", "text": m.group(1).strip(), "who": m.group(2).strip()}
    m = re.search(r"^(?:tell|make|have|get) (?:the )?(.+?)" + PI_WORDS + r" (?:to )?say (.+)$", low)
    if m:
        return {"kind": "say", "text": m.group(2).strip(), "who": m.group(1).strip()}
    m = re.search(r"\b(?:show|put|switch to|display) (?:the )?([a-z ]+?) (?:screen )?on (?:the )?(.+?)" + PI_WORDS + r"\s*$", low)
    if m and m.group(1).strip() not in ("camera", "cam", "webcam", "picture", "photo", "view"):
        return {"kind": "screen", "screen": m.group(1).strip(), "who": m.group(2).strip()}
    m = re.search(r"\bwhere(?:'s| is| are)? (?:the )?(.+?)" + PI_WORDS + r"\s*\??$", low)
    if m and re.search(r"\b(pi|pie|pie-ink|pieink)\b", low):
        return {"kind": "where", "who": m.group(1).strip()}
    return None


# -- asking one of them ------------------------------------------------------------------------

def fetch_frame(peer, timeout=8):
    """A JPEG from another Pi's camera, or (None, why)."""
    try:
        r = requests.get(f"{peer['url']}/api/camera.jpg", timeout=timeout)
        if r.status_code == 503:
            try:
                return None, r.json().get("error") or "its camera isn't giving a picture"
            except ValueError:
                return None, "its camera isn't giving a picture"
        r.raise_for_status()
        if not r.content or not r.headers.get("Content-Type", "").startswith("image/"):
            return None, "it sent no picture"
        return r.content, ""
    except requests.exceptions.ConnectionError:
        return None, f"{peer.get('host')} isn't answering"
    except Exception as e:
        return None, str(e)[:100]


def say_on(peer, text, timeout=10):
    try:
        r = requests.post(f"{peer['url']}/api/audio/say", json={"text": text}, timeout=timeout)
        data = r.json() if r.content else {}
        return bool(data.get("ok")), data.get("error") or data.get("message") or ""
    except Exception as e:
        return False, str(e)[:100]


def show_on(peer, mode, timeout=8):
    try:
        r = requests.post(f"{peer['url']}/api/mode", json={"mode": mode}, timeout=timeout)
        data = r.json() if r.content else {}
        return bool(data.get("ok")), data.get("error") or ""
    except Exception as e:
        return False, str(e)[:100]


def where_words(peer):
    wh = peer.get("where") or {}
    if wh.get("lat") is None:
        return "hasn't said where it is"
    place = wh.get("place") or f"{wh['lat']:.4f}, {wh['lon']:.4f}"
    return f"at {place}" + (" (GPS)" if str(wh.get("source", "")).startswith("gps") else " (its weather spot)")
