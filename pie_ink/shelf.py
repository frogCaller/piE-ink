"""The shelves: what there is to show, read or play — on this Pi and on a Pietato —
found by a few words, so one bot answers for the whole house.

Here: the camera's photos (data/photos), the books (data/books), the music
(data/music), the drawing and the painting — and whatever's on a USB drive plugged in
(pie_ink/drives.py: its pictures, songs, videos and documents, found the same way;
"show me the beach photo on the usb drive", "what's on the usb drive"). On a Pietato that's about (pie_ink/pitato.py):
its Pictures, Music, Videos and Documents, asked for the way its own page asks —
GET /api/recent for the newest pictures, /api/list folder by folder, /api/file for the
file itself, POST /api/pi/show to put one on its screen. A page with a password keeps
its shelves to itself.

    show me the latest photo          find the song called blue monday
    do I have a pdf about taxes       what videos are there
    play dayvan cowboy                 put the holiday video on the pietato
    send me the picture of the cat    (Telegram: the picture comes back)

Whichever Pi has it: a picture goes on the panel here (and comes back in Telegram), a
book or PDF opens in Read (copied over from the Pietato if that's where it was), a song
plays here if it's here and on the Pietato if it's there, and a video plays on the
Pietato's screen. parse() says what was asked; the app carries it out with the rest.
"""
import io
import logging
import os
import re
import threading
import time
import urllib.parse

import requests

from . import drives, music, photos, pitato, reader
from .settings import DATA_DIR

log = logging.getLogger(__name__)

PICTURE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff")
MUSIC_EXTS = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".wma", ".mp2")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpg", ".mpeg")
DOC_EXTS = (".pdf", ".epub", ".txt", ".md", ".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".csv",
            ".pptx", ".ppt", ".odp", ".cbz")
GAME_EXTS = (".gb", ".gbc", ".gba", ".nes", ".sfc", ".smc", ".md", ".gen", ".smd", ".sms", ".gg", ".z64", ".n64",
             ".v64", ".cue", ".bin", ".chd", ".pbp", ".m3u", ".iso", ".cso", ".nds")    # in a Pietato's Games, as it plays them
READABLE = (".epub", ".txt", ".pdf", ".cbz")          # what Read opens
KIND_WORDS = {
    "photo": ("photo", "photos", "picture", "pictures", "pic", "pics", "selfie", "selfies", "snapshot", "snapshots",
              "image", "images", "shot", "shots"),
    "video": ("video", "videos", "film", "films", "movie", "movies", "clip", "clips", "recording", "recordings"),
    "music": ("song", "songs", "track", "tracks", "music", "tune", "tunes", "album", "albums", "mp3", "mp3s"),
    "document": ("document", "documents", "doc", "docs", "pdf", "pdfs", "paper", "papers", "text", "texts",
                 "spreadsheet", "spreadsheets", "file", "files"),
    "book": ("book", "books", "ebook", "ebooks", "epub", "epubs", "comic", "comics"),
    "drawing": ("drawing", "drawings", "sketch", "sketches", "painting", "paintings", "doodle", "doodles", "postcard"),
    "game": ("game", "games", "rom", "roms"),
}
DRIVE_WORDS = "usb drive stick thumb flash memory pen key thumbdrive flashdrive pendrive"   # a drive's things answer to these
_ON_DRIVE = re.compile(r"\b(usb|thumb ?drive|flash ?drive|pen ?drive|memory stick|(?:the|my|this|that) (?:drive|stick))\b")
KIND_OF_WORD = {w: k for k, ws in KIND_WORDS.items() for w in ws}
NEWEST = ("latest", "newest", "last", "recent", "most recent", "new", "newest")
OLDEST = ("oldest", "first", "earliest")
STOP = {"the", "a", "an", "my", "our", "of", "called", "named", "titled", "about", "with", "that", "from", "on", "in",
        "for", "to", "some", "any", "this", "please", "me", "one", "ones", "it", "and", "or", "by", "is", "are",
        "there", "here", "pitato", "pi", "tv", "i", "we", "do", "have", "got"} | set(NEWEST) | set(OLDEST) | set(KIND_OF_WORD)
