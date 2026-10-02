"""Your miners: Duino-Coin and Verus, and a word when the hashrate drops.

Two pools, one shape. Duino-Coin's server knows everything about a username
— balance, trust score, every miner connected under it with its rate, pool
and software; api.json has the coin's price and the network's rate. For
Verus, luckpool.net knows a wallet: the rate in sols, the unpaid balance,
what has been paid, the luck, and each worker's rate. A thread per coin asks
once a minute while the coin is on, keeps a day of samples (data/mining-
duco.json, data/mining-verus.json) so it can say what you are earning a
day, and watches the total hashrate: when it stays under the line you set
(or at nothing, if you set no line) for two readings, the app is told —
your phone, the bot, the events list — and told again when it comes back. A
worker that vanishes from the list gets a word of its own.

Each coin appears in the crypto rotation: on the e-ink, the LCD and the
colour panel. The bot knows the numbers too ("how's my mining?").
"""
import json
import logging
import os
import re
import threading
import time

import requests

from .settings import DATA_DIR

log = logging.getLogger(__name__)

DUCO_USER_URL = "https://server.duinocoin.com/v3/users/{user}"
DUCO_NET_URL = "https://server.duinocoin.com/api.json"
VERUS_URL = "https://luckpool.net/verus/miner/{wallet}"
SAMPLE_HOURS = 24            # of history kept for the earnings-a-day figure
NET_EVERY = 300              # the price and the network rate move slowly
LOW_READINGS = 2             # readings under the line before it counts (a miner reconnecting isn't a drop)
UA = "pie-ink/3.0 (https://github.com/frogCaller/piE-ink)"


def hashrate_text(h):
    """450 H/s, 12.3 KH/s, 34.42 MH/s — the way the pools write it."""
    try:
        h = float(h or 0)
    except (TypeError, ValueError):
        h = 0.0
    for cut, unit in ((1e12, "TH/s"), (1e9, "GH/s"), (1e6, "MH/s"), (1e3, "KH/s")):
        if h >= cut:
            return f"{h / cut:.2f} {unit}"
    return f"{h:.1f} H/s" if h < 100 else f"{h:.0f} H/s"


