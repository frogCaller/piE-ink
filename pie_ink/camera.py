"""Camera capture in the background.

One thread grabs frames continuously while anyone wants them (the Camera
mode on the panel, or a browser watching the stream) and stops itself after
a while when nobody does. The panel takes one frame a second; the web page
takes as many as the Pi can encode.

Sources, tried in order: the Pi camera (picamera2), a USB webcam (OpenCV),
or a moving test pattern so the page works without any camera.
"""
import errno
import fcntl
import io
import logging
import math
import os
import struct
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

STREAM_SIZE = (480, 360)  # what we ask a camera for; it answers with the nearest size it has
IDLE_STOP = 20.0          # seconds without a consumer before the camera is released
TARGET_FPS = 10           # frames a second to the page when the Pi has to decode and re-encode each one
RAW_FPS = 12              # and when the camera's own JPEGs are passed straight through
BIG_FRAME = 250_000       # a JPEG this large (a camera with no small mode) goes out at a gentler rate
PHOTO_SIZE = (1920, 1080)  # a photo is taken at the camera's biggest JPEG size up to this
STALL_SECONDS = 2.5       # no new frame for this long and the camera is closed and opened again
COME_BACK = 45.0          # how long a camera that stopped is waited for before giving up on it

# A webcam that stops sending (a USB hiccup, a firmware that chokes while its gimbal turns) leaves
# OpenCV waiting 10 s for each frame that never comes. A few seconds is plenty to call it stuck.
os.environ.setdefault("OPENCV_VIDEOIO_V4L_SELECT_TIMEOUT", "3")


class _PiCamera:
    name = "Pi camera"
    id = "picamera"

    def __init__(self):
        from picamera2 import Picamera2
        # libcamera lists USB webcams too; only take a real (CSI) Pi camera here,
        # webcams go through the USB path below
        cams = Picamera2.global_camera_info()
        csi = [i for i, c in enumerate(cams) if "usb" not in str(c.get("Id", "")).lower()
               and "uvc" not in str(c.get("Model", "")).lower()]
        if not csi:
            raise RuntimeError("no Pi camera attached" + (" (only a USB one)" if cams else ""))
        self.cam = Picamera2(camera_num=csi[0])
        cfg = self.cam.create_video_configuration(main={"size": STREAM_SIZE, "format": "RGB888"})
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(0.3)

    def read(self):
        return self.cam.capture_image("main").convert("RGB")

    def close(self):
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:
            pass


# V4L2: VIDIOC_QUERYCAP and the capability bits that matter
_VIDIOC_QUERYCAP = 0x80685600
_CAP_VIDEO_CAPTURE = 0x00000001
_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
_CAP_VIDEO_M2M = 0x00008000
_CAP_VIDEO_M2M_MPLANE = 0x00004000
_CAP_META_CAPTURE = 0x00800000
_CAP_DEVICE_CAPS = 0x80000000


