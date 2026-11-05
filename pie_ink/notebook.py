"""The bot's notebook: the things you've asked it to remember, read into every
answer it gives — on the page, out loud, from Telegram or from a Pietato — so the
one bot knows what you told it last week.

    remember that the bins go out on tuesday     noted
    remember my sister is called ana             noted
    what do you remember?                        the list
    forget the bins                              that one gone
    forget everything                            a fresh notebook

Kept in data/notes.json, on the Pi and nowhere else. A few dozen lines at most:
the notebook is for facts about you and the house, not a diary. With other PiE-inks
of yours on the network (pie_ink/household.py) the notebook is one: what was noted in
another room is read here too, and "forget" reaches every room.
"""
import json
import logging
import os
import re
import threading
import time

from .settings import DATA_DIR

log = logging.getLogger(__name__)

PATH = os.path.join(DATA_DIR, "notes.json")
MOST = 60                  # notes kept, at most (the oldest go first)
LONGEST = 200              # characters a note may run to
_lock = threading.Lock()

_LEAD = r"^(?:(?:please|can you|could you|would you|will you|hey|now|ok|okay|and|then|just|also)[ ,]+)*"
_SAVE = re.compile(_LEAD + r"(?:remember|note|take note|make a note|write down|keep in mind|bear in mind|don'?t forget|do not forget|memori[sz]e)"
                   r"(?:\s+(?:that|this|the fact that|the following|for me|please))*[:,]?\s+(.+)$", re.I)
_FORGET_ALL = re.compile(_LEAD + r"(?:forget|clear|wipe|erase|delete)\s+(?:everything|all|it all|all of it|all your notes|your notes|the notebook|everything you remember|all you remember)\s*$", re.I)
_FORGET = re.compile(_LEAD + r"(?:forget|don'?t remember|stop remembering|delete the note|remove the note|scrap the note)"
                     r"(?:\s+(?:that|about|the one about|the note about|what i said about|the))?\s+(.+?)\s*$", re.I)
_LIST = re.compile(_LEAD + r"(?:what (?:do|did) (?:you|u) (?:remember|know about me|have in your notebook|have noted)|what have i (?:told|asked) you(?: to remember)?"
                   r"|(?:show|read|list)(?: me)? (?:your|the|my) (?:notes|notebook|memory|memories)|your notes|notebook|what'?s in your notebook)\s*\??$", re.I)
_STOP = {"the", "a", "an", "my", "that", "about", "of", "is", "are", "to", "it", "on", "in", "and", "for", "i", "you", "me"}


def _load():
    try:
        with open(PATH) as f:
            items = json.load(f)
        return [n for n in items if isinstance(n, dict) and n.get("text")]
    except (OSError, ValueError):
        return []


def _save(items):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PATH + ".tmp", "w") as f:
            json.dump(items, f, indent=1)
        os.replace(PATH + ".tmp", PATH)
    except OSError as e:
        log.warning("could not save the notebook: %s", e)


def notes():
    with _lock:
        return _load()


def _tidy(text):
    text = " ".join((text or "").split()).strip(" .")
    text = re.sub(r"^(?:that|this)\s+", "", text, flags=re.I)
    return text[:LONGEST]


def add(text, how="typed"):
    """A note kept: the note, or None if there was nothing to keep (or it's one you already have)."""
    text = _tidy(text)
    if not text:
        return None
    with _lock:
        items = _load()
        for n in items:
            if n["text"].lower() == text.lower():
                return n
        note = {"id": f"{int(time.time() * 1000):x}", "text": text, "ts": time.time(), "how": how}
        items.append(note)
        del items[:-MOST]
        _save(items)
    return note


def _words(text):
    return {w for w in re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split() if w not in _STOP}


def matching(items, words):
    """The notes among `items` that are about those words."""
    want = _words(words)
    if not want:
        return []
    gone = [n for n in items if want <= _words(n["text"]) or (len(want) == 1 and any(w.startswith(next(iter(want))) for w in _words(n["text"])))]
    if not gone:                                       # looser: most of the words
        gone = [n for n in items if len(want & _words(n["text"])) >= max(1, len(want) - 1) and len(want) > 1]
    return gone


def forget(words):
    """The notes that are about those words, gone: the ones removed."""
    with _lock:
        items = _load()
        gone = matching(items, words)
        if gone:
            keep = [n for n in items if n not in gone]
            _save(keep)
    return gone


def elsewhere():
    """The notes kept in the other rooms of the house (other PiE-inks of yours), without the ones here too."""
    try:
        from . import household
        others = household.HOUSE.notes_elsewhere()
    except Exception:
        return []
    have = {n["text"].lower() for n in notes()}
    out = []
    for n in others:
        if n["text"].lower() not in have:
            have.add(n["text"].lower())
            out.append(n)
    return out


def remove(note_id):
    with _lock:
        items = _load()
        keep = [n for n in items if n.get("id") != note_id]
        if len(keep) != len(items):
            _save(keep)
            return True
    return False


def clear():
    with _lock:
        n = len(_load())
        _save([])
    return n


def words(owner="they"):
    """The notebook, for the bot's context — this room's notes and the other rooms'; '' when it's empty."""
    items = notes() + elsewhere()
    if not items:
        return ""
    return (f"Things {owner} asked you to remember (your notebook): " + " · ".join(n["text"] for n in items[-MOST:]))


def parse(text):
    """What was asked of the notebook, or None: {"kind": "add"|"forget"|"clear"|"list", "text"}."""
    low = " ".join((text or "").split()).strip()
    if not low:
        return None
    if _FORGET_ALL.match(low):
        return {"kind": "clear", "text": ""}
    if _LIST.match(low):
        return {"kind": "list", "text": ""}
    m = _SAVE.match(low)
    if m:                                                 # ("remember to call mum at 5" is a reminder: that parser goes first)
        return {"kind": "add", "text": m.group(1).strip()}
    m = _FORGET.match(low)
    if m and not re.search(r"\b(?:reminder|timer|alarm)s?\b", m.group(1), re.I):
        return {"kind": "forget", "text": m.group(1).strip()}
    return None


def do(intent, how="typed"):
    """Carry it out: what to say."""
    kind, text = intent["kind"], intent.get("text", "")
    if kind == "add":
        note = add(text, how)
        return f"Noted: {note['text']}." if note else "Noted — nothing, though. Say what to remember."
    if kind == "forget":
        gone = forget(text) + _elsewhere_forget(text)
        if not gone:
            return f"I don't have anything about '{text}' in my notebook."
        return "Forgotten: " + "; ".join(n["text"] for n in gone) + "."
    if kind == "clear":
        n = clear() + _elsewhere_clear()
        return f"Notebook cleared — {n} note{'s' if n != 1 else ''} gone." if n else "My notebook was empty already."
    items = notes() + elsewhere()
    if not items:
        return "Nothing in my notebook yet. Say 'remember that …' and I'll keep it."
    return f"I remember {len(items)} thing{'s' if len(items) != 1 else ''}:\n" + "\n".join(f"• {n['text']}" for n in items)


def _elsewhere_forget(text):
    try:
        from . import household
        return household.HOUSE.forget_elsewhere(text)
    except Exception:
        log.debug("forgetting in the other rooms", exc_info=True)
        return []


def _elsewhere_clear():
    try:
        from . import household
        return household.HOUSE.clear_notes_elsewhere()
    except Exception:
        log.debug("clearing the other rooms' notes", exc_info=True)
        return 0
