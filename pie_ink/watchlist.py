"""The watchlist: which coins rotate on the display."""
import json
import logging
import os
import threading

from . import coingecko
from .settings import DATA_DIR

log = logging.getLogger(__name__)
PATH = os.path.join(DATA_DIR, "watchlist.json")
_lock = threading.Lock()

DEFAULT = [
    {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "show": True},
    {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "show": True},
    {"id": "dogecoin", "symbol": "DOGE", "name": "Dogecoin", "show": True},
    {"id": "litecoin", "symbol": "LTC", "name": "Litecoin", "show": False},
    {"id": "verus-coin", "symbol": "VRSC", "name": "Verus Coin", "show": False},
]


def load():
    with _lock:
        if os.path.exists(PATH):
            try:
                with open(PATH) as f:
                    items = json.load(f)
                # older files mixed in stocks; keep only coins
                return [i for i in items if i.get("kind", "crypto") == "crypto"]
            except (json.JSONDecodeError, OSError):
                pass
    items = [dict(i) for i in DEFAULT]
    for c in coingecko.load_coins():        # coins ticked in the old catalogue
        if c.get("show") and not any(i["id"] == c["id"] for i in items):
            items.append({"id": c["id"], "symbol": c["display"], "name": c["name"], "show": True})
    save(items)
    return items


def save(items):
    with _lock:
        try:
            with open(PATH, "w") as f:
                json.dump(items, f, indent=1)
        except OSError as e:
            log.warning("could not save watchlist: %s", e)


def visible():
    items = [i for i in load() if i.get("show")]
    return items or [dict(DEFAULT[0])]


def add(coin_id):
    items = load()
    if any(i["id"] == coin_id for i in items):
        return items
    coin = next((c for c in coingecko.load_coins() if c["id"] == coin_id), None)
    if not coin:
        raise ValueError("unknown coin")
    items.append({"id": coin["id"], "symbol": coin["display"], "name": coin["name"], "show": True})
    save(items)
    return items


def remove(coin_id):
    items = [i for i in load() if i["id"] != coin_id]
    save(items)
    return items


def set_visible(coin_id, show):
    items = load()
    for i in items:
        if i["id"] == coin_id:
            i["show"] = bool(show)
    save(items)
    return items


def set_amount(coin_id, amount):
    """How much of a coin you hold (0 clears it) — what the holdings total is made of."""
    try:
        amount = max(0.0, float(amount or 0))
    except (TypeError, ValueError):
        raise ValueError("amount?")
    items = load()
    for i in items:
        if i["id"] == coin_id:
            if amount > 0:
                i["amount"] = amount
            else:
                i.pop("amount", None)
    save(items)
    return items


def quote(item, graph_days=7, cached_only=False):
    """Normalised quote for a watchlist item, or None if nothing is known yet."""
    d = coingecko.coin_data(item["id"], cached_only)
    if not d:
        return None
    hist = coingecko.price_history(item["id"], graph_days, cached_only) or []
    price = d.get("price")
    return {
        "seed": bool(d.get("seed")),
        "as_of": d.get("as_of", ""),
        "age": coingecko.coin_age(item["id"]),
        "symbol": item["symbol"],
        "name": item.get("name", item["symbol"]),
        "price": price,
        "decimals": coingecko.price_format(price),
        "day_high": d.get("high_24h"),
        "day_low": d.get("low_24h"),
        "ath": d.get("ath"),
        "ath_date": d.get("ath_date"),
        "from_ath": d.get("from_ath"),
        "market_cap": d.get("market_cap"),
        "rank": d.get("rank"),
        "total_supply": d.get("total_supply"),
        "circulating_supply": d.get("circulating_supply"),
        "sentiment_up": d.get("sentiment_up"),
        "change": d.get("change", {}),
        "graph": hist,
    }
