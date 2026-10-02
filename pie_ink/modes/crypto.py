"""Cryptogotchi: rotates through the watchlist with a face that reacts to
sentiment, a price graph, rotating stats and headlines.

Network fetches happen in a background thread; render() never blocks.
"""
import logging
import os
import random
import socket
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
from PIL import Image, ImageOps

from .. import holdings, mining, watchlist
from ..settings import ASSETS_DIR
from ..text import font, text_width, wrap
from .base import Mode

log = logging.getLogger(__name__)

FACES = {
    "look_r": "( ⚆_⚆)", "look_l": "(☉_☉ )",
    "look_r_happy": "( ◕‿◕)", "look_l_happy": "(◕‿◕ )",
    "awake": "(◕‿‿◕)", "cool": "(⌐■_■)", "happy": "(•‿‿•)",
    "hot": "('⚆_⚆)", "hot2": "(☉_☉')",
    "sad": "(╥☁╥ )", "worried": "(°▃▃°)", "bored": "(-__-)",
    "sleep": "(⇀‿‿↼)", "sleep2": "(≖‿‿≖)",
}
QUOTES = ["In crypto, we trust.", "Crypto never sleeps", "TO THE MOON!",
          "Stay calm and HODL", "Buy the dip!"]
# your miners, in the rotation: one item per pool that is on
MINING_ITEMS = {"duco": {"id": "duino-coin", "symbol": "DUCO", "name": "Duino-Coin", "mining": "duco"},
                "verus": {"id": "verus-mining", "symbol": "VRSC", "name": "Verus", "mining": "verus"}}
# what you hold, as one more page: the total and how it has moved
HOLDINGS_ITEM = {"id": "holdings", "symbol": "TOTAL", "name": "Your crypto", "holdings": True}
HOT_QUOTES = ["It's getting kinda hot!", "Is it hot in here?", "I'm sweating a bit", "I am HOT!"]
FEEDS = [
    "https://feeds.feedburner.com/CoinDesk",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
]
NEWS_TTL = 600
QUOTE_SECONDS = 10
BOTTOM_SECONDS = 30
FACE_SECONDS, GRAPH_SECONDS = 60, 30


def _num(value, decimals=2, prefix=""):
    if value is None:
        return "N/A"
    try:
        return f"{prefix}{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _pct(value):
    if value is None:
        return "N/A"
    return f"{'+' if value >= 0 else ''}{value:.2f}%"


def _short(value, decimals=2):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "N/A"
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= cut:
            return f"{n / cut:.{decimals}f}{suffix}"
    return f"{n:.{decimals}f}"


def _fit(d, text, fnt, max_w):
    if text_width(d, text, fnt) <= max_w:
        return text
    while len(text) > 1 and text_width(d, text + "…", fnt) > max_w:
        text = text[:-1]
    return text + "…"


def _cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        return None


