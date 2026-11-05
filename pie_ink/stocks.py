"""Stocks, beside the coins: a share's price, today's move, its ranges and a
chart, from Yahoo Finance's chart service (no key, delayed a little).

The same shape as pie_ink/coingecko.py gives for a coin, so the Finance
screen, the LCD, the holdings total and the bot treat a share and a coin
alike; what a share has that a coin doesn't (the previous close, the day's
volume, the exchange, the 52-week range) rides along for the screens that
show it. Three fetches a share — today, the last month by the hour, the last
year by the day — each kept in the cache with its time, like a coin's.
"""
import logging
import re
import threading
import time
import urllib.parse

import requests

from . import cache

log = logging.getLogger(__name__)

YAHOO = "https://query1.finance.yahoo.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PiE-ink/3.0 (+https://github.com/frogCaller/piE-ink)",
      "Accept": "application/json"}
QUOTE_TTL = 300            # seconds before a share's price is asked for again
HOURLY_TTL = 900           # ...the last month by the hour (the chart)
DAILY_TTL = 3600           # ...the last year by the day (the longer changes)
MIN_GAP = 0.5              # seconds between calls
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,11}$")
KINDS = {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}      # what a search may offer (coins come from CoinGecko)
MONEY = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "C$", "AUD": "A$", "INR": "₹", "CHF": "CHF ",
         "HKD": "HK$", "KRW": "₩", "CNY": "¥", "SEK": "kr", "NOK": "kr", "DKK": "kr"}
EXCHANGES = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "NYSE AMERICAN", "PCX": "NYSE ARCA",
             "BTS": "BATS", "TOR": "TSX", "LSE": "LSE", "GER": "XETRA", "FRA": "FRANKFURT", "PAR": "PARIS", "AMS": "AMSTERDAM",
             "HKG": "HKEX", "JPX": "TOKYO", "ASX": "ASX", "NSI": "NSE", "BSE": "BSE", "SAO": "B3"}
PENCE = {"GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS"}            # Yahoo quotes some markets in hundredths: shown in the unit
PRICE_KEYS = ("regularMarketPrice", "previousClose", "chartPreviousClose", "regularMarketDayHigh", "regularMarketDayLow",
              "fiftyTwoWeekHigh", "fiftyTwoWeekLow")

_net_lock = threading.Lock()
_last_call = 0.0


def clean_symbol(text):
    """A ticker the way Yahoo writes it, or None: AAPL, BRK-B, ^GSPC."""
    s = (text or "").strip().upper().replace(" ", "")
    return s if SYMBOL.match(s) else None


def _get(url, params=None):
    global _last_call
    with _net_lock:
        wait = MIN_GAP - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        r = requests.get(url, params=params, timeout=10, headers=UA)
        _last_call = time.time()
    if r.status_code == 404:                                  # Yahoo's answer for a ticker it doesn't know
        try:
            why = ((r.json().get("chart") or {}).get("error") or {}).get("description")
        except ValueError:
            why = None
        raise ValueError(why or "no such symbol")
    r.raise_for_status()
    return r.json()


def _chart(symbol, rng, interval):
    """One call to the chart service: (meta, timestamps, closes) with the
    gaps (a bar with no trade) left out."""
    d = _get(f"{YAHOO}/v8/finance/chart/{urllib.parse.quote(symbol)}",
             {"range": rng, "interval": interval, "includePrePost": "false", "events": ""})
    chart = d.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        err = (chart.get("error") or {}).get("description") or "no such symbol"
        raise ValueError(f"{symbol}: {err}")
    r = result[0]
    meta = dict(r.get("meta") or {})
    stamps = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = q.get("close") or []
    pairs = [(t, c) for t, c in zip(stamps, closes) if c is not None]
    stamps, closes = [t for t, _ in pairs], [float(c) for _, c in pairs]
    if meta.get("currency") in PENCE:                         # 7,250 pence is £72.50
        meta["currency"] = PENCE[meta["currency"]]
        for k in PRICE_KEYS:
            if meta.get(k) is not None:
                meta[k] = float(meta[k]) / 100
        closes = [c / 100 for c in closes]
    return meta, stamps, closes


