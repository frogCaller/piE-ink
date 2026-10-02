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

from pie_ink import (agent, audio, buddy, cache, chat, coingecko, friends, gps, here, holdings, home, journey, lcd, lcd_screens, listen, llm, locate,
                     maps, mascot, mesh, mining, music, panels, photos, presence, ptz, reader, sight, social, sources,
                     sysupdate, telegram, watcher, watchlist, weather, whisplay)
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
    g = gps.GPS.status()
    if g["enabled"]:
        out["gps"] = {"fix": g["fix"], "place": g.get("place") or "", "sats": g.get("sats_used"), "connected": g["connected"]}
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
                           "move": (holdings.HOLDINGS.move or {}).get("kind")}
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
    return ok(**out)


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
    if "gps" in patch:
        threading.Thread(target=gps.GPS.update, args=(_gps_conf(config),), daemon=True).start()
    if "display" in patch or "whisplay" in patch:
        threading.Thread(target=start_board, daemon=True).start()
    if "home" in patch:
        home.HOME.configure(config.get("home", {}))
    if "agent" in patch:
        threading.Thread(target=agent.AGENT.update, args=(config.get("agent", {}),), daemon=True).start()
    if "ptz" in patch:
        ptz.PTZ.configure(config.get("ptz", {}))
    if "buddy" in patch:
        buddy.BUDDY.update(config.get("buddy", {}))
    if "journey" in patch:
        journey.JOURNEY.configure(config.get("journey", {}))
    for pool in ("duco", "verus"):
        if pool in patch:
            threading.Thread(target=mining.MINERS[pool].update, args=(config.get(pool, {}),), daemon=True).start()
    if "holdings" in patch:
        threading.Thread(target=holdings.HOLDINGS.update, args=(config.get("holdings", {}),), daemon=True).start()
    if "me" in patch or "camera" in patch or "gps" in patch or "audio" in patch or "listen" in patch or "llm" in patch:
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
home.HOME.configure(service.config.get("home", {}))
friends.where_from(lambda: maps.where(service.config))   # the beacon says where we are, for friends' maps
friends.caps_from(lambda: mesh.my_caps(service.config))  # and what we have, so "find the pi with a camera" works
journey.JOURNEY.configure(service.config.get("journey", {}))
lcd.SECOND.interval = lcd_screens.interval


def _lcd_conf(config):
    conf = dict(config.get("lcd", {}))
    if os.environ.get("PIE_INK_DRIVER") == "mock":
        conf["driver"] = "mock"                  # no hardware here either
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


# -- the map --------------------------------------------------------------------------------

@app.get("/api/map")
def api_map():
    """Where we are, where the other PiE-inks are, and what the page needs
    to draw its own map."""
    me = maps.where(service.config)
    others = []
    for p in friends.peers():
        wh = p.get("where") or {}
        if wh.get("lat") is None:
            continue
        others.append({"host": p["host"], "url": p["url"], "lat": wh["lat"], "lon": wh["lon"],
                       "place": wh.get("place") or "", "source": wh.get("source"), "ago": p["ago"], "mode": p.get("mode")})
    m = service.config.get("map", {})
    return ok(me=me, friends=others, tiles=maps.TILE_URL, zoom=int(m.get("zoom", 15) or 15),
              me_host=socket.gethostname(), cache=maps.cache_status(), gps=gps.GPS.status())


@app.post("/api/map/prefetch")
def api_map_prefetch():
    """Fetch the tiles the e-ink map needs for its current view now (the
    page's Fetch button), so the panel has them the moment it is shown."""
    me = maps.where(service.config)
    if not me:
        return fail("no position yet")
    m = service.config.get("map", {})
    zoom = int(m.get("zoom", 15) or 15)
    w, h = service.panel.width, service.panel.height
    started = maps.fetch_async(me["lat"], me["lon"], zoom, (w, max(20, h - 29)))
    return ok(started=started, cache=maps.cache_status())


# -- Telegram --------------------------------------------------------------------------

