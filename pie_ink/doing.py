"""What it is doing right now, in a sentence.

Everything the app gets up to on its own can say so here: Snake on the little
screen, the e-ink's screen, a tune on the speaker, the camera watching the
room, the agent, the little friend, the miners it keeps an eye on, the
microphone. The bot gets the line among its facts, so a roundabout
question still gets a true answer — and asked straight out ("what are you doing?",
"whatcha up to?") it answers from here, no model needed.
"""
import logging
import re
import time

log = logging.getLogger(__name__)

# "what are you doing", "what r u up to", "whatcha doin", "what are you working on", "what have you been up to"
_ASKS = [re.compile(p, re.I) for p in (
    r"\bwhat(?:'s|s| is| are| r| have| do)?\s*(?:cha|ya|you|u)\s*(?:been\s+)?(?:doin|up to|busy with|playin|working on)",
    r"\bwyd\b",
    r"\bare (?:you|u) (?:busy|bored|playing)\b",
    r"\b(?:you|u) busy\b",
    r"\bwhat game\b",
    r"\bkeeping (?:you|u) busy\b",
)]
# a question about plans, not about now — the bot answers those
_NOT_NOW = re.compile(r"\b(?:tomorrow|tonight|later|next|weekend|this evening|for (?:dinner|lunch|christmas|the holidays))\b", re.I)

LCD_LABELS = {"system": "the system numbers", "clock": "the clock", "weather": "the weather", "crypto": "the markets",
              "camera": "the camera", "paint": "your painting", "drawing": "your drawing",
              "bot": "my own screen", "chat": "the group chat", "mirror": "a copy of the e-ink",
              "testcard": "the test card", "buddy": "my little friend's face", "snake": "Snake"}
EINK = {"clock": "showing the clock", "weather": "showing the weather", "me": "showing your day",
        "earnings": "showing what today has earned", "crypto": "watching the markets", "music": "showing the music player", "message": "showing a message",
        "image": "showing your drawing", "camera": "showing the camera", "postcard": "showing a postcard",
        "groupchat": "showing the group chat", "system": "showing the system numbers",
        "buddy": "being your little friend", "off": "resting (the screen is off)"}

boredom = lambda: {}           # the app's record: "from" (set while Snake is up because nobody was about), "ended"


def bored():
    return boredom().get("from") is not None


def just_stopped():
    """Did a touch end a bored game of Snake a moment ago? Asking is a touch, so the
    question "what are you doing?" is usually what ended it."""
    return time.time() - (boredom().get("ended") or 0) < 20


def asked(text):
    """Is this "what are you doing?" in some form — about now, not about plans?"""
    t = " ".join((text or "").lower().replace("’", "'").split())
    if not t or _NOT_NOW.search(t):
        return False
    return any(p.search(t) for p in _ASKS)