def parse_hashrate(text):
    """'34.42 MH', '1.2 GH/s', '12345' -> H/s."""
    m = re.match(r"\s*([\d.,]+)\s*([kKmMgGtT]?)[hHsS]?", str(text or ""))
    if not m:
        return 0.0
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    return value * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}[m.group(2).lower()]


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class Miner:
    """What every pool watcher shares: the thread, the samples, the line."""
    kind = "miner"
    symbol = "COIN"
    title = "MINING"
    decimals = 3                 # for the balance on a screen
    balance_label = "balance"

    def __init__(self):
        self.conf = {}
        self._lock = threading.Lock()
        self.thread = None
        self.stop_flag = threading.Event()
        self._wake = threading.Event()
        self.data = None             # the last good reading
        self.error = None
        self.fetched_at = 0.0
        self._samples = None         # [(ts, earned)]
        self._low_n = 0
        self._ok_n = 0
        self.alert = None            # {"since", "hashrate", "line", "text"} while the hashrate is under the line
        self._seen = {}              # worker identifier -> readings in a row it has been missing
        self._known = set()          # identifiers seen mining lately
        self.on_alert = None         # (kind, text, data) -> None; kind: "drop" | "back" | "worker"

    # -- to be filled in per pool ---------------------------------------------------------------

    def who(self):
        """The username or wallet the pool is asked about."""
        return ""

    def who_short(self):
        return self.who()

    def _read(self):
        """One reading from the pool as the common record. Raises on trouble."""
        raise NotImplementedError

    def _earned(self, data):
        """What grows as you mine — sampled for the earnings-a-day figure."""
        return data["balance"]

    # -- lifecycle ---------------------------------------------------------------------------

    def configure(self, conf):
        self.conf = dict(conf or {})

    def enabled(self):
        return bool(self.conf.get("enabled")) and bool(self.who())

    def line(self):
        """The hashrate you want kept, in H/s; 0 means only 'nothing mining' is worth a word."""
        return max(0.0, _num(self.conf.get("min_hashrate"), 0.0))

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, conf):
        self.configure(conf)
        if not self.enabled():
            self.stop()
            return False
        if self.running():
            self._wake.set()
            return True
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"mining-{self.kind}")
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()
        self._wake.set()

    def update(self, conf):
        old = self.who()
        self.configure(conf)
        if not self.enabled():
            self.stop()
            return
        if self.who() != old:
            with self._lock:
                self.data, self.error, self._samples = None, None, []
                self.alert, self._low_n, self._ok_n, self._known = None, 0, 0, set()
        self.start(self.conf)

    def refresh_now(self):
        self._wake.set()

    def _loop(self):
        log.info("%s: watching %s", self.title.lower(), self.who_short())
        while not self.stop_flag.is_set():
            try:
                self.fetch()
            except Exception:
                log.exception("%s", self.kind)
            every = max(30, int(_num(self.conf.get("refresh_seconds"), 60) or 60))
            self._wake.wait(every)
            self._wake.clear()
        log.info("%s: stopped", self.title.lower())

    # -- the numbers ----------------------------------------------------------------------------

    def fetch(self, keep=True):
        """One reading. Never raises. keep=False is a look-up only: nothing
        sampled, nothing watched."""
        if not self.who():
            return None
        try:
            data = self._read()
        except requests.exceptions.ConnectionError:
            self.error = "can't reach the pool"
            return None
        except Exception as e:
            self.error = str(e)[:120]
            log.info("%s: %s", self.kind, self.error)
            return None
        data.update(kind=self.kind, symbol=self.symbol, title=self.title, who=self.who_short(),
                    decimals=self.decimals, balance_label=self.balance_label, line=self.line(),
                    hashrate_text=hashrate_text(data["hashrate"]), workers=len(data["miners"]))
        if not keep:
            return data
        self._sample(self._earned(data))
        data["per_day"] = self.per_day()
        with self._lock:
            self.data, self.error, self.fetched_at = data, None, time.time()
        self._watch(data)
        return data

    # -- what it earns -------------------------------------------------------------------------

    def _samples_path(self):
        return os.path.join(DATA_DIR, f"mining-{self.kind}.json")

    def _load_samples(self):
        if self._samples is None:
            try:
                with open(self._samples_path()) as fh:
                    raw = json.load(fh)
                self._samples = [(float(t), float(b)) for t, b in raw.get("samples", [])] if raw.get("who") == self.who() else []
            except (OSError, ValueError, TypeError):
                self._samples = []
        return self._samples

    def _sample(self, earned):
        now = time.time()
        with self._lock:
            samples = self._load_samples()
            samples.append((now, earned))
            cutoff = now - SAMPLE_HOURS * 3600
            self._samples = [s for s in samples if s[0] >= cutoff][-3000:]
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                tmp = self._samples_path() + ".part"
                with open(tmp, "w") as fh:
                    json.dump({"who": self.who(), "samples": self._samples}, fh)
                os.replace(tmp, self._samples_path())
            except OSError:
                pass

    def per_day(self):
        """Coins a day at the rate the earnings have been growing — over the
        samples kept, drops (a withdrawal) left out. None until there is at
        least a quarter of an hour of history."""
        with self._lock:
            samples = list(self._load_samples())
        if len(samples) < 2 or samples[-1][0] - samples[0][0] < 900:
            return None
        gained, hours = 0.0, 0.0
        for (t0, b0), (t1, b1) in zip(samples, samples[1:]):
            if t1 <= t0:
                continue
            if b1 >= b0:
                gained += b1 - b0
            hours += (t1 - t0) / 3600.0
        return round(gained / hours * 24, 4) if hours > 0 else None

    # -- keeping an eye on it ------------------------------------------------------------------

    def _watch(self, data):
        line = self.line()
        total = data["hashrate"]
        low = total <= 0 or (line > 0 and total < line)
        fire = []
        name = self.title.title()
        if low:
            self._low_n += 1
            self._ok_n = 0
            if self._low_n >= LOW_READINGS and not self.alert:
                if total <= 0:
                    text = f"Nothing is mining {name} right now" + (f" — the {data['workers']} workers are all quiet" if data["workers"] else "")
                else:
                    text = (f"{name} mining has dropped to {hashrate_text(total)}, under your {hashrate_text(line)} line"
                            + (f" — {data['workers']} worker{'s' if data['workers'] != 1 else ''} going: "
                               + ", ".join(f"{m['identifier']} {hashrate_text(m['hashrate'])}" for m in data["miners"][:4]) if data["miners"] else ""))
                self.alert = {"since": time.time(), "hashrate": total, "line": line, "text": text}
                fire.append(("drop", text))
        else:
            self._ok_n += 1
            self._low_n = 0
            if self.alert and self._ok_n >= LOW_READINGS:
                mins = int((time.time() - self.alert["since"]) / 60)
                text = (f"{name} mining is back: {hashrate_text(total)} with {data['workers']} worker{'s' if data['workers'] != 1 else ''}"
                        + (f", after {mins} min under the line" if mins else ""))
                self.alert = None
                fire.append(("back", text))
        # a worker that was here and isn't any more
        current = {m["identifier"] for m in data["miners"]}
        for ident in list(self._known):
            if ident in current:
                self._seen.pop(ident, None)
                continue
            self._seen[ident] = self._seen.get(ident, 0) + 1
            if self._seen[ident] == LOW_READINGS and not low:
                fire.append(("worker", f"{name} miner {ident} has gone quiet — {data['workers']} still going at {hashrate_text(total)}"))
            if self._seen[ident] >= LOW_READINGS:
                self._known.discard(ident)
                self._seen.pop(ident, None)
        self._known |= current
        for kind, text in fire:
            log.info("%s: %s", self.kind, text)
            if self.on_alert:
                try:
                    self.on_alert(kind, text, data)
                except Exception:
                    log.exception("%s alert", self.kind)

    # -- for the screens, the page and the bot -------------------------------------------------

    def snapshot(self):
        with self._lock:
            data = dict(self.data) if self.data else None
        if data:
            data["per_day"] = self.per_day()
            data["line"] = self.line()
            data["age"] = int(time.time() - self.fetched_at) if self.fetched_at else None
            data["alert"] = dict(self.alert) if self.alert else None
        return data

    def status(self):
        return {"enabled": self.enabled(), "who": self.who(), "kind": self.kind, "symbol": self.symbol,
                "running": self.running(), "error": self.error,
                "fetched_ago": int(time.time() - self.fetched_at) if self.fetched_at else None,
                "line": self.line(), "alert": dict(self.alert) if self.alert else None, "data": self.snapshot()}

    def words(self):
        """One line for the bot's memory of the world."""
        d = self.snapshot()
        if not self.enabled():
            return ""
        name = self.title.title()
        if not d:
            return f"{name} mining ({self.who_short()}): no numbers yet" + (f" ({self.error})" if self.error else "")
        bits = [f"{self.balance_label} {d['balance']:,.{self.decimals}f} {self.symbol}"
                + (f" (about ${d['balance'] * d['price']:,.2f})" if d.get("price") else "")]
        if d.get("paid") is not None:
            bits.append(f"paid out {d['paid']:,.{self.decimals}f} {self.symbol} so far")
        if d["hashrate"] > 0:
            miners = ", ".join(f"{m['identifier']} {hashrate_text(m['hashrate'])}" for m in d["miners"][:4])
            bits.append(f"mining at {d['hashrate_text']} with {d['workers']} worker{'s' if d['workers'] != 1 else ''}" + (f" ({miners})" if miners else ""))
        else:
            bits.append("nothing mining right now")
        if d.get("per_day") is not None:
            bits.append(f"earning about {d['per_day']:.{self.decimals}f} {self.symbol} a day")
        if d.get("luck"):
            bits.append(f"luck {d['luck']}")
        if d.get("line"):
            bits.append(f"the line you set is {hashrate_text(d['line'])}")
        if d.get("alert"):
            mins = int((time.time() - d["alert"]["since"]) / 60)
            bits.append(f"UNDER THE LINE for {mins} min: {d['alert']['text']}")
        return f"{name} mining ({self.who_short()}): " + "; ".join(bits)


