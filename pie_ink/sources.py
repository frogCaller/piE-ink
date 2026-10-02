"""Free places to get books and comics from, all public domain or freely
published: Project Gutenberg (via the Gutendex API), xkcd, and the Internet
Archive's scanned Golden Age comics."""
import logging
import random

import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "pie-ink/2.6 (+https://github.com/frogCaller/PiE-ink)"}
GUTENDEX = "https://gutendex.com/books"
XKCD = "https://xkcd.com"
ARCHIVE = "https://archive.org"


def _get(url, params=None, timeout=15):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


# -- Project Gutenberg ---------------------------------------------------------------

def gutenberg_search(query, limit=20):
    data = _get(GUTENDEX, {"search": query, "mime_type": "application/epub"})
    out = []
    for b in data.get("results", [])[:limit]:
        url, ext = gutenberg_pick(b.get("formats", {}))
        if not url:
            continue
        out.append({
            "source": "gutenberg", "id": str(b["id"]),
            "title": b.get("title", "Untitled"),
            "by": ", ".join(a.get("name", "") for a in b.get("authors", [])),
            "url": url, "ext": ext, "kind": "text",
            "downloads": b.get("download_count", 0),
        })
    return out


def gutenberg_pick(formats):
    """Prefer EPUB (with images stripped it's small), then plain text."""
    for key in ("application/epub+zip",):
        for k, v in formats.items():
            if k.startswith(key) and not v.endswith(".zip"):
                return v, ".epub"
    for k, v in formats.items():
        if k.startswith("text/plain") and not v.endswith(".zip"):
            return v, ".txt"
    return None, None


# -- xkcd ----------------------------------------------------------------------------------

def xkcd(which="random"):
    latest = _get(f"{XKCD}/info.0.json")
    if which == "latest":
        data = latest
    else:
        n = random.randint(1, latest["num"]) if which == "random" else int(which)
        if n == 404:
            n = 405                     # there is no xkcd 404, famously
        data = _get(f"{XKCD}/{n}/info.0.json")
    img = data.get("img", "")
    ext = ".png" if img.endswith(".png") else ".jpg"
    return {
        "source": "xkcd", "id": str(data["num"]),
        "title": f"xkcd #{data['num']}: {data.get('safe_title') or data.get('title')}",
        "by": data.get("alt", ""), "url": img, "ext": ext, "kind": "pages",
    }


# -- Internet Archive comics ----------------------------------------------------------

def archive_search(query, limit=20):
    q = f'({query}) AND mediatype:texts AND (collection:comics OR subject:comics OR subject:"comic books")'
    data = _get(f"{ARCHIVE}/advancedsearch.php", {
        "q": q, "fl[]": ["identifier", "title", "creator", "year", "downloads"],
        "rows": limit, "sort[]": "downloads desc", "output": "json",
    }, timeout=25)
    out = []
    for d in data.get("response", {}).get("docs", []):
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = ", ".join(creator[:2])
        out.append({
            "source": "archive", "id": d["identifier"],
            "title": d.get("title", d["identifier"]),
            "by": " · ".join(str(x) for x in (creator, d.get("year")) if x),
            "kind": "pages", "downloads": d.get("downloads", 0),
        })
    return out


def archive_file(identifier):
    """Pick the best page file in an item: CBZ, then PDF. Returns (url, ext, size)."""
    meta = _get(f"{ARCHIVE}/metadata/{identifier}", timeout=25)
    files = meta.get("files", [])
    best = None
    for f in files:
        name = f.get("name", "")
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if ext not in ("cbz", "pdf"):
            continue
        if "_text" in name.lower() or "_jp2" in name.lower():
            continue
        rank = 0 if ext == "cbz" else 1
        size = int(f.get("size") or 0)
        if best is None or (rank, -size) < (best[0], -best[2]):
            best = (rank, name, size)
    if not best:
        raise ValueError("no CBZ or PDF in this item")
    _, name, size = best
    return f"{ARCHIVE}/download/{identifier}/{requests.utils.quote(name)}", "." + name.rsplit(".", 1)[-1].lower(), size
