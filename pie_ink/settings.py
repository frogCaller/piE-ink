"""Single source of truth for configuration.

config.yaml only stores what the user changed; DEFAULTS fills the rest.
Every mode reads from the same structure, so "dark mode" and "rotation"
exist exactly once.
"""
import copy
import logging
import os
import threading

import yaml

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")
DATA_DIR = os.path.join(ROOT, "data")
ASSETS_DIR = os.path.join(ROOT, "assets")

DEFAULTS = {
    "display": {
        "driver": "epd2in13_V4",   # epd2in13_V4 | epd2in13_V3 | epd2in7_V2 | epd3in7 | whisplay | mock
        "rotation": 0,             # 0 or 180 (an LCD panel takes 90 and 270 too, for landscape)
        "dark_mode": False,        # white on black
        "full_refresh_every": 60,  # partial updates between full refreshes (ghosting)
        "brightness": 70,          # an LCD panel's backlight
        "accent": "#62d2ff",       # the colour screens' accent, on a colour panel
        "crypto_days": 1,          # the colour crypto screen's chart window, on a colour panel
    },
    "whisplay": {                  # the PiSugar Whisplay HAT's button and LED (its screen is the panel)
        "led": True,               # the RGB LED shows what the bot is up to
        "hold_to_talk": True,      # hold the button and the bot listens
        "button": "next_mode",     # a short press: next_mode | prev_mode | next | prev | refresh | off | none
    },
    "startup_mode": "me",      # what shows when it starts
    "buttons": {                   # keys on the 2.7" HAT: prev | next | next_mode | prev_mode | refresh | off | none
        "key1": "prev",
        "key2": "next",
        "key3": "next_mode",
        "key4": "refresh",
    },
    "clock": {
        "format": "12",            # "12" | "24"
        "show_seconds": False,
        "show_date": False,
        "font": "SpecialElite-Regular.ttf",
        "size": 36,
    },
    "weather": {
        "location": "",            # e.g. "San Diego, CA" — found by itself (the GPS, or the Pi's internet address), or looked up
        "lat": None,
        "lon": None,
        "country": "",
        "source": "",              # where the location came from: gps | network | typed
        "units": "F",              # F | C
        "refresh_minutes": 10,
        "username": "",            # prompt on screen; empty = same as crypto
    },
    "me": {
        "name": "",
        "job_title": "",
        "location": "",            # where this Pi lives, for the other Pis: "workshop", "upstairs"
        "birthday": "",            # optional, YYYY-MM-DD: adds a couple of stats
        "mood": "auto",            # auto, or one of the faces in the Me tab
        "show_fortunes": True,     # mix fortunes and conversation topics into the comments
        "show_jokes": True,
    },
    "finance": {
        "salary": 0,               # your yearly salary; stays on the Pi, in this file
        "currency": "$",
        "pay_periods": 52,         # paychecks a year: 52 weekly, 26 fortnightly, 24 twice monthly, 12 monthly
        "show_today": True,        # today's pay so far, from the Me schedule
    },
    "schedule": {                  # your day, so the Me and Finance screens know what you're up to
        "workdays": ["mon", "tue", "wed", "thu", "fri"],
        "blocks": [                # "paid" ones earn on the Finance screen
            {"start": "06:00", "end": "17:00", "activity": "Working", "paid": True},
            {"start": "22:30", "end": "06:00", "activity": "Sleeping", "paid": False},
        ],
    },
    "map": {                       # the Map screen: OpenStreetMap around the GPS fix (or the weather spot)
        "zoom": 15,                # 8 (a region) … 18 (a street)
        "style": "lines",          # lines (traced, crisp on e-ink) | shaded (dithered tones)
        "friends": True,           # the other PiE-inks that say where they are, as lettered dots
        "journey": True,           # today's track, as a trail
    },
    "journey": {                   # where it has been: a point every few metres while the GPS has a fix
        "enabled": True,
        "min_metres": 20,          # how far it must move for a new point
        "keep_days": 30,
    },
    "gps": {                       # a USB GPS receiver (u-blox 7 or any NMEA dongle)
        "enabled": "auto",         # auto: used whenever one is plugged in | on | off
        "device": "auto",          # auto | /dev/ttyACM0 … | gpsd | mock
        "set_weather": True,       # move the weather location to wherever it is
    },
    "lcd": {                       # a second screen: a Waveshare SPI LCD (1.69" or 1.9")
        "enabled": False,
        "model": "1.69",           # 1.69 (240x280) | 1.9 (170x320)
        "screen": "system",        # system | clock | weather | crypto | camera | paint | drawing | bot | chat | mirror
        "brightness": 60,
        "light_mode": False,
        "accent": "#62d2ff",       # the numbers; "" for all white
        "rotation": 0,             # 0, 90, 180, 270
        "fps": 2,
        "crypto_days": 1,          # the crypto screen's chart window: 1, 7 or 30 days
        "driver": "st7789",        # or "mock" for running without the hardware
        "cs": 1, "dc": 22, "rst": 27, "bl": 12,   # CE1 and pins the e-ink HAT doesn't use
        "speed_hz": 40000000,      # SPI clock; drop to 16000000 over long or untidy jumpers
    },
    "telegram": {
        "token": "",               # from @BotFather
        "chat_id": "",             # filled in when you send the pairing code
        "chat_name": "",
        "notify_room": True,       # message you when the watcher sees something (with the picture)
        "notify_friends": True,    # and when another PiE-ink sends something
        "notify_remarks": False,   # the bot's unprompted lines too (chatty)
    },
    "home": {                      # Home Assistant: "turn on the tv", "channel 5452", "volume up", "turn off the lamp"
        "enabled": False,
        "url": "http://homeassistant.local:8123",
        "token": "",               # a long-lived access token: your profile → Security → Long-lived access tokens
        "tv": "",                  # the TV's media_player entity, e.g. media_player.living_room_tv
        "remote": "",              # its remote entity (key presses: channel digits, arrows); "" = none
        "preset": "samsung",       # samsung | webos | androidtv | roku | appletv — what the buttons are called
        "names": "living room tv, tv, television",   # what you call it
        "others": True,            # "turn on the lamp": anything else Home Assistant knows by name
        "say_back": True,          # the bot confirms out loud ("TV on")
    },
    "social": {                    # the other PiE-inks
        "peers": [],               # extra Pis by address, for ones the network can't see
        "ai_max_hops": 0,          # turns two bots may take; 0 = until you press Stop
        "speak_messages": True,    # read out messages from friends
        "speak_ai": False,         # each bot says its own lines, so two Pis sound like a conversation
        "indicator": True,         # a small mark on the panel while the bots are talking
        "indicator_corner": "top-right",
        "show_on_screen": False,   # switch the panel to the conversation while they talk
        "show_postcards": True,    # switch the screen to a postcard when one arrives
    },
    "camera_watch": {              # keep an eye on the room
        "enabled": False,
        "interval": 6,             # seconds between looks
        "sensitivity": 12,         # how much has to change, in percent
        "quiet_minutes": 3,        # never remark more often than this
        "describe": True,          # ask the model what it can see
        "speak": True,
        "events": True,            # log what changes: person, package, door, pets (the Camera tab)
        "detector": "auto",        # auto | model (a vision model on Ollama) | basic (OpenCV on the Pi) | off
        "look_gap": 20,            # seconds between looks while things keep moving
        "check_minutes": 15,       # a look now and then even when nothing moves; 0 = only on motion
        "notify_events": "notable",  # Telegram: notable | all | off
        "speak_events": False,     # say the notable ones out loud
    },
    "agent": {                     # the autonomous agent: goals, a look around every few minutes, a decision
        "enabled": False,
        "goals": "Keep an eye on the room.\nTell me if something unusual happens.",
        "every_minutes": 10,
        "look": True,              # take a look through the camera each time
        "sweep": False,            # and look around with it — the camera turns (a camera that pans)
        "can_look": True,          # may turn the camera to look at something, and judge again
        "can_notify": True,        # may message your phone (Telegram)
        "can_say": False,          # may speak out loud
        "can_show": True,          # may put a line on the bot's screen
        "can_home": False,         # may give Home Assistant commands
        "watch_urls": "",          # servers to check, comma-separated: "http://nas.local:5000, 192.168.1.20"
    },
    "cycle": {                     # a slideshow of the screens you tick
        "enabled": False,
        "seconds": 30,
        "screens": ["me", "clock", "weather", "crypto"],
    },
    "audio": {
        "device": "",              # ALSA device, blank = system default
        "volume": 80,
        "piper_voice": "",         # which downloaded Piper voice to use
        "piper_speed": 1.0,
        "voice": "en-gb",          # the plain espeak voice, used until Piper is set up
        "rate": 160,
        "speak_bot": True,         # read the bot's answers out loud
    },
    "listen": {                    # a USB microphone and a wake phrase
        "enabled": False,
        "device": "",              # arecord device, blank = system default
        "wake": "hey cool beans",
        "model": "",               # which downloaded speech model to use
        "timeout": 8,              # seconds to wait for what you say after the wake phrase
        "pause_music": True,       # duck the music while it listens and answers
    },
    "music": {
        "folder": "",              # blank = data/music
        "shuffle": False,
        "repeat": True,
    },
    "llm": {                       # a local Ollama server gives it a voice
        "enabled": False,
        "host": "http://192.168.0.179:11434",
        "model": "",
        "use_backup": False,       # fall back to the Pi's own Ollama when that machine is off
        "backup_host": "http://127.0.0.1:11434",
        "backup_model": "",        # a Pi can only manage a small one
        "temperature": 0.8,
        "max_tokens": 300,         # a reasoning model needs room to think and still answer
        "see": True,               # let it look through the camera when asked about the room
        "vision_model": "",        # a model that can see; blank = the same one
        "max_chars": 90,           # how long a line for the panel may be
        "chatter_minutes": 30,     # how often it remarks on something unprompted; 0 = never
        "timeout_seconds": 180,    # how long to wait for an answer (a cold 20GB model takes a minute)
        "keep_alive": "30m",       # how long Ollama keeps the model in memory between lines
        "no_think": True,          # ask reasoning models to skip the thinking pass
        "persona": "",             # blank = the built-in one
    },
    "message": {
        "text": "Hello, world",
        "source": "message",       # message | joke | fortune | topic | bot
        "speak_new": False,        # read each new joke / fortune / topic out loud
        "bot_face": True,          # a face beside the bot's answers
        "font": "Font.ttc",
        "size": 18,
        "align": "left",           # left | center | right
        "valign": "top",           # top | center | bottom
        "cycle_seconds": 30,       # joke/fortune rotation; 0 = static
    },
    "crypto": {
        "username": "coolbeans",
        "show_faces": True,
        "graph_days": 7,
        "refresh_seconds": 3,
        "coin_seconds": 90,        # seconds per coin
        "show_news": True,
    },
    "holdings": {                  # your crypto: how much of each coin is on the watchlist; this is what it's worth
        "show": True,              # a page in the crypto rotation: the total and how it has moved
        "milestone": 1000000,      # $: a word when the total passes this (you're rich), and when it slips back under; 0 = off
        "move_pct": 10,            # a day's move of this much, up or down, gets a word (a crash, a rally)
        "notify": True,            # to your phone (Telegram)
        "comment": True,           # the bot says something about it (in its own words, with a model)
    },
    "duco": {                      # Duino-Coin: your miners, as a coin in the crypto rotation
        "enabled": False,
        "username": "",            # your Duino-Coin username
        "min_hashrate": 0,         # H/s: a word when the total falls under this; 0 = only when nothing is mining
        "notify": True,            # to your phone (Telegram)
        "comment": True,           # the bot says something about it (in its own words, with a model)
        "refresh_seconds": 60,
    },
    "verus": {                     # Verus on luckpool.net: the same, for a wallet address
        "enabled": False,
        "wallet": "",              # the R… address your miners pay into
        "min_hashrate": 0,         # H/s (a CPU does millions: 5000000 is 5 MH/s)
        "notify": True,
        "comment": True,
        "refresh_seconds": 60,
    },
    "image": {
        "dither": True,
        "live_lcd": False,         # drawing for the LCD: every stroke straight to it (True), or when you press Send
    },
    "reader": {
        "font": "Font.ttc",
        "size": 12,
        "margin": 4,
        "rotation": 0,             # 0 | 90 | 180 | 270: which way the display is turned
        "page_numbers": True,
        "auto_seconds": 0,         # 0 = turn pages by hand
        "zoom": 1.0,               # comics: 1 = page width fits the screen
        "dither": True,
        "auto_contrast": True,
    },
    "ptz": {                       # a camera that turns: pan, tilt and zoom over UVC (an OBSBOT Tiny 2, say)
        "speed": 60,               # degrees a second at full stick (gentler when zoomed in)
        "swap_x": False,           # left and right swapped (mounted upside down)
        "swap_y": False,           # up and down swapped
        "sweep_degrees": 70,       # how far left and right "look around" turns
        "home": None,              # {"pan", "tilt", "zoom"}: saved with "Set home"
        "follow": False,           # an OBSBOT's own tracking: off unless you switch Follow me on
        "lowest": -30,             # never tilt lower than this (degrees): an OBSBOT pointed down goes to sleep
        "gentle": 25,              # degrees a second when it turns by itself (the little friend, a look around)
        "wake": True,              # an OBSBOT asleep (lens down) is woken whenever PiE-ink uses the camera
    },
    "buddy": {                     # the little friend: looks around by itself, says hi, is surprised by what's new
        "enabled": False,
        "every_seconds": 45,       # how often it turns to look somewhere else
        "reach": 70,               # how far it turns each way (degrees), around home
        "rest": 60,                # the camera turns at most this many seconds in ten minutes: the motors rest
        "greet_minutes": 15,       # someone away this long gets a new hello
        "talk": True,              # say its hellos out loud
        "show_face": True,         # put its face on the screen when it reacts
        "night": True,             # sleep at night: no turning, no talking
        "sleep_from": "23:00",
        "sleep_to": "07:00",
    },
    "camera": {
        "source": "auto",          # auto | picamera | usb | mock
        "interval": 1.0,           # seconds between e-ink updates
        "dither": True,
        "fit": "cover",            # cover | contain
        "mirror": False,
        "rotation": 0,             # 0 | 90 | 180 | 270
        "auto_contrast": True,
    },
}

