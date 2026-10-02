"""The running conversation with the bot.

Everything said either way goes in here — typed, spoken through the
microphone, and the remarks it makes on its own — so the chat on the page
shows the whole story rather than only what you typed. Kept as a small JSON
file, capped, so it survives a reboot without growing forever.
"""
import json
import logging
import os
import threading
import time

from .settings import DATA_DIR

log = logging.getLogger(__name__)
PATH = os.path.join(DATA_DIR, "chat.json")
KEEP = 80
_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(PATH) as fh:
                _cache = json.load(fh)[-KEEP:]
        except (OSError, ValueError):
            _cache = []
    return _cache


def history(limit=40):
    with _lock:
        return _load()[-limit:]


def add(role, text, how="typed"):
    """role: 'you' or 'bot'. how: typed | spoken | remark | screen."""
    text = (text or "").strip()
    if not text:
        return history()
    with _lock:
        items = _load()
        items.append({"role": role, "text": text, "how": how, "ts": time.time()})
        del items[:-KEEP]
        try:
            tmp = PATH + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(items, fh)
            os.replace(tmp, PATH)
        except OSError as e:
            log.debug("couldn't save the conversation: %s", e)
        return items[-40:]


def clear():
    global _cache
    with _lock:
        _cache = []
        try:
            os.remove(PATH)
        except OSError:
            pass
    return []