PITATO_FOLDERS = {"pictures": "photo", "music": "music", "videos": "video", "documents": "document", "games": "game"}
CACHE_FOR = 60                 # seconds a Pietato's shelves are good for
LIST_CALLS = 30                # at most this many folder listings per look at a Pietato
LIST_DEPTH = 3                 # folders within folders, this deep
MOST_FILES = 800               # per folder kind, at most
PICTURE_MAX = 25_000_000       # bytes: a picture fetched to show here
DOC_MAX = 60_000_000           # ...a book or PDF copied into Read
TIMEOUT = 4

_cache = {}                    # a Pietato's ip -> {"at", "items", "counts"}
_lock = threading.Lock()


# -- what's here -------------------------------------------------------------------------------

def _kind_of(name, folder=None):
    ext = os.path.splitext(name)[1].lower()
    if folder == "games":                                # (a .md there is a Mega Drive game, not a note)
        return "game" if ext in GAME_EXTS else ""
    if ext in PICTURE_EXTS:
        return "photo"
    if ext in MUSIC_EXTS:
        return "music"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in DOC_EXTS:
        return "document"
    return ""


def _title(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r"[_\-]+", " ", stem).strip() or name


def game_title(name):
    """"Super Mario Bros. 3 (USA) (Rev 1) [!].nes" -> "Super Mario Bros. 3"."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem)
    return re.sub(r"[_]+", " ", stem).strip(" -") or _title(name)


def here(config=None):
    """Everything on this Pi's shelves: [{name, title, kind, where: "here", folder, path, when, size, words}]."""
    out = []
    items, _ = photos.listing(600)
    for p in items:
        out.append({"name": p["name"], "title": f"photo {time.strftime('%A %-d %B', time.localtime(p['ts']))}"
                    + (f" ({p['label']})" if p.get("filter") not in (None, "none") else ""),
                    "kind": "photo", "where": "here", "folder": "photos", "path": p["name"], "when": p["ts"],
                    "size": p.get("bytes", 0), "words": f"photo picture {p.get('label', '')}"})
    try:
        for b in reader.library():
            path = os.path.join(reader.BOOKS_DIR, b["file"])
            out.append({"name": b["file"], "title": b["title"], "kind": "book", "where": "here", "folder": "books",
                        "path": b["file"], "when": b.get("added") or 0, "size": 0, "words": "book " + b.get("ext", ""),
                        "book": b})
    except Exception:
        log.debug("no books for the shelf", exc_info=True)
    try:
        conf = (config or {}).get("music", {})
        root = music.folder(conf)
        for t in music.library(conf):
            full = os.path.join(root, t["path"])
            try:
                when = os.path.getmtime(full)
            except OSError:
                when = 0
            out.append({"name": os.path.basename(t["path"]), "title": t["title"], "kind": "music", "where": "here",
                        "folder": "music", "path": t["path"], "when": when, "size": t.get("size", 0),
                        "words": "song music " + t.get("folder", "")})
    except Exception:
        log.debug("no music for the shelf", exc_info=True)
    for fname, title in (("drawing.png", "the drawing"), ("paint.png", "the painting"), ("postcard.png", "the postcard")):
        full = os.path.join(DATA_DIR, fname)
        if os.path.isfile(full):
            out.append({"name": fname, "title": title, "kind": "drawing", "where": "here", "folder": "drawing", "path": fname,
                        "when": os.path.getmtime(full), "size": os.path.getsize(full), "words": "drawing picture sketch painting"})
    try:
        out += on_drives()
    except Exception:
        log.debug("no USB drives for the shelf", exc_info=True)
    return out


# -- what's on a USB drive -----------------------------------------------------------------------