def query_node(path):
    """What a /dev/videoN is, asked of the kernel directly: {"driver", "card",
    "capture"} — capture is True only for a node that gives frames (a webcam
    has one of those and one metadata node; the Pi's own codec, ISP and
    camera-front-end nodes aren't cameras at all). None if it can't be opened."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM):
            return {"driver": "", "card": "", "capture": False, "error": "no permission"}
        return None
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buf)
    except OSError:
        return None
    finally:
        os.close(fd)
    driver = bytes(buf[0:16]).split(b"\0")[0].decode(errors="ignore")
    card = bytes(buf[16:48]).split(b"\0")[0].decode(errors="ignore")
    caps, device_caps = struct.unpack_from("<II", buf, 84)
    use = device_caps if caps & _CAP_DEVICE_CAPS else caps
    capture = bool(use & (_CAP_VIDEO_CAPTURE | _CAP_VIDEO_CAPTURE_MPLANE)) \
        and not use & (_CAP_VIDEO_M2M | _CAP_VIDEO_M2M_MPLANE) and not use & _CAP_META_CAPTURE
    return {"driver": driver, "card": card, "capture": capture, "error": None}


NOT_CAMERAS = ("bcm2835-codec", "bcm2835-isp", "rpi-hevc", "pispbe", "rp1-cfe", "unicam", "pisp")


def video_devices(details=False):
    """The /dev/video* that could be webcams, USB ones first — never the Pi's
    own codec and ISP nodes, which OpenCV would otherwise sit and wait on."""
    try:
        names = sorted((d for d in os.listdir("/dev") if d.startswith("video") and d[5:].isdigit()),
                       key=lambda d: int(d[5:]))
    except OSError:
        return []
    found = []
    for dev in names:
        info = query_node(f"/dev/{dev}")
        if info is None:
            continue
        if info.get("error"):
            found.append((dev, info))
            continue
        if not info["capture"] or any(x in (info["card"] + info["driver"]).lower() for x in NOT_CAMERAS):
            continue
        found.append((dev, info))
    found.sort(key=lambda t: (0 if t[1]["driver"] == "uvcvideo" else 1, int(t[0][5:])))
    return found if details else [dev for dev, _ in found]


class _UsbCamera:
    """A webcam through OpenCV's V4L2 backend.

    Three things keep it quick on a small Pi. The size is chosen from what
    the camera actually offers (a 4K camera asked for 480x360 can answer with
    1080p, or 4K, and a Zero 2 W then spends its whole second decoding one
    frame). The camera's own MJPEG frames go to the page as they are, with
    no decode and re-encode in between — the Pi only decodes a frame when
    the panel or the watcher wants pixels, and then at a reduced size. And
    whatever has piled up in the driver's queue while we were busy is thrown
    away before a frame is taken, so the picture is the newest one, not one
    from a second ago.
    """
    name = "USB camera"

    def __init__(self, device=None, card=None):
        """device: that /dev/videoN only. card: any, but that camera first
        (it may have come back as another /dev/videoN after a USB hiccup)."""
        try:
            import cv2
        except ImportError:
            raise RuntimeError("python3-opencv is not installed (sudo apt install python3-opencv)")
        self.cv2 = cv2
        self.cap = None
        self.raw = False                 # the camera's JPEGs pass straight through
        self.format = ""                 # "640x480 MJPG" — what was negotiated
        self.size = STREAM_SIZE
        self.card = ""
        if device:
            devices = [os.path.basename(device)]
        else:
            found = video_devices(details=True)
            devices = [d for d, i in found if card and (i.get("card") or "") == card] + \
                      [d for d, i in found if not card or (i.get("card") or "") != card]
        problems = []
        for dev in devices:
            info = query_node(f"/dev/{dev}") or {}
            if info.get("error"):
                problems.append(f"{dev}: {info['error']} (add yourself to the video group: sudo usermod -aG video $USER)")
                continue
            cap = cv2.VideoCapture(f"/dev/{dev}", cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                problems.append(f"{dev}: wouldn't open")
                continue
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                try:
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)   # newer OpenCV: the same, per camera
                except Exception:
                    pass
            # MJPEG at a modest size: a fraction of the USB bandwidth of raw YUYV, which is what
            # a webcam left to its own devices picks — and what starves it on a Zero 2 W's one port.
            # The size comes from the camera's own list, so a 4K camera doesn't answer 480x360 with 1080p.
            fourcc, size = choose_format(formats(f"/dev/{dev}"))
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            frame = None
            give_up = time.time() + 8.0
            for _ in range(20):                         # webcams drop the first frames; a slow Pi takes a while
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    break
                frame = None
                if time.time() > give_up:               # a camera that answers nothing: don't sit here a minute
                    break
                time.sleep(0.15)
            if frame is None and self._obsbot(dev, info):
                frame = self._wake_and_read(cap)         # asleep, an OBSBOT can send nothing at all
            if frame is not None:
                self.cap = cap
                label = info.get("card") or "USB camera"
                self.card = info.get("card") or ""
                self.name = f"{label} ({dev})"
                self.id = f"usb:/dev/{dev}"
                got = _fourcc_name(int(cap.get(cv2.CAP_PROP_FOURCC)))
                self.size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or size[0], int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or size[1])
                self.format = f"{self.size[0]}x{self.size[1]} {got}"
                if got == "MJPG":
                    self.raw = self._try_raw()
                if self.raw:
                    self.format += ", passed through"
                log.info("camera: %s on /dev/%s — %s", label, dev, self.format)
                break
            cap.release()
            problems.append(f"{dev}: opened but gave no frames")
        if self.cap is None:
            raise RuntimeError("no USB camera answered" + (f" — {'; '.join(problems)}" if problems
                               else " (nothing on /dev/video* looks like a camera)"))

    @staticmethod
    def _obsbot(dev, info):
        from . import ptz
        return "obsbot" in (info.get("card") or "").lower() or ptz.vendor_of(f"/dev/{dev}") == ptz.OBSBOT_VENDOR

    @staticmethod
    def _wake_and_read(cap):
        """An OBSBOT asleep may send no picture at all: wake it, and ask again."""
        from . import ptz
        try:
            if not ptz.PTZ.wake("it sent no picture"):
                return None
        except Exception as e:
            log.debug("camera: couldn't wake it: %s", e)
            return None
        give_up = time.time() + 6.0
        while time.time() < give_up:
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                return frame
            time.sleep(0.15)
        return None

    def still(self, most=PHOTO_SIZE):
        """A photo: one frame at the camera's biggest JPEG size up to `most`,
        then back to the stream's size. JPEG bytes, or None when the stream's
        own frame is as big as it gets. Raises if the camera won't."""
        cv2 = self.cv2
        sizes = formats(self.id.split(":", 1)[1]).get("MJPG") or []
        fits = [s for s in sizes if s[0] <= most[0] and s[1] <= most[1]]
        if not fits:
            return None
        big = max(fits, key=lambda s: s[0] * s[1])
        if big[0] * big[1] <= self.size[0] * self.size[1] * 1.2:
            return None
        got = None
        try:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, big[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, big[1])
            end, seen = time.time() + 6.0, 0
            while time.time() < end and got is None:
                ok, frame = self.cap.read()
                if not ok or frame is None or not frame.size:
                    time.sleep(0.05)
                    continue
                seen += 1
                if seen < 2:                                   # the first can be one queued before the change
                    continue
                if self.raw:
                    data = frame.reshape(-1).tobytes()
                    if data[:2] == b"\xff\xd8" and _jpeg_size(data) == big:
                        end_at = data.rfind(b"\xff\xd9")
                        got = data[:end_at + 2] if end_at > 0 else data
                elif frame.shape[1] == big[0] and frame.shape[0] == big[1]:
                    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    got = enc.tobytes() if ok else None
        finally:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])     # back to the stream's size
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
            if self.raw:
                for _ in range(8):                             # still the camera's own JPEGs?
                    ok, frame = self.cap.read()
                    if ok and frame is not None and frame.size:
                        data = frame.reshape(-1)
                        if not (data.size > 4 and data[0] == 0xFF and data[1] == 0xD8):
                            self.raw = self._try_raw()
                        break
                    time.sleep(0.05)
        if got is None:
            raise RuntimeError(f"it didn't send a {big[0]}x{big[1]} picture")
        return got

    def _try_raw(self):
        """Ask OpenCV for the camera's JPEG bytes instead of decoded pixels.
        Works with the V4L2 backend in OpenCV 4.1 and later; checked, not assumed."""
        cv2 = self.cv2
        try:
            if not self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 0):
                return False
            for _ in range(5):
                ok, frame = self.cap.read()
                if ok and frame is not None and frame.size:
                    data = frame.reshape(-1)
                    if data.size > 4 and data[0] == 0xFF and data[1] == 0xD8:
                        return True
                    break
                time.sleep(0.05)
        except Exception as e:
            log.debug("camera: no raw JPEG from OpenCV (%s)", e)
        try:
            self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        except Exception:
            pass
        return False

    def _freshest(self):
        """Drop what queued up while we were away: a grab that comes back at
        once was a stale buffer; one that had to wait is the live frame."""
        for _ in range(4):
            t = time.time()
            if not self.cap.grab():
                raise RuntimeError("USB camera read failed")
            if time.time() - t > 0.004:
                break

    def read(self):
        """The newest frame: JPEG bytes when they pass through, else a PIL image."""
        self._freshest()
        ok, frame = self.cap.retrieve()
        if not ok or frame is None or frame.size == 0:
            raise RuntimeError("USB camera read failed")
        if self.raw:
            data = frame.reshape(-1)
            if data.size > 4 and data[0] == 0xFF and data[1] == 0xD8:
                raw = data.tobytes()
                end = raw.rfind(b"\xff\xd9")               # the buffer can run past the JPEG's end marker
                return raw[:end + 2] if end > 0 else raw
            raise RuntimeError("the camera sent something that isn't a JPEG")
        h, w = frame.shape[:2]
        if w > STREAM_SIZE[0] * 1.5:                    # decoding a big frame is bad enough; don't encode it big too
            scale = (STREAM_SIZE[0] * 1.5) / w
            frame = self.cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=self.cv2.INTER_AREA)
        return Image.fromarray(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))

    def close(self):
        self.cap.release()


