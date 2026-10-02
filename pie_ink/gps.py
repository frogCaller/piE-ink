"""A USB GPS receiver — a u-blox 7 dongle, or anything that talks NMEA over
a serial port.

Plug it in: it is used the moment it appears (Auto, on the Weather tab —
or On to insist, Off to leave it alone). It is read on a thread
of its own, found again whenever it is unplugged and plugged back in, and
the position goes to the weather (if you let it), to the bot, and to the GPS
screen. Nothing needs gpsd: the NMEA sentences are read straight off the
port. If gpsd is running and holding the port, it is read through gpsd
instead, so either way works.

A cold receiver takes a minute or more to find satellites, and needs a view
of the sky — a window sill is fine, the middle of a room usually is not.
"""
import collections
import glob
import json
import logging
import math
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone

from . import cache

log = logging.getLogger(__name__)

STALE_AFTER = 300            # a fix older than this no longer counts as "where we are"
GPSD_PORT = 2947
BAUD = 9600                  # what a u-blox 7 speaks by default (ignored over USB anyway)
BAUDS = (9600, 4800, 38400, 115200, 57600, 19200)   # tried in turn when a port sends only garbage
UA = "pie-ink/2.0 (https://github.com/frogCaller/piE-ink)"
UDEV_RULE = "/etc/udev/rules.d/99-pie-ink-gps.rules"
# USB vendors that usually mean a GPS receiver or the serial chip inside one
GPS_VENDORS = {"1546": "u-blox", "067b": "Prolific serial chip", "10c4": "Silicon Labs serial chip",
               "1a86": "CH340 serial chip", "0403": "FTDI serial chip", "091e": "Garmin", "1bc7": "Telit",
               "0e8d": "MediaTek", "1d50": "OpenMoko", "05c6": "Qualcomm"}


# -- finding the receiver ---------------------------------------------------------------------

def mode(conf):
    """"auto" (read a receiver whenever one is plugged in), "on" (insist, and say
    when there isn't one) or "off" — from whatever the setting holds: the old
    True/False, or the words."""
    v = (conf or {}).get("enabled", "auto")
    if v is True or str(v).strip().lower() in ("on", "true", "1", "yes"):
        return "on"
    if v is False or v is None or str(v).strip().lower() in ("off", "false", "0", "no", ""):
        return "off"
    return "auto"


def candidates():
    """Serial ports that could be a GPS, best guess first: the udev symlink
    setup.sh makes for a u-blox, then anything by-id that says u-blox or GPS,
    then every USB serial port there is."""
    found = []
    for pattern in ("/dev/gps0", "/dev/serial/by-id/*u-blox*", "/dev/serial/by-id/*[Gg][Pp][Ss]*",
                    "/dev/ttyACM*", "/dev/ttyUSB*"):
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real not in [os.path.realpath(p) for p in found]:
                found.append(path)
    return found


