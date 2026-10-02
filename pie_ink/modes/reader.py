"""Read: a page of a book, or a tile of a comic, on the panel.

The reading state (which book, which page) lives in the module so the web
API and the mode share it. Pages are laid out by reader.Layout in the
background; until it's ready the panel shows progress.
"""
import threading
import time

from PIL import Image, ImageOps

from .. import reader
from ..text import draw_block, font
from .base import Frame, Mode


class ReaderState:
    def __init__(self):
        self.lock = threading.Lock()
        self.book = None          # library entry
        self.layout = None        # reader.Layout for text books
        self.pages = None         # reader.Pages for comics
        self.tiles = None         # (scaled image, [(x, y)...]) for the current comic page
        self.tile_page = None
        self.page = 0
        self.tile = 0
        self.changed = time.time()
        self.error = None

    def open(self, book):
        with self.lock:
            self.book = book
            self.layout = None
            self.pages = None
            self.tiles = None
            self.tile_page = None
            self.page = int(book.get("page", 0))
            self.tile = int(book.get("tile", 0))
            self.error = None
            self.changed = time.time()

    def reset_layout(self):
        with self.lock:
            self.layout = None
            self.tiles = None

    def total(self):
        if not self.book:
            return 0
        if self.book["kind"] == "text":
            return len(self.layout.pages) if self.layout and self.layout.pages else 0
        return self.pages.count if self.pages else 0

    def go(self, page, tile=0):
        total = self.total()
        with self.lock:
            self.page = max(0, min(page, max(0, total - 1))) if total else max(0, page)
            self.tile = max(0, tile)
            self.changed = time.time()
        if self.book:
            reader.set_position(self.book["id"], self.page, self.tile)

    def step(self, delta):
        """Next/previous page, or next/previous tile within a comic page."""
        if not self.book:
            return
        if self.book["kind"] == "pages" and self.tiles:
            n = len(self.tiles[1])
            t = self.tile + delta
            if 0 <= t < n:
                self.go(self.page, t)
                return
            if delta > 0 and self.page + 1 < self.total():
                self.go(self.page + 1, 0)
            elif delta < 0 and self.page > 0:
                self.go(self.page - 1, -1)        # -1 = last tile, resolved on render
            return
        self.go(self.page + delta)

    def status(self):
        with self.lock:
            ready = (self.layout is not None and self.layout.pages is not None) if (self.book and self.book["kind"] == "text") else self.pages is not None
            return {
                "book": self.book, "page": self.page, "tile": self.tile, "total": self.total(),
                "tiles": len(self.tiles[1]) if self.tiles else 0,
                "ready": ready, "error": self.error or (self.layout.error if self.layout else None),
                "progress": self.layout.progress if self.layout else (1.0 if ready else 0.0),
            }


STATE = ReaderState()


