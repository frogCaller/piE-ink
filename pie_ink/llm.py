"""Talking to an Ollama server on your network.

Generation never happens while a frame is being drawn — a small model on a
spare PC still takes seconds. A background thread fetches a line and the
screen picks it up on its next refresh, falling back to the built-in
comments whenever the server is unreachable.
"""
import logging
import re
import threading
import time

import requests

log = logging.getLogger(__name__)
TIMEOUT = 180                      # a cold 20GB model can take well over a minute
MAX_CHARS = 90            # a line for the panel; answers you read on the page get more
# how much more the screen can show than the little 250x122 e-ink these limits were sized
# for — the service sets it from the panel (a Whisplay or a 2.7" holds about twice as much)
ROOM = {"factor": 1.0}


def room(chars, most=3.0):
    """A character limit scaled to the screen in use."""
    return int(chars * max(1.0, min(most, ROOM["factor"])))
THINK = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.S | re.I)

_speech = {"text": "", "kind": "", "ts": 0.0, "error": None, "busy": False}
_picture = {"image": None, "what": "", "ts": 0.0}
_lock = threading.Lock()
_on_say = None          # set by the app so new lines can be read out loud


def on_new_line(fn):
    global _on_say
    _on_say = fn


PI = "http://127.0.0.1:11434"        # Ollama on the Pi itself


def normalise(host):
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    if ":" not in host.split("//", 1)[1]:
        host += ":11434"
    return host


def base(conf):
    return normalise(conf.get("host"))


def targets(conf):
    """Where to ask, in order: the machine you chose, then the Pi if you asked
    for that as a back-up. Each has its own model — a Pi can't run a 27B."""
    out = []
    host, model = base(conf), (conf.get("model") or "").strip()
    if host and model:
        out.append((host, model))
    if conf.get("use_backup"):
        backup = normalise(conf.get("backup_host") or PI)
        backup_model = (conf.get("backup_model") or "").strip()
        if backup and backup_model and (backup, backup_model) not in out:
            out.append((backup, backup_model))
    return out


def probe(conf, timeout=6, host=None):
    """-> {ok, models, error}. Used by the page to fill the model pickers."""
    url = normalise(host) if host else base(conf)
    if not url:
        return {"ok": False, "models": [], "error": "no address set"}
    try:
        r = requests.get(f"{url}/api/tags", timeout=timeout)
        r.raise_for_status()
        models = sorted(m.get("name", "") for m in r.json().get("models", []))
        return {"ok": True, "models": [m for m in models if m], "error": None}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "models": [], "error": f"can't reach {url}"}
    except Exception as e:
        return {"ok": False, "models": [], "error": str(e)[:120]}


def tidy(text, limit=MAX_CHARS):
    """Tidy a reply: no markdown, no quotes, no reasoning block, joined into
    one paragraph. `limit` is how much room there is to say it."""
    text = THINK.sub(" ", text or "")
    text = re.sub(r"^\s*<think>.*", "", text, flags=re.S | re.I)      # unclosed block
    text = text.strip()
    for junk in ("```", "**", "*", "_"):
        text = text.replace(junk, "")
    line = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
    line = line.strip('"“”\'')
    if limit and len(line) > limit:
        cut = line[:limit].rsplit(" ", 1)[0]
        line = cut.rstrip(",;:") + "…"
    return line


