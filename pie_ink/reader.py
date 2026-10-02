"""Books and comics for a tiny screen.

- Library: files uploaded from the page live in data/books/, with title,
  type and reading position in data/books/library.json.
- Text (EPUB, TXT, PDF via pdftotext) is split into paragraphs and paginated
  for the screen at the chosen font and size. Pagination runs in a thread
  and is cached, so a long novel only gets laid out once per font/size.
- Pages (CBZ, PDF via pdftoppm, single images) are scanned in screen-sized
  tiles at a chosen zoom, left-to-right then top-to-bottom.
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from html.parser import HTMLParser

from PIL import Image, ImageOps

from .settings import DATA_DIR
from .text import font, line_height

log = logging.getLogger(__name__)

BOOKS_DIR = os.path.join(DATA_DIR, "books")
CACHE_DIR = os.path.join(BOOKS_DIR, "cache")
LIBRARY = os.path.join(BOOKS_DIR, "library.json")
TEXT_TYPES = {".epub", ".txt"}
PAGE_TYPES = {".cbz", ".zip", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_lock = threading.Lock()

for d in (BOOKS_DIR, CACHE_DIR):
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass


# -- library ---------------------------------------------------------------------

def _load_library():
    try:
        with open(LIBRARY) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save_library(items):
    try:
        with open(LIBRARY, "w") as f:
            json.dump(items, f, indent=1)
    except OSError as e:
        log.warning("could not save library: %s", e)


def library():
    with _lock:
        return _load_library()


def get(book_id):
    return next((b for b in library() if b["id"] == book_id), None)


def poppler_missing():
    return not (shutil.which("pdftotext") and shutil.which("pdftoppm") and shutil.which("pdfinfo"))


def add(filename, data, kind=None, title=None):
    """Store a file. kind: 'text' or 'pages' (auto from extension)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        if poppler_missing():
            raise ValueError("PDFs need poppler-utils on the Pi: sudo apt install poppler-utils, then restart")
        kind = kind or "text"
    elif ext in TEXT_TYPES:
        kind = "text"
    elif ext in PAGE_TYPES:
        kind = "pages"
    else:
        raise ValueError("Unsupported file: use EPUB, TXT, PDF, CBZ or an image")
    book_id = hashlib.sha1(data).hexdigest()[:12]
    path = os.path.join(BOOKS_DIR, f"{book_id}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    title = title or _title_for(path, ext) or os.path.splitext(os.path.basename(filename))[0]
    with _lock:
        items = [b for b in _load_library() if b["id"] != book_id]
        items.insert(0, {"id": book_id, "title": title, "file": os.path.basename(path), "kind": kind,
                         "ext": ext, "page": 0, "tile": 0, "added": time.time()})
        _save_library(items)
    return get(book_id)


# -- downloads from the free sources -----------------------------------------------

JOBS = {}
_job_seq = 0


def fetch_async(url, filename, kind, title):
    """Download in the background; progress in JOBS[id]. Returns the job id."""
    global _job_seq
    import requests
    _job_seq += 1
    job_id = str(_job_seq)
    job = JOBS[job_id] = {"id": job_id, "title": title, "progress": 0.0, "done": False, "error": None, "book": None}

    def run():
        try:
            with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "pie-ink/2.6"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                buf, got = io.BytesIO(), 0
                for chunk in r.iter_content(256 * 1024):
                    buf.write(chunk); got += len(chunk)
                    if got > 300 * 1024 * 1024:
                        raise ValueError("file too large (300 MB max)")
                    job["progress"] = got / total if total else 0.0
            job["book"] = add(filename, buf.getvalue(), kind, title)
            job["progress"] = 1.0
        except Exception as e:
            log.warning("download failed: %s", e)
            job["error"] = str(e)
        finally:
            job["done"] = True

    threading.Thread(target=run, daemon=True, name="download").start()
    return job_id


def jobs():
    """Active and recently finished downloads (finished ones are dropped once reported)."""
    out = list(JOBS.values())
    for j in out:
        if j["done"]:
            JOBS.pop(j["id"], None)
    return out


def remove(book_id):
    with _lock:
        items = _load_library()
        for b in items:
            if b["id"] == book_id:
                try:
                    os.remove(os.path.join(BOOKS_DIR, b["file"]))
                except OSError:
                    pass
        items = [b for b in items if b["id"] != book_id]
        _save_library(items)
    for name in os.listdir(CACHE_DIR):
        if name.startswith(book_id):
            try:
                os.remove(os.path.join(CACHE_DIR, name))
            except OSError:
                pass
    return items


def set_position(book_id, page=None, tile=None):
    with _lock:
        items = _load_library()
        for b in items:
            if b["id"] == book_id:
                if page is not None:
                    b["page"] = int(page)
                if tile is not None:
                    b["tile"] = int(tile)
        _save_library(items)


def path_of(book):
    return os.path.join(BOOKS_DIR, book["file"])


# -- text extraction -------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr", "blockquote", "section", "article", "hr"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.buf, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.parts.append(text)
        self.buf = []

    def paragraphs(self):
        self._flush()
        return self.parts


def _epub_paragraphs(path):
    import xml.etree.ElementTree as ET
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "o": "http://www.idpf.org/2007/opf"}
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = root.find(".//c:rootfile", ns).get("full-path")
        opf = ET.fromstring(z.read(opf_path))
        base = os.path.dirname(opf_path)
        items = {i.get("id"): i.get("href") for i in opf.find("o:manifest", ns)}
        paras = []
        for ref in opf.find("o:spine", ns):
            href = items.get(ref.get("idref"))
            if not href:
                continue
            name = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
            try:
                html = z.read(name).decode("utf-8", "replace")
            except KeyError:
                continue
            p = _TextExtractor()
            p.feed(html)
            paras += p.paragraphs()
    return paras