def on_drives(fresh=False, only=None):
    """What's on the USB drives mounted now (or just `only`, from drives.mounted()): shelf items like the
    rest — where "here", folder "drive", path within the drive — with the drive's name in their words."""
    out = []
    for d in (only if only is not None else drives.mounted()):
        name = drives.title(d)
        for rel, size, when in drives.files(d, PICTURE_EXTS + MUSIC_EXTS + VIDEO_EXTS + DOC_EXTS, fresh):
            kind = _kind_of(rel)
            if not kind:
                continue
            sub = os.path.dirname(rel)
            out.append({"name": os.path.basename(rel), "title": music.pretty(rel) if kind == "music" else _title(rel),
                        "kind": kind, "where": "here", "folder": "drive", "drive": name, "root": d["path"], "dev": d["dev"],
                        "path": rel, "when": when, "size": size,
                        "words": f"{kind} {DRIVE_WORDS} {name} {sub.replace(os.sep, ' ')}"})
    return out


def drive_asked(words):
    """Do these words ask about a USB drive? ("on the usb drive", "on my thumb drive", "on the stick")"""
    return bool(_ON_DRIVE.search(" " + (words or "").lower()))


def _count_words(items):
    """"214 photos, 12 songs and 3 documents"."""
    names = {"photo": ("photo", "photos"), "music": ("song", "songs"), "video": ("video", "videos"),
             "document": ("document", "documents"), "book": ("book", "books"), "drawing": ("drawing", "drawings")}
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    bits = [f"{counts[k]} {names[k][0] if counts[k] == 1 else names[k][1]}" for k in names if counts.get(k)]
    return ", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else "".join(bits)


def drive_summary(kinds=None, only=None, ask=True):
    """A line about what's on the USB drives (or just `only`): "On the USB drive SANDISK: 214 photos and
    12 songs — ask me for any of them." — or why there's nothing to say."""
    mounted = only if only is not None else drives.mounted()
    if not mounted:
        try:
            from . import plugs
            plugged = [d for d in plugs.devices() if plugs.kind_of(d) in ("drive", "reader") and drives.disks_on(d["bus"])]
        except Exception:
            plugged = []
        if plugged:
            return ("There's a USB drive in, but it isn't mounted, so I can't look inside — run setup.sh again "
                    "and drives get mounted as they go in.")
        return "There's no USB drive plugged in."
    bits = []
    for d in mounted:
        items = [i for i in on_drives(only=[d]) if not kinds or i["kind"] in kinds]
        name = drives.title(d)
        what = _count_words(items) or ("nothing like that" if kinds else "nothing I can show or play")
        bits.append(f"on the USB drive {name}: {what}".replace("drive : ", "drive: "))
    line = "; ".join(bits)
    line = line[0].upper() + line[1:]
    return line + (" — ask me for any of it." if ask and "nothing" not in line else ".")


# -- what's on a Pietato ---------------------------------------------------------------------------

def _get(base, path, params=None, timeout=TIMEOUT):
    r = requests.get(base + path, params=params, timeout=timeout, headers={"Accept": "application/json"})
    if r.status_code == 401:
        raise PermissionError("its page has a password")
    r.raise_for_status()
    return r.json()


