"""Camera: a black-and-white frame from the camera, once a second."""
import time
from PIL import Image, ImageOps

from ..camera import CAMERA
from ..text import draw_block, font
from .base import Mode


class CameraMode(Mode):
    name = "camera"
    label = "Camera"

    @property
    def interval(self):
        return max(0.5, float(self.settings.get("interval", 1.0)))

    def on_button(self, name):
        """Key 4 on the 2.7" HAT: the next camera."""
        if name in ("key4", "next"):
            from .. import camera as cam
            cam.CAMERA.next_camera()
            return True
        return False

    def start(self):
        CAMERA.touch(60)

    def render(self):
        CAMERA.touch(15)          # keep it running while we're the active mode
        img = CAMERA.frame()
        f = self.frame()
        if img is None:
            msg = CAMERA.error or "Starting camera…"
            draw_block(f.draw, msg, font(1, 13), (0, 0, f.width, f.height), f.fg,
                       align="center", valign="center")
            return f.image
        s = self.settings
        g = img.convert("L")
        if s.get("auto_contrast", True):
            g = ImageOps.autocontrast(g, cutoff=2)
        if s.get("mirror"):
            g = ImageOps.mirror(g)
        rot = int(s.get("rotation", 0)) % 360
        if rot:
            g = g.rotate(rot, expand=True)
        if s.get("fit", "cover") == "cover":
            g = ImageOps.fit(g, (f.width, f.height), method=Image.LANCZOS)
        else:
            g = ImageOps.pad(g, (f.width, f.height), method=Image.LANCZOS, color=255)
        if f.dark:
            g = ImageOps.invert(g)
        dither = Image.FLOYDSTEINBERG if s.get("dither", True) else Image.NONE
        out = g.convert("1", dither=dither)
        # a corner label for a few seconds after a switch, so you know which camera this is
        from .. import camera as cam
        if len([c for c in cam.available() if c["id"] != "mock"]) > 1 and \
                time.time() - getattr(CAMERA, "switched_at", 0) < 8:
            from PIL import ImageDraw
            label = (CAMERA.status().get("source") or "").replace("USB camera ", "cam ")
            if label:
                d = ImageDraw.Draw(out)
                fnt = font(1, 11)
                w = d.textlength(label, font=fnt) + 8
                d.rectangle([2, 2, 2 + w, 16], fill=0 if not f.dark else 1)
                d.text((6, 3), label, font=fnt, fill=1 if not f.dark else 0)
        return out