def _epub_title(path):
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
            opf = ET.fromstring(z.read(opf_path))
            t = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
            return (t.text or "").strip() if t is not None else ""
    except Exception:
        return ""


def _txt_paragraphs(text):
    paras, buf = [], []
    for line in text.splitlines():
        if line.strip():
            buf.append(line.strip())
        elif buf:
            paras.append(" ".join(buf)); buf = []
    if buf:
        paras.append(" ".join(buf))
    return paras


def _pdf_paragraphs(path):
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not installed (sudo apt install poppler-utils)")
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, text=True, timeout=120)
    text = out.stdout.replace("\f", "\n\n")
    return _txt_paragraphs(text)


def _title_for(path, ext):
    if ext == ".epub":
        return _epub_title(path)
    return ""


def paragraphs(book):
    ext = book["ext"]
    path = path_of(book)
    if ext == ".epub":
        return _epub_paragraphs(path)
    if ext == ".txt":
        with open(path, encoding="utf-8", errors="replace") as f:
            return _txt_paragraphs(f.read())
    if ext == ".pdf":
        return _pdf_paragraphs(path)
    return []


# -- pagination ---------------------------------------------------------------------

class Layout:
    """Pages of lines for one book at one font/size/screen. Built in a
    background thread; `pages` is None until ready."""

    def __init__(self, book, font_index, size, box, pad):
        self.book = book
        safe_font = "".join(ch if ch.isalnum() else "_" for ch in str(font_index))
        self.key = f"{book['id']}-{safe_font}-{size}-{box[0]}x{box[1]}-{pad}"
        self.font_index, self.size, self.box, self.pad = font_index, size, box, pad
        self.pages = None
        self.error = None
        self.progress = 0.0
        self._cache = os.path.join(CACHE_DIR, self.key + ".json")
        if self._load_cache():
            return
        threading.Thread(target=self._build, daemon=True, name="paginate").start()

    def _load_cache(self):
        try:
            with open(self._cache) as f:
                self.pages = json.load(f)
            self.progress = 1.0
            return True
        except (OSError, json.JSONDecodeError):
            return False

    def _build(self):
        try:
            paras = paragraphs(self.book)
            if not paras:
                raise RuntimeError("No text found in this file")
            fnt = font(self.font_index, self.size)
            width = self.box[0] - 2 * self.pad
            lh = line_height(fnt)
            per_page = max(1, (self.box[1] - 2 * self.pad) // lh)
            widths = {}

            def w(text):
                v = widths.get(text)
                if v is None:
                    v = widths[text] = fnt.getlength(text)
                return v

            space = w(" ")
            pages, lines = [], []
            for i, para in enumerate(paras):
                cur, cur_w = [], 0.0
                for word in para.split():
                    ww = w(word)
                    if cur and cur_w + space + ww > width:
                        lines.append(" ".join(cur)); cur, cur_w = [word], ww
                    else:
                        cur_w = cur_w + (space if cur else 0) + ww; cur.append(word)
                if cur:
                    lines.append(" ".join(cur))
                lines.append("")          # paragraph gap
                while len(lines) >= per_page:
                    pages.append(lines[:per_page]); lines = lines[per_page:]
                if i % 50 == 0:
                    self.progress = (i + 1) / len(paras)
            while lines and not lines[-1]:
                lines.pop()
            if lines:
                pages.append(lines)
            # a page must not start with a blank line
            pages = [p if p[0] != "" else p[1:] + [""] for p in pages]
            self.pages = pages or [["(empty)"]]
            self.progress = 1.0
            try:
                with open(self._cache, "w") as f:
                    json.dump(self.pages, f)
            except OSError as e:
                log.warning("could not cache layout: %s", e)
        except Exception as e:
            log.exception("pagination failed")
            self.error = str(e)


# -- page sources (comics) ----------------------------------------------------------

class Pages:
    """Page images for a comic or PDF, one at a time."""

    def __init__(self, book):
        self.book = book
        self.path = path_of(book)
        self.ext = book["ext"]
        self.names = []
        self._zip = None
        if self.ext in (".cbz", ".zip"):
            self._zip = zipfile.ZipFile(self.path)
            self.names = sorted(n for n in self._zip.namelist() if n.lower().endswith(IMAGE_EXT) and not n.startswith("__MACOSX"))
            self.count = len(self.names)
        elif self.ext == ".pdf":
            self.count = self._pdf_pages()
        else:
            self.count = 1

    def _pdf_pages(self):
        if not shutil.which("pdfinfo"):
            raise RuntimeError("poppler-utils not installed (sudo apt install poppler-utils)")
        out = subprocess.run(["pdfinfo", self.path], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Pages:\s+(\d+)", out)
        return int(m.group(1)) if m else 0

    def image(self, index, target_width):
        """Greyscale PIL image of page `index`, at least target_width wide."""
        if self._zip is not None:
            with self._zip.open(self.names[index]) as f:
                return Image.open(io.BytesIO(f.read())).convert("L")
        if self.ext == ".pdf":
            with tempfile.TemporaryDirectory() as tmp:
                prefix = os.path.join(tmp, "p")
                subprocess.run(["pdftoppm", "-f", str(index + 1), "-l", str(index + 1), "-png", "-gray",
                                "-scale-to-x", str(target_width), "-scale-to-y", "-1", self.path, prefix],
                               check=True, timeout=120)
                files = sorted(os.listdir(tmp))
                if not files:
                    raise RuntimeError("pdftoppm produced nothing")
                return Image.open(os.path.join(tmp, files[0])).convert("L")
        return Image.open(self.path).convert("L")


def tiles_for(page_img, tile, zoom):
    """Scale a page so its width is tile_w*zoom and cut it into screen-sized
    tiles; returns (scaled image, [(x, y), ...]) in reading order."""
    tw, th = tile
    scale = (tw * zoom) / page_img.width
    img = page_img.resize((max(tw, int(page_img.width * scale)), max(1, int(page_img.height * scale))), Image.LANCZOS)
    if img.height < th:
        img = ImageOps.pad(img, (img.width, th), color=255, centering=(0.5, 0))
    xs = _steps(img.width, tw)
    ys = _steps(img.height, th)
    return img, [(x, y) for y in ys for x in xs]


def _steps(total, size):
    if total <= size:
        return [0]
    n = -(-(total - size) // int(size * 0.9)) + 1        # ~10% overlap between tiles
    step = (total - size) / (n - 1)
    return [int(round(i * step)) for i in range(n)]
