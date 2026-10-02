"""What the creature knows, and how it gets asked to speak.

The prompt is assembled from things that are actually true right now — the
time, your schedule, the weather, the next event, how well it's been looked
after — so the model has something to react to rather than inventing a day.
"""
import logging
import time
from datetime import datetime

from . import llm, weather

log = logging.getLogger(__name__)

DEFAULT_PERSONA = (
    "You live on a small e-ink screen on {owner}'s desk. "
    "Reply with ONE short sentence, at most 14 words — it has to fit on a "
    "screen the size of a matchbox. Plain text. "
    "No emoji, no quotation marks, no markdown, no stage directions."
)

LOOKING = ("see", "look", "looking", "camera", "in front of you", "around you", "wearing",
           "holding", "room", "desk", "behind me", "what is this", "what's this", "colour", "color")

TOPICS = {
    "camera": "Say something about what you can see.",
    "weather": "Say something about the weather.",
    "coins": "Say something about how the coins are doing.",
    "time": "Say something about the time of day.",
    "day": "Say something about how {owner}'s day is going.",
    "music": "Say something about the music playing.",
    "anything": "Say one thing about right now.",
    "rhythm": "Say something about when {owner} tends to be around, from what you have noticed.",
    "mining": "Say something about how {owner}'s mining is doing right now — the change is the point.",
    "holdings": "Say something about how {owner}'s crypto holdings are doing — what they are worth and how that moved.",
    "holdings_rich": "{owner}'s crypto just passed the line they set for it — they are rich now. Celebrate with them, big.",
    "holdings_under": "{owner}'s crypto slipped back under the line they set for it. Say something about it, gently.",
    "holdings_crash": "The market fell hard today and {owner}'s crypto lost a lot. Say something about it — "
                      "commiserate, or talk them down from the ledge.",
    "holdings_rally": "The market jumped today and {owner}'s crypto is up a lot. Say something about it.",
    "holdings_slump": "{owner}'s crypto has sunk well below the most it was ever worth. Say something about it.",
}

ASKS = {
    "idle": "Say one thing about right now.",
    "feed": "{owner} just fed you. React to the meal.",
    "play": "{owner} just played with you. React.",
    "pet": "{owner} just petted you. React.",
    "morning": "Greet {owner} for the morning.",
    "night": "Say goodnight to {owner}.",
}


def _owner(config):
    return (config.get("me", {}).get("name") or "").strip() or "the human"


def system_prompt(config):
    persona = (config.get("llm", {}).get("persona") or "").strip() or DEFAULT_PERSONA
    return persona.format(owner=_owner(config))


def recent(items, limit=6):
    """The last few turns, so it follows the thread instead of starting fresh."""
    lines = []
    for m in (items or [])[-limit:]:
        text = " ".join((m.get("text") or "").split())
        if not text:
            continue
        lines.append(("They said: " if m.get("role") == "you" else "You said: ") + text[:140])
    return lines


