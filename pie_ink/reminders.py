"""Reminders and timers, in your own words — typed, spoken, from Telegram or from a Pietato:

    remind me in 20 minutes to check the oven     set a timer for 10 minutes
    remind me to call mum at 5                     wake me at 7
    remind me tomorrow at 9 to take the bins out   remind me on friday to water the plants
    every day at 8 remind me to take the pills     remind me tonight at 8 that the match starts
    what reminders do I have    cancel the timer    cancel the reminder about the oven
    snooze / again in 10 minutes (after one has gone off)

When one is due, the panel shows it (a card, for a couple of minutes, then back to what
was showing), the speaker says it, your phone gets it (Telegram), and it's in the events
list and the conversation. The bot knows what's set, so "when's my next reminder?" works
too. Kept in data/reminders.json, so a restart doesn't lose them; one that was missed
while the Pi was off is said as soon as it's back (if it's less than a day late).

parse() turns the words into {"text", "at", "repeat"}; the Reminders thread watches the
clock; the app says what to do when one goes off (on_due). With other PiE-inks of yours
on the network (pie_ink/household.py) the list is one: a reminder set in another room is
known here, "cancel …" reaches every room, and each rings where it was set.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

from .settings import DATA_DIR

log = logging.getLogger(__name__)

PATH = os.path.join(DATA_DIR, "reminders.json")
MOST = 40                  # set at once, at most
LATE_OK = 86400            # seconds late a reminder may still go off (the Pi was off): a day
TICK = 2.0                 # seconds between looks at the clock
SHOW_FOR = 120             # seconds the card stays on the panel
_lock = threading.Lock()

WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5, "quarter": 0.25, "a couple of": 2, "couple of": 2}
_NUM = r"(?:\d+(?:[.,]\d+)?|a couple of|couple of|half an|half a|an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty-five|forty|fifty|sixty|ninety)"
_UNIT = r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|weeks?)"
_DURATION = re.compile(rf"\b(?:in|after|for)\s+({_NUM})\s*(?:and a half\s+)?{_UNIT}(?:\s+and a half)?(?:\s+(?:and\s+)?(\d+)\s*(minutes?|mins?|seconds?|secs?))?\b", re.I)
_TIMER = re.compile(rf"\b(?:set\s+)?(?:a\s+|an\s+)?(?:timer|alarm|countdown)\s+(?:for\s+|of\s+)?({_NUM})\s*{_UNIT}\b|\b({_NUM})[\s-]*(minutes?|mins?|hours?|hrs?|seconds?|secs?)\s+(?:timer|alarm|countdown)\b", re.I)
_CLOCK = re.compile(r"\b(?:at|by|@|for|around|about)\s+(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?\b(?!\s*(?:minutes?|mins?|hours?|hrs?|seconds?|secs?|days?|weeks?|m\b|h\b|s\b))", re.I)
_CLOCK_WORD = re.compile(r"\b(?:at\s+)?(noon|midday|midnight|lunchtime|dinner ?time|bedtime)\b", re.I)
_PAST = re.compile(r"\b(?:at\s+)?(half|quarter)\s+(past|to)\s+(\d{1,2})\b", re.I)
_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
         "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY = re.compile(r"\b(?:on\s+|next\s+|this\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.I)
_RELDAY = re.compile(r"\b(tomorrow|tonight|this evening|this afternoon|this morning|today|the day after tomorrow)\b", re.I)
_REPEAT = re.compile(r"\b(every day|everyday|daily|every morning|every evening|every night|each day|every weekday|weekdays)\b", re.I)
_LEAD = r"^(?:(?:please|can you|could you|would you|will you|hey|now|ok|okay|and|then|just|also)[ ,]+)*"
_ASK = re.compile(_LEAD + r"(?:remind me|remind us|set (?:me )?(?:a |an )?(?:reminder|alarm)|reminder|remember(?=\s+(?:me\s+)?to\b)|wake me(?: up)?|alarm"
                  r"|set (?:a |an )?timer|timer|countdown|ping me|tell me|let me know|nudge me|give me a (?:shout|nudge)"
                  r"|\d+[\s-]*(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)\s+(?:timer|alarm|countdown))\b", re.I)
_WAKE = re.compile(r"\b(?:wake me|alarm|get me up|morning)\b", re.I)
_LIST = re.compile(_LEAD + r"(?:what|which|any|list|show|read)(?: me)?(?: my| the| your)? (?:reminders?|timers?|alarms?)(?: (?:do i have|are (?:there|set)|have i (?:got|set)|is set|are on))?\s*\??$|^(?:my )?(?:reminders|timers)\s*\??$", re.I)
_CANCEL = re.compile(_LEAD + r"(?:cancel|stop|clear|delete|remove|forget|kill|drop|scrap)\s+(?:the |my |that |all |every )?(reminders?|timers?|alarms?)(?:\s+(?:about|for|to|that says|called)\s+(.+?))?\s*$", re.I)
_CANCEL_ALL = re.compile(_LEAD + r"(?:cancel|stop|clear|delete|remove)\s+(?:all|every)(?: of)?(?: my| the)? (?:reminders|timers|alarms)\s*$", re.I)
_SNOOZE = re.compile(_LEAD + r"(?:snooze|(?:remind me |tell me |ask me )?again|later|not now|in a bit)(?:\s+(?:in|for)\s+(" + _NUM + r")\s*(minutes?|mins?|hours?|hrs?))?\s*$", re.I)
_TRIM = re.compile(r"^(?:to|that|about|of|me|me to|me that|me about|us to|for)\s+", re.I)


def _number(text):
    t = (text or "").strip().lower()
    if t in WORD_NUMBERS:
        return float(WORD_NUMBERS[t])
    if t in ("half an", "half a"):
        return 0.5
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def _seconds(n, unit):
    u = (unit or "").lower()
    if u.startswith("s"):
        return n
    if u.startswith("m"):
        return n * 60
    if u.startswith("h"):
        return n * 3600
    if u.startswith("d"):
        return n * 86400
    if u.startswith("w"):
        return n * 7 * 86400
    return n * 60


def _next_clock(hour, minute, now, day_offset=None, weekday=None, pm=None):
    """The next time it's that o'clock: today if still ahead, else tomorrow; an hour with no am/pm
    (1–12) is whichever of its two readings comes first from now."""
    base = datetime.fromtimestamp(now)
    hours = [hour]
    if pm is True and hour < 12:
        hours = [hour + 12]
    elif pm is False and hour == 12:
        hours = [0]
    elif pm is None and 1 <= hour <= 12:
        hours = sorted({hour % 12, hour % 12 + 12})
    best = None
    for h in hours:
        if h > 23:
            continue
        t = base.replace(hour=h, minute=minute, second=0, microsecond=0)
        if weekday is not None:
            ahead = (weekday - t.weekday()) % 7
            t = t + timedelta(days=ahead)
            if t <= base:
                t += timedelta(days=7)
        elif day_offset:
            t = t + timedelta(days=day_offset)
        elif t <= base:
            t += timedelta(days=1)
        if best is None or t < best:
            best = t
    return best.timestamp() if best else None


def when(text, now=None, prefer_am=False):
    """The time in the words: (timestamp, repeat or None, the words with the time taken out), or
    (None, None, text) when there's no time in them. prefer_am: an hour with no am/pm is a
    morning one ("wake me at 7")."""
    now = now or time.time()
    low = " " + " ".join((text or "").split()) + " "
    repeat, pm_hint = None, (False if prefer_am else None)
    m = _REPEAT.search(low)
    if m:
        repeat = "daily"
        if "morning" in m.group(1).lower():
            pm_hint = False
        elif "evening" in m.group(1).lower() or "night" in m.group(1).lower():
            pm_hint = True
        low = low[:m.start()] + " " + low[m.end():]
    m = _TIMER.search(low)
    if m:
        n = _number(m.group(1) or m.group(3))
        unit = m.group(2) or m.group(4)
        if n is not None:
            return now + _seconds(n, unit), repeat, "timer"
    m = _DURATION.search(low)
    if m:
        n = _number(m.group(1))
        if n is not None:
            secs = _seconds(n, m.group(2))
            if "and a half" in m.group(0).lower():
                secs += _seconds(0.5, m.group(2))
            if m.group(3):
                secs += _seconds(float(m.group(3)), m.group(4))
            rest = (low[:m.start()] + " " + low[m.end():]).strip()
            return now + secs, repeat, rest
    day_offset, weekday = None, None
    m = _RELDAY.search(low)
    if m:
        word = m.group(1).lower()
        day_offset = 2 if "after" in word else 1 if word == "tomorrow" else 0
        if word in ("tonight", "this evening", "this afternoon"):
            pm_hint = True
        elif word == "this morning":
            pm_hint = False
        low = low[:m.start()] + " " + low[m.end():]
    m = _DAY.search(low)
    if m:
        weekday = _DAYS[m.group(1).lower()]
        low = low[:m.start()] + " " + low[m.end():]
    at = None
    m = _PAST.search(low)
    if m:
        h = int(m.group(3)) % 24
        mins = 30 if m.group(1).lower() == "half" else 15
        if m.group(2).lower() == "to":
            h, mins = (h - 1) % 24, 60 - mins
        at = _next_clock(h, mins, now, day_offset, weekday, pm_hint)
        low = low[:m.start()] + " " + low[m.end():]
    if at is None:
        m = _CLOCK_WORD.search(low)
        if m:
            h, mins = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0), "lunchtime": (12, 30), "dinner time": (18, 30),
                       "dinnertime": (18, 30), "bedtime": (22, 0)}[m.group(1).lower().replace("  ", " ")]
            at = _next_clock(h, mins, now, day_offset, weekday, True if h >= 12 else False)
            low = low[:m.start()] + " " + low[m.end():]
    if at is None:
        m = _CLOCK.search(low)
        if m:
            h, mins = int(m.group(1)), int(m.group(2) or 0)
            suffix = (m.group(3) or "").lower().replace(".", "")
            pm = True if suffix == "pm" else False if suffix == "am" else pm_hint
            if h > 24 or mins > 59:
                return None, None, text
            if h > 12:
                pm = None
            elif pm is None and 1 <= h <= 6:
                pm = True                                     # "at 2", "at 5": the afternoon, not the small hours
            at = _next_clock(h % 24, mins, now, day_offset, weekday, pm)
            low = low[:m.start()] + " " + low[m.end():]
    if at is None and (day_offset is not None or weekday is not None):
        hour = 9 if (day_offset or weekday is not None) else (20 if pm_hint else 9)   # "tomorrow": the morning; "tonight": 8
        if day_offset == 0 and pm_hint:
            hour = 20
        at = _next_clock(hour, 0, now, day_offset, weekday, True if hour >= 12 else False)
        if day_offset == 0 and at <= now:
            at = None
    if at is None:
        return None, None, text
    return at, repeat, low.strip()


def _tidy(rest, low):
    """The reminder's words: what's left once the time and "remind me to" are gone."""
    if rest.strip() == "timer":
        return "timer"
    text = " ".join(rest.split())
    text = re.sub(_LEAD, "", text, flags=re.I)
    text = re.sub(r"^(?:remind me|remind us|set (?:me )?(?:a |an )?(?:reminder|alarm)|reminder|remember|ping me|tell me|let me know|nudge me|give me a (?:shout|nudge))\s*", "", text, flags=re.I)
    text = _TRIM.sub("", text)
    text = _TRIM.sub("", text)
    text = text.strip(" ,.:;!-")
    if re.match(_LEAD + r"(?:wake me(?: up)?|alarm|set (?:a |an )?alarm)\b", low, re.I) or (not text and re.search(r"\bwake me\b", low, re.I)):
        return "wake up"
    return text


def parse(text, now=None):
    """What was asked, or None: {"kind": "set", "text", "at", "repeat"} · {"kind": "list"} ·
    {"kind": "cancel", "text"} · {"kind": "cancel_all"} · {"kind": "snooze", "seconds"}."""
    low = " ".join((text or "").split()).strip().rstrip("?!.")
    if not low:
        return None
    if _CANCEL_ALL.match(low):
        return {"kind": "cancel_all"}
    m = _CANCEL.match(low)
    if m:
        return {"kind": "cancel", "text": (m.group(2) or "").strip(), "noun": m.group(1).lower().rstrip("s")}
    if _LIST.match(low):
        return {"kind": "list"}
    m = _SNOOZE.match(low)
    if m:
        n = _number(m.group(1)) if m.group(1) else None
        return {"kind": "snooze", "seconds": _seconds(n, m.group(2)) if n is not None else 600}
    at, repeat, rest = when(low, now, prefer_am=bool(_WAKE.search(low)) and not re.search(r"\bpm\b", low, re.I))
    asked = _ASK.match(low) or _ASK.match(rest.strip())          # "in 2 hours remind me…": the time may come first
    if not asked:
        return None
    if at is None:
        if re.match(_LEAD + r"(?:set (?:a |an )?timer|timer|countdown)\b", low, re.I):
            return {"kind": "set", "text": "timer", "at": None, "repeat": None}      # a timer with no length: ask
        what = _tidy(low, low)
        return {"kind": "set", "text": what, "at": None, "repeat": None}            # no time given: ask when
    return {"kind": "set", "text": _tidy(rest, low), "at": at, "repeat": repeat}


# -- keeping them -------------------------------------------------------------------------

def _wanted(words):
    return {w for w in re.sub(r"[^a-z0-9' ]+", " ", (words or "").lower()).split() if w not in ("the", "a", "an", "my", "to", "about")}


def matches(r, words):
    """Is this reminder about those words?"""
    want = _wanted(words)
    return bool(want) and (bool(want & set(re.sub(r"[^a-z0-9' ]+", " ", r["text"].lower()).split()))
                           or (r["text"] == "timer" and "timer" in want))


def _elsewhere():
    """Reminders set in the other rooms of the house (other PiE-inks of yours), each with its room."""
    try:
        from . import household
        return household.HOUSE.reminders_elsewhere()
    except Exception:
        return []


def _cancel_elsewhere(words=""):
    try:
        from . import household
        return household.HOUSE.cancel_elsewhere(words)
    except Exception:
        log.debug("cancelling in the other rooms", exc_info=True)
        return []


class Reminders:
    def __init__(self):
        self.items = []              # [{"id", "text", "at", "repeat", "made", "how"}]
        self.last_fired = None       # the one that went off last: "snooze" brings it back
        self.on_due = None           # (reminder) -> None: the app says it, shows it, sends it
        self._stop = threading.Event()
        self._thread = None
        self._load()

    def _load(self):
        try:
            with open(PATH) as f:
                items = json.load(f)
            self.items = [r for r in items if isinstance(r, dict) and r.get("text") and isinstance(r.get("at"), (int, float))]
        except (OSError, ValueError):
            self.items = []

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(PATH + ".tmp", "w") as f:
                json.dump(self.items, f, indent=1)
            os.replace(PATH + ".tmp", PATH)
        except OSError as e:
            log.warning("could not save the reminders: %s", e)

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="reminders")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def add(self, text, at, repeat=None, how="typed"):
        with _lock:
            r = {"id": f"{int(time.time() * 1000):x}", "text": text or "reminder", "at": float(at), "repeat": repeat,
                 "made": time.time(), "how": how}
            self.items.append(r)
            self.items.sort(key=lambda x: x["at"])
            del self.items[MOST:]
            self._save()
        return r

    def remove(self, rid):
        with _lock:
            keep = [r for r in self.items if r["id"] != rid]
            gone = len(keep) != len(self.items)
            self.items = keep
            if gone:
                self._save()
        return gone

    def cancel(self, words=""):
        """The reminders about those words gone (all of them, with none): the ones removed."""
        with _lock:
            if not _wanted(words):
                gone, self.items = list(self.items), []
            else:
                gone = [r for r in self.items if matches(r, words)]
                self.items = [r for r in self.items if r not in gone]
            if gone:
                self._save()
        return gone

    def pending(self):
        with _lock:
            return sorted((dict(r) for r in self.items), key=lambda r: r["at"])

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("reminders")
            self._stop.wait(TICK)

    def _tick(self, now=None):
        now = now or time.time()
        due = []
        with _lock:
            for r in list(self.items):
                if r["at"] > now:
                    continue
                if now - r["at"] > LATE_OK and not r.get("repeat"):     # far too late (the Pi was off for days): dropped
                    self.items.remove(r)
                    continue
                due.append(dict(r))
                if r.get("repeat") == "daily":
                    while r["at"] <= now:
                        r["at"] += 86400
                else:
                    self.items.remove(r)
            if due:
                self._save()
        for r in due:
            r["late"] = int(now - r["at"])
            self.last_fired = r
            if self.on_due:
                try:
                    self.on_due(r)
                except Exception:
                    log.exception("a reminder going off")
        return due

    def snooze(self, seconds=600, how="typed"):
        """The last one that went off, again in a while: it, or None when none has."""
        r = self.last_fired
        if not r:
            return None
        return self.add(r["text"], time.time() + seconds, None, how)

    # -- words ------------------------------------------------------------------------------

    @staticmethod
    def when_words(at, now=None):
        """"in 20 min", "at 5:30 pm", "tomorrow 9:00", "Fri 9:00"."""
        now = now or time.time()
        d, n = datetime.fromtimestamp(at), datetime.fromtimestamp(now)
        gap = at - now
        if gap < 0:
            return "now"
        if gap < 59:
            return f"in {int(round(gap))} s"
        if gap < 5400:
            return f"in {int(round(gap / 60))} min"
        clock = d.strftime("%-I:%M %p").lower()
        if d.date() == n.date():
            return f"at {clock}"
        if d.date() == (n + timedelta(days=1)).date():
            return f"tomorrow at {clock}"
        if gap < 7 * 86400:
            return f"{d.strftime('%A')} at {clock}"
        return d.strftime("%-d %b at ") + clock

    def describe(self, r, now=None):
        text = r["text"]
        if text == "timer":
            mins = max(1, int(round((r["at"] - r.get("made", r["at"])) / 60)))
            text = f"{mins}-minute timer"
        return (f"{text} — {self.when_words(r['at'], now)}" + (" (every day)" if r.get("repeat") == "daily" else "")
                + (f" (in {r['label']})" if r.get("label") else ""))

    def everywhere(self):
        """What's set here and in the other rooms, soonest first."""
        return sorted(self.pending() + _elsewhere(), key=lambda r: r["at"])

    def words(self, owner="they"):
        """A line for the bot; '' when none is set."""
        items = self.everywhere()
        if not items:
            return ""
        return f"Reminders you've set for {owner}: " + "; ".join(self.describe(r) for r in items[:8])

    def due_words(self, r):
        """What to say when it goes off."""
        if r["text"] == "timer":
            mins = max(1, int(round((r["at"] - r.get("made", r["at"])) / 60)))
            return f"Time's up — your {mins}-minute timer."
        if r["text"] == "wake up":
            return "Time to get up!"
        late = r.get("late") or 0
        if late > 120:
            return f"Reminder (from {int(late // 60)} min ago — I was off): {r['text']}."
        return f"Reminder: {r['text']}."

    def do(self, intent, how="typed"):
        """Carry it out: what to say."""
        kind = intent["kind"]
        if kind == "set":
            if intent["at"] is None:
                if intent.get("text") == "timer":
                    return "A timer for how long? Say 'set a timer for 10 minutes'."
                return (f"When? Say 'remind me in 20 minutes to {intent['text']}' or 'at 5'." if intent.get("text")
                        else "Remind you of what, and when? Say 'remind me in 20 minutes to …'.")
            if intent["at"] < time.time() + 1:
                return "That's already gone — give me a time that's ahead."
            r = self.add(intent["text"] or "reminder", intent["at"], intent.get("repeat"), how)
            if r["text"] == "timer":
                return f"Timer set — {self.when_words(r['at'])}."
            if r["text"] == "wake up":
                return f"I'll wake you {self.when_words(r['at'])}."
            return f"I'll remind you {self.when_words(r['at'])}: {r['text']}." + (" Every day." if r.get("repeat") else "")
        if kind == "list":
            items = self.everywhere()
            if not items:
                return "No reminders set. Say 'remind me in 20 minutes to …' or 'set a timer for 5 minutes'."
            return f"{len(items)} set:\n" + "\n".join(f"• {self.describe(r)}" for r in items)
        if kind == "cancel_all":
            gone = self.cancel("") + _cancel_elsewhere("")
            return f"Cancelled {len(gone)} reminder{'s' if len(gone) != 1 else ''}." if gone else "There were none set."
        if kind == "cancel":
            words = intent.get("text", "")
            noun = intent.get("noun", "reminder")
            if words:
                gone = self.cancel(words) + _cancel_elsewhere(words)
            elif noun == "timer":
                gone = self.cancel("timer") + _cancel_elsewhere("timer")
            else:
                items = self.everywhere()
                if len(items) > 1:
                    return "Which one? " + "; ".join(self.describe(r) for r in items) + ". Say 'cancel the reminder about …'."
                gone = self.cancel("") + _cancel_elsewhere("")
            if not gone:
                return f"No {noun} about '{words}'." if words else f"There's no {noun} set."
            return "Cancelled: " + "; ".join(self.describe(r) for r in gone) + "."
        if kind == "snooze":
            r = self.snooze(intent.get("seconds") or 600, how)
            if not r:
                return "Nothing to snooze — no reminder has gone off."
            return f"Again {self.when_words(r['at'])}: {r['text']}."
        return None


REMINDERS = Reminders()
