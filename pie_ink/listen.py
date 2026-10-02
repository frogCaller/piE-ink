"""Listening for a wake phrase on a USB microphone.

Audio comes from arecord and goes into Vosk, which recognises speech offline
on the Pi. Nothing is sent anywhere: the recogniser runs locally and only the
words after your wake phrase are handed to the bot.

The loop is deliberately conservative — it ignores everything until it hears
the phrase, stops listening while the Pi is speaking (so it can't answer
itself), and gives up on a command after a few quiet seconds.
"""
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile

import requests

from .settings import DATA_DIR

log = logging.getLogger(__name__)
MODEL_DIR = os.path.join(DATA_DIR, "stt")
RATE = 16000
MODELS = [
    {"id": "vosk-model-small-en-us-0.15", "label": "English, small (40 MB) — best on a Pi",
     "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"},
    {"id": "vosk-model-en-us-0.22-lgraph", "label": "English, larger (128 MB) — more accurate",
     "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip"},
]
_install = {"running": False, "log": "", "ok": None}
_fetch_state = {"running": False, "message": "", "ok": None}


def have_vosk():
    import importlib.util
    try:
        return importlib.util.find_spec("vosk") is not None
    except Exception:
        return False


_mics = {"at": 0.0, "list": None}


def mic_devices(refresh=False):
    """Capture devices from `arecord -l` — asked at most every few seconds,
    since status polls and the Whisplay's LED ask often."""
    if not refresh and _mics["list"] is not None and time.time() - _mics["at"] < 10:
        return list(_mics["list"])
    from . import audio                       # the same naming as the speakers: by card name, not number
    out = audio.entries("arecord", fresh=refresh)
    _mics.update(at=time.time(), list=list(out))
    return out


def installed_models():
    try:
        names = sorted(d for d in os.listdir(MODEL_DIR)
                       if os.path.isdir(os.path.join(MODEL_DIR, d, "am"))
                       or os.path.isdir(os.path.join(MODEL_DIR, d, "graph")))
    except OSError:
        return []
    known = {m["id"]: m["label"] for m in MODELS}
    return [{"id": n, "label": known.get(n, n)} for n in names]


def model_path(name):
    return os.path.join(MODEL_DIR, name)


def chosen_model(conf=None):
    """The model to use: the one you picked, or the only one there is."""
    name = ((conf or {}).get("model") or "").strip()
    if name and os.path.isdir(model_path(name)):
        return name
    found = installed_models()
    return found[0]["id"] if found else ""


def install_status():
    return {**_install, "ready": have_vosk()}