def context(config, day=None):
    """The lines of fact that go into the prompt."""
    now = datetime.now()
    me = config.get("me", {})
    lines = [f"Time: {now.strftime('%A %-I:%M %p')}"]
    who = ", ".join(x for x in ((me.get("name") or "").strip(), (me.get("job_title") or "").strip()) if x)
    if who:
        lines.append(f"Who you live with: {who}")
    if day is not None:
        activity, _, ends = day.current()
        if activity:
            left = max(0, ends - day.minute) if ends is not None else 0
            lines.append(f"{_owner(config)} is {activity.lower()}, {int(left // 60)}h {int(left % 60):02d}m to go")
        elif not day.workday:
            lines.append(f"{_owner(config)} has the day off")
        else:
            lines.append(f"{_owner(config)} is free right now")
    try:
        from .gps import GPS, compass
        g = GPS.status()
        if g["enabled"] and g["fix"] != "none":
            mph = config.get("weather", {}).get("units", "F") == "F"
            speed = g.get("speed_kmh") or 0
            speed_text = (f", moving {speed * 0.621:.0f} mph" if mph else f", moving {speed:.0f} km/h") + \
                         (f" heading {compass(g.get('course'))}" if g.get("course") is not None else "") if speed >= 2 else ", not moving"
            where = f" — {g['place']}" if g.get("place") else ""
            lines.append(f"Location (from the GPS): {g['lat']:.4f}, {g['lon']:.4f}{where}{speed_text}"
                         + (f", altitude {g['alt_m']:.0f} m" if g.get("alt_m") is not None else ""))
        elif g["enabled"]:
            lines.append("The GPS receiver has no fix yet" if g["connected"] else "The GPS receiver is not plugged in")
    except Exception:
        log.debug("no gps for the prompt", exc_info=True)
    w = config.get("weather", {})
    if w.get("lat") is not None:
        try:
            wx = weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"), cached_only=True)
            if wx:
                units = wx["units"]
                lines.append(f"Weather now: {round(wx['temp'])}°{units}, {weather.describe(wx['code'])[0].lower()}"
                             f", feels {round(wx['feels'])}°, humidity {wx['humidity']}%, wind {round(wx['wind'])}")
                days = wx.get("days") or []
                if days:
                    today = days[0]
                    lines.append(f"Today: high {round(today['hi'])}°{units}, low {round(today['lo'])}°, "
                                 f"{today.get('rain', 0)}% chance of rain, "
                                 f"sunrise {today['sunrise']}, sunset {today['sunset']}")
                    ahead = [f"{datetime.fromisoformat(d['date']).strftime('%a')} "
                             f"{round(d['hi'])}/{round(d['lo'])}° {weather.describe(d['code'])[0].lower()}"
                             for d in days[1:4]]
                    if ahead:
                        lines.append("Next few days: " + "; ".join(ahead))
                if str(w.get("country", "")).upper() == "US":
                    worded = weather.nws_forecast(float(w["lat"]), float(w["lon"]), cached_only=True)
                    for period, text in (worded or [])[:2]:     # [(period, text), ...]
                        lines.append(f"{period}: {text[:180]}")
        except Exception:
            log.debug("no weather for the prompt", exc_info=True)

    try:
        from . import coingecko, watchlist
        items = watchlist.visible()
        if items:
            q = coingecko.coin_data(items[0]["id"], cached_only=True)
            if q and q.get("price") is not None:
                change = q.get("change", {}).get("24h")
                lines.append(f"{items[0]['symbol'].upper()}: ${q['price']:,.0f}"
                             + (f", {change:+.1f}% in a day" if change is not None else ""))
    except Exception:
        pass
    try:
        from . import music
        st = music.PLAYER.status()
        if st["state"] == "playing" and st["title"]:
            lines.append(f"Playing now: {st['title']}")
    except Exception:
        pass
    try:
        from . import presence
        line = presence.summary()
        if line:
            lines.append(line)
        for remark in presence.remarks()[:2]:
            lines.append("Worth mentioning: " + remark)
    except Exception:
        pass
    try:
        from . import mining
        lines.extend(mining.words())
    except Exception:
        pass
    try:
        from . import holdings
        line = holdings.HOLDINGS.words()
        if line:
            lines.append(line)
    except Exception:
        pass
    try:
        from . import sight
        seen = sight.summary(hours=24, limit=10)
        if seen:
            lines.append(f"The camera has seen today: {seen}")
    except Exception:
        pass
    try:
        from . import journey
        trip = journey.summary(units=config.get("weather", {}).get("units", "F"))
        if trip:
            lines.append(f"Where it has been: {trip}")
    except Exception:
        pass
    try:
        from . import agent
        st = agent.AGENT.status()
        if st["enabled"] and (st["last"] or {}).get("thought"):
            lines.append(f"The agent's last thought ({(st['last_ago'] or 0) // 60} min ago): {st['last']['thought']}")
    except Exception:
        pass
    return lines


def wants_eyes(text):
    """Does this sound like a question about what's in front of the camera?"""
    low = (text or "").lower()
    return any(w in low for w in LOOKING)


def as_base64(image, longest=640, quality=80):
    """A PIL image as base64 JPEG, small enough to send quickly."""
    import base64
    import io as _io
    image = image.convert("RGB")
    image.thumbnail((longest, longest))
    buf = _io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _look(config):
    """A still from the camera as base64. Returns (image, why-not)."""
    if not config.get("llm", {}).get("see", True):
        return None, "its camera is switched off in the bot panel"
    try:
        from . import camera
        camera.CAMERA.touch(20)
        frame = camera.CAMERA.frame()
        for _ in range(40):                     # a cold camera takes a few seconds
            if frame is not None:
                break
            time.sleep(0.25)
            frame = camera.CAMERA.frame()
        if frame is None:
            why = camera.CAMERA.status().get("error") or "the camera didn't produce a picture"
            log.info("camera: %s", why)
            return None, why
        return as_base64(frame), ""
    except Exception as e:
        log.info("camera trouble", exc_info=True)
        return None, str(e)[:120]


def ask(config, kind="idle", message=None, day=None, topic=None, history=None):
    """Kick off a line from the model. Returns False if it's busy or off."""
    conf = config.get("llm", {})
    if not conf.get("enabled") or not conf.get("model"):
        return False
    owner = _owner(config)
    lines = context(config, day)
    said = recent(history, 4)
    if said:
        lines.append("Lately: " + " / ".join(said))
    if message:
        lines.append(f"{owner} says to you: {message}")
        ask_line = "Reply to them."
    elif topic:
        ask_line = TOPICS.get(topic, TOPICS["anything"]).format(owner=owner)
    else:
        ask_line = ASKS.get(kind, ASKS["idle"]).format(owner=owner)
    images, _why = _look(config) if topic == "camera" else (None, "")
    if images:
        lines.append("You can see the attached picture of the room.")
    prompt = "\n".join(lines) + "\n\n" + ask_line
    return llm.say_async(conf, system_prompt(config), prompt, kind, images=[images] if images else None)


