"""Finding other PiE-inks on the same network.

Every instance shouts a small JSON beacon over UDP broadcast a few times a
minute and listens for everyone else's. No configuration, nothing leaves the
LAN, and a Pi that goes quiet drops off the list after half a minute.
"""
import json
import logging
import socket
import threading
import time
import uuid

from . import __version__

log = logging.getLogger(__name__)
PORT = 5151
INTERVAL = 5.0
EXPIRE = 30.0
_id = uuid.uuid4().hex[:8]
_peers = {}
_lock = threading.Lock()
_status = lambda: {}     # noqa: E731  set by start()
_where = lambda: None    # noqa: E731  set by start(): where this Pi is, if it knows
_caps = lambda: {}       # noqa: E731  set by the app: what this Pi has (camera, GPS, speaker…) and where it lives
_web_port = 5000


def _my_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def _beacon():
    st = _status() or {}
    msg = {"pie": 1, "id": _id, "host": socket.gethostname(), "port": _web_port,
           "version": __version__, "panel": st.get("panel", {}).get("label", ""), "mode": st.get("mode")}
    try:
        pos = _where()
    except Exception:
        pos = None
    if pos:
        msg["where"] = {"lat": round(pos["lat"], 5), "lon": round(pos["lon"], 5), "place": (pos.get("place") or "")[:60],
                        "source": pos.get("source")}
    try:
        caps = _caps() or {}
    except Exception:
        caps = {}
    if caps:
        msg["caps"] = caps
    return json.dumps(msg).encode()


def _send_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            s.sendto(_beacon(), ("255.255.255.255", PORT))
        except OSError as e:
            log.debug("beacon send failed: %s", e)
        except Exception:                        # a bad status must not end the beacon for good
            log.exception("beacon")
        time.sleep(INTERVAL)


def _recv_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    try:
        s.bind(("", PORT))
    except OSError as e:
        log.warning("friends: can't listen on UDP port %s (%s) — other PiE-inks won't be seen", PORT, e)
        return
    while True:
        try:
            data, (ip, _) = s.recvfrom(2048)
            msg = json.loads(data.decode())
            if msg.get("pie") != 1 or msg.get("id") == _id:
                continue
            with _lock:
                _peers[msg["id"]] = {"id": msg["id"], "host": msg.get("host", ip), "ip": ip, "port": int(msg.get("port", 5000)),
                                     "version": msg.get("version", ""), "panel": msg.get("panel", ""),
                                     "mode": msg.get("mode"), "where": msg.get("where") or None,
                                     "caps": msg.get("caps") if isinstance(msg.get("caps"), dict) else {}, "seen": time.time()}
        except Exception as e:
            log.debug("beacon receive: %s", e)
            time.sleep(0.5)


def where_from(fn):
    """A function that says where this Pi is ({"lat", "lon", "place",
    "source"} or None); it goes into the beacon so friends can map us."""
    global _where
    _where = fn


def caps_from(fn):
    """A function that says what this Pi has (see mesh.my_caps); it goes
    into the beacon so friends can ask for the Pi with a camera."""
    global _caps
    _caps = fn


def start(status_fn, web_port=5000, where_fn=None):
    global _status, _web_port
    _status, _web_port = status_fn, web_port
    if where_fn:
        where_from(where_fn)
    threading.Thread(target=_recv_loop, daemon=True, name="friends-rx").start()
    threading.Thread(target=_send_loop, daemon=True, name="friends-tx").start()


def peers():
    now = time.time()
    with _lock:
        for k in [k for k, p in _peers.items() if now - p["seen"] > EXPIRE]:
            del _peers[k]
        out = sorted(_peers.values(), key=lambda p: p["host"])
    for p in out:
        p["url"] = f"http://{p['ip']}:{p['port']}"
        p["ago"] = int(now - p["seen"])
    return out


def me():
    return {"id": _id, "host": socket.gethostname(), "ip": _my_ip(), "port": _web_port, "version": __version__}
