"""A camera that turns: pan, tilt and zoom, over UVC.

An OBSBOT Tiny 2, a Logitech PTZ, anything whose `v4l2-ctl -l` lists
pan_absolute, tilt_absolute or zoom_absolute. These are the standard V4L2
camera controls — pan and tilt in arc-seconds (324000 is 90°), zoom in
whatever units the camera uses — set straight from Python with the same
ioctls v4l2-ctl uses, on the camera's own node, while it streams.

The joystick on the Camera tab drives a speed, not a position: the page
says where the stick is a few times a second, a thread here moves the
target that far and writes pan and tilt together (one USB transfer, so a
diagonal is a diagonal and one axis never cancels the other mid-move), and
if the page goes quiet — a phone locked mid-drag — the camera stops. The
bot turns it too: "look left", "look around", the agent's own looks. While
it moves, the watcher knows the picture is changing because the camera
did, not the room.

An OBSBOT also follows people by itself. That is a switch here ("Follow
me"), flipped through the camera's own vendor controls — the same bytes
OBSBOT's app sends — and kept where you put it: the camera's hand gesture,
or its habit of starting up tracking, gets put back within a few seconds.

An OBSBOT left unused for a while falls asleep with its lens turned down,
and asleep it ignores every move. Whenever PiE-ink uses the camera — the
live view, the stick, the little friend, a photo — it asks whether the
camera is asleep and, if it is, wakes it (OBSBOT's own "run" command) and
points it back where it was.

When it turns by itself (the little friend, a look around) it glides,
easing in and out, rather than snapping across. An OBSBOT is turned at a
speed — its own speed command, sped up and slowed down along the way — so
the turn is one continuous movement; it's then told the exact place, which
also corrects any small miss. Any other camera goes a small step at a time.
"""
import collections
import contextlib
import ctypes
import errno
import fcntl
import logging
import math
import os
import re
import struct
import threading
import time

log = logging.getLogger(__name__)

# -- V4L2, the bits that matter --------------------------------------------------------------------

def _iowr(nr, size):
    """_IOWR('V', nr, size) — the generic layout, as on ARM and x86."""
    return (3 << 30) | (size << 16) | (ord("V") << 8) | nr


class _ExtControl(ctypes.Structure):
    """struct v4l2_ext_control (packed): the value lives in the low half of the union."""
    _pack_ = 1
    _fields_ = [("id", ctypes.c_uint32), ("size", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
                ("value64", ctypes.c_int64)]


class _ExtControls(ctypes.Structure):
    """struct v4l2_ext_controls — its pointer makes it 32 bytes on a 64-bit Pi, 24 on a 32-bit one."""
    _fields_ = [("which", ctypes.c_uint32), ("count", ctypes.c_uint32), ("error_idx", ctypes.c_uint32),
                ("request_fd", ctypes.c_int32), ("reserved", ctypes.c_uint32),
                ("controls", ctypes.POINTER(_ExtControl))]


class _XuQuery(ctypes.Structure):
    """struct uvc_xu_control_query: one request to a camera's vendor ("extension") unit."""
    _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8), ("query", ctypes.c_uint8),
                ("size", ctypes.c_uint16), ("data", ctypes.POINTER(ctypes.c_uint8))]


VIDIOC_QUERYCTRL = _iowr(36, 68)
VIDIOC_G_CTRL = _iowr(27, 8)
VIDIOC_S_CTRL = _iowr(28, 8)
VIDIOC_S_EXT_CTRLS = _iowr(72, ctypes.sizeof(_ExtControls))
UVCIOC_CTRL_QUERY = (3 << 30) | (ctypes.sizeof(_XuQuery) << 16) | (ord("u") << 8) | 0x21
UVC_SET_CUR, UVC_GET_CUR = 0x01, 0x81
CTRL_CLASS_CAMERA = 0x009A0000
CID = {"pan": 0x009A0908, "tilt": 0x009A0909, "zoom": 0x009A090D}
FLAG_DISABLED, FLAG_READ_ONLY = 0x1, 0x4
ARCSEC = 3600.0                  # pan and tilt are in arc-seconds
HEARTBEAT = 0.7                  # the stick goes quiet this long and the camera stops
TICK = 0.1                       # how often a held stick moves the camera
KEEP_EVERY = 3.0                 # how often the camera's own tracking is checked against the switch

# OBSBOT's vendor unit (unit 2 on a Tiny 2, GUID 9a1e7291-6843-4683-6d92-39bc7906ee49). Selector 6
# takes a 60-byte [tag, length, value…] write and reads back a status block; tag 0x16 is the
# camera's own person tracking: [0x16, 2, mode, framing], mode 2 one person, 0 off. Byte 24 of
# the status block is the mode it is in. Captured from OBSBOT's app by lxman/obsbot-mcp and
# yezin293/obsbot_control, and checked on a Tiny 2 and a Tiny 2 Lite.
OBSBOT_VENDOR = "3564"
XU_SETTINGS, XU_LEN = 6, 60
TAG_TRACKING, STATUS_MODE = 0x16, 24
AI_MODES = {0: "off", 1: "a group", 2: "one person", 3: "a hand", 4: "whiteboard", 5: "desk", 6: "switching"}
FOLLOWING = (1, 2, 3)            # the modes that turn the camera after someone
# Selector 2 of the same unit takes OBSBOT's framed commands (see v3_frame). Command 0xA0C2 to
# receiver 2 sets the run state: payload 00 00 00 00 wakes it, 01 00 00 00 puts it to sleep. Byte 2
# of the status block says which it's in (0 awake). From lxman/obsbot-mcp's protocol notes and
# captured frames; the frame is checked against those captures in the smoketest.
XU_COMMAND = 2
STATUS_ASLEEP = 0x02
CMD_RUN_STATE, TO_CAMERA = 0xA0C2, 0x02
RISE = 1.5                       # woken, the gimbal lifts itself level first: this long before it takes a move
WAKE_BACKOFF = 300.0             # a camera that wouldn't say it woke isn't asked again for a while
# Command 0x6484 to receiver 4 turns the gimbal at a speed until it's told another: payload roll,
# pitch, yaw, float32 °/s. Measured on a Tiny 2 by lxman/obsbot-mcp: the speed is exact (40°/s for
# 1.2 s turned it 48°), a positive yaw turns it the way a falling UVC pan goes, and 180°/s and up is
# ignored outright. Pitch is taken as OBSBOT's everywhere else: positive is down. It takes speeds
# only while it streams.
CMD_GIM_SPEED, TO_GIMBAL = 0x6484, 0x04
SPEED_MAX = 150.0
SPEED_TICK = 0.05                # how often a smooth turn updates its speed
STREAM_WAIT = 4.0                # how long a smooth turn waits for the camera to stream (and wake)
# turning by itself: easing in and out, at this many degrees a second on average
GENTLE = 25.0
SWEEP_HOLD = 2.0                 # a look around stops this long at each place, so you see it look
# errors that mean the camera is gone (unplugged, re-plugged) rather than busy for a moment
_GONE = {errno.ENODEV, errno.ENXIO, errno.EBADF, errno.ENOENT, errno.ESHUTDOWN}


def queryctrl(fd, cid):
    """What a control allows: {id, name, min, max, step, default}, or None
    when the camera doesn't have it (or has it switched off)."""
    buf = bytearray(68)
    struct.pack_into("<I", buf, 0, cid)
    try:
        fcntl.ioctl(fd, VIDIOC_QUERYCTRL, buf)
    except OSError:
        return None
    _, _, name, lo, hi, step, default, flags = struct.unpack_from("<II32siiiiI", buf, 0)
    if flags & (FLAG_DISABLED | FLAG_READ_ONLY) or hi <= lo:
        return None
    return {"id": cid, "name": name.split(b"\0")[0].decode(errors="ignore"), "min": lo, "max": hi,
            "step": max(1, step), "default": default}


def g_ctrl(fd, cid):
    buf = bytearray(struct.pack("<Ii", cid, 0))
    fcntl.ioctl(fd, VIDIOC_G_CTRL, buf)
    return struct.unpack("<Ii", bytes(buf))[1]


