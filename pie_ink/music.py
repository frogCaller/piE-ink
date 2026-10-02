"""Playing what's in your music folder.

mpg123 is driven in its remote-control mode: we send it LOAD/PAUSE/STOP and
it tells us the position and how much is left, which is what the progress bar
on the screen uses. Anything mpg123 can't open (flac, m4a) falls back to
ffplay, where the position is timed by the clock instead.

While a page has "Play on this device" on, the page plays the file instead
(kind "page"): the Pi keeps the playlist and the state, the page follows
them, and says how far it has got and when a track ends.
"""
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time

from .settings import DATA_DIR

log = logging.getLogger(__name__)
FOLDER = os.path.join(DATA_DIR, "music")
KINDS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".mp2")
MPG123_KINDS = (".mp3", ".mp2", ".wav")
TRACK_NO = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")


def folder(conf=None):
    path = ((conf or {}).get("folder") or "").strip() or FOLDER
    return os.path.expanduser(path)


def ensure_folder(conf=None):
    path = folder(conf)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def pretty(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = TRACK_NO.sub("", stem).replace("_", " ").strip()
    return stem or os.path.basename(name)


def library(conf=None):
    """Every playable file in the folder, sorted, newest folders included."""
    root = ensure_folder(conf)
    found = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(KINDS):
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            found.append({"path": rel, "title": pretty(name), "size": size,
                          "folder": os.path.dirname(rel)})
    found.sort(key=lambda t: (t["folder"].lower(), t["title"].lower()))
    return found


class Player:
    """One track at a time, with a playlist around it."""

    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.reader = None
        self.kind = None               # "mpg123" | "ffplay" | "page" (a phone or computer plays it)
        self.volume = 100              # software level, used only when the output has no mixer
        self.tracks = []
        self.index = -1
        self.state = "stopped"         # stopped | playing | paused
        self.position = 0.0
        self.duration = 0.0
        self.started = 0.0
        self.error = None
        self.conf = {}
        self.shuffle = False
        self.repeat = True
        self.order = []
        self._want_next = False
        self.page = None               # () -> True while a page plays PiE-ink's sound (here.HERE.listening)
        self.on_change = None          # () after anything a page should follow changes
        self.v = 0                     # counts those changes; a page's reports carry the one it's playing
        self.starts = 0                # counts play(): the same track again starts again, in a page too
        self._rep_at = 0.0             # when position was last known (a page's report, or a start)
        self._errs = 0                 # tracks in a row a page couldn't play

    def _to_page(self):
        try:
            return bool(self.page and self.page())
        except Exception:
            return False

    def _changed(self):
        self.v += 1
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.debug("music: telling the page", exc_info=True)

    def _pos(self):
        """Where it is: a page's last word on it, plus the time since, while playing."""
        if self.kind == "page" and self.state == "playing":
            pos = self.position + max(0.0, time.time() - self._rep_at)
            return min(pos, self.duration) if self.duration else pos
        return self.position

    # -- process handling -------------------------------------------------------------

    def _stop_proc(self):
        proc, self.proc, self.kind = self.proc, None, None
        if not proc:
            return
        try:
            if proc.poll() is None:
                if proc.stdin:
                    try:
                        proc.stdin.write("QUIT\n")
                        proc.stdin.flush()
                    except (OSError, ValueError):
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass

    def _alsa(self):
        device = (self.conf.get("device") or "").strip()
        return device

    def _start_mpg123(self, path, start=0.0):
        device = self._alsa()
        cmd = ["mpg123", "-R"]                 # no --quiet: the status lines are how we track position
        out_module = os.environ.get("PIE_INK_AUDIO_OUT")
        if out_module:
            cmd += ["-o", out_module]           # handy for testing without a sound card
        elif device:
            cmd += ["-o", "alsa", "-a", device]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        if self.volume < 100:                    # the output has no mixer: mpg123 turns itself down
            proc.stdin.write(f"VOLUME {self.volume}\n")
        proc.stdin.write(f"LOAD {path}\n")
        if start >= 1:
            proc.stdin.write(f"JUMP {start:.1f}s\n")   # carrying on from where a page had got to
        proc.stdin.flush()
        self.proc, self.kind = proc, "mpg123"
        self.reader = threading.Thread(target=self._read_mpg123, args=(proc,), daemon=True)
        self.reader.start()

    def _read_mpg123(self, proc):
        """mpg123 tells us where it is: @F frame frame secs secs, @P 0|1|2.
        Events from a process we've already replaced are ignored, or the end
        of the outgoing track would skip the incoming one."""
        for line in proc.stdout:
            if self.proc is not proc:
                return
            line = line.strip()
            if line.startswith("@F "):
                bits = line.split()
                if len(bits) >= 5:
                    try:
                        self.position = float(bits[3])
                        self.duration = float(bits[3]) + float(bits[4])
                    except ValueError:
                        pass
            elif line.startswith("@P "):
                code = line.split()[-1]
                if code == "0":                       # track finished
                    if self.state == "playing":
                        self._want_next = True
                elif code == "1":
                    self.state = "paused"
                elif code == "2":
                    self.state = "playing"
            elif line.startswith("@E"):
                self.error = line[3:].strip() or "mpg123 couldn't play that"
                self._want_next = True

    def _start_ffplay(self, path, start=0.0):
        if not shutil.which("ffplay"):
            self.error = "no player for that file type (install ffmpeg for flac/m4a)"
            return False
        self.duration = self._probe(path)
        env = dict(os.environ)
        device = self._alsa()
        if device:
            env["SDL_AUDIODRIVER"] = "alsa"
            env["AUDIODEV"] = device
        self.proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                                      "-volume", str(self.volume),
                                      *(["-ss", f"{start:.1f}"] if start >= 1 else []), path],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=env)
        self.kind = "ffplay"
        self.started = time.time() - start
        return True

    @staticmethod
    def _probe(path):
        if not shutil.which("ffprobe"):
            return 0.0
        try:
            out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", path], capture_output=True, text=True, timeout=10).stdout
            return float(out.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0.0

    # -- the playlist -----------------------------------------------------------------

    def load(self, tracks, conf):
        with self.lock:
            self.tracks = tracks
            self.conf = conf or {}
            self.shuffle = bool(self.conf.get("shuffle"))
            self.repeat = bool(self.conf.get("repeat", True))
            self._reorder()

    def _reorder(self):
        self.order = list(range(len(self.tracks)))
        if self.shuffle:
            random.shuffle(self.order)

    def play(self, index=None, conf=None, start=0.0):
        """Play a track (this one again, when not given), from `start` seconds —
        on the Pi, or in the page playing PiE-ink's sound, if one is."""
        with self.lock:
            if conf:
                self.conf = conf
            if not self.tracks:
                self.error = "no music in the folder yet"
                return self.status()
            if index is None:
                index = self.index if self.index >= 0 else (self.order[0] if self.order else 0)
            index = max(0, min(len(self.tracks) - 1, int(index)))
            same = index == self.index
            self._stop_proc()
            duration = self.duration if same and start else 0.0
            self.index, self.position, self.duration, self.error = index, float(start or 0.0), duration, None
            self._want_next = False
            track = self.tracks[index]
            path = os.path.join(folder(self.conf), track["path"])
            ext = os.path.splitext(path)[1].lower()
            if self._to_page():
                self.kind, self._rep_at = "page", time.time()
            elif ext in MPG123_KINDS and shutil.which("mpg123"):
                self._start_mpg123(path, start)
            elif not self._start_ffplay(path, start):
                self.state = "stopped"
                self._changed()
                return self.status()
            self.state = "playing"
            self.started = time.time() - self.position
            self.starts += 1
            self._changed()
            return self.status()

    def set_volume(self, percent):
        """The software level (0-100): applied now if mpg123 is playing, and
        to whatever plays next."""
        self.volume = max(0, min(100, int(percent)))
        try:
            if self.kind == "mpg123" and self.proc and self.proc.poll() is None:
                self.proc.stdin.write(f"VOLUME {self.volume}\n")
                self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def toggle(self):
        with self.lock:
            if self.state == "paused" and ((self.kind == "page") != self._to_page()
                                           or (self.kind != "page" and not self.proc)):
                # the sound moved while paused, or ffplay (which can't pause) stopped: carry on from there
                return self.play(self.index, start=self._pos())
            if self.kind == "page" and self.state != "stopped":
                if self.state == "playing":
                    self.position, self.state = self._pos(), "paused"
                else:
                    self.state, self._rep_at = "playing", time.time()
                self._changed()
                return self.status()
            if self.state == "stopped" or not self.proc:
                return self.play()
            if self.kind == "mpg123":
                try:
                    self.proc.stdin.write("PAUSE\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                self.state = "paused" if self.state == "playing" else "playing"
                if self.state == "playing":
                    self.started = time.time() - self.position
            else:                                   # ffplay can't pause: stop and remember
                if self.state == "playing":
                    self.position = time.time() - self.started
                    self._stop_proc()
                    self.state = "paused"
                else:
                    self.play(self.index)
            self._changed()
            return self.status()

    def stop(self):
        with self.lock:
            self._stop_proc()
            self.state = "stopped"
            self.position = 0.0
            self._changed()
            return self.status()

    # -- a page playing it ----------------------------------------------------------------

    def moved(self):
        """A page started playing PiE-ink's sound, or the last one stopped.
        Music playing on the Pi moves to the page, from where it had got to;
        music playing in a page that's gone is paused (not suddenly out of
        the Pi's speaker), and carries on wherever play is pressed next."""
        with self.lock:
            to_page = self._to_page()
            if self.state != "playing" or (self.kind == "page") == to_page:
                return self.status()
            if to_page:
                where = self.position if self.kind == "mpg123" else time.time() - self.started
                return self.play(self.index, start=max(0.0, where))
            self.position, self.state = self._pos(), "paused"
            self._changed()
            return self.status()

    def report(self, d):
        """A page says how the track is going: {v, position, duration, ended, error}.
        A report about something older (the track changed since) is ignored."""
        with self.lock:
            try:
                if int(d.get("v", -1)) != self.v or self.kind != "page":
                    return False
            except (TypeError, ValueError):
                return False
            if d.get("error"):
                self._errs += 1
                self.error = f"this device can't play it ({str(d['error'])[:60]})"
                if self._errs >= 3:                            # three in a row: stop, rather than skip through them all
                    self.state = "stopped"
                    self._changed()
                else:
                    self._want_next = True
                return True
            for key in ("position", "duration"):
                try:
                    val = float(d.get(key))
                except (TypeError, ValueError):
                    continue
                if val >= 0 and val == val and val != float("inf"):
                    setattr(self, key, val)
            if self.state == "playing":
                self._rep_at = time.time()
            if self.position > 1:
                self._errs = 0
            if d.get("ended") and self.state == "playing":
                self._want_next = True
            return True

    def page_state(self):
        """The music as a page plays it: nothing, unless it's the page's to play."""
        with self.lock:
            track = self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None
            mine = self.kind == "page" and track is not None and self.state in ("playing", "paused")
            return {"v": self.v, "state": self.state if mine else "stopped", "start": self.starts,
                    "path": track["path"] if mine else "", "title": track["title"] if mine else "",
                    "position": round(self._pos(), 2)}

    def step(self, delta):
        with self.lock:
            if not self.tracks:
                return self.status()
            if self.index in self.order:
                at = self.order.index(self.index)
            else:
                at = 0
            at = (at + delta) % len(self.order)
            return self.play(self.order[at])

    def tick(self):
        """Called by whoever renders: advances the playlist when a track ends."""
        with self.lock:
            if self.kind == "ffplay" and self.proc and self.state == "playing":
                self.position = time.time() - self.started
                if self.proc.poll() is not None:
                    self._want_next = True
            if self.kind == "page" and self.state == "playing" and not self._to_page():
                self.moved()                        # the page went away without a word: pause
            if self._want_next:
                self._want_next = False
                if self.repeat or (self.index in self.order and self.order.index(self.index) < len(self.order) - 1):
                    self.step(1)
                else:
                    self.stop()
            return self.status()

    def status(self):
        track = self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None
        return {
            "state": self.state, "index": self.index, "count": len(self.tracks),
            "title": track["title"] if track else "", "path": track["path"] if track else "",
            "folder": track["folder"] if track else "",
            "position": round(self._pos(), 1), "duration": round(self.duration, 1),
            "shuffle": self.shuffle, "repeat": self.repeat, "error": self.error,
            "on_page": self.kind == "page",
        }


PLAYER = Player()
