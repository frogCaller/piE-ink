"""Sound: which output to use, how loud, and saying things out loud.

Speech is Piper, a neural voice that runs on the Pi. The process is started
once and kept alive with the voice loaded, so a line starts speaking straight
away rather than waiting seconds for the model; asking for a new line cuts off
whatever is being said. Audio is piped into aplay on the device you picked —
or, while a page has "Play on this device" on, handed to that page (here.py).
Nothing here raises: if a piece is missing you get a message you can act on.
"""
import json
import logging
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time

import requests

from . import here
from .settings import DATA_DIR

log = logging.getLogger(__name__)
VOICE_DIR = os.path.join(DATA_DIR, "voices")
TTS_CACHE = os.path.join(DATA_DIR, "cache", "tts")
CACHE_KEEP = 60          # how many spoken lines to keep around

# a few Piper voices worth having; the files come from the project's own release
# repository and are a few tens of megabytes each
PIPER_VOICES = [
    {"id": "en_US-amy-low", "label": "Amy, quick — best on a Pi Zero", "path": "en/en_US/amy/low",
     "quick": True},
    {"id": "en_GB-alan-low", "label": "Alan, quick — British, small", "path": "en/en_GB/alan/low",
     "quick": True},
    {"id": "en_US-amy-medium", "label": "Amy — American, warm", "path": "en/en_US/amy/medium"},
    {"id": "en_US-lessac-medium", "label": "Lessac — American, clear", "path": "en/en_US/lessac/medium"},
    {"id": "en_US-ryan-high", "label": "Ryan — American, male", "path": "en/en_US/ryan/high"},
    {"id": "en_GB-alan-medium", "label": "Alan — British, male", "path": "en/en_GB/alan/medium"},
    {"id": "en_GB-jenny_dioco-medium", "label": "Jenny — British, female", "path": "en/en_GB/jenny_dioco/medium"},
    {"id": "en_GB-northern_english_male-medium", "label": "Northern English, male", "path": "en/en_GB/northern_english_male/medium"},
    {"id": "en_US-eminem-medium", "label": "Eminem (fan-made impression)", "bundled": True,
     "url": "https://github.com/simoniz0r/piper-voice-models/releases/download/eminem/en_US-eminem-medium.onnx"},
]
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def have(tool):
    return shutil.which(tool) is not None


def missing():
    """Which of the pieces aren't installed."""
    return [t for t in ("aplay", "mpg123") if not have(t)]


# -- Piper: a real voice rather than the robot one ----------------------------------

def piper_cmd():
    """How to run Piper here, or None."""
    if have("piper"):
        return ["piper"]
    try:
        r = subprocess.run(["python3", "-m", "piper", "--help"], capture_output=True, timeout=20)
        if r.returncode == 0:
            return ["python3", "-m", "piper"]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def installed_voices():
    """Piper voices sitting in data/voices."""
    try:
        names = sorted(f[:-5] for f in os.listdir(VOICE_DIR) if f.endswith(".onnx"))
    except OSError:
        return []
    known = {v["id"]: v["label"] for v in PIPER_VOICES}
    return [{"id": n, "label": known.get(n, n)} for n in names]


def voice_files(name):
    model = os.path.join(VOICE_DIR, f"{name}.onnx")
    return model, model + ".json"


def download_voice(name):
    """Fetch a Piper voice. Returns (ok, message)."""
    spec = next((v for v in PIPER_VOICES if v["id"] == name), None)
    if not spec:
        return False, "unknown voice"
    if spec.get("url"):                       # hosted somewhere other than the voice library
        return download_voice_url(spec["url"], name)
    os.makedirs(VOICE_DIR, exist_ok=True)
    model, meta = voice_files(name)
    try:
        for url, dest in ((f"{PIPER_BASE}/{spec['path']}/{name}.onnx", model),
                          (f"{PIPER_BASE}/{spec['path']}/{name}.onnx.json", meta)):
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                tmp = dest + ".part"
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        fh.write(chunk)
                os.replace(tmp, dest)
    except Exception as e:
        return False, f"download failed: {str(e)[:120]}"
    return True, f"{spec['label']} is ready"


_install = {"running": False, "log": "", "ok": None}


def install_status():
    return {**_install, "ready": piper_cmd() is not None}


