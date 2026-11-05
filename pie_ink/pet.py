"""The desk pet: it answers yes and no with its head.

Ask it something with a yes or a no in it — "are you happy?", "is it going
to rain?", "do you like cats?" — and, with a camera that turns, it nods for
yes and shakes its head for no (a tilt of the head when it can't say), the
way a small companion would. A decision model on the bot's Ollama makes the
call — tev1, and the little 0.8b one is plenty — from the same facts the bot
has, in a fraction of a second, before the words are even written; then the
bot's words are told what its head already said, so the two agree. With no
bot model set up at all, the head still answers.
"""
import logging
import re
import threading
import time

from . import llm

log = logging.getLogger(__name__)

AUX = (r"(?:are|is|am|do|does|did|can|could|will|would|should|shall|have|has|had|was|were|"
       r"isn't|aren't|don't|doesn't|didn't|can't|couldn't|won't|wouldn't|shouldn't|haven't|hasn't|wasn't|weren't|ain't)")
# "are you happy", "buddy, do you like me", "hey so is it raining", "ok will it rain tomorrow"
_LEAD = r"^(?:[^,?!.]{0,30}[,!]\s*)?(?:(?:hey|hi|hello|ok|okay|so|and|but|well|please|um|uh|now|right)[,!.]?\s+)*"
_YN = re.compile(_LEAD + AUX + r"\b", re.I)
_TAG = re.compile(r"\b(?:yes or no|right|correct|isn't it|is it|aren't you|are you|do you|don't you|can you|will you|would you|"
                  r"wasn't it|didn't you|doesn't it|does it|no)\s*\?\s*$", re.I)
_NOT = re.compile(r"^\s*(?:what|where|when|who|whom|whose|why|how|which)\b|"
                  r"\b(?:know|tell|say|remember|guess|explain|describe)\s+(?:me\s+)?(?:what|where|when|who|why|how|which)\b", re.I)

CRITERIA = {"yes": "The answer is yes — it nods.",
            "no": "The answer is no — it shakes its head.",
            "unsure": "It really can't be told from what it knows — it tilts its head."}
INSTRUCTIONS = ("Is the honest answer to the question yes or no? About its own feelings, likes and opinions, answer as the "
                "companion described: cheerful, curious, fond of the person it lives with. Pick 'unsure' only when the facts "
                "truly can't say.")
GESTURE = {"yes": "nod", "no": "shake", "unsure": "tilt"}
WORDS = {"yes": "Yes.", "no": "No.", "unsure": "I can't say."}       # when there's no bot to put it better
LINE = {"yes": "You have just answered this with a nod of your head (the camera nodded): the answer is YES. "
               "Your words must agree — a yes, and briefly why.",
        "no": "You have just answered this by shaking your head (the camera shook): the answer is NO. "
              "Your words must agree — a no, and briefly why.",
        "unsure": "You have just tilted your head (the camera): you can't say for sure. Say so in your words."}

LAST = {}                      # the last question answered this way, for the page
_lock = threading.Lock()
_said = {"error": ""}


def is_question(text):
    """Does this want a yes or a no? Questions that start with an auxiliary
    ("are you…", "does it…"), and ones that end on a tag ("…, right?")."""
    t = " ".join((text or "").replace("’", "'").split())
    if not t or _NOT.search(t):
        return False
    if _YN.search(t) or _TAG.search(t) or re.match(r"^yes or no\b", t, re.I):
        return True
    # "buddy are you happy" — its name first, then the auxiliary and a pronoun (or a question mark to be sure)
    if re.match(r"^(?:[\w'-]+\s+){1,3}" + AUX + r"\s+(?:you|u|it|i|we|they|there|this|that|he|she)\b", t, re.I):
        return True
    return t.endswith("?") and bool(re.match(r"^[\w'-]+\s+" + AUX + r"\b", t, re.I))


def model(conf):
    return ((conf or {}).get("model") or "tev1").strip()


def decide(config, question, facts, persona="", timeout=12.0):
    """The decision model's call on the question, from the facts the bot has:
    {"answer": yes|no|unsure, "probs", "confidence", "ms", "model"}. Raises
    with a line worth showing when it can't."""
    conf = config.get("pet", {})
    host = llm.decision_host(config.get("llm", {}))
    owner = (config.get("me", {}).get("name") or "").strip() or "the person it lives with"
    state = ((persona.strip() + "\n\n") if persona else "") + \
        "It is a small desk companion on a Raspberry Pi with a camera on a gimbal that can nod and shake its head.\n" \
        + ("What it knows right now:\n" + "\n".join(f"- {f}" for f in facts) + "\n" if facts else "") \
        + f"\n{owner} asks it: \"{question.strip()}\""
    name = llm.resolve_model(host, model(conf))        # "tev1" with only tev1:0.8b pulled: that one
    got = llm.choose(host, name, state, INSTRUCTIONS, CRITERIA, timeout=timeout)
    return {"answer": got["choice"], "probs": got["probs"], "confidence": got.get("confidence"), "ms": got["ms"],
            "model": name}


def answer(config, question, facts, persona=""):
    """A yes/no question, with a camera that turns and this switched on: the
    decision model decides, the head answers (in the background) and what it
    said comes back — (answer, the line for the bot's prompt). None when it
    isn't one, or it couldn't."""
    conf = config.get("pet", {})
    if not conf.get("enabled", True) or not is_question(question):
        return None
    from . import ptz
    if not ptz.PTZ.available():
        return None
    try:
        ptz.PTZ.eyes_open()           # the camera starts streaming now, while the model decides: a nod needs it
    except Exception:
        log.debug("pet: couldn't open its eyes", exc_info=True)
    entry = {"question": question.strip()[:120], "at": time.time()}
    try:
        got = decide(config, question, facts, persona)
    except Exception as e:
        why = str(e)[:140]
        if "Connection" in type(e).__name__ or "connect" in why.lower():
            why = f"can't reach the bot's Ollama at {llm.decision_host(config.get('llm', {})).split('//')[-1]}"
        with _lock:
            LAST.clear()
            LAST.update(entry, error=why)
            said = why == _said["error"]
            _said["error"] = why
        if not said:
            log.warning("pet: %s", why)
        return None
    kind = GESTURE[got["answer"]]
    with _lock:
        LAST.clear()
        LAST.update(entry, **got, gesture=kind, done=False)
        _said["error"] = ""
    threading.Thread(target=_move, args=(kind, tempo(conf)), daemon=True, name="pet-head").start()
    log.info("pet: '%s' -> %s (%.0f%%, %d ms): %s", entry["question"][:60], got["answer"],
             100 * got["probs"].get(got["answer"], 0), got["ms"], kind)
    return got["answer"], LINE[got["answer"]]


def tempo(conf):
    """Nods a second: quicker is keener, slower is more thoughtful."""
    try:
        return max(0.5, min(3.0, float((conf or {}).get("tempo", 1.6) or 1.6)))
    except (TypeError, ValueError):
        return 1.6


def _move(kind, hz=None):
    from . import ptz
    moved = False
    try:
        moved = ptz.PTZ.gesture(kind, tempo=hz)
    except Exception as e:
        log.info("pet: the head didn't move: %s", e)
    with _lock:
        if LAST.get("gesture") == kind:
            LAST["done"], LAST["moved"] = True, bool(moved)


def status():
    with _lock:
        last = dict(LAST)
    if "at" in last:
        last["ago"] = int(time.time() - last["at"])
    return last
