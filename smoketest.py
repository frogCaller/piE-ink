#!/usr/bin/env python3
"""Boot the app with the mock panel and poke every route.

This is the net for the boring kind of mistake: an endpoint deleted by
accident, a handler that throws on an empty config, a settings section that
stopped saving. It touches the real routes with the real service running, so
anything that has gone missing shows up as a failure rather than as silence
weeks later.

    python3 smoketest.py            # prints one line per route
"""
import os
import sys
import tempfile

os.environ.setdefault("PIE_INK_DRIVER", "mock")

FAILED = []


def check(name, got, want=(200,), body_has=None):
    ok = got.status_code in want
    detail = ""
    if ok and body_has:
        try:
            data = got.get_json() or {}
        except Exception:
            data = {}
        missing = [k for k in body_has if k not in data]
        if missing:
            ok, detail = False, f"missing {missing}"
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{' — ' + detail if detail else ''}"
          f"{'' if ok else f' (status {got.status_code})'}")
    if not ok:
        FAILED.append(name)


class _FakeHome:
    """Enough of Home Assistant's REST API to be spoken to: a TV with a
    remote, two lights, a scene. Records every service call."""

    STATES = [
        {"entity_id": "media_player.living_room_tv", "state": "on",
         "attributes": {"friendly_name": "Living Room TV", "source_list": ["TV", "HDMI 1", "Netflix", "YouTube"]}},
        {"entity_id": "remote.living_room_tv", "state": "on", "attributes": {"friendly_name": "Living Room TV Remote"}},
        {"entity_id": "light.kitchen_light", "state": "off", "attributes": {"friendly_name": "Kitchen Light"}},
        {"entity_id": "light.lamp", "state": "on", "attributes": {"friendly_name": "Lamp"}},
        {"entity_id": "scene.movie_night", "state": "unknown", "attributes": {"friendly_name": "Movie Night"}},
        {"entity_id": "sensor.outside", "state": "12", "attributes": {"friendly_name": "Outside"}},
    ]

    def __init__(self):
        self.calls = []
        self.token = "test-token"
        self.port = None
        self._server = None

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {fake.token}":
                    self._send(401, {"message": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/config":
                    return self._send(200, {"version": "2026.9.1", "location_name": "Test House"})
                if self.path == "/api/states":
                    return self._send(200, fake.STATES)
                if self.path.startswith("/api/states/"):
                    eid = self.path.split("/api/states/", 1)[1]
                    for st in fake.STATES:
                        if st["entity_id"] == eid:
                            return self._send(200, st)
                    return self._send(404, {"message": "Entity not found."})
                self._send(404, {"message": "not found"})

            def do_POST(self):
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path.startswith("/api/services/"):
                    domain, service = self.path.split("/api/services/", 1)[1].split("/")
                    fake.calls.append((domain, service, body))
                    return self._send(200, [])
                self._send(404, {"message": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing



class _FakeOllama:
    """Enough of Ollama to be asked: a model list, and answers — a scene as
    JSON when a picture comes with the question, a decision as JSON when the
    agent asks, a line of chat otherwise. Records every prompt."""

    def __init__(self):
        self.prompts = []
        self.scene = {"people": 1, "animals": ["cat"], "packages": 0, "vehicles": 0, "door": "closed", "lights": "on",
                      "unusual": "", "notes": "A person on the sofa with a cat."}
        self.decision = {"thought": "Someone is home with the cat; all is well.", "level": "note",
                         "actions": [{"do": "show", "text": "All quiet, cat on the sofa"}, {"do": "notify", "text": "Someone came in"}],
                         "remember": "cat was on the sofa at this time"}
        self.decision2 = {"thought": "Turned to look: it was only the cat by the window.", "level": "note",
                          "actions": [{"do": "show", "text": "Just the cat"}], "remember": ""}
        self.view_words = "A window with the cat on the sill; nothing unusual."
        self.port = None
        self._server = None

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": "fake:latest"}]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                fake.prompts.append(body)
                if self.path != "/api/generate":
                    return self._send(404, {"error": "not found"})
                prompt = body.get("prompt") or ""
                if body.get("images") and ("just turned the camera" in prompt or "views side by side" in prompt):
                    answer = fake.view_words
                elif body.get("images"):
                    answer = "Here you go:\n```json\n" + json.dumps(fake.scene) + "\n```"
                elif "You turned the camera to look" in prompt:
                    answer = json.dumps(fake.decision2)
                elif "Report at" in prompt:
                    answer = json.dumps(fake.decision)
                else:
                    answer = "Hello from the fake model."
                self._send(200, {"model": body.get("model"), "response": answer, "done": True})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing


class _FakeDuco:
    """server.duinocoin.com, as far as PiE-ink asks it: one user with two
    miners, and api.json's price and network rate. The hashrate can be
    changed between readings to test the alert."""

    def __init__(self):
        self.miners = [{"identifier": "ESP32-1", "hashrate": 320.5, "pool": "us-pool", "software": "ESP32 Miner 4.3",
                        "algorithm": "DUCO-S1", "accepted": 100, "rejected": 1, "sharetime": 4.2},
                       {"identifier": "PicoW", "hashrate": 130.0, "pool": "us-pool", "software": "Pico Miner",
                        "algorithm": "DUCO-S1", "accepted": 50, "rejected": 0, "sharetime": 5.1}]
        self.balance = 1234.5
        self.verus_sols = 34419715.2
        self.port = None
        self._server = None

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api.json":
                    return self._send(200, {"Duco price": 0.00041, "DUCO-S1 hashrate": "1.23 GH/s", "Active workers": 12345})
                if self.path.startswith("/verus/miner/"):          # luckpool.net, as it really answers
                    wallet = self.path.rsplit("/", 1)[1]
                    if wallet != "RTESTWALLET":
                        return self._send(200, {})
                    return self._send(200, {"timestamp": 1700000000, "address": wallet, "hashrateSols": fake.verus_sols,
                                            "hashrateString": f"{fake.verus_sols / 1e6:.2f} MH", "avgHashrateSols": fake.verus_sols,
                                            "avgHashrateSols24HR": fake.verus_sols * 0.9, "currentSharePercent": 0.01,
                                            "estimatedLuck": "87.5%", "shares": 1234.5, "efficiency": 99.1, "immature": 0.0123,
                                            "balance": 0.4567, "paid": 12.3456,
                                            "workers": [f"{wallet}.ryzen:{fake.verus_sols * 0.7}:800:on:na:false:1:22.4",
                                                        f"pi5:{fake.verus_sols * 0.3}:434.5:on:na:false:1:30.1"] if fake.verus_sols else []})
                if self.path.startswith("/v3/users/"):
                    user = self.path.rsplit("/", 1)[1]
                    if user != "tester":
                        return self._send(200, {"success": False, "message": "This user doesn't exist"})
                    return self._send(200, {"success": True, "result": {
                        "balance": {"balance": fake.balance, "stake_amount": 100.0, "stake_date": 1700000000, "trust_score": 100, "verified": "yes"},
                        "miners": fake.miners, "achievements": [1, 2, 3]}})
                self._send(404, {"success": False, "message": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing

FAKE_PIPER = r"""#!/usr/bin/env python3
# a stand-in for piper --output-raw: 0.8 s of tone per input line, streamed; "slow" is a voice that makes
# speech more slowly than it talks (a medium one on a Zero 2 W): 1.2 s to make each second, sent all at once
import sys, os, time, math, struct
mode = os.environ.get("FAKE_PIPER", "stream")           # stream | exit-after-one | slow
tone = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 22050))) for i in range(int(22050 * 0.8)))
time.sleep(0.2)                                          # loading the voice
for line in sys.stdin:
    if not line.strip():
        continue
    if mode == "slow":
        secs = max(0.3, 0.065 * len(line.strip()))
        time.sleep(1.2 * secs)
        n = int(22050 * secs) * 2
        sys.stdout.buffer.write((tone * (n // len(tone) + 1))[:n]); sys.stdout.buffer.flush()
        continue
    for i in range(0, len(tone), 4096):
        sys.stdout.buffer.write(tone[i:i + 4096]); sys.stdout.buffer.flush(); time.sleep(0.005)
    if mode == "exit-after-one":
        break
"""

FAKE_APLAY = r"""#!/usr/bin/env python3
# a stand-in for aplay: counts what it gets; FAKE_APLAY_FAIL="plughw:1,0=busy" refuses that output
import sys, os, time
if "-l" in sys.argv:
    print("**** List of PLAYBACK Hardware Devices ****")
    print("card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]")
    print("card 1: UACDemoV10 [UACDemoV1.0], device 0: USB Audio [USB Audio]")
    sys.exit(0)
dev = sys.argv[sys.argv.index("-D") + 1] if "-D" in sys.argv else ""
# a card by name is the same card as by number, as with the real aplay
by_name = {"plughw:CARD=Headphones,DEV=0": "plughw:0,0", "plughw:CARD=UACDemoV10,DEV=0": "plughw:1,0"}
if dev.startswith("plughw:CARD=") and dev not in by_name:
    sys.stderr.write(f"aplay: main:831: audio open error: No such file or directory\n")
    sys.exit(1)
for rule in filter(None, os.environ.get("FAKE_APLAY_FAIL", "").split(";")):
    d, _, how = rule.partition("=")
    if d == by_name.get(dev, dev):
        sys.stderr.write("aplay: main:834: audio open error: Device or resource busy\n")
        sys.exit(1)
n = 0
while True:
    b = sys.stdin.buffer.read(8192)
    if not b:
        break
    n += len(b)
with open(os.environ["FAKE_APLAY_LOG"], "a") as fh:
    fh.write(f"{dev or 'default'} {n}\n")
"""


def speaker_check():
    """The speaker's worker, against a stand-in Piper and aplay: every press
    plays (the 'works once' bug), every sentence plays even with a Piper that
    exits after each line, and a refused output is retried and then swapped
    for another — with the outcome reported, not guessed."""
    import json
    import os
    import stat
    import tempfile
    from pie_ink import audio
    root = tempfile.mkdtemp(prefix="pie-ink-speaker-")
    fakebin = os.path.join(root, "bin")
    os.makedirs(fakebin)
    for name, body in (("piper", FAKE_PIPER), ("aplay", FAKE_APLAY)):
        path = os.path.join(fakebin, name)
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    voices = os.path.join(root, "voices")
    os.makedirs(voices)
    with open(os.path.join(voices, "fake.onnx"), "wb") as fh:
        fh.write(b"not a model")
    with open(os.path.join(voices, "fake.onnx.json"), "w") as fh:
        json.dump({"audio": {"sample_rate": 22050}}, fh)
    log_path = os.path.join(root, "aplay.log")
    keep = {k: os.environ.get(k) for k in ("PATH", "FAKE_PIPER", "FAKE_APLAY_FAIL", "FAKE_APLAY_LOG")}
    old_dirs = audio.VOICE_DIR, audio.TTS_CACHE
    os.environ["PATH"] = fakebin + os.pathsep + os.environ.get("PATH", "")
    os.environ["FAKE_APLAY_LOG"] = log_path
    os.environ["FAKE_PIPER"] = "stream"
    os.environ.pop("FAKE_APLAY_FAIL", None)
    audio.VOICE_DIR, audio.TTS_CACHE = voices, os.path.join(root, "tts")
    conf = {"piper_voice": "fake", "device": "plughw:1,0", "piper_speed": 1.0}

    def played():
        try:
            with open(log_path) as fh:
                return [ln.split() for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            return []

    def clear():
        try:
            os.remove(log_path)
        except OSError:
            pass

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + detail}")
        if not cond:
            FAILED.append(name)

    spk = audio._Speaker()
    try:
        for n in range(3):
            okay, why = spk.say("Hello, this is a test.", conf, wait=True, timeout=30)
            expect(f"speaker press {n + 1}" + (" (from the cache)" if n else ""),
                   okay and len(played()) == n + 1 and played()[-1] == ["plughw:1,0", "35280"], f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_PIPER"] = "exit-after-one"
        spk.shutdown()                                     # so the next line starts a Piper of that kind
        okay, why = spk.say("One. Two, with version 2.5 in it! Three?", conf, wait=True, timeout=30)
        expect("three sentences with a Piper that exits after each line",
               okay and played() and played()[-1][1] == str(35280 * 3), f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_APLAY_FAIL"] = "plughw:1,0=busy"
        okay, why = spk.say("Busy output.", conf, wait=True, timeout=60)
        expect("a busy output is retried, then the default output takes the line",
               okay and "instead" in why and played() and played()[-1] == ["default", "35280"], f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_APLAY_FAIL"] = "plughw:1,0=busy;=busy;plughw:0,0=busy"
        okay, why = spk.say("Nothing works.", conf, wait=True, timeout=60)
        expect("when every output refuses, the button hears why", not okay and "busy" in why, f"{okay} {why}")
        expect("the worker is still alive and not 'speaking'", spk.worker.is_alive() and not spk.speaking)
        # outputs by card name: a camera's microphone plugged in first can't move them
        os.environ.pop("FAKE_APLAY_FAIL", None)
        clear()
        outs = audio.devices()
        expect("outputs are listed by card name, with their number as the alias",
               [d["id"] for d in outs] == ["", "plughw:CARD=Headphones,DEV=0", "plughw:CARD=UACDemoV10,DEV=0"]
               and outs[2].get("alias") == "plughw:1,0", str(outs))
        expect("either spelling is the same device", audio.same_device("plughw:1,0", "plughw:CARD=UACDemoV10,DEV=0")
               and not audio.same_device("plughw:0,0", "plughw:CARD=UACDemoV10,DEV=0")
               and audio.card_of("plughw:CARD=UACDemoV10,DEV=0") == "UACDemoV10" and audio.card_of("plughw:1,0") == "1")
        by_name = dict(conf, device="plughw:CARD=UACDemoV10,DEV=0")
        okay, why = spk.say("By name.", by_name, wait=True, timeout=30)
        expect("an output picked by name plays", okay and played() and played()[-1][0] == "plughw:CARD=UACDemoV10,DEV=0",
               f"{okay} {why} {played()}")
        expect("the fallbacks don't try the same card twice under its other name",
               "plughw:CARD=UACDemoV10,DEV=0" not in audio._fallback_outputs("plughw:1,0"), str(audio._fallback_outputs("plughw:1,0")))
        # "Play on this device": while a page is listening, a line goes to it in pieces, not to aplay
        import time as _t
        from pie_ink import here as _here
        hooks = _here.HERE.on_start, _here.HERE.on_stop
        _here.HERE.on_start = _here.HERE.on_stop = None
        try:
            clear()
            seen = _here.HERE.seq
            _here.HERE.seen("smoke-speaker")
            t0 = _t.time()
            okay, why = spk.say("One. Two.", conf, wait=True, timeout=30)
            took = _t.time() - t0
            says = [e for e in _here.HERE.events if e["seq"] > seen and e["kind"] == "say"]
            clips = [_here.HERE.clip(e["clip"]) or b"" for e in says]
            expect("while a page plays the sound, a line goes to it as WAV pieces, not to aplay, and takes as long",
                   okay and "phone or computer" in why and not played() and len(says) >= 2
                   and sum(len(x) - 44 for x in clips) == 35280 * 2 and all(x[:4] == b"RIFF" and x[8:12] == b"WAVE" for x in clips)
                   and len({e["line"] for e in says}) == 1 and took >= 1.5,
                   f"{okay} {why} {played()} {len(says)} {[len(x) for x in clips]} {took:.2f}")
            before = _here.HERE.seq
            spk.say("A first line that runs on. And on. And on.", conf)
            _t.sleep(0.6)
            okay, why = spk.say("Second.", conf, wait=True, timeout=30)
            evs = [e for e in _here.HERE.events if e["seq"] > before]
            lines = sorted({e["line"] for e in evs if e["kind"] == "say"})
            expect("a newer line hushes the one before on the page", okay and len(lines) == 2
                   and any(e["kind"] == "hush" and e["line"] == lines[0] for e in evs), str(evs))
            # a voice slower than speech: it learns how slow from one line, then holds the next back just long
            # enough that, once it speaks, it doesn't stop between sentences (it used to, for seconds)
            os.environ["FAKE_PIPER"] = "slow"
            spk.shutdown()
            spk._paces = {}                              # a voice it hasn't heard yet
            spk.say("One. And then two.", conf, wait=True, timeout=60)
            pace = spk.learnt_pace()
            before = _here.HERE.seq
            okay, why = spk.say("Hey! This one is a good deal longer than the first.", conf, wait=True, timeout=60)
            pieces = [(e["at"], e["secs"]) for e in _here.HERE.events if e["seq"] > before and e["kind"] == "say"]
            gaps, end = [], None                        # the page plays each piece when it comes, or when the last ends
            for at, secs in pieces:
                if end is not None and at > end:
                    gaps.append(round(at - end, 2))
                end = max(end or 0.0, at) + secs
            expect(f"a slow voice waits a moment, then says the whole line without stopping (pace {pace}, "
                   f"first sound after {spk.first_ms / 1000:.1f}s)",
                   okay and pace and 1.0 < pace < 1.6 and pieces and not gaps and spk.first_ms > 2000,
                   f"{okay} {why} pace {pace} gaps {gaps} pieces {pieces}")
            os.environ["FAKE_PIPER"] = "stream"
            spk.shutdown()
            _here.HERE.leave("smoke-speaker")
            clear()
            okay, why = spk.say("Back.", conf, wait=True, timeout=30)
            expect("when the page goes, the Pi's own speaker has it again", okay and played() and played()[-1][0] == "plughw:1,0",
                   f"{okay} {why} {played()}")
        finally:
            _here.HERE.leave("smoke-speaker")
            _here.HERE.on_start, _here.HERE.on_stop = hooks
    finally:
        spk.shutdown()
        audio.VOICE_DIR, audio.TTS_CACHE = old_dirs
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gps_drop_check():
    """A USB GPS that drops off the bus and comes back as a new device (a pty behind a by-id style link): the
    reader notices, says so, counts it, and has it again within a couple of seconds. Then "Why not working?"
    on a Pi like the one it was written for: it names the drops, and how many came as the camera turned."""
    import pty
    import tempfile
    import threading
    import time as _t
    from pie_ink import camera as _camera, gps, ptz as _ptz

    def plug(link):
        master, slave = pty.openpty()
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(os.ttyname(slave), link)
        stop, fake = threading.Event(), gps.MockStream(fix_after=0.3)

        def feed():
            while not stop.is_set():
                try:
                    os.write(master, ("\r\n".join(fake.lines()) + "\r\n").encode())
                except OSError:
                    return
                stop.wait(0.5)
        threading.Thread(target=feed, daemon=True).start()
        return master, slave, stop

    def unplug(master, slave, stop, link):
        stop.set()
        os.remove(link)
        for fd in (master, slave):
            os.close(fd)

    def until(cond, secs):
        end = _t.time() + secs
        while _t.time() < end and not cond():
            _t.sleep(0.1)
        return cond()

    folder = tempfile.mkdtemp(prefix="pie-ink-gps-")
    link = os.path.join(folder, "usb-u-blox_AG_u-blox_7-if00")
    g = gps.Gps()
    try:
        first = plug(link)
        g.start({"enabled": True, "device": link})
        up = until(lambda: g.status()["connected"], 6)
        unplug(*first, link)
        noticed = until(lambda: len(g.drops) == 1, 4)
        said = g.error or ""
        again = plug(link)
        t0 = _t.time()
        back = until(lambda: g.status()["connected"], 6)
        took = _t.time() - t0
        st = g.status()
        okay = up and noticed and "dropped off the USB bus" in said and back and took < 4 and st["drops"] == 1
        print(f"  {'ok  ' if okay else 'FAIL'} the GPS dropped off the bus: noticed and said ({said!r}), counted, and read "
              f"again {took:.1f}s after it came back")
        if not okay:
            print(f"       up {up} noticed {noticed} back {back} {st}")
            FAILED.append("gps drop")
        unplug(*again, link)
    finally:
        g.stop()
        try:
            os.remove(link)
        except OSError:
            pass
    # "Why not working?": three drops in the last hour, two as the camera turned, and the kernel saw it go twice
    now = _t.time()
    saved = (_camera._kernel_lines, _camera.usb_devices, gps.GPS.conf, list(gps.GPS.drops), list(_ptz.PTZ.turns))
    hub = {"bus": "1-1", "vid": "1a40", "pid": "0101", "name": "USB 2.0 Hub", "classes": ["hub"]}
    cam = {"bus": "1-1.2", "vid": "3564", "pid": "fef8", "name": "OBSBOT Tiny 2", "classes": ["video"]}
    blox = {"bus": "1-1.3", "vid": "1546", "pid": "01a7", "name": "u-blox 7 - GPS/GNSS Receiver", "classes": ["comms"]}
    try:
        _camera.usb_devices = lambda: [dict(u) for u in (hub, cam, blox)]
        _camera._kernel_lines = lambda limit=30, keys=(): ([
            "usb 1-1.3: USB disconnect, device number 4", "usb 1-1.3: new full-speed USB device number 8 using dwc2",
            "usb 1-1.3: Product: u-blox 7 - GPS/GNSS Receiver", "hub 1-1:1.0: port 3 disabled by hub (EMI?), re-enabling...",
            "usb 1-1.3: USB disconnect, device number 8", "usb 1-1.3: new full-speed USB device number 11 using dwc2"], None)
        gps.GPS.conf = {"enabled": True, "device": "auto"}
        gps.GPS.drops.clear()
        gps.GPS.drops.extend([now - 1500, now - 700, now - 120])
        _ptz.PTZ.turns.clear()
        _ptz.PTZ.turns.extend([(now - 702, now - 699), (now - 123, now - 121)])
        r = gps.probe()
        v, text = r["verdict"], gps.probe_text(r)
        want = ["keeps dropping off the USB bus", "3 times in the last hour", "2 min ago", "2 of those times the camera had just turned",
                "(EMI)", "hub with its own power adapter", "switch the little friend off"]
        missing = [w for w in want if w not in v]
        okay = not missing and "Dropped off the USB bus: 3 times in the last hour" in text and "the kernel saw 2 on 1-1.3" in text
        print(f"  {'ok  ' if okay else 'FAIL'} 'Why not working?' names the drops, and that two came as the camera turned")
        if not okay:
            print(f"       missing {missing}: {v}")
            FAILED.append("gps drop probe")
    finally:
        _camera._kernel_lines, _camera.usb_devices, gps.GPS.conf = saved[:3]
        gps.GPS.drops.clear()
        gps.GPS.drops.extend(saved[3])
        _ptz.PTZ.turns.clear()
        _ptz.PTZ.turns.extend(saved[4])


def sound_probe_check(pieapp):
    """"Speaker or mic missing?" on Pis like the one it was written for: a Zero 2 W, a four-port hub, a GPS
    and an OBSBOT on it, no speaker or mic on the bus. Once with the power sagging; once as the real one
    was — power fine, dwc2 switched on in config.txt, two ports failing with -32 while the camera streams."""
    from pie_ink import audio, camera
    hub = {"bus": "1-1", "vid": "1a40", "pid": "0101", "name": "USB 2.0 Hub", "classes": ["hub"], "speed": "480", "power_ma": "100mA"}
    gps = {"bus": "1-1.3", "vid": "1546", "pid": "01a7", "name": "u-blox 7", "classes": ["comms"], "speed": "12", "power_ma": "100mA"}
    obsbot = {"bus": "1-1.2", "vid": "3564", "pid": "fef8", "name": "Remo Tech OBSBOT Tiny 2", "classes": ["audio", "hid", "video"],
              "speed": "480", "power_ma": "500mA"}
    base = {"/proc/device-tree/model": "Raspberry Pi Zero 2 W Rev 1.0\0", "/sys/bus/usb/devices/1-1/maxchild": "4",
            "/sys/bus/usb/devices/1-1.2/product": "OBSBOT Tiny 2"}
    hdmi = [{"id": "", "label": "System default"}, {"id": "plughw:CARD=vc4hdmi,DEV=0", "label": "vc4-hdmi", "alias": "plughw:0,0"}]
    stock = "dtparam=audio=on\n[cm4]\notg_mode=1\n[cm5]\ndtoverlay=dwc2,dr_mode=host\n[all]\n"
    scenarios = [
        ("power sagging", [hub, gps, obsbot], dict(base, **{
            "/proc/asound/cards": (" 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n                      vc4-hdmi\n"
                                   " 1 [Tiny2          ]: USB-Audio - OBSBOT Tiny 2\n                      OBSBOT at usb-3f980000.usb-1.2\n"),
            "/proc/asound/card1/usbid": "3564:fef8", "/sys/devices/platform/soc/soc:firmware/get_throttled": "50005",
            "/boot/firmware/config.txt": stock}),
         ["usb 1-1.3: Product: UACDemoV1.0", "hwmon hwmon1: Undervoltage detected!", "usb 1-1.3: USB disconnect, device number 5",
          "usb 1-1.4: device descriptor read/64, error -71"],
         {"aplay": hdmi, "arecord": [{"id": "", "label": "System default"},
                                     {"id": "plughw:CARD=Tiny2,DEV=0", "label": "OBSBOT Tiny 2", "alias": "plughw:1,0"}]},
         ["doesn't see a USB speaker or microphone", "port 4 of the hub", "error -71", "UACDemoV1.0 came up earlier",
          "under-voltage", "hub with its own power adapter", "OBSBOT Tiny 2, under Microphone",
          "pick them again under Output and Microphone"], False),
        ("the real one: power fine, dwc2, -32", [hub, dict(obsbot, classes=["video"]), gps], dict(base, **{
            "/proc/asound/cards": " 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n                      vc4-hdmi\n",
            "/sys/devices/platform/soc/soc:firmware/get_throttled": "0",
            "/boot/firmware/config.txt": stock + "dtoverlay=dwc2\n"}),
         ["usb 1-1.4: device descriptor read/64, error -32", "usb 1-1-port4: attempt power cycle",
          "usb 1-1.4: new full-speed USB device number 13 using dwc2", "usb 1-1.4: device not accepting address 13, error -32",
          "usb 1-1-port4: unable to enumerate USB device", "usb 1-1.2: new high-speed USB device number 15 using dwc2",
          "usb 1-1.2: Product: OBSBOT Tiny 2", "usb 1-1.1: new full-speed USB device number 16 using dwc2",
          "usb 1-1.1: device descriptor read/64, error -32", "usb 1-1.1: device not accepting address 19, error -32",
          "usb 1-1-port1: unable to enumerate USB device"],
         {"aplay": hdmi, "arecord": [{"id": "", "label": "System default"}]},
         ["ports 1 and 4 of the hub", "error -32", "own power is fine", "full-speed", "dwc2 driver", "Watch the USB bus",
          "Pausing the camera", "take \u201cdtoverlay=dwc2\u201d out of /boot/firmware/config.txt"], True),
    ]
    saved = (camera.usb_devices, camera._read, camera._kernel_lines, audio.entries, camera.CAMERA.status, audio._kernel_64)
    audio._kernel_64 = lambda: False                     # a 32-bit system first, where dwc_otg is there to go back to
    try:
        for name, usb, files, kernel, lists, want, crowded in scenarios:
            camera.usb_devices = lambda usb=usb: [dict(u) for u in usb]
            camera._read = lambda p, files=files: files.get(p, "")
            camera._kernel_lines = lambda limit=30, keys=(), kernel=kernel: (kernel, None)
            audio.entries = lambda tool="aplay", fresh=False, lists=lists: lists[tool]
            camera.CAMERA.status = lambda: {"running": True, "id": "usb:/dev/video0", "age": 0.1}
            r = audio.probe({"audio": {"device": "plughw:1,0"}, "listen": {"device": "plughw:2,0"}}
                            if not crowded else {})
            v, text = r["verdict"], audio.probe_text(r)
            missing = [w for w in want if w not in v]
            okay = not missing and text.startswith(v) and "4 ports, 2 in use" in text and r["crowded"] == crowded
            print(f"  {'ok  ' if okay else 'FAIL'} 'Speaker or mic missing?' — {name}: "
                  + ("the right cause, and what to do" if okay else f"missing {missing}; crowded {r['crowded']}; {v}"))
            if not okay:
                FAILED.append(f"sound probe: {name}")
        files = dict(scenarios[1][2], **{"/boot/firmware/config.txt": stock})
        camera._read = lambda p: files.get(p, "")
        if audio.probe({})["overlay"] is not None:
            print("  FAIL a stock config.txt (dwc2 only under [cm5]) isn't this Pi switching to dwc2")
            FAILED.append("sound probe: stock config")
        # the same Pi on a 64-bit system: dwc_otg doesn't work there, so dwc2 stays — never "take it out"
        audio._kernel_64 = lambda: True
        files = dict(scenarios[1][2], **{"/boot/firmware/config.txt": stock + "dtoverlay=dwc2,dr_mode=host\n"})
        camera._read = lambda p: files.get(p, "")
        r = audio.probe({})
        v = r["verdict"]
        okay = ("Keep “dtoverlay=dwc2,dr_mode=host” in /boot/firmware/config.txt" in v and " out of " not in v
                and "worse at that" not in v and "Pausing the camera" in v and "Play on this device" in v
                and "64-bit" in audio.probe_text(r))
        print(f"  {'ok  ' if okay else 'FAIL'} 'Speaker or mic missing?' — the same Pi, 64-bit: it says to keep dwc2 "
              "(the driver there is), not to take it out")
        if not okay:
            print(f"       {v}")
            FAILED.append("sound probe: 64-bit keeps dwc2")
    finally:
        (camera.usb_devices, camera._read, camera._kernel_lines, audio.entries, camera.CAMERA.status,
         audio._kernel_64) = saved


def panel_check():
    """The e-ink HATs on a pretend Pi: fake lgpio and spidev modules under the real
    Waveshare drivers and the panel classes, every byte to the bus recorded. Every
    driver draws, the 2.7" frames are the bytes the stock driver would send, no SPI
    descriptor leaks, and the service swaps drivers while running without the page
    waiting on the old HAT."""
    import subprocess
    import threading
    import time as _t
    import types
    from PIL import Image, ImageDraw

    bus = {"spi": [], "claimed": set(), "opens": 0, "closes": 0, "busy": 0}

    class LgpioError(Exception):
        pass

    lg = types.ModuleType("lgpio")
    lg.error, lg.SET_PULL_DOWN, lg.SET_PULL_UP = LgpioError, 1, 2
    lg.gpiochip_open = lambda n: 100 if n == 0 else (_ for _ in ()).throw(LgpioError("no chip"))
    lg.gpio_get_chip_info = lambda h: (0, 54, "gpiochip0", "pinctrl-bcm2835")
    lg.gpiochip_close = lambda h: None
    lg.gpio_claim_output = lambda h, pin, level=0: bus["claimed"].add(pin)
    lg.gpio_claim_input = lambda h, pin, flags=0: bus["claimed"].add(pin)
    lg.gpio_free = lambda h, pin: bus["claimed"].discard(pin)
    lg.gpio_read = lambda h, pin: bus["busy"] if pin == 24 else 1

    def gpio_write(h, pin, v):
        if pin not in bus["claimed"]:                      # what real lgpio does with a line you let go of
            raise LgpioError("GPIO not allocated")
    lg.gpio_write = gpio_write

    class SpiDev:
        def __init__(self):
            self.max_speed_hz, self.mode, self.fd = 0, 0, -1

        def open(self, b, d):
            bus["opens"] += 1; self.fd = bus["opens"]

        def writebytes(self, data):
            if self.fd < 0:
                raise OSError("SPI not open")
            bus["spi"].append(bytes(data))
        writebytes2 = writebytes

        def close(self):
            if self.fd >= 0:
                bus["closes"] += 1; self.fd = -1

    sp = types.ModuleType("spidev"); sp.SpiDev = SpiDev
    sys.modules["lgpio"], sys.modules["spidev"] = lg, sp
    real_popen = subprocess.Popen

    class _Pi:                                               # epdconfig reads /proc/cpuinfo at import
        def communicate(self):
            return ("Model : Raspberry Pi Zero 2 W Rev 1.0\n", "")
    subprocess.Popen = lambda cmd, *a, **k: _Pi() if isinstance(cmd, str) and "cpuinfo" in cmd else real_popen(cmd, *a, **k)
    try:
        import waveshare_epd.epdconfig as epdconfig
    finally:
        subprocess.Popen = real_popen
    if type(epdconfig.implementation).__name__ != "LgpioPi":
        print(f"  FAIL epdconfig picked {type(epdconfig.implementation).__name__} on a Pi with lgpio")
        FAILED.append("epdconfig backend")
        return
    epdconfig.implementation.delay_ms = lambda ms: None      # the drivers' pauses, skipped
    epdconfig.delay_ms = epdconfig.implementation.delay_ms

    from pie_ink import panels
    from pie_ink.panels import base as _base, eink as _eink

    def picture(w, h, n):
        im = Image.new("L", (w, h), 255)
        d = ImageDraw.Draw(im)
        d.ellipse((5 + n * 9, 5, 70 + n * 9, 70), fill=0)
        d.text((4, h - 16), f"frame {n}", fill=0)
        return im

    def fresh():
        bus["spi"].clear(); bus["opens"] = bus["closes"] = 0

    # every driver, both ways up: open, five frames (the first full, then partial), the same frame
    # again (not sent), a forced full one, a clear, close — then nothing is left claimed or open
    for name in ("epd2in13_V4", "epd2in13_V3", "epd2in7_V2", "epd2in7", "epd3in7"):
        for rotation in (0, 180):
            fresh()
            bus["busy"] = 0 if name != "epd2in7" else 1    # the V1's controller says idle with a high line
            p = panels.make({"driver": name, "rotation": rotation, "full_refresh_every": 3})
            try:
                p.open()
                sent = [p.show(picture(p.width, p.height, n)) for n in range(5)]
                again = p.show(picture(p.width, p.height, 4))
                p.show(picture(p.width, p.height, 9), full=True)
                p.clear()
                p.close()
            except Exception as e:
                print(f"  FAIL {name} rotation {rotation}: {type(e).__name__}: {e}")
                FAILED.append(f"panel {name}")
                continue
            okay = all(sent) and not again and not bus["claimed"] and bus["opens"] == bus["closes"] and not p.warning
            print(f"  {'ok  ' if okay else 'FAIL'} {name} rotation {rotation}: {p.width}x{p.height}, 7 frames, "
                  f"{len(bus['spi'])} SPI transfers, SPI opened {bus['opens']} closed {bus['closes']}, lines left: {sorted(bus['claimed']) or 'none'}")
            if not okay:
                FAILED.append(f"panel {name}")

    # the 2.7" frames go in one transfer, packed with numpy: the same bytes the stock driver's loop makes
    import importlib
    for module in ("waveshare_epd.epd2in7_V2", "waveshare_epd.epd2in7"):
        epd = importlib.import_module(module).EPD()
        same = True
        for size in ((264, 176), (176, 264)):
            im = Image.effect_noise(size, 80).point(lambda v: 255 if v > 128 else 0).convert("1")
            if _eink.packed(im, epd) != epd.getbuffer(im):
                same = False
                print(f"  FAIL {module} {size[0]}x{size[1]}: the packed frame differs from the driver's own")
        if same:
            print(f"  ok   {module.split('.')[-1]}: the frame packed with numpy is byte for byte the driver's own, both ways up")
        else:
            FAILED.append(f"{module} packing")
    bus["busy"] = 0
    fresh()
    p = panels.make({"driver": "epd2in7_V2"}); p.open(); opened = len(bus["spi"])
    p.show(picture(264, 176, 1)); first = len(bus["spi"]) - opened
    p.show(picture(264, 176, 2)); second = len(bus["spi"]) - opened - first
    p.close()
    if first < 60 and second < 40:
        print(f"  ok   a 2.7\" V2 frame is {first} transfers for a full refresh and {second} for a partial one (the stock driver: 11,600 and 5,800)")
    else:
        print(f"  FAIL a 2.7\" V2 frame took {first} / {second} transfers")
        FAILED.append("2.7 transfers")

    # a panel that never answers: the page is told, and the word goes once the panel answers again
    class Slow(panels.MockEInk):
        name = "slow"
        took = 0.0

        def _push(self, image, full):
            Slow.clock += self.took

    Slow.clock = 1000.0
    real_mono = _base.time.monotonic
    _base.time.monotonic = lambda: Slow.clock
    try:
        s = Slow(); s.open()
        s.took = 31.0; s.show(picture(250, 122, 1))
        said = s.warning
        s.took = 0.5; s.show(picture(250, 122, 2))
        cleared = s.warning
    finally:
        _base.time.monotonic = real_mono
    if said == _base.NO_ANSWER and not cleared and "V1 or V2" in said:
        print("  ok   a frame the controller never finishes: 'The panel didn't answer — is the driver the right one…', gone once it answers")
    else:
        print(f"  FAIL the unanswered frame should warn, then clear: {said!r} / {cleared!r}")
        FAILED.append("panel warning")

    # the service, running: the driver changed under it, as from Settings → Screen. The request comes
    # back at once even while the old HAT is slow to answer; the old panel is released before the new
    # one claims the lines (they share them); the new one draws; the keys of a 2.7" come and go
    import tempfile
    from pie_ink import service as _svc, settings as _cfg
    saved_path = _cfg.CONFIG_PATH
    _cfg.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="pie-ink-panels-"), "config.yaml")
    saved_env = os.environ.pop("PIE_INK_DRIVER", None)
    svc = None
    try:
        svc = _svc.DisplayService()
        svc.apply_settings({"display": {"driver": "epd2in13_V4"}})
        svc.start()
        svc.set_mode("clock")
        end = _t.time() + 5
        while _t.time() < end and svc._frames < 1:
            _t.sleep(0.05)
        okay = svc.status()["panel"]["driver"] == "epd2in13_V4" and svc._frames >= 1 and svc.status()["error"] is None
        print(f"  {'ok  ' if okay else 'FAIL'} the service draws on the 2.13\" V4 ({svc._frames} frames, error {svc.status()['error']})")
        if not okay:
            FAILED.append("service on V4")
        for name, keys in (("epd2in7_V2", True), ("epd2in7", True), ("epd3in7", False), ("epd2in13_V3", False), ("epd2in13_V4", False)):
            bus["busy"] = 1 if name == "epd2in7" else 0
            before = svc._frames
            t0 = _t.time()
            svc.apply_settings({"display": {"driver": name}})
            took = _t.time() - t0
            at_once = svc.status()["panel"]["driver"]
            end = _t.time() + 6
            while _t.time() < end and (svc._frames <= before or not svc.panel.is_open):
                _t.sleep(0.05)
            st = svc.status()
            okay = (took < 0.5 and at_once == name and st["panel_open"] and st["frames"] > before and st["error"] is None
                    and (svc._buttons is not None) == keys and svc._retired is None)
            print(f"  {'ok  ' if okay else 'FAIL'} → {name}: the page is told at once ({took * 1000:.0f} ms), "
                  f"it draws ({st['frames'] - before} frames), {'keys on GPIO 5/6/13/19' if keys else 'no keys'}, error {st['error']}")
            if not okay:
                FAILED.append(f"switch to {name}")
        # the old HAT mid-frame and slow to answer: the change still comes back at once, and lands
        class Stuck(panels.MockEInk):
            name = "stuck"

            def _push(self, image, full):
                _t.sleep(1.2)
        panels.PANELS["stuck"] = Stuck
        try:
            svc.apply_settings({"display": {"driver": "stuck"}})
            end = _t.time() + 4
            while _t.time() < end and not (svc.panel.is_open and svc.panel.name == "stuck"):
                _t.sleep(0.05)
            _t.sleep(0.3)                                      # into a frame
            t0 = _t.time()
            svc.apply_settings({"display": {"driver": "epd2in7_V2"}})
            took = _t.time() - t0
            end = _t.time() + 6
            while _t.time() < end and not (svc.panel.name == "epd2in7_V2" and svc.panel.is_open and svc._retired is None):
                _t.sleep(0.05)
            st = svc.status()
            okay = took < 0.5 and st["panel"]["driver"] == "epd2in7_V2" and st["panel_open"] and st["error"] is None
            print(f"  {'ok  ' if okay else 'FAIL'} with the old HAT mid-frame and slow, the change comes back in {took * 1000:.0f} ms and the new one takes over")
            if not okay:
                FAILED.append("switch from a stuck panel")
        finally:
            panels.PANELS.pop("stuck", None)
    except Exception as e:
        print(f"  FAIL the service switching drivers: {type(e).__name__}: {e}")
        FAILED.append("service switching")
    finally:
        if svc is not None:
            svc.shutdown(); svc.join(5)
        _cfg.CONFIG_PATH = saved_path
        if saved_env is not None:
            os.environ["PIE_INK_DRIVER"] = saved_env
        if not bus["claimed"] and bus["opens"] == bus["closes"]:
            print("  ok   shut down: no GPIO line left claimed, every SPI descriptor closed")
        else:
            print(f"  FAIL after shutdown: lines {sorted(bus['claimed'])}, SPI opened {bus['opens']} closed {bus['closes']}")
            FAILED.append("panel shutdown")


def main():
    if True:
        import app as pieapp
        c = pieapp.app.test_client()

        print("pages")
        check("GET /", c.get("/"))
        check("GET /api/status", c.get("/api/status"), body_has=["settings", "service"])
        check("GET /api/preview.png", c.get("/api/preview.png"))
        check("GET /api/now", c.get("/api/now"), body_has=["mode", "speech"])
        check("GET /api/camera/probe", c.get("/api/camera/probe"), body_has=["report", "text"])
        check("GET /api/map", c.get("/api/map"), body_has=["me", "friends", "tiles", "zoom"])
        check("POST /api/render.png map", c.post("/api/render.png", json={"mode": "map"}))
        check("POST /api/lcd/preview.png map", c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": "map"}}}))
        check("PUT /api/settings map", c.put("/api/settings", json={"map": {"zoom": 14, "style": "shaded"}}))
        # a position from the weather spot, then the map knows where it is and friends hear about it
        c.post("/api/weather/place", json={"lat": 32.7157, "lon": -117.1611, "name": "San Diego", "admin": "California", "country_code": "US"})
        d = c.get("/api/map").get_json() or {}
        if not d.get("me") or d["me"].get("source") != "weather":
            print(f"  FAIL the map has no position from the weather spot: {d.get('me')}")
            FAILED.append("map position")
        else:
            print("  ok   the map takes the weather spot when there is no GPS")
        import json as _json
        from pie_ink import friends as _friends
        beacon = _json.loads(_friends._beacon())
        if (beacon.get("where") or {}).get("lat") is None:
            print(f"  FAIL the beacon does not say where we are: {beacon}")
            FAILED.append("map beacon")
        check("POST /api/map/prefetch", c.post("/api/map/prefetch"), body_has=["started"])
        check("POST /api/mode map", c.post("/api/mode", json={"mode": "map"}))
        if not isinstance(pieapp._tg_command("/map"), (tuple, str)):
            FAILED.append("telegram /map")
        c.post("/api/weather/place", json={})

        print("screens")
        from pie_ink.modes import MODES
        for mode in MODES:
            r = c.post("/api/mode", json={"mode": mode})
            check(f"POST /api/mode {mode}", r)

        print("settings")
        for section, patch in (
            ("me", {"me": {"name": "Test"}}),
            ("finance", {"finance": {"salary": 1000}}),
            ("display", {"display": {"full_refresh_every": 40}}),
            ("audio", {"audio": {"speak_bot": True}}),
            ("listen", {"listen": {"wake": "hey there"}}),
            ("routines", {"routines": {"enabled": False}}),
            ("message", {"message": {"text": "hello"}}),
            ("schedule", {"schedule": {"workdays": ["mon", "tue"]}}),
            ("music", {"music": {"repeat": True}}),
            ("llm", {"llm": {"chatter_minutes": 0}}),
            ("lcd", {"lcd": {"enabled": True, "screen": "clock"}}),
        ):
            r = c.put("/api/settings", json=patch)
            got = (r.get_json() or {}).get("settings", {})
            key, value = next(iter(patch[section].items()))
            saved = got.get(section, {}).get(key)
            check(f"PUT /api/settings {section}", r, body_has=["settings"])
            if saved != value:
                print(f"  FAIL {section}.{key} came back as {saved!r}, not {value!r}")
                FAILED.append(f"{section}.{key}")

        print("the bot, sound and listening")
        check("GET /api/chat", c.get("/api/chat"), body_has=["history"])
        check("POST /api/llm/chat (no model)", c.post("/api/llm/chat", json={"message": "hi"}), want=(400,))
        check("GET /api/llm", c.get("/api/llm"))
        check("GET /api/audio", c.get("/api/audio"), body_has=["devices", "engine"])
        check("POST /api/audio/say", c.post("/api/audio/say", json={"text": "test"}), want=(200, 400))
        check("POST /api/audio/say (wait)", c.post("/api/audio/say", json={"wait": True}), want=(200, 400))
        r = c.post("/api/audio/volume", json={"volume": 40})
        check("POST /api/audio/volume", r, body_has=["applied", "how"])
        if (r.get_json() or {}).get("how") not in ("mixer", "software"):
            print(f"  FAIL the volume went nowhere: {r.get_json()}")
            FAILED.append("volume")
        vol = (c.get("/api/status").get_json() or {}).get("settings", {}).get("audio", {}).get("volume")
        if vol != 40:
            print(f"  FAIL the volume was not remembered: {vol}")
            FAILED.append("volume saved")
        check("GET /api/camera/list", c.get("/api/camera/list?refresh=1"), body_has=["cameras"])
        check("POST /api/message/say", c.post("/api/message/say", json={"text": "test"}), want=(200, 400))
        check("GET /api/listen", c.get("/api/listen"), body_has=["devices", "status"])
        check("GET /api/audio/probe", c.get("/api/audio/probe"), body_has=["report", "text"])
        sound_probe_check(pieapp)
        # watching the USB bus: the kernel's lines since the last look, in plain words
        from pie_ink import usbwatch as _uw
        raw_was, cards_was, devs_was = _uw._kernel_raw, _uw.audio.sound_cards, _uw.camera.usb_devices
        log = ["[  100.000000] usb 1-1.3: new full-speed USB device number 4 using dwc2", "[  100.200000] usb 1-1.3: Product: u-blox 7"]
        _uw._kernel_raw = lambda: list(log)
        _uw.camera.usb_devices = lambda: [{"bus": "1-1", "vid": "1a40", "pid": "0101", "name": "USB 2.0 Hub", "classes": ["hub"]},
                                          {"bus": "1-1.3", "vid": "1546", "pid": "01a7", "name": "u-blox 7", "classes": ["comms"]}]
        _uw.audio.sound_cards = lambda: [{"index": 0, "id": "vc4hdmi", "driver": "vc4-hdmi", "name": "vc4-hdmi", "usb": None}]
        try:
            first = c.get("/api/usb/watch?since=-1").get_json() or {}
            log += ["[  130.000000] usb 1-1.4: new full-speed USB device number 5 using dwc2",
                    "[  130.500000] usb 1-1.4: device descriptor read/64, error -32", "[  131.000000] usb 1-1-port4: attempt power cycle",
                    "[  133.000000] usb 1-1-port4: unable to enumerate USB device", "[  140.000000] usb 1-1.1: new full-speed USB device number 6 using dwc2",
                    "[  140.300000] usb 1-1.1: Product: UACDemoV1.0", "[  140.400000] random: crng init done"]
            _uw.audio.sound_cards = lambda: [{"index": 0, "id": "vc4hdmi", "driver": "vc4-hdmi", "name": "vc4-hdmi", "usb": None},
                                             {"index": 1, "id": "UACDemoV10", "driver": "USB-Audio", "name": "UACDemoV1.0", "usb": "1b3f:2008"}]
            then = c.get(f"/api/usb/watch?since={first.get('now')}").get_json() or {}
            said = [f"{e['where']}: {e['text']}" for e in then.get("events", [])]
            okay = (first.get("now") == 100.2 and not first.get("events") and then.get("now") == 140.4 and len(said) == 6
                    and said[0] == "port 4 of the hub: something plugged in (full speed)"
                    and said[1] == "port 4 of the hub: it wouldn't answer (error -32: no proper answer from it)"
                    and said[3] == "port 4 of the hub: the Pi gave up on it" and said[5] == "port 1 of the hub: it's UACDemoV1.0"
                    and [x["name"] for x in then.get("cards", [])] == ["UACDemoV1.0"]
                    and [u["where"] for u in first.get("devices", [])] == ["the Pi's port", "port 3 of the hub"])
            print(f"  {'ok  ' if okay else 'FAIL'} watching the USB bus: only what happens from now on, each line in plain words, "
                  f"and the sound card once it's there")
            if not okay:
                print(f"       {first} {then}")
                FAILED.append("usb watch")
        finally:
            _uw._kernel_raw, _uw.audio.sound_cards, _uw.camera.usb_devices = raw_was, cards_was, devs_was
        check("POST /api/draw/describe (no bot)", c.post("/api/draw/describe", json={"image": ""}), want=(400,))

        print("the rest")
        check("GET /api/music", c.get("/api/music"), body_has=["tracks", "now"])
        check("POST /api/music/toggle", c.post("/api/music/toggle", json={}))

        print("Play on this device (a page playing the music)")
        import tempfile as _tf
        import shutil as _sh
        import time as _t
        from pie_ink import here as _here
        mdir = _tf.mkdtemp(prefix="pie-ink-music-")
        for name in ("01 - One.mp3", "02 - Two.mp3"):
            with open(os.path.join(mdir, name), "wb") as fh:
                fh.write(b"ID3" + bytes(4000))
        folder_was = pieapp.service.config.get("music", {}).get("folder", "")
        try:
            c.put("/api/settings", json={"music": {"folder": mdir}})
            c.post("/api/music/stop", json={})
            d = c.get("/api/audio/here?id=smoke&after=-1&mv=-1").get_json() or {}
            okay = d.get("ok") and (d.get("music") or {}).get("state") == "stopped" and _here.HERE.listening()
            now = (c.post("/api/music/play", json={"index": 0}).get_json() or {}).get("now") or {}
            d2 = c.get(f"/api/audio/here?id=smoke&after={d.get('seq', 0)}&mv={d.get('mv', 0)}").get_json() or {}
            m = d2.get("music") or {}
            okay = okay and now.get("on_page") and m.get("state") == "playing" and m.get("path") == "01 - One.mp3"
            print(f"  {'ok  ' if okay else 'FAIL'} a page listening: the track plays in it (the Pi just keeps track), "
                  f"and the page hears at once")
            if not okay:
                print(f"       {d} {now} {d2}")
                FAILED.append("here music")
            f = c.get("/api/music/file?p=01%20-%20One.mp3", headers={"Range": "bytes=0-99"})
            bad = [c.get("/api/music/file?p=" + q).status_code for q in ("..%2Fconfig.yaml", "nothing.mp3", "")]
            okay = f.status_code == 206 and len(f.data) == 100 and bad == [404, 404, 404] \
                and c.get("/api/audio/here/clip/999999").status_code == 404
            print(f"  {'ok  ' if okay else 'FAIL'} the page gets the file (with ranges, to seek), and only files in the list")
            if not okay:
                FAILED.append("here music file")
            c.post("/api/music/here", json={"v": m.get("v"), "position": 3.0, "duration": 180.0})
            st = (c.get("/api/music").get_json() or {}).get("now") or {}
            stale = (c.post("/api/music/here", json={"v": (m.get("v") or 0) - 1, "ended": True}).get_json() or {}).get("took")
            c.post("/api/music/here", json={"v": m.get("v"), "ended": True})
            st2 = (c.get("/api/music").get_json() or {}).get("now") or {}
            okay = 3.0 <= st.get("position", 0) < 4.5 and st.get("duration") == 180.0 and stale is False \
                and st2.get("index") == 1 and st2.get("on_page") and st2.get("state") == "playing"
            print(f"  {'ok  ' if okay else 'FAIL'} the page says how far it got, and when the track ends the next one "
                  f"starts; a report about an older track is ignored")
            if not okay:
                print(f"       {st} {stale} {st2}")
                FAILED.append("here music reports")
            c.post("/api/audio/here/off", json={"id": "smoke"})
            for _ in range(30):
                if (c.get("/api/music").get_json() or {}).get("now", {}).get("state") == "paused":
                    break
                _t.sleep(0.1)
            st3 = (c.get("/api/music").get_json() or {}).get("now") or {}
            okay = not _here.HERE.listening() and st3.get("state") == "paused" and st3.get("index") == 1
            print(f"  {'ok  ' if okay else 'FAIL'} the page switches it off: the music pauses (not out of the Pi's "
                  f"speaker all of a sudden)")
            if not okay:
                FAILED.append("here off pauses")
        finally:
            _here.HERE.leave("smoke")
            c.post("/api/music/stop", json={})
            c.put("/api/settings", json={"music": {"folder": folder_was}})
            _sh.rmtree(mdir, ignore_errors=True)
        check("GET /api/weather", c.get("/api/weather"), want=(200, 400, 502))
        check("GET /api/books", c.get("/api/books"))
        check("GET /api/watchlist", c.get("/api/watchlist"))
        check("GET /api/system", c.get("/api/system"), body_has=["cpu"])
        check("GET /api/friends", c.get("/api/friends"))
        check("GET /api/social", c.get("/api/social"), body_has=["history", "talking"])
        check("POST /api/social/show", c.post("/api/social/show"))

        print("the second screen")
        import time as _t
        import threading as _th
        _t.sleep(1.0)                                  # the LCD thread draws its first frame
        r = c.get("/api/lcd")
        check("GET /api/lcd", r, body_has=["status", "screens"])
        st = (r.get_json() or {}).get("status", {})
        if not st.get("running") or st.get("frames", 0) < 1 or st.get("error"):
            print(f"  FAIL the LCD loop is not drawing: {st}")
            FAILED.append("lcd loop")
        check("GET /api/lcd/preview.png", c.get("/api/lcd/preview.png"))
        for screen in ("system", "bot", "chat", "crypto", "weather", "camera", "paint", "drawing", "mirror", "clock", "testcard"):
            r = c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": screen, "light_mode": screen == "bot"}}})
            check(f"POST /api/lcd/preview.png {screen}", r)
        # painting on it: a picture goes up, the LCD switches to its Paint screen, the picture comes back
        import base64 as _b64
        import io as _io
        from PIL import Image as _Image
        buf = _io.BytesIO()
        _Image.new("RGB", (240, 280), (255, 200, 0)).save(buf, "PNG")
        png = "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        r = c.post("/api/paint", json={"image": png})
        check("POST /api/paint", r, body_has=["version", "shown"])
        if not (r.get_json() or {}).get("shown"):
            print("  FAIL painting did not switch the LCD to its Paint screen")
            FAILED.append("paint shown")
        r = c.get("/api/paint.png")
        check("GET /api/paint.png", r)
        if r.status_code == 200:
            back = _Image.open(_io.BytesIO(r.data))
            if back.size != (240, 280) or back.convert("RGB").getpixel((5, 5)) != (255, 200, 0):
                print(f"  FAIL the painting came back different: {back.size} {back.convert('RGB').getpixel((5, 5))}")
                FAILED.append("paint round trip")
        check("POST /api/paint (junk)", c.post("/api/paint", json={"image": "data:image/png;base64,AAAA"}), want=(400,))
        # the other panel: the loop restarts on it and every screen fits its shape
        check("PUT /api/settings lcd model 1.9", c.put("/api/settings", json={"lcd": {"model": "1.9", "screen": "system"}}))
        _t.sleep(1.0)
        r = c.get("/api/lcd")
        st = (r.get_json() or {}).get("status", {})
        if st.get("size") != [170, 320] or st.get("error"):
            print(f"  FAIL the 1.9\" panel did not come up: {st}")
            FAILED.append("lcd 1.9")
        for rot in (0, 90):
            for screen in ("system", "clock", "weather", "crypto", "bot", "chat", "paint", "mirror", "testcard"):
                r = c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": screen, "rotation": rot}}})
                if r.status_code == 200:
                    got = _Image.open(_io.BytesIO(r.data)).size
                    want = (170, 320) if rot == 0 else (320, 170)
                    if got != want:
                        print(f"  FAIL {screen} at {rot}° came out {got}, not {want}")
                        FAILED.append(f"lcd 1.9 {screen}")
                else:
                    check(f"POST /api/lcd/preview.png 1.9 {screen} {rot}", r)
        print("  ok   every screen fits the 1.9\" panel both ways round")
        check("PUT /api/settings lcd model 1.69", c.put("/api/settings", json={"lcd": {"model": "1.69"}}))
        check("PUT /api/settings lcd off", c.put("/api/settings", json={"lcd": {"enabled": False}}))

        print("the Whisplay HAT as the screen")
        r = c.put("/api/settings", json={"display": {"driver": "mock_whisplay", "rotation": 90}})
        check("PUT /api/settings display whisplay", r, body_has=["settings"])
        _t.sleep(1.0)
        panel = (c.get("/api/status").get_json() or {}).get("service", {}).get("panel", {})
        if not panel.get("colour") or panel.get("glass") != [280, 240] or (panel.get("width"), panel.get("height")) != (260, 220):
            print(f"  FAIL the panel did not become the sideways Whisplay (glass 280x240, drawing area 260x220): {panel}")
            FAILED.append("whisplay panel")
        for mode in ("clock", "system", "weather", "crypto", "gps", "message", "image", "me"):
            r = c.post("/api/render.png", json={"mode": mode})
            check(f"POST /api/render.png {mode} (whisplay)", r)
            if r.status_code == 200:
                im = _Image.open(_io.BytesIO(r.data))
                if im.size != (280 * 3, 240 * 3):
                    print(f"  FAIL {mode} preview is {im.size}")
                    FAILED.append(f"whisplay {mode}")
                if mode == "system" and im.mode != "RGB":
                    print(f"  FAIL the system screen should be in colour here, got {im.mode}")
                    FAILED.append("whisplay colour")
        buf = _io.BytesIO()
        _Image.new("RGB", (260, 220), (30, 130, 230)).save(buf, "PNG")
        r = c.post("/api/paint", json={"image": "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()})
        check("POST /api/paint (whisplay)", r, body_has=["shown"])
        _t.sleep(1.5)
        mode_now = (c.get("/api/status").get_json() or {}).get("service", {}).get("mode")
        if mode_now != "image":
            print(f"  FAIL painting should have put the drawing on screen, mode is {mode_now}")
            FAILED.append("whisplay paint")
        r = c.get("/api/preview.png")
        if r.status_code == 200:
            im = _Image.open(_io.BytesIO(r.data)).convert("RGB")
            if im.getpixel((im.width // 2, im.height // 2)) != (30, 130, 230):
                print(f"  FAIL the painting is not on the screen: {im.getpixel((im.width // 2, im.height // 2))}")
                FAILED.append("whisplay paint shown")
        check("GET /api/whisplay", c.get("/api/whisplay"), body_has=["status", "panel", "sound"])
        check("POST /api/whisplay/sound (no card here)", c.post("/api/whisplay/sound"), want=(200, 400))
        check("GET /api/lcd (no second screen)", c.get("/api/lcd"))
        check("PUT /api/settings display mock again", c.put("/api/settings", json={"display": {"driver": "mock", "rotation": 0}}))
        _t.sleep(0.5)

        print("the GPS")
        pieapp.gps.GPS.start(pieapp._gps_conf(pieapp.service.config))     # as at boot
        _t.sleep(0.5)
        r = c.get("/api/gps")
        check("GET /api/gps (auto, nothing plugged in)", r, body_has=["status", "devices"])
        st0 = (r.get_json() or {}).get("status", {})
        if st0.get("mode") != "auto" or st0.get("enabled") or not st0.get("running") or st0.get("device"):
            print(f"  FAIL out of the box the GPS should be Auto — the reader waiting, nothing to report: {st0}")
            FAILED.append("gps auto")
        else:
            print("  ok   out of the box: Auto — the reader waits for a receiver to be plugged in, and says nothing till then")
        if any("GPS" in ln for ln in pieapp.mascot.context(pieapp.service.config)):
            print("  FAIL with no receiver plugged in the bot shouldn't hear about the GPS")
            FAILED.append("gps auto in the bot's context")
        from pie_ink import gps as _gps
        if [_gps.mode({"enabled": v}) for v in (True, False, "auto", "on", "off", "true", "false", None)] != ["on", "off", "auto", "on", "off", "on", "off", "off"]:
            FAILED.append("gps mode words")
        check("PUT /api/settings gps on", c.put("/api/settings", json={"gps": {"enabled": True, "set_weather": True},
                                                                        "weather": {"lat": None, "lon": None, "location": ""}}))
        _t.sleep(4.5)                                  # the pretend receiver finds its satellites
        r = c.get("/api/gps")
        check("GET /api/gps (on)", r, body_has=["status"])
        st = (r.get_json() or {}).get("status", {})
        if st.get("fix") == "none" or not st.get("connected"):
            print(f"  FAIL the pretend receiver never got a fix: {st}")
            FAILED.append("gps fix")
        w = (c.get("/api/status").get_json() or {}).get("settings", {}).get("weather", {})
        if w.get("lat") is None or abs(float(w["lat"]) - 32.7157) > 0.01:
            print(f"  FAIL the weather did not follow the GPS: {w}")
            FAILED.append("gps weather")
        else:
            print(f"  ok   the weather followed the GPS to {w.get('location')!r}")
        r = c.get("/api/gps/probe")
        check("GET /api/gps/probe", r, body_has=["text", "verdict", "status"])
        if "Verdict:" not in ((r.get_json() or {}).get("text") or ""):
            FAILED.append("gps probe text")
        if not ((r.get_json() or {}).get("status") or {}).get("running"):
            print("  FAIL the probe left the reader stopped")
            FAILED.append("gps probe reader")
        check("POST /api/mode gps", c.post("/api/mode", json={"mode": "gps"}))
        check("POST /api/render.png gps", c.post("/api/render.png", json={"mode": "gps"}))
        check("POST /api/lcd/preview.png gps", c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": "gps"}}}))
        check("POST /api/llm/chat (no model)", c.post("/api/llm/chat", json={"message": "where am I"}), want=(400,))
        check("PUT /api/settings gps off", c.put("/api/settings", json={"gps": {"enabled": False}}))
        if (c.get("/api/gps").get_json() or {}).get("status", {}).get("mode") != "off":
            FAILED.append("gps off")

        print("where the Pi is, without being told")
        check("POST /api/weather/place (cleared)", c.post("/api/weather/place", json={}))
        real_net = pieapp.locate.from_network
        answers = [{"lat": 34.0522, "lon": -118.2437, "place": "Los Angeles, California", "country": "US", "at": _t.time()}]
        pieapp.locate.from_network = lambda cached_only=False: answers[-1]
        try:
            got = pieapp._auto_locate()
            w = pieapp.service.config.get("weather", {})
            okay = got and w.get("location") == "Los Angeles, California" and w.get("source") == "network" and abs(float(w["lat"]) - 34.0522) < 0.001
            print(f"  {'ok  ' if okay else 'FAIL'} nothing set: the network puts it near {w.get('location')!r} and the weather goes there")
            if not okay:
                FAILED.append("locate from the network")
            if pieapp._auto_locate() is not None:
                print("  FAIL the same answer again should change nothing")
                FAILED.append("locate same place")
            answers.append({"lat": 32.7157, "lon": -117.1611, "place": "San Diego, California", "country": "US", "at": _t.time()})
            got = pieapp._auto_locate()
            w = pieapp.service.config.get("weather", {})
            okay = got and w.get("location") == "San Diego, California"
            print(f"  {'ok  ' if okay else 'FAIL'} the Pi moved house (the network says 180 km away): the weather follows — {w.get('location')!r}")
            if not okay:
                FAILED.append("locate moved")
            check("POST /api/weather/place (typed)", c.post("/api/weather/place", json={"name": "Portland", "admin": "Oregon", "country": "United States",
                                                                                       "country_code": "US", "lat": 45.5152, "lon": -122.6784}))
            w = pieapp.service.config.get("weather", {})
            answers.append({"lat": 34.0522, "lon": -118.2437, "place": "Los Angeles, California", "country": "US", "at": _t.time()})
            okay = w.get("source") == "typed" and pieapp._auto_locate() is None and pieapp.service.config["weather"]["location"] == "Portland, Oregon"
            print(f"  {'ok  ' if okay else 'FAIL'} a town you typed stays put whatever the network says ({pieapp.service.config['weather']['location']!r})")
            if not okay:
                FAILED.append("locate typed stays")
            r = c.post("/api/weather/locate", json={})
            check("POST /api/weather/locate (Find me, no GPS fix)", r, body_has=["location", "source"])
            d = r.get_json() or {}
            if d.get("source") != "network" or d.get("location") != "Los Angeles, California":
                print(f"  FAIL Find me should take the network's answer without a GPS fix: {d}")
                FAILED.append("locate find me")
            else:
                print(f"  ok   Find me: {d['location']} (from the network)")
        finally:
            pieapp.locate.from_network = real_net
        # the real lookup, when the network is reachable (it may not be here): a place and a position, or a quiet None
        try:
            loc = pieapp.locate.from_network()
            print(f"  ok   the network lookup itself: {loc['place']} ({loc['lat']}, {loc['lon']})" if loc else "  ok   the network lookup itself: no answer here (no internet in this sandbox) — fine")
        except Exception as e:
            print(f"  FAIL the lookup should never raise: {e}")
            FAILED.append("locate raises")
        check("POST /api/weather/place (cleared again)", c.post("/api/weather/place", json={}))


        print("what the camera sees, the journey, the agent, the mesh")
        ollama = _FakeOllama()
        ollama.start()
        try:
            from PIL import Image as _Image
            from pie_ink import agent as _agent, friends as _friends, journey as _journey, sight as _sight
            check("PUT /api/settings llm (the fake model)", c.put("/api/settings", json={
                "llm": {"enabled": True, "host": f"http://127.0.0.1:{ollama.port}", "model": "fake:latest", "see": True},
                "camera_watch": {"events": True, "detector": "model"}, "me": {"name": "Tester", "location": "test bench"}}))
            _sight.clear()
            frame = _Image.new("RGB", (320, 240), (90, 120, 150))
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            texts = [e["text"] for e in entries]
            if obs is None or obs.get("people") != 1 or "Person detected" not in texts or "Cat spotted" not in texts:
                print(f"  FAIL the first look should log a person and a cat: {obs} {texts}")
                FAILED.append("sight first look")
            else:
                print(f"  ok   a look through the fake model: {_sight.describe(obs)} -> {', '.join(texts)}")
            ollama.scene = dict(ollama.scene, people=0, animals=[], packages=1, door="open")
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            texts = [e["text"] for e in entries]
            want = {"Nobody detected", "Cat left", "Package detected", "Door opened"}
            if not want.issubset(set(texts)):
                print(f"  FAIL the second look should log what changed: {texts}")
                FAILED.append("sight changes")
            else:
                print(f"  ok   only the changes become events: {', '.join(texts)}")
            ollama.scene = dict(ollama.scene, people=1)
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            if "Person returned" not in [e["text"] for e in entries]:
                print(f"  FAIL a person after a 'nobody' is a return: {[e['text'] for e in entries]}")
                FAILED.append("sight returned")
            else:
                print("  ok   a person after 'Nobody detected' is 'Person returned'")
            r = c.get("/api/events")
            check("GET /api/events", r, body_has=["events", "status", "detector"])
            if len((r.get_json() or {}).get("events") or []) < 6:
                FAILED.append("events listed")
            check("POST /api/events/look (no real camera here)", c.post("/api/events/look", json={}), want=(400,))
            if not _sight.summary():
                FAILED.append("events summary")
            # the journey: a short drive, then the day as numbers, a sentence and a map
            _journey.JOURNEY.configure({"enabled": True, "min_metres": 20})
            _journey.JOURNEY.clear()
            t0 = _t.time() - 1800
            for i in range(40):
                _journey.JOURNEY.record({"lat": 32.7157 + i * 0.0006, "lon": -117.1611 + i * 0.0004, "speed_kmh": 35.0, "alt_m": 10}, now=t0 + i * 15)
            _journey.JOURNEY.flush()
            r = c.get("/api/journey")
            check("GET /api/journey", r, body_has=["points", "stats", "summary", "days"])
            d = r.get_json() or {}
            if len(d.get("points") or []) < 30 or not (d.get("stats") or {}).get("distance_km") or "set off" not in (d.get("summary") or ""):
                print(f"  FAIL the journey should have a track and a sentence: {d.get('stats')} {d.get('summary')!r}")
                FAILED.append("journey")
            else:
                print(f"  ok   {d['summary']}")
            check("POST /api/render.png map (with the trail)", c.post("/api/render.png", json={"mode": "map"}))
            check("POST /api/lcd/preview.png map (with the trail)", c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": "map"}}}))
            check("GET /api/journey?day=2000-01-01 (nothing)", c.get("/api/journey?day=2000-01-01"), body_has=["points"])
            check("GET /api/journey?day=bad", c.get("/api/journey?day=yesterday"), want=(400,))
            tg = pieapp._tg_command("/journey")
            if not (isinstance(tg, tuple) and tg[0] and "set off" in tg[1]):
                print(f"  FAIL /journey should be a map and a line: {str(tg)[:80]}")
                FAILED.append("telegram /journey")
            else:
                print("  ok   /journey → a map and a line")
            check("DELETE /api/journey", c.delete("/api/journey"), body_has=["days"])
            # the agent: one check against the fake model, with the actions it is allowed
            check("PUT /api/settings agent on", c.put("/api/settings", json={"agent": {"enabled": True, "every_minutes": 30, "look": False,
                                                                                       "can_notify": True, "can_show": True, "can_say": False,
                                                                                       "goals": "Keep an eye on the room.\nTell me when the cat is about."}}))
            _t.sleep(0.3)
            entry = _agent.AGENT.tick("test")
            if not entry or entry.get("error") or entry.get("level") != "note" or "cat" not in entry.get("thought", "").lower():
                print(f"  FAIL the agent's check went wrong: {entry}")
                FAILED.append("agent tick")
            else:
                done = {a["do"]: a["result"] for a in entry["actions"]}
                if done.get("show") != "on the screen" or "Telegram" not in done.get("notify", ""):
                    print(f"  FAIL the agent's actions: {done}")
                    FAILED.append("agent actions")
                else:
                    print(f"  ok   the agent thought '{entry['thought']}' and acted: {done}")
            report = next((p for p in ollama.prompts if "Report at" in (p.get("prompt") or "")), None)
            if not report or "Events" not in report["prompt"] or "Keep an eye on the room" not in (report.get("system") or ""):
                print("  FAIL the agent's report should carry the events and the goals")
                FAILED.append("agent report")
            r = c.get("/api/agent")
            check("GET /api/agent", r, body_has=["status", "journal"])
            if not (r.get_json() or {}).get("journal"):
                FAILED.append("agent journal")
            check("POST /api/agent/run", c.post("/api/agent/run", json={}), body_has=["started"])
            _t.sleep(1.5)
            tg = pieapp._tg_command("/agent")
            if "cat" not in str(tg).lower():
                print(f"  FAIL /agent should tell the last thought: {tg!r}")
                FAILED.append("telegram /agent")
            check("GET /api/now (with the agent and the events)", c.get("/api/now"), body_has=["event", "agent"])
            check("PUT /api/settings agent off", c.put("/api/settings", json={"agent": {"enabled": False}}))
            check("DELETE /api/agent", c.delete("/api/agent"))
            # the mesh: another Pi on the network — this one, through a real socket — and what you can ask of it
            from werkzeug.serving import make_server
            srv = make_server("127.0.0.1", 0, pieapp.app, threaded=True)
            _th.Thread(target=srv.serve_forever, daemon=True).start()
            with _friends._lock:
                _friends._peers["fake-peer"] = {"id": "fake-peer", "host": "pie-up", "ip": "127.0.0.1", "port": srv.server_port, "version": "x",
                                                "panel": '2.13" e-ink', "mode": "clock", "where": None, "seen": _t.time(),
                                                "caps": {"location": "upstairs", "camera": True, "gps": False, "speaker": False, "mic": False,
                                                         "llm": True, "home": False, "agent": False, "lcd": "", "battery": None}}
            try:
                check("GET /api/mesh", c.get("/api/mesh"), body_has=["me", "pis"])
                asks = {"list the pis": "pie-up", "which pi has a camera": "pie-up", "find the pi with a gps": "No Pi",
                        "where is the upstairs pi": "hasn't said", "show me the camera from upstairs": "on the panel",
                        "say hello on the upstairs pi": "pie-up", "show the clock on the upstairs pi": "Showing clock",
                        "send this drawing to the upstairs pi": "drawing"}
                for ask, want in asks.items():
                    r = c.post("/api/llm/chat", json={"message": ask})
                    reply = (r.get_json() or {}).get("reply") or (r.get_json() or {}).get("error") or ""
                    okay = r.status_code == 200 and want.lower() in reply.lower()
                    print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply[:90]!r}")
                    if not okay:
                        FAILED.append(f"mesh: {ask}")
                    if ask.startswith("show me the camera") and pieapp.service.mode_name != "postcard":
                        print(f"  FAIL the friend's camera should be on the panel (postcard), not {pieapp.service.mode_name}")
                        FAILED.append("mesh camera on panel")
                tg = pieapp._tg_command("show me the camera from upstairs")
                if not (isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff"):
                    print(f"  FAIL from Telegram the camera should come back as a picture: {str(tg)[:60]}")
                    FAILED.append("telegram mesh camera")
                else:
                    print("  ok   from Telegram the friend's camera comes back as a picture")
                if "pie-up" not in pieapp._tg_command("/pis"):
                    FAILED.append("telegram /pis")
                r = c.post("/api/llm/chat", json={"message": "what can you see"})     # not a mesh thing: the bot answers
                if "pie-up" in ((r.get_json() or {}).get("reply") or ""):
                    print(f"  FAIL 'what can you see' is for the bot itself, not the mesh: {r.get_json()}")
                    FAILED.append("mesh not greedy")
                else:
                    print("  ok   'what can you see' goes to the bot, not the mesh")
            finally:
                with _friends._lock:
                    _friends._peers.pop("fake-peer", None)
                srv.shutdown()
            check("PUT /api/settings llm off", c.put("/api/settings", json={"llm": {"enabled": False}}))
        finally:
            ollama.stop()


        print("the miners: duino-coin and verus")
        from pie_ink import mining as _mining
        fake_pool = _FakeDuco()
        fake_pool.start()
        real = (_mining.DUCO_USER_URL, _mining.DUCO_NET_URL, _mining.VERUS_URL, _mining.DATA_DIR)
        _mining.DUCO_USER_URL = f"http://127.0.0.1:{fake_pool.port}/v3/users/{{user}}"
        _mining.DUCO_NET_URL = f"http://127.0.0.1:{fake_pool.port}/api.json"
        _mining.VERUS_URL = f"http://127.0.0.1:{fake_pool.port}/verus/miner/{{wallet}}"
        _mining.DATA_DIR = tempfile.mkdtemp(prefix="pie-ink-mining-")
        try:
            check("POST /api/mining/check (no such user)", c.post("/api/mining/check", json={"pool": "duco", "who": "nobody"}), want=(400,))
            r = c.post("/api/mining/check", json={"pool": "duco", "who": "tester"})
            check("POST /api/mining/check duco", r, body_has=["data", "miners", "words"])
            if "450 H/s" not in ((r.get_json() or {}).get("words") or ""):
                print(f"  FAIL the look-up should add the miners up: {r.get_json()}")
                FAILED.append("duco check")
            r = c.post("/api/mining/check", json={"pool": "verus", "who": "RTESTWALLET"})
            check("POST /api/mining/check verus", r, body_has=["data", "miners", "words"])
            if "34.42 MH/s" not in ((r.get_json() or {}).get("words") or "") or len((r.get_json() or {}).get("miners") or []) != 2:
                print(f"  FAIL luckpool's answer should parse: {r.get_json()}")
                FAILED.append("verus check")
            else:
                print(f"  ok   luckpool parsed: {(r.get_json() or {}).get('words')}")
            check("POST /api/mining/check (unknown wallet)", c.post("/api/mining/check", json={"pool": "verus", "who": "RNOBODY"}), want=(400,))
            check("GET /api/mining (off)", c.get("/api/mining"), body_has=["pools"])
            check("PUT /api/settings both pools on", c.put("/api/settings", json={
                "duco": {"enabled": True, "username": "tester", "min_hashrate": 300},
                "verus": {"enabled": True, "wallet": "RTESTWALLET", "min_hashrate": 20000000}}))
            _t.sleep(2.5)                                     # the threads' first readings
            r = c.get("/api/mining")
            check("GET /api/mining (on)", r, body_has=["pools"])
            pools = (r.get_json() or {}).get("pools") or {}
            dd = (pools.get("duco") or {}).get("data") or {}
            vv = (pools.get("verus") or {}).get("data") or {}
            if dd.get("hashrate") != 450.5 or dd.get("workers") != 2 or dd.get("price") != 0.00041 or dd.get("stake_amount") is not None and dd.get("stake_amount") <= 0:
                print(f"  FAIL the duino reading is wrong: {dd}")
                FAILED.append("duco reading")
            else:
                print(f"  ok   duino: {dd['who']} {dd['balance']} DUCO, {dd['hashrate_text']} from {dd['workers']} workers")
            if abs(vv.get("hashrate", 0) - 34419715.2) > 1 or vv.get("workers") != 2 or vv.get("paid") != 12.3456 or vv.get("luck") != "87.5%" \
                    or [w["identifier"] for w in vv.get("miners", [])] != ["ryzen", "pi5"]:
                print(f"  FAIL the verus reading is wrong: {vv}")
                FAILED.append("verus reading")
            else:
                print(f"  ok   verus: {vv['who']} {vv['balance']} VRSC unpaid, {vv['hashrate_text']} from {vv['workers']} workers, luck {vv['luck']}")
            check("POST /api/render.png crypto (the mining pages are in the rotation)", c.post("/api/render.png", json={"mode": "crypto"}))
            from pie_ink.modes import crypto as _cm
            if [i.get("mining") for i in _cm.CryptoMode._items() if i.get("mining")] != ["duco", "verus"]:
                FAILED.append("mining in the e-ink rotation")
            from PIL import Image as _Image
            probe = _cm.CryptoMode(pieapp.service.config, pieapp.service.panel.info())
            for m in _mining.MINERS.values():
                try:
                    img = probe._render_mining(probe.frame(), m, _t.time(), False)
                    if not isinstance(img, _Image.Image):
                        FAILED.append(f"{m.kind} e-ink page")
                except Exception as e:
                    print(f"  FAIL the {m.kind} e-ink page: {e}")
                    FAILED.append(f"{m.kind} e-ink page")
            print("  ok   both mining pages draw on the e-ink")
            from pie_ink import lcd_screens as _ls
            for m in _mining.MINERS.values():
                for wh in ((240, 280), (170, 320), (320, 240)):
                    try:
                        _ls._crypto_mining({"model": "1.69"}, wh, ((0, 0, 0), (255, 255, 255), (98, 210, 255), (140, 140, 140), (0, 255, 0), (255, 80, 80)), m)
                    except Exception as e:
                        print(f"  FAIL the {m.kind} LCD page at {wh}: {e}")
                        FAILED.append(f"{m.kind} lcd page")
            print("  ok   both mining pages draw on the LCD at three sizes")
            # the alert, on verus this time: two readings under the line, then two back over it
            fired = []
            orig = _mining.VERUS.on_alert
            _mining.VERUS.on_alert = lambda kind, text, data: (fired.append((kind, text)), orig(kind, text, data))
            fake_pool.verus_sols = 8000000.0
            _mining.VERUS.fetch()
            _mining.VERUS.fetch()
            fake_pool.verus_sols = 34419715.2
            _mining.VERUS.fetch()
            _mining.VERUS.fetch()
            _mining.VERUS.on_alert = orig
            kinds = [k for k, _ in fired]
            if kinds[:1] != ["drop"] or "back" not in kinds or "Verus" not in fired[0][1]:
                print(f"  FAIL the verus hashrate alert: {fired}")
                FAILED.append("verus alert")
            else:
                print(f"  ok   verus under the line: '{fired[0][1][:70]}…' then '{[t for k, t in fired if k == 'back'][0][:44]}'")
            # and nothing mining at all on duino
            fired.clear()
            orig = _mining.DUCO.on_alert
            _mining.DUCO.on_alert = lambda kind, text, data: (fired.append((kind, text)), orig(kind, text, data))
            kept = fake_pool.miners
            fake_pool.miners = []
            _mining.DUCO.fetch()
            _mining.DUCO.fetch()
            fake_pool.miners = kept
            _mining.DUCO.on_alert = orig
            _mining.DUCO.fetch()                              # the miners are back for the rest of the checks
            if not fired or fired[0][0] != "drop" or "Nothing is mining Duino-Coin" not in fired[0][1]:
                print(f"  FAIL nothing-mining should be said: {fired}")
                FAILED.append("duco nothing mining")
            ev = [e["text"] for e in pieapp.sight.events(limit=6, kinds=["mining"])]
            if not any("dropped" in t for t in ev) or not any("Nothing is mining" in t for t in ev):
                print(f"  FAIL the drops should be in the events list: {ev}")
                FAILED.append("mining events")
            hist = [m["text"] for m in pieapp.chat.history()[-8:] if m["role"] == "bot"]
            if not any("dropped" in t for t in hist):
                print(f"  FAIL the bot should have remarked on it: {hist}")
                FAILED.append("mining remark")
            tg = pieapp._tg_command("/mining")
            if "tester" not in tg or "ESP32-1" not in tg or "ryzen" not in tg or "luck 87.5%" not in tg:
                print(f"  FAIL /mining: {tg!r}")
                FAILED.append("telegram /mining")
            if "ryzen" not in pieapp._tg_command("/verus") or "ESP32-1" in pieapp._tg_command("/verus"):
                FAILED.append("telegram /verus")
            words = pieapp.mascot.context(pieapp.service.config)
            if not any("Duino-Coin mining (tester)" in ln for ln in words) or not any("Verus mining (" in ln for ln in words):
                print(f"  FAIL both pools should be in the bot's context: {[w for w in words if 'mining' in w]}")
                FAILED.append("mining in the bot's context")
            r = c.get("/api/now")
            check("GET /api/now (with mining)", r, body_has=["mining"])
            if len((r.get_json() or {}).get("mining") or []) != 2:
                FAILED.append("both pools in the sidebar")
            check("PUT /api/settings pools off", c.put("/api/settings", json={"duco": {"enabled": False}, "verus": {"enabled": False}}))
        finally:
            for m in _mining.MINERS.values():
                m.stop()
            _mining.DUCO_USER_URL, _mining.DUCO_NET_URL, _mining.VERUS_URL, _mining.DATA_DIR = real
            fake_pool.stop()

        print("your crypto: what you hold, what it is worth, and a word when that moves")
        from pie_ink import holdings as _hold
        r = c.put("/api/watchlist/bitcoin", json={"amount": 0.5})
        check("PUT /api/watchlist/bitcoin (you hold 0.5)", r, body_has=["items"])
        btc = next((i for i in (r.get_json() or {}).get("items", []) if i["id"] == "bitcoin"), {})
        r = c.get("/api/holdings")
        check("GET /api/holdings", r, body_has=["any", "value", "history", "line", "share"])
        hd = r.get_json() or {}
        row = next((x for x in hd.get("value", {}).get("items", []) if x["id"] == "bitcoin"), None)
        okay = btc.get("amount") == 0.5 and hd.get("any") and row and row["amount"] == 0.5 and row.get("price") \
            and abs(row["value"] - 0.5 * row["price"]) < 0.01 and abs(hd["value"]["total"] - row["value"]) < 0.01 and hd["line"] == 1000000
        print(f"  {'ok  ' if okay else 'FAIL'} 0.5 BTC at {_hold.money(row['price']) if row else '?'} is worth "
              f"{_hold.money(row['value']) if row else '?'} — the total, with a $1,000,000 line")
        if not okay:
            FAILED.append("holdings value")
        check("PUT /api/watchlist/bitcoin (a bad amount)", c.put("/api/watchlist/bitcoin", json={"amount": "lots"}), want=(400,))
        # the moments worth a word, on a watcher of its own with made-up totals
        real_path = _hold.PATH
        _hold.PATH = os.path.join(tempfile.mkdtemp(prefix="pie-ink-holdings-"), "holdings.json")
        try:
            h = _hold.Holdings()
            h.configure({"milestone": 1000000, "move_pct": 10})
            said = []
            h.on_alert = lambda kind, text, data: said.append((kind, text))

            def look(total, pct=1.0, when=None):
                v = {"total": total, "day": total * pct / 100, "day_pct": pct, "when": when or _t.time(), "unknown": [], "coins": 2,
                     "items": [{"symbol": "BTC", "value": total * 0.8, "change_1d": pct, "amount": 1}, {"symbol": "ETH", "value": total * 0.2, "change_1d": pct, "amount": 5}]}
                h.value_now = v
                with h._lock:
                    h._sample(total, v["when"])
                    fire = h._watch(v, v["when"])
                    h._save()
                for kind, text in fire:
                    h.on_alert(kind, text, v)
            t0 = _t.time() - 86400 * 3
            at = lambda k: t0 + k * 4000                                 # readings well apart (the same word waits hours)  # noqa: E731
            look(900000, 2.0, at(0))                                     # the first look: under the line, no news
            look(1100000, 3.0, at(1)); look(1100000, 3.0, at(2))         # two readings over it: rich
            look(950000, -1.0, at(3)); look(950000, -1.0, at(4))         # and two back under (more than 2 % under)
            kinds = [k for k, _ in said]
            okay = kinds == ["rich", "under"] and "$1,000,000" in said[0][1] and "You're rich" in said[0][1] and "$950,000" in said[1][1]
            print(f"  {'ok  ' if okay else 'FAIL'} past the line and back under it: {[t[:60] for _, t in said]}")
            if not okay:
                FAILED.append("holdings milestone")
            said.clear()
            look(836000, -12.0, at(5)); look(836000, -12.0, at(6))       # a crash: two readings down 12 %
            look(830000, -12.5, at(7))                                   # still down: no more said
            look(700000, -26.0, at(8)); look(700000, -26.0, at(9))       # a lot further: said again
            look(920000, -3.0, at(10)); look(920000, -3.0, at(11))       # eased
            look(1050000, 14.0, at(12)); look(1050000, 14.0, at(13))     # a rally (and past the line again)
            kinds = [k for k, _ in said]
            okay = kinds == ["crash", "crash", "rich", "rally"]
            print(f"  {'ok  ' if okay else 'FAIL'} a crash, a deeper one (the slump under the peak keeps quiet on a crash day), "
                  f"then a rally that takes it past the line: {kinds}")
            if not okay:
                FAILED.append("holdings crash")
                print("       " + "\n       ".join(t for _, t in said))
            words = h.words()
            okay = words.startswith("Your crypto holdings: worth $1,050,000") and "up 14.0% today" in words and "most it has ever been worth is $1,100,000" in words \
                and "past the $1,000,000 line" in words and "RALLY" in words
            print(f"  {'ok  ' if okay else 'FAIL'} the bot's line: {words[:120]}…")
            if not okay:
                FAILED.append("holdings words")
            st = h.status()
            if not (st["peak"]["total"] == 1100000 and st["over"] is True and st["move"]["kind"] == "rally" and len(st["history"]) >= 10):
                print(f"  FAIL the status: peak {st['peak']}, over {st['over']}, move {st['move']}, {len(st['history'])} readings")
                FAILED.append("holdings status")
            said.clear()
            for i in range(3):                                              # a quiet slide: under the line, and 23 % below the peak
                look(850000, -2.0, at(14 + i))                              # (once the rally has eased, two readings)
            kinds = [k for k, _ in said]
            okay = kinds == ["under", "slump"] and "23% below the most it has been worth ($1,100,000" in said[-1][1]
            print(f"  {'ok  ' if okay else 'FAIL'} a quiet day well below the peak: {kinds} — '{said[-1][1][:64] if said else ''}…'")
            if not okay:
                FAILED.append("holdings slump")
            h2 = _hold.Holdings()                                           # the file remembers
            if not (h2.peak and h2.peak[0] == 1100000 and h2.over is False and h2.slump and len(h2.samples) == len(h.samples)):
                print(f"  FAIL not remembered: {h2.peak} {h2.over} {h2.slump} {len(h2.samples)}")
                FAILED.append("holdings file")
            n = len(said)
            h.configure({"milestone": 500000, "move_pct": 10})              # the line moved under the total: not news
            if h.over is not True or len(said) != n:
                FAILED.append("holdings line moved")
        finally:
            _hold.PATH = real_path
        # the page the screen shows, the bot's context, the sidebar, Telegram
        from pie_ink.modes import crypto as _cm2
        if not any(i.get("holdings") for i in _cm2.CryptoMode._items()):
            FAILED.append("holdings in the e-ink rotation")
        probe = _cm2.CryptoMode(pieapp.service.config, pieapp.service.panel.info())
        try:
            img = probe._render_holdings(probe.frame(), _t.time(), False)
            okay = isinstance(img, _Image.Image)
        except Exception as e:
            print(f"  FAIL the holdings e-ink page: {e}")
            okay = False
        if okay:
            print("  ok   the holdings page draws on the e-ink, and is in the rotation")
        else:
            FAILED.append("holdings e-ink page")
        words = pieapp.mascot.context(pieapp.service.config)
        if not any(ln.startswith("Your crypto holdings: worth") for ln in words):
            print(f"  FAIL the bot should know the holdings: {[w for w in words if 'crypto' in w]}")
            FAILED.append("holdings in the bot's context")
        tg = pieapp._tg_command("/holdings")
        if "Your crypto:" not in tg or "BTC: 0.5" not in tg or "Line: $1,000,000" not in tg:
            print(f"  FAIL /holdings: {tg!r}")
            FAILED.append("telegram /holdings")
        else:
            print(f"  ok   /holdings: {tg.splitlines()[0]}")
        r = c.get("/api/now")
        check("GET /api/now (with holdings)", r, body_has=["holdings"])
        r = c.post("/api/holdings/try", json={"kind": "rich"})
        check("POST /api/holdings/try", r, body_has=["text", "model"])
        _t.sleep(1.0)
        if "You're rich" not in (pieapp.llm.latest().get("text") or "") or "You're rich" not in [m["text"] for m in pieapp.chat.history()[-3:] if m["role"] == "bot"][-1]:
            print(f"  FAIL the try-out should reach the bot's screen: {pieapp.llm.latest().get('text')!r}")
            FAILED.append("holdings try")
        else:
            print(f"  ok   'Past the line' on the page: the bot says '{(r.get_json() or {}).get('text', '')[:60]}…'")
        check("POST /api/holdings/try (nonsense)", c.post("/api/holdings/try", json={"kind": "lambo"}), want=(400,))
        check("PUT /api/watchlist/bitcoin (none held)", c.put("/api/watchlist/bitcoin", json={"amount": 0}))
        if (c.get("/api/holdings").get_json() or {}).get("any") or any(i.get("holdings") for i in _cm2.CryptoMode._items()):
            FAILED.append("holdings cleared")

        print("home assistant")
        fake = _FakeHome()
        fake.start()
        try:
            check("PUT /api/settings home", c.put("/api/settings", json={"home": {
                "enabled": True, "url": f"http://127.0.0.1:{fake.port}", "token": "test-token",
                "tv": "media_player.living_room_tv", "remote": "remote.living_room_tv", "preset": "samsung",
                "names": "living room tv, tv, television", "others": True, "say_back": True}}))
            r = c.post("/api/home/check", json={})
            check("POST /api/home/check", r, body_has=["ok", "players", "remotes"])
            d = r.get_json() or {}
            if not d.get("ok") or len(d.get("players", [])) != 1 or d.get("name") != "Test House":
                print(f"  FAIL the check did not see the fake house: {d}")
                FAILED.append("home check")
            check("GET /api/home", c.get("/api/home"), body_has=["configured", "players", "presets", "sources"])
            wanted = [
                ("turn on the living room tv", "TV on.", ("media_player", "turn_on", {"entity_id": "media_player.living_room_tv"})),
                ("go to channel five four five two", "Channel 5452.", ("media_player", "play_media",
                                                                        {"entity_id": "media_player.living_room_tv", "media_content_type": "channel", "media_content_id": "5452"})),
                ("volume up 2", "Volume up 2.", ("media_player", "volume_up", {"entity_id": "media_player.living_room_tv"})),
                ("mute", "Muted.", ("media_player", "volume_mute", {"entity_id": "media_player.living_room_tv", "is_volume_muted": True})),
                ("open netflix", "Netflix.", ("media_player", "select_source", {"entity_id": "media_player.living_room_tv", "source": "Netflix"})),
                ("channel up", "Channel up.", ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": "KEY_CHUP"})),
                ("press home", "Home.", ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": "KEY_HOME"})),
                ("turn off the lamp", "Lamp off.", ("homeassistant", "turn_off", {"entity_id": "light.lamp"})),
                ("turn on the kitchen lights", "Kitchen Light on.", ("homeassistant", "turn_on", {"entity_id": "light.kitchen_light"})),
                ("turn it off", "Kitchen Light off.", ("homeassistant", "turn_off", {"entity_id": "light.kitchen_light"})),
                ("movie night on", "Movie Night set.", ("scene", "turn_on", {"entity_id": "scene.movie_night"})),
                ("turn off the tv and the lamp", "TV off. Lamp off.", ("homeassistant", "turn_off", {"entity_id": "light.lamp"})),
            ]
            for said, want_reply, want_call in wanted:
                fake.calls.clear()
                r = c.post("/api/home/do", json={"text": said})
                reply = (r.get_json() or {}).get("reply")
                if r.status_code != 200 or reply != want_reply or want_call not in fake.calls:
                    print(f"  FAIL {said!r}: status {r.status_code}, reply {reply!r}, calls {fake.calls}")
                    FAILED.append(f"home {said}")
                else:
                    print(f"  ok   {said!r} → {reply}")
            fake.calls.clear()
            c.post("/api/home/do", json={"text": "volume up 2"})
            if fake.calls.count(("media_player", "volume_up", {"entity_id": "media_player.living_room_tv"})) != 2:
                print(f"  FAIL volume up 2 did not press twice: {fake.calls}")
                FAILED.append("home volume steps")
            check("POST /api/home/do (not a command)", c.post("/api/home/do", json={"text": "what is the weather"}), want=(400,))
            r = c.post("/api/home/do", json={"text": "is the tv on"})
            if (r.get_json() or {}).get("reply") != "The TV is on.":
                print(f"  FAIL asking about the tv: {r.get_json()}")
                FAILED.append("home query")
            r = c.post("/api/llm/chat", json={"message": "turn off the television"})
            d = r.get_json() or {}
            check("POST /api/llm/chat (a home command, no bot needed)", r, body_has=["reply", "home"])
            if d.get("reply") != "TV off.":
                print(f"  FAIL the chat bar did not do the tv: {d}")
                FAILED.append("home chat")
            if pieapp._tg_command("turn on the tv") != "TV on.":
                print("  FAIL telegram did not do the tv")
                FAILED.append("home telegram")
            else:
                print("  ok   telegram: 'turn on the tv' → TV on.")
            # the /tv remote in Telegram: a message with buttons, and the taps
            remote = pieapp._tg_command("/tv")
            labels = [b[0] for row in (remote.get("buttons") if isinstance(remote, dict) else []) for b in row]
            if not isinstance(remote, dict) or not {"▲", "▼", "◀", "▶", "OK", "On", "Off", "5"} <= set(labels):
                print(f"  FAIL /tv did not come back as a remote: {remote}")
                FAILED.append("telegram /tv")
            else:
                print(f"  ok   /tv → {len(labels)} buttons under '{remote['text'][:40]}…'")
            from pie_ink import telegram as _tg
            kb = _tg.keyboard(remote["buttons"]) if isinstance(remote, dict) else {}
            if not kb.get("inline_keyboard") or kb["inline_keyboard"][3][1] != {"text": "OK", "callback_data": "tv:ok"}:
                print(f"  FAIL the keyboard markup is off: {str(kb)[:200]}")
                FAILED.append("telegram keyboard")
            taps = [("tv:up", "Up.", "KEY_UP"), ("tv:ok", "OK.", "KEY_ENTER"), ("tv:5", "Pressed 5.", "KEY_5"),
                    ("tv:back", "Back.", "KEY_RETURN"), ("tv:mute", "Mute.", "KEY_MUTE")]
            for data, want_reply, want_key in taps:
                fake.calls.clear()
                got = pieapp._tg_tap(data)
                sent = ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": want_key})
                if got != want_reply or sent not in fake.calls:
                    print(f"  FAIL tap {data}: {got!r}, calls {fake.calls}")
                    FAILED.append(f"telegram tap {data}")
                else:
                    print(f"  ok   tap {data} → {got}")
            fake.calls.clear()
            if pieapp._tg_tap("tv:on") != "TV on." or ("media_player", "turn_on", {"entity_id": "media_player.living_room_tv"}) not in fake.calls:
                print("  FAIL tap tv:on")
                FAILED.append("telegram tap on")
            for said, want in (("/tv 5452", "Channel 5452."), ("/tv off", "TV off."), ("/tv volume up", "Volume up."),
                               ("/tv netflix", "Netflix."), ("/remote", None)):
                got = pieapp._tg_command(said)
                okay = (isinstance(got, dict) if want is None else got == want)
                print(f"  {'ok  ' if okay else 'FAIL'} {said} → {got if not isinstance(got, dict) else 'the remote'}")
                if not okay:
                    FAILED.append(f"telegram {said}")
            if not pieapp._tg_tap("tv:nothing").startswith("That button"):
                FAILED.append("telegram stale tap")
            fake.token = "other"                          # a refused token is a message, not a crash
            r = c.post("/api/home/do", json={"text": "turn on the tv"})
            if not ((r.get_json() or {}).get("reply") or "").startswith("Couldn't"):
                print(f"  FAIL a bad token should be reported: {r.get_json()}")
                FAILED.append("home bad token")
            else:
                print("  ok   a refused token is reported, not raised")
            check("PUT /api/settings home off", c.put("/api/settings", json={"home": {"enabled": False}}))
            check("POST /api/home/do (off)", c.post("/api/home/do", json={"text": "turn on the tv"}), want=(400,))
        finally:
            fake.stop()


        print("a camera that turns (a pretend OBSBOT)")
        from pie_ink import ptz as _ptz, sight as _sight, watcher as _watcher
        from PIL import Image as _Img
        cam_state = {_ptz.CID["pan"]: 0, _ptz.CID["tilt"]: 0, _ptz.CID["zoom"]: 0}
        limits = {_ptz.CID["pan"]: (-504000, 504000, 3600), _ptz.CID["tilt"]: (-324000, 324000, 3600), _ptz.CID["zoom"]: (0, 100, 1)}
        real = (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
                _ptz.xu_query, _ptz.vendor_of, _ptz.Ptz._awake, _ptz.Ptz._wake)

        def fake_query(fd, cid):
            if cid not in limits:
                return None
            lo, hi, step = limits[cid]
            return {"id": cid, "name": "x", "min": lo, "max": hi, "step": step, "default": 0}
        _ptz.queryctrl = fake_query
        _ptz.g_ctrl = lambda fd, cid: cam_state[cid]
        _ptz.s_ctrl = lambda fd, cid, v: cam_state.__setitem__(cid, v)
        _ptz.s_ext_ctrls = lambda fd, pairs: [cam_state.__setitem__(c, v) for c, v in pairs]
        _ptz.Ptz._candidates = lambda self: ["/dev/null"]
        view = {"n": 0}

        def fake_settle(self, timeout=3.5):
            view["n"] += 1
            return _Img.new("RGB", (480, 360), (40 * (view["n"] % 5), 90, 120))
        _ptz.Ptz.settle = fake_settle
        _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0
        saved_hold, saved_rise = _ptz.SWEEP_HOLD, _ptz.RISE
        _ptz.SWEEP_HOLD, _ptz.RISE = 0.0, 0.0                  # no waiting about in a test
        ollama2 = _FakeOllama()
        ollama2.start()
        try:
            check("PUT /api/settings ptz (turning by itself, fast for the test)",
                  c.put("/api/settings", json={"ptz": {"gentle": 1000}}))
            r = c.get("/api/ptz")
            check("GET /api/ptz", r, body_has=["available", "position"])
            if not (r.get_json() or {}).get("available"):
                print(f"  FAIL the pretend camera should be found: {r.get_json()}")
                FAILED.append("ptz found")
            # a glide: many small steps, easing in and out, and it ends where it was sent
            steps, real_ext = [], _ptz.s_ext_ctrls
            _ptz.s_ext_ctrls = lambda fd, pairs: (steps.append(dict(pairs)), real_ext(fd, pairs))
            _ptz.PTZ.set(pan=0, tilt=0)
            steps.clear()
            _ptz.PTZ.glide(pan=40, speed=80)
            _ptz.s_ext_ctrls = real_ext
            pans = [s[_ptz.CID["pan"]] / 3600 for s in steps if _ptz.CID["pan"] in s]
            if len(pans) < 4 or pans[-1] != 40 or pans[0] > 5 or any(b < a for a, b in zip(pans, pans[1:])):
                print(f"  FAIL a glide should turn in small steps and end where it was sent: {pans}")
                FAILED.append("ptz glide")
            else:
                print(f"  ok   a glide to 40° went in {len(pans)} small steps, first {pans[0]:.0f}°, and ended at 40°")
            _ptz.PTZ.set(pan=0, tilt=0)
            # OBSBOT's framed command, byte for byte as captured from its own app (wake and sleep)
            pad = lambda h: (h + "0" * 120)[:120]                  # noqa: E731
            if _ptz.v3_frame(0x0C, 0xA0C2, 2, b"\0\0\0\0").hex() != pad("aa250c000c0089420a02c2a00400be07") \
                    or _ptz.v3_frame(0x12, 0xA0C2, 2, b"\1\0\0\0").hex() != pad("aa2512000c00e9220a02c2a00400bffb01000000"):
                print("  FAIL the wake frame doesn't match the captured one")
                FAILED.append("ptz wake frame")
            else:
                print("  ok   the wake and sleep frames match OBSBOT's own, byte for byte (CRC-16/USB and all)")
            r = c.post("/api/ptz/set", json={"pan": 90, "tilt": 30, "zoom": 0.5})
            check("POST /api/ptz/set", r, body_has=["position"])
            if cam_state[_ptz.CID["pan"]] != 324000 or cam_state[_ptz.CID["tilt"]] != 108000 or cam_state[_ptz.CID["zoom"]] != 50:
                print(f"  FAIL 90° right, 30° up, zoom half should be 324000/108000/50: {cam_state}")
                FAILED.append("ptz set units")
            else:
                print("  ok   90° right, 30° up, zoom 50% → pan_absolute=324000 tilt_absolute=108000 zoom_absolute=50")
            check("POST /api/ptz/set center", c.post("/api/ptz/set", json={"center": True}))
            c.post("/api/ptz/set", json={"zoom": 0})                  # zoomed in, the stick is gentler on purpose
            # the stick over HTTP: held for a second, then a stop; a late message can't restart it
            for i in range(1, 7):
                c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "t", "seq": i, "speed": 60})
                _t.sleep(0.17)
            c.post("/api/ptz/stop", json={"sid": "t", "seq": 20})
            c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "t", "seq": 19})
            _t.sleep(0.5)
            pan = cam_state[_ptz.CID["pan"]] / 3600
            if not 30 <= pan <= 75 or _ptz.PTZ.driving():
                print(f"  FAIL a second of full stick should turn it ~60° and then stop: {pan}° driving={_ptz.PTZ.driving()}")
                FAILED.append("ptz stick")
            else:
                print(f"  ok   a second of full stick turned it {pan:.0f}°, and it stopped (a late message didn't restart it)")
            if not _ptz.PTZ.quiet():
                FAILED.append("ptz quiet after moving")
            check("POST /api/ptz/home (save)", c.post("/api/ptz/home", json={"save": True}), body_has=["home"])
            c.post("/api/ptz/set", json={"center": True})
            r = c.post("/api/ptz/home", json={})
            if abs(((r.get_json() or {}).get("position") or {}).get("pan", 0) - pan) > 1:
                print(f"  FAIL home should come back to {pan}: {r.get_json()}")
                FAILED.append("ptz home")
            c.post("/api/ptz/set", json={"center": True, "zoom": 0})
            # in words: no model yet, so counts from OpenCV
            for ask, want in {"look left": "Looking left", "zoom in": "Zoomed in", "zoom out a bit": "Zoomed out",
                              "look straight ahead": "straight ahead", "look around": "Left:"}.items():
                r = c.post("/api/llm/chat", json={"message": ask})
                reply = (r.get_json() or {}).get("reply") or (r.get_json() or {}).get("error") or ""
                okay = r.status_code == 200 and want.lower() in reply.lower()
                print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply[:80]!r}")
                if not okay:
                    FAILED.append(f"ptz: {ask}")
            if cam_state[_ptz.CID["pan"]] != 0:
                print(f"  FAIL looking around should turn back to where it was: {cam_state}")
                FAILED.append("ptz sweep back")
            for words in ("look up the weather in paris", "turn left at the lights", "go home", "what can you see"):
                if _ptz.parse(words) is not None or pieapp._camera_command(words, "typed") is not None:
                    print(f"  FAIL '{words}' isn't a camera command")
                    FAILED.append("ptz not greedy")
            tg = pieapp._tg_command("/look left")
            if not (isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff" and "left" in tg[1].lower()):
                print(f"  FAIL /look left should be a picture and a line: {str(tg)[:80]}")
                FAILED.append("telegram /look")
            else:
                print(f"  ok   /look left → a picture: {tg[1]!r}")
            # the page's button: in the background, the answer in the conversation
            check("POST /api/ptz/look", c.post("/api/ptz/look", json={"where": "around"}), body_has=["started"])
            for _ in range(50):
                if (_ptz.PTZ.last_look or {}).get("text"):
                    break
                _t.sleep(0.1)
            if not (_ptz.PTZ.last_look or {}).get("text", "").startswith("Left:"):
                print(f"  FAIL the look's answer: {_ptz.PTZ.last_look}")
                FAILED.append("ptz look button")
            # the watcher leaves the picture alone while the camera turns
            _ptz.PTZ.set(pan=10)
            w = _watcher.Watcher()
            w._previous = "something"
            w.conf = {"enabled": True, "interval": 0.5}
            w.stop_flag.clear()
            th = _th.Thread(target=w._run, daemon=True)
            th.start()
            _t.sleep(1.2)
            w.stop()
            if w._previous is not None and w.level:
                print("  FAIL the watcher should start afresh while the camera turns")
                FAILED.append("watcher ptz")
            else:
                print("  ok   the watcher ignores the picture while the camera turns")
            # the Seen list starts a new baseline, quietly, when the view changed
            _sight.clear()
            check("PUT /api/settings llm (for the looks)", c.put("/api/settings", json={
                "llm": {"enabled": True, "host": f"http://127.0.0.1:{ollama2.port}", "model": "fake:latest", "see": True},
                "camera_watch": {"events": True, "detector": "model"}}))
            frame = _Img.new("RGB", (320, 240), (90, 120, 150))
            with _sight._lock:
                _sight._state["last"], _sight._state["view"] = None, None
            _, first = _sight.look(frame, pieapp.service.config, "T", view=(0, 0, 0))
            ollama2.scene = dict(ollama2.scene, people=0, animals=[])
            _, moved = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            _, same = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            ollama2.scene = dict(ollama2.scene, people=1)
            _, arrived = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            if not first or moved or same or not arrived:
                print(f"  FAIL views: first {[e['text'] for e in first]} moved {moved} same {same} arrived {arrived}")
                FAILED.append("sight view baseline")
            else:
                print(f"  ok   a new view is a quiet baseline (no 'Nobody detected' just because the camera moved); "
                      f"from the same view changes still count: {arrived[0]['text']!r}")
            # with a model: the look is described, and the agent turns to look before deciding
            r = c.post("/api/llm/chat", json={"message": "look right and tell me if the window is open"})
            if "window" not in ((r.get_json() or {}).get("reply") or ""):
                print(f"  FAIL the look should be described by the model: {r.get_json()}")
                FAILED.append("ptz describe")
            else:
                print(f"  ok   'look right and tell me if the window is open' → {r.get_json()['reply']!r}")
            ollama2.decision = {"thought": "Something moved at the right edge.", "level": "note",
                                "actions": [{"do": "look", "text": "right"}], "remember": ""}
            check("PUT /api/settings agent on (may look)", c.put("/api/settings", json={
                "agent": {"enabled": True, "every_minutes": 30, "look": False, "can_look": True, "can_show": True}}))
            _t.sleep(0.3)
            before_pan = cam_state[_ptz.CID["pan"]]
            entry = pieapp.agent.AGENT.tick("test")
            if not entry or "looked" not in entry or "cat" not in entry.get("thought", "") or cam_state[_ptz.CID["pan"]] != before_pan:
                print(f"  FAIL the agent should look right, decide again, and turn back: {entry} pan {cam_state[_ptz.CID['pan']]} vs {before_pan}")
                FAILED.append("agent look")
            else:
                print(f"  ok   the agent looked {entry['looked'][:60]!r}… then thought {entry['thought']!r}, and turned back")
            check("PUT /api/settings agent off", c.put("/api/settings", json={"agent": {"enabled": False}, "llm": {"enabled": False}}))

            # Follow me: an OBSBOT's own tracking, through its vendor unit, as a switch that stays put
            import errno as _errno
            xu = {"mode": 2, "sets": [], "asleep": False, "frames": []}   # it starts up tracking, as a Tiny 2 can

            def fake_xu(fd, unit, selector, query, data=b""):
                if unit != 2:
                    raise OSError(_errno.ENOENT, "no such unit")
                if query == _ptz.UVC_SET_CUR and selector == _ptz.XU_COMMAND:
                    xu["frames"].append(bytes(data))      # a framed command: "run" wakes it
                    if data[10:12] == b"\xc2\xa0" and data[16:20] == b"\0\0\0\0":
                        xu["asleep"] = False
                    return bytes(60)
                if query == _ptz.UVC_SET_CUR:
                    if xu["asleep"]:
                        return bytes(60)                  # asleep, it takes nothing
                    xu["sets"].append(bytes(data[:4]).hex())
                    if data[:1] == b"\x16":
                        xu["mode"] = data[2]
                    return bytes(60)
                block = bytearray(60)
                block[24] = xu["mode"]
                block[2] = 1 if xu["asleep"] else 0
                block[5] = 1                                  # the rest of a real block isn't all zeros either
                return bytes(block)
            _ptz.xu_query, _ptz.vendor_of = fake_xu, (lambda path: "3564")
            _ptz.Ptz._awake = staticmethod(lambda: True)
            _ptz.Ptz._wake = lambda self, wait=4.0: None
            _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0       # found again, as an OBSBOT this time
            f = (c.get("/api/ptz").get_json() or {}).get("follow") or {}
            if not (f.get("can") and f.get("on") is True and f.get("want") is False):
                print(f"  FAIL an OBSBOT that is tracking should say so: {f}")
                FAILED.append("follow found")
            cam_state[_ptz.CID["pan"]] = 0
            _ptz.PTZ.keep_once()
            if xu["mode"] != 0 or xu["sets"][-1:] != ["16020000"]:
                print(f"  FAIL the switch says off, so the camera's own tracking should be switched off: {xu}")
                FAILED.append("follow kept off")
            else:
                print("  ok   the camera started up following; the switch says off, so it was switched off (16 02 00 00)")
            r = c.post("/api/ptz/follow", json={"on": True})
            if not ((r.get_json() or {}).get("follow") or {}).get("on") or xu["mode"] != 2 \
                    or pieapp.service.config["ptz"].get("follow") is not True:
                print(f"  FAIL Follow me on: {r.get_json()} {xu} {pieapp.service.config['ptz']}")
                FAILED.append("follow on")
            else:
                print("  ok   Follow me on → 16 02 02 00, and it's remembered")
            pan0 = cam_state[_ptz.CID["pan"]]
            r = c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "f", "seq": 1})
            _t.sleep(0.35)
            if not (r.get_json() or {}).get("following") or cam_state[_ptz.CID["pan"]] != pan0 or not _ptz.PTZ.quiet():
                print(f"  FAIL while following, the stick should rest (and say why): {r.get_json()}")
                FAILED.append("follow stick")
            else:
                print("  ok   while it follows you, the stick rests and the page is told why; the watcher waits")
            r = c.post("/api/llm/chat", json={"message": "look around"})
            reply = (r.get_json() or {}).get("reply") or ""
            if not reply.startswith("Left:") or xu["mode"] != 2 or xu["sets"][-2:] != ["16020000", "16020200"]:
                print(f"  FAIL a look around should stop following for the look and follow again after: {reply!r} {xu['sets'][-3:]}")
                FAILED.append("follow look around")
            else:
                print("  ok   'look around' while following: it stops, looks, and follows again")
            xu["mode"] = 0                                     # a raised palm switched it off on the camera
            _ptz.PTZ.keep_once()
            if xu["mode"] != 2:
                FAILED.append("follow gesture")
                print("  FAIL the switch says on: the camera's gesture should be undone")
            else:
                print("  ok   the camera's own gesture is undone: the switch here decides")
            for ask, want, mode in (("stop following me", "Stopped following you", 0), ("follow me", "Following you", 2)):
                reply = (c.post("/api/llm/chat", json={"message": ask}).get_json() or {}).get("reply") or ""
                okay = reply.startswith(want) and xu["mode"] == mode
                print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply!r}")
                if not okay:
                    FAILED.append(f"follow: {ask}")
            r = c.post("/api/ptz/set", json={"center": True})
            if not (r.get_json() or {}).get("stopped_following") or xu["mode"] != 0 \
                    or pieapp.service.config["ptz"].get("follow") is not False:
                print(f"  FAIL Center while following should stop the following (and keep it off): {r.get_json()}")
                FAILED.append("follow center")
            else:
                print("  ok   Center while following stops it — you pointed it somewhere — and it stays off")
            tg_on, tg_off = pieapp._tg_command("/follow"), pieapp._tg_command("/follow off")
            if tg_on != "Following you." or tg_off != "Stopped following you." or xu["mode"] != 0:
                print(f"  FAIL /follow and /follow off: {tg_on!r} {tg_off!r}")
                FAILED.append("telegram /follow")
            else:
                print("  ok   /follow and /follow off from Telegram")
            # asleep, lens down: a move wakes it (OBSBOT's own "run") and then goes where it was sent
            xu["asleep"], xu["frames"] = True, []
            _ptz.PTZ._asked_at = 0.0
            c.post("/api/ptz/set", json={"pan": 20, "tilt": 5})
            _ptz.PTZ.wait_awake(10)
            woke = xu["frames"][0] if xu["frames"] else b""
            s = (c.get("/api/ptz").get_json() or {}).get("sleep") or {}
            if xu["asleep"] or woke[:2] != b"\xaa\x25" or woke[10:12] != b"\xc2\xa0" or woke[16:20] != b"\0\0\0\0" \
                    or cam_state[_ptz.CID["pan"]] != 20 * 3600 or not s.get("can") or s.get("asleep") or s.get("wakes", 0) < 1:
                print(f"  FAIL a move should wake a sleeping OBSBOT and then land: {xu['asleep']} {woke[:20].hex()} "
                      f"{cam_state} {s}")
                FAILED.append("ptz wake")
            else:
                print("  ok   asleep with its lens down: the move woke it (AA 25 … C2 A0 · 00 00 00 00) and it turned as asked")
            xu["asleep"], xu["frames"] = True, []
            check("PUT /api/settings ptz wake off", c.put("/api/settings", json={"ptz": {"wake": False}}))
            _ptz.PTZ._asked_at = 0.0
            c.post("/api/ptz/set", json={"pan": 10})
            _t.sleep(0.3)
            if xu["frames"]:
                print("  FAIL with waking off it should be left asleep")
                FAILED.append("ptz wake off")
            else:
                print("  ok   with 'Wake it when it's used' off, it's left asleep")
            c.put("/api/settings", json={"ptz": {"wake": True}})
            xu["asleep"] = False

            # turning by itself, an OBSBOT turns at a speed (its own command): one smooth turn, not steps
            import struct as _struct
            from pie_ink import camera as _cam2
            f = _ptz.v3_frame(1, _ptz.CMD_GIM_SPEED, _ptz.TO_GIMBAL, _struct.pack("<fff", 10, 20, 30))
            hdr, seg = bytearray(f[:12]), bytearray(f[12:28])
            hdr[6:8], seg[2:4] = b"\0\0", b"\0\0"
            if (_ptz.crc16_usb(b"123456789") != 0xB4C8 or f[:2] != b"\xaa\x25" or f[8:14] != b"\x0a\x04\x84\x64\x0c\x00"
                    or _struct.unpack_from("<fff", f, 16) != (10.0, 20.0, 30.0)
                    or int.from_bytes(f[6:8], "little") != _ptz.crc16_usb(bytes(hdr))
                    or int.from_bytes(f[14:16], "little") != _ptz.crc16_usb(bytes(seg))):
                print(f"  FAIL the speed frame: {f[:28].hex()}")
                FAILED.append("ptz speed frame")
            else:
                print("  ok   the speed frame: AA 25 … 0A 04 84 64, roll/pitch/yaw as float32, CRC-16/USB (B4C8 check) right")
            timed, touch0, awake0, wait0 = [], _cam2.CAMERA.touch, _ptz.Ptz.__dict__["_awake"], _ptz.STREAM_WAIT
            _ptz.xu_query = lambda fd, unit, sel, q, data=b"": (
                timed.append((_t.time(), bytes(data))) if (sel == _ptz.XU_COMMAND and q == _ptz.UVC_SET_CUR) else None,
                fake_xu(fd, unit, sel, q, data))[1]
            writes, real_ext3 = [], _ptz.s_ext_ctrls
            _ptz.s_ext_ctrls = lambda fd, pairs: (writes.append((_t.time(), dict(pairs))), real_ext3(fd, pairs))
            _cam2.CAMERA.touch = lambda seconds=20: None
            speeds = lambda: [(t, _struct.unpack_from("<fff", d, 16)) for t, d in timed if d[10:12] == b"\x84\x64"]  # noqa: E731
            try:
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                writes.clear()
                _ptz.PTZ.glide(pan=30, tilt=10, speed=40)
                spd = speeds()
                yaw = sum(-a[1][2] * (b[0] - a[0]) for a, b in zip(spd, spd[1:]))       # the speeds added up
                pitch = sum(-a[1][1] * (b[0] - a[0]) for a, b in zip(spd, spd[1:]))
                peak = max([-s[2] for _, s in spd] or [0])
                ends = [(w[pan_id] / 3600, w[tilt_id] / 3600) for _, w in writes
                        for pan_id, tilt_id in [(_ptz.CID["pan"], _ptz.CID["tilt"])] if pan_id in w]
                okay = (len(spd) >= 10 and all(s[2] <= 0 and s[1] <= 0 for _, s in spd)
                        and abs(yaw - 30) < 2.5 and abs(pitch - 10) < 1.5 and 40 < peak < 80 and -spd[0][1][2] < peak / 2
                        and [s for _, s in spd[-2:]] == [(0.0, 0.0, 0.0)] * 2
                        and ends == [(30.0, 10.0)] * 2 and writes[0][0] >= spd[-1][0])
                print(f"  {'ok  ' if okay else 'FAIL'} a glide on an OBSBOT is one turn at a speed: {len(spd)} speeds rising to "
                      f"{peak:.0f}°/s and falling, adding up to {yaw:.1f}° across and {pitch:.1f}° up, then a stop (twice), "
                      f"then the exact place (twice): {ends}")
                if not okay:
                    FAILED.append("ptz smooth glide")
                # another move in the middle stops it where it's got to: the stop first, then that move
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                writes.clear()
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": -40, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.5)
                got = _ptz.PTZ.set(pan=15)
                th.join(5)
                spd = speeds()
                first_stop = next((t for t, s in spd if s == (0.0, 0.0, 0.0)), None)
                later = [s for t, s in spd if writes and t > writes[0][0] and s != (0.0, 0.0, 0.0)]
                okay = (first_stop is not None and len(writes) == 1 and writes[0][0] >= first_stop and not later
                        and cam_state[_ptz.CID["pan"]] == 15 * 3600 and cam_state[_ptz.CID["tilt"]] == 0
                        and got["pan"] == 15 and not th.is_alive())
                print(f"  {'ok  ' if okay else 'FAIL'} Center (or any move) mid-turn: a stop goes first, then the move; "
                      f"the turn doesn't carry on after it")
                if not okay:
                    FAILED.append("ptz smooth interrupted")
                # not streaming, it wouldn't take speeds: one move instead
                _ptz.Ptz._awake, _ptz.STREAM_WAIT = staticmethod(lambda: False), 0.2
                timed.clear()
                writes.clear()
                _ptz.PTZ.glide(pan=40, speed=40)
                okay = not speeds() and len(writes) == 1 and cam_state[_ptz.CID["pan"]] == 40 * 3600
                _ptz.Ptz._awake = awake0
                # "smooth: false" puts the small steps back
                _ptz.PTZ.conf["smooth"] = False
                writes.clear()
                _ptz.PTZ.glide(pan=0, speed=80)
                _ptz.PTZ.conf.pop("smooth", None)
                okay = okay and not speeds() and len(writes) >= 4 and cam_state[_ptz.CID["pan"]] == 0
                print(f"  {'ok  ' if okay else 'FAIL'} not streaming, it goes in one move; with smooth: false, in steps as before")
                if not okay:
                    FAILED.append("ptz smooth fallback")
                # the gimbal must always get its stop: found afresh (a restart mid-turn), and after a turn whose
                # stop the link wouldn't take — owed, and sent by the keeper the moment the link is back
                stop_frames = lambda: [d for _, d in timed if d[10:12] == b"\x84\x64" and d[16:28] == bytes(12)]   # noqa: E731
                timed.clear()
                _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0
                _ptz.PTZ.available()
                found_stop = len(stop_frames())
                broken = {"on": False}
                inner = _ptz.xu_query

                def flaky_xu(fd, unit, sel, q, data=b""):
                    if broken["on"] and sel == _ptz.XU_COMMAND and q == _ptz.UVC_SET_CUR:
                        raise OSError(_errno.EPIPE, "Broken pipe")
                    return inner(fd, unit, sel, q, data)
                _ptz.xu_query = flaky_xu
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": 60, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.4)
                broken["on"] = True                              # the link goes bad mid-turn: no speed, no stop gets through
                th.join(6)
                owed_after = _ptz.PTZ.stop_owed
                n_before = len(stop_frames())
                broken["on"] = False
                _ptz.PTZ.keep_once()                             # the keeper's next round: the stop is sent
                okay = (found_stop >= 1 and not th.is_alive() and owed_after and not _ptz.PTZ.stop_owed
                        and len(stop_frames()) > n_before and _ptz.PTZ.status()["stop_owed"] is False)
                print(f"  {'ok  ' if okay else 'FAIL'} the gimbal always gets its stop: one when it's found ({found_stop}), and one owed "
                      f"from a turn the link broke during, sent by the keeper once the link is back")
                if not okay:
                    print(f"       found_stop {found_stop} alive {th.is_alive()} owed {owed_after} now {_ptz.PTZ.stop_owed} frames {n_before}→{len(stop_frames())}")
                    FAILED.append("ptz stop owed")
                _ptz.xu_query = inner
                # the service stopping mid-turn: the turn is stopped first
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": -60, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.4)
                timed.clear()
                _ptz.PTZ.shutdown()
                th.join(5)
                okay = bool(stop_frames()) and not th.is_alive() and not _ptz.PTZ._speeding
                print(f"  {'ok  ' if okay else 'FAIL'} the service stopping mid-turn stops the turn first")
                if not okay:
                    FAILED.append("ptz shutdown stop")
                _ptz.PTZ._stop.clear()
                _ptz.PTZ.start()
            finally:
                _ptz.Ptz._awake, _ptz.STREAM_WAIT = awake0, wait0
                _ptz.s_ext_ctrls, _cam2.CAMERA.touch = real_ext3, touch0
                _ptz.xu_query = fake_xu
        finally:
            (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
             _ptz.xu_query, _ptz.vendor_of, _ptz.Ptz._awake, _ptz.Ptz._wake) = real
            _ptz.PTZ.fd, _ptz.PTZ.ctrls, _ptz.PTZ._looked_for, _ptz.PTZ.device = None, {}, _t.time(), None
            _ptz.PTZ.xu_unit, _ptz.PTZ.follow_on, _ptz.PTZ.asleep = None, None, None
            _ptz.SWEEP_HOLD, _ptz.RISE = saved_hold, saved_rise
            ollama2.stop()
        r = c.post("/api/llm/chat", json={"message": "look left"})
        if "no camera here that turns" not in ((r.get_json() or {}).get("reply") or ""):
            print(f"  FAIL with no camera that turns, 'look left' should say so: {r.get_json()}")
            FAILED.append("ptz none")
        else:
            print("  ok   with no camera that turns, 'look left' says so instead of pretending")
        check("GET /api/ptz (none)", c.get("/api/ptz"), body_has=["available"])

        print("the little friend (a pretend room, a pretend OBSBOT)")
        import random as _rnd
        from PIL import ImageDraw as _Draw
        from pie_ink import buddy as _buddy, camera as _cam
        room = _Img.new("L", (3600, 1200), 128)
        rd, rr = _Draw.Draw(room), _rnd.Random(7)
        for _ in range(700):
            x, y = rr.randrange(0, 3600), rr.randrange(0, 1200)
            rd.rectangle([x, y, x + rr.randrange(20, 150), y + rr.randrange(20, 150)], fill=rr.randrange(20, 235))
        where = {"person": None}
        pos = {_ptz.CID["pan"]: 0, _ptz.CID["tilt"]: 0, _ptz.CID["zoom"]: 0}

        def room_view(pan, tilt):
            cx, cy = (pan + 180) * 10, (60 - tilt) * 10
            img = room.crop((cx - 350, cy - 210, cx + 350, cy + 210)).convert("RGB")
            p = where["person"]
            if p and abs(p[0] - pan) < 30 and abs(p[1] - tilt) < 18:
                x, y = (p[0] - pan + 35) * 10, (tilt - p[1] + 21) * 10
                _Draw.Draw(img).ellipse([x - 45, y - 45, x + 45, y + 45], fill=(10, 10, 10))
                img.info["person"] = (x / 700, y / 420)
            return img

        def here():
            return pos[_ptz.CID["pan"]] / 3600, pos[_ptz.CID["tilt"]] / 3600

        def fake_detect(self, frame, cats=True):
            self.took = 0.01
            p = frame.info.get("person")
            return ([(p[0] - 0.07, p[1] - 0.1, 0.14, 0.2)] if p else []), False
        saved_fns = (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
                     _cam.CAMERA.status, _cam.CAMERA.frame, _cam.CAMERA.touch, _buddy.Buddy._detect)
        _ptz.queryctrl = fake_query
        _ptz.g_ctrl = lambda fd, cid: pos[cid]
        _ptz.s_ext_ctrls = lambda fd, pairs: [pos.__setitem__(k, v) for k, v in pairs]
        _ptz.Ptz._candidates = lambda self: ["/dev/null"]
        _ptz.Ptz.settle = lambda self, timeout=3.5: room_view(*here())
        _cam.CAMERA.status = lambda: {"running": True, "age": 0.1, "id": "usb:/dev/video0"}
        _cam.CAMERA.frame = lambda: room_view(*here())
        _cam.CAMERA.touch = lambda seconds=20: None
        _buddy.Buddy._detect = fake_detect
        _ptz.PTZ.fd, _ptz.PTZ._looked_for, _ptz.PTZ.touched_at, _ptz.PTZ.follow_on = None, 0.0, 0.0, None
        B = _buddy.BUDDY
        try:
            r = c.post("/api/buddy", json={"on": True})
            if not (r.get_json() or {}).get("enabled") or not pieapp.service.config["buddy"]["enabled"] or not B.running():
                print(f"  FAIL the Camera tab's switch should turn it on, and remember: {r.get_json()}")
                FAILED.append("buddy on")
            else:
                print("  ok   switched on from the Camera tab: it starts, and the setting is kept")
            B.stop()                                   # from here it's driven by hand, a glance at a time
            for _ in range(50):
                if not B.running():
                    break
                _t.sleep(0.1)
            B.conf = dict(pieapp.service.config["buddy"], night=False, every_seconds=20, rest=600)
            B.state, B.why = "looking", ""

            def glance(n=1, move=False):
                for _ in range(n):
                    if move:
                        B.next_move = 0
                    B.tick()
            glance(24, move=True)
            seen_spots = {tuple(k) for k in B.spots}
            low = min(t for _, t in seen_spots)
            home_pan = float((pieapp.service.config["ptz"].get("home") or {}).get("pan", 0) or 0)
            if len(seen_spots) < 6 or low < -12 or max(abs(p - home_pan) for p, _ in seen_spots) > 70.5:
                print(f"  FAIL it should look around home, 70° each way, no lower than -12°: {sorted(seen_spots)}")
                FAILED.append("buddy wander")
            else:
                print(f"  ok   it looked around by itself: {len(seen_spots)} spots around home ({home_pan:.0f}°), "
                      f"lowest {low}°, nowhere near the floor")
            B.next_move, t_before, p_before = 0, _t.time(), here()
            B.tick()
            if here() == p_before or B.next_move - t_before < _buddy.HOLD:
                print(f"  FAIL at a new spot it should stay a while: moved {here() != p_before}, "
                      f"next move in {B.next_move - t_before:.1f}s")
                FAILED.append("buddy stays")
            else:
                print(f"  ok   at each spot it stays a while before the next ({B.next_move - t_before:.0f}s this time)")
            # its motors get a rest: past the turning allowed in ten minutes, it holds still and says so
            B.conf["rest"] = 30
            _ptz.PTZ.turns.append((_t.time() - 40, _t.time() - 5))          # 35 s of turning just now
            B.next_move, p_before = 0, here()
            B.tick()
            resting = here() == p_before and B.why.startswith("resting its motors")
            st = (c.get("/api/buddy").get_json() or {})
            print(f"  {'ok  ' if resting else 'FAIL'} past its turning allowance it holds still: {B.why!r}")
            if not resting or not (st.get("why") or "").startswith("resting"):
                FAILED.append("buddy rest")
            _ptz.PTZ.turns.clear()
            B.conf["rest"] = 600
            r = c.post("/api/ptz/set", json={"pan": 0, "tilt": -80})
            got = ((r.get_json() or {}).get("position") or {}).get("tilt")
            if got != -30:
                print(f"  FAIL nothing should take it lower than 'Lowest it looks' (-30°): {got}")
                FAILED.append("ptz floor")
            else:
                print("  ok   asked for 80° down, it stops at -30° (an OBSBOT pointed down goes to sleep)")
            _ptz.PTZ.touched_at = 0
            _ptz.PTZ.set(pan=0, tilt=0)
            _ptz.PTZ.move_until = 0                    # it's arrived (a real one takes a second)
            B.next_move, B.prev = _t.time() + 999, None
            pieapp.service.set_mode("clock")
            for _ in range(30):
                if pieapp.service.mode_name == "clock":
                    break
                _t.sleep(0.1)
            for i in range(6):
                where["person"] = (-20 + (i % 2) * 0.6, 3)          # people fidget; posters don't
                glance()
            last = (pieapp.chat.history(1) or [{}])[-1]
            said = [last.get("text")] if last.get("text") == B.line and B.line else []
            st = (c.get("/api/buddy").get_json() or {})
            ev = pieapp.sight.events(limit=3)
            for _ in range(30):
                if pieapp.service.mode_name == "buddy":
                    break
                _t.sleep(0.1)
            okay = (len(said) == 1 and st.get("mood") == "happy" and ev and ev[0]["text"] == "Person detected"
                    and ev[0].get("source") == "friend" and pieapp.service.mode_name == "buddy" and here()[0] < 0)
            print(f"  {'ok  ' if okay else 'FAIL'} someone walked in: it said {said!r}, face {st.get('face')} ({st.get('mood')}), "
                  f"'{ev[0]['text'] if ev else '-'}' in the Seen list, its face on the screen, and it turned to them")
            if not okay:
                FAILED.append("buddy hello")
            pieapp._buddy_face["until"] = _t.time() - 1
            pieapp._buddy_popdown()
            for _ in range(30):
                if pieapp.service.mode_name != "buddy":
                    break
                _t.sleep(0.1)
            if pieapp.service.mode_name == "buddy":
                print("  FAIL after a minute the screen should go back to what it showed")
                FAILED.append("buddy popdown")
            else:
                print(f"  ok   a minute later the screen goes back ({pieapp.service.mode_name})")
            tg = pieapp._tg_command("/friend")
            if st.get("face", "?") not in tg:
                print(f"  FAIL /friend should say how it is: {tg!r}")
                FAILED.append("telegram /friend")
            else:
                print(f"  ok   /friend → {tg!r}")
            where["person"] = None
            _ptz.PTZ.user_moved()
            p0 = here()
            glance(3, move=True)
            if here() != p0 or B.why != "you're steering":
                print(f"  FAIL you steered: it should leave the camera be: {here()} vs {p0}, {B.why!r}")
                FAILED.append("buddy steering")
            else:
                print("  ok   you pointed the camera: it leaves it be for a while")
            r = c.post("/api/buddy", json={"on": False})
            if (r.get_json() or {}).get("enabled") or pieapp.service.config["buddy"]["enabled"]:
                FAILED.append("buddy off")
        finally:
            B.stop()
            (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
             _cam.CAMERA.status, _cam.CAMERA.frame, _cam.CAMERA.touch, _buddy.Buddy._detect) = saved_fns
            _ptz.PTZ.fd, _ptz.PTZ.ctrls, _ptz.PTZ._looked_for, _ptz.PTZ.device = None, {}, _t.time(), None
            pieapp.service.set_mode("clock")

        print("photos (from the test pattern)")
        from pie_ink import photos as _photos
        had = set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()
        try:
            # the colour filters are CSS's own matrices: sepia(1) turns white to rgb(255, 255, 239), as a browser does
            white, red = _Img.new("RGB", (4, 4), (255, 255, 255)), _Img.new("RGB", (4, 4), (255, 0, 0))
            got = (_photos.apply(white, "sepia").getpixel((0, 0)), _photos._colour(white, [["sepia", 1]]).getpixel((0, 0)),
                   _photos._colour(red, [["grayscale", 1]]).getpixel((0, 0)))
            if got[1:] != ((255, 255, 239), (54, 54, 54)):
                print(f"  FAIL the colour filters should match CSS's: {got}")
                FAILED.append("photo css maths")
            else:
                print("  ok   the colour filters are CSS's own: the live view shows them as the photo comes out")
            scene = _Img.new("RGB", (640, 360), (90, 120, 150))
            _Draw.Draw(scene).ellipse([200, 80, 420, 300], fill=(230, 200, 60))
            odd = [f["id"] for f in _photos.FILTERS if abs(_photos.apply(scene, f["id"]).width - 640) > 12]
            if odd:
                FAILED.append("photo filters")
                print(f"  FAIL every filter should keep the picture's size: {odd}")
            r = c.get("/api/photos")
            check("GET /api/photos", r, body_has=["photos", "filters", "count"])
            ids = [f["id"] for f in (r.get_json() or {}).get("filters", [])]
            if ids[:2] != ["none", "bw"] or "trace" not in ids or "faded" in ids:
                print(f"  FAIL the filters: {ids}")
                FAILED.append("photo filter list")
            traced = _photos.apply(scene, "trace")
            if traced.getpixel((20, 20))[0] > 30 or max(traced.convert("L").getdata()) < 200:
                print("  FAIL Edge trace should be bright outlines on black")
                FAILED.append("photo trace")
            else:
                print("  ok   Edge trace: the outlines, bright on black")
            r = c.post("/api/photos", json={"filter": "sepia", "rot": 90})
            check("POST /api/photos (sepia, the view turned)", r, body_has=["photo"])
            p = (r.get_json() or {}).get("photo") or {}
            if not (p.get("name", "").endswith("-sepia.jpg") and p.get("h", 0) > p.get("w", 0) > 0):
                print(f"  FAIL a sepia photo, turned like the view (taller than wide): {p}")
                FAILED.append("photo take")
            else:
                print(f"  ok   took {p['name']} ({p['w']}x{p['h']}, turned like the view)")
            r = c.get(p.get("url", "/api/photos/x.jpg"))
            if r.status_code != 200 or r.data[:3] != b"\xff\xd8\xff":
                FAILED.append("photo get")
            r = c.get(p.get("url", "/api/photos/x.jpg") + "?download=1")
            if "attachment" not in r.headers.get("Content-Disposition", ""):
                print(f"  FAIL download should come as a file: {r.headers.get('Content-Disposition')}")
                FAILED.append("photo download")
            r = c.get(p.get("thumb", "/api/photos/x.jpg/thumb"))
            if r.status_code != 200 or max(_Img.open(__import__("io").BytesIO(r.data)).size) > _photos.THUMB:
                FAILED.append("photo thumb")
            check("GET /api/photos/<none> (404)", c.get("/api/photos/..%2Fconfig.yaml"), want=(404,))
            # a drawn filter comes drawn from the Pi, in the live view's stream
            r = c.get("/api/camera/stream?filter=comic", buffered=False)
            first = next(iter(r.response), b"")
            r.close()
            if b"\xff\xd8" not in first:
                print(f"  FAIL the comic view should stream JPEGs: {first[:60]!r}")
                FAILED.append("photo stream")
            else:
                print("  ok   a drawn filter (comic) streams from the Pi for the live view")
            # in words, and from Telegram (a pretend camera, so it isn't only the test pattern)
            real_available = pieapp.camera.available
            pieapp.camera.available = lambda refresh=False: [{"id": "usb:/dev/video9", "label": "Pretend"},
                                                             {"id": "mock", "label": "Test pattern"}]
            try:
                reply = (c.post("/api/llm/chat", json={"message": "take a black and white photo"}).get_json() or {}).get("reply") or ""
                tg = pieapp._tg_command("/photo pixel")
                last = pieapp._tg_command("/photos")
                huh = pieapp._tg_command("/photo nonsense")
            finally:
                pieapp.camera.available = real_available
            okay = (reply.startswith("Took a black-and-white picture") and isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff"
                    and "pixel-art" in tg[1] and isinstance(last, tuple) and str(huh).startswith("Which filter?"))
            print(f"  {'ok  ' if okay else 'FAIL'} 'take a black and white photo' → {reply!r}; /photo pixel → a picture; "
                  f"/photos → the latest")
            if not okay:
                print(f"       {str(tg)[:80]} {str(last)[:60]} {huh!r}")
                FAILED.append("photo words")
            for words in ("take a shot", "take a picture of the weather", "picture this", "take me home"):
                if _photos.parse(words) is not None:
                    FAILED.append("photo not greedy")
                    print(f"  FAIL '{words}' isn't a photo")
            r = c.post(f"/api/photos/{p.get('name', 'x.jpg')}/show")
            for _ in range(30):
                if pieapp.service.mode_name == "postcard":
                    break
                _t.sleep(0.1)
            if r.status_code != 200 or pieapp.service.mode_name != "postcard":
                FAILED.append("photo show")
                print(f"  FAIL a photo on the screen: {r.get_json()} {pieapp.service.mode_name}")
            else:
                print("  ok   a photo on the screen (as a postcard)")
            n = (c.get("/api/photos").get_json() or {}).get("count", 0)
            r = c.delete(p.get("url", "/api/photos/x.jpg"))
            if r.status_code != 200 or (r.get_json() or {}).get("count") != n - 1 or _photos.path_of(p.get("name")):
                FAILED.append("photo delete")
            else:
                print("  ok   deleted")
            # the camera paused (Settings → Sound): nothing starts it, a photo says why, the friend waits, and it comes back
            from pie_ink import buddy as _buddy2
            paused = (c.post("/api/camera/pause", json={"seconds": 60}).get_json() or {}).get("paused_for", 0)
            pieapp.CAMERA.touch(20)
            st = c.get("/api/camera/status").get_json() or {}
            took = c.post("/api/photos", json={"filter": "none"})
            friend = _buddy2.Buddy()
            friend.conf = {"enabled": True, "night": False}
            friend.tick()
            back = (c.post("/api/camera/pause", json={"seconds": 0}).get_json() or {}).get("paused_for")
            pieapp.CAMERA.touch(20)
            for _ in range(50):
                if pieapp.CAMERA.status()["running"]:
                    break
                _t.sleep(0.1)
            okay = (paused >= 59 and not st.get("running") and st.get("paused_for", 0) > 0 and took.status_code == 400
                    and "paused" in ((took.get_json() or {}).get("error") or "") and friend.why == "the camera's paused"
                    and back == 0 and pieapp.CAMERA.status()["running"])
            print(f"  {'ok  ' if okay else 'FAIL'} the camera paused for plugging a speaker in: it stays off, a photo says why, "
                  f"the friend waits; resumed, it's back")
            if not okay:
                print(f"       {paused} {st} {took.get_json()} {friend.why!r} {back}")
                FAILED.append("camera pause")
        finally:
            for name in (set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()) - had:
                _photos.delete(name)
            pieapp.service.set_mode("clock")

        pieapp.service.shutdown()

        print("the speaker (stand-in Piper and aplay)")
        speaker_check()

        print("a USB GPS dropping off the bus")
        gps_drop_check()

        print("a camera picked by node that isn't there any more")
        from pie_ink import camera as _cam0, settings as _cfg0
        migrated = _cfg0._migrate({"camera": {"source": "hdmi", "hdmi_mode": "1080p30", "mirror": True}})["camera"]
        okay = migrated == {"source": "auto", "mirror": True}
        print(f"  {'ok  ' if okay else 'FAIL'} a saved source that no longer exists becomes Auto ({migrated})")
        if not okay:
            FAILED.append("camera source migrate")

        class _Answers:
            name, id, card, format, raw = "Some webcam (video3)", "usb:/dev/video3", "Some webcam", "640x480 MJPG", False

            def read(self):
                return _Img.new("RGB", (480, 360), (90, 120, 150))

            def close(self):
                pass
        usb_was = _cam0._UsbCamera
        _cam0._UsbCamera = lambda device=None, card=None: (_ for _ in ()).throw(RuntimeError(f"{device}: wouldn't open")) if device else _Answers()
        try:
            cam0 = _cam0.Camera()
            src = cam0._open("usb:/dev/video0")
            okay = getattr(src, "id", None) == "usb:/dev/video3" and cam0.error is None
            print(f"  {'ok  ' if okay else 'FAIL'} picked /dev/video0, which gives nothing now: the camera that answers is used instead "
                  f"({getattr(src, 'name', None)})")
            if not okay:
                FAILED.append("camera node fallback")
        finally:
            _cam0._UsbCamera = usb_was

        print("a webcam's formats")
        from pie_ink import camera as _camera
        cases = [({"MJPG": [(640, 480), (1280, 720), (1920, 1080), (3840, 2160)], "YUYV": [(640, 480)]}, ("MJPG", (640, 480))),
                 ({"MJPG": [(1920, 1080), (3840, 2160)], "YUYV": [(320, 240), (640, 480), (1280, 720)]}, ("YUYV", (640, 480))),
                 ({"MJPG": [(1920, 1080)], "YUYV": [(1920, 1080)]}, ("MJPG", (1920, 1080))),
                 ({"MJPG": [(320, 240), (640, 360)]}, ("MJPG", (640, 360))),
                 ({}, ("MJPG", _camera.STREAM_SIZE))]
        for offered, want in cases:
            got = _camera.choose_format(offered)
            okay = got == want
            print(f"  {'ok  ' if okay else 'FAIL'} {offered or 'nothing known'} -> {got}")
            if not okay:
                FAILED.append(f"choose_format {offered}")
        if _camera._fourcc_name(int.from_bytes(b"MJPG", "little")) != "MJPG":
            FAILED.append("fourcc name")

        print("a camera that stops sending (as one did while its gimbal turned)")
        import io as _io
        from PIL import Image as _Img2
        _b = _io.BytesIO()
        _Img2.new("RGB", (64, 48), (10, 120, 200)).save(_b, "JPEG")
        still_jpeg = _b.getvalue()

        class _Stuck:
            name, id, card, raw, format = "Stuck camera (video9)", "usb:/dev/video9", "Stuck camera", True, "64x48 MJPG"

            def __init__(self):
                self.n = 0

            def read(self):
                self.n += 1
                if self.n > 5:                                # sends a few frames, then nothing ever again
                    _t.sleep(0.3)
                    raise RuntimeError("USB camera read failed")
                _t.sleep(0.03)
                return still_jpeg

            def close(self):
                pass

        class _Fine(_Stuck):
            def read(self):
                _t.sleep(0.03)
                return still_jpeg

        stall_was = _camera.STALL_SECONDS
        _camera.STALL_SECONDS = 0.8
        cam = _camera.Camera()
        opened = []
        cam._open = lambda preferred: opened.append("first") or _Stuck()
        cam._open_again = lambda was, card: opened.append(("again", was, card)) or _Fine()
        cam.preferred = "usb:/dev/video9"
        try:
            cam.touch(10)
            seq, _ = cam.wait_jpeg(0, timeout=3)
            deadline = _t.time() + 8
            while _t.time() < deadline and not (cam.restarts and cam.status()["age"] is not None and cam.status()["age"] < 0.3):
                _t.sleep(0.1)
            st = cam.status()
            okay = (cam.restarts == 1 and st["running"] and not st["stalled"] and st["error"] is None
                    and opened[-1] == ("again", "usb:/dev/video9", "Stuck camera"))
            print(f"  {'ok  ' if okay else 'FAIL'} it stopped sending; it was closed and opened again, and the picture "
                  f"carries on (restarts {cam.restarts}, age {st['age'] and round(st['age'], 2)}, error {st['error']!r})")
            if not okay:
                FAILED.append("camera stall reopen")
        finally:
            cam._wanted_until = 0
            _camera.STALL_SECONDS = stall_was

        print("the live view on a slow link (it stays live: skips frames, then sends smaller ones)")
        # the server holds little back for a client that isn't taking it: what it did hold (16 MB) was
        # seconds of camera frames, and the picture on a phone ran that far behind, in slow motion
        mark = pieapp.SERVER_OPTIONS.get("outbuf_high_watermark", 1 << 30)
        try:
            from waitress.adjustments import Adjustments as _Adj
            _Adj(**pieapp._server_options())                    # every option is one this waitress knows
            known = True
        except ImportError:
            known = None
        except Exception as e:
            known = f"{e}"
        okay = mark <= 256 * 1024 and known in (True, None)
        note = "waitress takes every option" if known is True else "no waitress here" if known is None else known
        print(f"  {'ok  ' if okay else 'FAIL'} the server holds back at most {mark // 1024} KB of a response ({note})")
        if not okay:
            FAILED.append("server output buffer")
        _b = _io.BytesIO()
        big_scene = _Img2.radial_gradient("L").resize((1280, 720)).convert("RGB")
        _Draw.Draw(big_scene).rectangle([100, 100, 1100, 600], outline=(255, 80, 80), width=12)
        for i in range(0, 1280, 16):                            # detail, so the JPEG is a real camera's size
            _Draw.Draw(big_scene).line([i, 0, 1280 - i, 720], fill=(i % 255, 200, 80), width=2)
        big_scene.save(_b, "JPEG", quality=95)
        big_jpeg = _b.getvalue()
        cam2 = _camera.Camera()
        cam2._jpeg, cam2._seq = big_jpeg, 7
        seq, small = cam2.small_jpeg(640, 70)
        small_w = _Img2.open(_io.BytesIO(small)).size[0] if small else 0
        okay = seq == 7 and small_w == 640 and len(small) < len(big_jpeg) / 3 and cam2.small_jpeg(640, 70)[1] is small
        print(f"  {'ok  ' if okay else 'FAIL'} a smaller frame: {len(big_jpeg) // 1024} KB at 1280 wide -> "
              f"{len(small or b'') // 1024} KB at {small_w} wide, made once per frame")
        if not okay:
            FAILED.append("camera small frame")

        class _Link:
            """A camera as a viewer sees it, at 12 frames a second."""
            fps = 12.0

            def __init__(self, jpg):
                self.jpg, self.seq = jpg, 100

            def wait_jpeg(self, last_seq, timeout=1.0):
                self.seq += 1
                return self.seq, self.jpg

            def small_jpeg(self, width, quality):
                return self.seq, cam2.small_jpeg(width, quality)[1] if width == 640 else b"\xff\xd8" + b"s" * 12_000

        def feed(v, link, held, n):
            """n frames through the viewer, the socket holding each for `held` seconds."""
            out = []
            for _ in range(n):
                if v.yielded_at:
                    v.yielded_at = _t.time() - held
                got = len(v.next(link))
                out.append((v.level, got))                    # the level after that frame, and its size
            return out

        v, link = _camera.Viewer(), _Link(big_jpeg)
        v.WARM_UP = 0.0                                        # judged at once here, not after a moment
        steps = feed(v, link, 0.005, 20)                       # each frame gone in 5 ms: keeping up
        okay = v.level == -1 and {n for _, n in steps} == {len(big_jpeg)}
        print(f"  {'ok  ' if okay else 'FAIL'} a link that keeps up gets the camera's own frames")
        if not okay:
            FAILED.append("viewer fast link")
        v.sent = []                                            # (time passes: the window empties)
        steps = feed(v, link, 0.3, 12)                         # each frame held 0.3 s: saturated at 12 fps
        levels = [lv for lv, _ in steps]
        got640 = any(lv == 0 and n == len(small) for lv, n in steps)
        got480 = any(lv == 1 and n == 12_002 for lv, n in steps)
        okay = levels[0] == -1 and got640 and got480 and levels[-1] == 1 and v.changes == 2 and levels.index(0) == 1
        print(f"  {'ok  ' if okay else 'FAIL'} a link the socket can't keep up with gets 640 px frames, then 480 px "
              f"(after {levels.index(0) if 0 in levels else '-'} and {levels.index(1) if 1 in levels else '-'} frames)")
        if not okay:
            FAILED.append("viewer slow link")

        def until_own(most=10):
            """Frames gone in 5 ms (keeping up) until the camera's own frames are tried again."""
            for i in range(most):
                if feed(v, link, 0.005, 1)[0][1] == len(big_jpeg):
                    return i + 1
            return -1

        v.since, v.hold, v.sent = 0.0, 0.001, []               # keeping up with the small ones for long enough
        first = until_own()
        feed(v, link, 0.6, 2)                                  # the socket held two of them 0.6 s each: that's the link
        stayed, hold = v.level, v.hold
        v.since = 0.0
        second = until_own()
        feed(v, link, 0.02, 4)                                 # this time they go in 20 ms: they stay
        okay = first == 3 and stayed == 0 and hold == 0.002 and second == 3 and v.level == -1 and v.changes == 5
        print(f"  {'ok  ' if okay else 'FAIL'} after a while the camera's own frames are tried again: held 0.6 s each it goes back "
              f"to small ones (for twice as long), gone in 20 ms they stay (level {v.level}, {v.changes} changes)")
        if not okay:
            FAILED.append("viewer try again")
        v, link = _camera.Viewer(), _Link(b"\xff\xd8" + b"x" * 30_000)    # small frames held long: nothing to gain
        v.WARM_UP = 0.0
        feed(v, link, 0.3, 20)
        okay = v.level == -1 and v.changes == 0
        print(f"  {'ok  ' if okay else 'FAIL'} small frames that the socket holds stay as they are (a smaller one would gain nothing)")
        if not okay:
            FAILED.append("viewer small frames")
        r = c.get("/api/camera/stream", buffered=False)
        first = next(iter(r.response), b"")
        r.close()
        if b"\xff\xd8" not in first or b"Content-Length:" not in first:
            print(f"  FAIL the live view should stream JPEG frames: {first[:60]!r}")
            FAILED.append("camera stream")
        else:
            print("  ok   the live view streams frames through the viewer")

        print("the e-ink HATs on a pretend Pi (the real drivers over a fake bus)")
        panel_check()

    print()
    if FAILED:
        print(f"{len(FAILED)} problem(s): {', '.join(FAILED)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