def tidy_name(name, symbol):
    """Yahoo's long name, shortened the way the papers do: Vodafone Group PLC."""
    name = (name or symbol or "").strip()
    for long, short in (("Public Limited Company", "PLC"), ("Incorporated", "Inc."), ("Corporation", "Corp."),
                        ("Limited", "Ltd."), ("Aktiengesellschaft", "AG"), ("Société Anonyme", "SA")):
        name = name.replace(long, short)
    return name or symbol


def money_sign(currency):
    return MONEY.get(currency or "USD", f"{currency} " if currency else "$")


def exchange_name(meta):
    code = meta.get("exchangeName") or ""
    return EXCHANGES.get(code) or (meta.get("fullExchangeName") or code or "").upper()[:14]


def stock_age(symbol):
    """Seconds since the share's price was fetched, or None if never."""
    _, age = cache.read(f"stock_{symbol}")
    return age


def _today(symbol):
    """One look at a share, from Yahoo, now."""
    meta, stamps, closes = _chart(symbol, "1d", "5m")
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    if price is None:
        raise ValueError(f"{symbol}: no price")
    price = float(price)
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    prev = float(prev) if prev else None
    hi52, lo52 = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")
    day_hi, day_lo = meta.get("regularMarketDayHigh"), meta.get("regularMarketDayLow")
    if (day_hi is None or day_lo is None) and closes:
        day_hi, day_lo = max(closes), min(closes)
    return {
        "price": price,
        "prev_close": prev,
        "high_24h": float(day_hi) if day_hi is not None else None,
        "low_24h": float(day_lo) if day_lo is not None else None,
        "volume": meta.get("regularMarketVolume"),
        "currency": meta.get("currency") or "USD",
        "exchange": exchange_name(meta),
        "name": tidy_name(meta.get("longName") or meta.get("shortName"), symbol),
        "ath": float(hi52) if hi52 is not None else None,          # the 52-week high stands in for a coin's all-time high
        "low_52w": float(lo52) if lo52 is not None else None,
        "ath_date": "",
        "from_ath": ((price - float(hi52)) / float(hi52) * 100) if hi52 else None,
        "market_time": meta.get("regularMarketTime"),
        "today": closes[-200:],                                     # today's bars, five minutes apart
        "change": {"24h": ((price - prev) / prev * 100) if prev else None},
    }


def stock_data(symbol, cached_only=False):
    """Today: the price, the previous close and today's move, the day's range and
    volume, the 52-week range, the name and the exchange — and today's prices,
    for a one-day chart. Keyed and shaped like a coin's figures where they
    mean the same thing (price, high_24h, low_24h, ath, change.24h). From the
    cache while that's fresh; None when nothing is known and nothing could be asked."""
    return cache.cached(f"stock_{symbol}", QUOTE_TTL, lambda: _today(symbol), cached_only)


def lookup(symbol):
    """Ask about a share now — for adding one to the watchlist: no cache, no
    backoff, and the answer is kept. ValueError when there's no such share,
    anything else when Yahoo couldn't be asked."""
    d = _today(symbol)
    cache.write(f"stock_{symbol}", d)
    cache.clear(f"stock_{symbol}__fail")
    return d


def _series(symbol, rng, interval, ttl, cached_only=False):
    """[(timestamp, close), ...] over a range, from the cache when it's fresh."""
    def fetch():
        _, stamps, closes = _chart(symbol, rng, interval)
        return [[t, c] for t, c in zip(stamps, closes)]
    return cache.cached(f"stockhist_{symbol}_{rng}_{interval}", ttl, fetch, cached_only) or []


def _range_for(days):
    """Yahoo's range and interval a chart of so many days comes out of, and how long it's kept."""
    if days <= 30:
        return "1mo", "1h", HOURLY_TTL
    if days <= 90:
        return "3mo", "1d", DAILY_TTL
    if days <= 365:
        return "1y", "1d", DAILY_TTL
    return "5y", "1wk", DAILY_TTL


def history_age(symbol, days):
    """Seconds since the prices behind a chart of so many days were fetched, or None if they weren't."""
    days = max(1, int(days))
    if days <= 1:
        return stock_age(symbol)
    rng, interval, _ttl = _range_for(days)
    _, age = cache.read(f"stockhist_{symbol}_{rng}_{interval}")
    return age


