"""When did someone last do something?

A tap on the page, a word to the microphone, a Telegram command, a key on the HAT:
each is a touch. The page polling on its own isn't. Long enough without one and the
bot is bored — and plays Snake on the little screen, if that's switched on."""
import threading
import time

_last = time.time()
_hooks = []
_lock = threading.Lock()


def touch():
    global _last
    _last = time.time()
    for fn in list(_hooks):
        try:
            fn()
        except Exception:
            pass


def idle_seconds():
    return time.time() - _last


def on_touch(fn):
    """Call fn on every touch (cheap and quick, please — it runs inside requests)."""
    with _lock:
        if fn not in _hooks:
            _hooks.append(fn)