def activities(config):
    """What it is up to, the most interesting first: [(kind, phrase)], each phrase
    following "I'm". Every source is optional — a part that isn't there says nothing."""
    out = []

    def add(kind, phrase):
        if phrase:
            out.append((kind, phrase))

    try:
        from . import snake
        st = snake.GAME.status()
        lconf = config.get("lcd", {})
        on_lcd = lconf.get("enabled") and (lconf.get("screen") == "snake" or bored())   # not a page preview, not just ended
        score, best = st.get("score", 0), st.get("best", 0)
        score = (f"score {score}" if score else "nothing eaten yet") + (f", best {best}" if best else "")
        if st.get("playing") and on_lcd:
            if st.get("error"):
                add("snake", f"trying to play Snake on the little screen, but {st['error']}")
            else:
                add("snake", f"playing Snake on the little screen{' to pass the time' if bored() else ''} ({score})")
        elif just_stopped() and st.get("score") is not None:
            add("snake-was", f"just back from a game of Snake on the little screen ({score}) — it passes the time when nobody's about")
    except Exception:
        log.debug("doing: no snake", exc_info=True)
    try:
        from . import social
        if social.talking():
            add("social", "in the middle of a chat with another PiE-ink")
    except Exception:
        pass
    try:
        from . import music
        st = music.PLAYER.status()
        if st["state"] == "playing" and st["title"]:
            add("music", f"playing {st['title']} on the speaker")
    except Exception:
        pass
    add("eink", _eink(config))
    try:
        from . import watcher
        if watcher.WATCHER.running():
            add("watch", "keeping an eye on the room through the camera")
    except Exception:
        pass
    try:
        from . import buddy
        st = buddy.BUDDY.status()
        if st.get("enabled") and st.get("running") and st.get("state") not in ("off", "sleeping", None):
            add("buddy", "looking around the room as your little friend")
    except Exception:
        pass
    try:
        from . import agent
        st = agent.AGENT.status()
        if st.get("enabled") and st.get("running"):
            add("agent", f"checking on things every {st.get('every_minutes', 10)} minutes as the agent")
    except Exception:
        pass
    try:
        from . import mining
        for m in mining.enabled():
            d = m.snapshot()
            name = m.title.title()
            if d and d.get("hashrate", 0) > 0:
                n = d.get("workers", 0)
                add("mining", f"keeping an eye on your {name} mining — {d['hashrate_text']} across {n} worker{'s' if n != 1 else ''}")
            else:
                add("mining", f"keeping an eye on your {name} mining (nothing mining right now)")
    except Exception:
        pass
    try:
        from . import listen
        st = listen.EARS.status()
        if st.get("running"):
            add("ears", f"listening for \"{st['wake']}\"" if st.get("wake") else "listening")
    except Exception:
        pass
    return out


def _eink(config):
    """The screens: what the e-ink shows (with the detail that matters — the book,
    the coin), and the little screen when it shows something other than Snake."""
    from . import service as _service
    svc = _service._service
    if svc is None:
        return ""
    mode = svc.mode_name
    phrase = EINK.get(mode, f"showing {mode}" if mode else "")
    try:
        if mode == "reader":
            from .modes import reader
            st = reader.STATE.status()
            title = (st.get("book") or {}).get("title")
            phrase = f"reading {title}" if title else "showing a book"
            if title and st.get("total"):
                phrase += f" (page {st.get('page', 0) + 1} of {st['total']})"
        elif mode == "crypto":
            m = svc._mode
            item = m._current_item(m._items())
            phrase = f"watching {(item.get('symbol') or item.get('name') or 'the coin').upper()}" if item.get("symbol") else "watching the markets"
    except Exception:
        log.debug("doing: no detail for %s", mode, exc_info=True)
    if config.get("cycle", {}).get("enabled") and mode != "off":
        now = phrase[len("showing "):] if phrase.startswith("showing ") else phrase
        phrase = f"cycling through the e-ink's screens ({now} just now)"
    elif phrase and mode != "off":
        phrase += " on the e-ink"
    try:
        from . import lcd
        st = lcd.SECOND.status()
        if st.get("running") and st.get("screen") != "snake":
            label = LCD_LABELS.get(st.get("screen"), st.get("screen"))
            phrase = f"{phrase}, with {label} on the little screen" if phrase else f"showing {label} on the little screen"
    except Exception:
        pass
    return phrase


def facts(config):
    """One line for the bot's prompt (empty when there is nothing to say)."""
    acts = [p for _, p in activities(config)]
    return "What you are doing right now: " + "; ".join(acts) if acts else ""


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def sentence(config):
    """The answer to "what are you doing?" — the first things in one sentence, the rest in
    another. A bored game of Snake that the question itself just ended gets a sentence of its own."""
    acts = activities(config)
    if not acts:
        return "Not much — waiting for you to say something."
    lead = ""
    if acts[0][0] == "snake-was":
        lead = f"I'm {acts[0][1]}. "
        acts = acts[1:]
        if not acts:
            return lead.strip()
    if len(acts) == 1 and acts[0][0] == "eink":
        return f"{lead}{'Otherwise not' if lead else 'Not'} a lot — just {acts[0][1]}."
    main = [p for _, p in acts[:2]]
    rest = [p for _, p in acts[2:6]]
    text = f"{lead}{'Now' if lead else 'Right now'} I'm {_join(main)}."
    if rest:
        text += f" I'm also {_join(rest)}."
    return text