def s_ctrl(fd, cid, value):
    fcntl.ioctl(fd, VIDIOC_S_CTRL, bytearray(struct.pack("<Ii", cid, int(value))))


def s_ext_ctrls(fd, pairs):
    """Several camera controls in one go — the camera gets them in one transfer."""
    arr = (_ExtControl * len(pairs))()
    for i, (cid, value) in enumerate(pairs):
        arr[i].id, arr[i].size, arr[i].value64 = cid, 0, int(value)
    ctrls = _ExtControls(which=CTRL_CLASS_CAMERA, count=len(pairs), error_idx=0, request_fd=0, reserved=0,
                         controls=ctypes.cast(arr, ctypes.POINTER(_ExtControl)))
    fcntl.ioctl(fd, VIDIOC_S_EXT_CTRLS, ctrls)


def xu_query(fd, unit, selector, query, data=b""):
    """A 60-byte read (UVC_GET_CUR) or write (UVC_SET_CUR) on a vendor unit."""
    buf = (ctypes.c_uint8 * XU_LEN)(*bytes(data)[:XU_LEN])
    req = _XuQuery(unit=unit, selector=selector, query=query, size=XU_LEN,
                   data=ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)))
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, req)
    return bytes(buf)


def crc16_usb(data):
    """CRC-16/USB (reflected 0x8005, init 0xFFFF, out xor 0xFFFF): the checksum in OBSBOT's frames."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def v3_frame(seq, cmd, receiver, payload=b"", flags=0x25, sender=0x0A):
    """One of OBSBOT's framed commands, 60 bytes: 0xAA, flags, sequence, header length (12), the
    header's CRC, sender, receiver, command; then the payload's length, its CRC, and the payload."""
    f = bytearray(XU_LEN)
    f[0], f[1] = 0xAA, flags
    struct.pack_into("<HH", f, 2, seq & 0xFFFF, 12)
    f[8], f[9] = sender, receiver
    struct.pack_into("<H", f, 10, cmd)
    struct.pack_into("<H", f, 6, crc16_usb(f[:12]))              # worked out with its own field still zero
    payload = bytes(payload)
    if payload:
        n = len(payload)
        struct.pack_into("<H", f, 12, n)
        f[16:16 + n] = payload
        struct.pack_into("<H", f, 14, crc16_usb(f[12:16 + n]))
    return bytes(f)


def vendor_of(path):
    """The USB vendor id of a /dev/videoN, from sysfs ("3564" is OBSBOT), or ""."""
    try:
        dev = os.path.realpath(f"/sys/class/video4linux/{os.path.basename(path)}/device")
        with open(os.path.join(os.path.dirname(dev), "idVendor")) as fh:
            return fh.read().strip().lower()
    except OSError:
        return ""


# -- the camera --------------------------------------------------------------------------------------

def _curve(v):
    """Stick deflection to speed: gentle near the middle, for small moves."""
    return math.copysign(abs(v) ** 1.8, v)


class Ptz:
    def __init__(self):
        self._lock = threading.RLock()
        self.conf = {}
        self.fd = None
        self.device = None
        self.card = ""
        self.ctrls = {}                  # "pan" / "tilt" / "zoom" -> what queryctrl said
        self.target = {}                 # the raw values last written (or read)
        self._want = {}                  # the same, unrounded: slow stick moves add up here
        self.error = None
        self._looked_for = 0.0
        self.move_until = 0.0            # when the last move should be over
        self._vel = (0.0, 0.0)
        self._vel_at = 0.0
        self._opts = {}                  # speed and swaps sent with the stick (the page's unsaved ones)
        self._seq = {}                   # page -> the newest stick message seen, so a late one can't restart it
        self._driver = None
        self.looking = False             # a look around is under way
        self.last_look = None            # {"ts", "text", "where"}
        self.busy_until = 0.0            # the camera was slow to take the last move: let it catch up
        self._err_logged = 0.0
        self.touched_at = 0.0            # when you last pointed it (the stick, Center, "look left"): the friend waits
        # the camera's own tracking (an OBSBOT's): what the switch here says, and what the camera says
        self.xu_unit = None              # its vendor unit, when it can follow people by itself
        self.follow_want = False
        self.follow_on = None            # True / False as the camera last said; None: not known
        self.follow_mode = None          # its words for it: "one person", "off", "switching"…
        self._paused = 0                 # looks of PiE-ink's own that stopped the following for a moment
        self.on_follow_change = None     # called with True/False when the switch is flipped from here
        self._keeper = None
        self._stop = threading.Event()
        # asleep (an OBSBOT with its lens turned down): what it last said, and the waking
        self.asleep = None               # True / False as the camera last said; None: not known
        self._asked_at = 0.0             # when its status was last read
        self._woke_at = 0.0              # when "run" was last sent
        self._wake_off_until = 0.0       # it wouldn't say it woke: not asked again till then
        self._wake_fails = 0             # times in a row it wouldn't
        self.waking_until = 0.0          # woken just now: the gimbal is still lifting itself
        self.wakes = 0                   # times it was woken
        self._waking = None              # the thread waking it
        self._xu_seq = 0
        self._gen = 0                    # counts moves from outside a glide: a newer one ends the glide
        self.gliding = False
        # an OBSBOT turning at a speed (a smooth glide): the speeds sent, and where they've taken it
        self._speeding = False
        self._speed_gen = -1             # the glide that's turning it
        self._spd = (0.0, 0.0)           # pan and tilt, °/s the way the values rise
        self._spd_at = 0.0
        self._est = {}                   # pan / tilt, raw: the speeds added up
        self._cache_stale = False        # turned at a speed since pan and tilt were last written
        self.stop_owed = False           # a stop that couldn't be given: sent as soon as the camera can be reached
        self.turns = collections.deque(maxlen=200)   # (from, to): when its motors were turning it, for the USB checks

    def configure(self, conf):
        self.conf = dict(conf or {})
        self.follow_want = bool(self.conf.get("follow", False))

    # -- finding it ------------------------------------------------------------------------------

    def _candidates(self):
        """The node the camera is streaming from first, then every other USB camera."""
        from . import camera
        out = []
        cid = str(camera.CAMERA.status().get("id") or "")
        if cid.startswith("usb:"):
            out.append(cid[4:])
        for dev in camera.video_devices():
            path = f"/dev/{dev}"
            if path not in out:
                out.append(path)
        return out

    def _find(self):
        for path in self._candidates():
            try:
                fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            except OSError:
                continue
            found = {k: queryctrl(fd, cid) for k, cid in CID.items()}
            found = {k: v for k, v in found.items() if v}
            if not found:
                os.close(fd)
                continue
            self.fd, self.device, self.ctrls = fd, path, found
            try:
                from .camera import query_node
                self.card = ((query_node(path) or {}).get("card") or "").split(":")[0].strip()
            except Exception:
                self.card = ""
            for k, c in found.items():
                try:
                    self.target[k] = g_ctrl(fd, c["id"])
                except OSError:                           # keep what it was told last, if anything
                    self.target[k] = self.target.get(k, c["default"])
                self._want[k] = float(self.target[k])
            self.error = None
            self._find_follow()
            log.info("ptz: %s on %s can %s%s", self.card or "a camera", path, ", ".join(found),
                     f", and follow people by itself (following is {'on' if self.follow_on else 'off'})"
                     if self.xu_unit is not None else "")
            self.settle_gimbal("found it")                  # a turn left running by a restart or a hiccup ends here
            return True
        return False

    def available(self):
        """True when there is a camera that turns (looked for at most every ten seconds)."""
        if self.fd is not None:                          # no waiting on a move that's slow to go through
            return True
        with self._lock:
            if self.fd is not None:
                return True
            if time.time() - self._looked_for < 10:
                return False
            self._looked_for = time.time()
            try:
                return self._find()
            except Exception as e:
                self.error = str(e)[:120]
                return False

    def _lost(self, e):
        log.info("ptz: the camera went away (%s)", e)
        try:
            os.close(self.fd)
        except (OSError, TypeError):
            pass
        self.fd, self.device, self.ctrls = None, None, {}
        self.xu_unit, self.follow_on, self.follow_mode = None, None, None
        self._speeding, self._spd = False, (0.0, 0.0)
        self.asleep, self._asked_at = None, 0.0
        self._wake_fails, self._wake_off_until = 0, 0.0      # plugged back in: a fresh start
        self._looked_for = 0.0
        self.error = f"the camera went away ({e.strerror or e})"

    def check(self):
        """After the camera stopped and came back (maybe as another /dev/videoN):
        let go of a handle to a node that's gone, so the next move finds it again."""
        with self._lock:
            if self.fd is None or not self.ctrls:
                return
            buf = bytearray(68)
            struct.pack_into("<I", buf, 0, next(iter(self.ctrls.values()))["id"])
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERYCTRL, buf)
            except OSError as e:
                if e.errno in _GONE:
                    self._lost(e)

    # -- where it points ---------------------------------------------------------------------------

    def lowest(self):
        """How far down it may ever look, in degrees. An OBSBOT pointed down
        goes to sleep, so nothing here — the stick, "look down", the friend —
        takes it lower than this."""
        try:
            return max(-90.0, min(0.0, float(self.conf.get("lowest", -30))))
        except (TypeError, ValueError):
            return -30.0

    def user_moved(self):
        """You pointed it somewhere: the friend leaves it be for a while."""
        self.touched_at = time.time()

    def _range(self, name):
        c = self.ctrls[name]
        lo, hi = c["min"], c["max"]
        if name == "tilt":
            lo = max(lo, min(hi, int(round(self.lowest() * ARCSEC))))
        return lo, hi

    def _clamp(self, name, raw):
        c = self.ctrls[name]
        lo, hi = self._range(name)
        raw = max(lo, min(hi, raw))
        raw = c["min"] + round((raw - c["min"]) / c["step"]) * c["step"]
        return int(max(lo, min(hi, raw)))

    def _zoom_frac(self):
        c = self.ctrls.get("zoom")
        if not c:
            return 0.0
        return (self.target.get("zoom", c["min"]) - c["min"]) / float(c["max"] - c["min"])

    def position(self):
        """Where it was last told to point. Read without the lock, so the page's
        polling never waits on a move the camera is slow to take."""
        ctrls, target = dict(self.ctrls), dict(self.target)
        p, t, z = ctrls.get("pan"), ctrls.get("tilt"), ctrls.get("zoom")
        zoom = (target.get("zoom", z["min"]) - z["min"]) / float(z["max"] - z["min"]) if z else 0.0
        return {"pan": round(target.get("pan", 0) / ARCSEC, 1) if p else 0.0,
                "tilt": round(target.get("tilt", 0) / ARCSEC, 1) if t else 0.0,
                "zoom": round(zoom, 3),
                "pan_range": [p["min"] / ARCSEC, p["max"] / ARCSEC] if p else [0.0, 0.0],
                "tilt_range": [max(t["min"] / ARCSEC, min(t["max"] / ARCSEC, self.lowest())), t["max"] / ARCSEC]
                if t else [0.0, 0.0],
                "has": {k: k in ctrls for k in CID}}

    def view_key(self):
        """Where it points, coarsely — the same key means the same view of the room."""
        if self.fd is None:
            return None
        p = self.position()
        return (round(p["pan"] / 5), round(p["tilt"] / 5), round(p["zoom"] * 20))

    def quiet(self, after=1.5):
        """True while the camera is turning, and for a moment after: the
        picture changes because the camera moved, not the room. A camera
        that is following someone is always turning; one waking up lifts itself."""
        now = time.time()
        return self.looking or self.gliding or self.driving() or now < self.move_until + after \
            or bool(self.follow_on) or now < self.waking_until

    def driving(self):
        return bool(self._driver and self._driver.is_alive())

    # -- moving it -----------------------------------------------------------------------------------

    def _write(self, values):
        """Raw values to the camera. Pan and tilt always go as a pair, so a
        move on one axis carries the other's target instead of letting the
        camera fill in where it happens to be mid-move."""
        if self.fd is None:
            raise ValueError("no camera that turns")
        if self._speeding:
            self._halt_speed()                                    # turning at a speed: it stops where it's got to first
            if self.fd is None:
                raise ValueError("no camera that turns")
        self.wake_soon("a move")                                  # asleep, it would ignore this: wake it (in the background)
        if "pan" in values or "tilt" in values:
            for k in ("pan", "tilt"):
                if k in self.ctrls and k not in values:
                    values[k] = self.target.get(k, self.ctrls[k]["default"])
        pairs = [(self.ctrls[k]["id"], v) for k, v in values.items() if k in self.ctrls]
        if not pairs:
            return
        before = dict(self.target)
        t0 = time.time()
        try:
            try:
                s_ext_ctrls(self.fd, pairs)
            except OSError as e:
                if e.errno in (errno.EBUSY, errno.EIO, errno.EPIPE, errno.EPROTO):
                    time.sleep(0.08)                             # still busy with the last one: once more
                    s_ext_ctrls(self.fd, pairs)
                elif e.errno in (errno.EINVAL, errno.ENOTTY, errno.EACCES):
                    for cid, v in pairs:                         # an old driver: one at a time
                        s_ctrl(self.fd, cid, v)
                else:
                    raise
        except OSError as e:
            if e.errno in _GONE:
                self._lost(e)
            else:                                                # busy, not gone: give it a moment
                self.busy_until = time.time() + (2.0 if e.errno == errno.ETIMEDOUT else 0.5)
            raise
        took = time.time() - t0
        if took > 0.25:                                          # slow to answer: don't pile more on it
            self.busy_until = time.time() + min(1.0, took)
        self.target.update(values)
        if "pan" in values or "tilt" in values:
            self._cache_stale = False                            # the driver's copy is where it points again
        # about how long the gimbal takes to get there (it turns ~90°/s), and a little for zoom
        turn = max([abs(self.target.get(k, 0) - before.get(k, 0)) / ARCSEC for k in ("pan", "tilt") if k in values] or [0])
        zoom = 0.6 if "zoom" in values and values["zoom"] != before.get("zoom") else 0.0
        self.move_until = max(self.move_until, time.time() + 0.3 + turn / 90.0 + zoom)
        if turn >= 1:
            self.turns.append((t0, time.time() + 0.3 + turn / 90.0))

    def set(self, pan=None, tilt=None, zoom=None):
        """Point it: pan and tilt in degrees, zoom from 0 (wide) to 1. An
        absolute move takes over from the stick."""
        with self._lock:
            if not self.available():
                raise ValueError("no camera that turns is plugged in")
            self._vel = (0.0, 0.0)
            self._gen += 1                                        # a glide under way stops for this
            values = {}
            if pan is not None and "pan" in self.ctrls:
                values["pan"] = self._clamp("pan", float(pan) * ARCSEC)
            if tilt is not None and "tilt" in self.ctrls:
                values["tilt"] = self._clamp("tilt", float(tilt) * ARCSEC)
            if zoom is not None and "zoom" in self.ctrls:
                c = self.ctrls["zoom"]
                values["zoom"] = self._clamp("zoom", c["min"] + max(0.0, min(1.0, float(zoom))) * (c["max"] - c["min"]))
            self._write(values)
            for k in values:
                self._want[k] = float(self.target[k])
            return self.position()

    def _swaps(self):
        o = self._opts if time.time() - self._vel_at < 5 else {}
        sx = o.get("swap_x", self.conf.get("swap_x", False))
        sy = o.get("swap_y", self.conf.get("swap_y", False))
        return (-1 if sx else 1), (-1 if sy else 1)

    def nudge(self, dpan=0.0, dtilt=0.0, dzoom=0.0, gentle=False):
        """Turn by so many degrees (right and up are positive, whichever way
        it is mounted) and zoom by a fraction. gentle: glide there."""
        with self._lock:
            if not self.available():
                raise ValueError("no camera that turns is plugged in")
            sx, sy = self._swaps()
            p = self.position()
            pan = p["pan"] + dpan * sx if dpan else None
            tilt = p["tilt"] + dtilt * sy if dtilt else None
            if not gentle or dzoom:
                return self.set(pan=pan, tilt=tilt, zoom=p["zoom"] + dzoom if dzoom else None)
        return self.glide(pan=pan, tilt=tilt)

    def center(self):
        return self.set(pan=0, tilt=0)

    def home(self, gentle=False):
        h = self.conf.get("home") or {}
        if gentle:
            self.glide(pan=h.get("pan", 0), tilt=h.get("tilt", 0))
            return self.set(zoom=h.get("zoom")) if h.get("zoom") is not None else self.position()
        if not h:
            return self.center()
        return self.set(pan=h.get("pan", 0), tilt=h.get("tilt", 0), zoom=h.get("zoom"))

    def gentle_speed(self):
        """How fast it turns by itself, in degrees a second (the "Turns by itself" slider)."""
        try:
            return max(5.0, min(1000.0, float(self.conf.get("gentle", GENTLE) or GENTLE)))
        except (TypeError, ValueError):
            return GENTLE

    def glide(self, pan=None, tilt=None, speed=None, stop=None):
        """Turn there the way a head turns, easing in and out, at `speed`
        degrees a second on average (the slider's, when not given). An OBSBOT
        turns at a speed that rises and falls (one continuous movement); any
        other camera goes a small step every tick. Any other move — the
        stick, Center, another glide — ends it where it is, and so does
        stop() returning True. Returns the position."""
        with self._lock:
            if not self.available():
                raise ValueError("no camera that turns is plugged in")
            self._vel = (0.0, 0.0)
            self._gen += 1
            gen = self._gen
            self._halt_speed()                                     # one turning at a speed stops where it's got to
            goal = {}
            if pan is not None and "pan" in self.ctrls:
                goal["pan"] = float(self._clamp("pan", float(pan) * ARCSEC))
            if tilt is not None and "tilt" in self.ctrls:
                goal["tilt"] = float(self._clamp("tilt", float(tilt) * ARCSEC))
            start = {k: float(self.target.get(k, self.ctrls[k]["default"])) for k in goal}
        if not goal:
            return self.position()
        dist = max(abs(goal[k] - start[k]) for k in goal) / ARCSEC
        duration = dist / (speed or self.gentle_speed())
        if dist < 2.0 or duration < TICK * 2:                     # a small step, or a fast turn: just go
            return self.set(pan=pan, tilt=tilt)
        if self._smooth_ok():
            if not self._ready_to_speed(gen):
                if self._gen != gen:
                    return self.position()                         # another move came along while it got ready
                return self.set(pan=pan, tilt=tilt)                # not streaming: it wouldn't take speeds. One move
            return self._glide_smooth(goal, gen, duration, stop)
        t0 = time.time()
        self.gliding = True
        try:
            while True:
                if self._gen != gen or (stop is not None and stop()):
                    break
                now = time.time()
                x = min(1.0, (now - t0) / duration)
                eased = x * x * (3 - 2 * x)                        # slow to start, slow to stop
                with self._lock:
                    if self._gen != gen:
                        break
                    if self.fd is None:
                        raise ValueError("the camera went away")
                    if now >= self.busy_until or x >= 1.0:          # a camera slow to take moves isn't piled on
                        values = {k: self._clamp(k, start[k] + (goal[k] - start[k]) * eased) for k in goal}
                        values = {k: v for k, v in values.items() if v != self.target.get(k)}
                        if values:
                            try:
                                self._write(values)
                            except OSError as e:
                                if self.fd is None:
                                    raise ValueError("the camera went away")
                                if now - self._err_logged > 10:
                                    self._err_logged = now
                                    log.info("ptz: the camera didn't take a step (%s) — carrying on", e.strerror or e)
                            for k in values:
                                self._want[k] = float(self.target.get(k, values[k]))
                if x >= 1.0:
                    break
                time.sleep(TICK)
        finally:
            self.gliding = False
        return self.position()

    # -- turning smoothly: an OBSBOT at a speed ----------------------------------------------------------

    def _smooth_ok(self):
        """Can it be turned at a speed? An OBSBOT can (its own speed command),
        unless it's following someone itself, or "smooth: false" is set."""
        return (self.fd is not None and self.xu_unit is not None and not self.follow_on
                and self.conf.get("smooth", True) is not False)

    def _ready_to_speed(self, gen):
        """It takes speeds only awake and streaming: have it stream, wake it
        if it's asleep, and let a move under way finish (a speed would cut
        it short). False when that takes too long, or another move comes."""
        from . import camera
        camera.CAMERA.touch(15)
        self.wake_soon("a move")
        self.wait_awake()
        end = time.time() + STREAM_WAIT
        while True:
            if self._gen != gen or self.fd is None:
                return False
            now = time.time()
            if self._awake() and self.asleep is not True and now >= self.move_until and now >= self.waking_until:
                return True
            if now >= end:
                return False
            time.sleep(0.05)

    def _sign(self, key):
        """Which way a speed turns it, against the way the UVC value rises: -1
        (see CMD_GIM_SPEED), unless the settings flip it ("tilt_speed_flip")."""
        return 1.0 if key == "tilt" and self.conf.get("tilt_speed_flip") else -1.0

    def _integrate(self, now):
        """Add up where the speeds sent have taken it since they were last added up."""
        dt = max(0.0, now - self._spd_at)
        self._spd_at = now
        for k, v in zip(("pan", "tilt"), self._spd):
            if v and k in self._est and k in self.ctrls:
                c = self.ctrls[k]
                self._est[k] = max(float(c["min"]), min(float(c["max"]), self._est[k] + v * dt * ARCSEC))
                self.target[k] = int(round(self._est[k]))

    def _speed(self, vpan, vtilt):
        """Turn at so many degrees a second — pan and tilt the way their values
        rise — until told another. What the last speeds did is added up first."""
        self._integrate(time.time())
        vpan = max(-SPEED_MAX, min(SPEED_MAX, float(vpan)))
        vtilt = max(-SPEED_MAX, min(SPEED_MAX, float(vtilt)))
        payload = struct.pack("<fff", 0.0, self._sign("tilt") * vtilt, self._sign("pan") * vpan)   # roll, pitch, yaw
        try:
            self._command(CMD_GIM_SPEED, TO_GIMBAL, payload)
        except OSError as e:
            if e.errno in _GONE:
                self._lost(e)
                raise ValueError("the camera went away")
            raise
        self._spd = (vpan, vtilt)

    def _halt_speed(self):
        """A turn at a speed stops where it's got to, and that's where it points now."""
        if not self._speeding:
            return
        self._speeding = False
        self._integrate(time.time())
        self._spd = (0.0, 0.0)
        self._cache_stale = True
        self._stop_gimbal()

    def _stop_gimbal(self, tries=3):
        """Speed 0 to the gimbal: told a speed, it turns until it's told another,
        and a stop that goes missing (a USB hiccup mid-turn) would leave it
        pushing against its end stop — motors hot, "never resting". So: a few
        tries, and one it can't be given now is owed, and sent the moment the
        camera can be reached again (keep_once, or finding it afresh). True
        when the stop went through."""
        for i in range(tries):
            if self.fd is None or self.xu_unit is None:
                break
            try:
                self._command(CMD_GIM_SPEED, TO_GIMBAL, bytes(12))
                if i == 0:
                    time.sleep(0.03)
                    continue                                  # twice, when it can be: a stop has been seen to go missing
                self.stop_owed = False
                return True
            except OSError as e:
                if e.errno in _GONE:
                    self._lost(e)
                    break
                log.info("ptz: the camera didn't take a stop (%s)", e.strerror or e)
                time.sleep(0.1)
        self.stop_owed = True
        log.warning("ptz: the camera is owed a stop — it goes as soon as the camera can be reached")
        return False

    def settle_gimbal(self, why):
        """Insurance, whenever an OBSBOT comes to hand — found, back after a
        hiccup, at start-up: a stop, in case the last thing it was told (by
        this process before a restart, or a crash) was a speed. Cheap, and it
        does nothing to a gimbal that's still."""
        if self.fd is None or self.xu_unit is None:
            return
        try:
            self._command(CMD_GIM_SPEED, TO_GIMBAL, bytes(12))
            self.stop_owed = False
            log.debug("ptz: told the gimbal to stop (%s)", why)
        except OSError as e:
            if e.errno in _GONE:
                self._lost(e)
            else:
                self.stop_owed = True
                log.info("ptz: couldn't tell the gimbal to stop (%s): %s", why, e.strerror or e)

    def _glide_smooth(self, goal, gen, duration, stop):
        """Turn an OBSBOT there at a speed: it rises and falls smoothly (the
        same easing as the steps), and where it has got to is worked out from
        the speeds sent — each one aimed at where it should be by the next.
        At the end it's stopped and told the exact place (a small correction,
        if any), and told again a moment later: straight after a turn at a
        speed, OBSBOTs have been seen to take an older place for one axis."""
        with self._lock:
            if self._gen != gen or self.fd is None:
                return self.position()
            self._halt_speed()
            start = {k: float(self.target.get(k, self.ctrls[k]["default"])) for k in goal}
            dist = max(abs(goal[k] - start[k]) for k in goal) / ARCSEC
            duration = max(duration, 1.6 * dist / SPEED_MAX)       # at its fastest (1.5× the average) it stays in range
            t0 = time.time()
            self._est = dict(start)
            self._speeding, self._speed_gen = True, gen
            self._spd, self._spd_at = (0.0, 0.0), t0
            self.move_until = max(self.move_until, t0 + duration + 0.3)
            self.turns.append((t0, t0 + duration + 0.3))
        self.gliding = True
        halted = stopped = False
        misses = 0                                                 # speeds in a row the camera didn't take
        try:
            while True:
                stopped = stop is not None and bool(stop())
                with self._lock:
                    if self._gen != gen or not self._speeding or self._speed_gen != gen:
                        halted = True                              # another move stopped it, and takes over
                        break
                    if self.fd is None:
                        raise ValueError("the camera went away")
                    now = time.time()
                    if stopped or now - t0 >= duration:
                        break
                    self._integrate(now)
                    x = min(1.0, (now + SPEED_TICK - t0) / duration)
                    eased = x * x * (3 - 2 * x)                    # slow to start, slow to stop
                    v = []
                    for k in ("pan", "tilt"):
                        dps = 0.0
                        if k in goal:
                            want = start[k] + (goal[k] - start[k]) * eased
                            dps = (want - self._est[k]) / ARCSEC / SPEED_TICK
                            if dps * (goal[k] - start[k]) < 0:
                                dps = 0.0                          # a late tick took it a little ahead: never back
                        v.append(dps)
                    try:
                        self._speed(*v)
                        misses = 0
                    except OSError as e:                           # busy a moment: the last speed carries on
                        misses += 1
                        if now - self._err_logged > 10:
                            self._err_logged = now
                            log.info("ptz: the camera didn't take a speed (%s) — carrying on", e.strerror or e)
                        if misses >= 4:                            # the link's gone bad: stop rather than run on blind
                            log.warning("ptz: the camera stopped taking speeds — stopping the turn")
                            break
                time.sleep(SPEED_TICK)
        finally:
            with self._lock:
                if self._speeding and self._speed_gen == gen:
                    self._halt_speed()
            self.gliding = False
        if halted:
            return self.position()
        for again in (False, True):
            with self._lock:
                if self._gen != gen or self.fd is None or self.follow_on:
                    break                                          # another move, or it follows someone: leave it
                if not again:
                    values = {k: self._clamp(k, self._est[k] if stopped else goal[k]) for k in goal}
                try:
                    self._write(dict(values))
                except (OSError, ValueError) as e:
                    log.info("ptz: couldn't finish the turn: %s", e)
                    break
                for k in values:
                    self._want[k] = float(self.target[k])
            if not again:
                time.sleep(0.3)
        return self.position()

    # -- the stick ------------------------------------------------------------------------------------

    def drive(self, vx, vy, sid="", seq=0, speed=None, swap_x=None, swap_y=None):
        """The stick is here: -1…1 each way (right and up positive). Sent a
        few times a second while it is held; the camera stops when it isn't."""
        with self._lock:
            if not self.available():
                raise ValueError("no camera that turns is plugged in")
            if self.looking or (self.follow_on and not self._paused):
                self._vel = (0.0, 0.0)
                return self.position()                   # it's looking around, or following you: the stick waits
            if sid:
                if seq <= self._seq.get(sid, -1):
                    return self.position()               # overtaken by a newer message (a stop, say)
                self._seq[sid] = seq
                if len(self._seq) > 50:
                    self._seq = dict(list(self._seq.items())[-20:])
            clip = lambda v: max(-1.0, min(1.0, float(v or 0)))   # noqa: E731
            self._vel = (clip(vx), clip(vy))
            self._vel_at = time.time()
            if any(self._vel):
                self.touched_at = self._vel_at
                self._gen += 1                                    # you took the stick: a glide stops
                self._halt_speed()                                # (one at a speed stops right now, where it's got to)
            self._opts = {k: v for k, v in (("speed", speed), ("swap_x", swap_x), ("swap_y", swap_y)) if v is not None}
            if any(self._vel) and not self.driving():
                for k in ("pan", "tilt"):
                    if k in self.ctrls:
                        self._want[k] = float(self.target.get(k, 0))
                self._driver = threading.Thread(target=self._drive_loop, daemon=True, name="ptz-stick")
                self._driver.start()
            return self.position()

    def stop(self, sid="", seq=0):
        with self._lock:
            if sid:
                self._seq[sid] = max(int(seq or 0), self._seq.get(sid, -1))
            self._vel = (0.0, 0.0)
            return self.position() if self.fd is not None else {}

    def _drive_loop(self):
        last = time.time()
        while True:
            time.sleep(TICK)
            with self._lock:
                now = time.time()
                vx, vy = self._vel
                if now - self._vel_at > HEARTBEAT:
                    vx = vy = 0.0                         # the page went quiet: stop where it is
                if (not vx and not vy) or self.fd is None:
                    self._driver = None
                    return
                if now < self.busy_until:                 # the camera is still taking the last move
                    continue
                # the time since the last move, so a camera slow to take moves still turns at the stick's speed
                dt, last = min(0.6, now - last), now
                try:
                    speed = float(self._opts.get("speed") or self.conf.get("speed", 60) or 60)
                except (TypeError, ValueError):
                    speed = 60.0
                speed /= 1 + 3 * self._zoom_frac()        # zoomed in, the same stick turns it gentler
                sx, sy = self._swaps()
                values = {}
                for k, v, sign in (("pan", vx, sx), ("tilt", vy, sy)):
                    if not v or k not in self.ctrls:
                        continue
                    lo, hi = self._range(k)                   # never below the floor, however long it's held
                    want = self._want.get(k, float(self.target.get(k, 0))) + _curve(v) * sign * speed * dt * ARCSEC
                    self._want[k] = max(float(lo), min(float(hi), want))
                    raw = self._clamp(k, self._want[k])
                    if raw != self.target.get(k):
                        values[k] = raw
                if values:
                    try:
                        self._write(values)
                    except ValueError as e:
                        log.info("ptz: %s", e)
                        self._driver = None
                        return
                    except OSError as e:
                        if self.fd is None:                 # unplugged: the next touch of the stick finds it again
                            self._driver = None
                            return
                        if now - self._err_logged > 10:     # busy for a moment: keep going, a little slower
                            self._err_logged = now
                            log.info("ptz: the camera didn't take a move (%s) — trying again", e.strerror or e)

    # -- looking ---------------------------------------------------------------------------------------

    def settle(self, timeout=3.5):
        """Wait until the picture stops changing — the gimbal has arrived —
        and return the frame (or the last one, at the timeout)."""
        from . import camera
        from .watcher import Watcher
        camera.CAMERA.touch(30)
        self.wait_awake()                                     # it was asleep: let it lift itself and turn back first
        time.sleep(max(0.0, min(timeout, self.move_until - time.time())))
        end = time.time() + timeout
        prev, still, seq, frame = None, 0, 0, None
        while time.time() < end:
            seq, _ = camera.CAMERA.wait_jpeg(seq, timeout=1.0)
            got = camera.CAMERA.frame()
            if got is None:
                continue
            frame = got
            small = Watcher._small(frame)
            if prev is not None and Watcher._difference(prev, small) < 2.5:
                still += 1
                if still >= 2:
                    break
            else:
                still = 0
            prev = small
        return frame

    def sweep(self, spread=None, back=True):
        """Look left, ahead and right of where it points now, zoomed out;
        [(label, frame)]. Turns back (and zooms back) after. A camera that
        was following someone stops for the look and follows again after."""
        with self._lock:
            if not self.available() or "pan" not in self.ctrls:
                raise ValueError("this camera can't turn left and right")
            self.looking = True
        try:
            self.wake("a look around")                        # asleep, it would look at the desk
            with self.paused_follow():
                return self._sweep(spread, back)
        finally:
            self.looking = False

    def _sweep(self, spread, back):
        start = self.position()
        try:
            spread = float(spread or self.conf.get("sweep_degrees", 70) or 70)
        except (TypeError, ValueError):
            spread = 70.0
        sx, _ = self._swaps()
        lo, hi = start["pan_range"]
        plan = []
        for label, off in (("LEFT", -spread), ("AHEAD", 0.0), ("RIGHT", spread)):
            pan = max(lo, min(hi, start["pan"] + off * sx))
            if not any(abs(pan - p) < 10 for _, p in plan):
                plan.append((label, pan))
        frames = []
        try:
            for i, (label, pan) in enumerate(plan):
                if i == 0 and start["zoom"] > 0:
                    self.set(zoom=0.0)                            # zoomed right out for the look
                self.glide(pan=pan, tilt=start["tilt"])           # gently, not in a snap
                frame = self.settle()
                if frame is not None:
                    frames.append((label, frame))
                if back or i < len(plan) - 1:
                    time.sleep(SWEEP_HOLD)                        # it stops and looks before it moves on
        finally:
            try:
                if back:
                    self.glide(pan=start["pan"], tilt=start["tilt"])
                    if start["zoom"] > 0:
                        self.set(zoom=start["zoom"])
            except (OSError, ValueError) as e:
                log.info("ptz: couldn't turn back: %s", e)
        return frames

    # -- following you (the camera's own tracking) -----------------------------------------------------

    def _find_follow(self):
        """An OBSBOT follows people by itself, switched through its vendor unit.
        Only an OBSBOT is asked: another maker's unit 2 means something else."""
        self.xu_unit, self.follow_on, self.follow_mode = None, None, None
        if "obsbot" not in self.card.lower() and vendor_of(self.device or "") != OBSBOT_VENDOR:
            return
        for unit in (2, 3, 4, 5, 6):                     # a Tiny 2 has it at 2
            try:
                block = xu_query(self.fd, unit, XU_SETTINGS, UVC_GET_CUR)
            except OSError as e:
                if e.errno == errno.ENOENT:                 # no such unit: the next one
                    continue
                log.info("ptz: the camera's own tracking can't be reached (%s)", e.strerror or e)
                return
            self.xu_unit = unit
            self._note_follow(block)
            return

    def _note_follow(self, block):
        self._asked_at = time.time()
        if any(block):                                      # all zeros is no answer (a dead link reads the same)
            self.asleep = block[STATUS_ASLEEP] != 0
        mode = block[STATUS_MODE]
        self.follow_mode = AI_MODES.get(mode, f"mode {mode}")
        if mode != 6:                                       # 6: halfway through changing its mind
            self.follow_on = mode in FOLLOWING

    def _read_block(self):
        """The camera's status block, noted (it answers asleep too); None when it won't say."""
        try:
            block = xu_query(self.fd, self.xu_unit, XU_SETTINGS, UVC_GET_CUR)
        except OSError as e:
            if e.errno in _GONE:
                self._lost(e)
            return None
        self._note_follow(block)
        return block

    def read_follow(self):
        """Ask the camera whether it is following someone (it answers asleep too)."""
        with self._lock:
            if self.fd is None or self.xu_unit is None:
                return None
            if self._read_block() is None:
                return None
            return self.follow_on

    def _apply_follow(self, on):
        """Switch the camera's tracking and check that it took. Returns what the
        camera then says (True/False), or None when it wouldn't say."""
        was = self.follow_on
        for attempt in range(2):
            with self._lock:
                if self.fd is None or self.xu_unit is None:
                    raise ValueError("this camera can't follow anyone by itself")
                try:
                    xu_query(self.fd, self.xu_unit, XU_SETTINGS, UVC_SET_CUR,
                             bytes([TAG_TRACKING, 0x02, 0x02 if on else 0x00, 0x00]))
                except OSError as e:
                    if e.errno in _GONE:
                        self._lost(e)
                    raise
            end = time.time() + 1.6
            while time.time() < end:
                time.sleep(0.2)
                if self.read_follow() == on and self.follow_mode != "switching":
                    break
            if self.follow_on == on:
                break
            if attempt == 0:
                self._wake()                                # a sleeping camera ignores changes: wake it and ask again
        if not on and was is not False:
            self._regain()
        return self.follow_on

    def _regain(self):
        """After following, the camera points wherever the person was. If the
        driver can say where that is, carry on from there; if it only
        remembers what it was told (the usual case), put the camera back where
        PiE-ink last pointed it — so the stick, "look left" and the looks
        around all start from a place that's known."""
        with self._lock:
            if self.fd is None:
                return
            axes = [k for k in ("pan", "tilt") if k in self.ctrls]
            if not axes:
                return
            live = {}
            try:
                for k in axes:
                    if self._cache_stale:                     # turned at a speed since: the driver's copy is old
                        break
                    live[k] = g_ctrl(self.fd, self.ctrls[k]["id"])
            except OSError:
                live = {}
            if len(live) < len(axes):
                live = {}
            if live and any(abs(live[k] - self.target.get(k, 0)) > 1.5 * ARCSEC for k in axes):
                self.target.update(live)
                for k in axes:
                    self._want[k] = float(live[k])
                log.info("ptz: done following — it points at pan %.0f°, tilt %.0f°",
                         live.get("pan", 0) / ARCSEC, live.get("tilt", 0) / ARCSEC)
                return
            try:
                self._write({k: self._clamp(k, self.target.get(k, self.ctrls[k]["default"])) for k in axes})
                self.move_until = max(self.move_until, time.time() + 2.5)   # it may have a long way back
            except (OSError, ValueError) as e:
                log.info("ptz: couldn't turn back after following: %s", e)

    def _wake(self, wait=4.0):
        """The camera takes changes only while it's awake and streaming: wake it
        if it's asleep (you flipped the switch, so even with waking off), and
        have it stream."""
        from . import camera
        camera.CAMERA.touch(20)
        self.wake("a change to it", force=True)
        st = camera.CAMERA.status()
        if st.get("running") and st.get("age") is not None and st["age"] < 1.0:
            return
        seq, _ = camera.CAMERA.wait_jpeg(-1, timeout=0)
        camera.CAMERA.wait_jpeg(seq, timeout=wait)

    # -- asleep: an OBSBOT with its lens turned down ---------------------------------------------------

    def _command(self, cmd, receiver, payload):
        """One of OBSBOT's framed commands, through the vendor unit."""
        self._xu_seq = self._xu_seq % 0xFFFF + 1
        xu_query(self.fd, self.xu_unit, XU_COMMAND, UVC_SET_CUR, v3_frame(self._xu_seq, cmd, receiver, payload))

    def _send_run(self, why, again=False):
        try:
            self._command(CMD_RUN_STATE, TO_CAMERA, b"\x00\x00\x00\x00")
        except OSError as e:
            if e.errno in _GONE:
                self._lost(e)
            log.info("ptz: couldn't wake the camera (%s)", e.strerror or e)
            return False
        self._woke_at = time.time()
        self.waking_until = self._woke_at + 4.0 + RISE
        if not again:
            log.info("ptz: the camera is asleep — waking it (%s)", why or "it's being used")
        return True

    def wake(self, why="", wait=3.0, force=False):
        """Wake an OBSBOT that has fallen asleep — lens turned down, after a
        while unused, or pushed down by hand — since asleep it ignores every
        move. Sends "run" (the bytes OBSBOT's app sends), waits for it to say
        it's awake and for the gimbal to lift itself level, then points it back
        where PiE-ink had it. True if it was asleep and woke. Quick when it's
        awake (one question to the camera); nothing at all for other cameras,
        or when "Wake it when it's used" is off (unless force)."""
        if not force and not self.conf.get("wake", True):
            return False
        with self._lock:
            if self.fd is None:
                self._looked_for = 0.0                        # look for it now, not in ten seconds
            if not self.available() or self.xu_unit is None:
                return False
            if self._read_block() is None or not self.asleep:
                return False
            if time.time() < self._wake_off_until and not force:
                return False
            if not self._send_run(why):
                return False
        up, tries, end = False, 1, time.time() + wait
        while not up:
            time.sleep(0.25)
            with self._lock:
                if self.fd is None or self.xu_unit is None:
                    self.waking_until = 0.0
                    return False
                up = self._read_block() is not None and self.asleep is False
            if not up and time.time() >= end:
                if tries >= 2:
                    break
                tries, end = tries + 1, time.time() + wait
                with self._lock:                              # once more: the first can go missing
                    if self.fd is None or not self._send_run(why, again=True):
                        break
        if not up:
            # it may be up and just not saying so: point it back anyway, and ask less and less often
            self._wake_fails += 1
            rest = min(3600.0, WAKE_BACKOFF * 2 ** (self._wake_fails - 1))
            self._point_back()
            self.waking_until = 0.0
            self._wake_off_until = time.time() + rest
            log.info("ptz: the camera didn't say it woke up — not asking again for %d min", rest // 60)
            return False
        time.sleep(RISE)                                      # it lifts itself level before it takes a move
        self.wakes += 1
        self._wake_fails = 0
        self._point_back()
        self.waking_until = time.time() + 1.0
        log.info("ptz: the camera is awake")
        return True

    def _point_back(self):
        """Just woken, it looks level and wherever it likes side to side: back
        to where PiE-ink had it — or to home's height, if that was down where
        it sleeps (read that way when it was found asleep)."""
        with self._lock:
            if self.fd is None or self.follow_on:
                return                                        # following: it points itself
            axes = [k for k in ("pan", "tilt") if k in self.ctrls]
            if not axes:
                return
            values = {k: self._clamp(k, self.target.get(k, self.ctrls[k]["default"])) for k in axes}
            if "tilt" in values and self.target.get("tilt", 0) < self._range("tilt")[0]:
                h = self.conf.get("home") or {}
                values["tilt"] = self._clamp("tilt", float(h.get("tilt", 0) or 0) * ARCSEC)
            try:
                self._write(values)
            except (OSError, ValueError) as e:
                log.info("ptz: couldn't point it back after waking: %s", e)
                return
            for k in values:
                self._want[k] = float(self.target[k])
            self.move_until = max(self.move_until, time.time() + 2.0)

    def wake_soon(self, why=""):
        """wake(), in the background — the camera starting, a move, the keeper:
        nothing waits on it. Only an OBSBOT is asked, not more than every few
        seconds, and not at all when "Wake it when it's used" is off."""
        if not self.conf.get("wake", True):
            return False
        if self.fd is not None and self.xu_unit is None:
            return False                                      # not an OBSBOT: nothing to wake
        now = time.time()
        if (self.asleep is False and now - self._asked_at < 3.0) or now < self._wake_off_until:
            return False                                      # it said it's awake a moment ago
        t = self._waking
        if t is not None and t.is_alive():
            return True
        self._waking = threading.Thread(target=self._wake_quietly, args=(why,), daemon=True, name="ptz-wake")
        self._waking.start()
        return True

    def _wake_quietly(self, why):
        try:
            self.wake(why)
        except Exception as e:
            log.info("ptz: waking the camera: %s", e)

    def wait_awake(self, timeout=8.0):
        """A wake under way (the gimbal lifting itself): wait for it to finish."""
        t = self._waking
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)

    def _changed(self, on):
        if self.on_follow_change:
            try:
                self.on_follow_change(bool(on))
            except Exception as e:
                log.info("ptz: couldn't save following %s: %s", "on" if on else "off", e)

    def can_follow(self):
        return self.available() and self.xu_unit is not None

    def set_follow(self, on):
        """The switch: follow people (the camera's own tracking) or don't. It
        stays that way — saved, and put back if the camera changes it."""
        if not self.can_follow():
            raise ValueError("this camera can't follow anyone by itself" if self.available()
                             else "no camera that turns is plugged in")
        self._wake()
        got = self._apply_follow(bool(on))
        self.follow_want = bool(on)
        self._changed(on)
        return got

    def take_over(self):
        """You're steering (the stick, Center, "look left"): following stops,
        and stays off. True if it had been on."""
        if self._paused or self.xu_unit is None or not (self.follow_on or self.follow_want):
            return False
        try:
            self._apply_follow(False)
        except (OSError, ValueError) as e:
            log.info("ptz: couldn't stop following: %s", e)
        self.follow_want = False
        self._changed(False)
        return True

    @contextlib.contextmanager
    def paused_follow(self):
        """A look of PiE-ink's own (a look around, the agent's glance): the
        camera stops following while it looks, and follows again after."""
        with self._lock:
            pause = (self.xu_unit is not None and not self._paused
                     and bool(self.follow_on or self.follow_want))
            self._paused += 1
        try:
            if pause:
                try:
                    self._apply_follow(False)
                except (OSError, ValueError) as e:
                    log.info("ptz: couldn't stop following for a look: %s", e)
            yield
        finally:
            with self._lock:
                self._paused -= 1
            if pause and self.follow_want:
                try:
                    self._apply_follow(True)
                except (OSError, ValueError) as e:
                    log.info("ptz: couldn't follow again after the look: %s", e)

    def keep_once(self):
        """Keep the camera awake while it's used, and its tracking where the
        switch says. Asleep (lens down) while PiE-ink streams from it, it's
        woken. A raised palm toggles tracking on the camera, and some start up
        tracking; either way it is put back here — only while the camera
        streams, as asleep it ignores the change."""
        awake, using = self._awake(), self._in_use()
        if self.fd is None:
            if not (awake or using):
                return                                      # nothing is using the camera: don't go looking
            self.available()
        with self._lock:
            if self.fd is None or self.xu_unit is None:
                return
            if self.stop_owed and not self._speeding:
                self.settle_gimbal("owed from a turn that couldn't be stopped")
            if self._read_block() is None:
                return
        if self.asleep and using:
            self.wake_soon("it's in use")
            return
        if self._paused or self.looking or not awake or self.follow_on is None or self.follow_mode == "switching":
            return
        if self.follow_on != self.follow_want:
            log.info("ptz: the camera %s by itself — turning it %s, as the Follow me switch says",
                     "is following someone" if self.follow_on else "stopped following", "on" if self.follow_want else "off")
            self._apply_follow(self.follow_want)

    @staticmethod
    def _awake():
        """Is a USB camera streaming right now (so it's awake and takes changes)?"""
        from . import camera
        st = camera.CAMERA.status()
        return bool(st.get("running")) and st.get("age") is not None and st["age"] < 3.0 \
            and str(st.get("id") or "").startswith("usb:")

    @staticmethod
    def _in_use():
        """Is PiE-ink using a USB camera right now — streaming, or trying to?"""
        from . import camera
        st = camera.CAMERA.status()
        return bool(st.get("running")) and str(st.get("id") or "").startswith("usb:")

    def _keep(self):
        while not self._stop.wait(KEEP_EVERY):
            try:
                self.keep_once()
            except Exception as e:
                log.debug("ptz keeper: %s", e)

    def start(self):
        """The keeper: every few seconds, the camera's tracking is checked against the switch."""
        if self._keeper is None or not self._keeper.is_alive():
            self._stop.clear()
            self._keeper = threading.Thread(target=self._keep, daemon=True, name="ptz-keeper")
            self._keeper.start()

    def stop_keeping(self):
        self._stop.set()

    def shutdown(self):
        """The service stopping: a turn under way is stopped, so the gimbal
        isn't left turning (and then pushing against its end stop) with nobody
        to tell it otherwise."""
        self.stop_keeping()
        with self._lock:
            self._gen += 1
            self._halt_speed()

    def turning_near(self, when, before=1.0, after=4.0):
        """Was the camera's motor turning it around `when` (from a moment before a turn to a few seconds after)?
        A motor starting draws a burst of current, and a hub without its own power can reset what else is on it."""
        return any(start - before <= when <= end + after for start, end in list(self.turns))

    def turned_for(self, window=600.0):
        """Seconds its motors have been turning it in the last `window` seconds — every move counted,
        the friend's, yours, the bot's — so whoever moves it can let them rest."""
        now = time.time()
        since = now - window
        total = 0.0
        for start, end in list(self.turns):
            end = min(end, now)
            if end > since:
                total += end - max(start, since)
        return total

    def status(self):
        ok = self.available()
        return {"available": ok, "device": self.device, "card": self.card, "error": self.error,
                "position": self.position() if ok else None, "home": self.conf.get("home") or None,
                "moving": ok and (self.driving() or time.time() < self.move_until), "looking": self.looking,
                "last_look": self.last_look, "speed": self.conf.get("speed", 60),
                "follow": {"can": ok and self.xu_unit is not None, "on": self.follow_on,
                           "want": self.follow_want, "mode": self.follow_mode, "paused": self._paused > 0},
                "turned": round(self.turned_for()), "stop_owed": self.stop_owed,
                "sleep": {"can": ok and self.xu_unit is not None, "asleep": self.asleep,
                          "waking": time.time() < self.waking_until, "wakes": self.wakes,
                          "wake": bool(self.conf.get("wake", True))}}


