"""PiE-ink web server.

    python3 app.py                       # real panel (driver from config.yaml)
    PIE_INK_DRIVER=mock python3 app.py   # run anywhere, no hardware
"""
import base64
import contextlib
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import psutil
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from pie_ink import (agent, attention, audio, borrow, buddy, cache, chat, coingecko, doing, drives, friends, gestures, here, holdings, home, household, lcd,
                     lcd_screens, gamepad, listen, llm, locate, mascot, mesh, mining, moods, music, notebook, pad, panels, pet, photos, pitato,
                     pitato_screen, plugs, presence, ptz,
                     reader, reminders,
                     shelf, sight, snake, social, sources, stocks, sysupdate, telegram, usbwatch, watcher, watchlist, weather, whisplay)
from pie_ink.paint import PAINT
from pie_ink.modes import postcard
from pie_ink.modes.me import Day
from pie_ink import camera
from pie_ink.camera import CAMERA
from pie_ink.modes.reader import STATE as reading
from pie_ink import settings as cfg
from pie_ink.modes import MODES
from pie_ink.modes.simple import ImageMode
from pie_ink.service import get_service
from pie_ink import text as fonts_lib
from pie_ink.text import FONT_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.json.sort_keys = False
service = get_service()


def ok(**kw):
    return jsonify({"ok": True, **kw})


def fail(message, code=400):
    return jsonify({"ok": False, "error": message}), code


# -- pages -------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/fonts/<path:name>")
def fonts(name):
    return send_from_directory(FONT_DIR, name, max_age=86400)


@app.route("/icons/<path:name>")
def icons(name):
    return send_from_directory(os.path.join(cfg.ASSETS_DIR, "icons"), name, max_age=86400)


# -- status / settings ---------------------------------------------------------

@app.get("/api/status")
def api_status():
    return ok(
        service=service.status(),
        settings=service.config,
        modes=[{"id": m.name, "label": m.label} for m in MODES.values()],
        panels=panels.describe(),
        fonts=fonts_lib.available(),
        hostname=socket.gethostname(),
    )


@app.get("/api/now")
def api_now():
    """What is going on, for the page's side column: one cheap call, nothing
    fetched that isn't already known."""
    conf = service.config
    out = {"mode": service.mode_name, "speech": (llm.latest().get("text") or "").strip()}
    w = conf.get("weather", {})
    if w.get("lat") is not None:
        wx = weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"), cached_only=True)
        if wx:
            out["weather"] = {"temp": round(wx["temp"]), "units": wx["units"], "text": weather.describe(wx["code"])[0],
                              "place": w.get("location") or ""}
    m = music.PLAYER.status()
    if m["state"] in ("playing", "paused") and m.get("title"):
        out["music"] = {"state": m["state"], "title": m["title"]}
    wt = watcher.WATCHER.status()
    if wt["running"]:
        out["watcher"] = {"state": wt["state"], "seen_ago": wt["seen_ago"]}
    room = presence.summary()
    if room:
        out["room"] = room
    tv = home.HOME.tv_state()
    if tv:
        out["tv"] = tv
    pools = []
    for m in mining.enabled():
        dd = m.snapshot()
        if dd:
            pools.append({"kind": m.kind, "symbol": m.symbol, "hashrate": dd["hashrate"], "hashrate_text": dd["hashrate_text"],
                          "workers": dd["workers"], "balance": dd["balance"], "alert": bool(dd.get("alert"))})
    if pools:
        out["mining"] = pools
    hv = holdings.HOLDINGS.value_now
    if hv and hv["items"]:
        out["holdings"] = {"total": hv["total"], "day": hv["day"], "day_pct": hv["day_pct"],
                           "move": (holdings.HOLDINGS.move or {}).get("kind"), "whose": holdings.whose(hv)}
    due = reminders.REMINDERS.pending()
    if due:
        out["reminder"] = {"text": due[0]["text"], "at": due[0]["at"], "words": reminders.REMINDERS.describe(due[0]),
                           "more": len(due) - 1}
    rooms = household.HOUSE.rooms()
    if rooms:
        out["rooms"] = {"count": len(rooms), "labels": [r["label"] for r in rooms[:4]],
                        "showing": [r.get("mode") or "" for r in rooms[:4]]}
    pt = pitato.PITATO.status()
    if pt["any"]:
        first = pt["pitatos"][0]
        out["pitato"] = {"name": first["name"], "ago": first["ago"], "asks": first["asks"], "url": first["url"], "here": first["here"],
                         "cameras": first["cameras"], "more": len(pt["pitatos"]) - 1, "excited": pt["excited"],
                         "activity": first["activity"]}
    last = sight.events(limit=1)
    if last:
        out["event"] = {"text": last[0]["text"], "ago": int(time.time() - last[0]["ts"]), "level": last[0].get("level")}
    ag = agent.AGENT.status()
    if ag["enabled"]:
        out["agent"] = {"thought": (ag["last"] or {}).get("thought") or "", "level": (ag["last"] or {}).get("level"),
                        "ago": ag["last_ago"], "busy": ag["busy"], "error": ag["error"]}
    lc = conf.get("lcd", {})
    if lc.get("enabled") and not service.panel.colour:
        st = lcd.SECOND.status()
        names = dict(lcd_screens.LABELS)
        out["lcd"] = {"screen": st.get("screen"), "label": names.get(st.get("screen"), st.get("screen")),
                      "running": st.get("running"), "error": st.get("error")}
    sg = snake.GAME.status()
    if sg["playing"]:
        out["snake"] = {"score": sg.get("score", 0), "best": sg["best"], "model": sg["model"], "error": sg["error"],
                        "bored": _bored["from"] is not None}
    return ok(**out)


@app.get("/api/pet")
def api_pet():
    """The head that answers yes and no: whether it can, and the last question it answered."""
    return ok(available=ptz.PTZ.available(), gesture=ptz.PTZ.gesturing, last=pet.status(),
              model=pet.model(service.config.get("pet", {})))


def _head(kind):
    """Nod, shake or tilt its head, in the background: (started, what to say)."""
    kind = {"yes": "nod", "no": "shake", "unsure": "tilt", "hmm": "tilt"}.get(kind, kind)
    if kind not in ("nod", "shake", "tilt"):
        return False, "nod, shake or tilt"
    if not ptz.PTZ.available():
        return False, "no camera that turns is plugged in"
    if ptz.PTZ.gesturing or ptz.PTZ.looking:
        return False, "its head is busy"
    threading.Thread(target=ptz.PTZ.gesture, args=(kind,), kwargs={"tempo": pet.tempo(service.config.get("pet", {}))},
                     daemon=True, name="pet-head").start()
    return True, {"nod": "Nodding.", "shake": "Shaking its head.", "tilt": "Tilting its head."}[kind]


@app.post("/api/pet/try")
def api_pet_try():
    """Nod, shake or tilt, to see it: {"gesture": "nod"} — in the background."""
    kind = ((request.get_json(silent=True) or {}).get("gesture") or "nod").strip().lower()
    started, why = _head(kind)
    if not started:
        return fail(why)
    return ok(started=kind)


@app.get("/api/snake")
def api_snake():
    """How the game on the little screen is going."""
    return ok(**snake.GAME.status(), bored=_bored["from"] is not None,
              idle_minutes=round(attention.idle_seconds() / 60, 1))


@app.before_request
def _someone_is_here():
    """Anything you do on the page counts as attention; the page's own polling doesn't.
    A request from a Pietato (its User-Agent says so) means the handheld Pi is about."""
    if pitato.PITATO.is_pitato(request.headers.get("User-Agent")):
        pitato.PITATO.heard(request.remote_addr, request.path)
        return
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") \
            and not request.path.startswith(AUTOMATIC):
        attention.touch()


# the page sends these on its own, without a hand on it
AUTOMATIC = ("/api/lcd/preview.png", "/api/render.png", "/api/here/", "/api/ptz/stop", "/api/camera/pause")
# bored: "from" is the LCD screen Snake took over from (None = not bored); see _bored_loop
_bored = {"from": None, "checked": 0.0, "there": False, "ended": 0.0}


@app.put("/api/settings")
def api_settings():
    patch = request.get_json(silent=True) or {}
    try:
        config = service.apply_settings(patch)
    except Exception as e:
        log.exception("settings update failed")
        return fail(str(e), 500)
    _after_settings(patch, config)
    return ok(settings=config)


def _after_settings(patch, config):
    """The parts of the app that keep their own thread pick up their new
    settings now, whichever route saved them."""
    if "audio" in patch and "device" in patch["audio"]:    # a new output takes the saved level
        conf = config.get("audio", {})
        threading.Thread(target=audio.set_volume, args=(int(conf.get("volume", 80) or 80), conf.get("device", "")),
                         daemon=True).start()
    if "listen" in patch:                 # a new phrase, mic or model takes effect now
        threading.Thread(target=_relisten, daemon=True).start()
    if "camera_watch" in patch:
        threading.Thread(target=start_watching, daemon=True).start()
    if "lcd" in patch or "display" in patch:
        threading.Thread(target=_lcd_apply, args=(config,), daemon=True).start()
    if "display" in patch or "whisplay" in patch:
        threading.Thread(target=start_board, daemon=True).start()
    if "display" in patch:
        threading.Thread(target=start_gamepad, daemon=True).start()
    if "home" in patch:
        home.HOME.configure(config.get("home", {}))
    if "gestures" in patch or "home" in patch:
        gestures.GESTURES.configure(config.get("gestures", {}))
    if "agent" in patch:
        threading.Thread(target=agent.AGENT.update, args=(config.get("agent", {}),), daemon=True).start()
    if "ptz" in patch:
        ptz.PTZ.configure(config.get("ptz", {}))
    if "camera" in patch:
        camera.CAMERA.configure(config.get("camera", {}))
    if "buddy" in patch:
        buddy.BUDDY.update(config.get("buddy", {}))
    for pool in ("duco", "verus"):
        if pool in patch:
            threading.Thread(target=mining.MINERS[pool].update, args=(config.get(pool, {}),), daemon=True).start()
    if "holdings" in patch:
        threading.Thread(target=holdings.HOLDINGS.update, args=(config.get("holdings", {}),), daemon=True).start()
    if "me" in patch or "camera" in patch or "audio" in patch or "listen" in patch or "llm" in patch:
        mesh.my_caps(config, fresh=True)          # the beacon tells the others straight away


@app.post("/api/mode")
def api_mode():
    body = request.get_json(silent=True) or {}
    name = body.get("mode")
    if name not in MODES:
        return fail("unknown mode")
    if body.get("settings"):
        config = service.apply_settings(body["settings"])
        _after_settings(body["settings"], config)
    service.set_mode(name)
    if body.get("remember", True):
        service.apply_settings({"startup_mode": name})
    return ok(mode=name)


@app.post("/api/button")
def api_button():
    """Act as if a HAT key was pressed (1-4)."""
    body = request.get_json(silent=True) or {}
    try:
        key = int(body.get("key", 0))
    except (TypeError, ValueError):
        return fail("key must be 1-4")
    if not 1 <= key <= 4:
        return fail("key must be 1-4")
    service.on_button(key - 1)
    return ok()


@app.post("/api/refresh")
def api_refresh():
    service.refresh()
    return ok()


# -- preview ------------------------------------------------------------------

@app.get("/api/preview.png")
def api_preview():
    return Response(service.preview_png(), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/render.png")
def api_render():
    """Render what a mode would look like with the given (unsaved) settings."""
    body = request.get_json(silent=True) or {}
    name = body.get("mode")
    if name not in MODES:
        return fail("unknown mode")
    try:
        png = service.render_preview(name, body.get("settings"))
    except Exception as e:
        log.exception("preview render failed")
        return fail(str(e), 500)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})


# -- drawing ------------------------------------------------------------------

@app.post("/api/image")
def api_image():
    body = request.get_json(silent=True) or {}
    data = body.get("image", "")
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception:
        return fail("bad image data")
    if len(raw) > 4 * 1024 * 1024:
        return fail("image too large")
    try:
        ImageMode.store(raw)
    except ValueError:
        return fail("that is not an image")
    if body.get("show", True):
        service.set_mode("image")
        service.refresh()
        service.apply_settings({"startup_mode": "image"})
    return ok()


# -- reader -------------------------------------------------------------------

@app.get("/api/books")
def api_books():
    return ok(books=reader.library(), reading=reading.status(), jobs=reader.jobs(),
              pdf_ok=not reader.poppler_missing())


@app.get("/api/books/search")
def api_books_search():
    source, q = request.args.get("source", "gutenberg"), (request.args.get("q") or "").strip()
    try:
        if source == "gutenberg":
            if not q:
                return ok(results=[])
            return ok(results=sources.gutenberg_search(q))
        if source == "archive":
            return ok(results=sources.archive_search(q or "golden age"))
        if source == "xkcd":
            return ok(results=[sources.xkcd(q or "random")])
    except ValueError as e:
        return fail(str(e))
    except Exception as e:
        log.warning("search %s failed: %s", source, e)
        return fail(f"{source} is not reachable right now", 502)
    return fail("unknown source")


@app.post("/api/books/fetch")
def api_books_fetch():
    """Download a search result into the library (in the background)."""
    body = request.get_json(silent=True) or {}
    source, title = body.get("source"), body.get("title") or "Untitled"
    try:
        if source == "archive":
            url, ext, size = sources.archive_file(body["id"])
            if ext == ".pdf" and reader.poppler_missing():
                return fail("that one is a PDF; install poppler-utils on the Pi first")
            filename = f"{body['id']}{ext}"
        else:
            url, ext = body.get("url"), body.get("ext") or ".epub"
            if not url or not url.startswith("http"):
                return fail("no download link")
            if source == "xkcd":
                url = url.replace("http://", "https://")
            filename = f"{source}-{body.get('id', 'x')}{ext}"
        job = reader.fetch_async(url, filename, body.get("kind", "text"), title)
    except Exception as e:
        log.warning("fetch failed: %s", e)
        return fail(str(e), 502)
    return ok(job=job)


@app.post("/api/books")
def api_books_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("no file")
    data = f.read()
    if len(data) > 200 * 1024 * 1024:
        return fail("file too large (200 MB max)")
    try:
        book = reader.add(f.filename, data, request.form.get("kind") or None)
    except ValueError as e:
        return fail(str(e))
    except OSError as e:
        return fail(f"could not save: {e}", 500)
    reading.open(book)
    return ok(book=book, books=reader.library())


@app.delete("/api/books/<book_id>")
def api_books_delete(book_id):
    books = reader.remove(book_id)
    if reading.book and reading.book["id"] == book_id:
        reading.open(books[0]) if books else reading.__init__()
    return ok(books=books, reading=reading.status())


@app.post("/api/reader")
def api_reader():
    """Open a book / turn pages. Puts the reader on the panel if it isn't."""
    body = request.get_json(silent=True) or {}
    if body.get("book"):
        book = reader.get(body["book"])
        if not book:
            return fail("unknown book", 404)
        if not reading.book or reading.book["id"] != book["id"]:
            reading.open(book)
    action = body.get("action")
    if action == "next":
        reading.step(1)
    elif action == "prev":
        reading.step(-1)
    elif action == "goto":
        reading.go(int(body.get("page", 0)), 0)
    if service.mode_name != "reader":
        service.set_mode("reader")
        service.apply_settings({"startup_mode": "reader"})
    service.poke()
    return ok(reading=reading.status())


# -- the second screen ---------------------------------------------------------------

def _eink_frame():
    try:
        return service.panel.last_frame()
    except Exception:
        return None


def _lcd_render(conf):
    if time.time() < _buddy_face["until"] and service.config.get("buddy", {}).get("show_face", True):
        conf = dict(conf, screen="buddy")          # the little friend just reacted: its face, for a minute
    return lcd_screens.render(conf, eink=_eink_frame, config=service.config)


lcd.SECOND.render = _lcd_render          # known from the start, so enabling it later just works
lcd_screens.MAIN_MODE = lambda: service.mode_name   # "Same as the e-ink": what the panel shows, drawn for the LCD
home.HOME.configure(service.config.get("home", {}))
friends.where_from(lambda: locate.where(service.config))  # the beacon says where we are (the weather spot)
friends.caps_from(lambda: mesh.my_caps(service.config))  # and what we have, so "find the pi with a camera" works
lcd.SECOND.interval = lcd_screens.interval


def _lcd_conf(config):
    conf = dict(config.get("lcd", {}))
    if os.environ.get("PIE_INK_DRIVER") == "mock":
        conf["driver"] = "mock"                  # no hardware here either
    if _bored["from"] is not None:               # bored: Snake is up, without touching the saved choice
        conf["screen"] = "snake"
    return conf


def _lcd_apply(config):
    if service.panel.colour:                     # the main screen is an LCD: there is no second one
        lcd.SECOND.stop()
        return
    lcd.SECOND.update(_lcd_conf(config))


def _lcd_show(screen):
    """Put a screen on the LCD and remember it — if the LCD is switched on.
    On a colour main panel the painting is the Drawing screen itself."""
    if service.panel.colour:
        if screen == "paint" and service.mode_name != "image":
            service.set_mode("image")
            service.apply_settings({"startup_mode": "image"})
        return True
    conf = service.config.get("lcd", {})
    if not conf.get("enabled"):
        return False
    if conf.get("screen") != screen:
        _lcd_apply(service.apply_settings({"lcd": {"screen": screen}}))
    return True


def start_lcd():
    if service.panel.colour:
        return False, "the main screen is the LCD"
    return lcd.SECOND.start(_lcd_conf(service.config), _lcd_render)


# -- the Whisplay HAT's button and LED ---------------------------------------------------------

def _mood():
    """What the LED should say."""
    try:
        state = listen.EARS.status()["state"]
        if state == "listening":
            return "listening"
        if state == "thinking" or llm.busy():
            return "thinking"
        if audio.is_speaking():
            return "speaking"
        if social.talking():
            return "chatting"
    except Exception:
        pass
    return "off"


def _hold_to_talk():
    """The button held: stop talking if it was, and listen for what comes next."""
    if not service.config.get("whisplay", {}).get("hold_to_talk", True):
        return
    try:
        audio.stop_speaking()
    except Exception:
        pass
    if not listen.EARS.running():
        okay, why = start_listening()
        if not okay:
            llm.set_speech("", "listening", f"Can't listen: {why}")
            service.poke()
            return
        time.sleep(0.5)                            # the ears need a moment to open the microphone
    listen.EARS.wake_now()
    llm.set_speech("…", "listening")
    service.poke()


def start_gamepad():
    """The GPi Case 2W's buttons (or any game controller), only while its screen is the panel: each
    screen's own (pie_ink/pad.py), shown along the bottom of the case's screen."""
    if service.panel.name != "gpicase":
        gamepad.PADS.stop()
        return False
    gamepad.PADS.on_press = pad.press
    return gamepad.PADS.start()


@app.get("/api/gpi")
def api_gpi():
    """The GPi Case's buttons: the controllers being read, the last button pressed and what it did,
    and what this screen's buttons are."""
    pads = gamepad.PADS
    last = None
    if pads.last:
        last = {"button": pads.last[0], "did": pads.last[1], "ago": int(time.time() - pads.last[2])}
    hints, screen, _accent = pad.footer(service)
    return ok(running=pads.running(), found=pads.found, last=last, problem=pads.problem,
              panel=service.panel.name in ("gpicase", "mock_gpicase"), screen=screen,
              buttons=[{"key": k, "does": label, "on": bool(on)} for k, label, on in hints])


def start_board():
    """The Whisplay's button and LED, only while its screen is the panel."""
    board = whisplay.BOARD
    if service.panel.name != "whisplay":
        board.stop()
        return False
    conf = service.config.get("whisplay", {})
    board.led_on = bool(conf.get("led", True))
    board.on_press = lambda: service.on_button_action(service.config.get("whisplay", {}).get("button", "next_mode"))
    board.on_hold = _hold_to_talk
    board.mood = _mood
    return board.start()


def _whisplay_sound():
    """Where the HAT's speaker and microphone stand: found? chosen?"""
    card = whisplay.sound_card()
    conf = service.config
    return {"found": bool(card), "card": card,
            "speaker_in_use": bool(card and card.get("playback")
                                   and audio.same_device(conf.get("audio", {}).get("device"), card["playback"])),
            "mic_in_use": bool(card and card.get("capture")
                               and audio.same_device(conf.get("listen", {}).get("device"), card["capture"]))}