def _walk(base, folder, kind, budget):
    """The files of one of its folders, folders within folders too (a few levels, a few calls)."""
    out, queue = [], [("", 0)]
    while queue and budget[0] > 0 and len(out) < MOST_FILES:
        sub, depth = queue.pop(0)
        budget[0] -= 1
        try:
            d = _get(base, "/api/list", {"folder": folder, "path": sub})
        except PermissionError:
            raise
        except Exception as e:
            log.debug("couldn't list %s/%s on the Pietato: %s", folder, sub, e)
            continue
        entries = [e for e in (d.get("entries") or []) if isinstance(e, dict) and e.get("name")]
        names = {str(e["name"]).lower() for e in entries}
        for e in entries:
            name = str(e["name"])
            path = f"{sub}/{name}" if sub else name
            if e.get("dir"):
                if depth + 1 < LIST_DEPTH:
                    queue.append((path, depth + 1))
                continue
            if _kind_of(name, folder) != kind:
                continue
            stem, ext = os.path.splitext(name)
            if kind == "game" and ext.lower() == ".bin" and any(f"{stem}{x}".lower() in names for x in (".cue", ".m3u")):
                continue                                  # a disc's track: its .cue is the game
            out.append({"name": name, "title": game_title(name) if kind == "game" else _title(name), "kind": kind, "folder": folder, "path": path,
                        "when": float(e.get("mtime") or 0), "size": int(e.get("size") or 0),
                        "words": f"{kind} {sub.replace('/', ' ')}"})
    return out


def _look_at(p):
    """A Pietato's shelves, fresh: its newest pictures (one question), and its music, videos
    and documents folder by folder; and how many of each it says it has."""
    base = f"http://{p['ip']}:{pitato.PORT}"
    items, counts, locked = [], {}, False
    try:
        d = _get(base, "/api/recent", {"n": 300})
        for e in d.get("pictures") or []:
            if isinstance(e, dict) and e.get("path"):
                items.append({"name": str(e.get("name") or os.path.basename(e["path"])), "title": _title(e["path"]),
                              "kind": "photo", "folder": "pictures", "path": str(e["path"]), "when": float(e.get("mtime") or 0),
                              "size": int(e.get("size") or 0), "words": "photo picture " + os.path.dirname(e["path"]).replace("/", " ")})
        budget = [LIST_CALLS]
        for folder, kind in (("music", "music"), ("videos", "video"), ("documents", "document"), ("games", "game")):
            items += _walk(base, folder, kind, budget)
        info = _get(base, "/api/info")
        for f in info.get("folders") or []:
            if isinstance(f, dict) and f.get("key") in PITATO_FOLDERS and isinstance(f.get("files"), int):
                counts[f["key"]] = f["files"]
    except PermissionError:
        locked = True
    except Exception as e:
        log.debug("the Pietato's shelves: %s", e)
    for it in items:
        it.update(where="pitato", host=p.get("name") or p["ip"], ip=p["ip"])
    return {"at": time.time(), "items": items, "counts": counts, "locked": locked}


def pitatos():
    """The Pietatos whose shelves can be seen: those about, with a page."""
    return [p for p in pitato.PITATO.around() if p.get("web")]


def on_pitatos(fresh=False):
    """Everything on the Pietatos' shelves, from a look no older than a minute."""
    out = []
    for p in pitatos():
        with _lock:
            got = _cache.get(p["ip"])
        if fresh or not got or time.time() - got["at"] > CACHE_FOR:
            got = _look_at(p)
            with _lock:
                _cache[p["ip"]] = got
        out += got["items"]
    return out


def warm(older_than=300):
    """A look at each Pietato's shelves when the last was a while ago (the bot's context is
    told what they hold without a question having been asked): called now and then."""
    for p in pitatos():
        with _lock:
            got = _cache.get(p["ip"])
        if got and time.time() - got["at"] < older_than:
            continue
        got = _look_at(p)
        with _lock:
            _cache[p["ip"]] = got


def everything(config=None):
    return here(config) + on_pitatos()


# -- finding ---------------------------------------------------------------------------------------

def _tokens(text):
    return [w for w in re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split() if w not in STOP]


def _when_filter(words):
    """"today", "yesterday": only things from then."""
    low = " ".join(words)
    now = time.localtime()
    midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    if "today" in low:
        return lambda t: t >= midnight
    if "yesterday" in low:
        return lambda t: midnight - 86400 <= t < midnight
    return None


def _kinds_in(words):
    return {KIND_OF_WORD[w] for w in words if w in KIND_OF_WORD}