def _post(url, body, timeout, limit=MAX_CHARS):
    try:
        r = requests.post(f"{url}/api/generate", json=body, timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise ValueError(f"can't reach {url} — is Ollama listening on the network?")
    except requests.exceptions.ReadTimeout:
        raise ValueError(f"no answer in {timeout}s — a big model may need longer")
    if r.status_code == 400 and "think" in r.text.lower():
        body = {k: v for k, v in body.items() if k != "think"}    # older Ollama
        r = requests.post(f"{url}/api/generate", json=body, timeout=timeout)
    if r.status_code == 404:
        raise ValueError(f"the server doesn't have the model '{body.get('model')}'")
    if r.status_code >= 400:
        detail = ""
        try:
            detail = (r.json().get("error") or "").strip()
        except ValueError:
            detail = (r.text or "").strip()[:160]
        if body.get("images") and ("image" in detail.lower() or "vision" in detail.lower()
                                   or "multimodal" in detail.lower() or not detail):
            raise ValueError(f"{body.get('model')} can't look at pictures — choose a model that can "
                             "see (Gemma, LLaVA, Qwen-VL) under 'Eyes' in the bot panel")
        raise ValueError(detail or f"the server said {r.status_code}")
    data = r.json()
    return tidy(data.get("response", ""), limit), (data.get("thinking") or "").strip()


def generate(conf, system, prompt, timeout=None, images=None, limit=None):
    """One completion, blocking. Asks the machine you chose; if it can't be
    reached and you set a back-up, asks that instead."""
    where = targets(conf)
    if not where:
        if not base(conf):
            raise ValueError("no Ollama address set — put it in the bot panel and press Check")
        raise ValueError("no model chosen — open the bot panel, press Check, then pick one")
    last = None
    for i, (url, model) in enumerate(where):
        try:
            return _one(conf, url, model, system, prompt, timeout, images, limit)
        except ValueError as e:
            last = e
            if i + 1 < len(where) and "reach" in str(e):
                log.info("%s isn't answering — trying %s", url, where[i + 1][0])
                continue
            raise
    raise last


def _one(conf, url, model, system, prompt, timeout=None, images=None, limit=None):
    """One attempt against one machine."""
    timeout = timeout or int(conf.get("timeout_seconds", TIMEOUT))
    if images:
        model = (conf.get("vision_model") or "").strip() or model
    body = {
        "model": model, "prompt": prompt, "system": system, "stream": False,
        "options": {"temperature": float(conf.get("temperature", 0.8)),
                    "num_predict": int(conf.get("max_tokens", 300))},
    }
    keep = (conf.get("keep_alive") or "").strip()
    if keep:
        body["keep_alive"] = keep               # stay warm, so the next line isn't another cold start
    if images:
        body["images"] = images                 # base64 stills for a model that can see
    if conf.get("no_think", True):
        body["think"] = False                   # Qwen3 and friends: skip the reasoning pass

    # remarks on the screen stay short by design; a bigger screen gets a little more, not a lot
    limit = limit or room(int(conf.get("max_chars", MAX_CHARS) or MAX_CHARS), most=1.6)
    text, thinking = _post(url, body, timeout, limit)
    if text:
        return text
    if thinking:
        # a reasoning model spent the whole budget on its working: ask again
        # with the cap off and the thinking switched off as firmly as we can
        log.info("llm: only reasoning came back, retrying without a token cap")
        retry = dict(body)
        retry["options"] = dict(body["options"], num_predict=-1)
        retry.pop("think", None)
        retry["prompt"] = prompt + "\n\nAnswer directly. Do not show your reasoning."
        text, thinking = _post(url, retry, timeout, limit)
        if text:
            return text
        raise ValueError(f"{model} only returned its reasoning — try a non-reasoning model, "
                         "or raise llm.max_tokens")
    return ""


def latest():
    with _lock:
        return dict(_speech)


def set_picture(image, what="picture"):
    """Remember what it was just looking at, so the screen can show it too."""
    with _lock:
        _picture.update(image=image.copy() if image else None, what=what, ts=time.time())


def picture(max_age=600):
    """The picture it last talked about, if it is still fresh."""
    with _lock:
        if _picture["image"] is not None and time.time() - _picture["ts"] <= max_age:
            return dict(_picture)
    return None


def set_speech(text, kind="idle", error=None):
    with _lock:
        if text:
            _speech.update(text=text, kind=kind, ts=time.time())
        _speech["error"] = error
    if text and _on_say:
        try:
            _on_say(text, kind)
        except Exception:
            log.warning("couldn't pass the line on", exc_info=True)


def busy():
    with _lock:
        return _speech["busy"]


def say_async(conf, system, prompt, kind="idle", on_done=None, images=None):
    """Fetch a line in the background. One at a time; extra calls are dropped."""
    with _lock:
        if _speech["busy"]:
            return False
        _speech["busy"] = True

    def run():
        try:
            text = generate(conf, system, prompt, images=images)
            if text:
                set_speech(text, kind)
            else:
                set_speech("", kind, "the model said nothing")
        except Exception as e:
            log.warning("llm: %s", e)
            set_speech("", kind, str(e)[:120])
        finally:
            with _lock:
                _speech["busy"] = False
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    threading.Thread(target=run, daemon=True, name="llm").start()
    return True
