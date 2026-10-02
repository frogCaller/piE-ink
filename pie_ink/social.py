"""Talking to the other PiE-inks.

Every Pi already announces itself on the network and runs the same web API,
so a message is just a POST to a friend's /api/inbox. Three kinds travel:

  text      — a note from a person to a person (or to everyone)
  postcard  — a drawing, shown on the other screen with who it came from
  ai        — a line for the other Pi's bot, which answers back

An "ai" conversation can bounce between two bots: each reply carries a hop
count, it stops at `max_hops`, and a person can pull the plug at any moment
with stop_ai() — which also tells the other side to stand down.

Friends found on the network are used automatically; Pis elsewhere can be
added by address in the settings.
"""
import json
import logging
import os
import threading
import time

import requests

from . import friends
from .settings import DATA_DIR

log = logging.getLogger(__name__)
LOG_PATH = os.path.join(DATA_DIR, "social.json")
KEEP = 200
_lock = threading.Lock()
_log = None
_state = {"ai_on": True, "threads": {},        # thread id -> hops so far
          "last_ai": 0.0, "last_msg": 0.0, "thinking": False}   # for the little indicator on the panel
TALKING_FOR = 120                               # a bot-to-bot exchange counts as live this long after a turn
_on_receive = None                              # the app hooks in here
_seen = {}                                      # message id -> when, so repeats are ignored


# -- who is out there ----------------------------------------------------------------

def everyone(config=None):
    """Friends on the network plus any added by address: [{host, url}].
    One entry per Pi, however many ways we happen to know it."""
    mine = friends.me()
    my_urls = {f"http://{mine['ip']}:{mine['port']}", f"http://127.0.0.1:{mine['port']}"}
    by_url = {}
    for p in friends.peers():
        by_url.setdefault(p["url"], {"host": p["host"], "url": p["url"], "ago": p.get("ago", 0)})
    for extra in (config or {}).get("social", {}).get("peers", []) or []:
        extra = (extra or "").strip()
        if not extra:
            continue
        url = extra if extra.startswith("http") else f"http://{extra}"
        if ":" not in url.split("//", 1)[1]:
            url += ":5000"
        host = url.split("//", 1)[1].split(":")[0]
        by_url.setdefault(url, {"host": host, "url": url, "ago": None})
    out = [p for url, p in by_url.items() if url not in my_urls]
    return sorted(out, key=lambda p: p["host"])


def my_name():
    return friends.me()["host"]


# -- the log ---------------------------------------------------------------------------

def _load():
    global _log
    if _log is None:
        try:
            with open(LOG_PATH) as fh:
                _log = json.load(fh)[-KEEP:]
        except (OSError, ValueError):
            _log = []
    return _log


def _save():
    try:
        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(_log[-KEEP:], fh)
        os.replace(tmp, LOG_PATH)
    except OSError as e:
        log.debug("couldn't save the group chat: %s", e)


def history(limit=60):
    with _lock:
        return [m for m in _load()[-limit:]]


def remember(msg):
    with _lock:
        items = _load()
        items.append(msg)
        del items[:-KEEP]
        _save()


def clear():
    global _log
    with _lock:
        _log = []
        _save()


# -- sending ---------------------------------------------------------------------------

def _message(kind, text, to, sender=None, thread=None, hops=0, image=None, from_ai=False, end=False):
    return {"id": f"{int(time.time() * 1000)}-{os.getpid()}", "kind": kind, "text": text or "",
            "from": sender or my_name(), "from_ai": from_ai, "to": to, "thread": thread or None,
            "hops": hops, "ts": time.time(), "image": image, "end": bool(end)}


def _post(peer, msg, timeout=8):
    try:
        r = requests.post(f"{peer['url']}/api/inbox", json=msg, timeout=timeout)
        r.raise_for_status()
        return True, ""
    except requests.exceptions.ConnectionError:
        return False, f"{peer['host']} isn't answering"
    except Exception as e:
        return False, f"{peer['host']}: {str(e)[:80]}"