def composite(frames, tile=(400, 300)):
    """The views of a look around, side by side, each labelled — one
    picture for the model instead of three."""
    from PIL import Image, ImageDraw, ImageOps
    from .text import font
    w, h = tile
    bar, gap = 28, 4
    n = max(1, len(frames))
    img = Image.new("RGB", (w * n + gap * (n - 1), h + bar), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f = font(1, 18)
    for i, (label, frame) in enumerate(frames):
        x = i * (w + gap)
        img.paste(ImageOps.fit(frame.convert("RGB"), (w, h)), (x, bar))
        d.text((x + 8, 4), label, font=f, fill=(0, 0, 0))
    return img


# -- "look left", "look around", "zoom in" -------------------------------------------------------

_LEAD = r"^(?:(?:please|can you|could you|would you|will you|hey|now|ok|okay|go on|go ahead and|and|then|just)[ ,]+)*"
_A_BIT = r"(?: (?:a (?:bit|little|touch)|slightly|a tad))?"
_TAIL = r"(?:[ ,]+(?:please|now))?(?:$|[ ,]+(?:and|then)[ ,]+(?P<q>.+)$)"


def parse(text):
    """A camera command in plain words, or None. {"kind": "around" |
    "turn" | "center" | "home" | "zoom" | "follow", ...}. Only whole
    commands count: "look up the weather" is a question, not a tilt."""
    low = " ".join(re.sub(r"[^a-z0-9' ,-]+", " ", (text or "").lower().replace("’", "'")).split())
    if not low:
        return None
    what = r"(?:following(?: me)?|follow me|tracking(?: me)?)"
    if re.match(_LEAD + r"(?:(?:stop|quit|no more|don't|do not|dont) (?:following|follow|tracking|track)(?: me| us)?(?: around)?"
                r"|(?:stay|keep) (?:still|put)|(?:turn|switch) off " + what + r"|(?:turn|switch) " + what + r" off)" + _TAIL, low):
        return {"kind": "follow", "on": False}
    if re.match(_LEAD + r"(?:(?:start |keep )?(?:follow|following|track|tracking) (?:me|us)(?: around)?"
                r"|(?:turn|switch) on " + what + r"|(?:turn|switch) " + what + r" on|keep (?:your|the) (?:eye|eyes|camera) on me)"
                + _TAIL, low):
        return {"kind": "follow", "on": True}
    m = re.match(_LEAD + r"(?:(?:have|take) a look|look|looking|glance|scan|turn|pan)(?: (?:all|right))? (?:a)?round(?: the room| you| yourself)?" + _TAIL, low) \
        or re.match(_LEAD + r"(?:scan|survey|check|sweep) (?:the room|the area|around|your surroundings)" + _TAIL, low)
    if m:
        return {"kind": "around", "question": (m.group("q") or "").strip() or None}
    m = re.match(_LEAD + r"(?:look|turn|pan|point|face|rotate|swing)(?: the camera| your camera| yourself| your head| over)?"
                 + r"(?P<bit>" + _A_BIT + r")(?: (?:to|towards) (?:the|your))? (?:further )?(?P<dir>left|right)(?:wards)?"
                 + r"(?P<bit2>" + _A_BIT + r")" + _TAIL, low)
    if m:
        step = 15.0 if (m.group("bit") or m.group("bit2")) else 30.0
        return {"kind": "turn", "dpan": step if m.group("dir") == "right" else -step, "dtilt": 0.0,
                "where": m.group("dir"), "question": (m.group("q") or "").strip() or None}
    m = re.match(_LEAD + r"(?:look|tilt|point|turn|aim)(?: the camera| your camera| your head)?"
                 + r"(?P<bit>" + _A_BIT + r") (?:further )?(?P<dir>up|down)(?:wards?)?(?P<bit2>" + _A_BIT + r")" + _TAIL, low)
    if m:
        step = 10.0 if (m.group("bit") or m.group("bit2")) else 20.0
        return {"kind": "turn", "dpan": 0.0, "dtilt": step if m.group("dir") == "up" else -step,
                "where": m.group("dir"), "question": (m.group("q") or "").strip() or None}
    m = re.match(_LEAD + r"what(?:'s| is| do you see| can you see)?(?: (?:on|to|at|over))? (?:your|the) (?P<dir>left|right)\b", low)
    if m:
        return {"kind": "turn", "dpan": 30.0 if m.group("dir") == "right" else -30.0, "dtilt": 0.0,
                "where": m.group("dir"), "question": None}
    if re.match(_LEAD + r"(?:(?:look|point|face|turn)(?: the camera)? (?:straight(?: ahead)?|ahead|forwards?|to the front|front)"
                r"|(?:center|centre|reset|recenter|recentre|straighten)(?: the| your)? camera"
                r"|(?:center|centre) (?:yourself|the view))" + _TAIL, low):
        return {"kind": "center"}
    if re.match(_LEAD + r"(?:(?:go|turn|point|look)(?: the camera)? back home|camera home|(?:go|turn|point) the camera home"
                r"|home position|look home)" + _TAIL, low):
        return {"kind": "home"}
    m = re.match(_LEAD + r"zoom (?P<all>(?:all the way|fully|right|as far as you can) )?(?P<dir>in|out)(?: (?:more|further))?"
                 + r"(?P<bit>" + _A_BIT + r")" + _TAIL, low)
    if m:
        if m.group("all"):
            return {"kind": "zoom", "to": 1.0 if m.group("dir") == "in" else 0.0}
        step = 0.1 if m.group("bit") else 0.25
        return {"kind": "zoom", "dz": step if m.group("dir") == "in" else -step}
    return None


PTZ = Ptz()