def price_history(symbol, days=7, cached_only=False):
    """Closing prices over the last `days` days: by the hour up to a month,
    by the day up to a year, by the week beyond; today's bars for one day."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 7
    if days <= 1:
        d = stock_data(symbol, cached_only)
        return list((d or {}).get("today") or [])
    rng, interval, ttl = _range_for(days)
    since = time.time() - days * 86400
    pts = [c for t, c in _series(symbol, rng, interval, ttl, cached_only) if t >= since]
    return pts


def changes(symbol, cached_only=False):
    """How the price has moved: {"7d", "30d", "200d", "1y"} in percent (the
    keys a coin uses: a week, a month, about six months, a year), from the
    last year by the day. Missing where the series doesn't reach."""
    daily = _series(symbol, "1y", "1d", DAILY_TTL, cached_only)
    out = {"7d": None, "30d": None, "200d": None, "1y": None}
    if len(daily) < 2:
        return out
    last = daily[-1][1]
    now = time.time()
    for key, back in (("7d", 7), ("30d", 30), ("200d", 182), ("1y", 365)):
        then = now - back * 86400
        earlier = [c for t, c in daily if t <= then]
        if not earlier and daily[0][0] - then < max(4 * 86400, back * 86400 * 0.05):
            earlier = [daily[0][1]]                       # the series starts a day or two short of the mark: near enough
        if earlier and earlier[-1]:
            out[key] = (last - earlier[-1]) / earlier[-1] * 100
    return out


def is_open(d, now=None):
    """Whether the market was trading at the last look: the last trade within
    the last half hour. None when the figures don't say."""
    t = (d or {}).get("market_time")
    if not t:
        return None
    return (now or time.time()) - float(t) < 1800


def quote(item, graph_days=7, cached_only=False):
    """A share's quote in the watchlist's shape (see watchlist.quote), or
    None when nothing is known of it yet."""
    symbol = item["id"]
    d = stock_data(symbol, cached_only)
    if not d:
        return None
    price = d.get("price")
    ch = dict(d.get("change") or {})
    for k, v in changes(symbol, cached_only).items():
        ch.setdefault(k, v)
    return {
        "kind": "stock",
        "seed": False,
        "as_of": "",
        "age": stock_age(symbol),
        "symbol": item.get("symbol") or symbol,
        "name": d.get("name") or item.get("name") or symbol,
        "price": price,
        "decimals": 2 if price is None or price >= 1 else 4,
        "money": money_sign(d.get("currency")),
        "currency": d.get("currency") or "USD",
        "exchange": d.get("exchange") or "",
        "open": is_open(d),
        "prev_close": d.get("prev_close"),
        "volume": d.get("volume"),
        "day_high": d.get("high_24h"),
        "day_low": d.get("low_24h"),
        "ath": d.get("ath"),
        "ath_date": "",
        "from_ath": d.get("from_ath"),
        "low_52w": d.get("low_52w"),
        "market_cap": None,
        "rank": None,
        "total_supply": None,
        "circulating_supply": None,
        "sentiment_up": None,
        "change": ch,
        "graph": price_history(symbol, graph_days, cached_only) or [],
    }


_searches = {}             # what was typed -> (when, the answer): kept a while, in memory only
SEARCH_TTL = 6 * 3600


def search(query, limit=8):
    """Shares and funds whose name or ticker fits what was typed, from Yahoo's
    lookup: [{"id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "kind": "stock"}].
    Raises when Yahoo can't be asked (the page says so)."""
    q = (query or "").strip()
    if not q:
        return []
    key = q.lower()
    at, found = _searches.get(key, (0.0, None))
    if found is not None and time.time() - at < SEARCH_TTL:
        return found[:limit]
    d = _get(f"{YAHOO}/v1/finance/search", {"q": q, "quotesCount": limit * 2, "newsCount": 0, "listsCount": 0,
                                             "enableFuzzyQuery": "false"})
    out = []
    for x in d.get("quotes") or []:
        if (x.get("quoteType") or "").upper() not in KINDS:
            continue
        sym = clean_symbol(x.get("symbol"))
        if not sym:
            continue
        out.append({"id": sym, "symbol": sym, "name": tidy_name(x.get("longname") or x.get("shortname"), sym),
                    "exchange": (x.get("exchDisp") or x.get("exchange") or "").strip(), "kind": "stock"})
    if len(_searches) > 200:
        for old in sorted(_searches, key=lambda k: _searches[k][0])[:100]:
            del _searches[old]
    _searches[key] = (time.time(), out)
    return out[:limit]
