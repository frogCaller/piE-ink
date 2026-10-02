"""Telegram, so the Pi can reach you and you can reach it from anywhere.

Make a bot with @BotFather, paste its token into Settings, then send the
pairing code shown there to your bot. From then on that one chat can talk
to it — ask it things, put a screen up, take a photo — and it can message
you when something happens. Anyone else who finds the bot is ignored.

This uses the Bot API directly over HTTPS with long polling; nothing else
to install, and no server of ours facing the internet.
"""
import logging
import secrets
import threading
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
_state = {"code": None, "chat": None, "name": "", "error": None, "last_in": 0.0, "last_out": 0.0,
          "running": False}
_stop = threading.Event()
_handler = None
_on_tap = None
_thread = None


# -- talking to Telegram -------------------------------------------------------------

def _url(conf, method):
    base = (conf.get("api_base") or API).rstrip("/")
    return f"{base}/bot{(conf.get('token') or '').strip()}/{method}"


def _call(conf, method, params=None, wait=15):
    r = requests.post(_url(conf, method), json=params or {}, timeout=wait)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"telegram said {r.status_code}")
    return data.get("result")


def configured(conf):
    return bool((conf.get("token") or "").strip())


def paired(conf):
    return bool(conf.get("chat_id"))


def keyboard(rows):
    """Buttons under a message: rows of (label, data). Tapping one sends the
    data back (see on_tap in start)."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data[:64]} for label, data in row] for row in rows]}


def send_text(conf, text, chat_id=None, buttons=None):
    """A message to you (or to the chat given), with buttons under it if
    given (rows of (label, data)). Returns (ok, why)."""
    chat = chat_id or conf.get("chat_id")
    if not configured(conf) or not chat:
        return False, "Telegram isn't set up"
    try:
        params = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = keyboard(buttons)
        _call(conf, "sendMessage", params)
        _state["last_out"] = time.time()
        return True, "sent"
    except Exception as e:
        _state["error"] = str(e)[:120]
        return False, str(e)[:120]


def send_photo(conf, png_bytes, caption="", chat_id=None):
    chat = chat_id or conf.get("chat_id")
    if not configured(conf) or not chat:
        return False, "Telegram isn't set up"
    try:
        r = requests.post(_url(conf, "sendPhoto"), data={"chat_id": chat, "caption": caption[:1000]},
                          files={"photo": ("pie-ink.png", png_bytes, "image/png")}, timeout=30)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or "couldn't send the photo")
        _state["last_out"] = time.time()
        return True, "sent"
    except Exception as e:
        _state["error"] = str(e)[:120]
        return False, str(e)[:120]


def pairing_code():
    if not _state["code"]:
        _state["code"] = secrets.token_hex(3).upper()
    return _state["code"]


def status(conf):
    return {"configured": configured(conf), "paired": paired(conf), "chat_name": conf.get("chat_name", ""),
            "code": None if paired(conf) else pairing_code(), "error": _state["error"],
            "running": _state["running"],
            "last_in": _state["last_in"], "last_out": _state["last_out"]}


# -- receiving ---------------------------------------------------------------------------

def start(get_conf, handler, on_pair, on_tap=None):
    """Poll for messages. `handler(text)` answers a paired chat — with text,
    (png, caption) for a picture, or {"text", "buttons"} for a message with
    buttons under it; `on_tap(data)` answers a tapped button with a line that
    pops up on the phone; `on_pair(chat_id, name)` records the first person
    to send the pairing code."""
    global _handler, _on_tap, _thread
    _handler = handler
    _on_tap = on_tap
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(get_conf, on_pair), daemon=True, name="telegram")
    _thread.start()


def stop():
    _stop.set()


def _run(get_conf, on_pair):
    try:
        _poll(get_conf, on_pair)
    except Exception:
        log.exception("telegram stopped")
    finally:
        _state["running"] = False


def _poll(get_conf, on_pair):
    offset = None
    backoff = 3
    while not _stop.is_set():
        conf = get_conf()
        if not configured(conf):
            _state["running"] = False
            time.sleep(5)
            continue
        _state["running"] = True
        try:
            params = {"timeout": 25}                  # Telegram's long-poll window
            if offset:
                params["offset"] = offset
            updates = _call(conf, "getUpdates", params, wait=40)
            backoff = 3
        except Exception as e:
            _state["error"] = str(e)[:120]
            log.info("telegram: %s", _state["error"])
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
            continue
        _state["error"] = None
        for up in updates or []:
            offset = up.get("update_id", (offset or 0)) + 1
            try:
                _one(up, get_conf, on_pair)
            except Exception:                        # one odd update must not stop the polling
                log.exception("telegram update")
    _state["running"] = False


def _one(up, get_conf, on_pair):
    """Handle one update from Telegram: a tap on a button, or a message."""
    if up.get("callback_query"):
        _tapped(get_conf(), up["callback_query"])
        return
    msg = up.get("message") or up.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    who = chat.get("first_name") or chat.get("username") or str(chat_id)
    conf = get_conf()
    if not paired(conf):
        code = pairing_code()
        if code and code.lower() in text.lower():
            on_pair(chat_id, who)
            _state["code"] = None
            send_text(get_conf(), f"Paired. This is {who}'s PiE-ink now — say hello, or /help.",
                      chat_id=chat_id)
        else:
            send_text(conf, "This PiE-ink isn't paired with you. If it's yours, send the "
                            "pairing code shown in its Settings → Telegram.", chat_id=chat_id)
        return
    if str(chat_id) != str(conf.get("chat_id")):
        log.info("telegram: ignoring %s (not the paired chat)", who)
        return
    _state["last_in"] = time.time()
    try:
        reply = _handler(text)
    except Exception as e:
        log.exception("telegram handler")
        reply = f"Something went wrong: {str(e)[:100]}"
    if isinstance(reply, tuple):                 # (png_bytes, caption)
        send_photo(conf, reply[0], reply[1], chat_id=chat_id)
    elif isinstance(reply, dict):                # text with buttons under it
        send_text(conf, reply.get("text") or "…", chat_id=chat_id, buttons=reply.get("buttons"))
    elif reply:
        send_text(conf, reply, chat_id=chat_id)


def _tapped(conf, cq):
    """A button under one of our messages was tapped: only from the paired
    chat, and always answered, or the phone shows a spinner for a while."""
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    answer = {"callback_query_id": cq.get("id")}
    if not paired(conf) or str(chat_id) != str(conf.get("chat_id")):
        answer["text"] = "Not your PiE-ink."
    elif not _on_tap:
        answer["text"] = "Nothing to do with that."
    else:
        _state["last_in"] = time.time()
        try:
            reply = _on_tap(cq.get("data") or "") or "Done."
        except Exception as e:
            log.exception("telegram tap")
            reply = f"Something went wrong: {str(e)[:100]}"
        answer["text"] = str(reply)[:200]
        if str(reply).startswith(("Couldn't", "No TV", "Home Assistant")):
            answer["show_alert"] = True                   # a failure is worth a proper popup
    try:
        _call(conf, "answerCallbackQuery", answer, wait=10)
    except Exception as e:
        log.info("telegram: answering a tap: %s", e)