def install_vosk():
    """pip install vosk in the background."""
    if _install["running"]:
        return install_status()
    _install.update(running=True, log="", ok=None)

    def run():
        try:
            p = subprocess.Popen(["pip3", "install", "--break-system-packages", "vosk"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                _install["log"] = (_install["log"] + line)[-4000:]
            _install["ok"] = p.wait() == 0
        except Exception as e:
            _install["ok"] = False
            _install["log"] += f"\n{e}"
        finally:
            _install["running"] = False
            log.info("vosk install finished: ok=%s", _install["ok"])

    threading.Thread(target=run, daemon=True, name="vosk-install").start()
    return install_status()


def fetch_state():
    return dict(_fetch_state)


def download_model(name_or_url):
    """Fetch and unpack a Vosk model in the background."""
    if _fetch_state["running"]:
        return _fetch_state
    spec = next((m for m in MODELS if m["id"] == name_or_url), None)
    url = spec["url"] if spec else name_or_url
    if not url.startswith(("http://", "https://")) or not url.endswith(".zip"):
        return {**_fetch_state, "ok": False, "message": "link me to a model .zip"}
    _fetch_state.update(running=True, ok=None, message="downloading…")

    def run():
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp = os.path.join(MODEL_DIR, "download.zip")
        try:
            with requests.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                size = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        fh.write(chunk)
                        done += len(chunk)
                        if size:
                            _fetch_state["message"] = f"downloading… {done * 100 // size}%"
            _fetch_state["message"] = "unpacking…"
            with zipfile.ZipFile(tmp) as z:
                z.extractall(MODEL_DIR)
            os.remove(tmp)
            _fetch_state.update(ok=True, message="the model is ready")
        except Exception as e:
            _fetch_state.update(ok=False, message=f"failed: {str(e)[:110]}")
            try:
                os.remove(tmp)
            except OSError:
                pass
        finally:
            _fetch_state["running"] = False

    threading.Thread(target=run, daemon=True, name="vosk-model").start()
    return _fetch_state


def _words(text):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


class Ears:
    """Listens for the wake phrase, then hands what follows to a callback."""

    def __init__(self):
        self.thread = None
        self.stop_flag = threading.Event()
        self.state = "off"            # off | waiting | listening | thinking | error
        self.heard = ""
        self.error = None
        self.conf = {}
        self.partial = ""          # what it is hearing right now, for the page
        self.level = 0             # rough loudness, so you can see the mic working
        self.on_command = None
        self.is_busy = lambda: False   # set by the app: True while the Pi is talking
        self._poke = False             # a button asked it to listen right now

    # -- lifecycle -------------------------------------------------------------------

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, conf, on_command, is_busy=None):
        self.conf = conf or {}
        self.on_command = on_command
        if is_busy:
            self.is_busy = is_busy
        if self.running():
            return True, "already listening"
        def refuse(why):
            self.state, self.error = "error", why      # so the page can say why
            return False, why
        if not have_vosk():
            return refuse("the listener isn't installed yet — install it below")
        name = chosen_model(self.conf)
        if not name:
            return refuse("no speech model yet — download one below")
        self.conf = {**self.conf, "model": name}
        if not shutil.which("arecord"):
            return refuse("arecord is missing — run ./setup.sh on the Pi")
        if not [d for d in mic_devices() if d["id"]]:
            return refuse("no microphone found — is it plugged in?")
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="ears")
        self.thread.start()
        return True, "listening"

    def stop(self):
        self.stop_flag.set()
        self.state = "off"

    def wake_now(self):
        """Listen for what comes next as if the wake phrase had just been
        said — for a button. True if the ears are running."""
        if not self.running():
            return False
        self._poke = True
        return True

    def status(self):
        wake = (self.conf.get("wake") or "").strip()
        return {"state": self.state, "heard": self.heard, "error": self.error,
                "running": self.running(), "wake": wake, "always": not wake,
                "partial": self.partial, "level": self.level, "device": self._device()}

    # -- the loop --------------------------------------------------------------------

    def _device(self):
        """The chosen microphone, or the first one that actually records."""
        device = (self.conf.get("device") or "").strip()
        if device:
            return device
        found = [d["id"] for d in mic_devices() if d["id"]]
        return found[0] if found else ""      # the system default is often an output

    def _mic(self):
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
        device = self._device()
        if device:
            cmd += ["-D", device]
        log.info("microphone: %s", " ".join(cmd))
        # stderr goes to a file, not a pipe: arecord prints a line for every overrun, and a
        # pipe nobody reads fills up and stops it (and so the listening) without a word
        err = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        proc.pie_err = err
        return proc

    def _mic_error(self, mic):
        try:
            err = mic.pie_err
            err.seek(0)
            lines = [ln.strip() for ln in err.read().decode(errors="replace").splitlines() if ln.strip()]
            return lines[-1] if lines else ""
        except Exception:
            return ""

    def _run(self):
        import json
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        try:
            model = Model(model_path(self.conf["model"]))
        except Exception as e:
            self.state, self.error = "error", f"couldn't load the model: {e}"
            return
        rec = KaldiRecognizer(model, RATE)
        try:
            mic = self._mic()
        except OSError as e:
            self.state, self.error = "error", f"couldn't open the microphone: {e}"
            return

        wake = _words(self.conf.get("wake") or "")      # blank means answer everything
        patience = float(self.conf.get("timeout", 8))
        self.state, self.error = "waiting", None
        armed_at = 0.0
        armed = False
        log.info("listening for '%s'", wake)

        try:
            while not self.stop_flag.is_set():
                data = mic.stdout.read(4000)
                if not data:
                    why = self._mic_error(mic)
                    self.state = "error"
                    self.error = why or "the microphone stopped sending audio"
                    log.warning("microphone stopped: %s", self.error)
                    break
                self.level = max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                                 for i in range(0, min(len(data), 400), 2))
                if self._poke:                # the button: skip the wake phrase
                    self._poke = False
                    rec.Reset()
                    armed, armed_at = True, time.time()
                    self.state, self.heard, self.partial = "listening", "", ""
                    log.info("listening (button)")
                    continue
                if self.is_busy():            # don't listen to ourselves talking
                    rec.Reset()
                    if armed:
                        armed_at = time.time()
                    continue
                final = rec.AcceptWaveform(data)
                text = _words(json.loads(rec.Result())["text"] if final
                              else json.loads(rec.PartialResult()).get("partial", ""))
                if not armed:
                    self.partial = text
                    if not wake:                      # no phrase set: everything goes to the bot
                        if final and len(text.split()) >= 2:
                            self._deliver(text)
                        continue
                    if wake in text:
                        rest = text.split(wake, 1)[1].strip()
                        rec.Reset()
                        if len(rest.split()) >= 2:        # said it all in one breath
                            self._deliver(rest)
                            continue
                        armed, armed_at = True, time.time()
                        self.state, self.heard = "listening", ""
                        log.info("woke up")
                elif final and text:
                    armed = False
                    self.partial = ""
                    self._deliver(text)
                elif not final:
                    self.partial = text
                elif time.time() - armed_at > patience:
                    armed = False
                    self.state = "waiting"
                    log.info("nothing followed the wake word")
        except Exception as e:
            log.exception("listener stopped")
            self.state, self.error = "error", str(e)[:140]
        finally:
            try:
                mic.terminate()
            except OSError:
                pass
            if self.state != "error":
                self.state = "off"

    def _deliver(self, text):
        self.heard = text
        self.state = "thinking"
        log.info("heard: %s", text)
        try:
            if self.on_command:
                self.on_command(text)
        except Exception:
            log.exception("handling what was heard failed")
        finally:
            self.state = "waiting" if self.running() else "off"


    def test(self, conf, seconds=4):
        """Record a few seconds and say what came back — the quickest way to
        tell a dead microphone from a mishearing."""
        self.conf = conf or {}
        if not have_vosk():
            return {"ok": False, "message": "the listener isn't installed yet"}
        name = chosen_model(self.conf)
        if not name:
            return {"ok": False, "message": "no speech model downloaded yet"}
        if self.running():
            return {"ok": False, "message": "it's already listening — switch it off to test"}
        import json
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        try:
            rec = KaldiRecognizer(Model(model_path(name)), RATE)
            mic = self._mic()
        except Exception as e:
            return {"ok": False, "message": f"couldn't start: {str(e)[:120]}"}
        peak, words, end = 0, "", time.time() + seconds
        try:
            while time.time() < end:
                data = mic.stdout.read(4000)
                if not data:
                    return {"ok": False, "message": self._mic_error(mic) or "no audio from the microphone"}
                peak = max(peak, max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                                     for i in range(0, min(len(data), 800), 2)))
                if rec.AcceptWaveform(data):
                    words = (words + " " + json.loads(rec.Result())["text"]).strip()
            words = (words + " " + json.loads(rec.FinalResult())["text"]).strip()
        finally:
            try:
                mic.terminate()
            except OSError:
                pass
        loud = round(peak / 327.67)
        if peak < 300:
            return {"ok": False, "level": loud, "heard": words,
                    "message": f"almost silent ({loud}%) — check the microphone is the right one and unmuted"}
        return {"ok": True, "level": loud, "heard": words,
                "message": f"heard you at {loud}% — it made out: {words or '(nothing it recognised)'}"}


EARS = Ears()