def _gpsd_alive():
    try:
        socket.create_connection(("127.0.0.1", GPSD_PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


def gpsd_devices():
    """The ports gpsd is reading, if it is running: [] when it has none (its
    socket is often listening with nothing behind it), None when it isn't
    there at all."""
    if not _gpsd_alive():
        return None
    try:
        sock = socket.create_connection(("127.0.0.1", GPSD_PORT), timeout=2)
        sock.sendall(b"?DEVICES;\n")
        buf, end = b"", time.time() + 2
        while time.time() < end and b'"class":"DEVICES"' not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        sock.close()
    except OSError:
        return []
    for raw in buf.split(b"\n"):
        try:
            msg = json.loads(raw.decode("utf-8", "ignore"))
        except ValueError:
            continue
        if msg.get("class") == "DEVICES":
            return [d.get("path") for d in msg.get("devices", []) if d.get("path")]
    return []


# -- NMEA -----------------------------------------------------------------------------------

def checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return c


def valid(line):
    """A well-formed sentence with a matching checksum."""
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, given = line[1:].partition("*")
    try:
        return int(given[:2], 16) == checksum(body)
    except ValueError:
        return False


def _coord(value, hemi):
    """ddmm.mmmm / dddmm.mmmm with N/S/E/W -> signed degrees."""
    if not value or not hemi:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    deg = int(v // 100)
    minutes = v - deg * 100
    out = deg + minutes / 60
    return -out if hemi in ("S", "W") else out


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Parser:
    """Turns NMEA sentences into one dict of the latest known state."""

    def __init__(self):
        self.state = {}
        self._gsv = {}           # talker -> {prn: snr} being assembled
        self._gsv_done = {}      # talker -> the last complete set

    def feed(self, line):
        line = line.strip()
        if not valid(line):
            return
        body = line[1:].split("*", 1)[0]
        parts = body.split(",")
        kind = parts[0][2:]      # GPGGA -> GGA (the talker can be GP, GN, GL, GA, BD...)
        talker = parts[0][:2]
        st = self.state
        st["last_sentence"] = time.time()
        if kind == "GGA" and len(parts) >= 10:
            lat, lon = _coord(parts[2], parts[3]), _coord(parts[4], parts[5])
            quality = int(parts[6] or 0)
            st["quality"] = quality
            st["sats_used"] = int(parts[7] or 0)
            st["hdop"] = _num(parts[8])
            st["alt_m"] = _num(parts[9])
            if quality > 0 and lat is not None and lon is not None:
                st.update(lat=lat, lon=lon, fix_at=time.time())
            self._utc(parts[1])
        elif kind == "RMC" and len(parts) >= 10:
            ok = parts[2] == "A"
            lat, lon = _coord(parts[3], parts[4]), _coord(parts[5], parts[6])
            knots, course = _num(parts[7]), _num(parts[8])
            if ok and lat is not None and lon is not None:
                st.update(lat=lat, lon=lon, fix_at=time.time())
                if knots is not None:
                    st["speed_kmh"] = knots * 1.852
                st["course"] = course
            st["rmc_ok"] = ok
            self._utc(parts[1], parts[9])
        elif kind == "VTG" and len(parts) >= 8:
            kmh = _num(parts[7])
            if kmh is not None:
                st["speed_kmh"] = kmh
            if _num(parts[1]) is not None:
                st["course"] = _num(parts[1])
        elif kind == "GSA" and len(parts) >= 18:
            st["fix_type"] = int(parts[2] or 1)        # 1 none, 2 2D, 3 3D
            st["pdop"], st["hdop"], st["vdop"] = _num(parts[15]), _num(parts[16]), _num(parts[17])
        elif kind == "GSV" and len(parts) >= 4:
            try:
                total, num, in_view = int(parts[1]), int(parts[2]), int(parts[3] or 0)
            except ValueError:
                return
            if num == 1:
                self._gsv[talker] = {}
            sats = self._gsv.setdefault(talker, {})
            for i in range(4, min(len(parts), 20), 4):
                prn = parts[i]
                if prn:
                    sats[f"{talker}{prn}"] = int(parts[i + 3] or 0) if i + 3 < len(parts) and parts[i + 3].isdigit() else 0
            if num == total:
                self._gsv_done[talker] = dict(sats)
                merged = {}
                for d in self._gsv_done.values():
                    merged.update(d)
                st["snr"] = merged
                st["sats_view"] = sum(1 for _ in merged)
            st.setdefault("sats_view", in_view)

    def _utc(self, hhmmss, ddmmyy=None):
        if hhmmss and len(hhmmss) >= 6:
            self.state["utc"] = f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"
        if ddmmyy and len(ddmmyy) == 6:
            self.state["date"] = f"20{ddmmyy[4:6]}-{ddmmyy[2:4]}-{ddmmyy[0:2]}"


def sentence(body):
    """A body like 'GPGGA,...' as a full sentence with its checksum."""
    return f"${body}*{checksum(body):02X}"


# -- the pretend receiver, for running without one ---------------------------------------------

class MockStream:
    """Acts like a dongle on a window sill: nothing for a moment, then a fix
    that wanders a few metres, with a handful of satellites in view."""

    name = "pretend receiver"

    def __init__(self, lat=32.7157, lon=-117.1611, fix_after=2.0):
        self.lat, self.lon = lat, lon
        self.t0 = time.time()
        self.fix_after = fix_after
        self.n = 0

    def _nmea_coord(self, deg, lat=True):
        hemi = ("N" if deg >= 0 else "S") if lat else ("E" if deg >= 0 else "W")
        deg = abs(deg)
        d = int(deg)
        m = (deg - d) * 60
        return (f"{d:02d}{m:07.4f}" if lat else f"{d:03d}{m:07.4f}"), hemi

    def lines(self):
        """The next second's worth of sentences."""
        self.n += 1
        now = datetime.now(timezone.utc)
        hms, dmy = now.strftime("%H%M%S"), now.strftime("%d%m%y")
        fixed = time.time() - self.t0 >= self.fix_after
        wobble = math.sin(self.n / 7) * 0.00004
        lat, lon = self.lat + wobble, self.lon + wobble / 2
        la, lah = self._nmea_coord(lat, True)
        lo, loh = self._nmea_coord(lon, False)
        out = []
        if fixed:
            out.append(sentence(f"GPGGA,{hms},{la},{lah},{lo},{loh},1,08,1.1,42.3,M,-34.2,M,,"))
            out.append(sentence(f"GPRMC,{hms},A,{la},{lah},{lo},{loh},0.4,54.7,{dmy},,,A"))
            out.append(sentence("GPGSA,A,3,04,07,09,16,26,27,30,31,,,,,1.8,1.1,1.4"))
        else:
            out.append(sentence(f"GPGGA,{hms},,,,,0,00,99.99,,M,,M,,"))
            out.append(sentence(f"GPRMC,{hms},V,,,,,,,{dmy},,,N"))
            out.append(sentence("GPGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99"))
        out.append(sentence("GPGSV,2,1,08,04,62,150,41,07,45,300,38,09,20,080,29,16,70,010,44"))
        out.append(sentence("GPGSV,2,2,08,26,33,220,35,27,15,330,22,30,55,120,40,31,08,050,18"))
        return out

    def close(self):
        pass


# -- the receiver ---------------------------------------------------------------------------

class Gps:
    def __init__(self):
        self._lock = threading.Lock()
        self._parser = Parser()
        self.conf = {}
        self.thread = None
        self.stop_flag = threading.Event()
        self.on_fix = None               # (state) -> None, called every few seconds while there is a fix
        self.device = None               # what is being read right now
        self.source = None               # "serial" | "gpsd" | "mock"
        self.error = None
        self.place = None                # the last reverse-geocoded name, if any
        self._last_notify = 0.0
        self._notifying = False
        self.baud = BAUD                 # what the port is being read at
        self.good = 0                    # valid sentences since the port was opened
        self.bad = 0                     # lines that weren't NMEA (garbage, fragments)
        self.drops = collections.deque(maxlen=100)   # when the receiver vanished off the USB bus mid-read
        self._opened = None              # the port as it was opened: (path, the device it pointed at)
        self.seen = False                # a receiver has been read since the reader started (Auto: it counts from then)

    # -- control ------------------------------------------------------------------------------

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def mode(self):
        return mode(self.conf)

    def start(self, conf):
        self.conf = dict(conf or {})
        if self.mode() == "off":
            self.stop()
            return False
        if self.running():
            return True
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="gps")
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=4)
        self.device = self.source = None

    def update(self, conf):
        old, self.conf = self.conf, dict(conf or {})
        if self.mode() == "off":
            self.stop()
            return
        if old.get("device") != self.conf.get("device") and self.running():
            self.stop()
        self.start(self.conf)

    # -- what is known ---------------------------------------------------------------------------

    def state(self):
        with self._lock:
            return dict(self._parser.state)

    def position(self, max_age=STALE_AFTER):
        """(lat, lon) if there is a fix fresh enough to trust, else None."""
        st = self.state()
        if st.get("lat") is not None and time.time() - st.get("fix_at", 0) <= max_age:
            return st["lat"], st["lon"]
        return None

    def status(self):
        st = self.state()
        now = time.time()
        fix_age = (now - st["fix_at"]) if st.get("fix_at") else None
        has_fix = fix_age is not None and fix_age <= STALE_AFTER
        quality = st.get("fix_type", 0)
        if has_fix:
            fix = "3D" if quality == 3 else "2D" if quality == 2 else "yes"
        else:
            fix = "none"
        drops = [t for t in list(self.drops) if now - t < 3600]
        which = self.mode()
        return {
            # Auto counts as on once a receiver has been seen: until then there is nothing to report
            "enabled": which == "on" or (which == "auto" and (self.device is not None or self.seen)),
            "mode": which,
            "running": self.running(),
            "device": self.device, "source": self.source, "error": self.error,
            "drops": len(drops), "last_drop_ago": round(now - drops[-1]) if drops else None,
            "connected": bool(self.device) and st.get("last_sentence", 0) > now - 10,
            "last_sentence_age": round(now - st["last_sentence"]) if st.get("last_sentence") else None,
            "baud": self.baud if self.source == "serial" else None,
            "sentences": self.good, "garbage": self.bad,
            "fix": fix, "fix_age": round(fix_age) if fix_age is not None else None,
            "lat": st.get("lat") if has_fix or st.get("lat") else None,
            "lon": st.get("lon") if has_fix or st.get("lon") else None,
            "alt_m": st.get("alt_m"), "speed_kmh": st.get("speed_kmh"), "course": st.get("course"),
            "sats_used": st.get("sats_used", 0), "sats_view": st.get("sats_view", 0),
            "snr": st.get("snr", {}), "hdop": st.get("hdop"),
            "utc": st.get("utc"), "date": st.get("date"),
            "place": self.place,
        }

    # -- the reading loop ------------------------------------------------------------------------

    def _run(self):
        log.info("gps: looking for a receiver" + (" (whenever one is plugged in)" if self.mode() == "auto" else ""))
        while not self.stop_flag.is_set():
            try:
                src = self._open()
            except Exception as e:
                self.error = f"{type(e).__name__}: {str(e)[:120]}"
                src = None
            if src is None:
                self.device = self.source = None
                if self._just_dropped():                     # it's usually back in a second or two
                    self.error = "the receiver dropped off the USB bus — looking for it again"
                self.stop_flag.wait(1 if self._just_dropped() else 5)
                continue
            if not self.seen:
                log.info("gps: a receiver is plugged in — using it")
            self.seen = True
            log.info("gps: reading %s", self.device)         # the last error stands until a sentence arrives
            try:
                self._read(src)
            except Exception as e:
                if self._vanished():
                    self.drops.append(time.time())
                    self.error = "the receiver dropped off the USB bus — looking for it again"
                    n = sum(1 for t in self.drops if time.time() - t < 3600)
                    log.warning("gps: %s dropped off the USB bus (%s in the last hour)", self.device,
                                "once" if n == 1 else f"{n} times")
                else:
                    self.error = f"{type(e).__name__}: {str(e)[:120]}"
                    log.warning("gps: %s (%s)", self.error, self.device)
            finally:
                try:
                    src.close()
                except Exception:
                    pass
            self.device = self.source = None
            self.stop_flag.wait(1 if self._just_dropped() else 3)
        log.info("gps stopped")

    def _just_dropped(self):
        return bool(self.drops) and time.time() - self.drops[-1] < 30

    def _vanished(self):
        """After a read failed: is the port gone, or now pointing at another device (the receiver dropped off the
        bus and came back as a new one while we held the old)? udev takes a moment to tidy up: asked twice."""
        if self.source != "serial" or not self._opened:
            return False
        path, real = self._opened
        for _ in range(2):
            if not os.path.exists(path) or os.path.realpath(path) != real:
                return True
            if self.stop_flag.wait(0.5):
                break
        return False

    def _open(self):
        want = str(self.conf.get("device") or "auto")
        self.good = self.bad = 0
        if want == "mock":
            self.device, self.source = "mock", "mock"
            return MockStream()
        if want == "gpsd":
            g = self._open_gpsd()
            if g is None:
                self.error = "gpsd isn't running (sudo systemctl start gpsd), or pick Auto"
            return g
        ports = [want] if want != "auto" else candidates()
        # gpsd, when it is installed, grabs a USB receiver the moment it is plugged in; two
        # readers on one port each get half the bytes, so when gpsd has it we read through gpsd
        held = gpsd_devices() or []
        if any(os.path.realpath(p) in [os.path.realpath(h) for h in held] for p in ports):
            g = self._open_gpsd()
            if g is not None:
                return g
        for port in ports:
            try:
                import serial
            except ImportError:
                self.error = "python3-serial is not installed (sudo apt install python3-serial)"
                break
            try:
                s = serial.Serial(port, BAUD, timeout=2)
                self.device, self.source, self.baud = port, "serial", BAUD
                self._opened = (port, os.path.realpath(port))
                return s
            except Exception as e:
                words = str(e)
                if "ermission" in words:
                    words = "permission denied — this user isn't in the dialout group (sudo usermod -aG dialout $USER, then restart pie-ink)"
                self.error = f"{port}: {words[:140]}"
                if "busy" in words.lower() or "resource" in words.lower():
                    g = self._open_gpsd()               # gpsd has it: read through gpsd then
                    if g is not None:
                        return g
        if want == "auto" and not ports:
            g = self._open_gpsd()
            if g is not None and held:
                return g
            if g is not None:
                g.close()
            self.error = "no receiver found — plug it in"
        return None

    def _open_gpsd(self):
        if not _gpsd_alive():
            return None
        s = socket.create_connection(("127.0.0.1", GPSD_PORT), timeout=3)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        self.device, self.source = f"gpsd on port {GPSD_PORT}", "gpsd"
        return _GpsdStream(s)

    def _read(self, src):
        parser = self._parser
        quiet_since = time.time()
        serial_port = not isinstance(src, (MockStream, _GpsdStream))
        good_since = time.time()                # when a valid sentence last arrived (or the port opened)
        baud_at = 0                             # where we are in BAUDS, when hunting
        while not self.stop_flag.is_set():
            if isinstance(src, MockStream):
                lines = src.lines()
                self.stop_flag.wait(1.0)
            elif isinstance(src, _GpsdStream):
                lines = src.lines()
            else:
                raw = src.readline()
                lines = [raw.decode("ascii", "ignore")] if raw else []
            if not lines:
                if time.time() - quiet_since > 15:
                    raise RuntimeError("the port went silent — not a byte in 15 s (unplugged? "
                                       "ModemManager probing it? no power to it?)")
                continue
            quiet_since = time.time()
            with self._lock:
                for line in lines:
                    try:
                        if isinstance(line, dict):
                            self._from_gpsd(line)
                            self.good += 1
                            self.error = None
                        elif valid(line.strip()):
                            parser.feed(line)
                            self.good += 1
                            good_since = time.time()
                            self.error = None
                        else:
                            self.bad += 1
                    except Exception as e:          # one odd sentence must not cost the port
                        self.bad += 1
                        log.debug("gps: bad sentence %r: %s", line[:60], e)
            # bytes but no sentences for a while: the wrong speed (a dongle that isn't a
            # u-blox, or a serial adapter) — try the next one; or someone else is reading the
            # same port and we each get fragments, which no speed will fix
            if serial_port and self.good == 0 and time.time() - good_since > 6:
                baud_at += 1
                if baud_at < len(BAUDS):
                    self.baud = BAUDS[baud_at]
                    try:
                        src.baudrate = self.baud
                        src.reset_input_buffer()
                    except Exception as e:
                        raise RuntimeError(f"couldn't change speed: {e}")
                    log.info("gps: nothing readable on %s — trying %s baud", self.device, self.baud)
                    good_since = time.time()
                else:
                    raise RuntimeError("data but no NMEA at any speed — is another program (gpsd, ModemManager) "
                                       "reading the same port, or is this not a GPS?")
            self._notify()

    def _from_gpsd(self, msg):
        st = self._parser.state
        st["last_sentence"] = time.time()
        cls = msg.get("class")
        if cls == "TPV":
            mode = int(msg.get("mode", 0) or 0)
            st["fix_type"] = mode
            if mode >= 2 and msg.get("lat") is not None:
                st.update(lat=float(msg["lat"]), lon=float(msg["lon"]), fix_at=time.time())
                if msg.get("speed") is not None:
                    st["speed_kmh"] = float(msg["speed"]) * 3.6
                if msg.get("track") is not None:
                    st["course"] = float(msg["track"])
                if msg.get("alt") is not None or msg.get("altMSL") is not None:
                    st["alt_m"] = float(msg.get("altMSL", msg.get("alt")))
            if msg.get("time"):
                st["utc"], st["date"] = msg["time"][11:19], msg["time"][:10]
        elif cls == "SKY":
            sats = msg.get("satellites") or []
            st["snr"] = {str(s.get("PRN")): int(s.get("ss") or 0) for s in sats}
            st["sats_view"] = len(sats)
            st["sats_used"] = sum(1 for s in sats if s.get("used"))
            if msg.get("hdop") is not None:
                st["hdop"] = float(msg["hdop"])

    def _notify(self):
        """Tell the app about a fix every few seconds — on a thread of its own,
        since naming the place is a web request and the port must keep being
        read meanwhile (its buffer is small)."""
        if not self.on_fix or self._notifying or time.time() - self._last_notify < 3:
            return
        pos = self.position()
        if pos is None:
            return
        self._last_notify = time.time()
        state = self.state()

        def go():
            try:
                self.on_fix(state)
            except Exception:
                log.exception("gps: on_fix failed")
            finally:
                self._notifying = False
        self._notifying = True
        threading.Thread(target=go, daemon=True, name="gps-fix").start()


class _GpsdStream:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def lines(self):
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return []
        if not chunk:
            raise RuntimeError("gpsd closed the connection")
        self.buf += chunk
        out = []
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            try:
                out.append(json.loads(raw.decode("utf-8", "ignore")))
            except ValueError:
                pass
        return out

    def close(self):
        self.sock.close()


# -- where that is ----------------------------------------------------------------------------

def place_name(lat, lon):
    """A town and region for a position, from OpenStreetMap — cached for a
    long time, so the same spot is only ever asked once. Returns
    (label, country_code) or (None, None)."""
    key = f"place_{lat:.2f}_{lon:.2f}"

    def fetch():
        import requests
        r = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"format": "jsonv2", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "zoom": 10},
                         headers={"User-Agent": UA}, timeout=8)
        r.raise_for_status()
        a = r.json().get("address", {})
        town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("municipality") or a.get("county")
        region = a.get("state") or a.get("region") or a.get("country")
        label = ", ".join(x for x in (town, region) if x)
        return {"label": label or None, "country": (a.get("country_code") or "").upper()}
    d = cache.cached(key, 30 * 86400, fetch) or {}
    return d.get("label"), d.get("country")