def find(text, kinds=None, limit=6, config=None):
    """The things that fit a few words: the kind words say what ("photo", "song"), "latest"
    the newest, "today"/"yesterday" when, the rest match the name. Best first."""
    raw = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split()
    kinds = set(kinds or ()) | _kinds_in(raw)
    newest = any(w in NEWEST for w in raw)
    oldest = any(w in OLDEST for w in raw)
    when_ok = _when_filter(raw)
    tokens = [t for t in _tokens(text) if t not in ("today", "yesterday")]
    found = []
    for it in everything(config):
        if kinds and it["kind"] not in kinds and not (it["kind"] == "drawing" and "photo" in kinds):
            continue
        if when_ok and not when_ok(it.get("when") or 0):
            continue
        hay = f"{it['title']} {it['name']} {it.get('words', '')} {it.get('host', '')}".lower()
        hay_words = re.sub(r"[^a-z0-9' ]+", " ", hay).split()
        score = 0
        for t in tokens:
            if t in hay_words:
                score += 2
            elif any(w.startswith(t) for w in hay_words) or t in hay:
                score += 1
            else:
                score = -1
                break
        if score < 0 or (not tokens and not kinds and not newest and not oldest):
            continue
        found.append((score, it))
    if oldest:
        found.sort(key=lambda s: (-s[0], s[1].get("when") or 0))
    else:
        found.sort(key=lambda s: (-s[0], -(s[1].get("when") or 0)))
    return [it for _, it in found[:limit]]


def describe(it):
    """cat.jpg — a photo here, 2 h ago · blue monday — a song on the Pietato (Music/New Order)."""
    kind = {"photo": "a photo", "picture": "a picture", "music": "a song", "video": "a video", "document": "a document",
            "book": "a book", "drawing": "a drawing", "game": "a game"}.get(it["kind"], it["kind"])
    if it["where"] == "pitato":
        where = f"on the Pietato ({it['folder'].capitalize()}/{it['path']})" if "/" in it["path"] else f"on the Pietato's {it['folder'].capitalize()}"
    elif it.get("folder") == "drive":
        sub = os.path.dirname(it["path"])
        where = f"on the USB drive {it.get('drive') or ''}".rstrip() + (f" ({sub})" if sub else "")
    else:
        where = "here"
    ago = time.time() - (it.get("when") or 0)
    when = "" if not it.get("when") else (" · just now" if ago < 90 else f" · {int(ago // 60)} min ago" if ago < 5400
                                          else f" · {int(ago // 3600)} h ago" if ago < 172800 else f" · {int(ago // 86400)} d ago")
    return f"{it['title']} — {kind} {where}{when}"


# -- fetching, showing, playing ------------------------------------------------------------------

def fetch(it, most=PICTURE_MAX):
    """The file's bytes: from the disk here, or from the Pietato's page (up to `most` bytes)."""
    if it["where"] == "here":
        if it["folder"] == "drive":
            path = drives.path_in(it["root"], it["path"])
        elif it["folder"] == "photos":
            path = photos.path_of(it["path"])
        elif it["folder"] == "books":
            path = os.path.join(reader.BOOKS_DIR, it["path"])
        elif it["folder"] == "music":
            path = os.path.join(music.folder(), it["path"])
        else:
            path = os.path.join(DATA_DIR, it["path"])
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("it isn't there any more")
        with open(path, "rb") as f:
            return f.read(most + 1)[:most]
    base = f"http://{it['ip']}:{pitato.PORT}"
    r = requests.get(f"{base}/api/file", params={"folder": it["folder"], "path": it["path"]}, timeout=20, stream=True)
    if r.status_code == 401:
        raise PermissionError("its page has a password")
    r.raise_for_status()
    data = io.BytesIO()
    for chunk in r.iter_content(65536):
        data.write(chunk)
        if data.tell() > most:
            raise ValueError("that file is too big to bring over")
    return data.getvalue()