TG_HELP = """What I can do from here:
/screen <name> — put a screen up (clock, weather, me, message, drawing, book, camera, music, chat, crypto, system, off)
/screens — list them
/panel — a picture of what's on the panel now
/photo — a picture from the camera; /photo sepia (or bw, sketch, edges, comic, pixel, e-ink…) takes a photo with that filter and keeps it
/photos — the last photo you took
/say <words> — say it out loud on the Pi
/message <words> — put it on the message screen
/status — how things are
/map — where it is, as a map
/journey — where it has been today, as a map and a line; /journey 2026-09-20 for another day
/events — what the camera has seen today (person, package, door, pets)
/agent — what the agent last thought; /agent now for a fresh check
/pis — the other PiE-inks and what each has
/look — a camera that turns looks around and says what it sees, with the picture; /look left, /look up…
/follow — an OBSBOT follows you by itself; /follow off to stop (or say "follow me", "stop following me")
/friend — how the little friend is; /friend on, /friend off
/mining — your miners: balance, hashrate, workers (Duino-Coin and Verus); /duco, /verus for one
/holdings — what your crypto is worth, coin by coin, and how today went
/tv — a remote for the TV, as buttons (Home Assistant, once it's set up); /tv 5452, /tv on, /tv volume up
turn on the tv, channel 5452, volume up, turn off the lamp — the same, in words
take a picture, take a black and white photo — a photo, kept on the Pi
show me the camera from upstairs, send this drawing to the workshop pi, which pi has a gps — the other Pis
Anything else and I'll just answer you."""

