"""Your crypto: what you hold, what it is worth, and a word when that changes a lot.

Put in how much of each coin you hold (the Crypto tab, under each coin) and
the total is worked out from the same CoinGecko prices the screen shows —
plus whatever your miners have sitting at their pools, when those are on. A
thread keeps the prices fresh whatever screen is up, keeps a week of readings
of the total (data/holdings.json) so the page and the screen can show how
it has moved, remembers the most it has ever been worth, and watches for the
moments worth a word: the total passing the line you set (a million, say —
you are rich), slipping back under it, a day's move of more than the share
you set (a crash, or a rally), and a long slide well below the peak. Each
goes to your phone, the events list and the bot — in its own words when it
has a model. The bot knows the numbers all the time ("how's my crypto?").
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

from .settings import DATA_DIR

log = logging.getLogger(__name__)

PATH = os.path.join(DATA_DIR, "holdings.json")
REFRESH = 60                 # seconds between looks (the prices themselves are cached for five minutes)
SAMPLE_GAP = 300             # a reading of the total this often
KEEP_DAYS = 7                # of readings; older days keep one figure each
KEEP_DAILY = 400
READINGS = 2                 # readings past a line before it counts (one odd price isn't a crash)
BAND = 0.02                  # back under the line means 2 % under it: a total sitting on the line doesn't flip-flop
QUIET = 3 * 3600             # the same word about the line at most this often; the state follows regardless
SLUMP_TIMES = 2              # a slide of this many times the day's share below the peak gets a word
DEFAULTS = {"show": True, "milestone": 1000000, "move_pct": 10, "notify": True, "comment": True}


def money(v, decimals=None):
    """$1,234,567 · $12,345.67 · $0.0421 — as many decimals as the size needs."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if decimals is None:
        decimals = 0 if abs(v) >= 1000 else 2 if abs(v) >= 1 else 4
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.{decimals}f}"


def signed(v):
    """+$123 / -$45.20, for a change (no cents once it's hundreds)."""
    return ("+" if v >= 0 else "-") + money(abs(v), 0 if abs(v) >= 100 else None)


def _pct(v):
    return f"{v:+.1f}%" if v is not None else "N/A"