# -- Duino-Coin ----------------------------------------------------------------------------------

class Duco(Miner):
    kind = "duco"
    symbol = "DUCO"
    title = "DUINO-COIN"
    decimals = 3
    balance_label = "balance"

    def __init__(self):
        super().__init__()
        self._net = {}
        self._net_at = 0.0

    def who(self):
        return str(self.conf.get("username") or "").strip()

    def _read(self):
        r = requests.get(DUCO_USER_URL.format(user=self.who()), timeout=12, headers={"User-Agent": UA})
        r.raise_for_status()
        body = r.json()
        if not body.get("success", True) and body.get("message"):
            raise ValueError(str(body["message"]))
        result = body.get("result") or {}
        if not isinstance(result, dict) or not result:
            raise ValueError(f"no such user: {self.who()}" if "message" in body else "an empty answer")
        if time.time() - self._net_at > NET_EVERY:
            self._fetch_net()
        bal = result.get("balance") or {}
        miners = []
        for m in result.get("miners") or []:
            if not isinstance(m, dict):
                continue
            miners.append({"identifier": str(m.get("identifier") or m.get("threadid") or "?")[:24],
                           "hashrate": _num(m.get("hashrate")), "pool": str(m.get("pool") or ""),
                           "software": str(m.get("software") or "")[:30], "algorithm": str(m.get("algorithm") or ""),
                           "accepted": int(_num(m.get("accepted"))), "rejected": int(_num(m.get("rejected"))),
                           "sharetime": _num(m.get("sharetime"))})
        miners.sort(key=lambda m: -m["hashrate"])
        stake = _num(bal.get("stake_amount"))
        return {"balance": _num(bal.get("balance")), "paid": None, "immature": None,
                "stake_amount": stake if stake > 0 else None,        # staking was retired; shown only if there is one
                "trust_score": bal.get("trust_score"), "verified": bal.get("verified"),
                "achievements": len(result.get("achievements") or []), "miners": miners,
                "pools": sorted({m["pool"] for m in miners if m["pool"]}), "hashrate": sum(m["hashrate"] for m in miners),
                "price": self._net.get("price"), "net_hashrate": self._net.get("net_hashrate") or "",
                "net_workers": self._net.get("workers"), "luck": ""}

    def _fetch_net(self):
        try:
            r = requests.get(DUCO_NET_URL, timeout=12, headers={"User-Agent": UA})
            r.raise_for_status()
            j = r.json()
            self._net = {"price": _num(j.get("Duco price"), 0.0) or None,
                         "net_hashrate": j.get("DUCO-S1 hashrate") or j.get("Net hashrate") or "",
                         "workers": j.get("Active workers")}
        except Exception as e:
            log.info("duino-coin api.json: %s", str(e)[:100])
        self._net_at = time.time()