class ReaderMode(Mode):
    name = "reader"
    label = "Read"

    def __init__(self, config, panel=None):
        super().__init__(config, panel)
        self._last_auto = time.time()
        if STATE.book is None:
            lib = reader.library()
            if lib:
                STATE.open(lib[0])

    @property
    def interval(self):
        auto = float(self.settings.get("auto_seconds", 0) or 0)
        return max(1.0, auto) if auto else 5.0

    def on_button(self, action):
        if action in ("next", "prev"):
            STATE.step(1 if action == "next" else -1)
            self._last_auto = time.time()
            return True
        return False

    def update(self, config):
        old = dict(self.settings)
        super().update(config)
        if any(old.get(k) != self.settings.get(k) for k in ("font", "size", "rotation", "margin", "zoom")):
            STATE.reset_layout()

    # -- layout for the current panel/orientation ------------------------------------------

    @property
    def rotation(self):
        return int(self.settings.get("rotation", 0)) % 360

    def _page_size(self):
        w, h = self.size
        return (h, w) if self.rotation in (90, 270) else (w, h)

    def _rotate(self, img):
        """Page image -> panel orientation. 90 = display turned clockwise."""
        r = self.rotation
        return img.rotate(-r, expand=True) if r else img

    def _frame(self):
        """A frame in page orientation; rotated into the panel on the way out."""
        return Frame(self._page_size(), self.config["display"]["dark_mode"])

    def _finish(self, f):
        return self._rotate(f.image)

    def _message(self, text):
        f = self._frame()
        draw_block(f.draw, text, font(1, 13), (0, 0, f.width, f.height), f.fg, align="center", valign="center")
        return self._finish(f)

    # -- render ------------------------------------------------------------------------------

    def render(self):
        s = self.settings
        book = STATE.book
        if not book:
            return self._message("No book yet.\nUpload one in the Read tab.")
        auto = float(s.get("auto_seconds", 0) or 0)
        if auto and time.time() - self._last_auto >= auto:
            self._last_auto = time.time()
            STATE.step(1)
        if book["kind"] == "text":
            return self._render_text(book)
        return self._render_pages(book)

    def _render_text(self, book):
        s = self.settings
        pad = int(s.get("margin", 4))
        size = int(s.get("size", 12))
        page_size = self._page_size()
        with STATE.lock:
            if STATE.layout is None or STATE.layout.box != page_size or STATE.layout.size != size \
                    or STATE.layout.font_index != s.get("font", "Font.ttc") or STATE.layout.pad != pad:
                STATE.layout = reader.Layout(book, s.get("font", "Font.ttc"), size, page_size, pad)
            layout = STATE.layout
        if layout.error:
            return self._message(f"Can't read this book:\n{layout.error}")
        if layout.pages is None:
            return self._message(f"Preparing {book['title'][:28]}…\n{int(layout.progress * 100)}%")
        total = len(layout.pages)
        page = max(0, min(STATE.page, total - 1))
        f = self._frame()
        fnt = font(s.get("font", "Font.ttc"), size)
        lh = self.__class__._lh(fnt)
        y = pad
        for line in layout.pages[page]:
            if line:
                f.draw.text((pad, y), line, font=fnt, fill=f.fg)
            y += lh
        if s.get("page_numbers", True):
            label = f"{page + 1}/{total}"
            small = font(8, 8)
            tw = f.draw.textbbox((0, 0), label, font=small)[2]
            f.draw.text((f.width - tw - 2, f.height - 9), label, font=small, fill=f.fg)
        return self._finish(f)

    @staticmethod
    def _lh(fnt):
        from ..text import line_height
        return line_height(fnt)

    def _render_pages(self, book):
        s = self.settings
        page_size = self._page_size()
        zoom = float(s.get("zoom", 1.0) or 1.0)
        with STATE.lock:
            if STATE.pages is None:
                try:
                    STATE.pages = reader.Pages(book)
                except Exception as e:
                    STATE.error = str(e)
                    return self._message(f"Can't open this comic:\n{e}")
            pages = STATE.pages
        if pages.count == 0:
            return self._message("No pages found in this file.")
        page = max(0, min(STATE.page, pages.count - 1))
        with STATE.lock:
            if STATE.tiles is None or STATE.tile_page != page:
                try:
                    img = pages.image(page, int(page_size[0] * zoom))
                except Exception as e:
                    return self._message(f"Can't render page {page + 1}:\n{e}")
                STATE.tiles = reader.tiles_for(img, page_size, zoom)
                STATE.tile_page = page
            scaled, tiles = STATE.tiles
            if STATE.tile < 0 or STATE.tile >= len(tiles):
                STATE.tile = len(tiles) - 1 if STATE.tile < 0 else 0
            tile = STATE.tile
        x, y = tiles[tile]
        crop = scaled.crop((x, y, x + page_size[0], y + page_size[1]))
        if s.get("auto_contrast", True):
            crop = ImageOps.autocontrast(crop, cutoff=1)
        if self.config["display"]["dark_mode"]:
            crop = ImageOps.invert(crop)
        out = crop.convert("1", dither=Image.FLOYDSTEINBERG if s.get("dither", True) else Image.NONE)
        if s.get("page_numbers", True):
            # tiny page/tile marker, top right, on a white box
            from PIL import ImageDraw
            out = out.convert("L")
            label = f"{page + 1}/{pages.count}" + (f"  {tile + 1}/{len(tiles)}" if len(tiles) > 1 else "")
            small = font(8, 8)
            d = ImageDraw.Draw(out)
            tw = d.textbbox((0, 0), label, font=small)[2]
            d.rectangle([page_size[0] - tw - 6, 0, page_size[0], 10], fill=255)
            d.text((page_size[0] - tw - 3, 1), label, font=small, fill=0)
            out = out.convert("1")
        return self._rotate(out)