def show_on_pitato(it):
    """A thing of the Pietato's own, put on its screen (played, if it plays): (done, why not)."""
    if it["where"] != "pitato":
        return False, "it isn't on the Pietato"
    base = f"http://{it['ip']}:{pitato.PORT}"
    try:
        r = requests.post(f"{base}/api/pi/show", json={"folder": it["folder"], "path": it["path"]}, timeout=15)
    except Exception as e:
        return False, str(e)[:80]
    if r.status_code == 401:
        return False, "its page has a password"
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200 or d.get("error"):
        return False, d.get("error") or f"it said {r.status_code}"
    return True, ""


def send_to_pitato(it, data):
    """A picture from here into the Pietato's Pictures (a pie-ink folder there), then on its screen."""
    base = f"http://{it['ip']}:{pitato.PORT}"
    name = it["name"] if it["name"].lower().endswith(PICTURE_EXTS) else it["name"] + ".png"
    path = f"pie-ink/{name}"
    try:
        r = requests.put(f"{base}/api/upload", params={"folder": "pictures", "path": path}, data=data, timeout=30,
                         headers={"Content-Type": "application/octet-stream"})
    except Exception as e:
        return False, str(e)[:80]
    if r.status_code == 401:
        return False, "its page has a password"
    if r.status_code != 200:
        return False, f"it wouldn't take it ({r.status_code})"
    try:
        path = r.json().get("path") or path          # a name it had already: it's kept under a new one ("name (2).png")
    except ValueError:
        pass
    return show_on_pitato({**it, "where": "pitato", "folder": "pictures", "path": path})


# -- the words ---------------------------------------------------------------------------------------

def counts_here(config=None):
    n_photos = photos.listing(1)[1]
    try:
        n_books = len(reader.library())
    except Exception:
        n_books = 0
    try:
        n_songs = len(music.library((config or {}).get("music", {})))
    except Exception:
        n_songs = 0
    return {"photos": n_photos, "books": n_books, "songs": n_songs}


def words(config=None, owner="they"):
    """A line for the bot: what the shelves hold, here and on the Pietato, and that it can fetch any of it."""
    c = counts_here(config)
    bits, mine = [], []
    if c["photos"]:
        newest = ""
        latest = photos.latest()
        if latest:
            ago = time.time() - latest["ts"]
            newest = f" (newest {int(ago // 60)} min ago)" if ago < 5400 else f" (newest {int(ago // 3600)} h ago)" if ago < 172800 else ""
        mine.append(f"{c['photos']} photo{'s' if c['photos'] != 1 else ''}{newest}")
    if c["books"]:
        mine.append(f"{c['books']} book{'s' if c['books'] != 1 else ''}")
    if c["songs"]:
        mine.append(f"{c['songs']} song{'s' if c['songs'] != 1 else ''}")
    if mine:
        bits.append("here " + ", ".join(mine))
    try:
        for d in drives.mounted():
            have = _count_words(on_drives(only=[d]))
            if have:
                bits.append(f"on the USB drive {drives.title(d)} plugged in here ".replace("  ", " ") + have)
    except Exception:
        log.debug("no USB drives for the words", exc_info=True)
    for p in pitatos():
        with _lock:
            got = _cache.get(p["ip"])
        if got is None:
            continue
        if got.get("locked"):
            bits.append(f"the Pietato {p.get('name') or ''} keeps its shelves to itself (its page has a password)".replace("  ", " "))
            continue
        names = {"pictures": "picture", "music": "song", "videos": "video", "documents": "document", "games": "game"}
        theirs = [f"{n} {names[k]}{'s' if n != 1 else ''}" for k, n in got["counts"].items() if n and k in names]
        if not theirs:
            theirs = [f"{sum(1 for i in got['items'] if i['kind'] == k)} {names[f]}s" for f, k in PITATO_FOLDERS.items()
                      if any(i["kind"] == k for i in got["items"])]
        bits.append(f"on the Pietato{(' ' + p['name']) if len(pitatos()) > 1 and p.get('name') else ''} " + (", ".join(theirs) or "nothing yet"))
    if not bits:
        return ""
    return ("On the shelves — " + "; ".join(bits) + f". {owner.capitalize() if owner == 'they' else owner} can ask you for any of it by a few "
            "words of its name, or the latest photo, and you find it on whichever Pi has it: a picture goes on the panel, a book "
            "opens in Read, a song plays, a video goes on the Pietato's screen.")


