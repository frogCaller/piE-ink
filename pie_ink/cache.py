"""Tiny on-disk JSON cache with TTL and failure backoff, shared by the data
providers. A failed fetch is not retried until its backoff expires (2 min,
then 4, 8 … up to an hour), so a rate-limited API is left alone."""
import json
import logging
import os
import time

from .settings import DATA_DIR

log = logging.getLogger(__name__)
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

BACKOFF_BASE = 120
BACKOFF_MAX = 3600


LENDER = None          # (key, ttl, what's kept) -> (what another Pi has, its age) or None: asked before a fetch
                       # (pie_ink/borrow.py: a Pietato's)


def _path(key):
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
    return os.path.join(CACHE_DIR, f"{safe}.json")


def read(key):
    """Return (data, age_seconds) or (None, None)."""
    path = _path(key)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as f:
            entry = json.load(f)
        return entry.get("data"), time.time() - entry.get("ts", 0)
    except (json.JSONDecodeError, OSError):
        return None, None


def write(key, data, age=0.0):
    """Keep it — fetched just now, or `age` seconds ago (something another Pi fetched)."""
    try:
        with open(_path(key), "w") as f:
            json.dump({"ts": time.time() - max(0.0, age or 0.0), "data": data}, f)
    except OSError as e:
        log.warning("cache write %s failed: %s", key, e)


def keys(prefix):
    """The keys cached under a prefix ("hist_bitcoin_" → ["hist_bitcoin_1", "hist_bitcoin_7"...]), not the
    failures'. Keys are as they're stored on disk (letters, digits and - _ . kept, anything else _)."""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in prefix)
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return []
    return sorted(n[:-5] for n in names if n.startswith(safe) and n.endswith(".json") and not n.endswith("__fail.json"))


def clear(key):
    try:
        os.remove(_path(key))
    except OSError:
        pass


def backing_off(key):
    fail, age = read(key + "__fail")
    return bool(fail) and age < fail.get("wait", 0)


def cached(key, ttl, fetch, cached_only=False):
    """Fresh cache -> return it. Stale -> what another Pi has, when it's fresh enough (LENDER) ->
    else fetch, store, return. Fetch failure -> back off and return the stale value. cached_only
    never fetches."""
    data, age = read(key)
    if data is not None and (cached_only or age < ttl):
        return data
    if cached_only:
        return data
    if LENDER is not None:
        try:
            lent = LENDER(key, ttl, data)
        except Exception:
            lent = None
        if lent is not None and lent[0] is not None:
            write(key, lent[0], lent[1])
            clear(key + "__fail")
            return lent[0]
    if backing_off(key):
        return data
    try:
        fresh = fetch()
    except Exception as e:
        fail, _ = read(key + "__fail")
        n = (fail or {}).get("n", 0) + 1
        wait = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (n - 1))
        write(key + "__fail", {"n": n, "wait": wait})
        log.warning("fetch %s failed (%s); next try in %d min", key, e, wait // 60)
        return data
    if fresh is not None:
        write(key, fresh)
        clear(key + "__fail")
        return fresh
    return data