def install_piper():
    """pip install piper-tts in the background; it pulls onnxruntime, so it
    takes a few minutes on a Pi."""
    if _install["running"]:
        return install_status()
    _install.update(running=True, log="", ok=None)

    def run():
        try:
            p = subprocess.Popen(["pip3", "install", "--break-system-packages", "piper-tts"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                _install["log"] = (_install["log"] + line)[-4000:]
            _install["ok"] = p.wait() == 0
        except Exception as e:
            _install["ok"] = False
            _install["log"] += f"\n{e}"
        finally:
            _install["running"] = False
            log.info("piper install finished: ok=%s", _install["ok"])

    threading.Thread(target=run, daemon=True, name="piper-install").start()
    return install_status()


def _fetch(url, dest, timeout=180):
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        os.replace(tmp, dest)


def download_voice_url(url, name=""):
    """Add a Piper voice from any direct link to its .onnx file — a GitHub
    release, your own server, anywhere. The matching .onnx.json is fetched
    from alongside it, since Piper can't speak without it."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "that doesn't look like a link"
    if not url.lower().endswith(".onnx"):
        return False, "link me to the .onnx file itself"
    name = (name or os.path.basename(url)[:-5]).strip().replace(" ", "_")
    name = "".join(c for c in name if c.isalnum() or c in "-_.") or "custom-voice"
    os.makedirs(VOICE_DIR, exist_ok=True)
    model, meta = voice_files(name)
    try:
        _fetch(url, model)
    except Exception as e:
        return False, f"couldn't fetch the voice: {str(e)[:110]}"
    for candidate in (url + ".json", url[:-5] + ".onnx.json", url[:-5] + ".json"):
        try:
            _fetch(candidate, meta, timeout=60)
            break
        except Exception:
            continue
    else:
        os.remove(model)
        return False, ("found the voice but not its .onnx.json settings file — "
                       "download both by hand into data/voices")
    return True, f"{name} is ready"


def _voice_rate(name, default=22050):
    _, meta = voice_files(name)
    try:
        with open(meta) as fh:
            return int(json.load(fh).get("audio", {}).get("sample_rate", default))
    except (OSError, ValueError, KeyError):
        return default


# -- which card is which ---------------------------------------------------------------------------
#
# A sound card's number is only the order the kernel found it in: plug in a camera with a
# microphone and yesterday's card 1 is card 2. Its name ("UACDemoV10") stays put, so a device is
# kept as plughw:CARD=<name>,DEV=<n> — aplay, arecord, mpg123 and amixer all take that. The older
# plughw:1,0 still works wherever it's read.

_CARD_LINE = re.compile(r"card (\d+): (\S+) \[([^\]]+)\], device (\d+): [^\[]*\[([^\]]*)\]")
_listed = {}                     # tool -> (when, [entries]): asked at most every few seconds


def alsa_list(tool="aplay", fresh=False):
    """What `aplay -l` (or `arecord -l`) lists: [{"index", "card", "name", "device", "dev_name"}]."""
    path = shutil.which(tool)
    got = _listed.get((tool, path))
    if got and not fresh and time.time() - got[0] < 3:
        return list(got[1])
    out = []
    if path:
        try:
            listing = subprocess.run([path, "-l"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        for line in listing.splitlines():
            m = _CARD_LINE.match(line)
            if m:
                index, card, name, dev, dev_name = m.groups()
                out.append({"index": int(index), "card": card, "name": name, "device": int(dev), "dev_name": dev_name})
    _listed[(tool, path)] = (time.time(), out)
    return list(out)


def named(card, device=0):
    """A card by its name rather than its number."""
    return f"plughw:CARD={card},DEV={device}"


def entries(tool="aplay", fresh=False):
    """The devices for a picker: [{id, label, alias}] — id goes in the settings, alias is the
    same device by number (as older settings have it)."""
    out = [{"id": "", "label": "System default"}]
    for e in alsa_list(tool, fresh):
        label = e["name"] + (f" — {e['dev_name']}" if e["dev_name"] and e["dev_name"] != e["name"] else "")
        out.append({"id": named(e["card"], e["device"]), "label": label, "alias": f"plughw:{e['index']},{e['device']}"})
    return out


def canonical(device):
    """Either spelling of a device as its name, when its card is here now."""
    m = re.fullmatch(r"(?:plug)?hw:(\d+)(?:,(\d+))?", (device or "").strip())
    if not m:
        return (device or "").strip()
    for e in alsa_list("aplay") + alsa_list("arecord"):
        if e["index"] == int(m.group(1)):
            return named(e["card"], int(m.group(2) or 0))
    return device.strip()


def same_device(a, b):
    """plughw:1,0 and plughw:CARD=X,DEV=0 are the same device when card 1 is X."""
    a, b = (a or "").strip(), (b or "").strip()
    return a == b or (bool(a) and bool(b) and canonical(a) == canonical(b))


def card_of(device):
    """The card, for amixer -c: its number or its name, from either spelling; None for the default."""
    m = re.match(r"(?:plug)?hw:(\d+)", device or "")
    if m:
        return m.group(1)
    m = re.search(r"CARD=([^,\s]+)", device or "")
    return m.group(1) if m else None


def devices():
    """ALSA playback devices: [{id, label, alias}] — id goes in the settings."""
    return entries("aplay")


def mixer_controls(device=""):
    """Volume controls on the chosen card, best first."""
    if not have("amixer"):
        return []
    card = ["-c", card_of(device)] if card_of(device) else []
    try:
        out = subprocess.run(["amixer", *card, "scontrols"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = re.findall(r"Simple mixer control '([^']+)'", out)
    preferred = [n for n in names if n.lower() in ("pcm", "speaker", "master", "headphone")]
    return preferred + [n for n in names if n not in preferred]


# when the output has no mixer control (plenty of USB speakers and DACs
# don't), the level is applied here instead: speech is scaled before it goes
# to aplay, music through the players' own gain
_soft = {"gain": 1.0, "percent": 100}


def soft_gain():
    return _soft["gain"]


def soft_percent():
    return _soft["percent"]


def set_volume(percent, device=""):
    """0–100 on the card's first usable control, or in software if the
    output has none. Returns "mixer", "software" or False."""
    percent = max(0, min(100, int(percent)))
    controls = mixer_controls(device)
    card = ["-c", card_of(device)] if card_of(device) else []
    for name in controls[:2]:
        try:
            r = subprocess.run(["amixer", *card, "-q", "set", name, f"{percent}%"],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                _soft.update(gain=1.0, percent=100)
                return "mixer"
        except (OSError, subprocess.SubprocessError):
            continue
    # no control to turn: attenuate what we send instead (a curve, so that
    # half way sounds about half as loud rather than barely quieter)
    _soft.update(gain=(percent / 100.0) ** 1.7, percent=percent)
    try:
        from . import music
        music.PLAYER.set_volume(percent)
    except Exception:
        pass
    return "software"


def scaled(chunk):
    """Speech samples (S16LE) at the software level, when one is in force."""
    gain = _soft["gain"]
    if gain >= 0.999 or not chunk:
        return chunk
    try:
        import numpy as np
        samples = np.frombuffer(chunk[:len(chunk) - len(chunk) % 2], dtype="<i2").astype(np.float32)
        return np.clip(samples * gain, -32768, 32767).astype("<i2").tobytes()
    except ImportError:
        import array
        out = array.array("h", chunk[:len(chunk) - len(chunk) % 2])
        for i, v in enumerate(out):
            out[i] = int(max(-32768, min(32767, v * gain)))
        return out.tobytes()


def chosen_voice(conf):
    """The voice to use: the one you picked, or the only one there is."""
    name = (conf.get("piper_voice") or "").strip()
    if name and os.path.exists(voice_files(name)[0]):
        return name
    installed = installed_voices()
    return installed[0]["id"] if installed else ""


def piper_ready(conf):
    """(ok, message) — checked before we promise to say anything."""
    name = chosen_voice(conf)
    if not piper_cmd():
        return False, "Piper isn't installed yet — install it in Settings → Sound"
    if not name or not os.path.exists(voice_files(name)[0]):
        return False, "no voice downloaded yet — pick one in Settings → Sound"
    if not have("aplay"):
        return False, "aplay is missing — run ./setup.sh on the Pi"
    return True, ""


def _pace_path():
    """How fast each voice makes speech here, learnt from the lines it has made."""
    return os.path.join(TTS_CACHE, "pace.json")


_mem = []


def _memory():
    """machine_memory(), asked once."""
    if not _mem:
        _mem.append(machine_memory())
    return _mem[0]


def _cache_path(voice, speed, text):
    import hashlib
    key = hashlib.sha1(f"{voice}|{speed}|{text}".encode()).hexdigest()[:20]
    return os.path.join(TTS_CACHE, key + ".raw")


def _cache_trim():
    try:
        files = [os.path.join(TTS_CACHE, f) for f in os.listdir(TTS_CACHE) if f.endswith(".raw")]
    except OSError:
        return
    if len(files) <= CACHE_KEEP:
        return
    files.sort(key=lambda f: os.path.getmtime(f))
    for f in files[:len(files) - CACHE_KEEP]:
        try:
            os.remove(f)
        except OSError:
            pass


REPLAY_MAX = 6_000_000       # bytes of a line kept for a second try on another output (~2 minutes)


def _sentences(text):
    """A line as sentences, so it can be spoken (and interrupted) one at a
    time. Splits after . ! ? when a space follows — "version 2.5" stays
    whole — and keeps a stub like "Mr." with what comes next."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    out = []
    for p in parts:
        if out and len(out[-1]) < 4:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out or [text]


def output_label(device):
    """A name for an output, for messages: the card's name when we know it."""
    if device == here.OUTPUT:
        return "your phone or computer"
    if not device:
        return "the system default output"
    for d in devices():
        if device in (d["id"], d.get("alias")):
            return f"{d['label']} ({device})"
    return device


def _fallback_outputs(device):
    """Where else to try when an output refuses: the system default when a
    card was chosen, the cards themselves when the default was."""
    others = [""] if device else []
    others += [d["id"] for d in devices() if d["id"] and device not in (d["id"], d.get("alias"))]
    return others


def _aplay_words(line):
    """'aplay: main:834: audio open error: Device or resource busy' -> the words. The Pi's HDMI sound says
    "Unknown error 524" when no screen is plugged into HDMI: that's said in plain words too."""
    words = re.sub(r"^aplay: \w+:\d+: ", "", (line or "").strip())
    if re.search(r"\berror 524\b", words):
        words += " (the Pi's HDMI sound: nothing that plays sound is plugged into HDMI)"
    return words


class _Play:
    """One aplay, with what it says on stderr kept, so a failure has a
    reason. A dead aplay used to be invisible: its stderr went nowhere and
    the audio just stopped going anywhere — the speaker 'worked once'."""

    def __init__(self, rate, device):
        self.device = device
        self.err = tempfile.TemporaryFile()
        cmd = ["aplay", "-q", "-r", str(rate), "-f", "S16_LE", "-c", "1", "-t", "raw", "-"]
        if device:
            cmd += ["-D", device]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.err)

    def alive(self):
        return self.proc.poll() is None

    def write(self, chunk):
        """False when aplay is gone (the pipe broke)."""
        if not chunk:
            return True
        try:
            self.proc.stdin.write(chunk)
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    def complaint(self):
        """The last thing aplay said before it stopped."""
        try:
            self.err.seek(0)
            text = self.err.read().decode(errors="replace")
        except (OSError, ValueError):
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _aplay_words(lines[-1]) if lines else ""

    def finish(self, timeout, cut):
        """Close the pipe and let it play out. Returns (exit code, complaint);
        the code is None when we cut it short (cut() said so, or it hung)."""
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        end = time.time() + timeout
        while self.proc.poll() is None and time.time() < end and not cut():
            time.sleep(0.05)
        if self.proc.poll() is None:
            self.kill()
            return None, ""
        return self.proc.returncode, self.complaint()

    def kill(self):
        if self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=1)
            except Exception:
                pass
        try:
            self.err.close()
        except Exception:
            pass


class _HerePlay:
    """A page's speaker, in aplay's place ("Play on this device"): the audio
    goes to the page a piece at a time — about a second, or the end of a
    sentence — and the page plays the pieces back to back. It can't refuse,
    so finishing is waiting about as long as the page takes to play it, as
    aplay would: whoever waits on a line (the photo's "Say cheese!", music
    coming back after a reply) still waits the right time."""
    PIECE = 1.0                    # seconds of speech in a piece
    LAG = 0.2                      # a piece reaches the page and starts about this late

    def __init__(self, rate):
        self.device = here.OUTPUT
        self.rate = rate
        self.line = here.HERE.new_line()
        self.buf = bytearray()
        self.until = 0.0           # when the page should be done with what it has
        self.dead = False

    def alive(self):
        return not self.dead

    def write(self, chunk):
        if self.dead:
            return False
        self.buf.extend(chunk)
        if len(self.buf) >= self.rate * 2 * self.PIECE:
            self.flush()
        return True

    def flush(self):
        """What there is so far, to the page."""
        n = len(self.buf) - len(self.buf) % 2
        if n <= 0 or self.dead:
            return
        piece, self.buf = bytes(self.buf[:n]), self.buf[n:]
        here.HERE.say(piece, self.rate, self.line)
        self.until = max(self.until, time.time() + self.LAG) + n / (2.0 * self.rate)

    def complaint(self):
        return ""

    def finish(self, timeout, cut):
        """The rest to the page, then wait until it has played (or the next line cuts in)."""
        self.flush()
        end = min(self.until + 0.1, time.time() + timeout)
        while time.time() < end:
            if cut():
                self.kill()
                return None, ""
            time.sleep(0.05)
        return 0, ""

    def kill(self):
        if not self.dead:
            self.dead = True
            here.HERE.hush(self.line)