# -- what was asked ---------------------------------------------------------------------------------

_LEAD = r"^(?:(?:please|can you|could you|would you|will you|hey|now|ok|okay|go on|and|then|just|quick)[ ,]+)*"
_PITATO = r"(?:the )?(?:pitato|tv|television|telly|big screen|handheld|console)"
_KINDS = "|".join(sorted({w for ws in KIND_WORDS.values() for w in ws}, key=len, reverse=True))
_NEW = "|".join(NEWEST + OLDEST)
_DRIVE = r"(?:the |my |this |that )?(?:usb(?: drive| stick| key)?|thumb ?drive|flash ?drive|pen ?drive|memory stick|stick|drive)"


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower().strip("?!. ")).strip()


def _has_kind(words):
    return bool(_kinds_in(re.sub(r"[^a-z0-9' ]+", " ", words).split())) or re.search(rf"\b({_NEW})\b", words)


def parse(text):
    """What was asked of the shelves, or None: {"kind": "find"|"show"|"play"|"pitato"|"list", "words"}."""
    low = _clean(text)
    low = re.sub(_LEAD, "", low)
    if not low:
        return None
    m = re.match(rf"^(?:what(?:'s| is| do i have| have i got| have we got| do we have| else is)?|tell me what's|show me what's|list what's|"
                 rf"what (?:{_KINDS}) (?:are|is|do i have|have i got)(?: there)?) on {_DRIVE}$", low)
    if m:
        return {"kind": "list", "words": low, "drive": True}
    m = re.match(rf"^what (?:{_KINDS}) (?:do i have|are there|have i got|is there|have we got|do we have|are on (?:the )?(?:shelf|shelves|pi|pitato))\b", low)
    if m:
        return {"kind": "list", "words": low, "drive": drive_asked(low)}
    m = re.match(rf"^(?:play|put on|put|show|open|start|stream)\s+(?:the |a |an |my |some )?(.+?)\s+on {_PITATO}$", low)
    if m:
        return {"kind": "pitato", "words": m.group(1)}
    m = re.match(r"^play(?: me)?\s+(?:the |a |an |my |some )?(.+?)$", low)
    if m and not re.search(r"\b(snake|game|games|on (?:the )?(?:workshop|upstairs)|pi\b)", m.group(1)):
        return {"kind": "play", "words": m.group(1)}
    m = re.match(r"^(?:find|look for|look up|search for|search|locate|do i have|do we have|is there|have i got|have we got|where is|where's|where are|got)\s+(?:the |a |an |my |any |some )?(.+?)$", low)
    if m and _has_kind(m.group(1)) and not re.search(r"\b(pi|pis|pie-ink|pieink|pie|camera|cam)\b", m.group(1)):
        return {"kind": "find", "words": m.group(1)}
    m = re.match(r"^(?:show|open|bring up|put up|display|pull up|get|fetch|send|read)(?: me)?\s+(?:the |a |an |my )?(.+?)$", low)
    if m and _has_kind(m.group(1)) and not re.search(r"\b(camera|cam|webcam|pi|pis|screen|clock|weather)\b", m.group(1)):
        return {"kind": "show", "words": m.group(1)}
    return None


def games(fresh=False):
    """The games on the Pietatos' shelves (their Games folders): [{title, path, host, ip, ...}]."""
    return [it for it in on_pitatos(fresh) if it["kind"] == "game"]