def send(config, kind, text, to="all", image=None, thread=None, hops=0, from_ai=False, end=False):
    """Deliver to one friend or all of them. Returns (delivered, problems)."""
    targets = everyone(config)
    if to != "all":
        targets = [p for p in targets if p["host"] == to]
        if not targets:
            return 0, [f"no friend called {to}"]
    msg = _message(kind, text, to, thread=thread, hops=hops, image=image, from_ai=from_ai, end=end)
    if kind == "ai" and thread:
        _state["threads"][thread] = hops
        _state["last_ai"] = 0.0 if end else time.time()
    _state["last_msg"] = time.time()
    delivered, problems = 0, []
    for peer in targets:
        okay, why = _post(peer, msg)
        if okay:
            delivered += 1
        else:
            problems.append(why)
    shown = dict(msg)
    shown.pop("image", None)
    shown["delivered"] = delivered
    shown["mine"] = True
    remember(shown)
    return delivered, problems


# -- receiving -------------------------------------------------------------------------

def on_receive(fn):
    global _on_receive
    _on_receive = fn


def receive(msg):
    """A friend's Pi delivered this. Keep it, then let the app react."""
    msg = dict(msg)
    msg.setdefault("ts", time.time())
    msg["mine"] = False
    mid = msg.get("id")
    if mid:
        with _lock:                       # two copies can land at the same instant
            now = time.time()
            for old in [k for k, t in _seen.items() if now - t > 3600]:
                del _seen[old]
            if mid in _seen:
                return {"ok": True, "repeat": True}
            _seen[mid] = now
    if msg.get("kind") == "stop":
        stop_ai(tell_others=False)
        return {"ok": True}
    _state["last_msg"] = time.time()
    if msg.get("kind") == "ai":
        _state["last_ai"] = 0.0 if msg.get("end") else time.time()
    shown = dict(msg)
    shown.pop("image", None)
    remember(shown)
    if _on_receive:
        def handle():
            try:
                _on_receive(msg)
            except Exception:                    # an odd message is logged, not left half-handled in silence
                log.exception("handling a message from %s", msg.get("from"))
        threading.Thread(target=handle, daemon=True, name="inbox").start()
    return {"ok": True}


# -- the bots talking to each other ----------------------------------------------------

def ai_allowed(thread, hops, max_hops=0):
    """May we make reply number `hops` in this thread? The stop button is the
    normal way to end a conversation; a limit only applies if you set one."""
    if not _state["ai_on"]:
        return False, "the AIs have been told to stop"
    if max_hops and hops > max_hops:
        return False, f"that conversation reached {max_hops} turns"
    return True, ""


def note_hop(thread, hops):
    _state["threads"][thread] = max(_state["threads"].get(thread, 0), hops)


def thinking(on):
    """Our bot is working on its reply to the other one."""
    _state["thinking"] = bool(on)
    if on:
        _state["last_ai"] = time.time()


def talking():
    """Are the bots in the middle of a conversation right now?"""
    if not _state["ai_on"]:
        return False
    if _state["thinking"]:
        return True
    return bool(_state["last_ai"]) and time.time() - _state["last_ai"] < TALKING_FOR


def stop_ai(config=None, tell_others=True):
    """Pull the plug on bot-to-bot chatter, here and — if asked — on the other Pis too."""
    _state["ai_on"] = False
    _state["threads"].clear()
    _state["last_ai"] = 0.0
    _state["thinking"] = False
    if tell_others and config is not None:
        msg = _message("stop", "stop", "all")
        for peer in everyone(config):
            threading.Thread(target=_post, args=(peer, msg), daemon=True).start()
    return status()


def start_ai():
    _state["ai_on"] = True
    return status()


def status():
    return {"ai_on": _state["ai_on"], "threads": dict(_state["threads"]), "me": my_name(),
            "talking": talking(), "last_msg": _state["last_msg"]}