class CryptoMode(Mode):
    name = "crypto"
    label = "Crypto"

    def __init__(self, config, panel=None):
        super().__init__(config, panel)
        self._quotes = {}
        self._news = []
        self._news_ts = 0
        self._online = True
        self._online_ts = 0
        self._look = 0
        self._skip = 0            # coins stepped with the HAT keys
        self._stop = threading.Event()
        self._icons = None
        self._font12 = font(1, 12)
        self._face = font(2, 32)
        self._bold = font(2, 11)
        self._news_font = font(5, 10)

    @property
    def interval(self):
        return float(self.settings.get("refresh_seconds", 3))

    # -- background data ------------------------------------------------------

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._fetch_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _fetch_loop(self):
        for item in watchlist.visible():          # disk cache shows immediately
            self._quote_for(item)
        while not self._stop.is_set():
            try:
                self._fetch_all()
            except Exception:
                log.exception("crypto fetch failed")
            self._stop.wait(30)

    def _current_item(self, items, now=None):
        per_item = max(10, int(self.settings.get("coin_seconds", 90)))
        return items[(int((now or time.time()) // per_item) + self._skip) % len(items)]

    @staticmethod
    def _items():
        """The coins in the rotation: the watchlist, your miners for each pool that
        is on, and what you hold altogether (when you've put amounts in)."""
        h = holdings.HOLDINGS
        return (watchlist.visible() + [MINING_ITEMS[m.kind] for m in mining.enabled()]
                + ([HOLDINGS_ITEM] if h.conf.get("show", True) and h.any() else []))

    def on_button(self, action):
        if action in ("next", "prev"):
            self._skip += 1 if action == "next" else -1
            return True
        return False

    def _fetch_all(self):
        """One pass over the watchlist, current coin first. quote() only hits
        the network when its cache is stale and not backing off."""
        self._check_online()
        days = int(self.settings.get("graph_days", 7))
        items = watchlist.visible()
        current = self._current_item(self._items())
        for item in [current] + [i for i in items if i is not current]:
            if self._stop.is_set():
                return
            if item.get("mining"):
                continue                              # its own thread keeps those numbers
            q = watchlist.quote(item, days)
            if q:
                self._quotes[item["id"]] = q
            self._stop.wait(0.5)
        if self.settings.get("show_news", True) and time.time() - self._news_ts > NEWS_TTL:
            self._fetch_news()

    def _fetch_news(self):
        try:
            r = requests.get(random.choice(FEEDS), timeout=8, headers={"User-Agent": "pie-ink/2.0"})
            root = ET.fromstring(r.content)
            titles = [i.findtext("title", "").strip().upper() for i in root.iter("item")]
            self._news = [t for t in titles if t][:6]
        except Exception as e:
            log.warning("news failed: %s", e)
        self._news_ts = time.time()

    def _quote_for(self, item):
        q = self._quotes.get(item["id"])
        if q is None:   # previews, or the first frame before the fetch lands
            q = watchlist.quote(item, int(self.settings.get("graph_days", 7)), cached_only=True)
            if q:
                self._quotes[item["id"]] = q
        return q

    def _check_online(self):
        if time.time() - self._online_ts < 30:
            return self._online
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=1).close()
            self._online = True
        except OSError:
            self._online = False
        self._online_ts = time.time()
        return self._online

    # -- shared bits ------------------------------------------------------------

    def _pick_face(self, q, hot):
        t = int(time.time())
        flip = (t // 3) % 2 == 0
        if not self._online:
            return FACES["sad"]
        if hot:
            return FACES["hot"] if t % 2 == 0 else FACES["hot2"]
        if not q:
            return FACES["awake"]
        up = float(q.get("sentiment_up") or 0)
        if up > 75:
            self._look = (self._look + 1) % 5
            if self._look == 4:
                return FACES["cool"]
            return FACES["look_r_happy"] if flip else FACES["look_l_happy"]
        if up > 50:
            return FACES["look_r_happy"] if flip else FACES["look_l_happy"]
        return FACES["look_r"] if flip else FACES["look_l"]

    @staticmethod
    def _freshness(q):
        if q.get("seed"):
            return "Bundled figures"
        age = q.get("age")
        if age is None:
            return "Updated just now"
        if age < 60:
            return "Updated just now"
        if age < 3600:
            return f"Updated {int(age // 60)} min ago"
        if age < 86400:
            return f"Updated {int(age // 3600)} h ago"
        return f"Updated {int(age // 86400)} d ago"

    @staticmethod
    def _staleness(q):
        """'old' marker for the prompt line when the numbers aren't fresh."""
        if not q:
            return ""
        if q.get("seed"):
            return "old data"
        age = q.get("age")
        if age is None or age < 1800:
            return ""
        if age < 86400:
            return f"{int(age // 3600) or 1}h old"
        return f"{int(age // 86400)}d old"

    def _draw_graph(self, f, prices, box):
        x0, y0, x1, y1 = box
        lo, hi = min(prices), max(prices)
        span = (hi - lo) or 1
        n = len(prices)
        pts = [(x0 + i * (x1 - x0) / (n - 1), y1 - (p - lo) / span * (y1 - y0))
               for i, p in enumerate(prices)]
        f.draw.line(pts, fill=f.fg, width=1)

    # -- render -------------------------------------------------------------------

    def render(self):
        f = self.frame()
        now = time.time()
        item = self._current_item(self._items(), now)
        hot = (_cpu_temp() or 0) >= 72
        if item.get("mining"):
            return self._render_mining(f, mining.MINERS[item["mining"]], now, hot)
        if item.get("holdings"):
            return self._render_holdings(f, now, hot)
        q = self._quote_for(item)
        return self._render_large(f, item, q, now, hot)

    # -- your crypto: what it is all worth ---------------------------------------------------------

    def _holdings_face(self, v, st, hot):
        t = int(time.time())
        flip = (t // 3) % 2 == 0
        if not self._online:
            return FACES["sad"]
        if hot:
            return FACES["hot"] if t % 2 == 0 else FACES["hot2"]
        if not v or not v["items"]:
            return FACES["awake"]
        pct, share = v["day_pct"] or 0.0, st["share"]
        move = (st.get("move") or {}).get("kind")
        if move == "crash" or pct <= -share:
            return FACES["sad"]
        if pct <= -share / 2 or st.get("slump"):
            return FACES["worried"]
        if st.get("over") or move == "rally" or pct >= share:
            return FACES["cool"] if (t // 3) % 4 else FACES["happy"]
        if pct >= 0:
            return FACES["look_r_happy"] if flip else FACES["look_l_happy"]
        return FACES["look_r"] if flip else FACES["look_l"]

    def _render_holdings(self, f, now, hot):
        """The page for what you hold: the total, today's and the week's move,
        the peak, the line you set, each coin's share — and the face on it."""
        st = holdings.HOLDINGS.status()
        v = st["value"]
        draw, fg = f.draw, f.fg
        g = self._geometry(f)
        T = g["T"]
        font12 = font(1, round(12 * T))
        money, signed = holdings.money, holdings.signed

        # header: what it is · how many coins · time (today's move sits in the middle, under the total)
        n = len(v["items"])
        parts = ["YOUR CRYPTO", f"{n} COIN{'S' if n != 1 else ''}", datetime.now().strftime("%-I:%M %p")]
        widths = [text_width(draw, p, font12) for p in parts]
        gap = (f.width - 4 - sum(widths)) // max(1, len(parts) - 1)
        x = 2
        for part, w in zip(parts, widths):
            draw.text((x, 0), part, font=font12, fill=fg)
            x += w + gap
        draw.line([(0, g["hdr"]), (f.width, g["hdr"])], fill=fg, width=1)

        # prompt (left) and the total (right), as big as the right column allows
        prompt = f"{self.settings.get('username', 'pi')}>"
        ages = [r["age"] for r in v["items"] if r.get("age") is not None]
        if any(r.get("seed") for r in v["items"]):
            prompt += " old data"
        elif ages and max(ages) >= 1800:
            prompt += f" {int(max(ages) // 3600) or 1}h old" if max(ages) < 86400 else f" {int(max(ages) // 86400)}d old"
        draw.text((5, g["hdr"] + 1), _fit(draw, prompt, font12, g["col"] - 8), font=font12, fill=fg)
        total = money(v["total"])
        room = f.width - 8 - g["col"]
        size = round(25 * T)
        while size > 11 and text_width(draw, total, font(1, size)) > room:
            size -= 1
        total_font = font(1, size)
        draw.text((f.width - 8 - text_width(draw, total, total_font), g["hdr"] + 2), total, font=total_font, fill=fg)

        # left: the face, or the week's readings as a graph — both, one above the other, on a tall panel
        slot = now % (FACE_SECONDS + GRAPH_SECONDS)
        hist = [t for _, t in st["history"]]
        show_graph = (not self.settings.get("show_faces", True) or slot >= FACE_SECONDS) and len(hist) > 1
        if g["tall"] and len(hist) > 1:
            face_y = g["hdr"] + 14 * T
            draw.text((3, face_y), self._holdings_face(v, st, hot), font=font(2, round(32 * T)), fill=fg)
            small = font(1, round(11 * T))
            first = st["history"][0][0]
            since = "a week of readings" if now - first >= 6 * 86400 else f"readings since {datetime.fromtimestamp(first).strftime('%b %-d')}"
            draw.text((5, g["band"] - 6 - 13 * T), since, font=small, fill=fg)
            self._draw_graph(f, hist, (5, face_y + 48 * T, g["col"] - 7, g["band"] - 10 - 13 * T))
        elif show_graph:
            self._draw_graph(f, hist, (5, g["hdr"] + 16 * T, g["col"] - 7, g["band"] - 4))
        else:
            draw.text((3, g["face_y"]), self._holdings_face(v, st, hot), font=font(2, round(32 * T)), fill=fg)

        # right column: today's move, always, under the total; then the rest — one at a time on a
        # small panel, all of them on a big one
        if v["day_pct"] is not None:
            pct1 = f"{v['day_pct']:+.1f}%"
            today = (f"Today {signed(v['day'])} ({pct1})", f"Today {signed(v['day'])} {pct1}", f"Today {signed(v['day'])}", f"Today {pct1}")
        else:
            today = ("Today: no change figure yet", "Today: N/A")
        lines = []
        if st.get("move"):
            lines.append((f"{'CRASH' if st['move']['kind'] == 'crash' else 'RALLY'} TODAY: {_pct(v['day_pct'])}",
                          f"{'CRASH' if st['move']['kind'] == 'crash' else 'RALLY'} {_pct(v['day_pct'])}"))
        if st.get("week"):
            lines.append((f"Week {signed(st['week']['change'])} ({_pct(st['week']['pct'])})", f"Week {_pct(st['week']['pct'])}"))
        if st.get("line"):
            lines.append((f"Past the {money(st['line'])} line!", "RICH!") if st.get("over")
                         else (f"{st['progress']:.0f}% of the way to {money(st['line'])}", f"{st['progress']:.0f}% to {money(st['line'])}"))
        if v["unknown"]:
            lines.append((f"No price yet: {', '.join(v['unknown'])}", f"No price: {v['unknown'][0]}"))
        if not lines:
            lines.append(("Readings build up from here", "Readings from here"))
        if g["tall"]:                                          # room for each coin: how much, what it's worth
            for r in v["items"][:6]:
                if r.get("value") is not None:
                    lines.append((f"{r['symbol']} {r['amount']:g} · {money(r['value'])}"
                                  + (f" ({_pct(r['change_1d'])})" if r.get("change_1d") is not None else ""),
                                  f"{r['symbol']} {money(r['value'])}"))
        top = g["hdr"] + 30 * T
        avail = g["band"] - 4 - top
        lh = 14 * T
        rows = int(avail // lh)
        room = f.width - g["col"] - 4

        def best(variants):
            return next((x for x in variants if text_width(draw, x, font12) <= room), _fit(draw, variants[-1], font12, room))
        if rows < 3:                                           # a small panel: today, and one more line at a time
            step = int(now // QUOTE_SECONDS) % len(lines)
            draw.text((g["col"], top + 2 * T), best(today), font=font12, fill=fg)
            draw.text((g["col"], top + 2 * T + lh), best(lines[step]), font=font12, fill=fg)
        else:
            shown = [today] + lines[:rows - 1]
            step_y = min(avail / len(shown), lh * 1.5)
            y = top + 2 * T
            for variants in shown:
                draw.text((g["col"], y), best(variants), font=font12, fill=fg)
                y += step_y

        # bottom band: each coin and what it is worth — or, on a tall panel that has listed those
        # above, how each one did today
        band = g["band"]
        small = font(1, round(11 * T))
        bold = font(2, round(11 * T))
        coins = [r for r in v["items"] if r.get("value") is not None]
        moved = [r for r in coins if r.get("change_1d") is not None][:5]
        if g["tall"] and moved:
            col = f.width / len(moved)
            for i, r in enumerate(moved):
                cx = int(col * i + col / 2)
                val = _pct(r["change_1d"])
                draw.text((cx - text_width(draw, r["symbol"], bold) // 2, band), r["symbol"], font=bold, fill=fg)
                draw.text((cx - text_width(draw, val, font12) // 2, band + 14 * T), val, font=font12, fill=fg)
                if i:
                    draw.line([(int(col * i), band + 2), (int(col * i), band + 28 * T)], fill=fg, width=1)
        elif coins:
            cols = 3 if f.width >= 300 else 2
            shown = coins[:cols * (2 if g["band_h"] > 34 * T else 1)]
            more = f"+{len(coins) - len(shown)}" if len(coins) > len(shown) else ""
            col_w = (f.width - 10 - (text_width(draw, more, bold) + 6 if more else 0)) / cols
            for i, r in enumerate(shown):
                cx = 5 + (i % cols) * col_w
                cy = band + 3 + (i // cols) * round(13 * T)
                draw.text((cx, cy), _fit(draw, f"{r['symbol']} {money(r['value'])}", small, col_w - 6), font=small, fill=fg)
            if more:
                draw.text((f.width - 5 - text_width(draw, more, bold), band + 3), more, font=bold, fill=fg)
        else:
            draw.text((5, band + 3), "No prices yet", font=bold, fill=fg)

        # footer: the peak, when, and how far below it — like a coin's ATH
        draw.line([(0, g["footer"]), (f.width, g["footer"])], fill=fg, width=1)
        fy = g["footer"] + 1
        if st.get("peak"):
            peak = st["peak"]["total"]
            draw.text((5, fy), f"PEAK: {money(peak)}", font=font12, fill=fg)
            below = (v["total"] - peak) / peak * 100.0 if peak else 0.0
            pct = f"{below:+.1f} %" if below > 0.05 else f"{below:.1f} %"
            draw.text((f.width - 5 - text_width(draw, pct, font12), fy), pct, font=font12, fill=fg)
            date = datetime.fromtimestamp(st["peak"]["ts"]).strftime("%Y-%m-%d")
            draw.text(((f.width - text_width(draw, date, font12)) // 2 + 20, fy), date, font=font12, fill=fg)
        else:
            draw.text((5, fy), "PEAK: first reading soon", font=font12, fill=fg)
        return f.image

    # -- your miners: Duino-Coin, Verus -----------------------------------------------------

    def _mining_face(self, d, hot):
        t = int(time.time())
        flip = (t // 3) % 2 == 0
        if not self._online:
            return FACES["sad"]
        if hot:
            return FACES["hot"] if t % 2 == 0 else FACES["hot2"]
        if not d:
            return FACES["awake"]
        if d["hashrate"] <= 0:
            return FACES["bored"]
        if d.get("alert"):
            return FACES["worried"]
        cycle = (t // 3) % 6
        if cycle == 5:
            return FACES["cool"]
        return FACES["look_r_happy"] if flip else FACES["look_l_happy"]

    def _render_mining(self, f, miner, now, hot):
        """A pool's page: balance, hashrate, what it earns, the miners."""
        d = miner.snapshot()
        draw, fg = f.draw, f.fg
        g = self._geometry(f)
        T = g["T"]
        font12 = font(1, round(12 * T))
        sym = miner.symbol
        dec = miner.decimals

        # header: name · symbol · what it earns (or what is wrong) · time
        if d and d["hashrate"] <= 0:
            middle = "NOT MINING"
        elif d and d.get("alert"):
            middle = f"UNDER {mining.hashrate_text(d['line'])}"
        elif d and d.get("per_day") is not None:
            middle = f"AVG: {d['per_day']:.{dec}f} /day"
        else:
            middle = "MINING" if d else "…"
        parts = [miner.title, sym, middle, datetime.now().strftime("%-I:%M %p")]
        widths = [text_width(draw, p, font12) for p in parts]
        gap = (f.width - 4 - sum(widths)) // max(1, len(parts) - 1)
        x = 2
        for part, w in zip(parts, widths):
            draw.text((x, 0), part, font=font12, fill=fg)
            x += w + gap
        draw.line([(0, g["hdr"]), (f.width, g["hdr"])], fill=fg, width=1)

        # prompt (left) and the balance (right), as big as the right column allows
        prompt = f"{miner.who_short() or sym.lower()}>"
        if d and d.get("age") is not None and d["age"] > 600:
            prompt += f" {d['age'] // 60}m old"
        draw.text((5, g["hdr"] + 1), _fit(draw, prompt, font12, g["col"] - 8), font=font12, fill=fg)
        if d:
            bal = d["balance"]
            balance = f"{bal:,.{dec}f} {sym}" if bal < 10000 else f"{bal:,.1f} {sym}" if bal < 1e6 else f"{bal:,.0f} {sym}"
            room = f.width - 8 - g["col"]
            size = round(25 * T)
            while size > 11 and text_width(draw, balance, font(1, size)) > room:
                size -= 1
            bal_font = font(1, size)
            draw.text((f.width - 8 - text_width(draw, balance, bal_font), g["hdr"] + 2), balance, font=bal_font, fill=fg)
        else:
            draw.text((g["col"], g["hdr"] + 16 * T), _fit(draw, miner.error or "fetching…", font12, f.width - g["col"] - 4), font=font12, fill=fg)

        # the face, left
        draw.text((3, g["face_y"]), self._mining_face(d, hot), font=font(2, round(32 * T)), fill=fg)

        if d:
            # right column: the numbers, one at a time on a small panel, all of them on a big one;
            # each has a long and a short form, and the longest that fits is used
            workers = f"{d['workers']} worker{'s' if d['workers'] != 1 else ''}"
            lines = [(f"{d['hashrate_text']}  ·  {workers}", f"{d['hashrate_text']} · {d['workers']}w")]
            if d.get("paid") is not None:
                lines.append((f"Paid {d['paid']:,.{dec}f} {sym}", f"Paid {d['paid']:,.2f}"))
            if d.get("price"):
                lines.append((f"Price ${d['price']:.6f}".rstrip("0").rstrip("."), f"${d['price']:.4f}".rstrip("0").rstrip(".")))
            if d.get("net_hashrate"):
                lines.append((f"Network {d['net_hashrate']}", f"Net {d['net_hashrate']}"))
            if d.get("luck"):
                lines.append((f"Luck {d['luck']}",))
            if d.get("trust_score") is not None:
                ach = f"  ·  {d['achievements']} achievements" if d.get("achievements") else ""
                lines.append((f"Trust {d['trust_score']}{ach}", f"Trust {d['trust_score']}"))
            if d.get("stake_amount"):
                lines.append((f"Stake {d['stake_amount']:,.3f}", f"Stake {d['stake_amount']:,.0f}"))
            if d.get("alert"):
                lines.insert(0, (f"UNDER {mining.hashrate_text(d['line'])} LINE" if d["hashrate"] > 0 else "NOTHING MINING",))
            top = g["hdr"] + 30 * T
            avail = g["band"] - 4 - top
            lh = 14 * T
            rows = int(avail // lh)
            room = f.width - g["col"] - 4

            def best(variants):
                return next((v for v in variants if text_width(draw, v, font12) <= room), _fit(draw, variants[-1], font12, room))
            if rows < 3:
                step = int(now // QUOTE_SECONDS) % len(lines)
                draw.text((g["col"], g["quote_y"]), best(lines[step]), font=font12, fill=fg)
            else:
                step_y = min(avail / len(lines[:rows]), lh * 1.5)
                y = top + 2 * T
                for variants in lines[:rows]:
                    draw.text((g["col"], y), best(variants), font=font12, fill=fg)
                    y += step_y

            # bottom band: the miners, a hashrate each
            band = g["band"]
            small = font(1, round(11 * T))
            bold = font(2, round(11 * T))
            if d["miners"]:
                cols = 3 if f.width >= 300 else 2
                shown = d["miners"][:cols * (2 if g["band_h"] > 34 * T else 1)]
                more = f"+{len(d['miners']) - len(shown)}" if len(d["miners"]) > len(shown) else ""
                col_w = (f.width - 10 - (text_width(draw, more, bold) + 6 if more else 0)) / cols
                for i, m in enumerate(shown):
                    cx = 5 + (i % cols) * col_w
                    cy = band + 3 + (i // cols) * round(13 * T)
                    label = _fit(draw, f"{m['identifier']} {mining.hashrate_text(m['hashrate'])}", small, col_w - 6)
                    draw.text((cx, cy), label, font=small, fill=fg)
                if more:
                    draw.text((f.width - 5 - text_width(draw, more, bold), band + 3), more, font=bold, fill=fg)
            else:
                draw.text((5, band + 3), "No miners connected", font=bold, fill=fg)

        # footer: what the pool says about you · the pool · workers
        draw.line([(0, g["footer"]), (f.width, g["footer"])], fill=fg, width=1)
        if d:
            fy = g["footer"] + 1
            if d.get("trust_score") is not None:
                left = f"TRUST: {d['trust_score']}"
            elif d.get("paid") is not None:
                left = f"PAID: {d['paid']:,.2f}"
            else:
                left = sym
            draw.text((5, fy), left, font=font12, fill=fg)
            workers = f"Workers: {d['workers']}"
            wx = f.width - 5 - text_width(draw, workers, font12)
            draw.text((wx, fy), workers, font=font12, fill=fg)
            mid = (f"LUCK: {d['luck']}" if d.get("luck") else "") or (f"POOL: {d['pools'][0]}" if d.get("pools") else "")
            if mid:
                mid = _fit(draw, mid.upper(), font12, wx - 8 - (5 + text_width(draw, left, font12) + 10))
                tw = text_width(draw, mid, font12)
                x = max((f.width - tw) // 2, 5 + text_width(draw, left, font12) + 10)
                if x + tw <= wx - 8:
                    draw.text((x, fy), mid, font=font12, fill=fg)
        return f.image

    def _geometry(self, f):
        T = 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5
        T = max(1.0, min(1.6, T))
        g = {"T": T}
        g["hdr"] = round(14 * T)                  # header row height (line under it)
        g["footer"] = f.height - round(14 * T)    # footer line y
        g["band_h"] = round(29 * T)               # bottom panel band
        g["band"] = g["footer"] - 1 - g["band_h"] # bottom band top (3px lower than it used to be)
        mid = (g["hdr"] + g["band"]) / 2
        g["tall"] = (g["band"] - g["hdr"]) > 120          # 2.7" and up
        g["face_y"] = g["hdr"] + 14 * T if g["tall"] else max(g["hdr"] + 14 * T, mid - 21 * T)
        g["quote_y"] = max(g["hdr"] + 31 * T, mid - 6 * T)
        # right column: past the face, with a gap that grows with the panel
        face_w = text_width(f.draw, "(◕‿‿◕)", font(2, round(32 * T))) + 3
        g["col"] = max(f.width // 2, int(face_w + 12 * T))
        return g

    def _render_large(self, f, item, q, now, hot):
        d, fg = f.draw, f.fg
        g = self._geometry(f)
        T = g["T"]
        font12 = font(1, round(12 * T))
        dec = int(q.get("decimals", 2)) if q else 2

        # header: name · symbol · rank · time, spread across the width
        name = item["name"].upper() if len(item["name"]) <= 12 else ""
        rank = f"RANK: {q.get('rank') if q else '-'}"
        clock = datetime.now().strftime("%-I:%M %p")
        parts = [p for p in (name, item["symbol"], rank, clock) if p]
        widths = [text_width(d, p, font12) for p in parts]
        gap = (f.width - 4 - sum(widths)) // max(1, len(parts) - 1)
        x = 2
        for part, w in zip(parts, widths):
            d.text((x, 0), part, font=font12, fill=fg)
            x += w + gap
        d.line([(0, g["hdr"]), (f.width, g["hdr"])], fill=fg, width=1)

        # prompt (left) and price (right)
        prompt = f"{self.settings.get('username', 'pi')}>"
        stale = self._staleness(q)
        if stale:
            prompt += f" {stale}"
        d.text((5, g["hdr"] + 1), prompt, font=font12, fill=fg)
        if q and q.get("price") is not None:
            base = 18 if dec > 10 else 20 if dec > 8 else 22 if dec > 6 else 25
            price_font = font(1, round(base * T))
            price = _num(q["price"], dec, "$")
            d.text((f.width - 10 - text_width(d, price, price_font), g["hdr"] + 2), price, font=price_font, fill=fg)
        else:
            d.text((g["col"], g["hdr"] + 16 * T), "fetching…", font=font12, fill=fg)

        # left: face or graph
        slot = now % (FACE_SECONDS + GRAPH_SECONDS)
        show_graph = not self.settings.get("show_faces", True) or slot >= FACE_SECONDS
        face_font = font(2, round(32 * T))
        graph_bottom = g["band"] - 4
        face_y = g["face_y"]
        if g["tall"] and q:
            # under the face/graph: supply and how fresh the numbers are
            small = font(1, round(11 * T))
            lines = [f"Supply {_short(q.get('circulating_supply'))} of {_short(q.get('total_supply'))}"
                     if q.get("total_supply") else f"Supply {_short(q.get('circulating_supply'))}",
                     self._freshness(q)]
            lh = round(13 * T)
            y = g["band"] - 6 - lh * len(lines)
            for line in lines:
                d.text((5, y), line, font=small, fill=fg)
                y += lh
            graph_bottom = g["band"] - 8 - lh * len(lines)
            face_y = (g["hdr"] + 16 * T + graph_bottom) / 2 - 21 * T
        if show_graph and q and len(q.get("graph") or []) > 1:
            self._draw_graph(f, q["graph"], (5, g["hdr"] + 16 * T, g["col"] - 7, graph_bottom))
        else:
            d.text((3, face_y), self._pick_face(q, hot), font=face_font, fill=fg)

        if q:
            self._draw_quote(f, q, dec, hot, g)
            self._draw_bottom(f, q, dec, g)

        # footer: ATH · date · % from ATH
        d.line([(0, g["footer"]), (f.width, g["footer"])], fill=fg, width=1)
        if q:
            fy = g["footer"] + 1
            d.text((5, fy), f"ATH: {_num(q.get('ath'), dec, '$')}", font=font12, fill=fg)
            pct = f"{_num(q.get('from_ath'))} %"
            d.text((f.width - 5 - text_width(d, pct, font12), fy), pct, font=font12, fill=fg)
            if dec <= 9 and q.get("ath_date"):
                date = q["ath_date"]
                d.text(((f.width - text_width(d, date, font12)) // 2 + 20, fy), date, font=font12, fill=fg)
        return f.image

    def _draw_quote(self, f, q, dec, hot, g):
        T = g["T"]
        fnt = font(1, round(12 * T))
        n = len(QUOTES)
        step = int(time.time() % (QUOTE_SECONDS * (n + 4)) // QUOTE_SECONDS)
        quote = HOT_QUOTES[int(time.time() // QUOTE_SECONDS) % len(HOT_QUOTES)] if hot else QUOTES[step % n]
        # how many lines fit between the price row and the bottom band
        top = g["hdr"] + 30 * T
        avail = g["band"] - 4 - top
        lh = 14 * T
        rows = int(avail // lh)
        if rows < 3:
            # small panel: one rotating line, as before
            if hot:
                text = quote
            elif step == 0:
                text = f"Market Cap: ${_short(q.get('market_cap'))}"
            elif step == 1:
                text = f"Circulation: {_short(q.get('circulating_supply'))}"
            elif step == 2:
                text = f"Total Supply: {_short(q.get('total_supply'))}"
            elif step == 3:
                text = (f"High: {_num(q.get('day_high'), dec, '$')}\n"
                        f"Low: {_num(q.get('day_low'), dec, '$')}")
            else:
                text = QUOTES[(step - 4) % n]
            f.draw.multiline_text((g["col"], g["quote_y"]), text, font=fnt, fill=f.fg)
            return
        # bigger panel: a block of stats, spread evenly over the space, quote last
        ch = q.get("change", {})
        room = f.width - g["col"] - 4
        # each stat has a long and a short form; use the longest that fits the column
        lo, hi = q.get("day_low"), q.get("day_high")
        stats = [
            (f"Market cap  ${_short(q.get('market_cap'))}", f"Mcap ${_short(q.get('market_cap'))}"),
            (f"24h  {_num(lo, dec, '$')} – {_num(hi, dec, '$')}",
             f"24h ${_short(lo, 1)}–${_short(hi, 1)}", f"${_short(lo, 1)}–${_short(hi, 1)}", f"${_short(lo, 0)}–${_short(hi, 0)}"),
            (f"24h {_pct(ch.get('24h'))}   7d {_pct(ch.get('7d'))}", f"24h {_pct(ch.get('24h'))} 7d {_pct(ch.get('7d'))}"),
            (quote,),
        ][:rows]
        step_y = min(avail / len(stats), lh * 1.5)         # roomy but not spread out
        y = top + 2 * T
        for variants in stats:
            line = next((v for v in variants if text_width(f.draw, v, fnt) <= room), variants[-1])
            f.draw.text((g["col"], y), line, font=fnt, fill=f.fg)
            y += step_y

    def _icons_for(self, dark, size=18):
        if self._icons is None:
            def load(name):
                img = Image.open(os.path.join(ASSETS_DIR, name)).convert("L")
                return img, ImageOps.invert(img)
            self._icons = {"happy": load("happy_face.png"), "sad": load("sad_face.png")}
        i = 1 if dark else 0
        return (self._icons["happy"][i].resize((size, size), Image.LANCZOS),
                self._icons["sad"][i].resize((size, size), Image.LANCZOS))

    def _draw_bottom(self, f, q, dec, g):
        d, fg, T = f.draw, f.fg, g["T"]
        font12 = font(1, round(12 * T))
        bold = font(2, round(11 * T))
        news_font = font(5, round(10 * T))
        news = self._news if self.settings.get("show_news", True) else []
        panels = 4 if news else 3
        step = int(time.time() % (BOTTOM_SECONDS * panels) // BOTTOM_SECONDS)
        band = g["band"]
        line_y = band + round(19 * T)
        line_w = int(f.width * 0.78)
        line_x = (f.width + 2 - line_w) // 2
        r = round(5 * T)

        def bar(position, label):
            d.line([(line_x, line_y), (line_x + line_w, line_y)], fill=fg, width=2)
            for i in range(1, 4):
                tx = line_x + i * (line_w // 4)
                d.line([(tx, line_y - r), (tx, line_y + r)], fill=fg, width=1)
            if position is not None:
                cx = line_x + int(line_w * max(0.0, min(1.0, position)))
                d.ellipse([(cx - r, line_y - r), (cx + r, line_y + r)], fill=fg)
                d.text((cx - text_width(d, label, font12) // 2, line_y - 21 * T), label, font=font12, fill=fg)

        if step == 0:   # sentiment
            up = q.get("sentiment_up")
            bar(float(up) / 100 if up is not None else None, f"{_num(up, 1)} %")
            icon_px = round(18 * T)
            happy, sad = self._icons_for(f.dark, icon_px)
            f.blit(sad, (line_x - icon_px - 5, line_y - icon_px // 2 - 1))
            f.blit(happy, (line_x + line_w + 7, line_y - icon_px // 2 - 1))
        elif step == 1:  # 24h range
            lo, hi, cur = q.get("day_low"), q.get("day_high"), q.get("price")
            pos = (cur - lo) / (hi - lo) if None not in (lo, hi, cur) and hi > lo else None
            bar(pos, _num(cur, dec, "$"))
            d.text((line_x - text_width(d, "24L", font12) - 4, line_y - 9 * T), "24L", font=font12, fill=fg)
            d.text((line_x + line_w + 5, line_y - 10 * T), "24H", font=font12, fill=fg)
        elif step == 2:  # % changes
            labels = ["24H", "7D", "30D", "6M", "1Y"]
            keys = ["24h", "7d", "30d", "200d", "1y"]
            col = f.width / len(labels)
            for i, (lab, key) in enumerate(zip(labels, keys)):
                val = f"{_num(q['change'].get(key))}%"
                cx = int(col * i + col / 2)
                d.text((cx - text_width(d, lab, bold) // 2, band), lab, font=bold, fill=fg)
                d.text((cx - text_width(d, val, font12) // 2, band + 14 * T), val, font=font12, fill=fg)
                if i:
                    d.line([(int(col * i), band + 2), (int(col * i), band + 28 * T)], fill=fg, width=1)
        else:            # news
            title = news[int(time.time() / 10) % len(news)]
            lines = wrap(d, title, news_font, f.width - 64 * T, max_lines=2)
            d.text((4, band + 8 * T), "NEWS:", font=news_font, fill=fg)
            for i, line in enumerate(lines):
                d.text((60 * T, band + i * 14 * T), line, font=news_font, fill=fg)