_lock = threading.Lock()


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


ENV_DRIVER = "PIE_INK_DRIVER"   # runtime-only override, never written to disk


_last_user = {}          # last config successfully read or written; used when the disk fails


def _read_user():
    """The user's overrides from disk. A disk error (dying SD card) is logged
    and the last known copy is used, so the app keeps running."""
    global _last_user
    if not os.path.exists(CONFIG_PATH):
        return copy.deepcopy(_last_user)
    try:
        with open(CONFIG_PATH) as f:
            _last_user = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        log.warning("could not read %s (%s); using the in-memory copy", CONFIG_PATH, e)
    return copy.deepcopy(_last_user)


def _migrate(user):
    if "finance" in user:                                 # 2.1 config
        user.setdefault("crypto", user.pop("finance"))
    if "displays" in user:                                # 2.3 multi-screen config
        first = (user.pop("displays") or [{}])[0] or {}
        user.setdefault("startup_mode", first.pop("startup_mode", "clock"))
        first.pop("id", None); first.pop("name", None); first.pop("pins", None)
        user.setdefault("display", first)
    if user.get("startup_mode") == "finance":
        user["startup_mode"] = "me"
    if user.get("startup_mode") in ("snake", "game", "gif"):
        user["startup_mode"] = "clock"
    if user.get("display", {}).get("driver") in ("oled2in23", "mock_oled"):
        user["display"]["driver"] = "epd2in13_V4"
    for section in ("clock", "message", "reader"):     # fonts used to be numbers
        f = user.get(section, {}).get("font")
        if isinstance(f, int) or (isinstance(f, str) and f.isdigit()):
            from .text import LEGACY
            user[section]["font"] = LEGACY.get(int(f), "Font.ttc")
    me = user.get("me", {})                       # money moved off the Me screen to Finance
    if any(k in me for k in ("yearly_income", "currency", "pay_periods")):
        fin = user.setdefault("finance", {})
        for old, new in (("yearly_income", "salary"), ("currency", "currency"), ("pay_periods", "pay_periods")):
            if old in me and not fin.get(new):
                fin[new] = me.pop(old)
    me.pop("hours_per_year", None)
    me.pop("show_income", None)
    # the day's schedule has lived under "me" and then "calendar"; it has its own section now
    sched = user.setdefault("schedule", {})
    cal = user.pop("calendar", {}) or {}
    for src in (cal, me):
        if "schedule" in src and "blocks" not in sched:
            sched["blocks"] = src["schedule"]
        if "workdays" in src and "workdays" not in sched:
            sched["workdays"] = src["workdays"]
    me.pop("schedule", None)
    me.pop("workdays", None)
    if user.get("startup_mode") == "calendar":
        user["startup_mode"] = "me"
    cyc = user.get("cycle", {})
    if isinstance(cyc.get("screens"), list):
        cyc["screens"] = [m for m in cyc["screens"] if m != "calendar"]
    rd = user.get("reader", {})
    if "portrait" in rd:                                  # older portrait toggles
        rd["rotation"] = (270 if rd.pop("portrait_flip", False) else 90) if rd.pop("portrait") else 0
        rd.pop("portrait_flip", None)
    g = user.get("gps", {})
    if isinstance(g, dict) and g.get("enabled") is False:    # the GPS used to wait to be switched on; now it's used when plugged in
        g["enabled"] = "auto"
    elif isinstance(g, dict) and g.get("enabled") is True:
        g["enabled"] = "on"
    w = user.get("weather", {})
    if isinstance(w, dict) and w.get("lat") is not None and "source" not in w:
        w["source"] = "typed"                             # a location from before provenance was kept: yours, so it stays
    cam = user.get("camera", {})
    if isinstance(cam, dict):
        if cam.get("source") == "hdmi":                   # a source that's gone (an HDMI adapter, briefly): back to Auto
            cam["source"] = "auto"
        for key in ("hdmi_mode", "hdmi_width"):
            cam.pop(key, None)
    return user