# -- Verus, on luckpool.net --------------------------------------------------------------------

class Verus(Miner):
    kind = "verus"
    symbol = "VRSC"
    title = "VERUS"
    decimals = 4
    balance_label = "unpaid"

    def __init__(self):
        super().__init__()
        self._price = None
        self._price_at = 0.0

    def who(self):
        return str(self.conf.get("wallet") or "").strip()

    def who_short(self):
        w = self.who()
        return w if len(w) <= 14 else f"{w[:6]}…{w[-4:]}"

    def _earned(self, data):
        return (data.get("paid") or 0.0) + data["balance"]              # unpaid resets at a payout; the sum only grows

    def _read(self):
        r = requests.get(VERUS_URL.format(wallet=self.who()), timeout=12, headers={"User-Agent": UA})
        r.raise_for_status()
        j = r.json()
        if not isinstance(j, dict) or (not j.get("address") and "hashrateSols" not in j and "workers" not in j):
            raise ValueError("luckpool doesn't know that wallet")
        miners = []
        for w in j.get("workers") or []:
            if isinstance(w, str):
                bits = w.split(":")
                name = bits[0] or "worker"
                rate = _num(bits[1]) if len(bits) > 1 else 0.0
                shares = _num(bits[2]) if len(bits) > 2 else None
                status = bits[3] if len(bits) > 3 else ""
                sharetime = _num(bits[7]) if len(bits) > 7 else None
            elif isinstance(w, dict):
                name = str(w.get("name") or w.get("worker") or "worker")
                rate = _num(w.get("hashrateSols") or w.get("hashrate")) or parse_hashrate(w.get("hashrateString"))
                shares, status, sharetime = _num(w.get("shares"), None), str(w.get("status") or ""), _num(w.get("avgShareTime"), None)
            else:
                continue
            if "." in name and name.split(".", 1)[0] == self.who():
                name = name.split(".", 1)[1]                            # "wallet.worker1" -> "worker1"
            miners.append({"identifier": name[:24], "hashrate": rate, "shares": shares, "status": status,
                           "sharetime": sharetime, "pool": "luckpool", "software": ""})
        miners.sort(key=lambda m: -m["hashrate"])
        total = _num(j.get("hashrateSols")) or parse_hashrate(j.get("hashrateString")) or sum(m["hashrate"] for m in miners)
        if time.time() - self._price_at > NET_EVERY:
            self._fetch_price()
        return {"balance": _num(j.get("balance")), "paid": _num(j.get("paid")), "immature": _num(j.get("immature")),
                "stake_amount": None, "trust_score": None, "achievements": None, "miners": miners,
                "pools": ["luckpool"], "hashrate": total, "avg_hashrate": _num(j.get("avgHashrateSols")),
                "avg_hashrate_24h": _num(j.get("avgHashrateSols24HR")), "luck": str(j.get("estimatedLuck") or "").strip(),
                "efficiency": _num(j.get("efficiency"), None), "shares": _num(j.get("shares"), None),
                "price": self._price, "net_hashrate": "", "net_workers": None}

    def _fetch_price(self):
        """The coin's price, from the CoinGecko cache the crypto screen keeps (or one fetch)."""
        try:
            from . import coingecko
            d = coingecko.coin_data("verus-coin", cached_only=True) or coingecko.coin_data("verus-coin")
            self._price = _num((d or {}).get("price"), 0.0) or None
        except Exception as e:
            log.debug("verus price: %s", e)
        self._price_at = time.time()


DUCO = Duco()
VERUS = Verus()
MINERS = {"duco": DUCO, "verus": VERUS}


def enabled():
    """The pools that are on, in the rotation's order."""
    return [m for m in (DUCO, VERUS) if m.enabled()]


def words():
    """A line per pool, for the bot."""
    return [m.words() for m in (DUCO, VERUS) if m.enabled()]