# -- what a camera can send, asked of the kernel -------------------------------------------------

_VIDIOC_ENUM_FMT = 0xC0405602            # _IOWR('V', 2, struct v4l2_fmtdesc)
_VIDIOC_ENUM_FRAMESIZES = 0xC02C564A     # _IOWR('V', 74, struct v4l2_frmsizeenum)
_BUF_TYPE_VIDEO_CAPTURE = 1


def _jpeg_size(data):
    """(w, h) from a JPEG's header, or None."""
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return None


def _fourcc_name(code):
    try:
        return struct.pack("<I", int(code) & 0xFFFFFFFF).decode("ascii", errors="replace").strip("\0 ")
    except (struct.error, ValueError):
        return "?"


def formats(path):
    """{"MJPG": [(w, h), ...], "YUYV": [...]}: every pixel format a camera
    offers and the sizes it offers it at. Empty if it can't be asked."""
    out = {}
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return out
    try:
        for index in range(32):
            buf = bytearray(64)
            struct.pack_into("<II", buf, 0, index, _BUF_TYPE_VIDEO_CAPTURE)
            try:
                fcntl.ioctl(fd, _VIDIOC_ENUM_FMT, buf)
            except OSError:
                break
            pix = struct.unpack_from("<I", buf, 44)[0]
            name = _fourcc_name(pix)
            sizes = []
            for si in range(64):
                fb = bytearray(44)
                struct.pack_into("<III", fb, 0, si, pix, 0)
                try:
                    fcntl.ioctl(fd, _VIDIOC_ENUM_FRAMESIZES, fb)
                except OSError:
                    break
                kind = struct.unpack_from("<I", fb, 8)[0]
                if kind == 1:                                       # discrete
                    sizes.append(tuple(struct.unpack_from("<II", fb, 12)))
                else:                                               # stepwise or continuous: its ends
                    min_w, max_w, _, min_h, max_h, _ = struct.unpack_from("<IIIIII", fb, 12)
                    sizes += [(min_w, min_h), (max_w, max_h)]
                    break
            out[name] = sorted(set(sizes), key=lambda wh: wh[0] * wh[1])
    finally:
        os.close(fd)
    return out


def choose_format(offered, want=STREAM_SIZE):
    """(fourcc, (w, h)) to ask for: MJPEG at the smallest size that covers
    `want` (else the largest below it); raw YUYV only when the camera has no
    small MJPEG mode. With nothing to go on, MJPEG at `want` and hope."""
    def pick(sizes):
        covering = [s for s in sizes if s[0] >= want[0] and s[1] >= want[1]]
        if covering:
            return min(covering, key=lambda wh: wh[0] * wh[1])
        return max(sizes, key=lambda wh: wh[0] * wh[1]) if sizes else None
    mjpg = pick(offered.get("MJPG") or [])
    yuyv = pick(offered.get("YUYV") or [])
    if mjpg and mjpg[0] <= 1280:
        return "MJPG", mjpg
    if yuyv and yuyv[0] <= 800:                     # raw is heavy on the bus; only a small one
        return "YUYV", yuyv
    if mjpg:
        return "MJPG", mjpg
    if yuyv:
        return "YUYV", yuyv
    return "MJPG", want


class _TestPattern:
    name = "test pattern (no camera found)"
    id = "mock"

    def __init__(self):
        self.t0 = time.time()

    def read(self):
        w, h = STREAM_SIZE
        t = time.time() - self.t0
        img = Image.new("RGB", (w, h), (235, 235, 225))
        d = ImageDraw.Draw(img)
        for i in range(0, w, 40):
            d.line([(i, 0), (i, h)], fill=(215, 215, 205))
        for j in range(0, h, 40):
            d.line([(0, j), (w, j)], fill=(215, 215, 205))
        x = w / 2 + math.cos(t * 1.3) * w * 0.32
        y = h / 2 + math.sin(t * 0.9) * h * 0.3
        d.ellipse([x - 40, y - 40, x + 40, y + 40], fill=(40, 40, 40))
        d.rectangle([20, h - 60, 240, h - 20], fill=(60, 60, 60))
        d.text((30, h - 52), "NO CAMERA · " + datetime.now().strftime("%H:%M:%S"), fill=(240, 240, 240))
        return img

    def close(self):
        pass


_found = {"ts": 0.0, "list": []}