def load():
    """Return the full effective config (defaults + user overrides)."""
    with _lock:
        user = _migrate(_read_user())
    config = _deep_merge(DEFAULTS, user)
    if os.environ.get(ENV_DRIVER):
        config["display"]["driver"] = os.environ[ENV_DRIVER]
    return config


def save(config):
    """Persist the full config (minus the env driver override). Best effort:
    a disk error is logged and the in-memory copy stays current."""
    global _last_user
    config = copy.deepcopy(config)
    with _lock:
        if os.environ.get(ENV_DRIVER):
            on_disk = _migrate(_read_user()).get("display", {}).get("driver", DEFAULTS["display"]["driver"])
            config["display"]["driver"] = on_disk
        _last_user = copy.deepcopy(config)
        try:
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp, CONFIG_PATH)
            return True
        except OSError as e:
            log.warning("could not save %s (%s); settings kept in memory only", CONFIG_PATH, e)
            return False


def merge(config, patch):
    """Deep-merge a partial dict into a config (no disk access)."""
    return _deep_merge(config, patch)


def update(patch):
    """Deep-merge a partial dict into the config on disk and persist it."""
    config = _deep_merge(load(), patch)
    save(config)
    return config


try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError as e:
    log.warning("could not create %s: %s", DATA_DIR, e)
