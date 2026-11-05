"""How you say you feel, and what the bot does about it.

    "I'm bored"            it suggests a game from a Pietato's Games (a random one; "another"
                           for a different one, "yes" starts it) — or putting the TV on
    "play a random game"   it picks one and starts it on the Pietato
    "it's dark in here"    the lights come on (Home Assistant): this room's (Settings → You →
                           where this Pi is), or the house's few; full brightness if they're on

parse() says which it is; answer() reads the reply to a suggestion still open. What's done
is the app's (it has the Pietato and Home Assistant to hand).
"""
import re

OFFER_FOR = 180          # seconds a suggestion stays open for a "yes"

_LEAD = r"^(?:(?:oh|ugh|man|hey|well|so|honestly|god|omg|meh|sigh)[ ,.!]+)*"
_BORED = re.compile(_LEAD + r"(?:i'?m|i am|im|i feel|i'?m feeling|feeling|getting|so|kinda|i'?m getting)?\s*"
                    r"(?:so |really |very |kinda |pretty |a bit |a little |super |soo+ |sooo+ )*bored(?: out of my mind| stiff)?\b"
                    r"|^(?:there'?s )?nothing to do\b|^what (?:should|can|could) i do\b|^entertain me\b|^i need (?:something to do|a distraction)\b"
                    r"|^(?:give me|suggest|recommend) something (?:fun|to do|to play)\b", re.I)
_RANDOM_GAME = re.compile(r"^(?:please\s+)?(?:play|start|launch|put on|fire up|boot up)\s+(?:me\s+)?(?:a|some|any|another)\s+"
                          r"(?:random\s+|retro\s+|old\s+|fun\s+)*(?:game|retro game)s?\b(?:\s+(?:on|from)\s+(?:the\s+|my\s+)?(?P<who>[\w.-]+))?\s*[.!?]*$", re.I)
_DARK = re.compile(r"\b(?:it'?s|it is|its|getting|got|so|too|kinda|pretty|really)\s+(?:so |really |very |a bit |a little |kinda |pretty |too |quite )*"
                   r"dark(?:\s+(?:in )?(?:here|in here|this room|now))?\b|\bi can'?t see (?:a thing|anything)\b|\bdark in here\b", re.I)
_OUTSIDE = re.compile(r"\b(?:outside|out there|already|yet|when|what time|sunset|is it|will it)\b", re.I)

_YES = re.compile(r"^(?:yes|yeah|yea|yep|yup|sure|ok|okay|alright|go on|go for it|do it|please|yes please|start it|play it|"
                  r"let'?s go|let'?s do it|let'?s play|sounds good|why not|that one|go|deal|perfect|great)\b", re.I)
_NO = re.compile(r"^(?:no|nah|nope|not now|maybe later|no thanks|not really|never mind|nevermind|forget it)\b", re.I)
_ANOTHER = re.compile(r"^(?:(?:no|nah)[, ]+)?(?:another(?: one)?|something else|a different one|next|pick another|try another|"
                      r"what else|anything else|other)\b", re.I)
_TV = re.compile(r"^(?:(?:no|nah)[, ]+)?(?:the )?(?:tv|telly|television)(?: (?:please|then|instead))?\s*[.!?]*$|"
                 r"^(?:(?:no|nah)[, ]+)?(?:put|turn|switch) (?:on )?the (?:tv|telly|television)(?: on)?(?: instead)?\s*[.!?]*$", re.I)


def parse(text):
    """{"kind": "bored"} / {"kind": "random_game", "who"} / {"kind": "dark"}, or None."""
    t = " ".join((text or "").split()).strip()
    if not t or len(t) > 120:
        return None
    m = _RANDOM_GAME.match(t)
    if m:
        return {"kind": "random_game", "who": (m.group("who") or "").strip().lower()}
    if _BORED.search(t) and not t.rstrip().endswith("?") or re.match(r"^(?:i'?m|im) bored\??$", t, re.I):
        return {"kind": "bored"}
    if _DARK.search(t) and not _OUTSIDE.search(t) and not re.search(r"\b(?:mode|theme|screen|page)\b", t, re.I):
        return {"kind": "dark"}
    return None


def answer(text):
    """The reply to a suggestion still open: "yes" | "no" | "another" | "tv", or None (it wasn't one)."""
    t = " ".join((text or "").split()).strip().rstrip(".!")
    if not t or len(t.split()) > 7:
        return None
    if _TV.match(t):
        return "tv"
    if _ANOTHER.match(t):
        return "another"
    if _NO.match(t):
        return "no"
    if _YES.match(t):
        return "yes"
    return None


def room_lights(lights, where):
    """The lights for "it's dark in here": [(name, entity_id), ...] whose name has this Pi's room in it,
    or all of them when there are only a few. ([], ask) when it can't tell which."""
    if not lights:
        return [], "there are no lights in Home Assistant"
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", (where or "").lower()).split() if w not in ("the", "my", "room")]
    if words:
        here = [l for l in lights if all(w in l[0].lower() for w in words)]
        if here:
            return here, ""
    if len(lights) <= 3:
        return list(lights), ""
    names = ", ".join(l[0] for l in lights[:4])
    return [], f"which lights — {names}…? Say 'turn on the …', or tell me which room I'm in (Settings → You → where this Pi is)"