# -- why isn't it working? ----------------------------------------------------------------------

def _in_group(name):
    try:
        import grp
        gid = grp.getgrnam(name).gr_gid
        return gid in os.getgroups() or os.getegid() == gid
    except (KeyError, OSError):
        return None


def _service_active(name):
    """Whether a systemd service is running, or None if we can't tell."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=4)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return None


def _port_info(path):
    real = os.path.realpath(path)
    info = {"path": path, "real": real, "exists": os.path.exists(real), "readable": None, "owner": ""}
    if info["exists"]:
        info["readable"] = os.access(real, os.R_OK | os.W_OK)
        try:
            import grp
            import pwd
            st = os.stat(real)
            info["owner"] = f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name} {oct(st.st_mode & 0o777)[2:]}"
        except (OSError, KeyError):
            pass
    return info


def listen_once(port, baud, seconds=2.5):
    """Open a port at one speed and see what comes out of it."""
    out = {"baud": baud, "bytes": 0, "valid": 0, "kinds": [], "sample": "", "error": None}
    try:
        import serial
    except ImportError:
        out["error"] = "python3-serial is not installed"
        return out
    try:
        s = serial.Serial(port, baud, timeout=0.5)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out
    kinds, sample, junk = set(), "", ""
    try:
        s.reset_input_buffer()
        end = time.time() + seconds
        while time.time() < end:
            raw = s.readline()
            if not raw:
                continue
            out["bytes"] += len(raw)
            line = raw.decode("ascii", "ignore").strip()
            if valid(line):
                out["valid"] += 1
                kinds.add(line[3:6])
                sample = sample or line
                if out["valid"] >= 4:
                    break
            elif not junk and line:
                junk = line
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            s.close()
        except Exception:
            pass
    out["kinds"] = sorted(kinds)
    out["sample"] = sample or junk
    return out


def try_port(port):
    """What a port says at each speed, stopping at the first that makes
    sense: [listen_once results]."""
    results = []
    for n, baud in enumerate((9600, 4800, 38400, 115200)):
        r = listen_once(port, baud, 2.5 if n == 0 else 1.5)
        results.append(r)
        if r["valid"] or r["error"]:
            break
        if r["bytes"] == 0 and n >= 1:    # silent at two speeds: it is silent
            break
    return results


def _last_gps_port(kernel):
    """Where the kernel log last saw a GPS come up ("usb 1-1.3: Product: u-blox 7 …"), or None."""
    port = None
    for ln in kernel:
        m = re.search(r"usb (\d+-[\d.]+): (?:Product: .*(?:u-blox|GPS|GNSS)|New USB device found, idVendor=1546)", ln, re.I)
        if m:
            port = m.group(1)
    return port


def _drop_story(kernel, port):
    """What the kernel log says about the device on `port` going away: how many
    times, and any word of interference (EMI) or over-current on that port."""
    gone = sum(1 for ln in kernel if re.search(rf"usb {re.escape(port)}: USB disconnect", ln))
    hub, _, num = port.rpartition(".")
    emi = bool(hub) and any(re.search(rf"hub {re.escape(hub)}:[\d.]+: port {num} disabled by hub \(EMI\?\)", ln) for ln in kernel)
    over = any("over-current" in ln.lower() and (f"{hub}-port{num}:" in ln or f"usb {port}:" in ln) for ln in kernel)
    # something else on the same hub going too points at the hub (or its power), not the receiver
    names, others = {}, []
    for ln in kernel:
        m = re.search(r"usb (\d+-[\d.]+): (Product: (.+)|USB disconnect)", ln)
        if not m or not hub or m.group(1) == port or m.group(1).rpartition(".")[0] != hub:
            continue
        if m.group(3):
            names[m.group(1)] = m.group(3).strip()
        elif m.group(1) not in others:
            others.append(m.group(1))
    return {"port": port, "gone": gone, "emi": emi, "overcurrent": over,
            "others": [names.get(p, f"what was on port {p.rpartition('.')[2]}") for p in others]}


def _ago(seconds):
    s = int(max(0, seconds))
    return f"{s} s" if s < 90 else f"{round(s / 60)} min" if s < 5400 else f"{round(s / 3600)} h"


def probe():
    """Everything that bears on 'why isn't my GPS working', with a verdict at
    the top. Pauses the reader for a few seconds to listen to the port itself."""
    from .camera import _kernel_lines, usb_devices
    conf = dict(GPS.conf)
    before = GPS.status()
    usb = usb_devices()
    gps_like = [u for u in usb if u["vid"] in GPS_VENDORS or any(k in u["name"].lower() for k in ("gps", "gnss", "u-blox", "ublox"))]
    ports = [_port_info(p) for p in candidates()]
    try:
        import serial
        pyserial = getattr(serial, "__version__", "yes")
    except ImportError:
        pyserial = None
    gpsd = gpsd_devices()
    modem_manager = _service_active("ModemManager")
    rule = os.path.exists(UDEV_RULE)
    dialout = _in_group("dialout")

    # listen to the ports ourselves — the reader has to let go of them for that
    tests = {}
    want = str(conf.get("device") or "auto")
    was_running = GPS.running()
    if was_running and want != "mock":
        GPS.stop()
    try:
        if pyserial and want != "mock":
            to_try = [want] if want not in ("auto", "gpsd") else [p["path"] for p in ports if p["exists"]][:3]
            for path in to_try:
                if gpsd and os.path.realpath(path) in [os.path.realpath(h) for h in gpsd]:
                    continue                          # gpsd's: opening it too would only garble both
                tests[path] = try_port(path)
    finally:
        if was_running and want != "mock":
            GPS.start(conf)
    kernel_all, kernel_note = _kernel_lines(limit=200, keys=(
        "tty", "cdc_acm", "usbserial", "pl2303", "cp210x", "ch341", "ftdi", "u-blox", "over-current", "overcurrent",
        "under-voltage", "undervolt", "usb 1-", "usb 2-", "usb 3-", "disabled by hub"))
    kernel = kernel_all[-30:]
    # dropping off the bus and coming back: from the reader, and from the kernel's log
    now = time.time()
    drops = [t for t in list(GPS.drops) if now - t < 3600]
    port = gps_like[0]["bus"] if gps_like else _last_gps_port(kernel_all)
    story = _drop_story(kernel_all, port) if port else {"port": None, "gone": 0, "emi": False, "overcurrent": False,
                                                        "others": []}
    try:
        from .ptz import PTZ
        turning = sum(1 for t in drops if PTZ.turning_near(t))
    except Exception:
        turning = 0
    camera_on_bus = any("video" in u["classes"] for u in usb)
    dropping = len(drops) >= 2 or story["gone"] >= 2
    model = ""
    try:
        with open("/proc/device-tree/model", encoding="utf-8", errors="replace") as fh:
            model = fh.read().rstrip("\0")
    except OSError:
        pass

    # the verdict
    talking = {p: r for p, rs in tests.items() for r in rs if r["valid"]}
    heard = {p: rs for p, rs in tests.items() if any(r["bytes"] for r in rs)}
    refused = {p: r for p, rs in tests.items() for r in rs if r["error"]}
    refused_words = " ".join(r["error"].lower() for r in refused.values())
    zero = "zero" in model.lower()
    if mode(conf) == "off":
        verdict = "The GPS is switched off: set it to Auto (or On) above and save, then look again."
    elif want == "mock":
        verdict = "This is the pretend receiver (PIE_INK_DRIVER=mock): there is no real port to check."
    elif pyserial is None:
        verdict = "python3-serial is missing on this Pi: sudo apt install python3-serial, then restart pie-ink."
    elif dropping:
        n = len(drops) if drops else story["gone"]
        verdict = (("It works right now, but it keeps" if talking else "It keeps") + " dropping off the USB bus: "
                   + (f"{n} times in the last hour, the last {_ago(now - drops[-1])} ago. " if drops
                      else f"{n} times in the kernel's log. ")
                   + "Each time it comes back, but has to find satellites again (a minute or more). ")
        if story.get("others"):
            verdict += (f"{' and '.join(story['others'][:2])} on the same hub dropped off too, so it's the hub or its "
                        "power rather than the receiver. ")
        if turning:
            verdict += (f"{turning} of those times the camera had just turned: its motors draw a burst of current, and on a "
                        "hub without its own power that can reset what else is plugged in. ")
        elif camera_on_bus:
            verdict += ("The camera shares the bus: when its motors turn it they draw bursts of current, and on a hub "
                        "without its own power that can reset what else is plugged in. ")
        if story["emi"]:
            verdict += "The kernel also switched its hub port off for interference (EMI). "
        if story["overcurrent"]:
            verdict += "The kernel also saw an over-current on its port. "
        verdict += ("A hub with its own power adapter usually fixes it"
                    + ("; to check, switch the little friend off for a while." if camera_on_bus else
                       ", or try another cable."))
    elif gpsd and (want in ("auto", "gpsd") or any(os.path.realpath(want) == os.path.realpath(h) for h in gpsd)):
        verdict = (f"gpsd has the receiver ({', '.join(gpsd)}) and PiE-ink reads through it, which is fine. "
                   + ("It is getting sentences that way." if before["connected"] else
                      "Nothing is coming through gpsd though: check it with `gpsmon` or `gpspipe -r`, or stop it "
                      "(sudo systemctl disable --now gpsd.socket gpsd) so PiE-ink can read the port itself."))
    elif talking:
        path, r = next(iter(talking.items()))
        verdict = (f"The receiver works: {path} gave {r['valid']} good sentences at {r['baud']} baud ({', '.join(r['kinds'])}). "
                   + ("It has a fix. " if before["fix"] != "none" else
                      "It just needs satellites: a view of the sky (a window sill), and a minute or more from cold. ")
                   + (f"Note it speaks {r['baud']} baud, not the usual 9600 — the reader now finds that on its own. " if r["baud"] != 9600 else "")
                   + ("" if before["connected"] or not was_running else
                      "The reader wasn't getting these — it has been restarted; give it ten seconds."))
    elif refused and "ermission" in refused_words:
        verdict = ("The port is there but this user may not open it: " + ("it isn't in the dialout group. " if dialout is False else
                   "permission was refused. ") + "Run: sudo usermod -aG dialout $USER — then restart pie-ink (sudo systemctl restart "
                   "pie-ink, or log out and in if you start it by hand). ./setup.sh does this too.")
    elif refused and ("busy" in refused_words or "resource" in refused_words):
        verdict = (("The port is held by another program (not gpsd — it isn't running)" if gpsd is None else "The port is held by another program")
                   + ((" — ModemManager is running and probes new serial ports as modems; the udev rule from ./setup.sh tells it to leave "
                       "a u-blox alone" + (" (the rule is missing: run ./setup.sh again)." if not rule else
                       ", but only a u-blox; for another dongle: sudo systemctl disable --now ModemManager.")) if modem_manager else
                      ". Find it with: sudo fuser -v " + " ".join(p["real"] for p in ports) + "."))
    elif refused:
        path, r = next(iter(refused.items()))
        verdict = (f"{path} couldn't be opened: {r['error']}. " + (("Pick Auto, or one of: " + ", ".join(p["path"] for p in ports) + ".")
                   if ports else "No serial port has appeared at all — see the USB list above and the kernel log below."))
    elif heard:
        verdict = ("The port sends bytes but nothing readable at 9600, 4800, 38400 or 115200 baud. Either something else is reading "
                   "it at the same time — each reader gets fragments — " + ("and ModemManager is running (see above), " if modem_manager else "")
                   + "or it isn't a GPS. `cat " + next(iter(heard)) + "` in a terminal shows what it says.")
    elif tests:
        verdict = ("The port opens but stays silent. " + ("On a Zero 2 W check the power and the OTG adapter first. " if zero else "")
                   + ("ModemManager is running and may have the receiver tied up for half a minute after it is plugged in"
                      + (" — the udev rule that stops that is missing: run ./setup.sh again" if not rule else "") + ". " if modem_manager else "")
                   + "A u-blox that has gone quiet usually comes back after a replug; some dongles only talk once they have power "
                   "from a proper supply.")
    elif not ports and not gps_like:
        verdict = ("Nothing on the USB bus looks like a GPS receiver and no serial port has appeared, so nothing above the cable "
                   "can help. " + ("On a Zero 2 W that is nearly always the adapter or power: its one micro-USB port needs a data "
                                   "(OTG) adapter, not a charging cable, and a hub with its own power if a speaker or mic shares it. "
                                   if zero else "Try another port or cable. ")
                   + "Unplug and replug it and watch `dmesg -w`; `lsusb` should list it.")
    elif not ports:
        u = gps_like[0]
        verdict = (f"The dongle is on the bus ({u['name']}, {u['vid']}:{u['pid']}) but no serial port appeared for it: the kernel "
                   "driver (cdc_acm for a u-blox, or usbserial/pl2303/cp210x/ch341 for a serial chip) didn't bind. The kernel log below "
                   "should say; `sudo modprobe cdc_acm` (or the chip's module) and replugging usually fixes it.")
    else:
        verdict = "No serial port to try" + (f" ({want} doesn't exist — pick Auto)" if want not in ("auto", "gpsd") else "") + "."
    return {"model": model, "usb": usb, "gps_like": gps_like, "ports": ports, "pyserial": pyserial, "gpsd": gpsd,
            "modem_manager": modem_manager, "udev_rule": rule, "dialout": dialout, "status": before, "tests": tests,
            "kernel": kernel, "kernel_note": kernel_note, "drops": len(drops), "drops_turning": turning,
            "last_drop_ago": round(now - drops[-1]) if drops else None, "usb_story": story, "verdict": verdict}


def probe_text(report=None):
    r = report or probe()
    st = r["status"]
    lines = [f"Pi: {r['model'] or 'unknown'}",
             f"GPS in settings: {st.get('mode', 'on' if st['enabled'] else 'off')}, device {GPS.conf.get('device') or 'auto'}",
             f"Reader: {'running' if st['running'] else 'stopped'}"
             + (f", on {st['device']} ({st['source']})" if st['device'] else "")
             + (f", {st['sentences']} sentences, {st['garbage']} unreadable" if st['device'] else "")
             + (f", error: {st['error']}" if st['error'] else ""),
             f"Fix: {st['fix']}" + (f" ({st['sats_used']} used, {st['sats_view']} in view)" if st['fix'] != 'none' or st['sats_view'] else ""),
             "Dropped off the USB bus: " + (f"{r['drops']} times in the last hour (the last {_ago(r['last_drop_ago'])} ago)"
                                            + (f", {r['drops_turning']} while the camera was turning" if r.get("drops_turning") else "")
                                            if r.get("drops") else "not while PiE-ink was reading it")
             + (f"; the kernel saw {r['usb_story']['gone']} on {r['usb_story']['port']}" if (r.get("usb_story") or {}).get("gone") else ""),
             f"pyserial: {r['pyserial'] or 'MISSING'}   dialout group: {'yes' if r['dialout'] else 'NO' if r['dialout'] is False else 'unknown'}",
             f"gpsd: {'not running' if r['gpsd'] is None else ('running, reading ' + ', '.join(r['gpsd'])) if r['gpsd'] else 'running, no devices'}",
             f"ModemManager: {'running' if r['modem_manager'] else 'not running' if r['modem_manager'] is False else 'unknown'}"
             + f"   udev rule for u-blox: {'installed' if r['udev_rule'] else 'MISSING (run ./setup.sh)'}",
             "", f"USB devices ({len(r['usb'])}):"]
    for u in r["usb"]:
        tag = GPS_VENDORS.get(u["vid"], "")
        lines.append(f"  {u['bus']:<8} {u['vid']}:{u['pid']}  {u['name']}  [{', '.join(u['classes']) or '?'}]" + (f"  <- {tag}" if tag else ""))
    if not r["usb"]:
        lines.append("  (nothing but the Pi itself)")
    lines += ["", f"Serial ports ({len(r['ports'])}):"]
    for p in r["ports"]:
        lines.append(f"  {p['path']}" + (f" -> {p['real']}" if p['real'] != p['path'] else "")
                     + (f"  {p['owner']}  {'readable' if p['readable'] else 'NOT readable by this user'}" if p["exists"] else "  (gone)"))
    if not r["ports"]:
        lines.append("  (none)")
    if r["tests"]:
        lines += ["", "Listening to them:"]
        for path, rs in r["tests"].items():
            for t in rs:
                lines.append(f"  {path} @ {t['baud']}: " + (t["error"] if t["error"] else
                             f"{t['bytes']} bytes, {t['valid']} good sentences" + (f" ({', '.join(t['kinds'])})" if t['kinds'] else "")
                             + (f"  e.g. {t['sample'][:60]}" if t["sample"] else "")))
    lines += ["", "Kernel log (serial/USB):"]
    lines += [f"  {ln}" for ln in r["kernel"]] or [f"  {r['kernel_note'] or '(nothing about serial ports)'}"]
    lines += ["", "Verdict: " + r["verdict"]]
    return "\n".join(lines)


def distance_km(a, b):
    """Great-circle distance between two (lat, lon) pairs."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def compass(degrees):
    if degrees is None:
        return ""
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((float(degrees) + 22.5) // 45) % 8]


GPS = Gps()


if __name__ == "__main__":                              # python3 -m pie_ink.gps
    from .settings import load
    GPS.conf = dict(load().get("gps", {}))
    print(probe_text())