@app.get("/api/whisplay")
def api_whisplay():
    return ok(status=whisplay.BOARD.status(), panel=service.panel.name == "whisplay",
              colour=service.panel.colour, sound=_whisplay_sound())


@app.post("/api/whisplay/sound")
def api_whisplay_sound():
    """Use the HAT's speaker and microphones: pick them for the voice and the
    ears, and turn their levels up."""
    card = whisplay.sound_card()
    if not card:
        return fail("The HAT's sound card isn't there. Run ./setup.sh --whisplay on the Pi and reboot.")
    patch = {}
    if card.get("playback"):
        patch["audio"] = {"device": card["playback"]}
    if card.get("capture"):
        patch["listen"] = {"device": card["capture"]}
    levels = whisplay.set_levels()
    config = service.apply_settings(patch)
    _after_settings(patch, config)
    try:
        audio.set_volume(int(config.get("audio", {}).get("volume", 80)), card.get("playback", ""))
    except Exception:
        pass
    return ok(sound=_whisplay_sound(), levels=levels)


@app.get("/api/lcd")
def api_lcd():
    return ok(status=lcd.SECOND.status(), screens=lcd_screens.LABELS, models=lcd.models())


@app.route("/api/lcd/preview.png", methods=["GET", "POST"])
def api_lcd_preview():
    """What the LCD shows (GET), or what it would show with the page's
    unsaved changes (POST {"settings": {...}})."""
    import io as _io
    from flask import Response
    image = None
    patch = (request.get_json(silent=True) or {}).get("settings") if request.method == "POST" else None
    if patch:
        merged = cfg._deep_merge(service.config, patch)
        image = lcd_screens.render(merged.get("lcd", {}), eink=_eink_frame, config=merged)
    else:
        image = lcd.SECOND.preview()
    if image is None:
        image = lcd_screens.render(service.config.get("lcd", {}), eink=_eink_frame, config=service.config)
    buf = _io.BytesIO()
    image.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


# -- painting on the LCD --------------------------------------------------------------------

@app.get("/api/paint.png")
def api_paint_png():
    """The colour canvas as it is (404 while nothing has been painted)."""
    import io as _io
    image = PAINT.image()
    if image is None:
        return fail("nothing painted yet", 404)
    buf = _io.BytesIO()
    image.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/paint")
def api_paint():
    """The Draw tab's LCD canvas, whole, as it changes. With show (the
    default) the LCD switches to its Paint screen if it is on something else."""
    body = request.get_json(silent=True) or {}
    data = body.get("image", "")
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception:
        return fail("bad image data")
    if len(raw) > 2 * 1024 * 1024:
        return fail("image too large")
    try:
        version = PAINT.set(raw)
    except Exception as e:
        return fail(f"that is not a picture ({e})")
    shown = _lcd_show("paint") if body.get("show", True) else False
    if service.panel.colour:
        service.poke()                           # the main screen is the LCD: show the stroke now
    return ok(version=version, shown=shown, lcd_on=bool(service.config.get("lcd", {}).get("enabled")))


# -- Telegram --------------------------------------------------------------------------

TG_HELP = """What I can do from here:
/screen <name> — put a screen up (clock, weather, me, earnings, message, drawing, book, camera, music, chat, finance, system, off)
/screens — list them
/panel — a picture of what's on the panel now
/photo — a picture from the camera; /photo sepia (or bw, sketch, edges, comic, pixel, e-ink…) takes a photo with that filter and keeps it
/photos — the last photo you took
/say <words> — say it out loud on the Pi
/message <words> — put it on the message screen
/status — how things are
/events — what the camera has seen today (person, package, door, pets)
/agent — what the agent last thought; /agent now for a fresh check
/pis — the other PiE-inks and what each has, and a Pietato when one's about
/look — a camera that turns looks around and says what it sees, with the picture; /look left, /look up…
/follow — an OBSBOT follows you by itself; /follow off to stop (or say "follow me", "stop following me")
/yes, /no — the camera nods, or shakes its head; /hmm tilts it (a yes-or-no question gets this by itself)
/friend — how the little friend is; /friend on, /friend off
/mining — your miners: balance, hashrate, workers (Duino-Coin and Verus); /duco, /verus for one
/holdings — what your coins and shares are worth, one by one, and how today went
/pietato time (or /pietato weather — any screen —, /pietato drawing, /pietato <words>) — on the Pietato's screen, full screen
/remind in 20 minutes to check the oven — a reminder (or /remind at 5 pm …, tomorrow at 9 …); /timer 10 — a timer; /reminders — what's set
/remember the bins go out on tuesday — into its notebook; /notes — what it remembers; /forget the bins
/tv — a remote for the TV, as buttons (Home Assistant, once it's set up); /tv 5452, /tv on, /tv volume up
turn on the tv, channel 5452, volume up, turn off the lamp — the same, in words
take a picture, take a black and white photo — a photo, kept on the Pi
show me the camera from upstairs, send this drawing to the workshop pi, which pi has a camera — the other Pis
Anything else and I'll just answer you."""

SCREEN_WORDS = {"drawing": "image", "draw": "image", "book": "reader", "read": "reader", "you": "me",
                "chat": "groupchat", "group": "groupchat",
                "finance": "crypto", "stocks": "crypto", "shares": "crypto", "markets": "crypto", "coins": "crypto",
                "salary": "earnings", "pay": "earnings", "income": "earnings",
                "friend": "buddy", "face": "buddy"}

# the remote /tv puts in the chat: rows of (label, what the tap does)
TV_BUTTONS = {
    "on": ("tv_power", "on"), "off": ("tv_power", "off"), "mute": ("tv_mute", None), "source": ("tv_key", "source"),
    "vol-": ("tv_volume", "down", 1), "vol+": ("tv_volume", "up", 1),
    "ch-": ("tv_channel_step", "down"), "ch+": ("tv_channel_step", "up"),
    "home": ("tv_key", "home"), "up": ("tv_key", "up"), "back": ("tv_key", "back"),
    "left": ("tv_key", "left"), "ok": ("tv_key", "enter"), "right": ("tv_key", "right"),
    "menu": ("tv_key", "menu"), "down": ("tv_key", "down"), "exit": ("tv_key", "exit"),
    "guide": ("tv_key", "guide"), "info": ("tv_key", "info"),
    **{d: ("tv_key", d) for d in "0123456789"},
}
TV_LAYOUT = [
    [("On", "on"), ("Off", "off"), ("Mute", "mute"), ("Source", "source")],
    [("Vol −", "vol-"), ("Vol +", "vol+"), ("Ch −", "ch-"), ("Ch +", "ch+")],
    [("Home", "home"), ("▲", "up"), ("Back", "back")],
    [("◀", "left"), ("OK", "ok"), ("▶", "right")],
    [("Menu", "menu"), ("▼", "down"), ("Exit", "exit")],
    [("1", "1"), ("2", "2"), ("3", "3")],
    [("4", "4"), ("5", "5"), ("6", "6")],
    [("7", "7"), ("8", "8"), ("9", "9")],
    [("Guide", "guide"), ("0", "0"), ("Info", "info")],
]


def _tv_remote():
    """The /tv message: the TV's name and state, with the remote under it."""
    if not home.HOME.enabled():
        return "Home Assistant is switched off — Settings → Home on the page."
    if not home.HOME.ready() or not home.HOME.conf.get("tv"):
        return "No TV is set up yet — Settings → Home on the page."
    st = home.HOME.state_of(home.HOME.conf["tv"])
    name = ((st or {}).get("attributes") or {}).get("friendly_name") or "The TV"
    text = f"{name}: {home._state_word(st)}." if st else f"{name} — can't ask right now ({home.HOME.error})."
    return {"text": text + " Digits go to the TV one at a time, like a real remote — for a channel in one go, "
                    "type \"channel 5452\".",
            "buttons": [[(label, f"tv:{key}") for label, key in row] for row in TV_LAYOUT]}


def _tg_tap(data):
    """A button under a Telegram message: "tv:up" and friends."""
    kind, _, key = (data or "").partition(":")
    if kind == "tv" and key in TV_BUTTONS:
        return home.HOME.act(TV_BUTTONS[key], label=f"remote {key}")
    return "That button doesn't do anything any more."


def _tg_conf():
    return service.config.get("telegram", {})


def _tg_paired(chat_id, name):
    service.apply_settings({"telegram": {"chat_id": str(chat_id), "chat_name": name}})
    log.info("telegram: paired with %s", name)


def _tg_notify(text, photo=None):
    """Something happened worth telling you about."""
    conf = _tg_conf()
    if not telegram.paired(conf):
        return
    if photo is not None:
        try:
            import io as _io
            buf = _io.BytesIO()
            photo.convert("RGB").save(buf, "PNG")
            telegram.send_photo(conf, buf.getvalue(), text)
            return
        except Exception as e:
            log.info("telegram photo: %s", e)
    telegram.send_text(conf, text)


def _tg_command(text):
    """A line from your Telegram chat. Returns text, or (png, caption) for a picture."""
    attention.touch()
    conf = service.config
    low = text.lower().strip()
    said = re.match(r"/(?:pietato|pie-tato|pitato)(?:\s+|$)", low)        # (/pitato, as it was first called)
    if said:
        rest = text.strip()[said.end():].strip()
        word = rest.lower()
        kind = ("clock" if word in ("", "time", "clock") else "drawing" if word in ("drawing", "draw", "picture")
                else "stop" if word in ("stop", "off") else "screen" if _screen_named(word) else "message")
        return _pitato_screen_do(kind, None, rest if kind == "message" else "", name=word if kind == "screen" else None)[1]
    if low in ("/start", "/help", "help"):
        return TG_HELP
    from pie_ink.modes import MODES
    if low.startswith("/screens"):
        return "Screens: " + ", ".join(sorted(m for m in MODES if m != "postcard")) + ", off"
    if low.startswith(("/screen", "/show")):
        name = low.split(None, 1)[1].strip() if " " in low else ""
        name = SCREEN_WORDS.get(name, name)
        if name not in MODES and name != "off":
            return "Which screen? " + ", ".join(sorted(MODES)) + ", off"
        service.set_mode(name)
        return f"Showing {name}."
    if low.startswith(("/tv", "/remote")):
        words = text.split(None, 1)[1].strip() if " " in text else ""
        if not words:
            return _tv_remote()
        first = home.HOME.tv_names()[0]
        if words.isdigit():
            words = f"channel {words}"
        elif words.lower() in ("on", "off"):
            words = f"turn {words} the {first}"
        elif not home.parse(words, home.HOME.tv_names()):
            words = f"open {words} on the {first}"       # "/tv netflix"
        return _home_command(words, "telegram") or f"I don't know how to do '{words}' — /tv on its own shows the buttons."
    if low.startswith("/events"):
        items = sight.events(limit=20, since=time.time() - 24 * 3600)
        if not items:
            return "Nothing seen in the last day." + ("" if watcher.WATCHER.running() else " (Watching is off — Settings → Watching.)")
        return "\n".join(f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['text']}" for e in reversed(items))
    if low.startswith("/agent"):
        st = agent.AGENT.status()
        if not st["enabled"]:
            return "The agent is off — turn it on under Settings → Agent."
        if "now" in low.split()[1:]:
            entry = agent.AGENT.tick("telegram")
            if entry is None:
                return agent.AGENT.error or "It's busy — try again in a minute."
            if entry.get("error"):
                return f"That didn't work: {entry['error']}"
            acts = "; ".join(f"{a['do']}: {a['text']} ({a['result']})" for a in entry["actions"])
            return f"[{entry['level']}] {entry['thought']}" + (f"\nDid: {acts}" if acts else "")
        last = st["last"] or {}
        if not last.get("ts"):
            return "It hasn't checked yet." + (f" Next in {st['next_in'] // 60} min." if st.get("next_in") is not None else "")
        when = time.strftime("%H:%M", time.localtime(last["ts"]))
        acts = "; ".join(f"{a['do']}: {a['text']}" for a in last.get("actions", []))
        return f"{when} [{last.get('level')}] {last.get('thought')}" + (f"\nDid: {acts}" if acts else "") + "\n/agent now for a fresh check."
    if low.startswith("/pis"):
        return _mesh_do({"kind": "list"}, _everyone_for_mesh(), "telegram")
    first = (low.split() or [""])[0]
    if first in ("/yes", "/no", "/nod", "/shake", "/hmm", "/tilt"):
        started, why = _head({"/yes": "nod", "/no": "shake", "/nod": "nod", "/shake": "shake", "/hmm": "tilt", "/tilt": "tilt"}[first])
        if not started:
            return "There's no camera here that turns." if "no camera" in why else f"Couldn't — {why}."
        return why
    if low.startswith("/look"):
        where = text.split(None, 1)[1].strip() if " " in text else "around"
        done = _camera_command(f"look {where}", "telegram")
        if done is None:
            return "This camera can't turn — /photo sends what it sees."
        return done
    if low.startswith("/friend"):
        arg = low.split(None, 1)[1].strip() if " " in low else ""
        if arg in ("on", "off"):
            config = service.apply_settings({"buddy": {"enabled": arg == "on"}})
            buddy.BUDDY.update(config.get("buddy", {}))
            return ("The little friend is on: it'll look around and say hi." if arg == "on"
                    else "The little friend is off.")
        st = buddy.BUDDY.status()
        if not st["enabled"]:
            return "The little friend is off. /friend on wakes it."
        caption = st["caption"] + ("" if st["caption"][-1:] in "!?." else ".")
        return f"{st['face']} {caption}" + (f" Last said: {st['line']}" if st["line"] else "")
    if low.startswith("/follow"):
        if not ptz.PTZ.available():
            return "There's no camera here that turns."
        arg = low.split(None, 1)[1].strip() if " " in low else "on"
        return _follow_command(arg not in ("off", "stop", "no", "0", "false"))
    if low.startswith(("/holdings", "/portfolio", "/worth")):
        return holdings.HOLDINGS.summary()
    if low.startswith(("/mining", "/duco", "/verus")):
        pools = [mining.MINERS[k] for k in ("duco", "verus") if low.startswith(f"/{k}")] or list(mining.MINERS.values())
        out = []
        for m in pools:
            if not m.enabled():
                if low.startswith("/mining") and len(pools) > 1:
                    continue
                out.append(f"{m.title.title()} is off — set it up under Finance → {m.title.title()}.")
                continue
            d = m.snapshot()
            if not d:
                out.append(f"No numbers yet for {m.who_short()}" + (f" — {m.error}" if m.error else "") + ".")
                continue
            dec = m.decimals
            lines = [f"{m.title.title()} · {m.who_short()}: {d['balance']:,.{dec}f} {m.symbol} {m.balance_label}"
                     + (f" (${d['balance'] * d['price']:,.2f})" if d.get("price") else "")
                     + (f", {d['paid']:,.{dec}f} paid out" if d.get("paid") is not None else ""),
                     (f"Mining at {d['hashrate_text']} with {d['workers']} worker{'s' if d['workers'] != 1 else ''}" if d["hashrate"] > 0 else "Nothing mining right now")
                     + (f" — under your {mining.hashrate_text(d['line'])} line" if d.get("alert") else "")]
            if d.get("per_day") is not None:
                lines.append(f"About {d['per_day']:.{dec}f} {m.symbol} a day")
            lines += [f"{w['identifier']}: {mining.hashrate_text(w['hashrate'])}" + (f" ({w['software']})" if w.get("software") else "") for w in d["miners"][:8]]
            tail = [x for x in (f"trust {d['trust_score']}" if d.get("trust_score") is not None else "",
                                f"luck {d['luck']}" if d.get("luck") else "",
                                f"network {d['net_hashrate']}" if d.get("net_hashrate") else "") if x]
            if tail:
                lines.append(" · ".join(tail))
            out.append("\n".join(lines))
        return "\n\n".join(out) if out else "No mining set up — Finance → Duino-Coin or Verus."
    if low.startswith("/reminders"):
        return reminders.REMINDERS.do({"kind": "list"}, "telegram")
    if low.startswith("/remind"):
        arg = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        if not arg:
            return "Remind you of what, and when? /remind in 20 minutes to check the oven"
        return _reminder_command("remind me " + arg, "telegram") or "Couldn't find a time in that — /remind in 20 minutes to …, or at 5 pm …"
    if low.startswith("/timer"):
        arg = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        arg = arg if re.search(r"[a-z]", arg) else (f"{arg} minutes" if arg else "")
        return _reminder_command(f"set a timer for {arg}", "telegram") if arg else "A timer for how long? /timer 10"
    if low.startswith("/remember"):
        arg = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        return notebook.do({"kind": "add", "text": arg}, "telegram") if arg else "Remember what? /remember the bins go out on tuesday"
    if low.startswith("/notes"):
        return notebook.do({"kind": "list"}, "telegram")
    if low.startswith("/forget"):
        arg = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        return notebook.do({"kind": "forget", "text": arg}, "telegram") if arg else "Forget what? /forget the bins"
    if low.startswith("/panel"):
        png = service.preview_png()
        return (png, f"On the panel: {service.mode_name}") if png else "Nothing to show yet."
    if low.startswith("/photos"):
        last = photos.latest()
        if not last:
            return "No photos yet — the Camera tab's button takes one, or send /photo sepia (or bw, sketch…)."
        _, count = photos.listing(1)
        with open(photos.path_of(last["name"]), "rb") as fh:
            return (fh.read(), f"Your latest photo ({count} kept on the Pi)")
    if low.startswith("/photo"):
        arg = text.split(None, 1)[1].strip() if " " in text.strip() else ""
        if arg:
            done = _photo_command(f"take a {arg} photo", "telegram")
            return done if done is not None else \
                "Which filter? " + ", ".join(f["label"] for f in photos.FILTERS[1:]) + " — /photo sepia, say."
        images, why = mascot._look(conf)
        if not images:
            return f"No picture — {why}."
        import base64
        return (base64.b64decode(images), "From the camera just now")
    if low.startswith("/say"):
        words = text.split(None, 1)[1].strip() if " " in text else ""
        if not words:
            return "Say what?"
        okay, why = audio.say(words, conf.get("audio", {}))
        return "Said it." if okay else f"Couldn't: {why}"
    if low.startswith("/message"):
        words = text.split(None, 1)[1].strip() if " " in text else ""
        if not words:
            return "Message what?"
        service.apply_settings({"message": {"source": "message", "text": words}})
        service.set_mode("message")
        return "It's on the screen."
    if low.startswith("/status"):
        bits = [f"Screen: {service.mode_name}"]
        line = presence.summary()
        if line:
            bits.append(line)
        try:
            from pie_ink.modes import system as sysmode
            L = sysmode._last
            if L.get("temp") is not None:
                bits.append(f"CPU {L['temp']:.0f}°C, {L['cpu']:.0f}% busy")
        except Exception:
            pass
        w = conf.get("weather", {})
        if w.get("lat") is not None:
            wx = weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"), cached_only=True)
            if wx:
                bits.append(f"Weather {round(wx['temp'])}°{wx['units']}, {weather.describe(wx['code'])[0].lower()}")
        if home.HOME.enabled() and home.HOME.ready() and home.HOME.conf.get("tv"):
            tv = home.HOME.state_of(home.HOME.conf["tv"])
            bits.append(f"TV: {home._state_word(tv)}" if tv else f"TV: can't ask ({home.HOME.error})")
        return "\n".join(bits)
    done = _home_command(text, "telegram")
    if done is not None:
        return done
    # anything else is a question for the bot
    llm_conf = conf.get("llm", {})
    if not llm_conf.get("enabled") or not (llm_conf.get("model") or "").strip():
        return "The bot isn't set up on the Pi yet, but the commands still work — /help."
    prior = chat.history()
    chat.add("you", text, "telegram")
    reply = mascot.reply(conf, text, day=_day(), history=prior)
    if reply:
        chat.add("bot", reply, "telegram")
        llm.set_speech(reply, "chat")
        service.poke()
    return reply or "…"


