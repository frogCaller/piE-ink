"""The rhythm of the room.

The watcher says when something moves; this keeps a light record of it — one
count per hour per day — and turns that into the kind of thing a housemate
would notice: you're usually here by now, first time since Tuesday, three
hours at the desk. Nothing is stored beyond timestamps and counts.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from .settings import DATA_DIR

log = logging.getLogger(__name__)
PATH = os.path.join(DATA_DIR, "presence.json")
AWAY_AFTER = 20 * 60         # this long without movement and you've left
KEEP_DAYS = 60
_lock = threading.Lock()
_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(PATH) as fh:
                _data = json.load(fh)
        except (OSError, ValueError):
            _data = {"days": {}, "last_seen": 0.0, "here_since": 0.0}
        _data.setdefault("days", {})
        _data.setdefault("last_seen", 0.0)
        _data.setdefault("here_since", 0.0)
    return _data


def _save():
    try:
        tmp = PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(_data, fh)
        os.replace(tmp, PATH)
    except OSError as e:
        log.debug("couldn't save presence: %s", e)


def mark(now=None):
    """Something moved. Returns what's notable about that, if anything."""
    now = now or time.time()
    stamp = datetime.fromtimestamp(now)
    with _lock:
        d = _load()
        gone = now - d["last_seen"] if d["last_seen"] else None
        arrived = gone is None or gone > AWAY_AFTER
        if arrived:
            d["here_since"] = now
        d["last_seen"] = now
        day = d["days"].setdefault(stamp.strftime("%Y-%m-%d"), [0] * 24)
        day[stamp.hour] += 1
        # forget old days
        cutoff = (stamp - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        for key in [k for k in d["days"] if k < cutoff]:
            del d["days"][key]
        _save()
    return {"arrived": arrived, "gone_seconds": gone}


def here(now=None):
    now = now or time.time()
    d = _load()
    return bool(d["last_seen"]) and now - d["last_seen"] < AWAY_AFTER


def usual_arrival(weekday, now=None):
    """The hour you're usually first seen on this weekday, from past weeks."""
    d = _load()
    today = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
    hours = []
    for key, counts in d["days"].items():
        if key == today:
            continue
        try:
            if datetime.strptime(key, "%Y-%m-%d").weekday() != weekday:
                continue
        except ValueError:
            continue
        first = next((h for h, c in enumerate(counts) if c), None)
        if first is not None:
            hours.append(first)
    if len(hours) < 2:
        return None
    hours.sort()
    return hours[len(hours) // 2]


def last_seen_before(now=None):
    """The previous day you were around, before today."""
    d = _load()
    stamp = datetime.fromtimestamp(now or time.time())
    today = stamp.strftime("%Y-%m-%d")
    days = sorted(k for k, counts in d["days"].items() if k < today and any(counts))
    return days[-1] if days else None


def remarks(now=None):
    """Lines a housemate might say. Empty when nothing is notable."""
    now = now or time.time()
    stamp = datetime.fromtimestamp(now)
    d = _load()
    out = []
    if not d["last_seen"]:
        return out
    if here(now) and d["here_since"]:
        hours = (now - d["here_since"]) / 3600
        if hours >= 3:
            out.append(f"They have been at the desk for about {int(hours)} hours")
    usual = usual_arrival(stamp.weekday(), now)
    if usual is not None and here(now):
        if stamp.hour > usual + 1:
            out.append(f"They are usually here by {usual}:00; today it was later")
        elif stamp.hour < usual - 1:
            out.append(f"They are earlier than usual today (normally around {usual}:00)")
    prev = last_seen_before(now)
    if prev:
        days_gone = (stamp.date() - datetime.strptime(prev, "%Y-%m-%d").date()).days
        if days_gone >= 2 and (now - d["here_since"]) < 600:
            when = datetime.strptime(prev, "%Y-%m-%d").strftime("%A")
            out.append(f"First time they have been here since {when}")
    return out


def summary(now=None):
    """A short line for the prompt."""
    now = now or time.time()
    d = _load()
    if not d["last_seen"]:
        return ""
    if here(now):
        mins = int((now - d["here_since"]) / 60)
        return f"Someone is in the room (for {mins} minutes so far)"
    ago = int((now - d["last_seen"]) / 60)
    return f"The room has been empty for {ago} minutes" if ago < 600 else "The room has been empty for hours"
