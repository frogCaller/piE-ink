"""Hearing PiE-ink on your phone or computer instead of the Pi's speaker.

A page with "Play on this device" switched on keeps a request open to the Pi
(a long poll: it comes back as soon as there's something to play, or after
WAIT seconds, and the page asks again at once — no timers, so a tab in the
background keeps up too). While a page is asking, the Pi's voice goes to it:
a WAV for every second or so of speech, which the page plays back to back
without a gap. Music plays in the page from the file itself, and the Pi keeps
track of where it is from what the page reports, so the screen's progress bar
and "next track" still work.

A page that stops asking — switched off, closed — is forgotten after GRACE
seconds (at once, when it says goodbye), and the Pi's own speaker is back.
"""
import io
import logging
import threading
import time
import wave

log = logging.getLogger(__name__)

OUTPUT = "browser"            # the output's name, where audio.py takes a device
WAIT = 15.0                   # a long poll waits this long for something
GRACE = 25.0                  # a page that asked this recently is still listening
KEEP_BYTES = 16_000_000       # speech kept for pages to fetch
KEEP_EVENTS = 300


def wav_bytes(pcm, rate):
    """16-bit mono PCM as a WAV file, which every browser plays."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(bytes(pcm))
    return buf.getvalue()


class Here:
    def __init__(self):
        self.cv = threading.Condition()
        self.seq = 0                  # the newest event's number
        self.events = []              # {"seq", "kind": "say" | "hush", ...}
        self.clips = {}               # seq -> WAV bytes
        self.kept = 0
        self.pages = {}               # page id -> when it last asked
        self.lines = 0                # each spoken line has a number: a page drops an older line's audio
        self.music_v = 0              # counts changes to the music; a page follows the newest
        self.music = None             # () -> the music as a page plays it (music.PLAYER.page_state)
        self.on_start = None          # () when the first page starts listening
        self.on_stop = None           # () when the last one stops
        self._was = False

    # -- who's listening -------------------------------------------------------------------------

    def listening(self):
        """Is a page playing PiE-ink's sound right now?"""
        now = time.time()
        with self.cv:
            live = any(now - t < GRACE for t in self.pages.values())
            changed, self._was = live != self._was, live
        if changed:
            fn = self.on_start if live else self.on_stop
            log.info("sound: %s", "a page is playing it now" if live else "back on the Pi's own speaker")
            if fn is not None:                  # not here: whoever asked may hold a lock the callback wants
                threading.Thread(target=self._call, args=(fn,), daemon=True, name="sound-here").start()
        return live

    @staticmethod
    def _call(fn):
        try:
            fn()
        except Exception:
            log.exception("sound: after a page came or went")

    def pages_listening(self):
        now = time.time()
        with self.cv:
            return sum(1 for t in self.pages.values() if now - t < GRACE)

    def seen(self, page):
        now = time.time()
        with self.cv:
            self.pages[page] = now
            for p, t in list(self.pages.items()):
                if now - t > GRACE * 4:
                    del self.pages[p]
        self.listening()

    def leave(self, page):
        """A page switched it off, or is closing."""
        with self.cv:
            self.pages.pop(page, None)
            self.cv.notify_all()                # its long poll comes back now
        self.listening()

    # -- what they play ------------------------------------------------------------------------

    def new_line(self):
        with self.cv:
            self.lines += 1
            return self.lines

    def say(self, pcm, rate, line):
        """A piece of a spoken line, to be played straight after the one before."""
        if not pcm:
            return None
        data = wav_bytes(pcm, rate)
        with self.cv:
            self.seq += 1
            self.clips[self.seq] = data
            self.kept += len(data)
            self.events.append({"seq": self.seq, "kind": "say", "line": line, "clip": self.seq,
                                "secs": round(len(pcm) / (2.0 * rate), 3), "at": round(time.time(), 3)})
            self._trim()
            self.cv.notify_all()
            return self.seq

    def hush(self, line=None):
        """Stop the speech (the one line, or whatever is playing)."""
        with self.cv:
            self.seq += 1
            self.events.append({"seq": self.seq, "kind": "hush", "line": line})
            self._trim()
            self.cv.notify_all()

    def music_changed(self):
        with self.cv:
            self.music_v += 1
            self.cv.notify_all()

    def _trim(self):
        while self.events and (len(self.events) > KEEP_EVENTS or self.kept > KEEP_BYTES):
            gone = self.events.pop(0)
            data = self.clips.pop(gone.get("clip"), None)
            if data is not None:
                self.kept -= len(data)

    def clip(self, n):
        with self.cv:
            return self.clips.get(n)

    def wait(self, page, after, music_v, timeout=WAIT):
        """A page asking what to play: the events after `after` and, if it
        changed since `music_v`, the music. Waits (up to timeout) until there
        is something. A page that has just come (after < 0) gets nothing old."""
        self.seen(page)
        end = time.time() + timeout
        with self.cv:
            if after < 0 or after > self.seq:
                after = self.seq
            while (self.seq <= after and self.music_v == music_v and page in self.pages
                   and time.time() < end):
                self.cv.wait(max(0.05, end - time.time()))
            events = [e for e in self.events if e["seq"] > after]
            seq, mv = self.seq, self.music_v
        out = {"seq": seq, "events": events, "mv": mv}
        if mv != music_v and self.music is not None:
            try:
                out["music"] = self.music()
            except Exception:
                log.exception("sound: the music for a page")
        return out

    def status(self):
        return {"pages": self.pages_listening(), "listening": self.listening()}


HERE = Here()