SCREEN_WORDS = {"drawing": "image", "draw": "image", "book": "reader", "read": "reader", "you": "me",
                "chat": "groupchat", "group": "groupchat", "location": "gps", "where": "gps",
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
    conf = service.config
    low = text.lower().strip()
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
    if low.startswith(("/map", "/where")):
        me = maps.where(conf)
        if not me:
            return "No position yet — plug the GPS in, or press Find me on the Weather tab."
        link = f"https://www.openstreetmap.org/?mlat={me['lat']:.5f}&mlon={me['lon']:.5f}#map=16/{me['lat']:.5f}/{me['lon']:.5f}"
        caption = (f"{me['place'] + ' — ' if me.get('place') else ''}{me['lat']:.5f}, {me['lon']:.5f} "
                   f"({maps.source_words(me)})\n{link}")
        try:
            return (service.render_preview("map", None), caption)
        except Exception as e:
            log.info("telegram /map render: %s", e)
            return caption
    if low.startswith("/journey"):
        day = (low.split(None, 1)[1].strip() if " " in low else "") or journey.today()
        pts = journey.JOURNEY.points(day)
        line = journey.summary(day, conf.get("weather", {}).get("units", "F"), pts) or f"Nothing recorded for {day}."
        if len(pts) >= 2 and day == journey.today():
            try:
                return (service.render_preview("map", None), line)
            except Exception as e:
                log.info("telegram /journey render: %s", e)
        return line
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
        return _mesh_do({"kind": "list"}, friends.peers(), "telegram")
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
                out.append(f"{m.title.title()} is off — set it up under Crypto → {m.title.title()}.")
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
        return "\n\n".join(out) if out else "No mining set up — Crypto → Duino-Coin or Verus."
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
        g = gps.GPS.status()
        if g["enabled"]:
            if g["fix"] != "none":
                bits.append(f"GPS: {g['lat']:.4f}, {g['lon']:.4f}" + (f" ({g['place']})" if g.get("place") else "")
                            + f", {g['sats_used']} satellites")
            else:
                bits.append("GPS: no fix yet" if g["connected"] else "GPS: no receiver plugged in")
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
    """A friend's Pi sent something. Show it, say it, and let the bot answer if it was for the bot."""
    conf = service.config
    sender = msg.get("from") or "a friend"
    kind = msg.get("kind")
    text = (msg.get("text") or "").strip()
    social_conf = conf.get("social", {})

    if kind == "postcard" and msg.get("image"):
        try:
            postcard.keep(_image_from(msg["image"]), sender, text)
        except Exception as e:
            log.warning("bad postcard from %s: %s", sender, e)
            return
        if social_conf.get("show_postcards", True):
            service.set_mode("postcard")
        if social_conf.get("speak_messages", True):
            audio.say(f"A postcard from {sender}." + (f" It says: {text}" if text else ""),
                      conf.get("audio", {}))
        return

    if _tg_conf().get("notify_friends", True) and kind in ("text", "postcard"):
        _tg_notify(f"{sender}: {text}" if kind == "text" else f"A postcard from {sender}" + (f" — {text}" if text else ""),
                   photo=_image_from(msg["image"]) if kind == "postcard" and msg.get("image") else None)
    if kind == "text":
        chat.add("bot", f"{sender} says: {text}", "friend")
        if social_conf.get("speak_messages", True):
            audio.say(f"{sender} says: {text}", conf.get("audio", {}))
        service.poke()
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
    return ok(history=social.history(), friends=social.everyone(service.config), **social.status())


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
    try:
        done = home.HOME.handle(text)
    except Exception as e:
        log.warning("home command '%s' (%s) failed: %s", text, how, e)
        return f"Couldn't — {str(e)[:100]}."
    if done is not None:
        return done
    done = _mesh_command(text, how)
    if done is not None:
        return done
    done = _camera_command(text, how)
    if done is not None:
        return done
    return _photo_command(text, how)


# -- the other PiE-inks, as a mesh -----------------------------------------------------------

def _mesh_command(text, how):
    """"Which pi has a camera", "show me the camera from upstairs", "send this
    drawing to the workshop pi": done, or None if it wasn't that. From
    Telegram a picture comes back as (jpeg, caption)."""
    try:
        intent = mesh.parse(text)
        if not intent:
            return None
        return _mesh_do(intent, friends.peers(), how)
    except Exception as e:
        log.warning("mesh command '%s' (%s) failed: %s", text, how, e)
        return f"Couldn't — {str(e)[:100]}."


def _mesh_do(intent, peers, how):
    import base64
    import io as _io
    from PIL import Image
    from pie_ink.modes import MODES
    from pie_ink.modes.simple import ImageMode
    kind = intent["kind"]
    roll = "\n".join(mesh.describe(p) for p in peers)
    if kind == "list":
        return ("PiE-inks I can see:\n" + roll) if peers else "No other PiE-ink on the network right now."
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
            _tg_notify(f"Crypto: {text}")
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
    if [t["path"] for t in tracks] != [t["path"] for t in music.PLAYER.tracks]:
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
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return fail("say something first")
    prior = chat.history()
    chat.add("you", message, "typed")
    text = _home_command(message, "typed")
    if text is not None:                      # a home command, done: no bot needed
        llm.set_speech(text, "chat")
        chat.add("bot", text, "typed")
        if service.config.get("audio", {}).get("speak_bot") and service.config.get("home", {}).get("say_back", True):
            audio.say(text, service.config.get("audio", {}))
        service.poke()
        return ok(reply=text, speech=llm.latest(), history=chat.history(), home=True)
    try:
        text = mascot.reply(service.config, message, day=_day(), history=prior)
    except ValueError as e:
        log.info("chat refused: %s", e)          # shows up in journalctl, not just the page
        return fail(str(e))
    except Exception as e:
        log.warning("chat failed: %s", e)
        return fail(str(e)[:140], 502)
    if service.config.get("audio", {}).get("speak_bot"):
        audio.say(text, service.config.get("audio", {}))
    chat.add("bot", text, "typed")
    service.poke()
    return ok(reply=text, speech=llm.latest(), history=chat.history())


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
    """Find me: the GPS's fix if it has one, else where the Pi's internet address says it is."""
    pos = gps.GPS.position() if gps.GPS.running() else None
    if pos is not None:
        label, country = gps.place_name(*pos)
        patch = {"lat": round(pos[0], 4), "lon": round(pos[1], 4), "location": label or f"{pos[0]:.3f}, {pos[1]:.3f}",
                 "country": country or "", "source": "gps"}
    else:
        cache.clear("ip_location")               # asked for: look afresh, even after a failed try
        cache.clear("ip_location__fail")
        loc = locate.from_network()
        if not loc:
            return fail("couldn't tell where this Pi is — no GPS fix, and the network lookups didn't answer")
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


# -- the GPS receiver ------------------------------------------------------------------------

_gps_applied = None          # the last position the weather was moved to


def _gps_conf(config):
    conf = dict(config.get("gps", {}))
    if os.environ.get("PIE_INK_DRIVER") == "mock" and conf.get("device", "auto") == "auto" and gps.mode(conf) == "on":
        conf["device"] = "mock"                  # a pretend receiver where there is no USB — only when asked for one
    return conf


def _gps_fix(state):
    """A fresh position from the receiver: name the place (once per spot)
    and, if asked, move the weather there."""
    global _gps_applied
    here = (state["lat"], state["lon"])
    try:
        journey.JOURNEY.record(state)            # the day's track, a point every few metres
    except Exception:
        log.debug("journey point", exc_info=True)
    if _gps_applied and gps.distance_km(here, _gps_applied) < 1.0:
        return                                   # still the same place
    label, country = gps.place_name(*here)
    gps.GPS.place = label
    _gps_applied = here
    if service.config.get("gps", {}).get("set_weather", True):
        w = service.config.get("weather", {})
        patch = {"lat": round(here[0], 4), "lon": round(here[1], 4), "source": "gps",
                 "location": label or f"{here[0]:.3f}, {here[1]:.3f}", "country": country or w.get("country", "")}
        config = service.apply_settings({"weather": patch})
        _weather_prefetch(patch["lat"], patch["lon"], config["weather"].get("units", "F"))
        log.info("gps: the weather is now for %s", patch["location"])


def _auto_locate():
    """Nothing typed in and no GPS fix: the Pi's internet address says roughly
    where it is, and the weather goes there. A location that came from the
    network before is checked again (the Pi may have moved house); one you
    typed, or the GPS's, is left alone. Returns the patch applied, or None."""
    w = service.config.get("weather", {})
    if w.get("lat") is not None and w.get("source") != "network":
        return None
    if gps.GPS.running() and gps.GPS.position() is not None:
        return None                              # the receiver knows better, and the fix handler has it
    loc = locate.from_network()
    if not loc:
        return None
    if w.get("lat") is not None and gps.distance_km((float(w["lat"]), float(w["lon"])), (loc["lat"], loc["lon"])) < locate.MOVED_KM:
        return None                              # the same place as before
    patch = {"lat": loc["lat"], "lon": loc["lon"], "location": loc["place"], "country": loc["country"] or w.get("country", ""),
             "source": "network"}
    config = service.apply_settings({"weather": patch})
    _weather_prefetch(patch["lat"], patch["lon"], config["weather"].get("units", "F"))
    log.info("the weather is now for %s (from the Pi's internet address)", patch["location"])
    return patch


gps.GPS.on_fix = _gps_fix


@app.get("/api/gps")
def api_gps():
    return ok(status=gps.GPS.status(), devices=gps.candidates())


@app.get("/api/gps/probe")
def api_gps_probe():
    """Why isn't the receiver working: the USB bus, the ports, who else might
    be reading them, and what they say — with a verdict. Pauses the reader
    for the few seconds it listens."""
    if not gps.GPS.running():
        gps.GPS.conf = _gps_conf(service.config)
    report = gps.probe()
    return ok(text=gps.probe_text(report), verdict=report["verdict"], status=gps.GPS.status())


# -- the journey: where it has been -----------------------------------------------------------

@app.get("/api/journey")
def api_journey():
    """A day's track (today unless ?day=YYYY-MM-DD), thinned for the page,
    with its numbers and a sentence about it."""
    day = (request.args.get("day") or "").strip() or journey.today()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return fail("which day? YYYY-MM-DD")
    pts = journey.JOURNEY.points(day)
    units = service.config.get("weather", {}).get("units", "F")
    return ok(day=day, today=journey.today(), days=journey.JOURNEY.days(), points=journey.simplify(pts),
              stats=journey.stats(pts), summary=journey.summary(day, units, pts), status=journey.JOURNEY.status())


@app.delete("/api/journey")
def api_journey_clear():
    day = (request.args.get("day") or "").strip() or journey.today()
    journey.JOURNEY.clear(day)
    return ok(days=journey.JOURNEY.days())


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
    peers = friends.peers()
    return ok(me={**friends.me(), "caps": mesh.my_caps(service.config)},
              pis=[{**p, "words": mesh.describe(p)} for p in peers])


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


# -- friends: other PiE-inks on the network -----------------------------------

@app.get("/api/friends")
def api_friends():
    return ok(me=friends.me(), friends=friends.peers())


# -- camera -------------------------------------------------------------------

@app.get("/api/ptz")
def api_ptz():
    """A camera that turns: whether there is one, where it points, what it last saw."""
    return ok(**ptz.PTZ.status())


@app.post("/api/ptz/drive")
def api_ptz_drive():
    """The joystick: where the stick is (-1…1, right and up positive), a few times a second while held."""
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


# -- crypto watchlist ----------------------------------------------------------------

def _with_prices(items):
    out = []
    for i in items:
        q = watchlist.quote(i, cached_only=True)
        out.append({**i, "price": q.get("price") if q else None,
                    "change_1d": (q.get("change") or {}).get("24h") if q else None})
    return out


@app.get("/api/watchlist")
def api_watchlist():
    coingecko.refresh_coin_list_async()
    return ok(items=_with_prices(watchlist.load()))


@app.post("/api/watchlist")
def api_watchlist_add():
    body = request.get_json(silent=True) or {}
    try:
        items = watchlist.add((body.get("id") or "").strip())
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
    """What your crypto is worth: the total, each coin, the week's readings, the peak, the line."""
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
        return fail("put in how much of each coin you hold first")
    total, line = v["total"], h.line() or 1000000.0
    top = ", ".join(f"{r['symbol']} {holdings.money(r['value'])}" for r in v["items"][:3] if r.get("value"))
    text = {"rich": f"Your crypto just passed {holdings.money(line)}: it's worth {holdings.money(max(total, line * 1.003))} now"
                    + (f" ({top})" if top else "") + ". You're rich!",
            "under": f"Your crypto has slipped back under {holdings.money(line)}: {holdings.money(min(total, line * 0.997))} now.",
            "crash": f"The market is down: your crypto is worth {holdings.money(total)}, -{h.share():.0f}% today "
                     f"({holdings.signed(-total * h.share() / 100)}).",
            "rally": f"The market is up: your crypto is worth {holdings.money(total)}, +{h.share():.0f}% today "
                     f"({holdings.signed(total * h.share() / 100)}).",
            "slump": f"Your crypto is {2 * h.share():.0f}% below the most it has been worth: {holdings.money(total)} now."}[kind]
    threading.Thread(target=_holdings_alert, args=(kind, text, v, True), daemon=True).start()
    return ok(text=text, model=bool(service.config.get("llm", {}).get("enabled") and (service.config.get("llm", {}).get("model") or "").strip()))


@app.delete("/api/watchlist/<coin_id>")
def api_watchlist_remove(coin_id):
    return ok(items=_with_prices(watchlist.remove(coin_id)))


@app.get("/api/coins/search")
def api_coin_search():
    q = (request.args.get("q") or "").strip()
    return ok(results=[{"id": c["id"], "symbol": c["display"], "name": c["name"], "rank": c.get("rank")}
                       for c in coingecko.search(q, 10)])


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
                coingecko.coin_data(item["id"])
                coingecko.price_history(item["id"], int(conf.get("crypto", {}).get("graph_days", 7)))
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
        for stop in (ptz.PTZ.shutdown, lcd.SECOND.stop, gps.GPS.stop, whisplay.BOARD.stop):
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
    gps.GPS.start(_gps_conf(service.config))
    start_listening()
    start_watching()
    agent.AGENT.start(service.config.get("agent", {}))
    ptz.PTZ.start()                                # keeps an OBSBOT's own following where the switch says
    buddy.BUDDY.start(service.config.get("buddy", {}))   # the little friend, if it's switched on
    for _pool in ("duco", "verus"):
        mining.MINERS[_pool].start(service.config.get(_pool, {}))
    holdings.HOLDINGS.start(service.config.get("holdings", {}))

    def _maybe_speak(text, kind):
        if service.config.get("audio", {}).get("speak_bot") and kind != "chat" and not kind.startswith("buddy"):
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
