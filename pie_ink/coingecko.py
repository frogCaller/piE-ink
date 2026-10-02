"""CoinGecko access with caching, shared by the crypto mode and the web API.

All network calls have timeouts and every result is cached on disk so the
display keeps working (with stale numbers) when the API is down or rate-limited.
"""
import json
import logging
import math
import os
import threading
import time

import requests

from . import cache
from .settings import ASSETS_DIR, DATA_DIR

log = logging.getLogger(__name__)

API = "https://api.coingecko.com/api/v3"
COINS_PATH = os.path.join(DATA_DIR, "coins.json")

COIN_TTL = 300          # seconds before re-fetching a coin's details
HISTORY_TTL = 900
LIST_TTL = 6 * 3600

DEFAULT_COINS = [
    {"id": "bitcoin", "name": "Bitcoin", "display": "BTC", "format": 0, "show": False},
    {"id": "ethereum", "name": "Ethereum", "display": "ETH", "format": 2, "show": False},
    {"id": "dogecoin", "name": "Dogecoin", "display": "DOGE", "format": 3, "show": False},
    {"id": "litecoin", "name": "Litecoin", "display": "LTC", "format": 2, "show": False},
    {"id": "verus-coin", "name": "Verus Coin", "display": "VRSC", "format": 2, "show": False},
]

_lock = threading.Lock()
_list_refreshing = False
_net_lock = threading.Lock()
_last_call = 0.0
MIN_GAP = 2.0           # seconds between CoinGecko calls (free tier is ~10/min)


# -- helpers ------------------------------------------------------------------

def price_format(price):
    """Number of decimals to show for a price."""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return 2
    if price >= 10000:
        return 0
    if price >= 1:
        return 2
    if price > 0:
        return -math.floor(math.log10(price)) + 2
    return 6


def _get(url, params=None):
    global _last_call
    with _net_lock:
        wait = MIN_GAP - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        r = requests.get(url, params=params, timeout=10,
                         headers={"User-Agent": "pie-ink/2.0"})
        _last_call = time.time()
    r.raise_for_status()
    return r.json()


# -- coin list (what the UI toggles) ----------------------------------------

def load_coins():
    with _lock:
        if os.path.exists(COINS_PATH):
            try:
                with open(COINS_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return [dict(c) for c in DEFAULT_COINS]


def save_coins(coins):
    with _lock:
        try:
            with open(COINS_PATH, "w") as f:
                json.dump(coins, f, indent=1)
        except OSError as e:
            log.warning("could not save coin list: %s", e)


def set_coin_visible(coin_id, show):
    coins = load_coins()
    for c in coins:
        if c["id"] == coin_id:
            c["show"] = bool(show)
            break
    save_coins(coins)
    return coins


def refresh_coin_list(pages=2, force=False):
    """Pull the top-500 market list and merge it with saved toggles."""
    global _list_refreshing
    if _list_refreshing:
        return
    _, age = cache.read("coin_list_ts")
    if not force and age is not None and age < LIST_TTL:
        return
    if not force and cache.backing_off("coin_list"):
        return
    _list_refreshing = True
    try:
        fetched = []
        for page in range(1, pages + 1):
            fetched += _get(f"{API}/coins/markets", {
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": 250, "page": page,
            })
        existing = {c["id"]: c for c in load_coins()}
        merged = []
        seen = set()
        for c in fetched:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            merged.append({
                "id": c["id"],
                "name": c["name"],
                "display": (c.get("symbol") or "").upper(),
                "rank": c.get("market_cap_rank"),
                "price": c.get("current_price"),
                "format": price_format(c.get("current_price")),
                "show": existing.get(c["id"], {}).get("show", False),
            })
        # keep favourites that fell out of the top list
        for cid, c in existing.items():
            if cid not in seen and c.get("show"):
                merged.append(c)
        merged.sort(key=lambda c: c.get("rank") or 10**9)
        save_coins(merged)
        cache.write("coin_list_ts", True)
        cache.clear("coin_list__fail")
        log.info("coin list refreshed: %d coins", len(merged))
    except Exception as e:
        cache.write("coin_list__fail", {"n": 1, "wait": 1800})
        log.warning("coin list refresh failed: %s (next try in 30 min)", e)
    finally:
        _list_refreshing = False


def refresh_coin_list_async(force=False):
    threading.Thread(target=refresh_coin_list, kwargs={"force": force}, daemon=True).start()


# -- per-coin data ------------------------------------------------------------

def search(query, limit=8):
    """Search the local coin catalogue by name or symbol."""
    q = query.lower().strip()
    if not q:
        return []
    hits = [c for c in load_coins()
            if q in c["name"].lower() or q == c["display"].lower() or c["display"].lower().startswith(q)]
    hits.sort(key=lambda c: (not c["display"].lower().startswith(q), c.get("rank") or 10**9))
    return hits[:limit]


_seed = None


def seed(coin_id):
    """Bundled last-known figures so the screen is never empty; replaced by
    the first successful fetch. Returned dicts carry seed=True and a date."""
    global _seed
    if _seed is None:
        try:
            with open(os.path.join(ASSETS_DIR, "seed_prices.json")) as f:
                _seed = json.load(f)
        except (OSError, json.JSONDecodeError):
            _seed = {"as_of": "", "coins": {}}
    d = _seed["coins"].get(coin_id)
    if not d:
        return None
    out = {**d, "seed": True, "as_of": _seed.get("as_of", "")}
    if out.get("from_ath") is None and out.get("price") and out.get("ath"):
        out["from_ath"] = (out["price"] - out["ath"]) / out["ath"] * 100
    return out


def coin_age(coin_id):
    """Seconds since the coin's data was fetched, or None if never."""
    _, age = cache.read(f"coin_{coin_id}")
    return age


def coin_data(coin_id, cached_only=False):
    def fetch():
        d = _get(f"{API}/coins/{coin_id}", {
            "localization": "false", "tickers": "false",
            "community_data": "false", "developer_data": "false",
        })
        m = d.get("market_data", {})
        usd = lambda key: (m.get(key) or {}).get("usd")  # noqa: E731
        price, ath = usd("current_price"), usd("ath")
        return {
            "price": price,
            "ath": ath,
            "ath_date": (usd("ath_date") or "")[:10],
            "from_ath": ((price - ath) / ath * 100) if price and ath else None,
            "market_cap": usd("market_cap"),
            "rank": d.get("market_cap_rank"),
            "total_supply": m.get("total_supply"),
            "circulating_supply": m.get("circulating_supply"),
            "sentiment_up": d.get("sentiment_votes_up_percentage"),
            "high_24h": usd("high_24h"),
            "low_24h": usd("low_24h"),
            "change": {
                "24h": m.get("price_change_percentage_24h"),
                "7d": m.get("price_change_percentage_7d"),
                "30d": m.get("price_change_percentage_30d"),
                "200d": m.get("price_change_percentage_200d"),
                "1y": m.get("price_change_percentage_1y"),
            },
        }
    return cache.cached(f"coin_{coin_id}", COIN_TTL, fetch, cached_only) or seed(coin_id)


def price_history(coin_id, days=7, cached_only=False):
    def fetch():
        d = _get(f"{API}/coins/{coin_id}/market_chart",
                 {"vs_currency": "usd", "days": days})
        return [p[1] for p in d.get("prices", [])]
    return cache.cached(f"hist_{coin_id}_{days}", HISTORY_TTL, fetch, cached_only)