class _Speaker:
    """One Piper process, kept alive with the model loaded, so speaking starts
    straight away instead of waiting seconds for the model to load.

    A single thread owns the sequence: it kills whatever is playing, waits for
    the last generation to drain, then plays the newest line. Press the button
    ten times and you hear the tenth, immediately.

    Playback goes to aplay, and aplay is watched: if it refuses the output
    (busy, unplugged, wrong card number) it is tried again, then the other
    outputs, and the line is replayed from the start on whichever took it.
    What went wrong is kept in last_error, and say(wait=True) returns it.

    Piper makes a line a sentence at a time and is given the next sentence
    while the last one plays. A voice slower than speech (a medium one on a
    Zero 2 W) still can't keep up, so the start of a line is held back just
    long enough that, once it speaks, it doesn't stop to wait for the next
    sentence: how fast the voice is here is learnt from the lines it makes.
    """
    QUIET = 0.45         # a gap this long in Piper's output means the sentence is done
    FIRST_SOUND = 60     # how long a slow voice on a small Pi may take to produce its first sound
    HOLD_MAX = 8.0       # the longest a line is held back before it speaks
    GAP = 0.05           # no sound from Piper for this long: it's making the next sentence

    def __init__(self):
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.proc = None
        self.model = None
        self.rate = 22050
        self.play = None                  # the aplay in use (a _Play), while a line is out
        self.pending = None
        self._conf = {}                   # the last settings we were given
        self._device = ""                 # the output you chose
        self.load_ms = 0                  # how long the voice took to load
        self.first_ms = 0                 # and how long until the first sound
        self.wake = threading.Event()
        self.worker = None
        self.error = None                 # why Piper couldn't start
        self.last_error = None            # why the last line didn't come out, or came out elsewhere
        self._complaint = ""              # the words aplay used, the last time it refused
        self.speaking = False
        self.ticket = 0                   # each line gets a number, so a caller can wait for its own
        self.finished = 0
        self.outcome = (True, "")
        self._sent = bytearray()          # this line's audio so far, for a replay after a failure
        self._total = 0                   # all of it, counted (the replay copy is capped)
        self._tried = []                  # the outputs this line has been sent to
        self._tries = 0                   # how often the chosen output has been retried for it
        self._on = ""                     # the output the current aplay is on
        self._held = None                 # the start of a line, held back until the rest will keep up
        self._asked = 0.0                 # when this line was asked for
        self._sounded = False             # its first sound has gone out
        self._slow_write = 0.0            # when a write to the output last had to wait (a full pipe)
        self._piper_pending = 0           # sentences Piper was given whose sound hasn't come
        self._paces = None                # how fast each voice makes speech here: {voice|length: {r, spc}}
        self.pace = None                  # the one in use, once learnt

    # -- the resident process ------------------------------------------------------

    def _spawn(self, conf):
        started = time.time()
        name = chosen_voice(conf)
        model, _ = voice_files(name)
        speed = float(conf.get("piper_speed", 1.0) or 1.0)
        length = f"{1 / max(0.5, min(2.0, speed)):.2f}"
        base = piper_cmd()
        for raw_flag in ("--output-raw", "--output_raw"):
            try:
                proc = subprocess.Popen([*base, "-m", model, raw_flag, "--length-scale", length],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
            except OSError as e:
                self.error = str(e)
                return False
            time.sleep(0.3)
            if proc.poll() is None:
                first = self.model is None
                self.proc, self.model, self.rate = proc, (name, length), _voice_rate(name)
                self.error = None
                self.load_ms = int((time.time() - started) * 1000)
                log.log(logging.INFO if first else logging.DEBUG,
                        "piper ready with %s (%sms)", name, self.load_ms)
                return True
            proc.kill()
        self.error = "Piper wouldn't start — check the voice files"
        return False

    def ensure(self, conf):
        """Start (or restart, if the voice changed) the resident process."""
        self._conf = conf or {}
        okay, why = piper_ready(conf)
        if not okay:
            return False, why
        name = chosen_voice(conf)
        speed = float(conf.get("piper_speed", 1.0) or 1.0)
        want = (name, f"{1 / max(0.5, min(2.0, speed)):.2f}")
        with self.lock:
            if self.proc and self.proc.poll() is None and self.model == want:
                return True, "ready"
            if self.proc and self.proc.poll() is not None:
                log.info("piper stopped on its own (exit %s) — starting it again",
                         self.proc.returncode)          # if you see this often, tell me the code
            elif self.proc and self.model != want:
                log.info("voice changed to %s", name)
            self._shutdown_locked()
            if not self._spawn(conf):
                return False, self.error or "Piper wouldn't start"
        if not self.worker or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run, daemon=True, name="speaker")
            self.worker.start()
        return True, "ready"

    def _piper(self, restart=False):
        """The resident Piper, started again if it has gone: some builds exit
        after each line, and a small Pi sometimes runs out of memory and
        loses it."""
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            if alive and not restart:
                return self.proc
            if alive:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
            elif self.proc is not None:
                log.info("piper exited (code %s) — starting it again", self.proc.returncode)
            self.proc = self.model = None
            return self.proc if self._conf and self._spawn(self._conf) else None

    def _shutdown_locked(self):
        self._kill_play()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.proc = self.model = None

    def shutdown(self):
        with self.lock:
            self._shutdown_locked()

    # -- playback ------------------------------------------------------------------

    def _kill_play(self):
        play, self.play = self.play, None
        if play is not None:
            play.kill()

    def _drain(self, seconds=3.0):
        """Throw away whatever the interrupted sentence is still producing.
        If Piper is still at it after a few seconds (a long line on a slow
        Pi), it is started afresh rather than letting the old audio come out
        in front of the new line. It was still making a sentence it had been
        given (the next one, while the last played): started afresh at once,
        since that sentence would otherwise come out first."""
        if self._piper_pending:
            self._piper_pending = 0
            log.debug("piper was making a sentence of the old line — starting it afresh")
            self._piper(restart=True)
            return
        end = time.time() + seconds
        while time.time() < end:
            if not self._read(0.1):
                return
        log.info("piper still busy with the old line — starting it afresh")
        self._piper(restart=True)

    def _read(self, timeout):
        """Some of Piper's output: b"" when there is none yet, None at the end
        of it (Piper has exited)."""
        proc = self.proc
        if not proc or not proc.stdout:
            return None
        try:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                return b""
            chunk = proc.stdout.read1(8192) if hasattr(proc.stdout, "read1") else proc.stdout.read(8192)
        except (ValueError, OSError):
            return None
        return chunk if chunk else None

    def _run(self):
        while True:
            self.wake.wait()
            with self.lock:
                text, self.pending = self.pending, None
                ticket = self.ticket
                self.wake.clear()             # under the lock: a line queued now is seen next time round
            if not text:
                continue
            asked_at = time.time()
            self.speaking = True
            outcome = (False, "didn't get to it")
            try:
                self._kill_play()                 # silence whatever is playing right now
                self._drain()                     # and let the tail of that sentence run out
                outcome = self._line(text, asked_at)
            except Exception as e:                # the one thread that speaks must survive anything
                log.exception("speaking failed")
                outcome = (False, f"speaking failed: {str(e)[:120]}")
                self._kill_play()
            finally:
                self.speaking = False
                with self.cv:
                    self.finished, self.outcome = ticket, outcome
                    self.cv.notify_all()
            if not outcome[0] and "cut short" not in outcome[1]:
                log.info("speaker: the line didn't play: %s", outcome[1])
            elif outcome[0] and self.first_ms:
                log.debug("spoke after %sms (voice load %sms)", self.first_ms, self.load_ms)
            self._rewarm()

    def _line(self, text, asked_at):
        """Say one line: from the cache if we have it, else through Piper a
        sentence at a time. Returns (ok, message)."""
        self._sent = bytearray()
        self._total = 0
        self._tried, self._tries = [], 0
        self.last_error = None
        self._held, self._asked, self._sounded = None, asked_at, False
        self._play_on(self._device)
        if self.play is None:
            return False, self.last_error or "aplay wouldn't start"
        cached = self._cached(text)
        status = self._play_bytes(cached, asked_at) if cached is not None else self._through_piper(text, asked_at)
        if status == "cut" or self.wake.is_set():
            self._kill_play()
            return False, "cut short by the next line"
        if status == "noplay":
            return False, self.last_error or "nothing would play it"
        if status == "silent":
            self._kill_play()
            return False, self.last_error or "Piper produced no sound"
        okay, why = self._play_out()
        if not okay:
            return False, why
        if cached is None:
            self._remember(text, bytes(self._sent))
        where = output_label(self._on)
        if not self.last_error:
            return True, f"played through {where}"
        if self._on == self._device:
            return True, f"played through {where} on a later try — it first said: {self._complaint}"
        return True, f"played through {where} instead — {self.last_error}"

    def _play_on(self, device):
        """Start aplay on an output (or hand the line to the page playing PiE-ink's sound)."""
        self._tried.append(device)
        self._on = device
        if device == here.OUTPUT:
            self.play = _HerePlay(self.rate)
            return
        try:
            self.play = _Play(self.rate, device)
        except OSError as e:
            self.play = None
            self.last_error = f"couldn't start aplay: {e}"
            log.warning("speaker: %s", self.last_error)

    def _write(self, chunk):
        """Audio to aplay. If aplay has died, find out why, start it again (a
        couple of times, then on another output) and replay what it missed.
        False when nothing will play it."""
        self._total += len(chunk)
        if len(self._sent) < REPLAY_MAX:
            self._sent.extend(chunk)
        if self._held is not None:                   # the start of the line is being held back
            self._held.extend(chunk)
            return True
        return self._out(chunk)

    def _out(self, chunk):
        """Audio to the output itself (aplay, or the page playing PiE-ink's sound)."""
        if not self._sounded:
            self._sounded = True
            if self._asked:
                self.first_ms = int((time.time() - self._asked) * 1000)
        t = time.time()
        # a page has its own volume (the phone's or computer's): the Pi's turning-down is for its own outputs
        if self.play is not None and self.play.write(chunk if self.play.device == here.OUTPUT else scaled(chunk)):
            if time.time() - t > 0.05:
                self._slow_write = time.time()      # the output's pipe was full: the reading of Piper waited
            return True
        return self._recover()

    def _release(self):
        """Let the held-back start of the line go: from here it streams. In
        slices, as the rest of it goes, so a newer line can still cut in."""
        held, self._held = self._held, None
        for i in range(0, len(held or b""), 8192):
            if self.wake.is_set():
                return True                          # the caller sees it and stops
            if not self._out(bytes(held[i:i + 8192])):
                return False
        if hasattr(self.play, "flush"):
            self.play.flush()                        # a page gets it now, even if it's less than a piece
        return True

    def _recover(self, complaint=None):
        """aplay stopped on its own. Try again, then elsewhere, replaying the
        line so far on whatever takes it."""
        while not self.wake.is_set():
            dead, self.play = self.play, None
            if dead is not None:
                if complaint is None:
                    complaint = dead.complaint()
                dead.kill()
            nxt = self._next_output(complaint or "")
            complaint = None
            if nxt is None:
                return False
            device, pause = nxt
            if pause:
                time.sleep(pause)
            self._play_on(device)
            if self.play is None:
                return False
            if self.play.write(scaled(bytes(self._sent))):
                return True
        return False

    def _next_output(self, complaint):
        """After aplay refused an output: the same one again a couple of
        times (a busy output frees up — our own last line letting go, a
        desktop sound finishing), then the other outputs. None when we're
        out of ideas. Returns (device, seconds to wait first)."""
        label = output_label(self._on)
        self._complaint = complaint or "nothing, it just stopped"
        self.last_error = f"{label} said: {complaint}" if complaint else f"aplay stopped on {label} without a word"
        log.warning("speaker: %s", self.last_error)
        if len(self._tried) >= 6:
            return None
        low = complaint.lower()
        gone = any(k in low for k in ("no such", "not found", "invalid", "unknown pcm", "cannot find", "no soundcards"))
        if self._on == self._device and not gone and self._tries < 2:
            self._tries += 1
            return self._device, 0.5
        for other in _fallback_outputs(self._device):
            if other not in self._tried:
                return other, 0.0
        return None

    def _play_out(self):
        """Close the pipe and wait for aplay to finish with what it has (or
        for the next line to cut in). Its exit code is the only sure sign
        the sound came out: a short line fits in the pipe before a dead
        aplay is noticed, so a failure can show up only here."""
        while True:
            play = self.play
            if play is None:
                return False, self.last_error or "nothing would play it"
            seconds = self._total / float(max(8000, self.rate) * 2)
            code, complaint = play.finish(seconds + 5, self.wake.is_set)
            if code is None:                 # cut short, or stuck: over either way
                self.play = None
                return (False, "cut short by the next line") if self.wake.is_set() else (True, "")
            if code == 0:
                self.play = None
                return True, ""
            if not self._recover(complaint or f"aplay exit code {code}"):
                return False, self.last_error or "nothing would play it"

    def _through_piper(self, text, asked_at):
        """The line through Piper, a sentence at a time. Each sentence is given
        to it the moment the one before starts to sound, so it makes the next
        while the last one plays: given them one at a time, it sat idle through
        every sentence, and a slow voice paused after each. A voice that makes
        speech more slowly than it talks can't keep up however it's fed, so
        the start is held back (_hold_for) just long enough that, once it
        speaks, it never stops to wait for the next sentence.

        Piper says nothing when a sentence is done: a gap in its sound means
        it is making the next one. The end is a longer gap after the last
        sentence has sounded — in proportion to how slow the voice has been,
        so a late piece of it isn't cut off. Returns "ok", "cut" (a newer line
        came), "silent" (Piper produced nothing) or "noplay" (nothing would
        play it)."""
        sentences = _sentences(text)
        n = len(sentences)
        proc = self._piper()
        if proc is None:
            self.last_error = self.error or "Piper stopped and wouldn't start again"
            return "silent"
        if not self._feed(proc, sentences[0]):
            proc = self._piper(restart=True)
            if proc is None or not self._feed(proc, sentences[0]):
                self.last_error = self.error or "Piper won't take text"
                return "silent"
        t0 = time.time()
        hold = self._hold_for(sentences)
        if hold:
            self._held = bytearray()
        fed_at = [t0]                     # when each sentence was given to Piper
        fed, started = 1, 0               # sentences given to it; how many of them have started to sound
        first, made = 0, 0                # the first sentence this Piper was given, and the bursts it has sent
        bursts = []                       # [when it came, bytes, when Piper could start making it, measured cleanly]
        last, gap, retried, guessed = None, True, False, False
        try:
            while True:
                if self.wake.is_set():
                    return "cut"
                chunk = self._read(self.GAP)
                now = time.time()
                if chunk:
                    if gap:                                    # a new burst: Piper has made a sentence
                        begun = max(fed_at[min(len(bursts), len(fed_at) - 1)], last or t0)
                        bursts.append([now, 0, begun, self._slow_write < begun])
                        made += 1
                        started = min(fed, started + 1)
                        while fed < n and fed - started < 1:   # the next one, made while this one plays
                            self._feed(proc, sentences[fed])   # (a Piper that exits after each line drops it: below)
                            fed_at.append(now)
                            fed += 1
                    gap, last = False, now
                    bursts[-1][1] += len(chunk)
                    if not self._write(chunk):
                        return "noplay"
                    continue
                if chunk is None:                              # Piper exited: some builds do after every line
                    if started >= n:
                        break
                    resume = first + made                      # the ones it made; the rest went with it
                    if made == 0:
                        if retried:                            # it died on this sentence twice: that's all there is
                            if not self._total:
                                self.last_error = self.error or "Piper stopped before it said anything"
                                return "silent"
                            break
                        retried = True                         # once more, fresh
                    else:
                        retried = False
                    if resume >= n:
                        break
                    proc = self._piper(restart=True)
                    if proc is None or not self._feed(proc, sentences[resume]):
                        self.last_error = self.error or "Piper stopped and wouldn't start again"
                        if not self._total:
                            return "silent"
                        break
                    fed_at = fed_at[:resume] + [time.time()]
                    fed, started, first, made, gap = resume + 1, resume, resume, 0, True
                    continue
                if not gap and self._held is None and hasattr(self.play, "flush"):
                    self.play.flush()                          # a page gets the end of a sentence now
                gap = True
                since = now - max(last or t0, fed_at[-1])
                if started < fed:                              # waiting for a sentence to sound
                    if last is None:
                        if since > self.FIRST_SOUND:
                            self.last_error = (f"Piper gave no sound for '{sentences[0][:40]}' in {self.FIRST_SOUND} s"
                                               " — a slow voice on a small Pi? try a 'quick' one")
                            return "silent"
                    elif since > max(2.0, 3 * self._made_in(sentences[min(started, n - 1)])):
                        started, guessed = fed, True           # it came with the one before, too soon to tell apart
                        while fed < n and fed - started < 1:
                            self._feed(proc, sentences[fed])
                            fed_at.append(now)
                            fed += 1
                elif fed >= n and since >= self._tail(bursts, sentences[-1]):
                    break                                      # the last sentence has had its say
                if self._held is not None and self._total and now - t0 >= hold:
                    if not self._release():
                        return "noplay"
        finally:
            self._piper_pending = max(0, fed - started) or (1 if guessed else 0)
        if self._held is not None and not self._release():
            return "noplay"
        if hasattr(self.play, "flush"):
            self.play.flush()
        self._learn(sentences, bursts)
        return "ok"

    @staticmethod
    def _feed(proc, sentence):
        try:
            proc.stdin.write(sentence.encode() + b"\n")
            proc.stdin.flush()
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    # -- how fast the voice is here -----------------------------------------------------

    def _pace_key(self):
        name, length = self.model or (chosen_voice(self._conf), "1.00")
        return f"{name}|{length}"

    def _pace_now(self):
        """(r, spc): the seconds it takes to make a second of speech here, and
        the seconds of speech a character makes — learnt from the lines it has
        made, or guessed from the voice and the machine until then."""
        if self._paces is None:
            try:
                with open(_pace_path()) as fh:
                    self._paces = json.load(fh) or {}
            except (OSError, ValueError):
                self._paces = {}
        key = self._pace_key()
        name, _, length = key.partition("|")
        p = self._paces.get(key) or {}
        r = p.get("r")
        if not r:
            small = 0 < _memory() < 1.2e9
            big = "medium" in name or "high" in name
            r = (1.3 if big else 0.6) if small else (0.4 if big else 0.2)
        try:
            spc = float(p.get("spc") or 0.062 * float(length or 1))
        except ValueError:
            spc = 0.062
        return float(r), spc

    def learnt_pace(self):
        """How many seconds this voice takes to make a second of speech here, once learnt."""
        self._pace_now()
        return ((self._paces or {}).get(self._pace_key()) or {}).get("r")

    def _made_in(self, sentence):
        r, spc = self._pace_now()
        return r * max(0.3, len(sentence) * spc)

    def _hold_for(self, sentences):
        """How long to hold a line back — counted from when Piper is given the
        first sentence — so that once it speaks it never waits for the next
        one; 0 when there's no need. Piper makes them one after another, r
        seconds for each second of speech, and sentence k is needed when the
        ones before it have been said."""
        if len(sentences) < 2:
            return 0.0
        r, spc = self._pace_now()
        r *= 1.1                                             # a little to spare: it isn't always as quick
        need = made = said = 0.0
        first = None
        for s in sentences:
            d = max(0.3, len(s) * spc)
            made += r * d
            first = made if first is None else first
            need = max(need, made - said)
            said += d
        return min(self.HOLD_MAX, need + 0.15) if need > first + 0.1 else 0.0

    def _tail(self, bursts, sentence):
        """After the last sentence has sounded, how long a quiet means the line
        is done. Its sound as long as its words say it should be: done. Short
        of that, a slow voice may still be sending it in pieces: wait longer,
        in proportion to how slow it has been."""
        if not bursts:
            return 1.0
        _, spc = self._pace_now()
        if bursts[-1][1] / (2.0 * self.rate) >= 0.7 * len(sentence) * spc:
            return 0.4
        return min(6.0, max(1.0, 2 * (bursts[-1][0] - bursts[-1][2])))

    def _learn(self, sentences, bursts):
        """How fast the voice made this line, when each sentence came as one burst: kept, and saved."""
        if len(bursts) != len(sentences):
            return
        rs, secs, chars = [], 0.0, 0
        for s, (came, size, begun, clean) in zip(sentences, bursts):
            d = size / (2.0 * self.rate)
            if d < 0.25:
                continue
            secs += d
            chars += len(s)
            if clean:
                rs.append((came - begun) / d)
        if not chars:
            return
        key = self._pace_key()
        old = (self._paces or {}).get(key) or {}
        spc = secs / chars
        p = {"spc": round(0.5 * old["spc"] + 0.5 * spc if old.get("spc") else spc, 4)}
        if rs:
            rs.sort()
            r = rs[len(rs) // 2]
            # slower than thought: believed at once (a pause is worse than a longer wait); faster: bit by bit
            w = 0.8 if r > old.get("r", 0) else 0.3
            p["r"] = round((1 - w) * old["r"] + w * r if old.get("r") else r, 3)
        elif old.get("r"):
            p["r"] = old["r"]
        self._paces = dict(self._paces or {}, **{key: p})
        self.pace = p
        try:
            os.makedirs(TTS_CACHE, exist_ok=True)
            tmp = _pace_path() + ".part"
            with open(tmp, "w") as fh:
                json.dump(self._paces, fh)
            os.replace(tmp, _pace_path())
        except OSError as e:
            log.debug("couldn't keep the voice's pace: %s", e)

    # -- saying the same thing twice ------------------------------------------------

    def _cached(self, text):
        """The audio for this exact line, if we have said it before."""
        name = chosen_voice(self._conf)
        path = _cache_path(name, self._conf.get("piper_speed", 1.0), text)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            if len(data) < 2000:                 # a stub from a cut-off line isn't worth repeating
                return None
            os.utime(path, None)                 # keep the ones you use
            log.debug("saying a line we already have (%s bytes)", len(data))
            return data
        except OSError:
            return None

    def _remember(self, text, audio_bytes):
        if len(audio_bytes) < 2000 or len(audio_bytes) > 8_000_000:
            return
        name = chosen_voice(self._conf)
        path = _cache_path(name, self._conf.get("piper_speed", 1.0), text)
        try:
            os.makedirs(TTS_CACHE, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(audio_bytes)
            os.replace(tmp, path)
            _cache_trim()
        except OSError as e:
            log.debug("couldn't keep that line: %s", e)

    def _play_bytes(self, data, asked_at=None):
        """Straight to the speaker, no synthesis at all."""
        for i in range(0, len(data), 8192):
            if self.wake.is_set():
                return "cut"
            if not self._write(data[i:i + 8192]):
                return "noplay"
        return "ok"

    def _rewarm(self):
        """Some builds of Piper exit after each line. Load the next one now, so
        the wait lands here rather than on your next press."""
        with self.lock:
            if self.proc and self.proc.poll() is not None and not self.pending and self._conf:
                log.debug("piper exited after speaking; loading it again now")
                self._spawn(self._conf)

    # -- what the rest of the app calls --------------------------------------------

    def say(self, text, conf, wait=False, timeout=90):
        """Queue a line. With wait, block until it has been played (or
        couldn't be) and return what happened. While a page is playing
        PiE-ink's sound, it goes there instead of the output you picked."""
        self._device = here.OUTPUT if here.HERE.listening() else (conf.get("device") or "").strip()
        okay, why = self.ensure(conf)
        if not okay:
            return False, why
        with self.cv:
            self.pending = text
            self.ticket += 1
            ticket = self.ticket
            self.wake.set()                   # the worker stops the old line and starts this one
        if not wait:
            return True, "speaking"
        with self.cv:
            if not self.cv.wait_for(lambda: self.finished >= ticket, timeout):
                return True, "still speaking"
            if self.finished > ticket:
                return False, "cut short by a newer line"
            return self.outcome

    def stop(self):
        with self.lock:
            self.pending = None
            self.wake.set()                   # the worker drops the line in flight
        self._kill_play()
        if here.HERE.listening():
            here.HERE.hush()                  # and a page stops whatever it still has


SPEAKER = _Speaker()


def warm_up(conf):
    """Load the voice at boot so the first press is as quick as the rest,
    and put the volume where it was left."""
    try:
        set_volume(int(conf.get("volume", 80) or 80), (conf.get("device") or "").strip())
    except Exception:
        log.debug("volume at boot", exc_info=True)
    if chosen_voice(conf):
        SPEAKER._device = (conf.get("device") or "").strip()
        return SPEAKER.ensure(conf)
    return False, "no voice chosen yet"


_espeak_busy = threading.Event()


def _espeak_through(text, voice, rate, amp, device, timeout=120):
    """espeak-ng into aplay on one output. (ok, what aplay said if not)."""
    speak = subprocess.Popen(["espeak-ng", "-v", voice, "-s", str(rate), *amp, "--stdout", text],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryFile() as err:
        play = subprocess.Popen(["aplay", "-q", *(["-D", device] if device else [])],
                                stdin=speak.stdout, stderr=err)
        speak.stdout.close()
        try:
            play.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            play.kill()
        speak.wait()
        if play.returncode == 0:
            return True, ""
        try:
            err.seek(0)
            lines = [ln.strip() for ln in err.read().decode(errors="replace").splitlines() if ln.strip()]
        except (OSError, ValueError):
            lines = []
        return False, _aplay_words(lines[-1]) if lines else f"aplay exit code {play.returncode}"


def _wav_pcm(wav):
    """(pcm, rate) from a WAV file's bytes. espeak-ng's --stdout header can't
    know the length yet, so the sizes in it aren't trusted: the samples are
    whatever follows the "data" tag."""
    rate = int.from_bytes(wav[24:28], "little") if wav[:4] == b"RIFF" and len(wav) >= 28 else 22050
    at = wav.find(b"data", 12)
    pcm = wav[at + 8:] if at >= 0 else wav[44:]
    return pcm[:len(pcm) - len(pcm) % 2], rate or 22050


def _espeak_here(text, conf, wait=False):
    """The plain voice, to the page playing PiE-ink's sound: espeak-ng makes
    the line, the page plays it."""
    voice = (conf.get("voice") or "en-gb").strip()
    rate = int(conf.get("rate", 160) or 160)
    result = {"ok": True, "message": "said it"}

    def run():
        _espeak_busy.set()
        try:
            r = subprocess.run(["espeak-ng", "-v", voice, "-s", str(rate), "--stdout", text],
                               capture_output=True, timeout=120)
            pcm, sr = _wav_pcm(r.stdout)
            if not pcm:
                result.update(ok=False, message="espeak produced no audio")
                return
            here.HERE.say(pcm, sr, here.HERE.new_line())
            time.sleep(_HerePlay.LAG + len(pcm) / (2.0 * sr))     # about as long as the page takes to play it
            SPEAKER.last_error = None
            result.update(ok=True, message=f"played through {output_label(here.OUTPUT)}")
        except (OSError, subprocess.SubprocessError) as e:
            result.update(ok=False, message=f"the plain voice failed: {str(e)[:100]}")
        finally:
            _espeak_busy.clear()

    if wait:
        run()
        return result["ok"], result["message"]
    threading.Thread(target=run, daemon=True, name="speak-espeak").start()
    return True, "speaking"


def _say_espeak(text, conf, wait=False):
    """The plain robot voice — always there, and what we fall back to. Like
    the natural one, it tries the other outputs when the chosen one refuses,
    and says where the sound went."""
    if here.HERE.listening():
        return _espeak_here(text, conf, wait)
    device = (conf.get("device") or "").strip()
    voice = (conf.get("voice") or "en-gb").strip()
    rate = int(conf.get("rate", 160) or 160)
    result = {"ok": True, "message": "said it"}

    def run():
        _espeak_busy.set()
        try:
            amp = ["-a", str(max(1, int(100 * _soft["gain"])))] if _soft["gain"] < 0.999 else []
            if not have("aplay"):
                subprocess.run(["espeak-ng", "-v", voice, "-s", str(rate), *amp, text],
                               stderr=subprocess.DEVNULL, timeout=120)
                return
            note = ""
            for out in [device, *_fallback_outputs(device)]:
                okay, why = _espeak_through(text, voice, rate, amp, out)
                if okay:
                    SPEAKER.last_error = note or None
                    result.update(ok=True, message=f"played through {output_label(out)}" + (f" instead — {note}" if note else ""))
                    return
                note = f"{output_label(out)} said: {why}"
                log.warning("speaker (plain voice): %s", note)
            SPEAKER.last_error = note
            result.update(ok=False, message=note or "nothing would play it")
        except Exception as e:
            log.warning("espeak failed: %s", e)
            result.update(ok=False, message=f"the plain voice failed: {str(e)[:100]}")
        finally:
            _espeak_busy.clear()

    if wait:
        run()
        return result["ok"], result["message"]
    threading.Thread(target=run, daemon=True, name="speak-espeak").start()
    return True, "speaking"


def is_speaking():
    return bool(SPEAKER.speaking or SPEAKER.pending or _espeak_busy.is_set())


def wait_quiet(timeout=60):
    """Block until the speaker has finished, so two Pis take turns."""
    end = time.time() + timeout
    time.sleep(0.3)                         # let the line start
    while time.time() < end and is_speaking():
        time.sleep(0.2)


def machine_memory():
    """Total RAM in bytes, so we can warn about heavy voices on a small Pi."""
    try:
        import psutil
        return psutil.virtual_memory().total
    except Exception:
        return 0


def voice_advice(conf):
    """A word about speed, when the machine is small and the voice is big."""
    name = chosen_voice(conf)
    if not name:
        return ""
    total = machine_memory()
    if total and total < 1.2e9 and ("medium" in name or "high" in name):
        return ("This Pi has little memory, and a medium voice is slow here — "
                "a 'quick' voice speaks in a fraction of the time.")
    return ""


# -- making a file of it ------------------------------------------------------------

def _synthesise(text, conf, timeout=180):
    """Raw 16-bit audio for this line, from the cache if we have it. Returns
    (pcm, sample_rate)."""
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("nothing to say")
    name = chosen_voice(conf)
    if name and piper_cmd():
        cached = _cache_path(name, conf.get("piper_speed", 1.0), text)
        try:
            with open(cached, "rb") as fh:
                data = fh.read()
            if data:
                return data, _voice_rate(name)
        except OSError:
            pass
        speed = float(conf.get("piper_speed", 1.0) or 1.0)
        length = f"{1 / max(0.5, min(2.0, speed)):.2f}"
        model, _ = voice_files(name)
        for raw_flag in ("--output-raw", "--output_raw"):
            try:
                r = subprocess.run([*piper_cmd(), "-m", model, raw_flag, "--length-scale", length],
                                   input=text.encode() + b"\n", capture_output=True, timeout=timeout)
            except (OSError, subprocess.SubprocessError) as e:
                log.info("piper wouldn't run for the recording: %s", e)
                break
            if r.stdout:
                return r.stdout, _voice_rate(name)
            log.info("piper gave nothing back (%s)", (r.stderr or b"")[:120])
        log.info("recording with the plain voice instead")

    if not have("espeak-ng"):
        raise ValueError("no voice installed to record with")
    r = subprocess.run(["espeak-ng", "-v", (conf.get("voice") or "en-gb"),
                        "-s", str(int(conf.get("rate", 160) or 160)), "--stdout", text],
                       capture_output=True, timeout=timeout)
    wav = r.stdout
    if len(wav) < 45:
        raise ValueError("espeak produced no audio")
    return wav[44:], 22050          # drop the wav header, espeak's default rate


def encoder():
    """Whatever can write an MP3 here."""
    if shutil.which("lame"):
        return "lame"
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return ""


def _write_wav(pcm, rate, path):
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def to_mp3(text, conf, path):
    """Speak a line into a file. MP3 when the Pi can encode one, a WAV
    otherwise — better a file you can play than an error. Returns (ok, path,
    message)."""
    try:
        pcm, rate = _synthesise(text, conf)
    except ValueError as e:
        return False, path, str(e)
    tool = encoder()
    if not tool:
        wav = os.path.splitext(path)[0] + ".wav"
        try:
            _write_wav(pcm, rate, wav)
        except Exception as e:
            return False, path, f"couldn't write the audio: {str(e)[:100]}"
        return True, wav, "saved a WAV — for MP3, install lame: sudo apt install lame"
    if tool == "lame":
        cmd = ["lame", "--quiet", "-r", "-s", f"{rate / 1000:g}", "--bitwidth", "16",
               "--signed", "--little-endian", "-m", "m", "-h", "-", path]
    else:
        cmd = ["ffmpeg", "-loglevel", "quiet", "-y", "-f", "s16le", "-ar", str(rate),
               "-ac", "1", "-i", "pipe:0", "-codec:a", "libmp3lame", "-q:a", "5", path]
    try:
        r = subprocess.run(cmd, input=pcm, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return False, path, f"couldn't encode it: {str(e)[:100]}"
    if r.returncode != 0 or not os.path.exists(path):
        return False, path, (r.stderr or b"").decode(errors="replace")[:140] or "the encoder failed"
    return True, path, "ready"


def voice_in_use(conf):
    """Which voice will actually speak, and why — for the page to show."""
    ready, why = piper_ready(conf)
    if ready:
        return "piper", f"Natural voice: {chosen_voice(conf)}"
    if have("espeak-ng"):
        return "espeak", f"Plain voice — {why}"
    return "none", why + "; espeak-ng isn't installed either"


def say(text, conf, wait=False):
    """Speak a line, with the natural voice if it's set up and the plain one
    if not. Interrupts whatever is being said. With wait, comes back once the
    line has played (or couldn't), saying which output it went to or why
    not; without, straight away with "speaking"."""
    text = " ".join((text or "").split())
    if not text:
        return False, "nothing to say"
    ready, why = piper_ready(conf)
    if ready:
        return SPEAKER.say(text, conf, wait=wait)
    if have("espeak-ng"):
        log.info("using the plain voice: %s", why)
        SPEAKER.stop()                       # nothing else should be talking
        return _say_espeak(text, conf, wait)
    log.warning("nothing to speak with: %s", why)
    return False, why


def stop_speaking():
    SPEAKER.stop()


def last_error():
    """Why the last line didn't come out (or came out on another output), or None."""
    return SPEAKER.last_error


# -- why isn't my speaker (or microphone) found? ----------------------------------------------------

# what the kernel says when a USB device can't be brought up, in plain words
USB_TROUBLE = (
    ("device descriptor read", "it tried to talk to a device and got no answer"),
    ("not accepting address", "a device wouldn't take an address"),
    ("unable to enumerate", "it gave up on a device"),
    ("cannot enable", "a port wouldn't switch on (a bad cable, or not enough power)"),
    ("over-current", "a port was switched off for drawing too much current"),
    ("overcurrent", "a port was switched off for drawing too much current"),
    ("insufficient available bus power", "a device wanted more power than the hub may give it"),
    ("disabled by hub", "the hub switched a port off"),
    ("undervoltage", "the power supply sagged"),
    ("under-voltage", "the power supply sagged"),
)


def throttled():
    """The Pi firmware's power flags: {"now", "since_boot", "code"}, or None when it won't say."""
    from .camera import _read
    raw = _read("/sys/devices/platform/soc/soc:firmware/get_throttled")
    if not raw and have("vcgencmd"):
        try:
            raw = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                                 timeout=3).stdout.strip().split("=")[-1]
        except (OSError, subprocess.SubprocessError):
            raw = ""
    try:
        v = int(raw, 16)
    except (TypeError, ValueError):
        return None
    return {"now": bool(v & 0x1), "since_boot": bool(v & 0x10000), "code": hex(v)}


def sound_cards():
    """The kernel's sound cards: [{"index", "id", "driver", "name", "long", "usb"}] — usb is the
    USB device's "vid:pid" for a USB one."""
    from .camera import _read
    out = []
    lines = _read("/proc/asound/cards").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]:\s*(\S+)\s+-\s+(.*)$", line)
        if not m:
            continue
        index = int(m.group(1))
        long = lines[i + 1].strip() if i + 1 < len(lines) else ""
        out.append({"index": index, "id": m.group(2), "driver": m.group(3), "name": m.group(4).strip(), "long": long,
                    "usb": _read(f"/proc/asound/card{index}/usbid") or None})
    return out


def _usb_history(lines):
    """What the kernel log says came and went: {port: {"name", "gone"}} for USB devices it named."""
    seen = {}
    for line in lines:
        m = re.search(r"usb (\d+-[\d.]+): (.*)$", line)
        if not m:
            continue
        port, what = m.groups()
        if what.startswith("Product: "):
            seen[port] = {"name": what[9:].strip(), "gone": False}
        elif "USB disconnect" in what and port in seen:
            seen[port]["gone"] = True
    return seen


def probe(conf=None):
    """Everything that bears on "my USB speaker / microphone isn't listed", with a
    plain verdict on top: the USB bus, the sound cards, what's picked, the Pi's
    power, and what the kernel said about USB."""
    from . import camera
    conf = conf or {}
    model = camera._read("/proc/device-tree/model").rstrip("\0")
    root = "/sys/bus/usb/devices"
    usb = []
    for u in camera.usb_devices():
        d = os.path.join(root, u["bus"])
        attrs, ports = camera._read(os.path.join(d, "bmAttributes")), camera._read(os.path.join(d, "maxchild"))
        usb.append(dict(u, ports=int(ports) if ports.isdigit() else 0,
                        product=camera._read(os.path.join(d, "product")) or u["name"],
                        self_powered=bool(int(attrs, 16) & 0x40) if re.fullmatch(r"[0-9a-fA-F]+", attrs or "") else None))
    by_id = {f"{u['vid']}:{u['pid']}": u for u in usb}
    cards = sound_cards()
    for c in cards:
        dev = by_id.get((c["usb"] or "").lower())
        c["camera"] = bool(dev and "video" in dev["classes"])
    outs, ins = entries("aplay", fresh=True), entries("arecord", fresh=True)
    kernel, kernel_note = camera._kernel_lines(limit=80, keys=("usb", "snd", "audio", "voltage", "current", "hub"))
    trouble, failed_ports, codes, speeds, driver, last = [], [], set(), {}, "", {}
    for line in kernel:
        low = line.lower()
        m = re.search(r"usb (\d+-[\d.]+)-port(\d+)", line)                  # "usb 1-1-port4: …" is device 1-1.4
        port = f"{m.group(1)}.{m.group(2)}" if m else (re.search(r"usb (\d+-[\d.]+):", line) or [None, None])[1]
        m = re.search(r"new (\w+)-speed USB device number \d+ using (\w+)", line)
        if m and port:
            speeds[port], driver = m.group(1), m.group(2)
        if port and ("new usb device found" in low or ": product: " in low):
            last[port] = "ok"                                                # it came up
        for key, meaning in USB_TROUBLE:
            if key not in low:
                continue
            if meaning not in trouble:
                trouble.append(meaning)
            if "volt" not in key and port:
                last[port] = "fail"
                if port not in failed_ports:
                    failed_ports.append(port)
            codes.update(int(c) for c in re.findall(r"error -(\d+)", line))
    failed_ports = [p for p in failed_ports if last.get(p) == "fail"]    # still failing, as far as the log goes
    power = throttled()
    power_bad = bool(power and (power["now"] or power["since_boot"])) or any(
        "undervoltage detected" in ln.lower() or "under-voltage detected" in ln.lower() for ln in kernel)
    history = _usb_history(kernel)
    here = {u["bus"] for u in usb}
    failed_ports = [p for p in failed_ports if p not in here]            # one that came up after all isn't failing
    # what's on the bus now that has dropped off and come back: the hub's power, not any one device
    dropped = {}
    for line in kernel:
        m = re.search(r"usb (\d+-[\d.]+): USB disconnect", line)
        if m and m.group(1) in here:
            dropped[m.group(1)] = dropped.get(m.group(1), 0) + 1
    flaky = [(next((u["name"] for u in usb if u["bus"] == p), p), n) for p, n in dropped.items()]
    # dropped off and not back — by name: its port may have another device in it now
    names_here = " ".join(f"{u['name']} {u.get('product', '')}" for u in usb).lower()
    gone = [f"{h['name']} ({port})" for port, h in history.items() if h["gone"] and h["name"].lower() not in names_here]
    old_pi = model.startswith("Raspberry Pi") and not re.search(r"Pi (4|5|400|500)\b|Compute Module (4|5)", model)
    overlay = _dwc2_overlay() if old_pi else None
    bits64 = _kernel_64()                     # then dwc2 is the USB driver there is: never say to take it out
    st = camera.CAMERA.status()
    streaming = bool(st.get("running")) and str(st.get("id") or "").startswith("usb:")
    own = [u for u in usb if "audio" in u["classes"] and "video" not in u["classes"]]
    own_cards = [c for c in cards if c["driver"] == "USB-Audio" and not c["camera"]]
    cam_cards = [c for c in cards if c["camera"]]
    zero = "zero" in model.lower()
    hubs = [u for u in usb if "hub" in u["classes"]]
    for h in hubs:
        h["in_use"] = sum(1 for u in usb if u is not h and "." in u["bus"] and u["bus"].rsplit(".", 1)[0] == h["bus"])
    cams = [u for u in usb if "video" in u["classes"]]

    def picked(key, listed):
        value = ((conf.get(key) or {}).get("device") or "").strip()
        if not value:
            return {"value": "", "label": "System default", "here": True}
        match = next((x for x in listed if value in (x["id"], x.get("alias"))), None)
        return {"value": value, "label": match["label"] if match else None, "here": bool(match)}
    out_pick, in_pick = picked("audio", outs), picked("listen", ins)

    crowded = False                               # the camera's video may be leaving no room: offer to pause it
    said = []
    if not own:
        said.append("The Pi doesn't see a USB speaker or microphone at all, so there's nothing for the settings to list.")
        if failed_ports:
            errs = ", ".join(f"error -{c}" for c in sorted(codes))
            said.append(f"It tried to bring up what's on {_ports_words(failed_ports)} and couldn't"
                        + (f" ({errs})" if errs else "") + " — "
                        + ("probably your speaker and mic." if len(failed_ports) > 1 else "probably your speaker or mic."))
        if gone:
            said.append(" and ".join(g.split(" (")[0] for g in gone[:2]) + " came up earlier and dropped off.")
        if power_bad:
            said.append("Its power supply has sagged (under-voltage), and that's enough to cause it: put the speaker, "
                        "mic" + (" and camera" if cams else "") + " on a USB hub with its own power adapter.")
        elif failed_ports and cams:
            crowded = True
            small = [speeds[p] for p in failed_ports if p in speeds]
            said.append("The Pi's own power is fine. "
                        + ("Small full-speed devices like these" if small and all(s in ("full", "low") for s in small)
                           else "Devices like these")
                        + " have to squeeze in beside the camera's video" + (" on the Zero's one USB link" if zero else "")
                        + (", and the dwc2 driver this Pi uses is worse at that than the Pi's own."
                           if driver == "dwc2" and old_pi and not bits64 else "."))
            if flaky:
                said.append("What is on the hub has been dropping off and coming back too ("
                            + ", ".join(f"{name} {'once' if n == 1 else f'{n} times'}" for name, n in flaky[:3])
                            + "), which points at the hub's power rather than at the speaker or mic"
                            + (": the hub has no power of its own, and the camera's motors draw bursts of current."
                               if any(h.get("self_powered") is False for h in hubs) else
                               ": the camera's motors draw bursts of current, more than a Zero passes on through an "
                               "unpowered hub." if zero else "."))
            elif any(h.get("self_powered") is False for h in hubs):
                said.append("The hub has no power of its own, though, and the camera's motors draw bursts of current "
                            "when it turns: a hub with its own power adapter is worth trying first.")
            said.append("To tell for sure: unplug the camera" + (" and the GPS" if any("comms" in u["classes"] for u in usb) else "")
                        + ", plug only the speaker in, and press Watch the USB bus (below) — or plug the speaker "
                        "straight into the Pi's adapter, with no hub. Comes up alone: it's the load on the hub, and a hub "
                        "with its own power adapter fixes it. Doesn't, even alone: try another cable, and the speaker on "
                        "another computer.")
            said.append("Pausing the camera (the button below) lets you try it beside the camera without restarting.")
            if overlay and not bits64:
                said.append(f"If they come up only with the camera paused, take “{overlay[1]}” out of {overlay[0]} and "
                            "restart, unless you use this Pi as a USB gadget.")
            elif overlay:
                said.append(f"Keep “{overlay[1]}” in {overlay[0]}: on a 64-bit system it's the USB driver that works.")
        elif failed_ports:
            said.append("The Pi's own power is fine: try another port or cable, and without the hub if there is one.")
        else:
            said.append("There's nothing about them in the kernel log, so they may not be getting power or data at "
                        "all: try another cable (some only charge) and another port, and see if the speaker lights up.")
        if cam_cards:
            said.append(f"Meanwhile the camera's own microphone works for listening ({cam_cards[0]['name']}, under "
                        "Microphone).")
        said.append("Meanwhile, Play on this device (above) plays its voice and music on your phone or computer.")
    elif not own_cards:
        loaded = os.path.exists("/sys/module/snd_usb_audio")
        said.append(f"{own[0]['name']} is on the USB bus, but no sound card was made for it: "
                    + ("the USB sound driver is loaded but didn't take it — unplug it and plug it in again."
                       if loaded else "the USB sound driver isn't loaded (sudo modprobe snd-usb-audio)."))
    else:
        mine = {c["id"] for c in own_cards}
        spk = [x["label"] for x in outs if card_of(x["id"]) in mine]
        mic = [x["label"] for x in ins if card_of(x["id"]) in mine]
        found = [f"a speaker ({', '.join(spk)})" if spk else "", f"a microphone ({', '.join(mic)})" if mic else ""]
        if spk or mic:
            said.append("Found " + " and ".join(x for x in found if x) + ".")
        else:
            said.append(f"Found {own[0]['name']}, but it has no speaker or microphone the Pi can use.")
    missing = [where for p, where in ((out_pick, "Output"), (in_pick, "Microphone")) if p["value"] and not p["here"]]
    if missing:
        said.append(f"Then pick {'them' if len(missing) > 1 else 'it'} again under {' and '.join(missing)}: the "
                    f"{'ones' if len(missing) > 1 else 'one'} picked before went by card number, and the numbers moved "
                    "(now they're kept by name).")
    return {"model": model, "usb": usb, "cards": cards, "outputs": outs[1:], "inputs": ins[1:], "picked": {
        "output": out_pick, "microphone": in_pick}, "power": power, "kernel": kernel[-25:], "kernel_note": kernel_note,
        "trouble": trouble, "gone": gone, "streaming": streaming, "hubs": hubs, "failed": failed_ports,
        "speeds": speeds, "codes": sorted(codes), "driver": driver, "overlay": overlay, "crowded": crowded,
        "bits64": bits64, "camera_paused_for": camera.CAMERA.paused_for(), "verdict": " ".join(said)}


def _ports_words(ports):
    """["1-1.1", "1-1.4"] -> "ports 1 and 4 of the hub"; a port straight on the Pi says so."""
    on_hub = sorted((p.rsplit(".", 1)[1] for p in ports if "." in p), key=lambda n: int(n) if n.isdigit() else 99)
    direct = [p for p in ports if "." not in p]
    words = []
    if on_hub:
        words.append(("port " if len(on_hub) == 1 else "ports ") + " and ".join(on_hub) + " of the hub")
    if direct:
        words.append("the Pi's own USB port")
    return " and ".join(words)


def _kernel_64():
    """Is the kernel 64-bit? On one, the Pi's own USB driver (dwc_otg) doesn't work and dwc2 is the one to use
    (raspberrypi/linux#6883: "The downstream dwc-otg driver does not work in 64-bit mode; dwc2 should be used
    instead"), so dtoverlay=dwc2 must stay."""
    try:
        return os.uname().machine in ("aarch64", "arm64")
    except AttributeError:
        return False


def _dwc2_overlay():
    """(file, line) where config.txt switches this Pi to the dwc2 USB driver — in a part of it that applies
    to this Pi ([all], [pi0], [pi02]…), not the [cm5] lines a stock one has — or None."""
    from .camera import _read
    for path in ("/boot/firmware/config.txt", "/boot/config.txt"):
        text = _read(path)
        if not text:
            continue
        applies = True
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"\[([^\]]*)\]", line)
            if m:
                applies = m.group(1).strip().lower() in ("all", "pi0", "pi02", "pi0w", "pi1", "pi2", "pi3", "pi3+")
                continue
            if applies and re.match(r"dtoverlay\s*=\s*dwc2\b", line):
                return path, line
        return None
    return None


def probe_text(report):
    r = report
    lines = [r["verdict"], "", f"Pi: {r['model'] or 'unknown'}" + (", 64-bit" if r.get("bits64") else "")]
    p = r["power"]
    lines.append("Power: " + ("can't tell" if p is None else ("SHORT OF POWER NOW" if p["now"] else
                 "short of power at some point since it started" if p["since_boot"] else "fine") + f" ({p['code']})"))
    if r.get("driver"):
        lines.append(f"USB driver: {r['driver']}" + (f" (from “{r['overlay'][1]}” in {r['overlay'][0]})" if r.get("overlay") else ""))
    if r.get("failed"):
        lines.append("Wouldn't come up: " + ", ".join(
            p + (f" ({r['speeds'][p]}-speed)" if p in r.get("speeds", {}) else "") for p in r["failed"])
            + (" — " + ", ".join(f"error -{c}" for c in r.get("codes", [])) if r.get("codes") else ""))
    lines += ["", f"USB devices ({len(r['usb'])}):"]
    for u in r["usb"]:
        extra = ""
        if "hub" in u["classes"] and u.get("ports"):
            extra = f"  {u['ports']} ports, {u.get('in_use', 0)} in use" + (
                "" if u.get("self_powered") is None else ", powered" if u["self_powered"] else ", no power of its own")
        lines.append(f"  {u['bus']:<8} {u['vid']}:{u['pid']}  {u['name']}  [{', '.join(u['classes']) or '?'}]"
                     + (f"  wants {u['power_ma']}" if u.get("power_ma") else "") + extra)
    if not r["usb"]:
        lines.append("  (nothing but the Pi itself)")
    lines += ["", f"Sound cards ({len(r['cards'])}):"]
    for c in r["cards"]:
        kind = "the camera's microphone" if c["camera"] else "USB" if c["driver"] == "USB-Audio" else "built in"
        lines.append(f"  {c['index']}  {c['id']:<14} {c['name']}  ({kind})")
    if not r["cards"]:
        lines.append("  (none)")
    for what, key in (("Speakers", "outputs"), ("Microphones", "inputs")):
        lines.append(f"{what}: " + (", ".join(x["label"] for x in r[key]) or "none"))
    for what, key in (("Output picked", "output"), ("Microphone picked", "microphone")):
        pk = r["picked"][key]
        if pk["here"]:
            lines.append(f"{what}: {pk['label']}" + (f" ({pk['value']})" if pk["value"] else ""))
        else:
            lines.append(f"{what}: {pk['value']} — NOT HERE NOW")
    if r["gone"]:
        lines.append("Seen earlier, gone now: " + ", ".join(r["gone"]))
    lines += ["", "Kernel log (USB, sound, power):"]
    lines += [f"  {ln}" for ln in r["kernel"]] or [f"  {r['kernel_note'] or '(nothing about USB)'}"]
    return "\n".join(lines)