@app.get("/api/telegram")
def api_telegram():
    return ok(**telegram.status(_tg_conf()), notify={k: _tg_conf().get(k) for k in
                                                     ("notify_room", "notify_friends", "notify_remarks")})


@app.post("/api/telegram/test")
def api_telegram_test():
    okay, why = telegram.send_text(_tg_conf(), "Hello from your PiE-ink.")
    return ok(message=why) if okay else fail(why)


@app.post("/api/telegram/unpair")
def api_telegram_unpair():
    service.apply_settings({"telegram": {"chat_id": "", "chat_name": ""}})
    return ok(**telegram.status(_tg_conf()))


# -- Home Assistant -------------------------------------------------------------------

@app.get("/api/home")
def api_home():
    if home.HOME.enabled() and home.HOME.ready() and not home.HOME.info:
        home.HOME.check()                        # first look: the version, and names for the pickers
    return ok(**home.HOME.status())


@app.post("/api/home/check")
def api_home_check():
    """Reach Home Assistant with the address and token as typed (not yet
    saved), and list its TVs and remotes."""
    body = request.get_json(silent=True) or {}
    conf = dict(service.config.get("home", {}))
    for k in ("url", "token"):
        if body.get(k) is not None:
            conf[k] = body[k]
    return ok(**home.HOME.check(conf))


@app.post("/api/home/do")
def api_home_do():
    """Words, as if spoken: "channel 5452"."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return fail("say what?")
    if not home.HOME.enabled():
        return fail("switch Home Assistant on and save first")
    reply = _home_command(text, "page")
    if reply is None:
        return fail("that isn't a home command I know — try \"turn on the tv\" or \"channel 5\"")
    return ok(reply=reply, last=home.HOME.last)


# -- the other PiE-inks -------------------------------------------------------------

def _image_from(data_url):
    import base64
    import io as _io
    from PIL import Image
    data = (data_url or "").split(",", 1)[-1]
    return Image.open(_io.BytesIO(base64.b64decode(data)))


def _arrived(msg):
    """A friend's Pi sent something — or one of your other rooms did (another PiE-ink of yours:
    the same bot, on another screen). Show it, say it, and let the bot answer if it was for the bot."""
    conf = service.config
    sender = msg.get("from") or "a friend"
    kind = msg.get("kind")
    text = (msg.get("text") or "").strip()
    social_conf = conf.get("social", {})
    room = household.HOUSE.room(sender)
    whence = f"from {room['label']}" if room else f"from {sender}"

    if kind == "postcard" and msg.get("image"):
        try:
            postcard.keep(_image_from(msg["image"]), room["label"] if room else sender, text)
        except Exception as e:
            log.warning("bad postcard from %s: %s", sender, e)
            return
        if social_conf.get("show_postcards", True):
            service.set_mode("postcard")
        if social_conf.get("speak_messages", True):
            audio.say(f"A postcard {whence}." + (f" It says: {text}" if text else ""),
                      conf.get("audio", {}))
        return

    if _tg_conf().get("notify_friends", True) and kind in ("text", "postcard"):
        _tg_notify(f"{whence.capitalize()}: {text}" if kind == "text" else f"A postcard {whence}" + (f" — {text}" if text else ""),
                   photo=_image_from(msg["image"]) if kind == "postcard" and msg.get("image") else None)
    if kind == "text":
        line = f"{whence.capitalize()}: {text}" if room else f"{sender} says: {text}"
        chat.add("bot", line, "friend")
        if social_conf.get("speak_messages", True):
            audio.say(line, conf.get("audio", {}))
        service.poke()
        return

    if kind == "ai" and room:                     # one bot in the house: it doesn't chat with itself
        log.info("%s (a room of this house) sent a line for 'the bot' — there is only the one; ignored", sender)
        return

    if kind == "ai":
        if msg.get("end"):
            _chat_screen(False)
        else:
            _chat_screen(True)
        _bot_reply_to(msg)


_chat_was = {"mode": None}


def _chat_screen(on):
    """Put the conversation on the panel while the bots talk, if you asked for
    that, and go back to whatever was there when they stop."""
    conf = service.config.get("social", {})
    if on:
        if not social.status()["ai_on"]:
            return                                   # a straggler after Stop doesn't reopen it
        if conf.get("show_on_screen") and service.mode_name != "groupchat":
            _chat_was["mode"] = service.mode_name
            service.set_mode("groupchat")
        elif service.mode_name == "groupchat":
            service.poke()
    else:
        was = _chat_was.pop("mode", None)
        if was and service.mode_name == "groupchat" and was in service.modes_available():
            service.set_mode(was)


def _bot_reply_to(msg):
    """Their bot (or their person) said something to our bot. Answer, and send it back."""
    conf = service.config
    sender = msg.get("from") or "a friend"
    thread = msg.get("thread") or msg.get("id")
    hops = int(msg.get("hops") or 0) + 1
    max_hops = int(conf.get("social", {}).get("ai_max_hops", 0) or 0)     # 0: until you press Stop
    allowed, why = social.ai_allowed(thread, hops, max_hops)
    social.note_hop(thread, hops)
    if not allowed:
        log.info("not answering %s's bot: %s", sender, why)
        return
    llm_conf = conf.get("llm", {})
    if not llm_conf.get("enabled") or not (llm_conf.get("model") or "").strip():
        return
    if msg.get("end"):
        log.info("%s's bot wound the conversation up", sender)
        return
    # the whole thread, so it can follow what has been said rather than react to one line
    transcript, topic = [], ""
    for m in social.history(200):
        if m.get("thread") != thread or m.get("kind") != "ai":
            continue
        if not topic and not m.get("from_ai"):
            topic = m.get("text", "")
        who = "You" if m.get("mine") else (f"{m.get('from')}'s bot" if m.get("from_ai") else m.get("from"))
        transcript.append((who, m.get("text", "")))
    if not topic:
        topic = msg.get("text", "")
    social.thinking(True)
    service.poke()                                   # the indicator goes up straight away
    try:
        text, done = mascot.converse(conf, sender, topic, transcript)
    except Exception as e:
        log.info("couldn't answer %s's bot: %s", sender, e)
        return
    finally:
        social.thinking(False)
    if not text:
        return
    chat.add("bot", f"(to {sender}) {text}", "friend")
    llm.set_speech(text, "chat")
    service.poke()
    if conf.get("social", {}).get("speak_ai"):
        # say it here, and only send it on once we've finished — so the two
        # Pis take turns instead of talking over each other
        audio.say(text, conf.get("audio", {}))
        audio.wait_quiet(90)
    social.send(conf, "ai", text, to=sender, thread=thread, hops=hops, from_ai=True, end=done)


@app.post("/api/inbox")
def api_inbox():
    msg = request.get_json(silent=True) or {}
    if not msg.get("kind"):
        return fail("what kind of message is that?")
    result = social.receive(msg)
    if msg.get("kind") == "stop":
        _chat_screen(False)
        service.poke()
    return ok(**result)


@app.get("/api/social")
def api_social():
    """Everyone a message can go to — your other rooms (the same bot there) and friends' Pis added by address."""
    return ok(history=social.history(), friends=social.everyone(service.config),
              rooms=[r["host"] for r in household.HOUSE.rooms()], **social.status())


@app.post("/api/social/send")
def api_social_send():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind") or "text"
    text = (body.get("text") or "").strip()
    to = body.get("to") or "all"
    image = body.get("image")
    if kind == "postcard" and not image:
        return fail("no drawing came with that")
    if kind != "postcard" and not text:
        return fail("say something first")
    if kind == "ai":
        social.start_ai()                             # a fresh conversation may run
        _chat_screen(True)
    thread = f"{social.my_name()}-{int(time.time())}" if kind == "ai" else None
    delivered, problems = social.send(service.config, kind, text, to=to, image=image,
                                      thread=thread, from_ai=body.get("from_ai", False))
    if delivered == 0:
        return fail("; ".join(problems) or "nobody to send it to")
    return ok(delivered=delivered, problems=problems, history=social.history())


def _room_hello(room, back):
    """Another PiE-ink of yours showed up on the network (or came back after a while away): the
    house just got bigger. The one bot is pleased — a line on its screen with a starry-eyed face,
    out loud, in the events list, and in its own words when it has a model."""
    conf = service.config
    label = room.get("label") or room.get("host")
    text = (f"{label[0].upper()}{label[1:]} is back — the house is whole again." if back
            else f"The house just got bigger: I'm in {label} now, too!")
    entry = sight.log_event(text, kind="room", level="info", source="household")
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        pass
    llm_conf = conf.get("llm", {})
    chat.add("bot", text, "remark")
    llm.set_speech(text, "room")
    if llm_conf.get("enabled") and (llm_conf.get("model") or "").strip() and not llm.busy():
        before = llm.latest()["ts"]
        if mascot.ask(conf, day=_day(), topic="room_back" if back else "room", history=chat.history(6), room=label):
            threading.Thread(target=_note_remark, args=(before,), daemon=True).start()
    elif conf.get("audio", {}).get("speak_bot", True):
        audio.say(text, conf.get("audio", {}))
    service.poke()


household.HOUSE.on_room = _room_hello


# -- something plugged into a USB port, or pulled out: the bot's face changes ---------------------

_USB_ASKED = [0.0]             # when the model was last asked about a plug: once a minute at most
FACE_SCREENS = ("me", "message", "crypto")       # screens with the bot's face on them (the rest get a toast, in colour)


def _usb_changed(added, gone, text, kind):
    """Something came or went on the USB bus (pie_ink/plugs.py): a line on the bot's screen with a face to
    match — wide-eyed, starry for a drive, sad when it's pulled out — the events list (with the port, for
    a hub), out loud if the bot speaks; a drive gets looked through, anything else a word from the model."""
    conf = service.config
    ports = sorted({usbwatch.where(d["bus"]) for d in added + gone} - {""})
    entry = sight.log_event(text + (f" ({', '.join(ports)})" if ports else ""), kind="usb", level="info", source="usb")
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        pass
    llm.set_speech(text, kind)
    _usb_toast(text)
    service.poke()
    _usb_later(plugs.FRESH + 0.5)
    if gone and music.PLAYER.tracks and any(os.path.isabs(t["path"]) and not os.path.exists(t["path"]) for t in music.PLAYER.tracks):
        music.PLAYER.stop()                                     # its songs went with the drive
        music.PLAYER.load(music.library(conf.get("music", {})), {**conf.get("music", {}), **conf.get("audio", {})})
    inside = [d for d in added if plugs.kind_of(d) in ("drive", "reader")]
    if inside:
        threading.Thread(target=_usb_look_inside, args=(inside,), daemon=True).start()
        return
    llm_conf = conf.get("llm", {})
    if added and llm_conf.get("enabled") and (llm_conf.get("model") or "").strip() and not llm.busy() \
            and time.time() - _USB_ASKED[0] > 60:
        _USB_ASKED[0] = time.time()
        before = llm.latest()["ts"]
        if mascot.ask(conf, kind="usb", day=_day(), topic="usb", history=chat.history(6), what=plugs.phrase(added)):
            threading.Thread(target=_note_remark, args=(before,), daemon=True).start()


def _usb_look_inside(devs, wait=None):
    """A USB drive just went in: once it's mounted, what's on it, as the bot's next line ("On the USB drive
    SANDISK: 214 photos and 12 songs — ask me for any of it."); or that nothing mounted it."""
    wait = plugs.DRIVE_WAIT if wait is None else wait
    buses = [d["bus"] for d in devs]
    deadline = time.time() + wait
    found = []
    while True:
        found = [m for m in drives.mounted() if any(m["bus"] == b or m["bus"].startswith(b + ".") for b in buses)]
        if found or time.time() >= deadline:
            break
        time.sleep(1)
    if found:
        time.sleep(0.5)                                         # (a second partition, a moment behind)
        found = [m for m in drives.mounted() if any(m["bus"] == b or m["bus"].startswith(b + ".") for b in buses)] or found
        shelf.on_drives(fresh=True, only=found)
        text, kind = shelf.drive_summary(only=found), "usb-look"
    elif any(drives.disks_on(b) for b in buses):
        text, kind = ("I can't look inside it — it isn't mounted. Run setup.sh again and drives get mounted as they "
                      "go in."), "usb-gone"
    else:
        return None                                             # (a card reader with no card in it)
    plugs.PLUGS.more(text, kind)
    sight.log_event(text, kind="usb", level="info", source="usb")
    llm.set_speech(text, kind)
    _usb_toast(text)
    service.poke()
    _usb_later(plugs.FRESH + 0.5)
    return text


def _usb_toast(text):
    """On a colour screen without the bot's face on it, the line as a toast for a moment."""
    try:
        if service.panel.colour and service.mode_name not in FACE_SCREENS:
            pad.toast(text, 6)
    except Exception:
        log.debug("usb toast", exc_info=True)


def _usb_later(seconds):
    """Redraw once the face has gone back to its own."""
    t = threading.Timer(seconds, service.poke)
    t.daemon = True
    t.start()


plugs.PLUGS.on_change = _usb_changed
cache.LENDER = borrow.lend                         # what a Pietato about has fetched, before fetching it again


# -- hand gestures: the TV and the lights --------------------------------------------------------------

def _gesture_said(text, what):
    """What a gesture did, on the bot's screen ("The lights off.") — not out loud: the TV's on."""
    llm.set_speech(text, "gesture")
    service.poke()


gestures.GESTURES.remote.on_said = _gesture_said


@app.get("/api/gestures")
def api_gestures():
    """How the hand gestures are doing; with ?lights=1, the lights Home Assistant has, for the picker
    (and which of them are the TV's room's, the ones used when none are ticked)."""
    out = gestures.GESTURES.report()
    out["why_not"] = gestures.why_not() if out["enabled"] or request.args.get("lights") else ""
    if request.args.get("lights") and home.HOME.enabled() and home.HOME.ready():
        lights = [{"id": s.get("entity_id"), "name": (s.get("attributes") or {}).get("friendly_name") or s.get("entity_id"),
                   "on": s.get("state") == "on"}
                  for s in home.HOME.states() if str(s.get("entity_id", "")).startswith("light.")]
        out["lights"] = sorted(lights, key=lambda x: str(x["name"]).lower())
        room = gestures.Remote(home.HOME).group() if home.HOME._tv() else []
        out["room"] = [x for x in room if any(x == l["id"] for l in lights)] if len(room) < len(lights) else []
    return ok(**out)