CONVERSE = (
    "You are the assistant living on {owner}'s PiE-ink. You are talking with the assistant on "
    "{friend}'s PiE-ink; {friend} and {owner} are friends. The two of you were asked to discuss: "
    "{topic}. Have a real conversation: respond to what they just said, and add something new "
    "every turn — a fact you know, an opinion, a related idea, or one question. Never repeat a "
    "line either of you has already said, and don't just agree. Two or three sentences, plain "
    "text, no emoji, no markdown. No greetings after the first turn."
)


def _words(text):
    import re as _re
    return set(_re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def too_similar(text, earlier, cut=0.75):
    """Is this line more or less something already said?"""
    mine = _words(text)
    if not mine:
        return True
    for old in earlier:
        theirs = _words(old)
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= cut:
            return True
    return False


def converse(config, friend, topic, transcript, timeout=None):
    """One turn in a conversation with another Pi's bot. `transcript` is a
    list of (speaker, text) so it can follow the thread. Returns (text, done):
    done is True when it has run out of new things to say."""
    conf = {**config.get("llm", {})}
    conf["temperature"] = max(float(conf.get("temperature", 0.8) or 0.8), 0.9)
    owner = _owner(config)
    system = CONVERSE.format(owner=owner, friend=friend, topic=topic or "whatever comes up")
    lines = context(config)
    lines.append("")
    lines.append("The conversation so far:")
    for who, said in transcript[-10:]:
        lines.append(f"  {who}: {said}")
    lines.append("")
    lines.append("Your turn. Say something that moves it along.")
    prompt = "\n".join(lines)
    said_by_me = [t for who, t in transcript if who == "You"]
    said_by_anyone = [t for _, t in transcript]

    text = llm.generate(conf, system, prompt, timeout=timeout, limit=llm.room(320))
    if text and too_similar(text, said_by_anyone):
        log.info("that line was much like an earlier one — asking again")
        text = llm.generate(conf, system, prompt + "\nThat was too close to something already said. "
                            "Bring in something genuinely new, or change the subject.",
                            timeout=timeout, limit=llm.room(320))
    if not text or too_similar(text, said_by_me):
        return "I think we've said what there is to say on that — good talking to you.", True
    return text, False


def describe(config, image_b64, what="drawing", timeout=None):
    """Say what's in a picture — used by the Draw screen's AI button."""
    conf = config.get("llm", {})
    system = ("You look at pictures and describe them plainly. One or two short sentences, "
              "no more than 25 words in total. No markdown, no preamble.")
    # a description is read on the page, so it gets more room than a panel line
    prompt = (f"This is a {what} made by {_owner(config)} on a small black-and-white screen. "
              "Say what it shows, warmly and briefly.")
    return llm.generate(conf, system, prompt, timeout=timeout, images=[image_b64], limit=llm.room(240))


def describe_view(config, image_b64, where, question=None, timeout=None):
    """What the camera sees after turning — and, if you asked something, the answer."""
    conf = config.get("llm", {})
    owner = _owner(config)
    system = ("You are the eyes of a small home monitor with a camera that turns. You look at a picture from it "
              "and say plainly what is there. No markdown, no preamble.")
    prompt = (f"You just turned the camera to look {where} in {owner}'s room. This is what it sees. "
              + (f"{owner} asked: {question}. Answer that first, from the picture, in one or two short sentences."
                 if question else
                 "In one or two short sentences, say what is there and whether anything looks unusual."))
    return llm.generate(conf, system, prompt, timeout=timeout, images=[image_b64], limit=llm.room(300))


def survey(config, image_b64, labels, question=None, timeout=None):
    """A look around: several labelled views side by side, and a judgement on them."""
    conf = config.get("llm", {})
    owner = _owner(config)
    system = ("You are the eyes of a small home monitor with a camera that turns. You look at pictures from it "
              "and say plainly what is there. No markdown, no preamble.")
    prompt = (f"This picture is {len(labels)} views side by side from a camera that just turned to look around "
              f"{owner}'s room — {', '.join(labels)}, each labelled at the top. "
              + (f"{owner} asked: {question}. Answer that first. Then " if question else "")
              + "in two or three short sentences say what is in each direction, then give your judgement: "
              "does anything look unusual, out of place, or worth telling them about? If not, say all looks normal.")
    return llm.generate(conf, system, prompt, timeout=timeout, images=[image_b64], limit=llm.room(420))


def reply(config, message, day=None, timeout=None, history=None):
    """A blocking reply, for the chat bar. `history` is what was said before,
    so a follow-up like "and tomorrow?" still makes sense."""
    conf = config.get("llm", {})
    owner = _owner(config)
    lines = context(config, day)
    said = recent(history)
    if said:
        lines.append("The conversation so far:")
        lines.extend("  " + x for x in said)
    lines.append(f"{owner} now says: {message}")
    images, why = (_look(config) if wants_eyes(message) else (None, ""))
    if images:
        lines.append("The attached picture is what your camera can see right now.")
    elif why:
        lines.append(f"You cannot see anything right now ({why}); say so if they asked.")
    prompt = "\n".join(lines) + "\n\nReply to them, carrying on from what was already said."
    text = llm.generate(conf, system_prompt(config), prompt, timeout=timeout,
                        images=[images] if images else None, limit=llm.room(240))   # you read this one
    if text:
        llm.set_speech(text, "chat")
    return text