def available(refresh=False):
    """Every camera this Pi could use: [{id, label}]. Probing means opening
    each device briefly, so the answer is kept for a minute."""
    if not refresh and time.time() - _found["ts"] < 60 and _found["list"]:
        return list(_found["list"])
    out = []
    import importlib.util
    if importlib.util.find_spec("picamera2") is not None:
        out.append({"id": "picamera", "label": "Pi camera"})
    for dev, info in video_devices(details=True):
        label = (info.get("card") or "USB camera").split(":")[0].strip()
        if info.get("error"):
            label += " — no permission"
        out.append({"id": f"usb:/dev/{dev}", "label": f"{label} ({dev})"})
    out.append({"id": "mock", "label": "Test pattern"})
    # a list with a real camera in it can be kept for a minute; an empty one is asked again next time
    _found.update(ts=time.time() if len(out) > 1 else 0.0, list=out)
    return list(out)


class Camera:
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._jpeg = None
        self._ts = 0.0
        self._seq = 0
        self._new = threading.Condition(self._lock)
        self._wanted_until = 0.0
        self._thread = None
        self._source = None
        self.error = None
        self.source_name = None
        self.preferred = None            # which camera to use; None = the settings decide
        self.fps = 0.0                   # what the loop is managing, measured
        self.restarts = 0                # times it stopped sending and was opened again
        self.stalled_at = 0.0            # when that last happened
        self._still_req = None           # a photo asked for: the capture loop takes it between frames
        self._no_still = set()           # cameras that wouldn't take a bigger picture: the stream's will do
        self.shooting = False            # taking one now
        self._paused_until = 0.0         # paused (Settings → Sound): nothing starts the camera till then
        self._small = None               # (seq, width, jpeg): the newest frame made smaller, for slow links

    def switch(self, source):
        """Use a different camera. The capture loop restarts on the next frame
        wanted, so anyone watching just sees the picture change."""
        self.preferred = source or "auto"
        self.switched_at = time.time()
        _found["ts"] = 0.0
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            wanted = self._wanted_until
            self._wanted_until = 0.0            # lets the loop finish
        if running:
            self._thread.join(timeout=5)
        if wanted > time.time():
            self.touch(int(wanted - time.time()) + 1)

    def next_camera(self):
        """Cycle to the next one that exists. Returns the new id."""
        cams = [c["id"] for c in available() if c["id"] != "mock"] or ["mock"]
        current = getattr(self._source, "id", None) or self.preferred or "auto"
        if current in cams:
            chosen = cams[(cams.index(current) + 1) % len(cams)]
        else:
            chosen = cams[0]
        self.switch(chosen)
        return chosen

    # -- consumers ---------------------------------------------------------------

    def pause(self, seconds):
        """Let go of the camera for a while, whoever wants it: on a Pi Zero's one
        USB link, a speaker or mic plugged in then has it to itself. 0: back now."""
        seconds = max(0.0, float(seconds or 0))
        with self._lock:
            self._paused_until = time.time() + seconds if seconds else 0.0
            thread = self._thread
            if seconds:
                self._wanted_until = 0.0                   # the capture loop finishes and lets go
        if seconds and thread is not None and thread.is_alive():
            thread.join(timeout=5)
        log.info("camera: %s", f"paused for {seconds:.0f} s" if seconds else "back")
        return self.paused_for()

    def paused_for(self):
        """Seconds left of a pause, or 0."""
        return max(0, int(round(self._paused_until - time.time())))

    def touch(self, seconds=IDLE_STOP):
        """Tell the camera someone wants frames for a while; starts it if needed."""
        with self._lock:
            if time.time() < self._paused_until:
                return                                     # paused: it stays off
            self._wanted_until = max(self._wanted_until, time.time() + seconds)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
                self._thread.start()

    def frame(self):
        """Latest frame as a PIL RGB image, or None. A frame that came as a
        JPEG is decoded here, once, at a reduced size — the panel and the
        watcher need pixels; the page never does."""
        with self._lock:
            if self._frame is not None:
                return self._frame.copy()
            jpg, seq = self._jpeg, self._seq
        if not jpg:
            return None
        try:
            im = Image.open(io.BytesIO(jpg))
            im.draft("RGB", (640, 480))              # libjpeg scales while decoding: a 4K frame costs a 720p one
            im = im.convert("RGB")
        except Exception as e:
            log.debug("camera: couldn't decode a frame: %s", e)
            return None
        with self._lock:
            if self._seq == seq:
                self._frame = im
        return im.copy()

    def jpeg(self):
        with self._lock:
            return self._jpeg

    def wait_jpeg(self, last_seq, timeout=1.0):
        """Block until a frame newer than last_seq exists; returns (seq, jpeg)."""
        with self._new:
            if self._seq == last_seq:
                self._new.wait(timeout)
            return self._seq, self._jpeg

    def small_jpeg(self, width=640, quality=70):
        """The newest frame as a smaller JPEG — a quarter of the bytes or less
        at 640 wide — for a viewer whose link can't take the camera's own
        frames. Made once per frame however many are watching. Returns (seq,
        jpeg); the jpeg is None when there is no frame or it wouldn't decode."""
        with self._lock:
            seq, jpg, frame, small = self._seq, self._jpeg, self._frame, self._small
        if small and small[0] == seq and small[1] == width:
            return seq, small[2]
        if not jpg:
            return seq, None
        try:
            if frame is None:
                im = Image.open(io.BytesIO(jpg))
                im.draft("RGB", (width, max(1, width * im.size[1] // max(1, im.size[0]))))   # scaled while decoding
                im = im.convert("RGB")
            else:
                im = frame
            if im.size[0] > width:
                im = im.resize((width, max(1, im.size[1] * width // im.size[0])), Image.BILINEAR)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            out = buf.getvalue()
        except Exception as e:
            log.debug("camera: couldn't make a smaller frame: %s", e)
            return seq, None
        with self._lock:
            self._small = (seq, width, out)
        return seq, out

    def still(self, timeout=12.0):
        """A photo, as JPEG bytes: the camera's best size (up to PHOTO_SIZE),
        taken by the capture loop between frames — or the stream's newest
        frame, when the camera can't (a Pi camera, the test pattern, one that
        won't change size). None when there's no picture at all."""
        if self.paused_for():
            return None                                    # paused: the last frame is from before
        self.touch(30)
        running = lambda: self._thread is not None and self._thread.is_alive()   # noqa: E731
        seq, jpg = self.wait_jpeg(-1, timeout=0)
        if not jpg:                                        # starting up: wait for the first frame
            end = time.time() + 8.0
            while not jpg and time.time() < end and running():
                seq, jpg = self.wait_jpeg(seq, timeout=0.5)
        if not jpg:
            return None                                    # no camera would open
        req = {"done": threading.Event(), "jpeg": None}
        with self._lock:
            self._still_req = req
        end = time.time() + timeout
        while not req["done"].wait(0.5):
            if time.time() > end or not running():
                with self._lock:
                    if self._still_req is req:
                        self._still_req = None
                break
        return req["jpeg"] or self.jpeg()

    def _take_still(self, req):
        """In the capture loop: the photo asked for."""
        src, jpg = self._source, None
        key = getattr(src, "id", None)
        if hasattr(src, "still") and key not in self._no_still:
            self.shooting = True                           # the picture pauses a moment: not a stall
            try:
                jpg = src.still()
                if jpg is None:
                    self._no_still.add(key)                # its stream is as big as it gets
            except Exception as e:
                self._no_still.add(key)                    # don't try that again on this camera
                log.info("camera: no bigger picture for a photo (%s) — the stream's will do", e)
            finally:
                self.shooting = False
        req["jpeg"] = jpg or self._jpeg
        req["done"].set()

    def _woken(self):
        """A USB camera just started sending: an OBSBOT asleep with its lens
        down is woken (in the background — the picture doesn't wait on it)."""
        if not str(getattr(self._source, "id", "") or "").startswith("usb:"):
            return
        try:
            from . import ptz
            ptz.PTZ.wake_soon("the camera started")
        except Exception as e:
            log.debug("camera: couldn't ask the camera to wake: %s", e)

    def status(self):
        running = self._thread is not None and self._thread.is_alive()
        age = (time.time() - self._ts) if self._ts else None
        stalled = running and age is not None and age > STALL_SECONDS and not self.shooting
        return {"running": running,
                "source": self.source_name, "id": getattr(self._source, "id", None),
                "format": getattr(self._source, "format", "") or "", "fps": 0.0 if stalled else round(self.fps, 1),
                "error": self.error, "age": age, "stalled": stalled, "restarts": self.restarts,
                "stalled_at": self.stalled_at or None, "paused_for": self.paused_for()}

    # -- capture loop ----------------------------------------------------------------

    def _open(self, preferred):
        if preferred and preferred.startswith("usb:"):
            node = preferred.split(":", 1)[1]

            def any_usb():
                """The picked node gave nothing (the numbers moved after a replug or a
                reboot, or it's now something else): whatever USB camera answers."""
                src = _UsbCamera()
                log.info("camera: %s didn't answer — using %s instead", node, src.name)
                return src
            any_usb.name = "another USB camera"
            order = [lambda: _UsbCamera(node), any_usb]
        else:
            order = {"picamera": [_PiCamera], "usb": [_UsbCamera], "mock": [_TestPattern]}.get(
                preferred, [_PiCamera, _UsbCamera, _TestPattern])
        errors = []
        for cls in order:
            try:
                src = cls()
                self.source_name = src.name
                self.error = None if cls is not _TestPattern else ("; ".join(errors) or None)
                return src
            except Exception as e:
                errors.append(f"{getattr(cls, 'name', 'USB camera')}: {e}")
        raise RuntimeError("; ".join(errors))

    def _open_again(self, was, card):
        """The same camera as before, after it stopped: first its old node, then
        wherever it turned up (unplugged and back, it can be another /dev/videoN).
        No falling back to the test pattern here — that's for a camera that's gone."""
        if was == "picamera":
            src = _PiCamera()
        elif str(was or "").startswith("usb:"):
            try:
                src = _UsbCamera(was.split(":", 1)[1])
            except Exception:
                src = _UsbCamera(card=card)
        else:
            return self._open(was or "auto")
        self.source_name = src.name
        self.error = None
        return src

    def _reopen(self, why, quiet_for):
        """The camera stopped sending — a USB hiccup, or a firmware that stalls
        while its gimbal turns. OpenCV would wait on it forever; close it and
        open it again, and the picture carries on. False when it doesn't come
        back (and the loop ends: the next person to look starts it afresh)."""
        self.restarts += 1
        self.stalled_at = time.time()
        was, card = getattr(self._source, "id", None), getattr(self._source, "card", "")
        log.warning("camera: no picture for %.0f s (%s) — opening it again (%d so far)", quiet_for, why, self.restarts)
        try:
            self._source.close()
        except Exception:
            pass
        self._source = None
        try:
            from . import ptz                                # its turning controls may be on a node that's gone
            ptz.PTZ.check()
        except Exception:
            pass
        delay, give_up = 0.5, time.time() + COME_BACK
        while time.time() < min(give_up, self._wanted_until):
            time.sleep(delay)
            try:
                src = self._open_again(was, card)
            except Exception as e:
                self.error = f"the camera stopped sending and hasn't come back yet ({e})"
                delay = min(4.0, delay * 2)
                continue
            self._source = src
            self.source_name = getattr(src, "name", None) or self.source_name
            if not isinstance(src, _TestPattern):          # the test pattern keeps its note on why it's there
                self.error = None
            log.info("camera: back — %s", self.source_name)
            self._woken()
            return True
        log.warning("camera: it didn't come back — %s", self.error)
        return False

    def _run(self):
        from . import settings as cfg
        preferred = self.preferred or cfg.load().get("camera", {}).get("source", "auto")
        self._ts = 0.0
        try:
            self._source = self._open(preferred)
        except Exception as e:
            self.error = str(e)
            log.warning("camera: %s", e)
            with self._new:                          # no stale picture standing in for a camera that won't open
                self._frame = self._jpeg = None
                self._new.notify_all()
            return
        self.source_name = getattr(self._source, "name", None) or self.source_name
        if self.error:
            log.info("camera: no real camera answered — %s", self.error)
        log.info("camera started: %s", self.source_name)
        self._woken()
        note = self.error                            # the test pattern says why it's the test pattern
        last, good, fails = 0.0, time.time(), 0
        self.fps = 0.0
        try:
            while time.time() < self._wanted_until:
                req = self._still_req
                if req is not None:                          # a photo: taken here, between frames
                    self._still_req = None
                    self._take_still(req)
                    good = time.time()
                t = time.time()
                try:
                    got = self._source.read()
                except Exception as e:
                    fails += 1
                    self.error = f"{self.source_name}: {e}"
                    if fails == 1:
                        log.warning("camera read failed: %s", e)
                    quiet_for = time.time() - good
                    if fails >= 3 or quiet_for >= STALL_SECONDS:
                        if not self._reopen(e, quiet_for):
                            return
                        note, last, good, fails = self.error, 0.0, time.time(), 0
                    else:
                        time.sleep(0.3)
                    continue
                if fails:
                    self.error, fails = note, 0
                good = t
                if isinstance(got, (bytes, bytearray)):      # the camera's own JPEG, untouched
                    img, jpg = None, bytes(got)
                else:
                    img = got
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=70)
                    jpg = buf.getvalue()
                with self._new:
                    self._frame, self._jpeg, self._ts = img, jpg, t
                    self._seq += 1
                    self._new.notify_all()
                if last:
                    self.fps = 0.8 * self.fps + 0.2 / max(1e-3, t - last)
                last = t
                # a camera with no small mode sends big frames: a gentler rate spares the Wi-Fi
                period = 1.0 / (RAW_FPS if getattr(self._source, "raw", False) else TARGET_FPS)
                wait = max(period, 0.25) if len(jpg) > BIG_FRAME else period
                time.sleep(max(0.0, wait - (time.time() - t)))
        finally:
            if self._source is not None:
                self._source.close()
            self._source = None
            log.info("camera stopped (idle)")


CAMERA = Camera()


class Viewer:
    """One page watching the stream, kept real-time however slow its link.

    The server holds little back for a client that isn't taking it (see
    _serve() in app.py), so the stream's generator waits while the socket
    sends each frame: a link that can't keep up skips frames instead of piling
    them up — the picture is a little choppier, never seconds behind. And
    when the socket is holding each frame for a good part of the camera's
    frame time, the link is saturated and frames queue in what buffering
    there is, so the frames go out smaller instead (a quarter of the bytes at
    640 wide, then a tenth at 480): a moving picture that keeps up. Now and
    then the camera's own frames are tried again, for longer each time they
    don't fit.

    How long the socket held a frame is known one frame late: handing over a
    frame only waits for the one before it to have mostly gone."""
    LEVELS = ((640, 70), (480, 55))    # width and JPEG quality of the lighter frames
    BIG = 90_000                       # a frame this large is worth sending smaller; below it, nothing would be gained
    WINDOW = 3.0                       # judged over the last few seconds
    TOO_SLOW = 0.5                     # the socket holding frames this share of the camera's frame time, on average: saturated
    AT_ONCE = 1.0                      # two in a row held a whole frame time or more: no need to wait and see
    SETTLED = 0.1                      # holding them less than this: keeping up
    ENOUGH = 3                         # frames in the window before judging
    WARM_UP = 2.0                      # the first moments on a lighter level don't count (what queued up is still going)
    TRY_AGAIN = 90.0                   # seconds of lighter frames before the camera's own are tried again (doubles each time)

    def __init__(self):
        self.seq = 0
        self.level = -1                # -1: the camera's own frames; 0, 1: lighter ones
        self.since = time.time()
        self.hold = self.TRY_AGAIN
        self.sent = []                 # (when, held / frame time, counts) for the frames sent in the window
        self.queue = []                # (bytes, counts) of the last frames handed over, oldest first
        self.yielded_at = 0.0          # when the last frame was handed over to be sent
        self.changes = 0

    def next(self, camera, timeout=2.0):
        """Wait for the next frame; the camera's own or a lighter one. None on a timeout."""
        arrived = time.time()
        held = arrived - self.yielded_at if self.yielded_at else 0.0   # how long the socket held the last frame
        seq, jpg = camera.wait_jpeg(self.seq, timeout)
        self.seq = seq
        if not jpg:
            self.yielded_at, self.queue = 0.0, []
            return None
        now = time.time()
        period = 1.0 / max(1.0, camera.fps or RAW_FPS)
        if len(self.queue) == 2:                            # the frame before last is what the socket was holding
            size, counts = self.queue.pop(0)
            self.sent.append((now, held / period, counts))
        self.sent = [s for s in self.sent if s[0] >= now - self.WINDOW]
        self._judge(now)
        if self.level >= 0:
            width, quality = self.LEVELS[self.level]
            _, small = camera.small_jpeg(width, quality)
            jpg = small or jpg
        self.queue.append((len(jpg), self.level >= 0 or len(jpg) > self.BIG))
        self.yielded_at = time.time()
        return jpg

    def _behind(self, counting=False):
        """How long the socket held each frame lately, as a share of the camera's frame
        time — over the frames since this level's first moments, when what had queued
        up on the last one was still going. (share, how many frames that is)."""
        picked = [b for when, b, counts in self.sent if when >= self.since + self.WARM_UP and (counts or not counting)]
        return (sum(picked) / len(picked) if picked else 0.0), len(picked)

    def _judge(self, now):
        if self.level < 0:
            # the camera's own frames: two in a row held a whole frame time or more says it all —
            # nothing had queued up before them, so that was the link (a hiccup holds one, not two)
            last = [b for _, b, counts in self.sent[-2:] if counts]
            if len(last) == 2 and min(last) >= self.AT_ONCE:
                self._lighter(now, f"is holding each frame {min(last):.1f}× the camera's frame time")
                return
        else:
            behind, n = self._behind()
            if now - self.since >= self.hold and n >= self.ENOUGH and behind < self.SETTLED:
                self._go(-1, now, f"has kept up with the smaller frames for {self.hold:.0f} s")
                return
        behind, n = self._behind(counting=True)
        if n >= self.ENOUGH and behind >= self.TOO_SLOW:
            self._lighter(now, f"is holding each frame {behind:.1f}× the camera's frame time")

    def _lighter(self, now, why):
        if self.level >= len(self.LEVELS) - 1:
            return
        if self.level < 0 and self.changes:                 # the camera's own didn't fit after a try: the next waits longer
            self.hold = min(600.0, self.hold * 2)
        self._go(self.level + 1, now, why)

    def _go(self, level, now, why):
        self.level, self.since, self.sent, self.changes = level, now, [], self.changes + 1
        what = f"sending {self.LEVELS[level][0]} px frames" if level >= 0 else "trying the camera's own frames again"
        log.info("camera: a viewer's link %s — %s", why, what)


# -- why isn't it found? ---------------------------------------------------------------------------

_USB_CLASSES = {"01": "audio", "02": "comms", "03": "hid", "08": "storage", "09": "hub", "0e": "video", "ff": "vendor"}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def usb_devices():
    """What is on the USB bus, from sysfs: [{"bus", "vid", "pid", "name", "classes", "speed"}]."""
    root = "/sys/bus/usb/devices"
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    for e in entries:
        d = os.path.join(root, e)
        if ":" in e or not os.path.exists(os.path.join(d, "idVendor")):
            continue
        vid, pid = _read(os.path.join(d, "idVendor")), _read(os.path.join(d, "idProduct"))
        if vid == "1d6b":                                # the Pi's own root hubs
            continue
        name = " ".join(x for x in (_read(os.path.join(d, "manufacturer")), _read(os.path.join(d, "product"))) if x)
        classes = set()
        for iface in entries:
            if iface.startswith(e + ":"):
                cls = _read(os.path.join(root, iface, "bInterfaceClass")).lower()
                classes.add(_USB_CLASSES.get(cls, cls))
        dev_class = _read(os.path.join(d, "bDeviceClass")).lower()
        if dev_class == "09":
            classes.add("hub")
        out.append({"bus": e, "vid": vid, "pid": pid, "name": name or f"{vid}:{pid}", "classes": sorted(classes),
                    "speed": _read(os.path.join(d, "speed")), "power_ma": _read(os.path.join(d, "bMaxPower"))})
    return out


def _kernel_lines(limit=30, keys=("usb", "uvc", "over-current", "overcurrent", "undervolt", "under-voltage", "video")):
    """The last kernel lines about USB (or whatever `keys` say), if we're
    allowed to read them: (lines, why not)."""
    import subprocess
    for cmd in (["dmesg", "--time-format", "reltime"], ["dmesg"], ["journalctl", "-k", "-n", "400", "--no-pager"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode != 0:
            continue
        lines = [ln for ln in r.stdout.splitlines() if any(k in ln.lower() for k in keys)]
        return lines[-limit:], None
    return [], "the kernel log isn't readable by this user (sudo dmesg shows it)"


def _in_video_group():
    try:
        import grp
        gid = grp.getgrnam("video").gr_gid
        return gid in os.getgroups() or os.getegid() == gid
    except (KeyError, OSError):
        return None


def probe():
    """Everything that bears on 'why isn't my USB camera found', in one go,
    with a plain-English verdict at the top. Opens each camera node briefly."""
    model = _read("/proc/device-tree/model").rstrip("\0")
    usb = usb_devices()
    cams_on_bus = [u for u in usb if "video" in u["classes"]]
    nodes = []
    try:
        names = sorted((d for d in os.listdir("/dev") if d.startswith("video") and d[5:].isdigit()), key=lambda d: int(d[5:]))
    except OSError:
        names = []
    for dev in names:
        info = query_node(f"/dev/{dev}")
        if info is None:
            continue
        nodes.append({"dev": dev, "driver": info.get("driver", ""), "card": info.get("card", ""),
                      "capture": info.get("capture", False), "error": info.get("error"),
                      "formats": formats(f"/dev/{dev}") if info.get("capture") and not info.get("error") else {}})
    try:
        import cv2
        opencv = cv2.__version__
    except ImportError:
        cv2 = None
        opencv = None
    tests = []
    if cv2:
        for n in nodes:
            if not n["capture"] or n["error"] or any(x in (n["card"] + n["driver"]).lower() for x in NOT_CAMERAS):
                continue
            cap = cv2.VideoCapture(f"/dev/{n['dev']}", cv2.CAP_V4L2)
            result = {"dev": n["dev"], "opened": bool(cap.isOpened()), "frames": 0, "size": None, "seconds": 0.0,
                      "asked": None, "got": None, "fps": None}
            if result["opened"]:
                fourcc, size = choose_format(n.get("formats") or {})
                result["asked"] = f"{size[0]}x{size[1]} {fourcc}"
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                result["got"] = (f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
                                 f"{_fourcc_name(int(cap.get(cv2.CAP_PROP_FOURCC)))}")
                t0 = time.time()
                first = None
                while time.time() - t0 < 3.0 and result["frames"] < 30:
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size:
                        result["frames"] += 1
                        first = first or time.time()
                        result["size"] = [int(frame.shape[1]), int(frame.shape[0])]
                    else:
                        time.sleep(0.1)
                result["seconds"] = round(time.time() - t0, 1)
                if first and result["frames"] > 1 and time.time() - first > 0.2:
                    result["fps"] = round((result["frames"] - 1) / (time.time() - first), 1)
            cap.release()
            tests.append(result)
    kernel, kernel_note = _kernel_lines()
    in_video = _in_video_group()

    # the verdict
    bad_power = any(k in " ".join(kernel).lower() for k in ("over-current", "overcurrent", "under-voltage", "undervolt",
                                                            "error -71", "not accepting address", "device descriptor read"))
    zero = "zero" in model.lower()
    if not cams_on_bus:
        verdict = ("The Pi doesn't see a camera on the USB bus at all, so nothing above the cable can help. "
                   + ("On a Zero 2 W that is nearly always power or the adapter: its one micro-USB port can't feed a webcam "
                      "on top of a mic and a speaker — put the camera on a hub with its own power supply, and make sure the "
                      "micro-USB adapter is a data (OTG) one, not a charging cable. " if zero else
                      "Try another port or cable, and a powered hub if the camera draws a lot. ")
                   + ("The kernel log below shows USB power trouble." if bad_power else
                      "Unplug and replug it and watch `dmesg -w` for what the kernel says."))
    elif not any(n["capture"] for n in nodes if not n["error"] and not any(x in (n["card"] + n["driver"]).lower() for x in NOT_CAMERAS)):
        if any(n["error"] for n in nodes):
            verdict = ("The camera is there but this user may not open it: add yourself to the video group "
                       "(sudo usermod -aG video $USER, then log out and in, or reboot).")
        else:
            verdict = ("The camera is on the bus but the kernel made no video node for it: the uvcvideo driver isn't loaded "
                       "(sudo modprobe uvcvideo) or this camera isn't a standard UVC webcam.")
    elif opencv is None:
        verdict = "python3-opencv is missing on this Pi: sudo apt install python3-opencv, then restart pie-ink."
    elif tests and not any(t["frames"] for t in tests):
        verdict = ("The camera opens but gives no frames. On a Zero 2 W that is usually power or USB bandwidth: try it alone on "
                   "the port or on a powered hub, and unplug the speaker/mic for a moment to see if it comes alive."
                   + (" The kernel log below shows USB power trouble." if bad_power else ""))
    elif tests:
        t = next(x for x in tests if x["frames"])
        verdict = (f"The camera works: {t['dev']} gave {t['frames']} frames at {t['size'][0]}x{t['size'][1]}"
                   + (f" ({t['fps']} a second, decoded on the Pi)" if t.get("fps") else f" in {t['seconds']} s") + ". "
                   "Pick it under 'Which camera' (or Auto), and press Next if another source is in the way."
                   + (" It answered a small MJPEG request with a big frame, so every frame costs the Pi a decode — "
                      "the formats list below shows what it offers." if t["size"] and t["size"][0] > 1280 else ""))
    else:
        verdict = "Nothing to test: no capture node was found."
    if CAMERA.restarts:
        when = time.strftime("%H:%M", time.localtime(CAMERA.stalled_at)) if CAMERA.stalled_at else "?"
        verdict += (f" Since pie-ink started, the picture stopped {CAMERA.restarts} time{'s' if CAMERA.restarts != 1 else ''}"
                    f" (last at {when}) and the camera was opened again. If that happens while a camera turns, its motors"
                    " may be short of power on this port — a powered USB hub usually cures it."
                    + (" The kernel log below shows USB power trouble." if bad_power else ""))
    return {"model": model, "usb": usb, "cameras_on_bus": len(cams_on_bus), "nodes": nodes, "opencv": opencv,
            "tests": tests, "in_video_group": in_video, "kernel": kernel, "kernel_note": kernel_note, "verdict": verdict,
            "restarts": CAMERA.restarts}


def probe_text(report=None):
    r = report or probe()
    lines = [f"Pi: {r['model'] or 'unknown'}", f"OpenCV: {r['opencv'] or 'MISSING'}",
             f"In the video group: {'yes' if r['in_video_group'] else 'NO' if r['in_video_group'] is False else 'unknown'}", "",
             f"USB devices ({len(r['usb'])}):"]
    for u in r["usb"] or []:
        lines.append(f"  {u['bus']:<8} {u['vid']}:{u['pid']}  {u['name']}  [{', '.join(u['classes']) or '?'}]"
                     + (f"  {u['speed']} Mb/s" if u['speed'] else "") + (f"  wants {u['power_ma']}" if u['power_ma'] else ""))
    if not r["usb"]:
        lines.append("  (nothing but the Pi itself)")
    lines += ["", f"Video nodes ({len(r['nodes'])}):"]
    for n in r["nodes"]:
        what = "camera" if n["capture"] else "not a camera"
        lines.append(f"  /dev/{n['dev']:<8} {n['driver']:<14} {n['card'][:30]:<30} {n['error'] or what}")
        for fmt, sizes in (n.get("formats") or {}).items():
            lines.append(f"      {fmt}: " + ", ".join(f"{w}x{h}" for w, h in sizes[:12]) + (" …" if len(sizes) > 12 else ""))
    if not r["nodes"]:
        lines.append("  (none)")
    if r["tests"]:
        lines += ["", "Opening them:"]
        for t in r["tests"]:
            lines.append(f"  /dev/{t['dev']}: " + ("wouldn't open" if not t["opened"] else
                         f"asked {t['asked']}, got {t['got']}: {t['frames']} frames"
                         + (f" at {t['size'][0]}x{t['size'][1]}" if t["size"] else "") + f" in {t['seconds']} s"
                         + (f", {t['fps']} a second" if t.get("fps") else "")))
    lines += ["", "Kernel log (USB):"]
    lines += [f"  {ln}" for ln in r["kernel"]] or [f"  {r['kernel_note'] or '(nothing about USB)'}"]
    lines += ["", "Verdict: " + r["verdict"]]
    return "\n".join(lines)


if __name__ == "__main__":                              # python3 -m pie_ink.camera
    print(probe_text())
