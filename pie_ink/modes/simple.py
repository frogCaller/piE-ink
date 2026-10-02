"""Clock, message (text / joke / fortune), drawing, and off."""
import io
import json
import logging
import os
import random
import shutil
import subprocess
import time
from datetime import datetime

from PIL import Image, ImageOps

from ..settings import ASSETS_DIR, DATA_DIR
from ..text import draw_block, font, line_height, text_width, wrap
from .base import Mode

log = logging.getLogger(__name__)


class ClockMode(Mode):
    name = "clock"
    label = "Clock"

    @property
    def interval(self):
        return 1.0 if self.settings.get("show_seconds") else 5.0

    def render(self):
        s = self.settings
        now = datetime.now()
        if s.get("format") == "24":
            fmt = "%H:%M:%S" if s.get("show_seconds") else "%H:%M"
        else:
            fmt = "%-I:%M:%S %p" if s.get("show_seconds") else "%-I:%M %p"
        text = now.strftime(fmt)

        f = self.frame()
        fnt = font(s.get("font", 1), s.get("size", 36))
        if s.get("show_date"):
            date_font = font(s.get("font", 1), max(10, int(s.get("size", 36)) // 3))
            total = line_height(fnt) + line_height(date_font)
            top = (f.height - total) // 2
            draw_block(f.draw, text, fnt, (0, top, f.width, top + line_height(fnt)),
                       f.fg, align="center", valign="top", pad=0)
            draw_block(f.draw, now.strftime("%A, %B %-d"), date_font,
                       (0, top + line_height(fnt), f.width, f.height),
                       f.fg, align="center", valign="top", pad=0)
        else:
            draw_block(f.draw, text, fnt, (0, 0, f.width, f.height),
                       f.fg, align="center", valign="center")
        return f.image


# what the bot's face does, read off the line it just said
BOT_MOODS = {
    "listening": ("(◉_◉ )", "( ◉_◉)"),
    "happy":   ("(◕‿◕ )", "( ◕‿◕)"),
    "pleased": ("(•‿‿•)", "(◕‿‿◕)"),
    "cool":    ("(⌐■_■)", "(⌐■_■)"),
    "curious": ("(☉_☉ )", "( ⚆_⚆)"),
    "sorry":   ("(╥_╥ )", "( ╥_╥)"),
    "worried": ("(°▃▃°)", "(°▃▃°)"),
    "sleepy":  ("(⇀‿‿↼)", "(≖‿‿≖)"),
    "thinking": ("(•_• )", "( •_•)"),
    "plain":   ("(•‿• )", "( •‿•)"),
    "surprised": ("(⊙o⊙ )", "( ⊙o⊙)"),
}
# lines that come with their own mood: the little friend's hellos and surprises
KIND_MOODS = {"buddy-happy": "happy", "buddy-surprised": "surprised"}
SAFE_FACE = ("(•‿• )", "( •‿•)")          # if a font can't draw the chosen one
GOOD = ("glad", "lovely", "great", "nice", "good", "happy", "yes", "sure", "thanks", "thank",
        "wonderful", "excellent", "perfect", "enjoy", "fun", "delight", "warm", "sunny", "well",
        "finally", "clear", "bright", "ready", "cheer", "love", "best", "better")
BAD = ("cold", "wet", "grim", "dull", "miserable", "tired", "bad", "wrong", "trouble",
       "problem", "miss", "late", "busy", "stuck", "dark", "grey", "gray")
APOLOGY = ("sorry", "afraid", "unfortunately", "sadly", "can't", "cannot", "don't know", "no idea")
SLEEPY = ("sleep", "bed", "dream", "yawn", "goodnight", "good night", "nap")


def bot_face(text, kind="idle", when=None):
    """Pick a face from the tone of the line, with a slow blink. Positive and
    negative words are counted against each other rather than first-match, so
    "the rain has finally stopped" doesn't read as gloom."""
    import time as _time
    low = (text or "").lower()
    if kind == "listening":                  # it heard the wake phrase: ears up
        mood = "listening"
    elif kind in KIND_MOODS and low:
        mood = KIND_MOODS[kind]
    elif not low:
        mood = "thinking"
    else:
        good = sum(1 for w in GOOD if w in low)
        bad = sum(1 for w in BAD if w in low)
        score = good - bad
        if any(w in low for w in APOLOGY):
            mood = "sorry"
        elif any(w in low for w in SLEEPY):
            mood = "sleepy"
        elif low.rstrip().endswith("?"):
            mood = "curious"
        elif score >= 2 or ("!" in low and score >= 1):
            mood = "cool"
        elif score >= 1:
            mood = "happy"
        elif score <= -2:
            mood = "worried"
        elif kind == "chat":
            mood = "pleased"
        else:
            mood = "plain"
    pair = BOT_MOODS[mood]
    blink = int((when or _time.time()) // 3) % 2
    return pair[blink], mood


def drawable_face(fnt, text, kind="idle", when=None):
    """The face for this line, guaranteed to be drawable in this font — a
    missing glyph would come out as an empty box."""
    from ..text import draws
    face, mood = bot_face(text, kind, when)
    if draws(fnt, face):
        return face, mood
    fallback = SAFE_FACE[int((when or time.time()) // 3) % 2]
    return (fallback if draws(fnt, fallback) else ":-)"), mood


class MessageMode(Mode):
    """Shows a fixed message, or cycles through jokes / fortunes."""
    name = "message"
    label = "Message"
    interval = 1.0

    def __init__(self, config, panel=None):
        super().__init__(config, panel)
        self._jokes = None
        self._current = None
        self._spoken = None          # the last one it read out, so it doesn't repeat
        self._next_switch = 0

    def _load_jokes(self):
        if self._jokes is None:
            path = os.path.join(ASSETS_DIR, "jokes.json")
            with open(path) as fh:
                self._jokes = json.load(fh)
        return self._jokes

    @staticmethod
    def _lines(name):
        with open(os.path.join(ASSETS_DIR, name)) as fh:
            return [line.strip() for line in fh if line.strip()]

    def _read_aloud(self, text, s):
        """Say a freshly picked joke or fortune, if you asked it to. The bot
        speaks for itself, and your own text is yours to read."""
        if not text or not s.get("speak_new"):
            return
        if s.get("source", "message") not in ("joke", "fortune", "topic"):
            return
        if text == self._spoken:
            return
        self._spoken = text
        try:
            from .. import audio
            audio.say(text.replace("\n", ". "), self.config.get("audio", {}))
        except Exception:
            log.debug("couldn't read that one out", exc_info=True)

    def _pick(self, s=None):
        s = s if s is not None else self.settings
        source = s.get("source", "message")
        if source == "bot":
            return self._bot(s)
        if source == "joke":
            j = random.choice(self._load_jokes())
            return f"{j['setup']}\n-- {j['punchline']}"
        if source == "fortune":
            return self._fortune()
        if source == "topic":
            return self._fits(self._lines("topics.txt"))
        return s.get("text") or ""

    def _bot(self, s=None):
        """Whatever the bot last said, asking for something new when the line
        is stale. Generation runs in the background, never here."""
        s = s if s is not None else self.settings
        from .. import listen, llm, mascot
        ears = listen.EARS.status()
        if ears["state"] == "listening":
            return "Listening…"
        if ears["state"] == "thinking":
            return "Thinking…"          # what you said stays between you and the Pi
        conf = self.config.get("llm", {})
        if not conf.get("enabled"):
            return "The bot is off.\nTurn it on with the robot button."
        if not (conf.get("model") or "").strip():
            return "No model chosen.\nOpen the bot panel and press Check."
        said = llm.latest()
        cycle = int(s.get("cycle_seconds", 0) or 0)
        stale = cycle and (time.time() - said["ts"]) >= cycle
        if (not said["text"] or stale) and not llm.busy():
            day = None
            try:
                from .me import Day
                day = Day(self.config.get("schedule", {}))
            except Exception:
                pass
            mascot.ask(self.config, "idle", day=day)
        if said["text"]:
            return said["text"]
        if llm.busy():
            return "Thinking…"
        return said["error"] or "Say something to it — the robot button, top right."

    def _fits(self, candidates):
        """A random entry that fits the screen at the current font and size."""
        fnt = font(self.settings.get("font", "Font.ttc"), self.settings.get("size", 18))
        f = self.frame()
        max_lines = max(1, (f.height - 8) // line_height(fnt))
        random.shuffle(candidates)
        for text in candidates:
            if len(wrap(f.draw, text, fnt, f.width - 8)) <= max_lines:
                return text
        return candidates[0] if candidates else ""

    def _fortune(self):
        """The bundled fortunes, plus the `fortune` program's if it's installed."""
        pool = self._lines("fortunes.txt")
        if shutil.which("fortune"):
            for _ in range(5):
                try:
                    out = subprocess.run(["fortune", "-s", "-n", "120"], capture_output=True,
                                         text=True, timeout=3).stdout.strip()
                    if out:
                        pool.append(out)
                except Exception:
                    break
        return self._fits(pool)

    def update(self, config):
        super().update(config)
        self._current = None          # settings changed -> re-pick

    def on_button(self, action):
        source = self.settings.get("source", "message")
        if source == "bot":
            from .. import llm, mascot
            if not llm.busy():
                mascot.ask(self.config, "idle")
            return True
        if source != "message":
            self._current = None      # a new joke / fortune
            return True
        return False

    def render(self):
        s = self.settings
        now = time.time()
        cycle = int(s.get("cycle_seconds", 0) or 0)
        source = s.get("source", "message")
        if source == "bot":
            self._current = self._pick(s) or ""   # cheap: it reads the last line
            self._next_switch = float("inf")
        elif self._current is None or (source != "message" and cycle and now >= self._next_switch):
            self._current = self._pick(s) or ""
            self._next_switch = now + cycle if cycle else float("inf")
            self._read_aloud(self._current, s)

        f = self.frame()
        fnt = font(s.get("font", 1), s.get("size", 18))
        if source == "bot" and s.get("bot_face", True):
            return self._render_with_face(f, fnt, s)
        # the bot's answers always start at the top: they arrive at any length
        valign = "top" if source == "bot" else s.get("valign", "top")
        draw_block(f.draw, self._current, fnt, (0, 0, f.width, f.height), f.fg,
                   align=s.get("align", "left"), valign=valign)
        return f.image

    def _render_with_face(self, f, fnt, s):
        """Face top-left, the picture it is talking about top-right, and the
        words underneath — the bot narrating what it just looked at."""
        from .. import listen, llm
        said = llm.latest()
        ears = listen.EARS.status()["state"]
        kind = "listening" if ears in ("listening", "thinking") else said.get("kind", "idle")
        T = max(1.0, min(1.8, min(f.width / 250, f.height / 122)))
        face_font = font(2, round(26 * T))
        face, _mood = drawable_face(face_font, self._current if said["text"] else "", kind)
        f.draw.text((3, 1), face, font=face_font, fill=f.fg)
        top = round(29 * T)

        seen = llm.picture() if said.get("kind") in ("drawing", "seen", "buddy-happy", "buddy-surprised") else None
        if seen and seen.get("image") is not None:
            top = max(top, self._paste_picture(f, seen["image"], T))

        box = (0, top, f.width, f.height)
        # narration, so it sits a size below what would fill the space
        fitted = self._fit_font(f, self._current, box, s, cap=round(20 * T))
        draw_block(f.draw, self._current, fitted, box, f.fg,
                   align=s.get("align", "left"), valign="top")
        return f.image

    def _paste_picture(self, f, image, T):
        """Draw it small in the top-right corner, at the panel's own resolution
        so the dithering is crisp. Returns where the text should start."""
        from PIL import Image
        room_w, room_h = int(f.width * 0.42), int(f.height * 0.46)
        scale = f.scale
        shot = image.copy().convert("L")
        shot.thumbnail((int(room_w * scale), int(room_h * scale)), Image.LANCZOS)
        shot = shot.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
        w, h = round(shot.width / scale), round(shot.height / scale)
        x, y = f.width - w - 3, 2
        f.image.paste(shot, (int(round(x * scale)), int(round(y * scale))))
        f.draw.rectangle([x - 1, y - 1, x + w, y + h], outline=f.fg, width=1)
        return y + h + round(4 * T)

    _FITTED = {}

    @classmethod
    def _fit_font(cls, f, text, box, s, cap=48):
        """The biggest size that still fits the box — short answers fill the
        screen instead of leaving it half empty. `cap` keeps narration small.

        The answer only changes when the words, the box or the font do, so it
        is worked out once and kept; this runs on every frame otherwise."""
        x0, y0, x1, y1 = box
        pad = 4                                   # draw_block pads both sides
        width, height = x1 - x0 - 2 * pad, y1 - y0 - 2 * pad
        name = s.get("font", "Font.ttc")
        key = (text, width, height, name, cap)
        known = cls._FITTED.get(key)
        if known is not None:
            return known
        best = font(name, 11)
        for size in range(11, max(12, int(cap)) + 1):
            candidate = font(name, size)
            lines = wrap(f.draw, text or " ", candidate, width)
            if len(lines) * line_height(candidate) > height:
                break
            if max((text_width(f.draw, ln, candidate) for ln in lines), default=0) > width:
                break
            best = candidate
        if len(cls._FITTED) > 60:
            cls._FITTED.clear()
        cls._FITTED[key] = best
        return best


class ImageMode(Mode):
    """Shows the drawing the Draw tab last sent.

    The decoded drawing is kept in memory and only written to
    data/drawing.png as a best effort, so a flaky SD card can't take the
    screen down with it."""
    name = "image"
    label = "Drawing"
    interval = 5.0
    PATH = os.path.join(DATA_DIR, "drawing.png")
    _current = None          # PIL image, shared by all instances
    _disk_error = None

    @classmethod
    def store(cls, raw):
        """Accept PNG/JPEG bytes from the web page."""
        img = Image.open(io.BytesIO(raw))
        img.load()
        cls._current = img.convert("RGBA")
        cls._disk_error = None
        try:
            tmp = cls.PATH + ".part"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, cls.PATH)
        except OSError as e:
            cls._disk_error = str(e)
            log.warning("could not save drawing to disk (%s); keeping it in memory", e)

    @classmethod
    def _load(cls):
        if cls._current is None and os.path.exists(cls.PATH):
            try:
                img = Image.open(cls.PATH)
                img.load()
                cls._current = img.convert("RGBA")
            except OSError as e:
                cls._disk_error = str(e)
                log.warning("could not read %s: %s", cls.PATH, e)
        return cls._current

    def render(self):
        f = self.frame()
        img = self._load()
        if img is None:
            text = ("Drawing on disk is unreadable.\nSend it again from the Draw tab."
                    if self._disk_error else "No drawing yet.\nUse the Draw tab to send one.")
            draw_block(f.draw, text, font(1, 14), (0, 0, f.width, f.height), f.fg,
                       align="center", valign="center")
            return f.image
        bg = Image.new("RGBA", img.size, "black" if f.dark else "white")
        composite = Image.alpha_composite(bg, img).convert("L")
        if f.dark:
            composite = ImageOps.invert(composite)
        composite = composite.resize((f.width, f.height), Image.LANCZOS)
        dither = Image.FLOYDSTEINBERG if self.settings.get("dither", True) else Image.NONE
        return composite.convert("1", dither=dither)


class OffMode(Mode):
    """Blank screen. The service puts the panel to sleep in this mode."""
    name = "off"
    label = "Off"
    interval = 60.0

    def render(self):
        return self.frame().image