class Holdings:
    def __init__(self):
        self.conf = dict(DEFAULTS)
        self.samples = []            # [ts, total] for the last week
        self.days = []               # [date, total] one a day, further back
        self.peak = None             # [total, ts]: the most it has ever been worth
        self.over = None             # above the line? None: not judged yet
        self.move = None             # {"kind": "crash"|"rally", "pct": said at, "since": ts} while a day's move is on
        self.slump = None            # {"since": ts, "pct": said at} while it's well under the peak
        self.value_now = None        # the last worked-out value (see value())
        self.on_alert = None         # (kind, text, data) -> None; kind: rich | under | crash | rally | slump
        self._counts = {}            # readings towards a line
        self._said = {}              # when each kind of word last went out
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._load()

    # -- settings ---------------------------------------------------------------------------------

    def configure(self, conf):
        old = dict(self.conf)
        self.conf = {**DEFAULTS, **(conf or {})}
        if self.line() != self._line_of(old) and self.value_now:
            # the line moved: where the total stands to it now is the starting point, not news
            self.over = self.value_now["total"] >= self.line() if self.line() else None
            self._counts = {}
            self._save()

    @staticmethod
    def _line_of(conf):
        try:
            return max(0.0, float(conf.get("milestone") or 0))
        except (TypeError, ValueError):
            return 0.0

    def line(self):
        return self._line_of(self.conf)

    def share(self):
        """The day's move, in percent, that gets a word."""
        try:
            return max(1.0, min(90.0, float(self.conf.get("move_pct") or 10)))
        except (TypeError, ValueError):
            return 10.0

    # -- what you hold ---------------------------------------------------------------------------

    @staticmethod
    def held():
        """The watchlist coins with an amount against them."""
        from . import watchlist
        out = []
        for i in watchlist.load():
            try:
                amount = float(i.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount > 0:
                out.append({**i, "amount": amount})
        return out

    def any(self):
        """Is there anything to watch — an amount, or a miner with a balance?"""
        if self.held():
            return True
        try:
            from . import mining
            return any((m.snapshot() or {}).get("balance") for m in mining.enabled())
        except Exception:
            return False

    def value(self, cached_only=True):
        """What it is all worth: {"total", "day" ($ since yesterday), "day_pct",
        "items": [...], "unknown": [symbols with no price], "when"}. Prices come
        from the cache unless asked otherwise (the thread asks; the page doesn't)."""
        from . import coingecko
        items, total, yesterday, unknown = [], 0.0, 0.0, []
        for i in self.held():
            d = coingecko.coin_data(i["id"], cached_only) or {}
            price = d.get("price")
            row = {"id": i["id"], "symbol": i["symbol"], "name": i.get("name", i["symbol"]), "amount": i["amount"],
                   "price": price, "value": None, "change_1d": None, "day": None,
                   "seed": bool(d.get("seed")), "age": coingecko.coin_age(i["id"])}
            if price is None:
                unknown.append(i["symbol"])
            else:
                v = i["amount"] * float(price)
                ch = (d.get("change") or {}).get("24h")
                v0 = v / (1 + ch / 100.0) if ch is not None and ch > -99.9 else v
                row.update(value=v, change_1d=ch, day=v - v0)
                total += v
                yesterday += v0
            items.append(row)
        try:
            from . import mining
            for m in mining.enabled():
                d = m.snapshot()
                if d and d.get("balance") and d.get("price"):
                    v = float(d["balance"]) * float(d["price"])
                    items.append({"id": f"{m.kind}-mining", "symbol": m.symbol, "name": f"{m.title.title()} at the pool",
                                  "amount": float(d["balance"]), "price": float(d["price"]), "value": v,
                                  "change_1d": None, "day": 0.0, "mined": True, "seed": False, "age": d.get("age")})
                    total += v
                    yesterday += v
        except Exception:
            log.debug("no mining for the total", exc_info=True)
        items.sort(key=lambda r: -(r["value"] or 0))
        day = total - yesterday
        out = {"total": total, "day": day, "day_pct": (day / yesterday * 100.0) if yesterday > 0 else None,
               "items": items, "unknown": unknown, "when": time.time(),
               "coins": sum(1 for r in items if not r.get("mined"))}
        with self._lock:
            self.value_now = out
        return out

    # -- history --------------------------------------------------------------------------------

    def _load(self):
        try:
            with open(PATH) as f:
                d = json.load(f)
            self.samples = [s for s in d.get("samples", []) if isinstance(s, list) and len(s) == 2]
            self.days = d.get("days", [])
            self.peak = d.get("peak")
            self.over = d.get("over")
            self.move = d.get("move")
            self.slump = d.get("slump")
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _save(self):
        try:
            with open(PATH, "w") as f:
                json.dump({"samples": self.samples, "days": self.days, "peak": self.peak, "over": self.over,
                           "move": self.move, "slump": self.slump}, f)
        except OSError as e:
            log.warning("could not save holdings: %s", e)

    def _sample(self, total, now):
        if self.samples and now - self.samples[-1][0] < SAMPLE_GAP:
            return False
        self.samples.append([round(now, 1), round(total, 2)])
        cut = now - KEEP_DAYS * 86400
        aged = [s for s in self.samples if s[0] < cut]
        self.samples = [s for s in self.samples if s[0] >= cut]
        for ts, v in aged:                                 # older days keep their last figure
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if self.days and self.days[-1][0] == day:
                self.days[-1][1] = v
            else:
                self.days.append([day, v])
        del self.days[:-KEEP_DAILY]
        if not self.peak or total > self.peak[0]:
            self.peak = [round(total, 2), round(now, 1)]
        return True

    def ago(self, seconds, now=None):
        """The total about this long ago, from the readings, or None."""
        now = now or time.time()
        want = now - seconds
        best = None
        for ts, v in self.samples:
            if best is None or abs(ts - want) < abs(best[0] - want):
                best = (ts, v)
        if best and abs(best[0] - want) <= max(SAMPLE_GAP * 3, seconds * 0.15):
            return best[1]
        for day, v in reversed(self.days):
            try:
                ts = datetime.strptime(day, "%Y-%m-%d").timestamp() + 86400
            except ValueError:
                continue
            if abs(ts - want) <= 86400:
                return v
        return None

    def history(self, points=200):
        """The week's readings for a small graph: [[ts, total], ...], thinned."""
        s = self.samples
        if len(s) <= points:
            return list(s)
        step = len(s) / points
        return [s[int(i * step)] for i in range(points)] + [s[-1]]

    # -- the thread -------------------------------------------------------------------------------

    def start(self, conf):
        self.configure(conf)
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="holdings", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def update(self, conf):
        self.configure(conf)
        self.refresh()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                log.exception("holdings")
            self._stop.wait(REFRESH)

    def refresh(self):
        """Fresh prices for what you hold (from the cache when it's recent), a
        reading of the total, and a look at the lines."""
        if not self.any():
            return None
        v = self.value(cached_only=False)
        if v["unknown"]:
            return v                                       # a coin without a price: no reading, no judgement
        now = v["when"]
        with self._lock:
            sampled = self._sample(v["total"], now)
            fire = self._watch(v, now)
            if sampled or fire:
                self._save()
        for kind, text in fire:
            log.info("holdings: %s", text)
            if self.on_alert:
                try:
                    self.on_alert(kind, text, v)
                except Exception:
                    log.exception("holdings alert")
        return v

    def _count(self, key, hit):
        """Readings in a row for `key`; 0 when the reading went the other way."""
        self._counts[key] = self._counts.get(key, 0) + 1 if hit else 0
        return self._counts[key]

    def _watch(self, v, now):
        fire = []
        total, pct = v["total"], v["day_pct"]
        top = ", ".join(f"{r['symbol']} {money(r['value'])}" for r in v["items"][:3] if r.get("value"))
        line = self.line()
        if line:
            if self.over is None:
                self.over = total >= line                  # the first look: where it stands is the starting point
            over_n, under_n = self._count("over", total >= line), self._count("under", total < line * (1 - BAND))
            if not self.over and over_n >= READINGS:
                self.over, self._counts["under"] = True, 0
                if now - self._said.get("rich", 0) >= QUIET:
                    fire.append(("rich", f"Your crypto just passed {money(line)}: it's worth {money(total)} now"
                                 + (f" ({top})" if top else "") + ". You're rich!"))
            elif self.over and under_n >= READINGS:
                self.over, self._counts["over"] = False, 0
                if now - self._said.get("under", 0) >= QUIET:
                    fire.append(("under", f"Your crypto has slipped back under {money(line)}: {money(total)} now"
                                 + (f", {_pct(pct)} today" if pct is not None else "") + "."))
        share = self.share()
        if pct is not None:
            kind = "crash" if pct <= -share else "rally" if pct >= share else None
            crash_n, rally_n = self._count("crash", kind == "crash"), self._count("rally", kind == "rally")
            if kind and max(crash_n, rally_n) >= READINGS:
                said = self.move["pct"] if self.move and self.move["kind"] == kind else None
                if said is None or abs(pct) >= abs(said) + share:      # a word, and another if it goes a lot further
                    self.move = {"kind": kind, "pct": round(pct, 1), "since": (self.move or {}).get("since") or now}
                    movers = ", ".join(f"{r['symbol']} {_pct(r['change_1d'])}" for r in v["items"][:3] if r.get("change_1d") is not None)
                    fire.append((kind, f"The market is {'down' if kind == 'crash' else 'up'}: your crypto is worth "
                                 f"{money(total)}, {_pct(pct)} today ({signed(v['day'])})" + (f" — {movers}" if movers else "") + "."))
            if self.move and self._count("calm", abs(pct) < share / 2) >= READINGS:
                self.move = None                           # the day's move has eased: the next one is news again
        if self.peak and self.peak[0] > 0:
            # a long slide: well below the peak on a day that isn't itself a crash (that has its own word)
            below = (self.peak[0] - total) / self.peak[0] * 100.0
            deep = SLUMP_TIMES * share
            if self._count("slump", below >= deep and not self.move) >= READINGS:
                said = self.slump["pct"] if self.slump else None
                if said is None or below >= said + share:
                    self.slump = {"since": (self.slump or {}).get("since") or now, "pct": round(below, 1)}
                    when = datetime.fromtimestamp(self.peak[1]).strftime("%b %-d")
                    fire.append(("slump", f"Your crypto is {below:.0f}% below the most it has been worth "
                                 f"({money(self.peak[0])} on {when}): {money(total)} now."))
            if self.slump and below < share:
                self.slump = None
        for kind, _ in fire:
            self._said[kind] = now
        return fire

    # -- for the page, the screen, the bot ------------------------------------------------------

    def status(self):
        v = self.value_now
        if not v or time.time() - v["when"] > 30:          # from the cache: quick, and what the thread last fetched
            v = self.value()
        now = time.time()
        line = self.line()
        week = self.ago(7 * 86400, now)
        return {"any": bool(v["items"]), "value": v, "history": self.history(),
                "peak": {"total": self.peak[0], "ts": self.peak[1]} if self.peak else None,
                "week": {"then": week, "change": v["total"] - week, "pct": (v["total"] - week) / week * 100.0 if week else None} if week else None,
                "line": line, "over": self.over if line else None,
                "progress": (v["total"] / line * 100.0) if line else None,
                "move": self.move, "slump": self.slump, "share": self.share(),
                "readings": len(self.samples)}

    def words(self):
        """One line for the bot's memory of the world, or '' with nothing held."""
        v = self.value_now or (self.value() if self.any() else None)
        if not v or not v["items"]:
            return ""
        bits = [f"worth {money(v['total'])} across {len(v['items'])} coin{'s' if len(v['items']) != 1 else ''}"
                + " (" + ", ".join(f"{r['symbol']} {money(r['value'])}" for r in v["items"][:4] if r.get("value")) + ")"]
        if v["day_pct"] is not None:
            bits.append(f"{'down' if v['day'] < 0 else 'up'} {abs(v['day_pct']):.1f}% today ({signed(v['day'])})")
        week = self.ago(7 * 86400)
        if week:
            bits.append(f"{'down' if v['total'] < week else 'up'} {abs(v['total'] - week) / week * 100:.1f}% on a week ago")
        if self.peak:
            below = (self.peak[0] - v["total"]) / self.peak[0] * 100.0 if self.peak[0] else 0
            when = datetime.fromtimestamp(self.peak[1]).strftime("%b %-d")
            bits.append(f"the most it has ever been worth is {money(self.peak[0])} ({when})"
                        + (f", {below:.0f}% below that now" if below >= 1 else " — that's now"))
        line = self.line()
        if line:
            bits.append(f"past the {money(line)} line you set — rich" if v["total"] >= line
                        else f"{v['total'] / line * 100:.0f}% of the way to the {money(line)} line you set")
        if self.move:
            bits.append(f"a {'CRASH' if self.move['kind'] == 'crash' else 'RALLY'} today")
        if v["unknown"]:
            bits.append("no price yet for " + ", ".join(v["unknown"]))
        return "Your crypto holdings: " + "; ".join(bits)

    def summary(self):
        """A few lines for Telegram or the page."""
        v = self.value()
        if not v["items"]:
            return "No holdings yet — put in how much of each coin you hold under Crypto."
        lines = [f"Your crypto: {money(v['total'])}"
                 + (f", {_pct(v['day_pct'])} today ({signed(v['day'])})" if v["day_pct"] is not None else "")]
        for r in v["items"]:
            if r.get("value") is None:
                lines.append(f"{r['symbol']}: {r['amount']:g} — no price yet")
            else:
                lines.append(f"{r['symbol']}: {r['amount']:g} × {money(r['price'])} = {money(r['value'])}"
                             + (f" ({_pct(r['change_1d'])})" if r.get("change_1d") is not None else "")
                             + (" at the pool" if r.get("mined") else ""))
        week = self.ago(7 * 86400)
        if week:
            lines.append(f"A week ago: {money(week)} ({_pct((v['total'] - week) / week * 100)})")
        if self.peak:
            lines.append(f"Peak: {money(self.peak[0])} on {datetime.fromtimestamp(self.peak[1]).strftime('%b %-d')}")
        line = self.line()
        if line:
            there = "past it" if v["total"] >= line else f"{v['total'] / line * 100:.0f}% there"
            lines.append(f"Line: {money(line)} — {there}")
        return "\n".join(lines)


HOLDINGS = Holdings()