@app.get("/api/gestures/view")
def api_gestures_view():
    """The camera's picture as the gestures see it, the hands drawn on it (while the page asks for it)."""
    jpg = gestures.GESTURES.want_view()
    if not jpg:
        return ("", 204)
    return Response(jpg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/social/ai/<action>")
def api_social_ai(action):
    if action == "stop":
        result = social.stop_ai(service.config)
        _chat_screen(False)
        service.poke()
        return ok(**result)
    if action == "start":
        return ok(**social.start_ai())
    return fail("unknown action")


@app.post("/api/social/show")
def api_social_show():
    service.set_mode("groupchat")
    return ok(mode="groupchat")


@app.delete("/api/social")
def api_social_clear():
    social.clear()
    return ok(history=[])


# -- watching the room -----------------------------------------------------------

def _saw(frame, level, moved=True):
    """The watcher handed a frame on: something moved, or it is time for a
    look regardless. Log what changed as events, then — when something
    moved and it has been quiet long enough — say what it looks like."""
    conf = service.config
    watch = conf.get("camera_watch", {})
    if moved:
        noticed = presence.mark()
        if noticed.get("arrived"):
            for line in presence.remarks():
                chat.add("bot", line, "remark")
        service.poke()

    # what is there, as events: "Person detected", "Package detected", "Door opened"
    obs, entries = None, []
    if watch.get("events", True):
        try:
            obs, entries = sight.look(frame, conf, mascot._owner(conf), view=ptz.PTZ.view_key())
        except Exception as e:
            log.info("couldn't look for events: %s", e)
        for entry in entries:
            _event_out(entry, frame, watch, conf)
        if entries:
            service.poke()

    # a remark about it, the way it always has been: not more often than you asked for
    if not moved or not watch.get("describe", True):
        return
    quiet = max(30, float(watch.get("quiet_minutes", 3) or 3) * 60)
    if watcher.WATCHER.is_busy() or time.time() - watcher.WATCHER.last_told < quiet:
        return
    llm_conf = conf.get("llm", {})
    if not llm_conf.get("enabled") or not (llm_conf.get("model") or "").strip():
        return
    watcher.WATCHER.last_told = time.time()
    text = (obs or {}).get("notes") if obs and obs.get("source") == "model" else ""
    if not text:
        try:
            text = mascot.describe(conf, mascot.as_base64(frame), "view of the room")
        except Exception as e:
            log.info("couldn't say what moved: %s", e)
            return
    if not text:
        return
    log.info("watcher: %s", text)
    llm.set_speech(text, "seen")           # the screen shows the frame alongside
    llm.set_picture(frame, "room")
    if _tg_conf().get("notify_room", True):
        _tg_notify(text, photo=frame)
    chat.add("bot", text, "remark")
    if watch.get("speak", True) and conf.get("audio", {}).get("speak_bot", True) and not buddy.BUDDY.running():
        audio.say(text, conf.get("audio", {}))          # with the little friend on, it does the talking
    service.poke()


def _event_out(entry, frame, watch, conf):
    """An event happened: your phone (the notable ones, or all, as you set),
    the speaker if you asked, and the agent's attention."""
    want = (watch.get("notify_events") or "notable").lower()
    notable = entry.get("level") in ("note", "alert")
    if want == "all" or (want == "notable" and notable):
        if _tg_conf().get("notify_room", True):
            _tg_notify(f"{time.strftime('%H:%M')} {entry['text']}", photo=frame if notable else None)
    if watch.get("speak_events") and notable and conf.get("audio", {}).get("speak_bot", True):
        audio.say(entry["text"], conf.get("audio", {}))
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        log.debug("agent didn't take the event", exc_info=True)


def start_watching():
    conf = service.config.get("camera_watch", {})
    if not conf.get("enabled"):
        watcher.WATCHER.stop()
        return False, "not watching"
    return watcher.WATCHER.start(conf, _saw,
                                 is_busy=lambda: audio.SPEAKER.speaking or llm.busy()
                                 or listen.EARS.status()["state"] in ("listening", "thinking"))


@app.get("/api/watch")
def api_watch():
    return ok(status=watcher.WATCHER.status(), on_screen=service.mode_name)


@app.post("/api/watch/<action>")
def api_watch_do(action):
    if action == "start":
        okay, message = start_watching()
        return ok(message=message, status=watcher.WATCHER.status()) if okay else fail(message)
    if action == "stop":
        watcher.WATCHER.stop()
        return ok(status=watcher.WATCHER.status())
    return fail("unknown action")


# -- listening for the wake phrase -----------------------------------------------

def _heard(text):
    """Someone said the wake phrase and then this. Answer it."""
    attention.touch()
    conf = service.config
    was_playing = music.PLAYER.state == "playing"
    if was_playing and conf.get("listen", {}).get("pause_music", True):
        music.PLAYER.toggle()
    llm.set_speech("…", "listening")
    service.poke()
    prior = chat.history()                    # what came before, so it follows the thread
    chat.add("you", text, "spoken")
    reply = _home_command(text, "spoken")
    if reply is not None:                     # "turn on the tv": done, and said back if you want that
        llm.set_speech(reply, "chat")
        chat.add("bot", reply, "spoken")
        if not conf.get("home", {}).get("say_back", True):
            reply = ""
    else:
        try:
            reply = mascot.reply(conf, text, day=_day(), history=prior)
            if reply:
                llm.set_speech(reply, "chat")     # on the screen, whatever answered
                chat.add("bot", reply, "spoken")
        except Exception as e:
            log.warning("couldn't answer '%s': %s", text, e)
            llm.set_speech("", "listening", str(e)[:120])
            reply = ""
    if reply and conf.get("audio", {}).get("speak_bot", True):
        audio.say(reply, conf.get("audio", {}))
    service.poke()
    if was_playing and conf.get("listen", {}).get("pause_music", True):
        def music_back(started=time.time()):
            time.sleep(1.0)
            audio.wait_quiet(180)                       # the reply said, however long it took to start
            time.sleep(max(0.0, 4 - (time.time() - started)))
            music.PLAYER.toggle()
        threading.Thread(target=music_back, daemon=True, name="music-back").start()


def _home_command(text, how):
    """The words as a Home Assistant command, if they are one: what was
    done, or None. Never raises — a failure is something to say. Something
    about the other PiE-inks ("show me the camera from upstairs") is
    answered the same way, from here."""
    text = pitato.plain_name(text)                   # ("put the clock on the pietato": the name it had first, below)
    done = _mood_command(text, how)                  # "I'm bored", "it's dark in here" — and "yes" to what it suggested
    if done is not None:
        return done
    try:
        done = home.HOME.handle(text)
    except Exception as e:
        log.warning("home command '%s' (%s) failed: %s", text, how, e)
        return f"Couldn't — {str(e)[:100]}."
    if done is not None:
        return done
    done = _pitato_screen_command(text, how)         # "show the time on the pitato": its screen, before the other Pis
    if done is not None:
        return done
    done = _mesh_command(text, how)
    if done is not None:
        return done
    done = _camera_command(text, how)
    if done is not None:
        return done
    done = _photo_command(text, how)
    if done is not None:
        return done
    done = _reminder_command(text, how)
    if done is not None:
        return done
    done = _notebook_command(text, how)
    if done is not None:
        return done
    done = _shelf_command(text, how)
    if done is not None:
        return done
    if doing.asked(text):                            # "what are you doing?": the true answer, no model needed
        if pet.is_question(text):                    # "are you busy?": the head answers too, a moment behind
            threading.Thread(target=_head_too, args=(text,), daemon=True, name="pet-head-too").start()
        return doing.sentence(service.config)
    return None


# -- how you feel: "I'm bored" (a game on the Pietato, or the TV), "it's dark in here" (the lights) ------------

_offer = {}                                       # what the bot just suggested: {"kind": "game"|"tv", "game", "until", "had"}


def _tv_there():
    try:
        return home.HOME.enabled() and home.HOME.ready() and bool(home.HOME._tv())
    except Exception:
        return False


def _pick_game(games, had=()):
    import random
    fresh = [g for g in games if (g["ip"], g["path"]) not in had] or games
    return random.choice(fresh) if fresh else None


def _start_game(g):
    okay, why = shelf.show_on_pitato(g)
    host = g.get("host") or "the Pietato"
    return f"Starting {g['title']} on {host} — have fun!" if okay else f"{host} wouldn't start it — {why}."


def _suggest_game(games, tv):
    g = _pick_game(games, _offer.get("had", set()))
    had = set(_offer.get("had", set())) | {(g["ip"], g["path"])}
    _offer.clear()
    _offer.update(kind="game", game=g, until=time.time() + moods.OFFER_FOR, had=had, tv=tv)
    system = g["path"].split("/")[0] if "/" in g["path"] else ""
    where = g.get("host") or "the Pietato"
    return (f"How about {g['title']}" + (f" ({system})" if system and len(system) <= 12 else "") + f" on {where}? "
            "Say yes and I'll start it, or 'another' for a different one" + (" — or I can put the TV on." if tv else "."))


def _lights_on():
    """"It's dark in here": this room's lights on (or up to full, when they're on already)."""
    conf = service.config
    lights = [(name, eid) for name, eid, domain in home.HOME.named() if domain == "light"]
    picked, ask = moods.room_lights(lights, (conf.get("me", {}).get("location") or "").strip())
    if not picked:
        return f"I'd put the lights on, but {ask}."
    ids = [eid for _, eid in picked]
    states = {x.get("entity_id"): x for x in home.HOME.states(force=True)}
    off = [eid for eid in ids if (states.get(eid) or {}).get("state") != "on"]
    names = ", ".join(n for n, _ in picked)
    if off:
        okay, why = home.HOME.call("light", "turn_on", {"entity_id": off})
        lit = ", ".join(n for n, eid in picked if eid in off)
        return f"Let there be light — {lit} on." if okay else f"Couldn't put the lights on — {why}."
    okay, why = home.HOME.call("light", "turn_on", {"entity_id": ids, "brightness_pct": 100})
    return f"{names} {'is' if len(picked) == 1 else 'are'} on already — turned up full." if okay else f"Couldn't — {why}."


def _mood_command(text, how):
    """What you said about how you feel, acted on — or the answer to what the bot just suggested.
    None when it wasn't either (the model answers as usual)."""
    try:
        if _offer and time.time() < _offer.get("until", 0):
            reply = moods.answer(text)
            if reply:
                kind = _offer.get("kind")
                if reply == "no":
                    _offer.clear()
                    return "Fair enough. I'm here if you change your mind."
                if reply == "tv" and _offer.get("tv"):
                    _offer.clear()
                    return home.HOME.act(("tv_power", "on"), label="the TV, for something to do")
                if reply == "another" and kind == "game":
                    games = shelf.games()
                    if games:
                        return _suggest_game(games, _offer.get("tv"))
                if reply == "yes":
                    g = _offer.get("game")
                    _offer.clear()
                    if kind == "game" and g:
                        return _start_game(g)
                    if kind == "tv":
                        return home.HOME.act(("tv_power", "on"), label="the TV, for something to do")
        elif _offer:
            _offer.clear()
        intent = moods.parse(text)
        if not intent:
            return None
        if intent["kind"] == "dark":
            if not (home.HOME.enabled() and home.HOME.ready()):
                return None                              # no lights to reach: the model can say something kind
            return _lights_on()
        games = shelf.games()
        if intent["kind"] == "random_game":
            who = intent.get("who")
            if who and who not in ("pitato", "pitatos"):
                games = [g for g in games if who in ((g.get("host") or "").lower(), g.get("ip"))]
            if not games:
                return "There are no games in a Pietato's Games folder that I can see."
            _offer.clear()
            return _start_game(_pick_game(games))
        tv = _tv_there()                                  # bored
        if games:
            return "Bored? " + _suggest_game(games, tv)
        if tv:
            _offer.clear()
            _offer.update(kind="tv", until=time.time() + moods.OFFER_FOR, tv=True)
            return "Bored? I can put the TV on — say yes."
        return None
    except Exception as e:
        log.warning("mood '%s' (%s): %s", text, how, e)
        return None


def _head_too(text):
    try:
        conf = service.config
        pet.answer(conf, text, mascot.context(conf, day=_day()), persona=mascot.system_prompt(conf))
    except Exception:
        log.debug("pet: no head answer", exc_info=True)


# -- the other PiE-inks, as a mesh -----------------------------------------------------------

def _mesh_command(text, how):
    """"Which pi has a camera", "show me the camera from upstairs", "send this
    drawing to the workshop pi": done, or None if it wasn't that. From
    Telegram a picture comes back as (jpeg, caption)."""
    try:
        intent = mesh.parse(text)
        if not intent:
            return None
        peers = _everyone_for_mesh()
        wanted = shelf.parse(text) if intent.get("who") else None
        if wanted and (wanted["kind"] == "pitato" or mesh.resolve(peers, intent["who"]) is None):
            return None          # "put the film on the pitato", "the picture of whiskers": a thing on the shelves, not a Pi
        return _mesh_do(intent, peers, how)
    except Exception as e:
        log.warning("mesh command '%s' (%s) failed: %s", text, how, e)
        return f"Couldn't — {str(e)[:100]}."


def _drawing_now():
    """The drawing as it stands: the colour painting when the main screen is colour (or the LCD's is the one
    being drawn), else the e-ink drawing. None if there's nothing drawn yet."""
    from PIL import Image
    from pie_ink.modes.simple import ImageMode
    if service.panel.colour:
        img = PAINT.image()
        if img is not None:
            return img
    img = ImageMode._current
    if img is None:
        try:
            img = Image.open(ImageMode.PATH)
            img.load()
        except OSError:
            img = None
    return img if img is not None else PAINT.image()


def _screen_named(word):
    """One of the panel's screens by a word for it ("weather", "finance", "the book"), or None."""
    from pie_ink.modes import MODES
    w = " ".join((word or "").lower().replace("screen", " ").split())
    w = re.sub(r"^(?:the|my)\s+", "", w)
    name = SCREEN_WORDS.get(w, w)
    if name in MODES and name not in ("off",):
        return name
    for mode in MODES.values():                       # by its label: "Finance", "System"...
        if mode.label.lower() == w and mode.name != "off":
            return mode.name
    return None


def _pitato_screen_do(kind, to=None, text="", image=None, name=None):
    """(done, what to say): something put on a Pietato's screen."""
    conf = service.config
    if kind == "stop":
        ts, _why = pitato_screen.targets(to)
        stopped = any([pitato_screen.stop_clock(x["ip"]) for x in ts]) if ts else pitato_screen.stop_clock(None)
        return True, "The Pietato's screen is no longer kept up to date." if stopped else "Nothing was being kept on the Pietato."
    if kind == "screen":
        from pie_ink.modes import MODES
        screen = _screen_named(name or "")
        if screen is None:
            return False, f"There's no '{name}' screen."
        if screen == "image":
            return _pitato_screen_do("drawing", to)
        if screen == "message":
            return _pitato_screen_do("message", to)
        return pitato_screen.send_screen(screen, conf, to, label=f"The {MODES[screen].label.lower()} screen")
    if kind == "clock":
        return pitato_screen.send_clock(conf.get("clock", {}), to)
    if kind == "message":
        if not text:
            text = (conf.get("message", {}).get("text") or "").strip()
        return pitato_screen.send_message(text, to, conf.get("message", {}).get("font") or "Font.ttc")
    if kind == "drawing":
        img = image if image is not None else _drawing_now()
        if img is None:
            return False, "There's no drawing yet — draw something in the Draw tab first."
        return pitato_screen.send_drawing(img, to)
    return False, "That isn't something I can put on the Pietato."


def _pitato_screen_command(text, how):
    try:
        intent = pitato_screen.parse(text)
        if not intent:
            return None
        if intent["kind"] == "screen":
            if not _screen_named(intent.get("what")):
                return None              # "show the holiday video on the pitato": a thing on its shelves, not a screen
            okay, said = _pitato_screen_do("screen", intent["to"], name=intent["what"])
            return said
        okay, said = _pitato_screen_do(intent["kind"], intent["to"], intent.get("text", ""))
        return said
    except Exception as e:
        log.warning("pitato screen '%s' (%s) failed: %s", text, how, e)
        return f"Couldn't — {str(e)[:100]}."


@app.get("/api/pitato/screens")
def api_pitato_screens():
    """The Pietatos whose screen can be used, and whether the time is being kept on each."""
    return ok(screens=[{**s, "clock": pitato_screen.clock_on(s["ip"]), "showing": pitato_screen.keeping(s["ip"])}
                       for s in pitato_screen.screens()])


@app.post("/api/pitato/show")
def api_pitato_show():
    """{kind: drawing | message | clock | screen | stop, to?, text?, image?, name? (the screen, for kind screen)}:
    on a Pietato's screen, full screen."""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind") or ""
    image = None
    if kind == "drawing" and body.get("image"):
        try:
            image = _image_from(body["image"])
            image.load()
        except Exception as e:
            return fail(f"that is not a picture ({str(e)[:60]})")
    okay, said = _pitato_screen_do(kind, body.get("to"), (body.get("text") or "").strip(), image, body.get("name"))
    return ok(said=said) if okay else fail(said)


def _everyone_for_mesh():
    """The other PiE-inks, and a Pietato's cameras (they answer the same calls)."""
    return friends.peers() + pitato.PITATO.peers()


def _mesh_do(intent, peers, how):
    import base64
    import io as _io
    from PIL import Image
    from pie_ink.modes import MODES
    from pie_ink.modes.simple import ImageMode
    kind = intent["kind"]
    roll = "\n".join(mesh.describe(p) for p in peers)
    if kind == "list":
        lines = pitato.PITATO.describe()                       # the handheld Pi, when it's about
        if not peers and not lines:
            return "No other PiE-ink on the network right now — this is the whole house."
        return ("The other rooms of the house (PiE-inks of yours — I'm the same bot on each):\n" + roll if peers
                else "No other PiE-ink on the network right now — this is the whole house.") \
            + ("\n" + "\n".join(lines) if lines else "")
    if kind == "find":
        cap = intent["cap"]
        label = mesh.CAP_LABELS.get(cap, cap)
        found = mesh.find(peers, cap)
        mine = mesh.my_caps(service.config)
        have_it = bool(mine.get(cap)) if cap not in ("screen", "battery") else (cap == "screen" or mine.get("battery") is not None)
        names = [f"{p['host']}" + (f" ({p['caps'].get('location')})" if (p.get("caps") or {}).get("location") else "") for p in found]
        if have_it:
            names.insert(0, "this one")
        if not names:
            return f"No Pi with a {label} that I can see." + (f" There are {len(peers)}: {', '.join(p['host'] for p in peers)}." if peers else "")
        return f"With a {label}: {', '.join(names)}."
    who = intent.get("who", "")
    peer = mesh.resolve(peers, who)
    if peer is None:
        return f"I don't know a Pi called '{who}'." + (f" The ones I can see:\n{roll}" if peers else " No other PiE-ink is on the network right now.")
    where = (peer.get("caps") or {}).get("location") or ""
    name = f"{peer['host']}" + (f" ({where})" if where else "")
    if peer.get("kind") == "pitato" and kind not in ("camera", "where"):
        return f"{name} is a Pietato camera — I can look through it, nothing more."
    if kind == "camera":
        data, why = mesh.fetch_frame(peer)
        if not data:
            return f"{name} can't show its camera — {why}."
        img = Image.open(_io.BytesIO(data)).convert("RGB")
        postcard.keep(img, peer["host"], "its camera")
        llm.set_picture(img, f"{peer['host']}'s camera")
        service.set_mode("postcard")
        if how == "telegram":
            return (data, f"{name}'s camera just now — it's on the panel too.")
        return f"{name}'s camera is on the panel now."
    if kind == "drawing":
        current = ImageMode._current
        if current is None:
            try:
                current = Image.open(ImageMode.PATH)
            except OSError:
                return "There's no drawing to send yet — draw something in the Draw tab first."
        buf = _io.BytesIO()
        current.convert("L").save(buf, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        delivered, problems = social.send(service.config, "postcard", "", to=peer["host"], image=data_url)
        return f"Sent the drawing to {name}." if delivered else f"Couldn't send it — {'; '.join(problems) or 'no answer'}."
    if kind == "say":
        okay, why = mesh.say_on(peer, intent["text"])
        return f"Said it on {name}." if okay else f"{name} couldn't say it — {why or 'no answer'}."
    if kind == "screen":
        word = intent["screen"].strip()
        screen = SCREEN_WORDS.get(word, word)
        if screen not in MODES and screen != "off":
            return f"{name} has no '{word}' screen. Screens: " + ", ".join(sorted(m for m in MODES if m != "postcard")) + ", off."
        okay, why = mesh.show_on(peer, screen)
        return f"Showing {screen} on {name}." if okay else f"{name} wouldn't — {why or 'no answer'}."
    if kind == "where":
        return f"{name} {mesh.where_words(peer)}."
    return None


# -- the miners: when the hashrate drops under the line ------------------------------------------

def _mining_alert(kind, text, data):
    """A pool's hashrate fell under your line (or came back, or a miner went
    quiet): your phone, the events list, the agent — and the bot has a word
    about it, in its own words when it has a model."""
    conf = service.config
    pconf = conf.get(data.get("kind", "duco"), {})
    level = "info" if kind == "back" else "note"
    entry = sight.log_event(text, kind="mining", level=level, source=data.get("kind", "mining"))
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        pass
    if pconf.get("notify", True):
        _tg_notify(f"Mining: {text}")
    if not pconf.get("comment", True):
        service.poke()
        return
    llm_conf = conf.get("llm", {})
    chat.add("bot", text, "remark")
    llm.set_speech(text, "mining")
    if llm_conf.get("enabled") and (llm_conf.get("model") or "").strip() and not llm.busy() and kind in ("drop", "back"):
        before = llm.latest()["ts"]
        if mascot.ask(conf, day=_day(), topic="mining", history=chat.history(6)):
            threading.Thread(target=_note_remark, args=(before,), daemon=True).start()   # its own words follow
    elif conf.get("audio", {}).get("speak_bot", True):
        audio.say(text, conf.get("audio", {}))
    service.poke()


def _note_remark(before, wait=120):
    """Write the bot's next line into the conversation once it lands."""
    for _ in range(wait):
        time.sleep(1)
        said = llm.latest()
        if said["ts"] != before and said["text"]:
            chat.add("bot", said["text"], "remark")
            service.poke()
            return


for _pool in mining.MINERS.values():
    _pool.on_alert = _mining_alert


# -- a Pietato: the handheld Pi shows up on the network -------------------------------------------

def _pitato_hello(p, back):
    """A Pietato just showed up (or came back after a while away): PiE-ink is thrilled — a
    line on the bot's screen with a starry-eyed face, out loud, the events list, the agent,
    and the bot's own words when it has a model."""
    conf = service.config
    name = p.get("name") or p["ip"]
    if p.get("here"):                                           # the two of them on one Pi
        text = ("My Pietato housemate is back, right here on this Pi!" if back
                else "A Pietato! It's right here with me, on this very Pi — hello, housemate!")
    else:
        text = (f"{name} is back! My Pietato friend is on the network again." if back
                else f"A Pietato! {name} just showed up on the network — hello, friend!")
    entry = sight.log_event(text, kind="pitato", level="info", source="pitato")
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        pass
    llm_conf = conf.get("llm", {})
    chat.add("bot", text, "remark")
    llm.set_speech(text, "pitato")
    if llm_conf.get("enabled") and (llm_conf.get("model") or "").strip() and not llm.busy():
        before = llm.latest()["ts"]
        if mascot.ask(conf, day=_day(), topic="pitato_back" if back else "pitato", history=chat.history(6)):
            threading.Thread(target=_note_remark, args=(before,), daemon=True).start()   # its own words follow
    elif conf.get("audio", {}).get("speak_bot", True):
        audio.say(text, conf.get("audio", {}))
    service.poke()


pitato.PITATO.on_arrive = _pitato_hello


# -- your crypto: past the line, a crash, a rally -------------------------------------------------

def _holdings_alert(kind, text, data, only_bot=False):
    """The total passed the line you set (rich!), slipped back under it, moved a
    lot in a day, or has sunk well below its peak: your phone, the events list,
    the agent — and the bot has a word about it, in its own words with a model.
    only_bot: a try-out from the page — the bot alone, nothing sent anywhere."""
    conf = service.config
    hconf = conf.get("holdings", {})
    if not only_bot:
        level = "alert" if kind in ("crash", "slump") else "note"
        entry = sight.log_event(text, kind="holdings", level=level, source="crypto")
        try:
            agent.AGENT.noticed(entry)
        except Exception:
            pass
        if hconf.get("notify", True):
            _tg_notify(f"Finance: {text}")
        if not hconf.get("comment", True):
            service.poke()
            return
    llm_conf = conf.get("llm", {})
    chat.add("bot", text, "remark")
    llm.set_speech(text, "holdings")
    if llm_conf.get("enabled") and (llm_conf.get("model") or "").strip() and not llm.busy():
        before = llm.latest()["ts"]
        if mascot.ask(conf, day=_day(), topic=f"holdings_{kind}", history=chat.history(6)):
            threading.Thread(target=_note_remark, args=(before,), daemon=True).start()   # its own words follow
    elif conf.get("audio", {}).get("speak_bot", True):
        audio.say(text, conf.get("audio", {}))
    service.poke()


holdings.HOLDINGS.on_alert = _holdings_alert


# -- the camera that turns --------------------------------------------------------------------

ptz.PTZ.configure(service.config.get("ptz", {}))
camera.CAMERA.configure(service.config.get("camera", {}))     # a camera with two lenses: which view, swapped


def _follow_saved(on):
    """Follow me flipped from here (the switch, the bot, or you taking the stick): remembered."""
    config = service.apply_settings({"ptz": {"follow": bool(on)}})
    ptz.PTZ.configure(config.get("ptz", {}))


ptz.PTZ.on_follow_change = _follow_saved


def _can_see(conf):
    lc = conf.get("llm", {})
    return bool(lc.get("enabled") and lc.get("see", True)
                and ((lc.get("model") or "").strip() or (lc.get("vision_model") or "").strip()))


def _counts(frame):
    """People and cats, from OpenCV alone — for when there is no model to look."""
    try:
        o = sight._look_basic(frame)
    except Exception:
        return "can't tell without a model that sees"
    bits = (["1 person" if o["people"] == 1 else f"{o['people']} people"] if o["people"] else []) + (o.get("animals") or [])
    return ", ".join(bits) or "nobody"


def _jpeg(picture):
    import io as _io
    buf = _io.BytesIO()
    picture.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _camera_look(intent, back=False):
    """Turn the camera as asked, look, and say what is there: (text,
    picture). With back, it turns back to where it was afterwards (and a
    camera that was following someone follows again); without, it stays
    turned — and stops following, since you pointed it somewhere."""
    conf = service.config
    if intent["kind"] == "around":
        frames = ptz.PTZ.sweep()                     # turns back by itself
        if not frames:
            return "The camera turned, but no picture came back.", None
        picture = ptz.composite(frames)
        if _can_see(conf):
            text = mascot.survey(conf, mascot.as_base64(picture, longest=1200), [label.lower() for label, _ in frames],
                                 intent.get("question"))
        else:
            text = "; ".join(f"{label.lower()}: {_counts(f)}" for label, f in frames)
            text = text[:1].upper() + text[1:] + "."
    else:
        if not back:
            ptz.PTZ.take_over()
        with (ptz.PTZ.paused_follow() if back else contextlib.nullcontext()):
            start = ptz.PTZ.position()
            where = intent.get("where") or "ahead"
            try:
                if intent["kind"] == "center":
                    ptz.PTZ.home() if (service.config.get("ptz", {}).get("home")) else ptz.PTZ.center()
                else:
                    ptz.PTZ.nudge(dpan=intent.get("dpan", 0.0), dtilt=intent.get("dtilt", 0.0))
                frame = ptz.PTZ.settle()
                if frame is None:
                    return "No picture from the camera.", None
                picture = frame
                if _can_see(conf):
                    text = mascot.describe_view(conf, mascot.as_base64(frame), where, intent.get("question"))
                else:
                    text = f"Looking {where}: {_counts(frame)}."
            finally:
                if back:
                    ptz.PTZ.set(pan=start["pan"], tilt=start["tilt"])
    llm.set_picture(picture, "room")                 # the screen shows what it looked at
    ptz.PTZ.last_look = {"ts": time.time(), "text": text, "where": intent.get("where") or intent["kind"]}
    return text or "…", picture


def _camera_command(text, how):
    """"Look left", "look around", "zoom in", "look straight ahead": done,
    or None when it isn't one. From Telegram a look comes back with its
    picture."""
    intent = ptz.parse(text)
    if not intent:
        return None
    if not ptz.PTZ.available():
        if intent["kind"] == "around":
            return None                              # a camera that can't turn: the bot answers from its one view
        return "There's no camera here that turns — this one only looks the one way."
    ptz.PTZ.user_moved()                                  # you asked: the little friend leaves the camera to you
    try:
        kind = intent["kind"]
        if kind == "follow":
            return _follow_command(intent["on"])
        if kind == "zoom":
            if not ptz.PTZ.position()["has"]["zoom"]:
                return "This camera can't zoom."
            pos = ptz.PTZ.set(zoom=intent["to"]) if "to" in intent else ptz.PTZ.nudge(dzoom=intent["dz"])
            closer = intent.get("to", intent.get("dz", 0)) > 0
            return f"Zoomed {'in' if closer else 'out'} — {round(pos['zoom'] * 100)}%."
        # pointing it somewhere stops the following (a look around doesn't: it follows again after)
        stopped = "Stopped following you. " if kind != "around" and ptz.PTZ.take_over() else ""
        if kind == "center":
            ptz.PTZ.center()
            return stopped + "Looking straight ahead."
        if kind == "home":
            ptz.PTZ.home()
            return stopped + ("Back to its home position." if service.config.get("ptz", {}).get("home") else
                              "Straight ahead — no home saved yet (Camera tab → Set home).")
        said, picture = _camera_look(intent)
        said = stopped + said
        if how == "telegram" and picture is not None:
            return (_jpeg(picture), said)
        return said
    except (OSError, ValueError) as e:
        log.info("camera command '%s': %s", text, e)
        return f"Couldn't turn the camera — {e}"


def _follow_command(on):
    """"Follow me" / "stop following me": the camera's own tracking, as a switch."""
    if not ptz.PTZ.can_follow():
        return "This camera can't follow anyone by itself — only turn when it's told."
    was = ptz.PTZ.follow_on
    try:
        got = ptz.PTZ.set_follow(on)
    except (OSError, ValueError) as e:
        log.info("follow %s: %s", "on" if on else "off", e)
        return f"Couldn't — the camera didn't answer ({getattr(e, 'strerror', None) or e})."
    if got is None or got != on:
        return "I asked, but the camera didn't say it would. Try again in a moment."
    if on:
        return "Already following you." if was else "Following you."
    return "I wasn't following you — staying put." if was is False else "Stopped following you."


def _look_now(where):
    """The page's Look around (or look left…): in the background, the answer to the conversation."""
    conf = service.config
    intent = ptz.parse(f"look {where}") or {"kind": "around"}
    try:
        said, _ = _camera_look(intent)
    except Exception as e:
        log.info("look %s: %s", where, e)
        ptz.PTZ.last_look = {"ts": time.time(), "text": f"Couldn't look: {str(e)[:100]}", "where": where, "error": True}
        return
    chat.add("bot", said, "looked")
    llm.set_speech(said, "seen")
    if conf.get("audio", {}).get("speak_bot", True):
        audio.say(said, conf.get("audio", {}))
    service.poke()


def _agent_look(where):
    """The agent wants a closer look: turn there, see, turn back."""
    w = " ".join((where or "").lower().split())
    intent = ptz.parse(f"look {w}") if w else None
    if intent is None or intent["kind"] not in ("around", "turn", "center"):
        return f"'{where}' isn't a way the camera can look (left, right, up, down, around, ahead)"
    said, _ = _camera_look(intent, back=True)
    chat.add("bot", said, "agent")
    return said


# -- photos -------------------------------------------------------------------------------------

PHOTO_WORDS = {"bw": "black-and-white", "comic": "comic-book", "pixel": "pixel-art", "trace": "edge-traced"}


def _photo_words(fid):
    """"a sepia picture", "an e-ink picture", "a picture"."""
    if fid in (None, "none"):
        return "a picture"
    word = PHOTO_WORDS.get(fid) or photos.BY_ID[fid]["label"].lower()
    return f"{'an' if word[:1] in 'aeiou' else 'a'} {word} picture"


def _photo_command(text, how):
    """"Take a picture", "take a black and white photo of me": taken, and kept
    with the Camera tab's photos. Spoken, it says "Say cheese!" first; from
    Telegram the picture comes back."""
    intent = photos.parse(text)
    if not intent:
        return None
    if not [c for c in camera.available() if c["id"] != "mock"]:
        return "There's no camera here to take it with."
    conf = service.config
    fid = intent["filter"]
    if how == "spoken" and conf.get("audio", {}).get("speak_bot", True):
        audio.say("Say cheese!", conf.get("audio", {}), wait=True)
        time.sleep(0.6)
    try:
        got = photos.take(fid, mirror=bool(conf.get("camera", {}).get("mirror")))
    except (OSError, ValueError, RuntimeError) as e:
        log.info("photo: %s", e)
        return f"Couldn't take it — {e}."
    what = _photo_words(fid)
    if how == "telegram":
        with open(photos.path_of(got["name"]), "rb") as fh:
            return (fh.read(), f"Took {what} — it's kept on the Pi too (Camera tab).")
    return f"Took {what} — it's with your photos on the Camera tab."


# -- the notebook and the reminders: what you tell it to keep, and when to tell you ---------------------------

def _notebook_command(text, how):
    """"Remember that…", "forget…", "what do you remember?": done, or None if it wasn't that."""
    intent = notebook.parse(text)
    if not intent:
        return None
    return notebook.do(intent, how)


def _reminder_command(text, how):
    """"Remind me in 20 minutes to…", "set a timer for 5 minutes", "what reminders do I have",
    "cancel the timer", "snooze": done, or None if it wasn't that."""
    intent = reminders.parse(text)
    if not intent:
        return None
    return reminders.REMINDERS.do(intent, how)


def _reminder_card(text, kind="Reminder"):
    """The reminder as a card for the panel: the kind up top, the words large underneath."""
    from PIL import Image, ImageDraw
    from pie_ink.text import font, wrap, draw_block, line_height
    info = service.panel.info()
    pw, ph = max(200, int(info.get("width", 250))), max(100, int(info.get("height", 122)))
    T = max(1.0, min(1.6, 1 + (min(pw / 250, ph / 122) - 1) * 0.5))
    w, h = pw * 3, (ph - round(14 * T) - 2) * 3                  # the room above the postcard's footer, exactly
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    head = font(2, round(13 * T) * 3)
    d.text((12, 6), kind.upper(), font=head, fill=0)
    top = line_height(head) + 12
    d.line([(0, top), (w, top)], fill=0, width=3)
    box = (6, top + 6, w - 6, h - 4)
    size = round(26 * T) * 3
    while size > 36:
        f = font(1, size)
        if len(wrap(d, text, f, box[2] - box[0] - 8)) * line_height(f) <= box[3] - box[1] - 8:
            break
        size -= 3
    draw_block(d, text, font(1, size), box, 0, align="left", valign="top")
    return img


def _reminder_due(r):
    """One has come round: the card on the panel for a couple of minutes, out loud, your phone,
    the events list, the conversation — and the bot's screen."""
    conf = service.config
    text = reminders.REMINDERS.due_words(r)
    kind = "Timer" if r["text"] == "timer" else "Wake up" if r["text"] == "wake up" else "Reminder"
    entry = sight.log_event(text, kind="reminder", level="note", source="reminder")
    try:
        agent.AGENT.noticed(entry)
    except Exception:
        pass
    chat.add("bot", text, "remark")
    llm.set_speech(text, "reminder")
    was = service.mode_name
    try:
        body = r["text"] if r["text"] not in ("timer", "wake up") else text
        postcard.keep(_reminder_card(body, kind), "reminder", text, label=kind)
        service.set_mode("postcard")
        if was and was != "postcard":
            def back():
                if service.mode_name == "postcard":
                    service.set_mode(was)
            threading.Timer(reminders.SHOW_FOR, back).start()
    except Exception:
        log.exception("the reminder's card")
    if conf.get("audio", {}).get("speak_bot", True):
        audio.say(text, conf.get("audio", {}))
    _tg_notify(f"⏰ {text}")
    service.poke()


reminders.REMINDERS.on_due = _reminder_due


@app.get("/api/reminders")
def api_reminders():
    rs = reminders.REMINDERS
    return ok(reminders=[{**r, "words": rs.describe(r)} for r in rs.pending()])


@app.post("/api/reminders")
def api_reminders_add():
    """{text}: in your own words — "in 20 minutes check the oven", "tomorrow at 9 bins out"."""
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return fail("say when, and what")
    intent = reminders.parse(text) or reminders.parse("remind me " + text)
    if not intent or intent["kind"] != "set":
        return fail("couldn't find a time in that — try 'in 20 minutes …' or 'at 5 pm …'")
    reply = reminders.REMINDERS.do(intent, "page")
    if intent.get("at") is None:
        return fail(reply)
    rs = reminders.REMINDERS
    return ok(reply=reply, reminders=[{**r, "words": rs.describe(r)} for r in rs.pending()])


@app.delete("/api/reminders/<rid>")
def api_reminders_remove(rid):
    rs = reminders.REMINDERS
    rs.remove(rid)
    return ok(reminders=[{**r, "words": rs.describe(r)} for r in rs.pending()])


@app.get("/api/notes")
def api_notes():
    return ok(notes=notebook.notes())


@app.post("/api/notes")
def api_notes_add():
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    note = notebook.add(text, "page") if text else None
    if not note:
        return fail("say what to remember")
    return ok(note=note, notes=notebook.notes())


@app.delete("/api/notes/<nid>")
def api_notes_remove(nid):
    notebook.remove(nid)
    return ok(notes=notebook.notes())


# -- the shelves: photos, books, music here, and the Pietato's pictures, music, videos, documents -----------

def _shelf_command(text, how):
    """"Show me the latest photo", "find the song called blue monday", "play X on the
    pitato", "what videos are there": done on whichever Pi has it, or None if it wasn't
    that. From Telegram a picture comes back as (jpeg, caption)."""
    try:
        intent = shelf.parse(text)
        if not intent:
            return None
        return _shelf_do(intent, how)
    except Exception as e:
        log.warning("shelf '%s' (%s) failed: %s", text, how, e, exc_info=True)
        return f"Couldn't — {str(e)[:100]}."


def _shelf_do(intent, how):
    import io as _io
    from PIL import Image
    from pie_ink.modes.simple import ImageMode
    conf = service.config
    kind, words = intent["kind"], intent["words"]

    def nowhere():                                           # worded once the shelves have been looked at
        return ("on either Pi" if shelf.pitatos() else "here") + \
            (" — the Pietato's page has a password, so its shelves are closed to me"
             if any((shelf._cache.get(p["ip"]) or {}).get("locked") for p in shelf.pitatos()) else "")
    if kind == "list":
        kinds = shelf._kinds_in(words.split())
        if intent.get("drive"):                              # "what's on the usb drive"
            line = shelf.drive_summary(kinds or None)
            if kinds:
                newest = sorted((i for i in shelf.on_drives() if i["kind"] in kinds), key=lambda i: -(i.get("when") or 0))[:4]
                if newest:
                    line += " Newest: " + ", ".join(i["title"] for i in newest) + "."
            return line
        found = shelf.find(" ".join(kinds) if kinds else "latest", kinds=kinds, limit=400, config=conf)
        if not found:
            return f"Nothing like that {nowhere()}."
        here_n = sum(1 for i in found if i["where"] == "here")
        there_n = len(found) - here_n
        newest = sorted(found, key=lambda i: -(i.get("when") or 0))[:5]
        what = "/".join(sorted(kinds)) if kinds else "thing"
        return (f"{len(found)} {what}{'s' if len(found) != 1 else ''}" + (f": {here_n} here, {there_n} on the Pietato" if there_n else " here")
                + ". Newest: " + "; ".join(shelf.describe(i) for i in newest) + ".")
    if kind == "find":
        found = shelf.find(words, config=conf)
        if not found:
            return f"I couldn't find anything like '{words}' {nowhere()}."
        lines = [shelf.describe(i) for i in found[:5]]
        return (f"Found {len(found)}: " if len(found) > 1 else "Found it: ") + "; ".join(lines) + \
            (". Say 'show me …' or 'play …' and I'll fetch it." if how != "telegram" else "")
    if kind == "pitato":
        if not shelf.pitatos():
            return "There's no Pietato about to put it on."
        found = shelf.find(words, config=conf)
        if not found:
            return f"I couldn't find anything like '{words}' {nowhere()}."
        it = next((i for i in found if i["where"] == "pitato"), found[0])
        if it["where"] == "pitato":
            okay, why = shelf.show_on_pitato(it)
            return f"{it['title']} is on the Pietato's screen." if okay else f"The Pietato wouldn't — {why}."
        if it["kind"] in ("photo", "drawing"):
            okay, why = shelf.send_to_pitato(it, shelf.fetch(it))
            return f"Sent {it['title']} over — it's on the Pietato's screen (in its Pictures, under pie-ink)." if okay \
                else f"Couldn't send it to the Pietato — {why}."
        return f"{shelf.describe(it)} — it's here, not on the Pietato, and only pictures go across."
    if kind == "play":
        found = shelf.find(words, kinds={"music", "video", "game"} if not shelf._kinds_in(words.split()) else None, config=conf)
        if not found and shelf._kinds_in(words.split()) == {"music"}:        # "play some music": whatever's here
            found = shelf.find("music", kinds={"music"}, limit=1, config=conf)
        if not found:
            return f"I couldn't find anything like '{words}' to play {nowhere()}."
        it = found[0]
        if it.get("folder") == "drive" and it["kind"] == "music":
            return _play_from_drive(it, conf)
        if it["where"] == "here" and it["kind"] == "music":
            mconf = {**conf.get("music", {}), **conf.get("audio", {})}
            tracks = music.library(conf.get("music", {}))
            index = next((k for k, t in enumerate(tracks) if t["path"] == it["path"]), None)
            if index is None:
                return "That song has gone from the music folder."
            music.PLAYER.load(tracks, mconf)
            music.PLAYER.play(index, mconf)
            service.set_mode("music")
            return f"Playing {it['title']}."
        if it["where"] == "pitato" and it["kind"] == "game":
            return _start_game(it)
        if it["where"] == "pitato":
            okay, why = shelf.show_on_pitato(it)
            return f"Playing {it['title']} on the Pietato." if okay else f"The Pietato wouldn't — {why}."
        return f"{shelf.describe(it)} — that isn't something I can play."
    # show: a picture on the panel, a book in Read, a song playing, a video on the Pietato
    found = shelf.find(words, config=conf)
    if not found:
        return f"I couldn't find anything like '{words}' {nowhere()}."
    it = found[0]
    if it["kind"] in ("photo", "drawing"):
        if it["where"] == "here" and it["folder"] == "drawing" and it["name"] == "drawing.png":
            service.set_mode("image")
            return "The drawing is on the panel."
        data = shelf.fetch(it)
        img = Image.open(_io.BytesIO(data)).convert("RGB")
        label = f"{it['title']}" + (" · from the Pietato" if it["where"] == "pitato" else " · from the USB drive" if it["folder"] == "drive" else "")
        postcard.keep(img, (it.get("host") or "the Pietato") if it["where"] == "pitato" else "here", "", label=label)
        llm.set_picture(img, it["title"])
        service.set_mode("postcard")
        if how == "telegram":
            return (data if it["name"].lower().endswith((".jpg", ".jpeg")) else _as_jpeg(img), f"{shelf.describe(it)} — on the panel too.")
        return f"{it['title']} is on the panel ({shelf.describe(it).split(' — ', 1)[1]})."
    if it["kind"] == "book" or (it["kind"] == "document" and it["name"].lower().endswith(shelf.READABLE)):
        if it["where"] == "here" and it.get("book"):
            reading.open(it["book"])
        else:
            data = shelf.fetch(it, shelf.DOC_MAX)
            book = reader.add(it["name"], data)
            reading.open(book)
        service.set_mode("reader")
        return f"{it['title']} is open in Read" + (" (copied over from the Pietato)." if it["where"] == "pitato"
                                                    else " (copied over from the USB drive)." if it["folder"] == "drive" else ".")
    if it["kind"] == "music":
        return _shelf_do({"kind": "play", "words": words}, how)
    if it["where"] == "pitato":
        okay, why = shelf.show_on_pitato(it)
        return f"{it['title']} is on the Pietato's screen." if okay else f"The Pietato wouldn't — {why}."
    return f"{shelf.describe(it)} — I can't show that kind of thing on the panel."


def _play_from_drive(it, conf):
    """A song on a USB drive: played from there, the drive's other songs around it as the playlist."""
    if not it["name"].lower().endswith(music.KINDS):
        return f"{shelf.describe(it)} — that's a kind of file I can't play."
    songs = [i for i in shelf.on_drives() if i["kind"] == "music" and i.get("dev") == it.get("dev")
             and i["name"].lower().endswith(music.KINDS)]
    tracks = sorted(({"path": drives.path_in(i["root"], i["path"]), "title": i["title"], "size": i["size"],
                      "folder": os.path.dirname(i["path"])} for i in songs), key=lambda t: (t["folder"].lower(), t["title"].lower()))
    want = drives.path_in(it["root"], it["path"])
    index = next((k for k, t in enumerate(tracks) if t["path"] == want), None)
    if index is None:
        return "That song has gone from the USB drive."
    mconf = {**conf.get("music", {}), **conf.get("audio", {})}
    music.PLAYER.load(tracks, mconf)
    music.PLAYER.play(index, mconf)
    service.set_mode("music")
    return f"Playing {it['title']} (from the USB drive{(' ' + it['drive']) if it.get('drive') else ''})."


def _as_jpeg(img):
    import io as _io
    buf = _io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


@app.get("/api/photos")
def api_photos():
    """The photos, newest first, and the filters there are."""
    try:
        limit = max(1, min(500, int(request.args.get("limit", 60))))
    except ValueError:
        limit = 60
    items, count = photos.listing(limit)
    return ok(photos=items, count=count, filters=photos.FILTERS)


@app.post("/api/photos")
def api_photos_take():
    """The shutter: {filter, rot} — rot is the live view's own turn (its round
    button), so the photo comes out the way the view shows it."""
    b = request.get_json(silent=True) or {}
    try:
        rot = int(b.get("rot") or 0)
    except (TypeError, ValueError):
        rot = 0
    try:
        got = photos.take(str(b.get("filter") or "none"), rot=rot,
                          mirror=bool(service.config.get("camera", {}).get("mirror")))
    except (OSError, ValueError, RuntimeError) as e:
        log.info("photo: %s", e)
        return fail(f"couldn't take it — {e}")
    return ok(photo=got)


@app.get("/api/photos/<name>")
def api_photo(name):
    from flask import send_file
    path = photos.path_of(name)
    if not path:
        return fail("no such photo", 404)
    return send_file(path, mimetype="image/jpeg", as_attachment=request.args.get("download") == "1",
                     download_name=f"pie-ink-{name}", max_age=3600)


@app.get("/api/photos/<name>/thumb")
def api_photo_thumb(name):
    from flask import send_file
    path = photos.thumb_of(name)
    if not path:
        return fail("no such photo", 404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@app.delete("/api/photos/<name>")
def api_photo_delete(name):
    if not photos.delete(name):
        return fail("no such photo", 404)
    items, count = photos.listing(60)
    return ok(photos=items, count=count)


@app.post("/api/photos/<name>/show")
def api_photo_show(name):
    """On the screen, as a postcard from the camera."""
    from PIL import Image
    path = photos.path_of(name)
    if not path:
        return fail("no such photo", 404)
    with Image.open(path) as im:
        postcard.keep(im.convert("RGB"), "the camera", label="A photo", ts=photos.info(name)["ts"] or None)
    service.set_mode("postcard")
    return ok(mode="postcard")


@app.post("/api/photos/<name>/send")
def api_photo_send(name):
    """To your Telegram chat."""
    path = photos.path_of(name)
    if not path:
        return fail("no such photo", 404)
    conf = _tg_conf()
    if not telegram.paired(conf):
        return fail("Telegram isn't paired yet — Settings → Telegram")
    with open(path, "rb") as fh:
        okay, why = telegram.send_photo(conf, fh.read(), "A photo from your PiE-ink")
    return ok(sent=True) if okay else fail(f"couldn't send it — {why}")


# -- the little friend ------------------------------------------------------------------------

BUDDY_FACE_SECONDS = 60
_buddy_face = {"was": None, "until": 0.0}        # its face is up on the screens until then


def _buddy_show(line, kind, frame):
    """Its line where the bot's lines go — the screens that show the bot, the
    conversation — with the picture it reacted to."""
    llm.set_speech(line, f"buddy-{kind}")
    if frame is not None:
        llm.set_picture(frame, "room")
    chat.add("bot", line, "remark")
    service.poke()


def _buddy_say(line):
    conf = service.config
    if not conf.get("buddy", {}).get("talk", True):
        return
    try:
        if audio.is_speaking() or listen.EARS.status()["state"] in ("listening", "thinking"):
            return                                    # it doesn't talk over you, or over the bot
    except Exception:
        pass
    audio.say(line, conf.get("audio", {}))


def _buddy_log(text, kind, level):
    if service.config.get("camera_watch", {}).get("events", True):
        sight.log_event(text, kind, level, source="friend")


def _buddy_popup(kind):
    """Its face on the screen for a minute when it reacts, then back to what was there."""
    if not service.config.get("buddy", {}).get("show_face", True):
        return
    _buddy_face["until"] = time.time() + BUDDY_FACE_SECONDS
    current = service.mode_name
    if current not in (None, "off", "buddy"):
        _buddy_face["was"] = current
        service.set_mode("buddy", restart_cycle=False)
    else:
        service.poke()
    lcd.SECOND.wake.set()
    timer = threading.Timer(BUDDY_FACE_SECONDS + 1, _buddy_popdown)
    timer.daemon = True
    timer.start()


def _buddy_popdown():
    if time.time() < _buddy_face["until"] - 0.5:
        return                                        # it reacted again since: its face stays up
    was, _buddy_face["was"] = _buddy_face.get("was"), None
    if was and service.mode_name == "buddy" and was in service.modes_available():
        service.set_mode(was, restart_cycle=False)
    lcd.SECOND.wake.set()


buddy.BUDDY.configure(config_fn=lambda: service.config, show=_buddy_show, say=_buddy_say, log=_buddy_log,
                      popup=_buddy_popup, presence=presence.mark)


@app.get("/api/buddy")
def api_buddy():
    """The little friend: on or off, how it is, what it last said."""
    return ok(**buddy.BUDDY.status())


@app.post("/api/buddy")
def api_buddy_set():
    """The Camera tab's switch: on or off, saved, and it starts or stops at once."""
    b = request.get_json(silent=True) or {}
    config = service.apply_settings({"buddy": {"enabled": bool(b.get("on"))}})
    buddy.BUDDY.update(config.get("buddy", {}))
    return ok(**buddy.BUDDY.status())


# -- the agent ------------------------------------------------------------------------------

def _agent_frame():
    """A picture for the agent's look, or None: no camera, or only the test pattern."""
    try:
        if not [c for c in camera.available() if c["id"] != "mock"]:
            return None
        camera.CAMERA.touch(20)
        camera.CAMERA.wait_jpeg(0, timeout=5)
        if "test pattern" in (camera.CAMERA.source_name or ""):
            return None
        return camera.CAMERA.frame()
    except Exception as e:
        log.debug("agent frame: %s", e)
        return None


def _agent_notify(text):
    conf = _tg_conf()
    if not telegram.paired(conf):
        return "Telegram isn't paired"
    photo = llm.picture(max_age=120)
    _tg_notify(f"Agent: {text}", photo=photo["image"] if photo else None)
    chat.add("bot", text, "agent")
    return "sent"


def _agent_say(text):
    okay, why = audio.say(text, service.config.get("audio", {}))
    chat.add("bot", text, "agent")
    return "said" if okay else f"couldn't: {why}"


def _agent_show(text):
    llm.set_speech(text, "agent")
    service.poke()
    return "on the screen"


def _agent_home(text):
    done = home.HOME.handle(text)
    return done if done is not None else "not a command Home Assistant understands"


agent.AGENT.configure(
    config_fn=lambda: service.config,
    frame_fn=_agent_frame,
    facts_fn=lambda config: mascot.context(config, day=_day()),
    do={"notify": _agent_notify, "say": _agent_say, "show": _agent_show, "home": _agent_home},
    on_events=lambda entries, frame: [_event_out(e, frame, service.config.get("camera_watch", {}), service.config)
                                      for e in entries],
    look_fn=_agent_look,
    survey_fn=lambda: _camera_look({"kind": "around"})[0],
    can_turn=ptz.PTZ.available,
)


def start_listening():
    conf = service.config.get("listen", {})
    if not conf.get("enabled"):
        listen.EARS.stop()
        return False, "listening is switched off"
    return listen.EARS.start(conf, _heard, is_busy=lambda: audio.SPEAKER.speaking)


@app.get("/api/listen")
def api_listen():
    # the install status keeps its own "ok" flag, so it goes in its own object
    return ok(devices=listen.mic_devices(refresh=True), models=listen.MODELS, installed=listen.installed_models(),
              fetch=listen.fetch_state(), status=listen.EARS.status(), install=listen.install_status())


@app.post("/api/listen/install")
def api_listen_install():
    return ok(install=listen.install_vosk())


@app.post("/api/listen/model")
def api_listen_model():
    body = request.get_json(silent=True) or {}
    return ok(fetch=listen.download_model((body.get("model") or "").strip()))


def _relisten():
    """Called after settings change so a new phrase or mic takes effect."""
    try:
        listen.EARS.stop()
        time.sleep(0.2)
        start_listening()                 # waits for the old ears to close, or restarts them itself when they do
    except Exception:
        log.exception("couldn't restart listening")


@app.post("/api/listen/start")
def api_listen_start():
    okay, message = start_listening()
    return ok(message=message, status=listen.EARS.status()) if okay else fail(message)


@app.post("/api/listen/test")
def api_listen_test():
    conf = {**service.config.get("listen", {}), **(request.get_json(silent=True) or {})}
    return ok(**listen.EARS.test(conf))


@app.post("/api/listen/stop")
def api_listen_stop():
    listen.EARS.stop()
    return ok(status=listen.EARS.status())


# -- sound and music ------------------------------------------------------------

def _music_reply(**extra):
    conf = service.config.get("music", {})
    tracks = music.library(conf)
    from_drive = bool(music.PLAYER.tracks) and all(os.path.isabs(t["path"]) for t in music.PLAYER.tracks) \
        and music.PLAYER.state in ("playing", "paused")             # (songs off a USB drive: left playing)
    if not from_drive and [t["path"] for t in tracks] != [t["path"] for t in music.PLAYER.tracks]:
        music.PLAYER.load(tracks, {**conf, **service.config.get("audio", {})})
    return ok(tracks=tracks, now=music.PLAYER.tick(), folder=music.folder(conf), **extra)


@app.get("/api/music")
def api_music():
    return _music_reply()


@app.post("/api/music/<action>")
def api_music_action(action):
    body = request.get_json(silent=True) or {}
    conf = {**service.config.get("music", {}), **service.config.get("audio", {})}
    tracks = music.library(service.config.get("music", {}))
    music.PLAYER.load(tracks, conf)
    if action == "play":
        music.PLAYER.play(body.get("index"), conf)
    elif action == "toggle":
        music.PLAYER.toggle()
    elif action == "stop":
        music.PLAYER.stop()
    elif action == "next":
        music.PLAYER.step(1)
    elif action == "prev":
        music.PLAYER.step(-1)
    else:
        return fail("unknown action")
    service.poke()
    return _music_reply()


@app.get("/api/music/file")
def api_music_file():
    """A track, for the page playing PiE-ink's sound. Only what's in the
    library, by the path it's listed under; ranges work, so it can seek."""
    from flask import send_file
    rel = request.args.get("p", "")
    conf = service.config.get("music", {})
    known = {t["path"] for t in music.PLAYER.tracks} or {t["path"] for t in music.library(conf)}
    if not rel or rel not in known:
        return fail("no such track", 404)
    path = os.path.join(music.folder(conf), rel)
    if not os.path.isfile(path):
        return fail("that file has gone", 404)
    return send_file(path, conditional=True, max_age=0)


@app.post("/api/music/here")
def api_music_here():
    """The page playing the music says how it's going: {v, position, duration, ended, error}."""
    body = request.get_json(silent=True) or {}
    took = music.PLAYER.report(body)
    if took and (body.get("ended") or body.get("error")):
        music.PLAYER.tick()                       # the next track now, not when the screen next draws
        service.poke()
    return ok(took=took)


# -- hearing it on your phone or computer ("Play on this device") --------------------------

def _sound_moved():
    """A page started playing PiE-ink's sound, or the last one stopped: music follows."""
    music.PLAYER.moved()
    service.poke()


music.PLAYER.page = here.HERE.listening
music.PLAYER.on_change = here.HERE.music_changed
here.HERE.music = music.PLAYER.page_state
here.HERE.on_start = here.HERE.on_stop = _sound_moved


@app.get("/api/audio/here")
def api_audio_here():
    """A page with "Play on this device" on, asking what to play. Comes back
    when there is something (speech, a change to the music), or after a while
    with nothing, and the page asks again."""
    page = (request.args.get("id") or request.remote_addr or "page")[:60]
    try:
        after, mv = int(request.args.get("after", -1)), int(request.args.get("mv", -1))
    except ValueError:
        return fail("after and mv are numbers")
    return ok(**here.HERE.wait(page, after, mv))


@app.get("/api/audio/here/clip/<int:n>")
def api_audio_here_clip(n):
    data = here.HERE.clip(n)
    if data is None:
        return fail("gone", 404)
    return Response(data, mimetype="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/audio/here/off")
def api_audio_here_off():
    """The page switched it off, or is closing (sent as a beacon)."""
    body = request.get_json(silent=True, force=True) or {}
    page = str(body.get("id") or request.remote_addr or "page")[:60]
    here.HERE.leave(page)
    return ok(listening=here.HERE.listening())


@app.post("/api/music/upload")
def api_music_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("no file")
    if not f.filename.lower().endswith(music.KINDS):
        return fail("that isn't an audio file I can play")
    root = music.ensure_folder(service.config.get("music", {}))
    name = os.path.basename(f.filename)
    try:
        f.save(os.path.join(root, name))
    except OSError as e:
        return fail(f"couldn't save it: {e}", 500)
    return _music_reply(added=name)


@app.get("/api/audio")
def api_audio():
    conf = service.config.get("audio", {})
    engine, note = audio.voice_in_use(conf)
    advice = audio.voice_advice(conf)
    speaker = audio.SPEAKER
    return ok(devices=audio.devices(), missing=audio.missing(), engine=engine, voice_note=note,
              advice=advice, timing={"load_ms": speaker.load_ms, "first_ms": speaker.first_ms,
                                     "pace": speaker.learnt_pace()},
              last_error=audio.last_error(), here=here.HERE.status(),
              controls=audio.mixer_controls(service.config.get("audio", {}).get("device", "")),
              piper={"offered": audio.PIPER_VOICES, "installed": audio.installed_voices(),
                     **audio.install_status()})


@app.get("/api/audio/probe")
def api_audio_probe():
    """Why isn't my USB speaker or microphone listed? The bus, the cards, the
    power, the kernel's USB lines — and a verdict. Takes a moment."""
    report = audio.probe(service.config)
    return ok(report=report, text=audio.probe_text(report))


@app.get("/api/usb/watch")
def api_usb_watch():
    """What's happened on the USB bus since `since` (a kernel timestamp; -1
    for "start now"), in plain words, and what's on it now: the page asks
    every second or two while you plug things in."""
    from pie_ink import usbwatch
    try:
        since = float(request.args.get("since", -1))
    except ValueError:
        since = -1.0
    return ok(**usbwatch.events(since))


@app.post("/api/audio/piper/install")
def api_piper_install():
    return ok(piper={"offered": audio.PIPER_VOICES, "installed": audio.installed_voices(),
                     **audio.install_piper()})


@app.get("/api/audio/piper")
def api_piper_status():
    return ok(piper={"offered": audio.PIPER_VOICES, "installed": audio.installed_voices(),
                     **audio.install_status()})


@app.post("/api/audio/piper/voice")
def api_piper_voice():
    """Either one of the listed voices, or a link to any .onnx voice."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if url:
        okay, message = audio.download_voice_url(url, body.get("name", ""))
        chosen = os.path.basename(url)[:-5]
    else:
        chosen = (body.get("voice") or "").strip()
        okay, message = audio.download_voice(chosen)
    if not okay:
        return fail(message)
    installed = audio.installed_voices()
    pick = next((v["id"] for v in installed if v["id"].startswith(chosen[:20])), chosen)
    service.apply_settings({"audio": {"engine": "piper", "piper_voice": pick}})
    return ok(message=message, installed=installed)


@app.post("/api/audio/say")
def api_audio_say():
    body = request.get_json(silent=True) or {}
    conf = service.config.get("audio", {})
    text = (body.get("text") or "").strip() or f"Hello, this is {socket.gethostname()}"
    # wait: come back once it has played (or couldn't), with where it went or why not —
    # the "Test the speaker" button uses this, so a dead output is a message, not a guess
    okay, message = audio.say(text, conf, wait=bool(body.get("wait")))
    return ok(message=message) if okay else fail(message)


@app.post("/api/message/say")
def api_message_say():
    """Read the message screen's text out loud — typed, joke, fortune or bot."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        mode = getattr(service, "_mode", None)
        if mode is not None and mode.name == "message":
            text = (getattr(mode, "_current", "") or "").strip()
        if not text:
            conf = service.config.get("message", {})
            text = (conf.get("text") or "").strip() if conf.get("source", "message") == "message" else ""
    if not text:
        return fail("nothing to read — type something, or show the screen first")
    okay, message = audio.say(text.replace("\n", ". "), service.config.get("audio", {}))
    return ok(message=message, said=text[:120]) if okay else fail(message)


@app.post("/api/message/mp3")
def api_message_mp3():
    """The words on the message screen, spoken into an MP3 you can keep."""
    import tempfile
    from flask import send_file
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        mode = getattr(service, "_mode", None)
        if mode is not None and mode.name == "message":
            text = (getattr(mode, "_current", "") or "").strip()
    if not text:
        return fail("nothing to record — type something first")
    path = os.path.join(tempfile.gettempdir(), f"pie-ink-{int(time.time())}.mp3")
    okay, path, message = audio.to_mp3(text.replace("\n", ". "),
                                       service.config.get("audio", {}), path)
    if not okay:
        log.info("recording refused: %s", message)
        return fail(message)
    words = "-".join(re.findall(r"[a-z0-9]+", text.lower())[:5]) or "message"
    ext = os.path.splitext(path)[1] or ".mp3"
    kind = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    log.info("recorded %s (%s)", os.path.basename(path), message)
    return send_file(path, mimetype=kind, as_attachment=True,
                     download_name=f"{words}{ext}", max_age=0)


@app.post("/api/audio/volume")
def api_audio_volume():
    body = request.get_json(silent=True) or {}
    level = int(body.get("volume", 80))
    conf = service.config.get("audio", {})
    how = audio.set_volume(level, conf.get("device", ""))
    service.apply_settings({"audio": {"volume": level}})
    return ok(applied=bool(how), how=how)


# -- the bot's voice --------------------------------------------------------------

def _day():
    return Day(service.config.get("schedule", {}))


@app.get("/api/llm")
def api_llm():
    """Models on the machine you chose, and on the Pi itself for the back-up."""
    conf = service.config.get("llm", {})
    host = request.args.get("host")
    if host:
        return ok(**llm.probe(conf, host=host))
    info = llm.probe(conf) if (conf.get("host") or "").strip() else {"ok": False, "models": [], "error": "no address set"}
    here = llm.probe(conf, timeout=3, host=llm.PI)
    return ok(**info, speech=llm.latest(), busy=llm.busy(),
              pi={"ok": here["ok"], "models": here["models"], "error": here["error"], "host": llm.PI})


@app.post("/api/llm/say")
def api_llm_say():
    conf = service.config.get("llm", {})
    if not conf.get("enabled"):
        return fail("its voice is switched off — turn it on in the Me tab")
    if not (conf.get("model") or "").strip():
        return fail("no model chosen — press Check, then pick one from the list")
    if llm.busy():
        return fail("it's still thinking about the last one")
    if not mascot.ask(service.config, "idle", day=_day()):
        return fail("couldn't ask it just now")
    return ok(speech=llm.latest())


@app.get("/api/chat")
def api_chat():
    return ok(history=chat.history(), speaking=audio.SPEAKER.speaking,
              listening=listen.EARS.status()["state"])


@app.delete("/api/chat")
def api_chat_clear():
    return ok(history=chat.clear())


@app.post("/api/draw/describe")
def api_draw_describe():
    """Look at what's on the drawing canvas and say what it is."""
    import base64
    import io as _io
    from PIL import Image
    body = request.get_json(silent=True) or {}
    data = (body.get("image") or "").split(",", 1)[-1]
    if not data:
        return fail("no drawing came through")
    conf = service.config.get("llm", {})
    if not conf.get("enabled") or not (conf.get("model") or "").strip():
        return fail("set the bot up first — the robot button, top right")
    try:
        image = Image.open(_io.BytesIO(base64.b64decode(data)))
    except Exception:
        return fail("couldn't read that drawing")
    try:
        text = mascot.describe(service.config, mascot.as_base64(image), "drawing")
    except ValueError as e:
        log.info("describe refused: %s", e)
        return fail(str(e))
    except Exception as e:
        log.warning("describe failed: %s", e)
        return fail(str(e)[:140], 502)
    if text:
        llm.set_speech(text, "drawing")
        llm.set_picture(image, "drawing")      # so the screen can show what it described
        chat.add("bot", text, "screen")
        if service.config.get("audio", {}).get("speak_bot", True):
            audio.say(text, service.config.get("audio", {}))
    service.poke()
    return ok(text=text)


@app.post("/api/llm/chat")
def api_llm_chat():
    """{message} — and, from a Pietato's Bot using this one, {image}: a picture as a data URL
    or plain base64, which goes to the model with the words (a model that sees)."""
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    image = _picture_in(body.get("image"))
    if not message and not image:
        return fail("say something first")
    message = message or "What do you make of this picture?"
    # from a Pietato's Bot page: the same bot, answering there — so it isn't also said out loud here
    how = "pitato" if pitato.PITATO.is_pitato(request.headers.get("User-Agent")) else "typed"
    prior = chat.history()
    chat.add("you", message + (" [sent a picture]" if image else ""), how)
    text = _home_command(message, how) if not image else None
    if text is not None:                      # a home command, done: no bot needed
        llm.set_speech(text, "chat")
        chat.add("bot", text, how)
        if how != "pitato" and service.config.get("audio", {}).get("speak_bot") and service.config.get("home", {}).get("say_back", True):
            audio.say(text, service.config.get("audio", {}))
        service.poke()
        return ok(reply=text, speech=llm.latest(), history=chat.history(), home=True)
    try:
        text = mascot.reply(service.config, message, day=_day(), history=prior, image=image)
    except ValueError as e:
        log.info("chat refused: %s", e)          # shows up in journalctl, not just the page
        return fail(str(e))
    except Exception as e:
        log.warning("chat failed: %s", e)
        return fail(str(e)[:140], 502)
    if how != "pitato" and service.config.get("audio", {}).get("speak_bot"):
        audio.say(text, service.config.get("audio", {}))
    chat.add("bot", text, how)
    service.poke()
    head = pet.status()                              # what its head did with this question, if it was one
    return ok(reply=text, speech=llm.latest(), history=chat.history(),
              head=head if head.get("question") == message.strip()[:120] and head.get("ago", 999) < 60 else None)


def _picture_in(value):
    """A picture sent with a message: base64 (a data URL or bare), checked to be one, no bigger
    than a few MB; None otherwise."""
    import base64 as _b64
    import io as _io
    from PIL import Image
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    if len(raw) > 12_000_000:
        return None
    try:
        data = _b64.b64decode(raw, validate=False)
        with Image.open(_io.BytesIO(data)) as im:
            im.verify()
    except Exception:
        return None
    return _b64.b64encode(data).decode()


# -- weather -------------------------------------------------------------------

@app.get("/api/weather/lookup")
def api_weather_lookup():
    q = (request.args.get("q") or "").strip()
    if not q:
        return ok(results=[])
    try:
        return ok(results=weather.geocode(q))
    except Exception as e:
        log.warning("geocode failed: %s", e)
        return fail("Couldn't look that up right now", 502)


@app.post("/api/weather/place")
def api_weather_place():
    """Set the location from a lookup result (or clear it)."""
    body = request.get_json(silent=True) or {}
    if not body.get("lat"):
        service.apply_settings({"weather": {"location": "", "lat": None, "lon": None, "country": "", "source": ""}})
        return ok()
    label = ", ".join(x for x in (body.get("name"), body.get("admin") or body.get("country")) if x)
    config = service.apply_settings({"weather": {"location": label, "lat": float(body["lat"]), "lon": float(body["lon"]),
                                                 "country": body.get("country_code", ""), "source": "typed"}})
    _weather_prefetch(float(body["lat"]), float(body["lon"]), config["weather"].get("units", "F"))
    return ok(location=label)


@app.post("/api/weather/locate")
def api_weather_locate():
    """Find me: where the Pi's internet address says it is."""
    cache.clear("ip_location")                   # asked for: look afresh, even after a failed try
    cache.clear("ip_location__fail")
    loc = locate.from_network()
    if not loc:
        return fail("couldn't tell where this Pi is — the network lookups didn't answer")
    patch = {"lat": loc["lat"], "lon": loc["lon"], "location": loc["place"], "country": loc["country"], "source": "network"}
    config = service.apply_settings({"weather": patch})
    _weather_prefetch(patch["lat"], patch["lon"], config["weather"].get("units", "F"))
    return ok(location=patch["location"], source=patch["source"], lat=patch["lat"], lon=patch["lon"])


def _weather_prefetch(lat, lon, units):
    """Fetch a new spot's forecast in the background, then nudge the running
    screen so it shows it now rather than at its next scheduled refresh."""
    def go():
        try:
            weather.fetch(lat, lon, units)
            service.apply_settings({})
        except Exception as e:
            log.info("weather prefetch: %s", e)
    threading.Thread(target=go, daemon=True).start()


# -- where the Pi is, without being told ------------------------------------------------------

def _auto_locate():
    """Nothing typed in: the Pi's internet address says roughly where it is,
    and the weather goes there. A location that came from the network before
    is checked again (the Pi may have moved house); one you typed is left
    alone. Returns the patch applied, or None."""
    w = service.config.get("weather", {})
    if w.get("lat") is not None and w.get("source") != "network":
        return None
    loc = locate.from_network()
    if not loc:
        return None
    if w.get("lat") is not None and locate.distance_km((float(w["lat"]), float(w["lon"])), (loc["lat"], loc["lon"])) < locate.MOVED_KM:
        return None                              # the same place as before
    patch = {"lat": loc["lat"], "lon": loc["lon"], "location": loc["place"], "country": loc["country"] or w.get("country", ""),
             "source": "network"}
    config = service.apply_settings({"weather": patch})
    _weather_prefetch(patch["lat"], patch["lon"], config["weather"].get("units", "F"))
    log.info("the weather is now for %s (from the Pi's internet address)", patch["location"])
    return patch


# -- what the camera has seen ------------------------------------------------------------------

@app.get("/api/events")
def api_events():
    since = float(request.args.get("since") or 0)
    limit = max(1, min(400, int(request.args.get("limit") or 80)))
    return ok(events=sight.events(limit=limit, since=since), status=sight.status(),
              detector=sight.detector(service.config), watching=watcher.WATCHER.status()["running"])


@app.delete("/api/events")
def api_events_clear():
    sight.clear()
    return ok(events=[])


@app.post("/api/events/look")
def api_events_look():
    """Take a look right now, whatever the watcher is doing."""
    frame = _agent_frame()
    if frame is None:
        return fail("no picture from a camera — the test pattern doesn't count")
    conf = service.config
    obs, entries = sight.look(frame, conf, mascot._owner(conf), view=ptz.PTZ.view_key())
    for e in entries:
        _event_out(e, frame, conf.get("camera_watch", {}), conf)
    if obs is None:
        return fail(sight.status().get("error") or "nothing could look at it (no vision model, no OpenCV)")
    return ok(saw=sight.describe(obs), observation=obs, events=entries, status=sight.status())


# -- the agent ------------------------------------------------------------------------------

@app.get("/api/agent")
def api_agent():
    return ok(status=agent.AGENT.status(), journal=agent.AGENT.journal(30))


@app.post("/api/agent/run")
def api_agent_run():
    """A check now, in the background; the page watches the status."""
    if agent.AGENT.busy:
        return ok(started=False, message="it's in the middle of one")
    conf = service.config
    llm_conf = conf.get("llm", {})
    if not llm_conf.get("enabled") or not (llm_conf.get("model") or "").strip():
        return fail("the agent needs the bot — set Ollama up in the Message tab first")
    if agent.AGENT.running():
        agent.AGENT.run_now()
    else:
        threading.Thread(target=agent.AGENT.tick, args=("asked",), daemon=True, name="agent-once").start()
    return ok(started=True)


@app.delete("/api/agent")
def api_agent_clear():
    agent.AGENT.clear()
    return ok(journal=[])


@app.get("/api/mesh")
def api_mesh():
    """The PiE-inks and what each has, in words and in fields."""
    peers = _everyone_for_mesh()
    return ok(me={**friends.me(), "caps": mesh.my_caps(service.config)},
              pis=[{**p, "words": mesh.describe(p)} for p in peers], pitatos=pitato.PITATO.describe())


# -- the miners: Duino-Coin and Verus ------------------------------------------------------------

@app.get("/api/mining")
def api_mining():
    """Both pools: what the Crypto tab shows under the coins."""
    if request.args.get("refresh"):
        for m in mining.enabled():
            if m.running():
                m.refresh_now()
            else:
                m.fetch()
        time.sleep(1.5)
    return ok(pools={k: m.status() for k, m in mining.MINERS.items()})


@app.post("/api/mining/check")
def api_mining_check():
    """Look a username or wallet up right now, before saving — so a typo is caught here."""
    body = request.get_json(silent=True) or {}
    pool = (body.get("pool") or "duco").strip()
    who = (body.get("who") or "").strip()
    if pool not in mining.MINERS:
        return fail("which pool?")
    if not who:
        return fail("what's your Duino-Coin username?" if pool == "duco" else "what's the wallet address?")
    probe = mining.Duco() if pool == "duco" else mining.Verus()
    probe.configure({"enabled": True, "username": who, "wallet": who, "min_hashrate": 0})
    d = probe.fetch(keep=False)
    if not d:
        return fail(probe.error or f"couldn't look up {who}")
    return ok(data={k: v for k, v in d.items() if k != "miners"}, miners=d["miners"][:10],
              words=f"{probe.who_short()}: {d['balance']:,.{probe.decimals}f} {probe.symbol} {probe.balance_label}, "
                    f"{d['hashrate_text']} from {d['workers']} worker{'s' if d['workers'] != 1 else ''}")


@app.get("/api/weather")
def api_weather():
    w = service.config.get("weather", {})
    if w.get("lat") is None:
        return ok(data=None)
    data = weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"), cached_only=True)
    return ok(data=data, location=w.get("location"))


# -- what this Pi has fetched already, for the other Pis on the network -----------------------

SHARE_POINTS = 500                 # a chart handed to another Pi: thinned to this many prices at most
STOCK_RANGES = (1, 7, 30, 90, 180, 365)   # a share's charts, each cut from the prices Yahoo gave (pie_ink/stocks.py)


def _thin(values, most=SHARE_POINTS):
    """A chart's prices, at most `most` of them (evenly picked, the newest always in), each to six figures:
    the shape is all a chart needs, and the answer stays small however many ranges and coins there are."""
    vals = [v for v in (values or []) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(vals) > most:
        step = (len(vals) - 1) / (most - 1)
        vals = [vals[round(i * step)] for i in range(most)]
    return [float(f"{v:.6g}") for v in vals]


def _coin_graphs(coin_id):
    """Every chart of a coin that's been fetched, whatever its range: [{"days", "v", "age"}], shortest first
    ("v": the prices — the shape a Pietato shares its own charts in, and reads another's)."""
    out = []
    for key in cache.keys(f"hist_{coin_id}_"):
        tail = key[len(f"hist_{coin_id}_"):]
        if not tail.isdigit():
            continue
        graph, age = cache.read(key)
        if graph and age is not None:
            out.append({"days": int(tail), "v": _thin(graph), "age": round(age, 1)})
    return sorted(out, key=lambda g: g["days"])


def _stock_graphs(symbol):
    """A share's charts for the usual ranges, each from the prices already fetched: [{"days", "v", "age"}]."""
    out = []
    for days in STOCK_RANGES:
        age = stocks.history_age(symbol, days)
        graph = stocks.price_history(symbol, days, cached_only=True) if age is not None else []
        if graph:
            out.append({"days": days, "v": _thin(graph), "age": round(age, 1)})
    return out


@app.get("/api/shared")
def api_shared():
    """What this Pi has already fetched from the internet, and how old each piece is,
    so another Pi of yours on the same network (a Pietato, another PiE-ink) can use it
    instead of fetching the same thing: the weather for the weather spot — now, the
    hours ahead and the week — with the US worded forecast, and every coin and share
    on the Finance tab with its charts, every range that's been fetched. Only what's in
    the cache — nothing is fetched on the asker's behalf, and nothing here is yours (no
    settings, no keys, no names, no amounts).

      {"weather": {"lat", "lon", "units", "place", "country", "age", "data", "nws", "nws_age"} | null,
       "coins": [{"id", "symbol", "name", "age", "data", "graph", "graph_days", "graph_age", "graphs"}, ...],
       "stocks": [{"id", "symbol", "name", "age", "data", "graph", "graph_days", "graphs"}, ...]}

    "age" is seconds since that piece came from the internet; each keeps its source's
    own shape (pie_ink/weather.py's forecast — "hours": the next 24 from this one,
    [{"time", "temp", "code", "rain", "is_day"}], "days": a week; pie_ink/coingecko.py's
    coin; pie_ink/stocks.py's share). "graph" is the chart of the Finance tab's own range
    ("graph_days"), as it always was; "graphs" is every range there is,
    [{"days": 1 | 7 | 30 | 180 | 365..., "v": prices oldest first (500 at most), "age"}] — the shape a
    Pietato shares its own charts in (and reads them in)."""
    conf = service.config
    out = {"at": time.time(), "weather": None, "coins": []}
    w = conf.get("weather", {})
    if w.get("lat") is not None and w.get("lon") is not None:
        lat, lon, units = float(w["lat"]), float(w["lon"]), w.get("units", "F")
        data = weather.fetch(lat, lon, units, cached_only=True)
        age = weather.age(lat, lon, units)
        if data and age is not None:
            nws, nws_age = cache.read(f"nws_{lat:.3f}_{lon:.3f}")
            out["weather"] = {"lat": lat, "lon": lon, "units": units, "place": w.get("location") or "",
                              "country": w.get("country") or "", "age": round(age, 1), "data": data,
                              "nws": nws or [], "nws_age": None if not nws else round(nws_age, 1)}
    days = int(conf.get("crypto", {}).get("graph_days", 7) or 7)
    for item in watchlist.load():
        if watchlist.kind(item) != "crypto":                   # (a share's figures are Yahoo's: under "stocks" below)
            continue
        d = coingecko.coin_data(item["id"], cached_only=True)
        age = coingecko.coin_age(item["id"])
        if not d or d.get("seed") or age is None:              # bundled figures aren't a fetch
            continue
        graph, graph_age = cache.read(f"hist_{item['id']}_{days}")
        out["coins"].append({"id": item["id"], "symbol": item.get("symbol", ""), "name": item.get("name", item.get("symbol", "")),
                             "age": round(age, 1), "data": d, "graph": graph or [], "graph_days": days,
                             "graph_age": None if not graph else round(graph_age, 1), "graphs": _coin_graphs(item["id"])})
    out["stocks"] = []
    for item in watchlist.load():
        if watchlist.kind(item) != "stock":
            continue
        d = stocks.stock_data(item["id"], cached_only=True)
        age = stocks.stock_age(item["id"])
        if not d or age is None:
            continue
        out["stocks"].append({"id": item["id"], "symbol": item["id"], "name": d.get("name") or item.get("name", ""),
                              "age": round(age, 1), "data": {k: v for k, v in d.items() if k != "today"},
                              "graph": stocks.price_history(item["id"], days, cached_only=True) or [], "graph_days": days,
                              "graphs": _stock_graphs(item["id"])})
    return ok(**out)


# -- friends: other PiE-inks on the network -----------------------------------

@app.get("/api/friends")
def api_friends():
    """The other PiE-inks (the rooms of this house: the same bot there) — and the Pietatos about, with their cameras."""
    pt = pitato.PITATO.status()
    return ok(me=friends.me(), friends=friends.peers(), rooms=household.HOUSE.status(), pitatos=pt["pitatos"],
              cameras=pitato.PITATO.peers(), excited=pt["excited"])


# -- camera -------------------------------------------------------------------

@app.get("/api/ptz")
def api_ptz():
    """A camera that turns: whether there is one, where it points, what it last saw — and
    whose camera this is (a Pietato's Camera tab names it from here)."""
    return ok(**ptz.PTZ.status(), name=socket.gethostname(), server="PiE-ink")


@app.post("/api/ptz/drive")
def api_ptz_drive():
    """Turning at a speed (the arrow keys, a game controller): -1…1 each way, right and up
    positive, a few times a second while held. A drag on the picture uses /ptz/set instead."""
    b = request.get_json(silent=True) or {}
    try:
        pos = ptz.PTZ.drive(b.get("vx", 0), b.get("vy", 0), sid=str(b.get("sid") or ""), seq=int(b.get("seq") or 0),
                            speed=b.get("speed"), swap_x=b.get("swap_x"), swap_y=b.get("swap_y"))
    except ValueError as e:
        return fail(str(e))
    return ok(position=pos, following=bool(ptz.PTZ.follow_on))   # following: the stick rests, the page says why


@app.post("/api/ptz/stop")
def api_ptz_stop():
    b = request.get_json(silent=True) or {}
    return ok(position=ptz.PTZ.stop(sid=str(b.get("sid") or ""), seq=int(b.get("seq") or 0)))


@app.post("/api/ptz/set")
def api_ptz_set():
    """Point it: {pan, tilt} in degrees, {zoom} 0…1, or {center: true}."""
    b = request.get_json(silent=True) or {}
    ptz.PTZ.user_moved()                                  # you're pointing it: the little friend waits
    try:
        num = lambda k: float(b[k]) if b.get(k) is not None else None   # noqa: E731
        stopped = False
        if b.get("center") or num("pan") is not None or num("tilt") is not None:
            stopped = ptz.PTZ.take_over()                 # pointing it somewhere: it stops following
        if b.get("center"):
            pos = ptz.PTZ.center()
        else:
            pos = ptz.PTZ.set(pan=num("pan"), tilt=num("tilt"), zoom=num("zoom"))
    except (OSError, ValueError) as e:
        return fail(str(e))
    return ok(position=pos, stopped_following=stopped)


@app.post("/api/ptz/follow")
def api_ptz_follow():
    """Follow me: the camera's own tracking (an OBSBOT's), on or off — and kept that way."""
    b = request.get_json(silent=True) or {}
    ptz.PTZ.user_moved()
    try:
        got = ptz.PTZ.set_follow(bool(b.get("on")))
    except (OSError, ValueError) as e:
        return fail(getattr(e, "strerror", None) or str(e))
    status = ptz.PTZ.status()
    if got is None or got != bool(b.get("on")):
        return fail("the camera didn't take it — try again in a moment", 503)
    return ok(follow=status["follow"], position=status["position"])


@app.post("/api/ptz/home")
def api_ptz_home():
    """Go to the home position, or {save: true} to make this it."""
    b = request.get_json(silent=True) or {}
    if not ptz.PTZ.available():
        return fail("no camera that turns is plugged in")
    try:
        if b.get("save"):
            p = ptz.PTZ.position()
            home_pos = {"pan": p["pan"], "tilt": p["tilt"], "zoom": p["zoom"]}
            config = service.apply_settings({"ptz": {"home": home_pos}})
            ptz.PTZ.configure(config.get("ptz", {}))
            return ok(position=p, home=home_pos)
        ptz.PTZ.user_moved()
        stopped = ptz.PTZ.take_over()
        return ok(position=ptz.PTZ.home(), home=service.config.get("ptz", {}).get("home"), stopped_following=stopped)
    except (OSError, ValueError) as e:
        return fail(str(e))


@app.post("/api/ptz/look")
def api_ptz_look():
    """Look around (or left, right, up, down) and say what is there — in the
    background; the answer lands in the conversation and in last_look."""
    b = request.get_json(silent=True) or {}
    where = (b.get("where") or "around").strip().lower()
    if not ptz.PTZ.available():
        return fail("no camera that turns is plugged in")
    if ptz.PTZ.looking:
        return fail("it's already looking around")
    ptz.PTZ.user_moved()
    ptz.PTZ.last_look = None
    threading.Thread(target=_look_now, args=(where,), daemon=True, name="ptz-look").start()
    return ok(started=True)


@app.get("/api/camera/probe")
def api_camera_probe():
    """Why the USB camera isn't found: the bus, the nodes, a test open, the
    kernel's USB lines, and a verdict. Takes a few seconds."""
    report = camera.probe()
    return ok(report=report, text=camera.probe_text(report))


@app.get("/api/camera/list")
def api_camera_list():
    refresh = request.args.get("refresh") == "1"
    return ok(cameras=camera.available(refresh=refresh), using=camera.CAMERA.status().get("id"))


@app.post("/api/camera/switch")
def api_camera_switch():
    """Use a particular camera, or the next one along."""
    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "").strip()
    if body.get("next") or source == "next":
        source = camera.CAMERA.next_camera()
    elif source:
        camera.CAMERA.switch(source)
    else:
        return fail("which camera?")
    service.apply_settings({"camera": {"source": source}})
    service.poke()
    return ok(source=source, status=camera.CAMERA.status())


@app.get("/api/camera/status")
def api_camera_status():
    return ok(**CAMERA.status())


@app.post("/api/camera/pause")
def api_camera_pause():
    """Let go of the camera for a while, whoever wants it (Settings → Sound, for
    plugging a speaker or mic in on a Pi Zero); {seconds: 0} brings it back."""
    b = request.get_json(silent=True) or {}
    try:
        seconds = max(0.0, min(600.0, float(b.get("seconds", 120))))
    except (TypeError, ValueError):
        return fail("seconds?")
    return ok(paused_for=CAMERA.pause(seconds))


@app.get("/api/camera.jpg")
def api_camera_still():
    CAMERA.touch()
    jpg = CAMERA.jpeg()
    if not jpg:
        return fail(CAMERA.error or "camera starting", 503)
    return Response(jpg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/camera/stream")
def api_camera_stream():
    """MJPEG: the browser shows this in an <img> and it just keeps updating.
    ?filter=sketch (a drawn photo filter): each picture drawn that way here,
    a few a second — the page does the colour filters itself."""
    CAMERA.touch()
    fid = request.args.get("filter") or ""
    drawn = fid in photos.DRAWN
    # each frame is handed to the socket as it is yielded and the next one waits until it
    # has mostly gone (the server's small buffers, set in _serve() below): a phone on a slow
    # link skips frames rather than piling up seconds of them, and one the socket keeps
    # holding frames for gets smaller frames instead — see camera.Viewer
    viewer = camera.Viewer()

    def gen():
        nonlocal drawn
        seq, drew = 0, 0.0
        while True:
            CAMERA.touch()
            if drawn:
                seq, jpg = CAMERA.wait_jpeg(seq, timeout=2.0)
                if jpg:
                    time.sleep(max(0.0, 0.2 - (time.time() - drew)))      # a few a second is plenty to aim with
                    frame = CAMERA.frame()
                    drew = time.time()
                    if frame is None:
                        continue
                    try:
                        jpg = _jpeg(photos.apply(frame, fid))
                    except Exception as e:
                        log.info("stream filter %s: %s", fid, e)
                        drawn = False
            else:
                jpg = viewer.next(CAMERA, timeout=2.0)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
            elif CAMERA.error and not CAMERA.status()["running"]:
                return

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


# -- the watchlist: coins and shares ----------------------------------------------------------------

def _with_prices(items):
    """The watchlist for the page: each item with its kind, its price (in its own
    money: $ for a coin, the exchange's currency for a share) and today's move."""
    out = []
    for i in items:
        q = watchlist.quote(i, cached_only=True)
        out.append({**i, "kind": watchlist.kind(i), "price": q.get("price") if q else None,
                    "money": (q.get("money") or "$") if q else "$", "exchange": (q.get("exchange") or "") if q else "",
                    "change_1d": (q.get("change") or {}).get("24h") if q else None})
    return out


@app.get("/api/watchlist")
def api_watchlist():
    coingecko.refresh_coin_list_async()
    return ok(items=_with_prices(watchlist.load()))


@app.post("/api/watchlist")
def api_watchlist_add():
    """{id} a coin's CoinGecko id, or {id, kind: "stock"} a share's ticker."""
    body = request.get_json(silent=True) or {}
    try:
        items = watchlist.add((body.get("id") or "").strip(), "stock" if body.get("kind") == "stock" else "crypto")
    except ValueError as e:
        return fail(str(e))
    return ok(items=_with_prices(items))


@app.put("/api/watchlist/<coin_id>")
def api_watchlist_toggle(coin_id):
    """{show: bool} — in the rotation or not; {amount: n} — how much of it you hold."""
    body = request.get_json(silent=True) or {}
    if "amount" in body:
        try:
            items = watchlist.set_amount(coin_id, body["amount"])
        except ValueError as e:
            return fail(str(e))
        holdings.HOLDINGS.value()                                                   # the total with the new figure, now
        threading.Thread(target=holdings.HOLDINGS.refresh, daemon=True).start()   # and fresh prices for it
        if "show" not in body:
            return ok(items=_with_prices(items))
    return ok(items=_with_prices(watchlist.set_visible(coin_id, body.get("show", True))))


@app.get("/api/holdings")
def api_holdings():
    """What you hold is worth: the total, each coin and share, the week's readings, the peak, the line."""
    if request.args.get("refresh"):
        holdings.HOLDINGS.refresh()
    return ok(**holdings.HOLDINGS.status())


@app.post("/api/holdings/try")
def api_holdings_try():
    """Hear what the bot will say when it happens — with today's real numbers, to the bot alone."""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind") or "rich"
    if kind not in ("rich", "crash", "rally", "under", "slump"):
        return fail("which moment?")
    h = holdings.HOLDINGS
    v = h.value()
    if not v["items"]:
        return fail("put in how much of each coin or share you hold first")
    total, line = v["total"], h.line() or 1000000.0
    yours = holdings.whose(v)
    plural = yours == "Your holdings"
    are, have, they = ("are", "they have", "they are") if plural else ("is", "it has", "it's")
    top = ", ".join(f"{r['symbol']} {holdings.money(r['value'])}" for r in v["items"][:3] if r.get("value") and r["symbol"] not in v.get("foreign", []))
    text = {"rich": f"{yours} just passed {holdings.money(line)}: {they} worth {holdings.money(max(total, line * 1.003))} now"
                    + (f" ({top})" if top else "") + ". You're rich!",
            "under": f"{yours} {'have' if plural else 'has'} slipped back under {holdings.money(line)}: {holdings.money(min(total, line * 0.997))} now.",
            "crash": f"The market is down: {yours.lower()} {are} worth {holdings.money(total)}, -{h.share():.0f}% today "
                     f"({holdings.signed(-total * h.share() / 100)}).",
            "rally": f"The market is up: {yours.lower()} {are} worth {holdings.money(total)}, +{h.share():.0f}% today "
                     f"({holdings.signed(total * h.share() / 100)}).",
            "slump": f"{yours} {are} {2 * h.share():.0f}% below the most {have} been worth: {holdings.money(total)} now."}[kind]
    threading.Thread(target=_holdings_alert, args=(kind, text, v, True), daemon=True).start()
    return ok(text=text, model=bool(service.config.get("llm", {}).get("enabled") and (service.config.get("llm", {}).get("model") or "").strip()))


@app.delete("/api/watchlist/<coin_id>")
def api_watchlist_remove(coin_id):
    items = watchlist.remove(coin_id)
    holdings.HOLDINGS.value()                   # the total without it, now (it may have been held)
    return ok(items=_with_prices(items))


@app.get("/api/coins/search")
def api_coin_search():
    """Coins (the local catalogue) and shares (Yahoo's lookup) that fit what was
    typed — a ticker typed in capitals puts the shares first."""
    q = (request.args.get("q") or "").strip()
    coins = [{"id": c["id"], "symbol": c["display"], "name": c["name"], "rank": c.get("rank"), "kind": "crypto"}
             for c in coingecko.search(q, 8)]
    shares, note = [], ""
    if len(q) >= 2:
        try:
            shares = stocks.search(q, 6)
        except Exception as e:
            note = f"couldn't ask about shares ({str(e)[:60]})"
    first_shares = q.isupper() and len(q) <= 6
    results = (shares + coins) if first_shares else (coins + shares)
    return ok(results=results[:12], note=note)


# -- system -------------------------------------------------------------------

def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "n/a"


@app.get("/api/system")
def api_system():
    """Same numbers the System screen shows (one sampler, one truth)."""
    from pie_ink.modes.system import _ensure_sampler, _last, _sample
    _ensure_sampler()
    if not _last["ts"]:
        _sample(interval=0.2)
    return ok(
        cpu=round(_last["cpu"], 1),
        mhz=_last["mhz"],
        memory=round(_last["mem_pct"], 1),
        temp=None if _last["temp"] is None else round(_last["temp"], 1),
        disk={"used_gb": round(_last["disk_used"] / 1e9, 1), "total_gb": round(_last["disk_total"] / 1e9, 1),
              "percent": _last["disk_pct"]},
        uptime=int(time.time() - psutil.boot_time()),
        ip=_last["ip"] if _last["ip"] != "n/a" else _ip(),
        hostname=socket.gethostname(),
    )


@app.get("/api/system/update")
def api_sysupdate_status():
    return ok(**sysupdate.status())


@app.post("/api/system/update")
def api_sysupdate_start():
    """sudo apt-get update && upgrade, in the background; poll GET for the log."""
    if not sysupdate.start():
        return fail("an update is already running")
    return ok()


@app.post("/api/power")
def api_power():
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in ("reboot", "shutdown"):
        return fail("action must be reboot or shutdown")
    cmd = ["sudo", "-n", "reboot"] if action == "reboot" else ["sudo", "-n", "shutdown", "now"]
    try:
        subprocess.Popen(cmd)
    except OSError as e:
        return fail(str(e), 500)
    return ok(action=action)


# -- bored: nothing from you for a while, and it plays Snake on the little screen ------------------

def _not_bored():
    """A touch while Snake is up out of boredom: back to the screen it had."""
    if _bored["from"] is None:
        return
    _bored["from"], _bored["ended"] = None, time.time()
    log.info("snake: someone's about — back to the %s screen", service.config.get("lcd", {}).get("screen"))
    threading.Thread(target=_lcd_apply, args=(service.config,), daemon=True).start()


def _bored_check():
    """One look: long enough without a word, the model there, the little screen on and
    showing something else — then Snake goes up. Returns True when it did."""
    conf = service.config
    sconf, lconf = conf.get("snake", {}), conf.get("lcd", {})
    try:
        minutes = float(sconf.get("bored_minutes", 0) or 0)
    except (TypeError, ValueError):
        minutes = 0
    if _bored["from"] is not None:
        if not lconf.get("enabled") or lconf.get("screen") == "snake" or minutes <= 0:
            _bored["from"] = None                # picked by hand, or switched off: nothing to go back to
        return False
    if minutes <= 0 or not lconf.get("enabled") or service.panel.colour or lconf.get("screen") == "snake":
        return False
    if attention.idle_seconds() < minutes * 60:
        return False
    # only when the model is there — an error screen is no way to pass the time
    if time.time() - _bored["checked"] > 300:
        _bored["checked"] = time.time()
        host = llm.base(conf.get("llm", {})) or llm.normalise(conf.get("llm", {}).get("backup_host") or llm.PI)
        model = (sconf.get("model") or "tev1").strip()
        models = llm.probe(conf.get("llm", {}), timeout=4, host=host)["models"]
        _bored["there"] = any(m == model or m.split(":")[0] == model.split(":")[0] for m in models)
        if not _bored["there"]:
            log.info("snake: bored, but %s isn't on %s — not playing", model, host)
    if not _bored["there"]:
        return False
    _bored["from"] = lconf.get("screen", "system")
    log.info("snake: bored (%g min without a word) — playing on the little screen", minutes)
    _lcd_apply(conf)
    return True


def _bored_loop():
    while True:
        time.sleep(20)
        try:
            _bored_check()
        except Exception:
            log.exception("bored loop")


# -- config.yaml written by someone else (setup.sh, a hand edit): taken up while running -------------

def _config_check():
    """One look at config.yaml: changed since this process last read or wrote it,
    the differences are applied as if they had been saved on the page. True when
    something was taken up."""
    if not cfg.changed_on_disk():
        return False
    fresh = cfg.load()
    patch = cfg.diff(service.config, fresh)
    if not patch:
        cfg.mark_seen()                            # the same settings, written again (another copy of the app, say): nothing to do
        return False
    log.info("config.yaml changed on disk — taking up %s", ", ".join(sorted(patch)))
    config = service.apply_settings(patch)
    _after_settings(patch, config)
    return True


def _config_watch():
    while True:
        time.sleep(3)
        try:
            _config_check()
        except Exception:
            log.exception("config watch")


attention.on_touch(_not_bored)                   # any touch while Snake is up out of boredom ends it
doing.boredom = lambda: _bored                   # so "what are you doing?" can say it's passing the time


def _chatter():
    """Every so often it says something about the weather, the coins, the time
    or whatever else is going on — unprompted, like a thing that lives here."""
    import random
    # weighted: plenty about the day and the weather, coins only now and then
    topics = ["weather", "weather", "time", "time", "day", "day", "day",
              "anything", "anything", "camera", "coins", "rhythm", "holdings"]
    while True:
        try:
            gap = int(float(service.config.get("llm", {}).get("chatter_minutes", 30) or 0))
        except (TypeError, ValueError):
            gap = 0
        if gap <= 0:
            time.sleep(60)
            continue
        time.sleep(max(60, gap * 60) * random.uniform(0.7, 1.3))   # not on the dot
        try:
            _remark(topics)
        except Exception:                             # one bad remark must not end them all
            log.exception("chatter")


def _remark(topics):
    import random
    conf = service.config
    if not conf.get("llm", {}).get("enabled") or llm.busy():
        return
    if music.PLAYER.state == "playing" or listen.EARS.status()["state"] in ("listening", "thinking"):
        return                                    # don't talk over the music or you
    topic = random.choice(topics)
    if topic == "music" and music.PLAYER.state != "playing":
        topic = "anything"
    if topic == "camera" and not conf.get("llm", {}).get("see", True):
        topic = "anything"
    if topic == "holdings" and not holdings.HOLDINGS.any():
        topic = "coins"
    log.info("saying something about %s", topic)
    before = llm.latest()["ts"]
    mascot.ask(conf, day=_day(), topic=topic, history=chat.history(6))
    for _ in range(120):                        # note it down once it lands
        time.sleep(1)
        said = llm.latest()
        if said["ts"] != before and said["text"]:
            chat.add("bot", said["text"], "remark")
            if _tg_conf().get("notify_remarks"):
                _tg_notify(said["text"])
            break
    service.poke()


def _warm_up():
    """Fetch what the screens need while the Pi is booting, so the first switch
    to Weather, Crypto or Calendar doesn't sit waiting on the network."""
    def run():
        try:
            _auto_locate()                       # nowhere set yet: the network says roughly where this is
        except Exception as e:
            log.info("warm-up: locate %s", e)
        conf = service.config
        w = conf.get("weather", {})
        if w.get("lat") is not None:
            try:
                weather.fetch(float(w["lat"]), float(w["lon"]), w.get("units", "F"))
                if str(w.get("country", "")).upper() == "US":
                    weather.nws_forecast(float(w["lat"]), float(w["lon"]))
            except Exception as e:
                log.info("warm-up: weather %s", e)
        try:
            for item in watchlist.visible()[:4]:
                watchlist.quote(item, int(conf.get("crypto", {}).get("graph_days", 7)))
        except Exception as e:
            log.info("warm-up: coins %s", e)
        try:
            music.PLAYER.load(music.library(conf.get("music", {})),
                              {**conf.get("music", {}), **conf.get("audio", {})})
        except Exception as e:
            log.info("warm-up: music %s", e)
        try:
            audio.warm_up(conf.get("audio", {}))      # load the voice so the first line is quick
        except Exception as e:
            log.info("warm-up: voice %s", e)
        llm_conf = conf.get("llm", {})
        if llm_conf.get("enabled") and (llm_conf.get("model") or "").strip():
            try:
                mascot.ask(service.config, "idle", day=_day())     # so the Bot screen has a line ready
            except Exception as e:
                log.info("warm-up: llm %s", e)
        log.info("warm-up done")

    threading.Thread(target=run, daemon=True, name="warm-up").start()
    threading.Thread(target=_chatter, daemon=True, name="chatter").start()
    threading.Thread(target=_bored_loop, daemon=True, name="bored").start()
    threading.Thread(target=_config_watch, daemon=True, name="config-watch").start()


# How waitress is run. threads: each open camera stream keeps one busy, so a few more than
# the pages need. outbuf_high_watermark: how much of a response the server will hold for a
# client that hasn't taken it yet before the app is made to wait — waitress's default is 16 MB,
# which on the camera's MJPEG stream is many seconds' worth of frames: a phone on a slow Wi-Fi
# link saw the picture seconds behind, in slow motion. A few tens of KB and the stream's
# generator waits for each frame to go instead, so a slow link skips frames and stays live
# (camera.Viewer sends it smaller frames when it's holding them up). SEND_BUFFER: the kernel's
# own send buffer per connection, kept small for the same reason — left to itself Linux grows
# it to megabytes, which was another several seconds of frames. send_bytes=1: hand each piece
# of a streaming response to the socket as it is written, rather than holding the tail of a
# frame back until 18 KB more has piled up (waitress 3 does that by default and is retiring
# the option, so it's only passed to a waitress that needs it).
SERVER_OPTIONS = {"threads": 12, "outbuf_high_watermark": 32 * 1024}
SEND_BUFFER = 64 * 1024


def _server_options():
    try:
        from waitress.adjustments import Adjustments
        if getattr(Adjustments, "send_bytes", 1) != 1:
            SERVER_OPTIONS["send_bytes"] = 1
    except Exception:
        pass
    return SERVER_OPTIONS


def _serve(port):
    """The app under waitress (python3-waitress), with the options above."""
    from waitress import create_server
    server = create_server(app, host="0.0.0.0", port=port, ident="pie-ink", **_server_options())
    server.adj.socket_options = [o for o in server.adj.socket_options if o[1] != socket.SO_SNDBUF] + \
        [(socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER)]
    log.info("serving on http://0.0.0.0:%d (waitress)", port)
    server.run()


def _shutdown(signum, frame):
    """systemctl stop / Ctrl-C: put the panel to sleep and release the GPIO."""
    log.info("shutting down")
    try:
        for stop in (ptz.PTZ.shutdown, lcd.SECOND.stop, whisplay.BOARD.stop, gamepad.PADS.stop):
            try:
                stop()
            except Exception:
                pass
        service.shutdown()
        service.join(timeout=5)
    finally:
        os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    port = int(os.environ.get("PIE_INK_PORT", 5000))
    friends.start(service.status, port)
    social.on_receive(_arrived)
    telegram.start(_tg_conf, _tg_command, _tg_paired, _tg_tap)
    start_lcd()
    start_board()
    start_gamepad()
    start_listening()
    start_watching()
    agent.AGENT.start(service.config.get("agent", {}))
    ptz.PTZ.start()                                # keeps an OBSBOT's own following where the switch says
    buddy.BUDDY.start(service.config.get("buddy", {}))   # the little friend, if it's switched on
    for _pool in ("duco", "verus"):
        mining.MINERS[_pool].start(service.config.get(_pool, {}))
    holdings.HOLDINGS.start(service.config.get("holdings", {}))
    reminders.REMINDERS.start()
    household.HOUSE.start()                        # your other PiE-inks: rooms of the one bot's house
    plugs.PLUGS.start()                            # what's plugged into the USB ports: the bot's face when that changes
    gestures.GESTURES.configure(service.config.get("gestures", {}))   # hand gestures, if they're switched on

    def _maybe_speak(text, kind):
        if service.config.get("audio", {}).get("speak_bot") and kind not in ("chat", "gesture") and not kind.startswith("buddy"):
            audio.say(text, service.config.get("audio", {}))   # chat replies are spoken by their own route
    llm.on_new_line(_maybe_speak)

    _warm_up()
    if "--debug" in sys.argv:
        app.run(host="0.0.0.0", port=port, threaded=True, debug=True, use_reloader=False)
    else:
        try:
            import waitress                            # a proper server, if installed (python3-waitress)
        except ImportError:
            waitress = None
        if waitress is not None:
            _serve(port)
        else:
            app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
