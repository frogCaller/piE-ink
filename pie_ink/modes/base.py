"""A mode is a thing that knows how to draw one frame for the attached panel.

Frames are drawn at SCALE x resolution through a scaling draw proxy, so
modes keep using panel coordinates. The panel downsamples and thresholds
the result; the web preview gets the full-size render, which is why it's
sharp instead of a blown-up bitmap.
"""
import functools

from PIL import Image, ImageDraw, ImageFont

SCALE = 3
DEFAULT_PANEL = {"width": 250, "height": 122, "kind": "eink"}


@functools.lru_cache(maxsize=128)
def _scaled_font(path, size, index, scale):
    return ImageFont.truetype(path, int(round(size * scale)), index=index)


def scale_font(fnt, scale):
    if scale == 1 or not isinstance(fnt, ImageFont.FreeTypeFont):
        return fnt
    return _scaled_font(fnt.path, fnt.size, getattr(fnt, "index", 0) or 0, scale)


class ScaledDraw:
    """ImageDraw that takes panel coordinates and draws them scaled up.
    textbbox() answers in panel coordinates too, so wrapping code doesn't
    need to know."""

    def __init__(self, image, scale):
        self.d = ImageDraw.Draw(image)
        self.s = scale

    def _xy(self, xy):
        s = self.s
        if not xy:
            return xy
        if isinstance(xy[0], (int, float)):
            return [v * s for v in xy]
        return [(x * s, y * s) for x, y in xy]

    def _w(self, width):
        return max(1, int(round(width * self.s)))

    def text(self, xy, text, font=None, fill=None, **kw):
        self.d.text(self._xy(xy), text, font=scale_font(font, self.s), fill=fill, **kw)

    def multiline_text(self, xy, text, font=None, fill=None, spacing=4, **kw):
        self.d.multiline_text(self._xy(xy), text, font=scale_font(font, self.s), fill=fill,
                              spacing=spacing * self.s, **kw)

    def textbbox(self, xy, text, font=None, **kw):
        box = self.d.textbbox(self._xy(xy), text, font=scale_font(font, self.s), **kw)
        return tuple(v / self.s for v in box)

    def line(self, xy, fill=None, width=1):
        self.d.line(self._xy(xy), fill=fill, width=self._w(width))

    def rectangle(self, xy, fill=None, outline=None, width=1):
        self.d.rectangle(self._xy(xy), fill=fill, outline=outline, width=self._w(width))

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._xy(xy), radius=radius * self.s, fill=fill,
                                 outline=outline, width=self._w(width))

    def polygon(self, xy, fill=None, outline=None):
        self.d.polygon(self._xy(xy), fill=fill, outline=outline)

    def arc(self, xy, start, end, fill=None, width=1):
        self.d.arc(self._xy(xy), start, end, fill=fill, width=self._w(width))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self.d.ellipse(self._xy(xy), fill=fill, outline=outline, width=self._w(width))


class Frame:
    """A blank canvas plus the colours for the current theme. width/height
    are panel units; image is SCALE times that."""

    def __init__(self, size, dark=False, scale=SCALE):
        self.dark = bool(dark)
        self.bg = 0 if self.dark else 255
        self.fg = 255 if self.dark else 0
        self.width, self.height = size
        self.scale = scale
        self.image = Image.new("L", (int(round(self.width * scale)), int(round(self.height * scale))), self.bg)
        self.draw = ScaledDraw(self.image, scale)

    def stamp(self, mask, xy, size):
        """Paint the foreground colour through a mask (an icon) at panel coords."""
        s = self.scale
        px = int(round(size * s))
        m = mask.convert("L").resize((px, px), Image.LANCZOS)
        x, y = int(round(xy[0] * s)), int(round(xy[1] * s))
        self.image.paste(self.fg, (x, y, x + px, y + px), mask=m)

    def blit(self, img, xy):
        """Paste a small panel-resolution image (e.g. an 18x18 icon) at panel coords."""
        s = self.scale
        big = img.convert("L").resize((img.width * s, img.height * s), Image.LANCZOS)
        self.image.paste(big, (int(round(xy[0] * s)), int(round(xy[1] * s))))


class Mode:
    name = "base"
    label = "Base"
    interval = 1.0          # seconds between render calls

    def __init__(self, config, panel=None):
        self.config = config
        self.panel = panel or DEFAULT_PANEL
        self.settings = config.get(self.name, {})

    @property
    def size(self):
        return (self.panel["width"], self.panel["height"])

    def start(self):
        """Called once when the mode becomes active."""

    def stop(self):
        """Called once when the mode is replaced."""

    def update(self, config):
        """Called when settings change while the mode is active."""
        self.config = config
        self.settings = config.get(self.name, {})

    def on_button(self, action):
        """A HAT key mapped to "prev"/"next". Return True if handled."""
        return False

    def frame(self):
        return Frame(self.size, self.config["display"]["dark_mode"])

    # -- fixed layouts on other panel sizes ---------------------------------------------
    # Some screens are hand-placed for 250x122. On a bigger panel they're drawn
    # at a larger scale (still crisp: fonts are rasterised at that size) and
    # centred. `fit_factor` says how much bigger the panel is than the layout.

    def fit_factor(self, layout=(250, 122)):
        return min(self.size[0] / layout[0], self.size[1] / layout[1])

    def fitted_frame(self, layout=(250, 122), k=None):
        k = k or self.fit_factor(layout)
        return Frame(layout, self.config["display"]["dark_mode"], scale=SCALE * k)

    def compose(self, sub, layout=(250, 122), k=None):
        """Paste a fitted layout frame into a full-panel frame."""
        k = k or self.fit_factor(layout)
        f = self.frame()
        ox = int(round((self.size[0] - layout[0] * k) / 2 * SCALE))
        oy = int(round((self.size[1] - layout[1] * k) / 2 * SCALE))
        f.image.paste(sub.image, (ox, oy))
        return f.image

    def render(self):
        return self.frame().image
