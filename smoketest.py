#!/usr/bin/env python3
"""Boot the app with the mock panel and poke every route.

This is the net for the boring kind of mistake: an endpoint deleted by
accident, a handler that throws on an empty config, a settings section that
stopped saving. It touches the real routes with the real service running, so
anything that has gone missing shows up as a failure rather than as silence
weeks later.

    python3 smoketest.py            # prints one line per route
"""
import os
import sys
import tempfile

os.environ.setdefault("PIE_INK_DRIVER", "mock")

FAILED = []


def check(name, got, want=(200,), body_has=None):
    ok = got.status_code in want
    detail = ""
    if ok and body_has:
        try:
            data = got.get_json() or {}
        except Exception:
            data = {}
        missing = [k for k in body_has if k not in data]
        if missing:
            ok, detail = False, f"missing {missing}"
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{' — ' + detail if detail else ''}"
          f"{'' if ok else f' (status {got.status_code})'}")
    if not ok:
        FAILED.append(name)


class _FakeHome:
    """Enough of Home Assistant's REST API to be spoken to: a TV with a
    remote, two lights, a scene. Records every service call."""

    STATES = [
        {"entity_id": "media_player.living_room_tv", "state": "on",
         "attributes": {"friendly_name": "Living Room TV", "source_list": ["TV", "HDMI 1", "Netflix", "YouTube"]}},
        {"entity_id": "remote.living_room_tv", "state": "on", "attributes": {"friendly_name": "Living Room TV Remote"}},
        {"entity_id": "light.kitchen_light", "state": "off", "attributes": {"friendly_name": "Kitchen Light"}},
        {"entity_id": "light.lamp", "state": "on", "attributes": {"friendly_name": "Lamp"}},
        {"entity_id": "scene.movie_night", "state": "unknown", "attributes": {"friendly_name": "Movie Night"}},
        {"entity_id": "sensor.outside", "state": "12", "attributes": {"friendly_name": "Outside"}},
    ]

    def __init__(self):
        self.calls = []
        self.token = "test-token"
        self.port = None
        self._server = None
        self.tv_follows = False                       # the TV's state follows turn_on / turn_off (a real one does)
        self.tv_stubborn = False                      # ... except it won't come on (it has no network while off)
        self.tv_mac = ""                              # what its device registry knows the TV's MAC as
        self.registry_extra = {}                      # more entities the registry lists: {entity_id: integration}

    def tv(self):
        return next(st for st in self.STATES if st["entity_id"] == "media_player.living_room_tv")

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {fake.token}":
                    self._send(401, {"message": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/config":
                    return self._send(200, {"version": "2026.9.1", "location_name": "Test House"})
                if self.path == "/api/states":
                    return self._send(200, fake.STATES)
                if self.path.startswith("/api/states/"):
                    eid = self.path.split("/api/states/", 1)[1]
                    for st in fake.STATES:
                        if st["entity_id"] == eid:
                            return self._send(200, st)
                    return self._send(404, {"message": "Entity not found."})
                self._send(404, {"message": "not found"})

            def do_POST(self):
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/template":                      # the registry, asked the way PiE-ink asks
                    tpl = body.get("template") or ""
                    out = ""
                    if "states.media_player" in tpl:                   # every player and remote: entity|integrations|mac
                        for st in fake.STATES:
                            eid = st["entity_id"]
                            if eid.startswith(("media_player.", "remote.")):
                                out += f"{eid}|samsungtv|{fake.tv_mac if eid == 'media_player.living_room_tv' else ''}\n"
                        for eid, via in fake.registry_extra.items():
                            out += f"{eid}|{via}|\n"
                    out = out.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    return self.wfile.write(out)
                if self.path.startswith("/api/services/"):
                    domain, service = self.path.split("/api/services/", 1)[1].split("/")
                    fake.calls.append((domain, service, body))
                    if domain == "wake_on_lan":
                        return self._send(400, {"message": "Service wake_on_lan.send_magic_packet not found."})
                    if fake.tv_follows and domain == "media_player" and body.get("entity_id") == "media_player.living_room_tv":
                        if service == "turn_off" or (service == "toggle" and fake.tv()["state"] == "on"):
                            fake.tv()["state"] = "off"
                        elif service in ("turn_on", "toggle") and not fake.tv_stubborn:
                            fake.tv()["state"] = "on"
                    return self._send(200, [])
                self._send(404, {"message": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing



class _FakeOllama:
    """Enough of Ollama to be asked: a model list, and answers — a scene as
    JSON when a picture comes with the question, a decision as JSON when the
    agent asks, a line of chat otherwise. Records every prompt."""

    def __init__(self):
        self.prompts = []
        self.scene = {"people": 1, "animals": ["cat"], "packages": 0, "vehicles": 0, "door": "closed", "lights": "on",
                      "unusual": "", "notes": "A person on the sofa with a cat."}
        self.decision = {"thought": "Someone is home with the cat; all is well.", "level": "note",
                         "actions": [{"do": "show", "text": "All quiet, cat on the sofa"}, {"do": "notify", "text": "Someone came in"}],
                         "remember": "cat was on the sofa at this time"}
        self.decision2 = {"thought": "Turned to look: it was only the cat by the window.", "level": "note",
                          "actions": [{"do": "show", "text": "Just the cat"}], "remember": ""}
        self.view_words = "A window with the cat on the sill; nothing unusual."
        self.snake_asks = []                          # every /v1/systemone question (the Snake player)
        self.snake_play = "well"                      # or "badly": straight into the nearest wall
        self.yesno = "yes"                            # what the decision model says to a yes/no question
        self.yesno_asks = []                          # the questions it was asked
        self.tags = ["fake:latest", "tev1:latest"]    # what /api/tags lists
        self.port = None
        self._server = None

    def snake_answer(self, question):
        """A decision the way tev1 gives one: a choice with probabilities, read off the facts."""
        scored = {}
        for k, text in question["criteria"].items():
            if self.snake_play == "badly":
                scored[k] = 1.0 if "deadly" in text else 0.01
            elif "deadly" in text:
                scored[k] = 0.01
            elif "dead end" in text or "bad," in text:
                scored[k] = 0.05
            elif "reaches the food" in text:
                scored[k] = 1.0
            elif "nothing between" in text:
                scored[k] = 0.9
            elif "closer to the food" in text and "still in the way" not in text:
                scored[k] = 0.5
            else:
                scored[k] = 0.2
        total = sum(scored.values()) or 1
        probs = {k: v / total for k, v in scored.items()}
        choice = max(probs, key=probs.get)
        return {"model": "tev1", "answers": {"move": {"type": "choice", "choice": choice, "probabilities": probs, "confidence": 0.8}}}

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": n} for n in fake.tags]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/v1/systemone":
                    if not body.get("model", "").startswith("tev1"):
                        return self._send(404, {"error": "model not found"})
                    if "answer" in body.get("questions", {}):           # a yes/no for the head
                        fake.yesno_asks.append(body)
                        probs = {k: (0.8 if k == fake.yesno else 0.1) for k in body["questions"]["answer"]["criteria"]}
                        return self._send(200, {"model": "tev1", "answers": {"answer": {
                            "type": "choice", "choice": fake.yesno, "probabilities": probs, "confidence": 0.7}}})
                    fake.snake_asks.append(body)
                    return self._send(200, fake.snake_answer(body["questions"]["move"]))
                fake.prompts.append(body)
                if self.path != "/api/generate":
                    return self._send(404, {"error": "not found"})
                prompt = body.get("prompt") or ""
                if body.get("images") and ("just turned the camera" in prompt or "views side by side" in prompt):
                    answer = fake.view_words
                elif body.get("images"):
                    answer = "Here you go:\n```json\n" + json.dumps(fake.scene) + "\n```"
                elif "You turned the camera to look" in prompt:
                    answer = json.dumps(fake.decision2)
                elif "Report at" in prompt:
                    answer = json.dumps(fake.decision)
                else:
                    answer = "Hello from the fake model."
                self._send(200, {"model": body.get("model"), "response": answer, "done": True})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing


class _FakeDuco:
    """server.duinocoin.com, as far as PiE-ink asks it: one user with two
    miners, and api.json's price and network rate. The hashrate can be
    changed between readings to test the alert."""

    def __init__(self):
        self.miners = [{"identifier": "ESP32-1", "hashrate": 320.5, "pool": "us-pool", "software": "ESP32 Miner 4.3",
                        "algorithm": "DUCO-S1", "accepted": 100, "rejected": 1, "sharetime": 4.2},
                       {"identifier": "PicoW", "hashrate": 130.0, "pool": "us-pool", "software": "Pico Miner",
                        "algorithm": "DUCO-S1", "accepted": 50, "rejected": 0, "sharetime": 5.1}]
        self.balance = 1234.5
        self.verus_sols = 34419715.2
        self.port = None
        self._server = None

    def start(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api.json":
                    return self._send(200, {"Duco price": 0.00041, "DUCO-S1 hashrate": "1.23 GH/s", "Active workers": 12345})
                if self.path.startswith("/verus/miner/"):          # luckpool.net, as it really answers
                    wallet = self.path.rsplit("/", 1)[1]
                    if wallet != "RTESTWALLET":
                        return self._send(200, {})
                    return self._send(200, {"timestamp": 1700000000, "address": wallet, "hashrateSols": fake.verus_sols,
                                            "hashrateString": f"{fake.verus_sols / 1e6:.2f} MH", "avgHashrateSols": fake.verus_sols,
                                            "avgHashrateSols24HR": fake.verus_sols * 0.9, "currentSharePercent": 0.01,
                                            "estimatedLuck": "87.5%", "shares": 1234.5, "efficiency": 99.1, "immature": 0.0123,
                                            "balance": 0.4567, "paid": 12.3456,
                                            "workers": [f"{wallet}.ryzen:{fake.verus_sols * 0.7}:800:on:na:false:1:22.4",
                                                        f"pi5:{fake.verus_sols * 0.3}:434.5:on:na:false:1:30.1"] if fake.verus_sols else []})
                if self.path.startswith("/v3/users/"):
                    user = self.path.rsplit("/", 1)[1]
                    if user != "tester":
                        return self._send(200, {"success": False, "message": "This user doesn't exist"})
                    return self._send(200, {"success": True, "result": {
                        "balance": {"balance": fake.balance, "stake_amount": 100.0, "stake_date": 1700000000, "trust_score": 100, "verified": "yes"},
                        "miners": fake.miners, "achievements": [1, 2, 3]}})
                self._send(404, {"success": False, "message": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()           # or a later request to its port hangs instead of failing

class _FakeYahoo:
    """query1.finance.yahoo.com, as far as pie_ink/stocks.py asks it: the chart
    service for a few tickers over any range, the lookup, and a headline feed.
    AAPL trades in dollars, VOD.L in pence (which the client turns into pounds)."""

    SHARES = {
        "AAPL": {"currency": "USD", "exchangeName": "NMS", "fullExchangeName": "NasdaqGS", "longName": "Apple Inc.", "shortName": "Apple Inc.",
                 "price": 258.10, "prev": 255.00, "hi": 260.00, "lo": 254.50, "volume": 45000000, "hi52": 288.60, "lo52": 169.20},
        "TSLA": {"currency": "USD", "exchangeName": "NMS", "fullExchangeName": "NasdaqGS", "longName": "Tesla, Inc.", "shortName": "Tesla, Inc.",
                 "price": 440.20, "prev": 450.00, "hi": 452.00, "lo": 438.00, "volume": 90000000, "hi52": 488.50, "lo52": 212.10},
        "VOD.L": {"currency": "GBp", "exchangeName": "LSE", "fullExchangeName": "London", "longName": "Vodafone Group Public Limited Company",
                  "shortName": "VODAFONE GROUP PLC ORD USD0.20", "price": 7250.0, "prev": 7200.0, "hi": 7300.0, "lo": 7150.0,
                  "volume": 30000000, "hi52": 8000.0, "lo52": 6000.0},
    }
    FOUND = {"aapl": ["AAPL"], "apple": ["AAPL"], "tesla": ["TSLA"], "tsla": ["TSLA"], "vod": ["VOD.L"], "vodafone": ["VOD.L"]}

    def __init__(self):
        self.port = None
        self._server = None
        self.calls = []

    def _bars(self, sym, rng, interval):
        """(timestamps, closes): a wobble around the price, oldest first, the last one the price."""
        import math
        import time
        share = self.SHARES[sym]
        step = {"5m": 300, "1h": 3600, "1d": 86400, "1wk": 7 * 86400}[interval]
        span = {"1d": 78 * 300, "1mo": 30 * 86400, "3mo": 90 * 86400, "1y": 365 * 86400, "5y": 5 * 365 * 86400}[rng]
        n = span // step
        now = int(time.time()) - 600
        stamps = [now - (n - 1 - i) * step for i in range(n)]
        closes = [share["price"] * (1 + 0.04 * math.sin(i / 7.0) - 0.08 * (n - 1 - i) / n) for i in range(n)]
        closes[-1] = share["price"]
        return stamps, closes

    def start(self):
        import json
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data, kind="application/json"):
                body = json.dumps(data).encode() if kind == "application/json" else data.encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.calls.append(u.path)
                if u.path.startswith("/v8/finance/chart/"):
                    sym = u.path.rsplit("/", 1)[1].upper()
                    share = fake.SHARES.get(sym)
                    if not share:
                        return self._send(404, {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
                    stamps, closes = fake._bars(sym, q.get("range", "1d"), q.get("interval", "5m"))
                    meta = {"currency": share["currency"], "symbol": sym, "exchangeName": share["exchangeName"],
                            "fullExchangeName": share["fullExchangeName"], "longName": share["longName"], "shortName": share["shortName"],
                            "regularMarketPrice": share["price"], "previousClose": share["prev"], "chartPreviousClose": share["prev"],
                            "regularMarketDayHigh": share["hi"], "regularMarketDayLow": share["lo"], "regularMarketVolume": share["volume"],
                            "fiftyTwoWeekHigh": share["hi52"], "fiftyTwoWeekLow": share["lo52"], "regularMarketTime": int(time.time()) - 600}
                    closes[len(closes) // 2] = None                   # a bar with no trade, as Yahoo sends
                    return self._send(200, {"chart": {"result": [{"meta": meta, "timestamp": stamps,
                                                                  "indicators": {"quote": [{"close": closes}]}}], "error": None}})
                if u.path == "/v1/finance/search":
                    hits = fake.FOUND.get((q.get("q") or "").lower(), [])
                    quotes = [{"symbol": sym, "shortname": fake.SHARES[sym]["shortName"], "longname": fake.SHARES[sym]["longName"],
                               "quoteType": "EQUITY", "exchDisp": "NASDAQ" if sym != "VOD.L" else "London", "exchange": fake.SHARES[sym]["exchangeName"]}
                              for sym in hits]
                    if hits:
                        quotes.append({"symbol": hits[0] + "251219C00100000", "shortname": "an option", "quoteType": "OPTION", "exchDisp": "OPR"})
                    return self._send(200, {"quotes": quotes, "news": []})
                if u.path == "/rss/2.0/headline":
                    sym = q.get("s", "")
                    xml = ("<?xml version='1.0'?><rss version='2.0'><channel><title>Yahoo! Finance</title>"
                           f"<item><title>{sym} rallies after results</title></item><item><title>Analysts weigh {sym}</title></item></channel></rss>")
                    return self._send(200, xml, "application/xml")
                self._send(404, {"error": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def stocks_check(pieapp, c):
    """Shares beside the coins, against the stand-in Yahoo: adding one (and not
    a made-up ticker), its quote in the watchlist's shape, the graph windows,
    the search that mixes coins and shares, the e-ink and LCD pages, a share
    among the holdings (and one in pounds, kept out of the dollar total), the
    bot's context, Telegram, /api/shared, and the share's own headlines."""
    import time as _t
    from PIL import Image as _Image
    from pie_ink import cache as _cache
    from pie_ink import holdings as _hold
    from pie_ink import lcd_screens as _ls
    from pie_ink import stocks as _st
    from pie_ink import watchlist as _wl
    from pie_ink.modes import crypto as _cm
    print("finance: shares beside the coins (a stand-in Yahoo)")
    fake = _FakeYahoo()
    fake.start()
    real = _st.YAHOO, _cm.STOCK_NEWS
    _st.YAHOO = f"http://127.0.0.1:{fake.port}"
    _cm.STOCK_NEWS = f"http://127.0.0.1:{fake.port}/rss/2.0/headline?s={{symbol}}"
    added, paused = [], []
    before = [i for i in _wl.load() if i["id"] in ("AAPL", "TSLA", "VOD.L")]     # a fresh list starts with Apple and Tesla
    for i in before:
        c.delete(f"/api/watchlist/{i['id']}")

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)
    try:
        for sym in ("AAPL", "TSLA", "VOD.L", "NOPE"):
            for key in (f"stock_{sym}", f"stock_{sym}__fail"):
                _cache.clear(key)
        r = c.post("/api/watchlist", json={"id": "nope", "kind": "stock"})
        check("POST /api/watchlist NOPE (no such share)", r, want=(400,))
        expect("a made-up ticker is refused with Yahoo's reason", "no share called NOPE" in ((r.get_json() or {}).get("error") or ""), r.get_json())
        check("POST /api/watchlist (not a ticker)", c.post("/api/watchlist", json={"id": "not a ticker!", "kind": "stock"}), want=(400,))
        r = c.post("/api/watchlist", json={"id": "aapl", "kind": "stock"})
        check("POST /api/watchlist aapl (a share)", r, body_has=["items"])
        items = (r.get_json() or {}).get("items", [])
        row = next((i for i in items if i["id"] == "AAPL"), {})
        if row:
            added.append("AAPL")
        expect("AAPL on the list, as a share, with Yahoo's name, price, exchange and today's move",
               row.get("kind") == "stock" and row.get("name") == "Apple Inc." and row.get("price") == 258.10 and row.get("money") == "$"
               and row.get("exchange") == "NASDAQ" and row.get("change_1d") is not None and abs(row["change_1d"] - 1.2157) < 0.01, row)
        r = c.post("/api/watchlist", json={"id": "AAPL", "kind": "stock"})
        expect("adding it again changes nothing", len((r.get_json() or {}).get("items", [])) == len(items))
        item = {"id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "kind": "stock", "show": True}
        q = _wl.quote(item, 7)
        expect("the quote has the watchlist's shape, and a share's extras",
               q and q["kind"] == "stock" and q["price"] == 258.10 and q["decimals"] == 2 and q["money"] == "$" and q["exchange"] == "NASDAQ"
               and q["open"] is True and q["prev_close"] == 255.0 and q["volume"] == 45000000 and q["day_high"] == 260.0 and q["day_low"] == 254.5
               and q["ath"] == 288.6 and q["low_52w"] == 169.2 and abs(q["from_ath"] + 10.57) < 0.01 and q["rank"] is None
               and q["sentiment_up"] is None and q["market_cap"] is None, {k: q.get(k) for k in ("kind", "price", "exchange", "open", "ath")} if q else q)
        ch = (q or {}).get("change") or {}
        expect("today's, the week's, the month's, six months' and the year's moves",
               all(ch.get(k) is not None for k in ("24h", "7d", "30d", "200d", "1y")) and abs(ch["24h"] - 1.2157) < 0.01, ch)
        g7 = (q or {}).get("graph") or []
        expect("a week's graph from the hourly series (about 7 × 24 bars, the empty one left out)", 150 <= len(g7) <= 170, len(g7))
        g1 = _wl.quote(item, 1)["graph"]
        expect("one day's graph is today's five-minute bars", 70 <= len(g1) <= 78 and g1[-1] == 258.10, len(g1))
        g60 = _wl.quote(item, 60)["graph"]
        expect("two months' graph is daily", 55 <= len(g60) <= 61, len(g60))
        g2y = _wl.quote(item, 730)["graph"]
        expect("two years' graph is weekly", 100 <= len(g2y) <= 106, len(g2y))
        expect("each window is one fetch, kept (today, a month by the hour, a year by the day…)",
               fake.calls.count("/v8/finance/chart/AAPL") <= 6, fake.calls.count("/v8/finance/chart/AAPL"))
        # the search mixes coins and shares; a ticker in capitals puts the shares first
        r = c.get("/api/coins/search?q=AAPL")
        check("GET /api/coins/search?q=AAPL", r, body_has=["results"])
        hits = (r.get_json() or {}).get("results", [])
        expect("AAPL finds the share first, with its exchange, and not the option",
               hits and hits[0]["id"] == "AAPL" and hits[0]["kind"] == "stock" and hits[0]["exchange"] == "NASDAQ"
               and not any("251219" in h["id"] for h in hits), hits[:2])
        hits = (c.get("/api/coins/search?q=tesla").get_json() or {}).get("results", [])
        expect("tesla finds TSLA among the results", any(h["id"] == "TSLA" and h["kind"] == "stock" for h in hits), hits[:3])
        hits = (c.get("/api/coins/search?q=bitcoin").get_json() or {}).get("results", [])
        expect("bitcoin finds the coin first", hits and hits[0]["id"] == "bitcoin" and hits[0]["kind"] == "crypto", hits[:2])
        # the e-ink page, on the small panel and a big one, through every bottom panel and the stats
        for size in ({"width": 250, "height": 122, "kind": "eink"}, {"width": 400, "height": 300, "kind": "eink"}):
            probe = _cm.CryptoMode(pieapp.service.config, size)
            probe._stock_news["AAPL"] = (_t.time(), ["AAPL RALLIES AFTER RESULTS"])
            real_time = _cm.time
            try:
                for step in range(5):
                    t = step * _cm.BOTTOM_SECONDS * 1.0 + 1 + 1000 * _cm.BOTTOM_SECONDS * 4 * 5     # lands on each bottom panel in turn
                    _cm.time = type("T", (), {"time": staticmethod(lambda t=t: t)})
                    img = probe._render_large(probe.frame(), item, q, t, False)
                    if not isinstance(img, _Image.Image):
                        raise RuntimeError("no image")
                    probe._pick_face(q, False)
            except Exception as e:
                print(f"  FAIL the share's e-ink page at {size['width']}x{size['height']}: {e!r}")
                FAILED.append("share e-ink page")
            finally:
                _cm.time = real_time
        print("  ok   the share's page draws on the e-ink at two sizes, through every panel")
        sad = _wl.quote({**item, "id": "AAPL"}, 7)
        sad = {**sad, "change": {**sad["change"], "24h": -5.0}}
        probe = _cm.CryptoMode(pieapp.service.config, {"width": 250, "height": 122, "kind": "eink"})
        expect("the face follows today's move (a 5 % fall: sad; a 1.2 % rise: happy)",
               probe._pick_face(sad, False) == _cm.FACES["sad"] and probe._pick_face(q, False) in (_cm.FACES["look_r_happy"], _cm.FACES["look_l_happy"]))
        # the LCD page, at the moment the rotation is on AAPL
        vis = _wl.visible()
        k = next(i for i, it in enumerate(vis) if it["id"] == "AAPL")
        per = max(10, int(pieapp.service.config.get("crypto", {}).get("coin_seconds", 90) or 90))
        for wh in ((240, 280), (170, 320), (320, 240)):
            try:
                img = _ls.crypto({"model": "1.69", "crypto_days": 7}, now=(k + 10 * len(vis)) * per + 1, size=wh, config=pieapp.service.config)
                if not isinstance(img, _Image.Image):
                    raise RuntimeError("no image")
            except Exception as e:
                print(f"  FAIL the share's LCD page at {wh}: {e!r}")
                FAILED.append("share lcd page")
        print("  ok   the share's page draws on the LCD at three sizes")
        # the share's own headlines
        probe._fetch_stock_news("AAPL")
        expect("a share's headlines come from its own feed", probe._news_for(item) == ["AAPL RALLIES AFTER RESULTS", "ANALYSTS WEIGH AAPL"], probe._news_for(item))
        expect("a coin's come from the crypto feeds", probe._news_for({"id": "bitcoin", "symbol": "BTC"}) is probe._news)
        # holdings: ten Apple shares, then a hundred Vodafone in pounds (two coins paused, so the
        # share is among the first few the bot is told about)
        paused = [i["id"] for i in _wl.visible() if i["id"] != "AAPL"][1:]
        for cid in paused:
            c.put(f"/api/watchlist/{cid}", json={"show": False})
        r = c.put("/api/watchlist/AAPL", json={"amount": 10})
        check("PUT /api/watchlist/AAPL (you hold 10)", r, body_has=["items"])
        hd = c.get("/api/holdings").get_json() or {}
        row = next((x for x in hd.get("value", {}).get("items", []) if x["id"] == "AAPL"), None)
        expect("ten shares at $258.10 are $2,581 of the total", row and row["kind"] == "stock" and abs(row["value"] - 2581.0) < 0.01
               and abs(hd["value"]["total"] - 2581.0) < 0.01 and row["money"] == "$", row)
        expect("the words say holdings, not crypto, once a share is in", _hold.whose(hd["value"]) == "Your holdings")
        now_h = (c.get("/api/now").get_json() or {}).get("holdings") or {}
        expect("the sidebar's row is 'Your holdings'", now_h.get("whose") == "Your holdings", now_h)
        tg = pieapp._tg_command("/holdings")
        expect("/holdings lists the shares", tg.startswith("Your holdings: $2,581") and "AAPL: 10 × $258.10 = $2,581" in tg, tg.splitlines()[:2])
        words = pieapp.mascot.context(pieapp.service.config)
        expect("the bot knows the share's price and the holdings",
               any(ln.startswith("AAPL (share): $258, +1.2% today") for ln in words) and any(ln.startswith("Your holdings (coins and shares): worth $2,581") for ln in words),
               [w for w in words if "AAPL" in w or "holdings" in w])
        r = c.post("/api/watchlist", json={"id": "VOD.L", "kind": "stock"})
        check("POST /api/watchlist VOD.L (a share in pence)", r, body_has=["items"])
        vod = next((i for i in (r.get_json() or {}).get("items", []) if i["id"] == "VOD.L"), {})
        if vod:
            added.append("VOD.L")
        expect("pence become pounds: £72.50 on the LSE, and the name shortened",
               vod.get("price") == 72.5 and vod.get("money") == "£" and vod.get("exchange") == "LSE" and vod.get("name") == "Vodafone Group PLC", vod)
        c.put("/api/watchlist/VOD.L", json={"amount": 100})
        hd = c.get("/api/holdings").get_json() or {}
        vrow = next((x for x in hd.get("value", {}).get("items", []) if x["id"] == "VOD.L"), None)
        expect("a hundred Vodafone are £7,250 — shown, but kept out of the dollar total",
               vrow and abs(vrow["value"] - 7250.0) < 0.01 and vrow["money"] == "£" and hd["value"]["foreign"] == ["VOD.L"]
               and abs(hd["value"]["total"] - 2581.0) < 0.01, (vrow, hd["value"].get("foreign"), hd["value"]["total"]))
        tg = pieapp._tg_command("/holdings")
        expect("/holdings says so", "VOD.L: 100 × £72.50 = £7,250.00" in tg and "not in the total" in tg, tg.splitlines())
        text = (c.post("/api/holdings/try", json={"kind": "crash"}).get_json() or {}).get("text", "")
        expect("the try-out speaks of holdings", text.startswith("The market is down: your holdings are worth $2,581"), text)
        # what this Pi shares with the others
        sh = c.get("/api/shared").get_json() or {}
        aapl = next((x for x in sh.get("stocks", []) if x["id"] == "AAPL"), None)
        expect("/api/shared lists the shares, with their figures and a graph, and no amounts",
               aapl and aapl["data"]["price"] == 258.10 and "today" not in aapl["data"] and len(aapl["graph"]) > 100 and "amount" not in aapl
               and aapl["age"] is not None and {x["id"] for x in sh["stocks"]} == {"AAPL", "VOD.L"}, aapl and {k: aapl[k] for k in ("id", "age")})
        expect("…and the coins list is only coins", all("kind" not in x or x["kind"] == "crypto" for x in sh.get("coins", []))
               and not any(x["id"] in ("AAPL", "VOD.L") for x in sh.get("coins", [])))
    finally:
        for sym in added:
            c.delete(f"/api/watchlist/{sym}")
        for cid in paused:
            c.put(f"/api/watchlist/{cid}", json={"show": True})
        for sym in ("AAPL", "TSLA", "VOD.L", "NOPE"):
            for key in (f"stock_{sym}", f"stock_{sym}__fail"):
                _cache.clear(key)
            for rng, iv in (("1mo", "1h"), ("3mo", "1d"), ("1y", "1d"), ("5y", "1wk")):
                _cache.clear(f"stockhist_{sym}_{rng}_{iv}")
        _st.YAHOO, _cm.STOCK_NEWS = real
        _st._searches.clear()
        fake.stop()
    left = [i["id"] for i in _wl.load() if _wl.kind(i) == "stock" and i["id"] not in ("AMZN",)]
    if left:
        print(f"  FAIL shares left on the list: {left}")
        FAILED.append("shares cleared")
    if (c.get("/api/holdings").get_json() or {}).get("any"):
        FAILED.append("share holdings cleared")
    if before:                                                                  # back as they were
        items = _wl.load()
        _wl.save(items + [i for i in before if i["id"] not in {x["id"] for x in items}])


class _FakePitato:
    """A Pietato on the network, as far as pie_ink/pitato.py looks at it: its web page's
    /api/info on one port, and its camera server (camera.py) on another."""

    def __init__(self):
        self.port = self.cam_port = self.ctl_port = None
        self._servers = []
        self.locked = False
        self.key = "feedfacefeedface"
        self.state = {"mode": "media", "view": "game", "where": "Games › Snake", "playing": None, "volume": 40,
                      "video": False, "controllers": [], "size": [640, 480]}
        # its shelves: folder -> {path: (mtime, bytes)}; what its page was asked to show, and sent
        import time as _time
        now = _time.time()
        self.shelves = {"pictures": {"cats/whiskers.jpg": (now - 3 * 86400, None), "holiday/beach-2026.jpg": (now - 40 * 86400, None)},
                        "music": {"New Order/Blue Monday.mp3": (now - 9000, b"ID3 not really"),
                                  "Boards of Canada/Dayvan Cowboy.flac": (now - 8000, b"fLaC not really")},
                        "videos": {"Holiday 2026.mp4": (now - 20 * 86400, b"\x00\x00\x00 ftypisom")},
                        "documents": {"taxes-2025.pdf": (now - 100 * 86400, b"%PDF-1.4 not really"),
                                      "notes.txt": (now - 50000, b"Milk, eggs, a new SD card.\n")}}
        self.shown = []
        self.uploaded = {}
        self.presses = []               # buttons pressed from its page (POST /api/pi/press)
        self.clips = []                 # (pose, bytes) said on its speaker (POST /coder/say)
        self.shared = None              # what it has fetched, as it shares it (GET /api/shared: no password, your network)
        self.shared_asks = 0

    def start(self, host="127.0.0.1", port=0):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from PIL import Image
        import io
        fake = self
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (90, 160, 220)).save(buf, "JPEG")
        jpeg = buf.getvalue()

        class Page(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data, ctype="application/json", headers=()):
                body = json.dumps(data).encode() if ctype == "application/json" else (data.encode() if isinstance(data, str) else data)
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _args(self):
                from urllib.parse import parse_qs, urlparse
                u = urlparse(self.path)
                return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

            def do_GET(self):
                path, q = self._args()
                if path == "/api/shared":                             # (your other Pietatos ask it: no password)
                    fake.shared_asks += 1
                    return self._send(200, fake.shared) if fake.shared is not None else \
                        self._send(503, {"ok": False, "error": "Pietato isn't answering"})
                if fake.locked:
                    return self._send(401, {"error": "Password needed"}, headers=[("WWW-Authenticate", 'Basic realm="Pietato"')])
                if path == "/api/info":
                    folders = [{"key": k, "label": k.capitalize(), "path": f"/home/pi/{k.capitalize()}", "files": len(v)}
                               for k, v in fake.shelves.items()]
                    return self._send(200, {"name": "pitato-test", "accent": "#62d2ff", "folders": folders, "kinds": {},
                                            "settingsFiles": [], "disk": {"free": 1, "total": 2}})
                if path == "/api/pi/state":                           # as Pietato's page guards it: only the page asks
                    if self.headers.get("X-Pitato-Page") != "1":
                        return self._send(403, "Only the page asks for that", "text/plain")
                    return self._send(200, fake.state)
                if path == "/api/recent":                             # its newest pictures, folders and all
                    pics = sorted(fake.shelves["pictures"].items(), key=lambda kv: -kv[1][0])
                    return self._send(200, {"folder": "pictures", "pictures": [
                        {"path": p, "name": p.rsplit("/", 1)[-1], "mtime": int(t), "size": 1000} for p, (t, _) in pics]})
                if path == "/api/list":                               # one folder, one level, as its page lists it
                    folder, sub = q.get("folder", "music"), q.get("path", "")
                    if folder not in fake.shelves:
                        return self._send(404, {"error": "no such folder"})
                    entries, dirs = [], set()
                    for p, (t, _) in fake.shelves[folder].items():
                        if sub and not p.startswith(sub + "/"):
                            continue
                        rest = p[len(sub) + 1:] if sub else p
                        if "/" in rest:
                            dirs.add(rest.split("/", 1)[0])
                        else:
                            entries.append({"name": rest, "dir": False, "size": 1000, "mtime": int(t)})
                    entries = [{"name": d, "dir": True, "size": 0, "mtime": 0, "items": 1} for d in sorted(dirs)] + entries
                    return self._send(200, {"folder": folder, "path": sub, "entries": entries})
                if path == "/api/file":
                    folder, p = q.get("folder", ""), q.get("path", "")
                    got = (fake.shelves.get(folder) or {}).get(p)
                    if got is None:
                        return self._send(404, {"error": "no such file"})
                    data = got[1] if got[1] is not None else jpeg
                    ctype = "image/jpeg" if p.endswith(".jpg") else "application/octet-stream"
                    return self._send(200, data, ctype)
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if fake.locked:
                    return self._send(401, {"error": "Password needed"}, headers=[("WWW-Authenticate", 'Basic realm="Pietato"')])
                path, q = self._args()
                n = int(self.headers.get("Content-Length") or 0)
                if path == "/coder/say":                               # a WAV to say out loud, as a coding agent sends one
                    raw = self.rfile.read(n) if n else b""
                    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE" or self.headers.get("Content-Type") != "audio/wav":
                        return self._send(415, {"ok": False, "error": "not a WAV file"})
                    fake.clips.append((q.get("pose", "talking"), raw))
                    return self._send(200, {"ok": True, "ms": max(1, (len(raw) - 44) // 44)})
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if path == "/api/pi/show":
                    if (body.get("folder") not in fake.shelves or body.get("path") not in fake.shelves[body["folder"]]
                            and body.get("path") not in fake.uploaded):
                        return self._send(404, {"error": "That file isn't there"})
                    fake.shown.append((body["folder"], body["path"]))
                    return self._send(200, {"ok": True})
                if path == "/api/pi/press":                           # a button, as its page's remote presses one
                    fake.presses.append((body.get("button"), fake.state.get("view")))
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})

            def do_PUT(self):
                path, q = self._args()
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else b""
                if path == "/api/upload":
                    if fake.locked:
                        return self._send(401, {"error": "Password needed"}, headers=[("WWW-Authenticate", 'Basic realm="Pietato"')])
                    where = q.get("path", "")
                    if where in fake.uploaded and q.get("exists", "rename") == "rename":     # as its page does: a new name
                        stem, _, ext = where.rpartition(".")
                        k = 2
                        while f"{stem} ({k}).{ext}" in fake.uploaded:
                            k += 1
                        where = f"{stem} ({k}).{ext}"
                    fake.uploaded[where] = data
                    return self._send(200, {"ok": True, "name": where.rsplit("/", 1)[-1], "path": where, "size": len(data)})
                self._send(404, {"error": "not found"})

        class Control(Page):                                          # media mode's channel, on its own Pi only
            def do_GET(self):
                if self.headers.get("X-Pitato-Key") != fake.key:
                    return self._send(401, {"error": "The web page's key doesn't match. Restart pitato-web."})
                if self.path == "/state":
                    return self._send(200, dict(fake.state, via="control"))
                self._send(404, {"error": "not found"})

        class Cam(Page):
            def do_GET(self):
                if self.path in ("/api/info", "/api/status"):
                    return self._send(200, {"ok": True, "server": "Pietato camera", "name": "Pietato cam", "hostname": "pitato-test"})
                if self.path == "/api/camera.jpg":
                    return self._send(200, jpeg, "image/jpeg")
                self._send(404, {"ok": False, "error": "Not found"})

        for cls, attr in ((Page, "port"), (Cam, "cam_port"), (Control, "ctl_port")):
            srv = HTTPServer((host, port) if cls is Page else ("127.0.0.1", 0), cls)
            setattr(self, attr, srv.server_address[1])
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self._servers.append(srv)

    def stop(self):
        for srv in self._servers:
            srv.shutdown()
            srv.server_close()


def pitato_check(pieapp, c):
    """A Pietato — the handheld Pi — on the network: known the moment it asks for what this
    Pi has fetched, greeted (the screen, the face, the conversation, the events, the bot's
    context, the sidebar), not greeted twice, its camera heard and looked through, remembered
    across a restart, and welcomed back after an hour away."""
    import time as _t
    from pie_ink import chat as _chat
    from pie_ink import friends as _fr
    from pie_ink import llm as _llm
    from pie_ink import mascot as _mascot
    from pie_ink import pitato as _pt
    from pie_ink import sight as _sight
    from pie_ink.modes.me import MeMode
    from pie_ink.modes.simple import bot_face, drawable_face
    from pie_ink.text import font
    print("a Pietato on the network: the handheld Pi, noticed and greeted")
    fake = _FakePitato()
    fake.start()
    P = _pt.PITATO
    real = _pt.PORT, _pt.PATH, _pt._my_ips, _pt.CONTROL_PORT, _pt.CONTROL_KEY, _pt.STATE_EVERY
    _pt.PORT = fake.port
    tmp = tempfile.mkdtemp(prefix="pie-ink-pitato-")
    _pt.PATH = os.path.join(tmp, "pitato.json")
    _pt.CONTROL_PORT, _pt.CONTROL_KEY, _pt.STATE_EVERY = fake.ctl_port, os.path.join(tmp, "control-key"), 1
    with open(_pt.CONTROL_KEY, "w") as f:
        f.write(fake.key + "\n")
    _pt._my_ips = lambda: set()                                   # the stand-in is at 127.0.0.1: first as another Pi…
    P.seen.clear(); P.cameras.clear(); P._excited_until = 0.0
    UA = {"User-Agent": "pietato/1.0 (Raspberry Pi media player)", "Accept": "application/json"}
    mode_before = pieapp.service.mode_name

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def settle(cond, seconds=8):
        for _ in range(int(seconds * 4)):
            if cond():
                return True
            _t.sleep(0.25)
        return cond()
    def hellos():                                                 # its greetings (other threads add lines of their own)
        return [m["text"] for m in _chat.history() if m["role"] == "bot" and "Pietato" in m["text"]
                and ("showed up" in m["text"] or "is back" in m["text"] or "housemate" in m["text"])]
    try:
        n_chat = len(hellos())
        check("GET /api/shared (as a Pietato)", c.get("/api/shared", headers=UA))
        settle(lambda: P.status()["pitatos"] and P.status()["pitatos"][0]["name"] == "pitato-test" and _llm.latest().get("kind") == "pitato")
        st = P.status()
        first = st["pitatos"][0] if st["pitatos"] else {}
        expect("the Pietato is known from its first ask, by its page's name, its asks counted",
               first.get("name") == "pitato-test" and first.get("asks") == 1 and first.get("url") == f"http://127.0.0.1:{fake.port}"
               and first.get("accent") == "#62d2ff" and not first.get("locked"), first)
        said = _llm.latest()
        expect("hello: a line on the bot's screen", said.get("kind") == "pitato" and said["text"].startswith("A Pietato! pitato-test"), said)
        face, mood = bot_face(said.get("text") or "", "pitato")
        expect("…with a starry-eyed face the font can draw", mood == "excited" and "★" in face
               and drawable_face(font(2, 26), said.get("text") or "", "pitato")[0] == face, (face, mood))
        expect("…in the conversation", any("pitato-test" in t for t in hellos()[-2:]) and len(hellos()) == n_chat + 1, hellos()[-3:])
        expect("…and in the events list", any(e["kind"] == "pitato" and "pitato-test" in e["text"] for e in _sight.events(limit=5)))
        words = _mascot.context(pieapp.service.config)
        expect("the bot knows it's about, and is thrilled", any(ln.startswith("Pietato —") and "pitato-test" in ln and "thrilled" in ln for ln in words),
               [w for w in words if "Pietato" in w])
        probe = MeMode(pieapp.service.config, pieapp.service.panel.info())
        _face, notes = probe._mood("Working", None, None)
        expect("the Me screen is pleased too", "A Pietato is here!" in notes, notes)
        now = (c.get("/api/now").get_json() or {}).get("pitato") or {}
        expect("the sidebar has a tile for it", now.get("name") == "pitato-test" and now.get("excited") is True and now.get("asks") == 1, now)
        c.get("/api/shared", headers=UA)
        _t.sleep(0.5)
        expect("asked again: counted, not greeted again", P.status()["pitatos"][0]["asks"] == 2 and len(hellos()) == n_chat + 1)
        tg = pieapp._tg_command("/pis")
        expect("/pis names it", "Pietato pitato-test: asks me for the weather and prices (2 times)" in tg and f"127.0.0.1:{fake.port}" in tg, tg)
        # what's on its screen, as its page is asked: a game, a film, a station, a pause
        settle(lambda: P.status()["pitatos"][0].get("activity") == "playing Snake")
        first = P.status()["pitatos"][0]
        expect("it sees the game being played (asked the way its page asks)", first.get("activity") == "playing Snake" and first.get("screen") == "page", first)
        fake.state = {"mode": "media", "view": "list", "where": "TV › Plex › Films",
                      "playing": {"title": "Blade Runner", "sub": "1982", "src": "plex:1", "kind": "plex", "pos": 2520.0, "length": 7020.0, "paused": False, "song": ""}}
        settle(lambda: P.status()["pitatos"][0].get("activity", "").startswith("watching"))
        expect("…a film, and how far in", P.status()["pitatos"][0].get("activity") == "watching Blade Runner, 42 of 117 min in", P.status()["pitatos"][0].get("activity"))
        owner = _mascot._owner(pieapp.service.config)
        words = _mascot.context(pieapp.service.config)
        expect("…and tells the bot what you're doing", any(f"{owner} is watching Blade Runner, 42 of 117 min in on it right now" in ln for ln in words),
               [w for w in words if "Pietato" in w])
        now = (c.get("/api/now").get_json() or {}).get("pitato") or {}
        expect("…the tile says so", now.get("activity") == "watching Blade Runner, 42 of 117 min in", now)
        expect("…and /pis", "— watching Blade Runner" in pieapp._tg_command("/pis"))
        fake.state = {"mode": "media", "view": "now", "where": "Radio › Now playing",
                      "playing": {"title": "Groove Salad", "sub": "", "src": "http://x", "kind": "radio", "pos": 0, "length": 0, "paused": True, "song": "Boards of Canada - Dayvan Cowboy"}}
        settle(lambda: P.status()["pitatos"][0].get("activity", "").startswith("listening"))
        expect("…a station, paused", P.status()["pitatos"][0].get("activity") == "listening to Boards of Canada - Dayvan Cowboy on Groove Salad (paused)",
               P.status()["pitatos"][0].get("activity"))
        fake.state = {"mode": "terminal", "view": "home", "where": "Terminal", "playing": None}
        settle(lambda: P.status()["pitatos"][0].get("activity") == "in the terminal")
        expect("…or the terminal", P.status()["pitatos"][0].get("activity") == "in the terminal")
        fake.state = {"mode": "media", "view": "game", "where": "Games › Snake", "playing": None}
        # its camera: a beacon on the PiE-inks' port, then a look through it
        _fr.heard("127.0.0.1", {"gpicam": 1, "id": "abcd1234", "host": "pitato-test", "port": fake.cam_port, "name": "Pietato cam", "turns": True})
        cams = P.camera_list()
        expect("its camera's beacon puts the camera on the list", cams and cams[0]["name"] == "Pietato cam" and cams[0]["turns"]
               and cams[0]["url"] == f"http://127.0.0.1:{fake.cam_port}", cams)
        d = c.get("/api/friends").get_json() or {}
        expect("/api/friends lists the Pietato and its camera", (d.get("pitatos") or [{}])[0].get("name") == "pitato-test"
               and (d.get("cameras") or [{}])[0].get("host") == "Pietato cam" and d["cameras"][0].get("kind") == "pitato"
               and d["pitatos"][0].get("cameras") == ["Pietato cam"], (d.get("pitatos"), d.get("cameras")))
        reply = pieapp._mesh_do({"kind": "camera", "who": "pitato cam"}, pieapp._everyone_for_mesh(), "chat")
        expect("'show me the camera from pitato cam' puts its picture on the panel", "Pietato cam's camera is on the panel now" in reply, reply)
        reply = pieapp._mesh_do({"kind": "camera", "who": "pietato cam"}, pieapp._everyone_for_mesh(), "chat")
        expect("…and 'from pietato cam', its name as it's said now", "Pietato cam's camera is on the panel now" in reply, reply)
        reply = pieapp._mesh_do({"kind": "say", "text": "hi", "who": "pitato cam"}, pieapp._everyone_for_mesh(), "chat")
        expect("…but it can't be told to say things", "look through it" in reply, reply)
        mesh = c.get("/api/mesh").get_json() or {}
        expect("/api/mesh describes the camera as a Pietato's", any("a Pietato camera" in p.get("words", "") for p in mesh.get("pis", [])) and mesh.get("pitatos"))
        P._save(force=True)
        again = _pt.Pitato()
        expect("remembered across a restart, with when it was greeted (no second hello)",
               again.seen.get("127.0.0.1", {}).get("name") == "pitato-test" and again.seen["127.0.0.1"]["greeted"] > 0, again.seen)
        # back after an hour away
        with P._lock:
            P.seen["127.0.0.1"]["last"] -= 7200
            P.seen["127.0.0.1"]["greeted"] -= 7200
            P.seen["127.0.0.1"]["probed"] -= 7200
        n_chat = len(hellos())
        c.get("/api/shared", headers=UA)
        settle(lambda: len(hellos()) > n_chat)
        said = _llm.latest()
        expect("back after an hour away: welcomed back", said.get("kind") == "pitato" and "is back" in said["text"] and "pitato-test" in said["text"], said)
        # a page with a password: still a Pietato, named by what the router calls it (nothing, here)
        fake.locked = True
        P.seen.clear()
        n_chat = len(hellos())
        c.get("/api/shared", headers=UA)
        settle(lambda: P.seen.get("127.0.0.1", {}).get("probed", 0) > 0 and len(hellos()) > n_chat)
        p = P.seen.get("127.0.0.1") or {}
        expect("a page with a password is still a Pietato", p.get("web") is True and p.get("locked") is True, p)
        settle(lambda: (P.seen.get("127.0.0.1") or {}).get("state_how") == "locked")
        words = _mascot.context(pieapp.service.config)
        expect("…whose screen stays its own", (P.seen.get("127.0.0.1") or {}).get("state_how") == "locked" and not P.activity(P.seen["127.0.0.1"])
               and any("its page has a password" in ln for ln in words), [w for w in words if "Pietato" in w])
        # …then as what it is here: a Pietato on this very Pi (the two of them on one Zero 2 W)
        fake.locked = False
        _pt._my_ips = real[2]
        P.seen.clear()
        n_chat = len(hellos())
        c.get("/api/shared", headers=UA)
        settle(lambda: len(hellos()) > n_chat)
        said = _llm.latest()
        st = P.status()
        expect("a Pietato on this very Pi is greeted as a housemate",
               said.get("kind") == "pitato" and "right here with me, on this very Pi" in said["text"]
               and st["pitatos"] and st["pitatos"][0]["here"] is True, (said, st["pitatos"]))
        now = (c.get("/api/now").get_json() or {}).get("pitato") or {}
        words = _mascot.context(pieapp.service.config)
        expect("…the sidebar and the bot say so", now.get("here") is True and any("lives on this very Pi" in ln for ln in words),
               (now, [w for w in words if "Pietato" in w]))
        expect("…and /pis", "(on this Pi)" in pieapp._tg_command("/pis"))
        settle(lambda: P.status()["pitatos"][0].get("screen") == "local")
        first = P.status()["pitatos"][0]
        expect("on this Pi its screen is read over media mode's own channel, with the key it leaves for the page",
               first.get("screen") == "local" and first.get("activity") == "playing Snake", first)
        os.remove(_pt.CONTROL_KEY)                                   # no key to read: the page's way instead
        settle(lambda: P.status()["pitatos"][0].get("screen") == "page")
        expect("…and the page's way when the key can't be read", P.status()["pitatos"][0].get("screen") == "page", P.status()["pitatos"][0])
    finally:
        fake.stop()
        _pt.PORT, _pt.PATH, _pt._my_ips, _pt.CONTROL_PORT, _pt.CONTROL_KEY, _pt.STATE_EVERY = real
        P.seen.clear(); P.cameras.clear(); P._excited_until = 0.0
        pieapp.service.set_mode(mode_before)
    if P.status()["any"]:
        FAILED.append("pitato cleared")


def shelf_check(pieapp, c):
    """One bot for both Pis: asked for a photo, a song, a video or a document, it looks on this
    Pi and on the Pietato (its page's own questions), and does the right thing with what it finds."""
    import time as _t
    from PIL import Image as _Image
    from pie_ink import mascot as _mascot
    from pie_ink import photos as _ph
    from pie_ink import pitato as _pt
    from pie_ink import reader as _rd
    from pie_ink import shelf as _sh
    print("the shelves: photos, songs, videos and documents on either Pi, for the one bot")
    fake = _FakePitato()
    fake.start()
    P = _pt.PITATO
    real = _pt.PORT, _pt._my_ips
    _pt.PORT = fake.port
    _pt._my_ips = lambda: set()
    now = _t.time()
    P.seen.clear(); P.cameras.clear()
    P.seen["127.0.0.1"] = {"ip": "127.0.0.1", "name": "pitato-test", "web": True, "locked": False, "accent": "", "first": now,
                           "last": now, "asks": 1, "greeted": now, "probed": now, "here": False}
    _sh._cache.clear()
    mode_before = pieapp.service.mode_name
    photo_name = _t.strftime("%Y%m%d-%H%M%S") + "-sepia.jpg"
    os.makedirs(_ph.DIR, exist_ok=True)
    _Image.new("RGB", (80, 60), (200, 160, 90)).save(os.path.join(_ph.DIR, photo_name), "JPEG")
    from pie_ink import music as _mu
    music_dir = _mu.ensure_folder(pieapp.service.config.get("music", {}))
    song = os.path.join(music_dir, "Test Song.mp3")
    with open(song, "wb") as f:
        f.write(b"ID3 not really a song")
    books_before = {b["id"] for b in _rd.library()}

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def ask(text):
        r = c.post("/api/llm/chat", json={"message": text})
        d = r.get_json() or {}
        return d.get("reply") or d.get("error") or ""
    try:
        # what was asked
        wants = {"show me the latest photo": ("show", "latest photo"), "find the song called blue monday": ("find", "song called blue monday"),
                 "do I have a pdf about taxes": ("find", "pdf about taxes"), "play dayvan cowboy": ("play", "dayvan cowboy"),
                 "put the holiday video on the pitato": ("pitato", "holiday video"), "what videos are there": ("list", "what videos are there"),
                 "send me the picture of the beach": ("show", "picture of the beach"), "open the document called notes": ("show", "document called notes"),
                 "play some music": ("play", "music"), "can you show me the newest picture?": ("show", "newest picture")}
        bad = {t: _sh.parse(t) for t, (k, w) in wants.items() if (_sh.parse(t) or {}).get("kind") != k or (_sh.parse(t) or {}).get("words") != w}
        expect("the shelf hears what's asked of it", not bad, bad)
        nots = ["show me the camera from upstairs", "where is the workshop pi", "find the pi with a camera", "show the clock on the workshop pi",
                "turn on the tv", "what are you doing", "play snake", "take a picture"]
        bad = [t for t in nots if _sh.parse(t)]
        expect("…and leaves the rest alone", not bad, bad)
        # the shelves themselves
        items = _sh.everything(pieapp.service.config)
        kinds = sorted({(i["where"], i["kind"]) for i in items})
        expect("this Pi's photos and music, and the Pietato's pictures, music, videos and documents, on one shelf",
               ("here", "photo") in kinds and ("here", "music") in kinds and all((x, k) in kinds for x, k in
               (("pitato", "photo"), ("pitato", "music"), ("pitato", "video"), ("pitato", "document"))), kinds)
        expect("…its folders within folders too", any(i["path"] == "New Order/Blue Monday.mp3" for i in items))
        # the bot's questions, answered without a model
        reply = ask("show me the latest photo")
        expect("'show me the latest photo': this Pi's, the newest, on the panel",
               "is on the panel" in reply and "here" in reply and pieapp.service.mode_name == "postcard", reply)
        reply = ask("show me the picture of whiskers")
        expect("'show me the picture of whiskers': fetched from the Pietato, on the panel", reply.startswith("whiskers is on the panel (a photo on the Pietato"), reply)
        got = pieapp._tg_command("send me the picture of the beach")
        expect("Telegram: the picture comes back", isinstance(got, tuple) and got[0][:3] == b"\xff\xd8\xff" and "beach" in got[1], got if not isinstance(got, tuple) else got[1])
        reply = ask("find the song called blue monday")
        expect("'find the song called blue monday': found on the Pietato, with where", reply.startswith("Found it: Blue Monday — a song on the Pietato (Music/New Order"), reply)
        reply = ask("play dayvan cowboy")
        expect("'play dayvan cowboy': played on the Pietato (its page asked to show the file)",
               reply == "Playing Dayvan Cowboy on the Pietato." and fake.shown[-1:] == [("music", "Boards of Canada/Dayvan Cowboy.flac")], (reply, fake.shown))
        reply = ask("put the holiday video on the pitato")
        expect("'put the holiday video on the pitato'", reply == "Holiday 2026 is on the Pietato's screen." and fake.shown[-1] == ("videos", "Holiday 2026.mp4"), (reply, fake.shown))
        reply = ask("do I have a pdf about taxes")
        expect("'do I have a pdf about taxes'", reply.startswith("Found it: taxes 2025 — a document on the Pietato"), reply)
        reply = ask("open the document called notes")
        new_books = [b for b in _rd.library() if b["id"] not in books_before]
        for _ in range(60):                                   # (the screen changes on the display's own thread)
            if pieapp.service.mode_name == "reader":
                break
            _t.sleep(0.05)
        expect("'open the document called notes': copied over and open in Read",
               "open in Read (copied over from the Pietato)" in reply and new_books and pieapp.service.mode_name == "reader", (reply, new_books))
        reply = ask("what videos are there")
        expect("'what videos are there'", reply.startswith("1 video: 0 here, 1 on the Pietato. Newest: Holiday 2026"), reply)
        reply = ask("play some music")
        expect("'play some music': this Pi's own", reply == "Playing Test Song.", reply)
        reply = ask("show me the picture of a dinosaur")
        expect("nothing like it: says so, naming both Pis", reply == "I couldn't find anything like 'picture of a dinosaur' on either Pi.", reply)
        words = _sh.words(pieapp.service.config, "Sam")
        expect("the bot is told what the shelves hold", "here 1 photo" in words and "on the Pietato 2 pictures, 2 songs, 1 video, 2 documents" in words
               and "Sam can ask you" in words, words)
        expect("…in its context", any(ln.startswith("On the shelves") for ln in _mascot.context(pieapp.service.config)))
        # a Pietato page with a password keeps its shelves to itself
        fake.locked = True
        _sh._cache.clear()
        reply = ask("find the song called blue monday")
        expect("a locked page: its shelves are closed, and the bot says why", "password" in reply, reply)
        fake.locked = False
        _sh._cache.clear()
        # a picture sent with a message (a Pietato's Bot using this one): it reaches the conversation
        import base64 as _b64
        import io as _io
        buf = _io.BytesIO()
        _Image.new("RGB", (32, 32), (10, 200, 10)).save(buf, "PNG")
        pic = "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        r = c.post("/api/llm/chat", json={"message": "what's this?", "image": pic})
        last_you = [m for m in pieapp.chat.history() if m["role"] == "you"][-1]["text"]
        expect("a picture with a message is taken in (no model here, so it stops there)", r.status_code == 400 and "[sent a picture]" in last_you, (r.status_code, last_you))
        expect("…and a bad one is not", pieapp._picture_in("data:image/png;base64,AAAA") is None and pieapp._picture_in(pic))
        # asked from a Pietato's Bot page (one bot for the house): answered there, not said out loud here too
        said = []
        real_say, speak_before = pieapp.audio.say, pieapp.service.config.get("audio", {}).get("speak_bot")
        pieapp.audio.say = lambda text, *a, **k: said.append(text)
        try:
            c.put("/api/settings", json={"audio": {"speak_bot": True}})
            r = c.post("/api/llm/chat", json={"message": "find the song called blue monday"},
                       headers={"User-Agent": "pietato/1.0 (Raspberry Pi media player)"})
            last = pieapp.chat.history()[-1]
            expect("from the Pietato: the same answer, kept in the one conversation, marked as from the Pietato, not spoken here",
                   r.status_code == 200 and (r.get_json() or {}).get("reply", "").startswith("Found it: Blue Monday")
                   and last["how"] == "pitato" and not said, (r.status_code, last, said))
            c.post("/api/llm/chat", json={"message": "find the song called blue monday"})
            expect("…from the page: spoken, as before", said == ["Found it: Blue Monday — a song on the Pietato (Music/New Order) · 2 h ago. Say 'show me …' or 'play …' and I'll fetch it."]
                   or (len(said) == 1 and said[0].startswith("Found it: Blue Monday")), said)
        finally:
            pieapp.audio.say = real_say
            c.put("/api/settings", json={"audio": {"speak_bot": bool(speak_before)}})
    finally:
        fake.stop()
        _pt.PORT, _pt._my_ips = real
        P.seen.clear(); P.cameras.clear()
        _sh._cache.clear()
        _ph.delete(photo_name)
        try:
            os.remove(song)
        except OSError:
            pass
        for b in _rd.library():
            if b["id"] not in books_before:
                _rd.remove(b["id"])
        pieapp.music.PLAYER.stop()
        pieapp.service.set_mode(mode_before)


def voice_check(pieapp, c):
    """No speaker here: the bot's lines, reminders and "say" go to a Pietato's speaker — the clip
    made here, said there — when that output is chosen, or when this Pi has no sound card at all."""
    import time as _t
    from pie_ink import audio as _au
    from pie_ink import pitato as _pt
    print("a voice on the Pietato: said there when there's no speaker here")
    fake = _FakePitato()
    fake.start()
    P = _pt.PITATO
    real = _pt.PORT, _pt._my_ips, _au._synthesise, _au.alsa_list
    _pt.PORT = fake.port
    _pt._my_ips = lambda: set()
    now = _t.time()
    P.seen.clear(); P.cameras.clear()
    P.seen["127.0.0.1"] = {"ip": "127.0.0.1", "name": "pitato-test", "web": True, "locked": False, "accent": "", "first": now,
                           "last": now, "asks": 1, "greeted": now, "probed": now, "here": False}
    _au._synthesise = lambda text, conf, timeout=180: (b"\x10\x00" * 2205, 22050)        # a tenth of a second of hum
    device_before = pieapp.service.config.get("audio", {}).get("device", "")

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)
    try:
        devs = (c.get("/api/audio").get_json() or {}).get("devices", [])
        expect("the Sound settings offer a Pietato's speaker as an output", any(d["id"] == "pitato" for d in devs), devs)
        conf = {"device": "pitato"}
        got = _au.say("Reminder: the kettle.", conf, wait=True)
        expect("chosen as the output: the line is made here and said on the Pietato",
               got == (True, "said on the Pietato (pitato-test)") and len(fake.clips) == 1 and fake.clips[0][0] == "talking"
               and fake.clips[0][1][:4] == b"RIFF" and len(fake.clips[0][1]) == 44 + 4410, (got, [(p, len(b)) for p, b in fake.clips]))
        got = _au.say("Again.", conf)
        _t.sleep(0.8)
        expect("…and without waiting, in the background", got == (True, "speaking on the Pietato") and len(fake.clips) == 2, (got, len(fake.clips)))
        check("PUT /api/settings audio.device pitato", c.put("/api/settings", json={"audio": {"device": "pitato"}}))
        check("POST /api/audio/say (to the Pietato)", c.post("/api/audio/say", json={"text": "hello there", "wait": True}))
        expect("the page's Say goes there too", len(fake.clips) == 3)
        c.put("/api/settings", json={"audio": {"device": device_before}})
        _au.alsa_list = lambda tool="aplay", fresh=False: []                                 # no sound card at all
        got = _au.say("No speaker here.", {"device": ""}, wait=True)
        expect("no sound card here: it goes to the Pietato by itself", got[0] and len(fake.clips) == 4, (got, len(fake.clips)))
        _au.alsa_list = real[3]
        fake.locked = True
        P.seen["127.0.0.1"]["locked"] = True
        got = _au.say("Locked.", conf, wait=True)
        expect("a Pietato page with a password can't be asked", got == (False, "the Pietato's page has a password, so it can't be asked to speak"), got)
        fake.locked = False
        P.seen.clear()
        got = _au.say("Nobody.", conf, wait=True)
        expect("…nor a Pietato that isn't about", got == (False, "no Pietato about to say it on"), got)
        expect("…and the label", _au.output_label("pitato") == "the Pietato's speaker")
    finally:
        fake.stop()
        _pt.PORT, _pt._my_ips, _au._synthesise, _au.alsa_list = real
        P.seen.clear(); P.cameras.clear()
        c.put("/api/settings", json={"audio": {"device": device_before}})


def pietato_check(pieapp, c):
    """Pietato, by its name now: its User-Agent and its page's password box as they are now (and as they were),
    its own beacon heard (its name, its colour) and greeted, "pietato" understood in what's said and in Telegram;
    and what it has fetched used here rather than fetched again — the weather near here in this Pi's units, the
    worded forecast, a coin's figures over the ones kept, a coin's charts — only while fresh enough, with how old
    it really is, asked at most once a minute, and never of one that isn't about."""
    import copy as _copy
    import shutil
    import socket as _so
    import time as _t
    from pie_ink import borrow as _bo, cache as _cache, coingecko as _cg, friends as _fr, llm as _llm, pitato as _pt, weather as _wx

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"pietato: {name}")

    def wait_for(test, secs=8):
        deadline = _t.time() + secs
        while not test() and _t.time() < deadline:
            _t.sleep(0.1)
        return test()

    P = _pt.PITATO
    keep = (_pt.PORT, _pt.PATH, _copy.deepcopy(P.seen), P._excited_until, _wx._get, _cg._get)
    tmp = tempfile.mkdtemp(prefix="pie-ink-pietato-")
    _pt.PATH = os.path.join(tmp, "pitato.json")
    fake = _FakePitato()
    fake.start()
    lat, lon = 40.0, -105.0
    keys = [f"weather_{lat:.3f}_{lon:.3f}_F", f"nws_{lat:.3f}_{lon:.3f}", "coin_testcoin", "hist_testcoin_7",
            "hist_testcoin_30", "hist_testcoin_1"]
    try:
        with P._lock:
            P.seen.clear()
        uas = [P.is_pitato(ua) for ua in ("pietato/1.0 (Raspberry Pi media player)", "pie-tato/1.0 (Raspberry Pi media player)",
                                          "pitato/1.0 (Raspberry Pi media player)", "Mozilla/5.0", "pietatoes/1", "")]
        said = [_pt.plain_name(t) for t in ("put the clock on the pietato", "Send this drawing to the Pie-tato",
                                            "show the weather on both pietatos", "the pie tato", "the pitato", "a pietà",
                                            "show the time on pietato-tv")]
        note("its User-Agent as it is now (pietato/) and as it was (pie-tato/, pitato/); \"pietato\" in what's said is it",
             uas == [True, True, True, False, False, False]
             and said == ["put the clock on the pitato", "Send this drawing to the Pitato", "show the weather on both pitatos",
                          "the pitato", "the pitato", "a pietà", "show the time on pietato-tv"], f"{uas} {said}")
        typed = [pieapp._home_command(t, "page") for t in ("show the time on the pietato", "show the time on the pitato")]
        tg = [pieapp._tg_command(t) for t in ("/pietato time", "/pitato time")]
        note("…typed, or in Telegram (/pietato, and /pitato as before): the same as ever",
             typed[0] is not None and typed[0] == typed[1] and tg[0] == tg[1] and "I don't know" not in (tg[0] or "")
             and "/pietato time" in pieapp.TG_HELP, f"{typed} {tg}")

        # its own beacon
        s = _so.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
        s.close()
        _pt.PORT = closed                                       # (no page answering there: the beacon's name stands)
        _fr.heard("127.0.0.2", {"pietato": 1, "id": "run-1", "port": 5432, "host": "gpi-den", "accent": "Teal"})
        _fr.heard("127.0.0.3", {"pietato": 1, "port": 5432, "host": "no-id"})
        _fr.heard("127.0.0.4", {"pietato": 1, "id": "run-2", "port": "x", "host": "bad-port"})
        greeted = wait_for(lambda: "gpi-den" in (_llm.latest().get("text") or ""))
        p = P.seen.get("127.0.0.2") or {}
        note("its own beacon: it's there by its name and colour, greeted, and the bot knows how it was heard",
             greeted and p.get("name") == "gpi-den" and p.get("accent") == "#40c4b4" and "Pietato" in _llm.latest()["text"]
             and "gpi-den (saying it's there" in P.words() and "127.0.0.3" not in P.seen and "127.0.0.4" not in P.seen,
             f"{p} {_llm.latest().get('text')} {P.words()[:200]}")
        before = _llm.latest()["ts"]
        _fr.heard("127.0.0.2", {"pietato": 1, "id": "run-1", "port": 5432, "host": "gpi-den", "accent": "Teal"})
        _t.sleep(0.5)
        note("…and its next beacon isn't another hello", _llm.latest()["ts"] == before)

        # a request from it, by its name now
        _pt.PORT = fake.port
        with P._lock:
            P.seen.clear()
        c.get("/api/shared", headers={"User-Agent": "pietato/1.0 (Raspberry Pi media player)"})
        note("a request from it (pietato/1.0) is a Pietato's; its page answers",
             wait_for(lambda: (P.seen.get("127.0.0.1") or {}).get("web") is True), str(P.seen.get("127.0.0.1")))

        # what it has fetched, used here
        for k in keys:
            _cache.clear(k)
            _cache.clear(k + "__fail")
        now = _t.time()
        prices7 = [1.0 + i / 100 for i in range(168)]
        fake.shared = {
            "ok": True, "from": "pietato", "id": "run-3", "at": now, "stocks": [],
            "coins": [{"id": "testcoin", "symbol": "tst", "name": "Test", "age": 30.0,
                       "data": {"price": 2.0, "ath": 4.0, "market_cap": 2e9, "rank": 7, "circulating_supply": 1e9,
                                "sentiment_up": 70.0, "high_24h": 2.2, "low_24h": 1.9, "change": {"24h": -1.5}},
                       "graph": prices7, "graph_days": 7, "graph_age": 40.0,
                       "graphs": [{"days": 7, "v": prices7, "age": 40.0}, {"days": 1, "v": [2.0] * 288, "age": 20.0}]}],
            "weather": {"lat": lat + 0.05, "lon": lon, "units": "C", "age": 120.0, "place": "Somewhere", "country": "US",
                        "data": {"temp": 20.0, "feels": 19.0, "humidity": 50, "wind": 16.09344, "wind_dir": 270, "code": 2,
                                 "is_day": True, "units": "C",
                                 "hours": [{"time": f"2026-11-05T{h:02d}:00", "temp": 10.0 + h, "code": 1, "rain": 5, "is_day": True}
                                           for h in range(10, 18)],
                                 "days": [{"date": f"2026-11-{5 + i:02d}", "code": 3, "hi": 25.0, "lo": 10.0, "rain": 20,
                                           "sunrise": "06:25", "sunset": "16:50"} for i in range(7)]},
                        "nws": [["Tonight", "Clear, with a low around 40."]], "nws_age": 120.0}}
        asked = []

        def nope(url, *a, **k):
            asked.append(str(url))
            raise RuntimeError("no internet in the test")
        _wx._get = _cg._get = nope
        _bo._answers.clear()
        n0 = fake.shared_asks
        w = _wx.fetch(lat, lon, "F") or {}
        age = _wx.age(lat, lon, "F") or 0
        note("the weather near here, from the Pietato rather than the internet: in this Pi's units, with its true age",
             not asked and w.get("temp") == 68.0 and w.get("wind") == 10.0 and w["days"][0]["hi"] == 77.0
             and len(w["hours"]) == 8 and w["hours"][0]["temp"] == 68.0 and w.get("units") == "F" and 119 <= age < 135
             and w.get("from") == "pietato", f"{asked} {age} {str(w)[:300]}")
        note("…and the worded forecast", _wx.nws_forecast(lat, lon) == [["Tonight", "Clear, with a low around 40."]] and not asked,
             str(_wx.nws_forecast(lat, lon, cached_only=True)))
        _cache.write("coin_testcoin", {"price": 1.5, "ath": 4.0, "ath_date": "2025-01-01", "from_ath": -62.5, "market_cap": 1e9,
                                       "rank": 9, "total_supply": 5e9, "circulating_supply": 1e9, "sentiment_up": 60.0,
                                       "high_24h": 1.6, "low_24h": 1.4,
                                       "change": {"24h": 3.0, "7d": 12.0, "30d": -4.0, "200d": 50.0, "1y": 80.0}},
                     age=_cg.COIN_TTL + 60)
        d = _cg.coin_data("testcoin") or {}
        note("a coin's figures, over the ones kept here (the longer changes stay this Pi's)",
             not asked and d.get("price") == 2.0 and d.get("rank") == 7 and d["change"]["24h"] == -1.5 and d["change"]["7d"] == 12.0
             and d.get("ath_date") == "2025-01-01" and d.get("from_ath") == -50.0 and 29 <= (_cg.coin_age("testcoin") or 0) < 45,
             f"{asked} {d}")
        h7, h1 = _cg.price_history("testcoin", 7) or [], _cg.price_history("testcoin", 1) or []
        h30 = _cg.price_history("testcoin", 30)
        note("a coin's chart for a range it has; the internet asked for one it hasn't",
             h7 == prices7 and len(h1) == 288 and not h30 and any("market_chart" in a for a in asked), f"{len(h7)} {len(h1)} {asked}")
        note("…all of it from one answer (kept a minute)", fake.shared_asks - n0 == 1, str(fake.shared_asks - n0))

        def fetched_itself(change):
            fake.shared = change(_copy.deepcopy(base))
            _bo._answers.clear()
            asked.clear()
            for k in keys[:1]:
                _cache.clear(k)
                _cache.clear(k + "__fail")
            _wx.fetch(lat, lon, "F")
            return bool(asked)
        base = _copy.deepcopy(fake.shared)
        old = fetched_itself(lambda d: (d["weather"].update(age=700.0), d)[1])
        far = fetched_itself(lambda d: (d["weather"].update(lat=lat + 1.0), d)[1])
        stranger = fetched_itself(lambda d: (d.pop("from"), d)[1])
        note("…not when it's older than this Pi keeps its own, for somewhere over 25 km away, or not a Pietato's answer",
             old and far and stranger, f"{old} {far} {stranger}")
        with P._lock:
            P.seen["127.0.0.1"]["last"] = _t.time() - 400
        n1 = fake.shared_asks
        away = fetched_itself(lambda d: d)
        note("…and none is asked when no Pietato's been about lately", away and fake.shared_asks == n1, str(fake.shared_asks - n1))
    finally:
        _pt.PORT, _pt.PATH, seen, P._excited_until, _wx._get, _cg._get = keep
        with P._lock:
            P.seen.clear()
            P.seen.update(seen)
        for k in keys:
            _cache.clear(k)
            _cache.clear(k + "__fail")
        _bo._answers.clear()
        fake.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def pitato_screen_check(pieapp, c):
    """A Pietato's screen as one more screen: any screen, a drawing, a message and the time put on it as
    pictures made for its shape, through its own page, made full screen (no footer, no bars) once its
    picture viewer is up; the live ones kept fresh while they're still the picture there. Two Pietatos:
    which one is asked, or both."""
    import io
    import time as _t
    import base64
    from PIL import Image
    from pie_ink import pitato as _pt
    from pie_ink import pitato_screen as _ps
    from pie_ink import mascot as _mascot
    print("the Pietato's screen: any screen, a drawing, a message, the time — on one Pietato or both")
    fake = _FakePitato()
    fake.state = dict(fake.state, view="list", where="Home", size=[1920, 1080])
    fake.start()
    P = _pt.PITATO
    real = _pt.PORT, _pt._my_ips, _ps.FIRST_LOOK, _ps._till_next_minute
    _pt.PORT = fake.port
    _pt._my_ips = lambda: set()
    now = _t.time()
    P.seen.clear()
    P.seen["127.0.0.1"] = {"ip": "127.0.0.1", "name": "pitato-test", "web": True, "locked": False, "accent": "", "first": now,
                           "last": now, "asks": 1, "greeted": now, "probed": now, "here": False}
    NOW = "pie-ink/now/screen.png"
    two = None

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def ask(text):
        return ((c.post("/api/llm/chat", json={"message": text}).get_json() or {}).get("reply") or "")

    def picture(path, f=None):
        return Image.open(io.BytesIO((f or fake).uploaded[path]))
    try:
        state, how = P._state_of(P.seen["127.0.0.1"])
        P.seen["127.0.0.1"].update(state=state, state_how=how)
        expect("its screen's shape is read from what it says about itself", (state or {}).get("size") == [1920, 1080], state)
        d = c.get("/api/pitato/screens").get_json() or {}
        expect("GET /api/pitato/screens lists it", [x["name"] for x in d.get("screens", [])] == ["pitato-test"], d)
        # a drawing, from the page
        buf = io.BytesIO()
        img = Image.new("L", (250, 122), 255)
        img.paste(0, (100, 40, 150, 80))
        img.save(buf, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        r = c.post("/api/pitato/show", json={"kind": "drawing", "image": data_url})
        kept = [k for k in fake.uploaded if k.startswith("pie-ink/drawings/")]
        expect("a drawing from the page goes on its screen, and a copy is kept in its Pictures",
               r.status_code == 200 and len(kept) == 1 and fake.shown[-1] == ("pictures", NOW), (r.get_json(), list(fake.uploaded), fake.shown[-1:]))
        pic = picture(NOW)
        expect("…made for its screen's shape exactly (16:9 here: no bars when it fills it), the drawing's pixels kept crisp",
               pic.size == (1600, 900) and pic.getpixel((800, 450)) == (0, 0, 0) and pic.getpixel((3, 3)) == (255, 255, 255), pic.size)
        expect("…and Start isn't pressed while its screen isn't the picture viewer", not fake.presses, fake.presses)
        c.post("/api/pitato/show", json={"kind": "drawing", "image": data_url})
        kept = [k for k in fake.uploaded if k.startswith("pie-ink/drawings/")]
        expect("a second drawing the same minute is kept too", len(kept) == 2, kept)
        # full screen, once the picture viewer is up
        fake.state = dict(fake.state, view="photo", where="Files › Pictures › pie-ink › now")
        r = c.post("/api/pitato/show", json={"kind": "message", "text": "Dinner is ready"})
        expect("a message from the page, full screen (the viewer's Start pressed once it's up)",
               r.status_code == 200 and fake.shown[-1] == ("pictures", NOW) and picture(NOW).size == (1600, 900)
               and fake.presses[-1:] == [("start", "photo")], (r.get_json(), fake.presses))
        reply = ask('show "come and see this" on the pitato')
        expect("'show \"come and see this\" on the pitato'", reply.startswith("On pitato-test's screen: come and see this"), reply)
        reply = ask("send a message to the pitato saying the pizza is here")
        expect("'send a message to the pitato saying …'", "the pizza is here" in reply, reply)
        # any screen
        r = c.post("/api/pitato/show", json={"kind": "screen", "name": "me"})
        pic = picture(NOW)
        expect("any screen from the page (the Me screen): laid out for its shape and filling it",
               r.status_code == 200 and pic.size == (1600, 900) and len(pic.getcolors(1 << 16) or []) > 2, r.get_json())
        reply = ask("show the finance screen on the pitato")
        expect("'show the finance screen on the pitato'", reply.startswith("The finance screen is on pitato-test's screen"), reply)
        reply = ask("show the holiday video on the pitato")
        expect("…while 'show the holiday video on the pitato' is still a thing on its shelves", "Finance" not in reply and "finance" not in reply, reply)
        # the time, kept fresh while it's the picture on its screen
        _ps.FIRST_LOOK = 0.2
        _ps._till_next_minute = lambda: 0.6
        before = len(fake.shown)
        reply = ask("show the time on the pitato")
        expect("'show the time on the pitato'", reply.startswith("The time is on pitato-test's screen") and fake.shown[-1] == ("pictures", NOW), reply)
        _t.sleep(2.0)
        expect("…drawn again each minute while it's still the picture there, full screen each time",
               len(fake.shown) - before >= 3 and _ps.keeping("127.0.0.1") == "clock" and fake.presses[-1] == ("start", "photo"), len(fake.shown) - before)
        expect("the bot knows it can, and that the time is up there", "show the time on the pietato" in _ps.words("Test")
               and "The time is on pitato-test's screen now" in "\n".join(_mascot.context(pieapp.service.config)))
        fake.state = dict(fake.state, view="list", where="Home")                 # a button pressed on the Pietato
        _t.sleep(3.5)                                                            # (a picture already on its way lands first)
        n = len(fake.shown)
        _t.sleep(1.0)
        expect("…and it stops once it's put away there", not _ps.clock_on("127.0.0.1") and len(fake.shown) == n)
        fake.state = dict(fake.state, view="photo", where="Files › Pictures › pie-ink › now")
        ask("show the weather on the pitato")
        expect("a live screen is kept fresh too", _ps.keeping("127.0.0.1") == "weather", _ps.keeping("127.0.0.1"))
        reply2 = ask("stop the clock on the pitato")
        expect("'stop the clock on the pitato'", reply2 == "The Pietato's screen is no longer kept up to date." and not _ps.clock_on("127.0.0.1"), reply2)
        # the drawing in hand, from words; Telegram
        from pie_ink.modes.simple import ImageMode
        ImageMode._current = img.convert("RGBA")
        reply = ask("send this drawing to the pitato")
        expect("'send this drawing to the pitato'", reply == "The drawing is on pitato-test's screen.", reply)
        expect("Telegram's /pitato puts the time up", pieapp._tg_command("/pitato").startswith("The time is on pitato-test"))
        expect("…and /pitato system any screen", pieapp._tg_command("/pitato system").startswith("The system screen is on pitato-test"))
        pieapp._tg_command("/pitato stop")
        expect("a thing of its own is still shown from its shelves, not made into a message", pitato_screen_parse_ok())
        # two Pietatos: which one, or both
        two = _FakePitato()
        two.state = dict(fake.state, size=[640, 480])
        two.start(host="127.0.0.2", port=fake.port)
        P.seen["127.0.0.2"] = {"ip": "127.0.0.2", "name": "pitato-two", "web": True, "locked": False, "accent": "", "first": now,
                               "last": now, "asks": 1, "greeted": now, "probed": now, "here": False}
        st2, how2 = P._state_of(P.seen["127.0.0.2"])
        P.seen["127.0.0.2"].update(state=st2, state_how=how2)
        reply = ask("show the weather on the pitato")
        expect("two Pietatos: 'on the pitato' asks which", reply.startswith("Which Pietato —") and "pitato-two" in reply and "both" in reply, reply)
        a_before, b_before = len(fake.shown), len(two.shown)
        reply = ask("show the weather on both pitatos")
        expect("'… on both pitatos' puts it on both, each made for its own shape",
               "pitato-test and pitato-two's screens" in reply and len(fake.shown) > a_before and len(two.shown) > b_before
               and picture(NOW, two).size == (1600, 1200) and picture(NOW).size == (1600, 900), (reply, picture(NOW, two).size))
        b_before = len(two.shown)
        r = c.post("/api/pitato/show", json={"kind": "message", "text": "just you", "to": "127.0.0.2"})
        expect("the page's pick of one goes to that one only", r.status_code == 200 and len(two.shown) == b_before + 1, r.get_json())
        reply = ask("show the time on pitato-two")
        expect("…and by its name", reply.startswith("The time is on pitato-two's screen"), reply)
        expect("the bot is told how to say which", "on both pietatos" in _ps.words("Test"))
        _ps.stop_clock()
        two.stop()
        two = None
        P.seen.pop("127.0.0.2", None)
        # a page with a password; none about
        fake.locked = True
        r = c.post("/api/pitato/show", json={"kind": "message", "text": "hello"})
        expect("a Pietato page with a password can't be asked", r.status_code == 400 and "password" in (r.get_json() or {}).get("error", ""), r.get_json())
        fake.locked = False
        P.seen.clear()
        r = c.post("/api/pitato/show", json={"kind": "clock"})
        expect("…nor one that isn't about", r.status_code == 400 and "no Pietato" in (r.get_json() or {}).get("error", ""), r.get_json())
    finally:
        _ps.stop_clock()
        if two is not None:
            two.stop()
        fake.stop()
        _pt.PORT, _pt._my_ips, _ps.FIRST_LOOK, _ps._till_next_minute = real
        P.seen.clear()


def pitato_screen_parse_ok():
    import app as pieapp
    from pie_ink import pitato_screen as _ps
    whiskers = _ps.parse("show the picture of whiskers on the pitato")
    return ((whiskers is None or (whiskers["kind"] == "screen" and pieapp._screen_named(whiskers["what"]) is None))
            and pieapp._pitato_screen_command("show the picture of whiskers on the pitato", "typed") is None
            and _ps.parse("show the clock on the kitchen pi") is None
            and _ps.parse("show the time on the pitato")["kind"] == "clock" and _ps.parse("what time is it") is None)


def memory_check(pieapp, c):
    """The bot's notebook and its reminders: what's said is kept and read back into its context;
    a reminder comes round on the panel, out loud, on the phone and in the conversation."""
    import time as _t
    from datetime import datetime
    from pie_ink import mascot as _mascot
    from pie_ink import notebook as _nb
    from pie_ink import reminders as _rm
    print("the notebook and the reminders: one bot that remembers, and tells you when")
    R = _rm.REMINDERS
    R.start()                                                     # (app.py starts it when it serves; here we import it)
    real = _nb.PATH, _rm.PATH, pieapp._tg_notify, pieapp.audio.say, _rm.SHOW_FOR
    tmp = tempfile.mkdtemp(prefix="pie-ink-memory-")
    _nb.PATH, _rm.PATH = os.path.join(tmp, "notes.json"), os.path.join(tmp, "reminders.json")
    R.items, R.last_fired = [], None
    sent, said = [], []
    pieapp._tg_notify = lambda text, photo=None: sent.append(text)
    pieapp.audio.say = lambda text, *a, **k: said.append(text)
    _rm.SHOW_FOR = 2
    owner = _mascot._owner(pieapp.service.config)

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def ask(text):
        return ((c.post("/api/llm/chat", json={"message": text}).get_json() or {}).get("reply") or "")

    def settle(cond, seconds=8):
        for _ in range(int(seconds * 4)):
            if cond():
                return True
            _t.sleep(0.25)
        return cond()
    mode_before = "clock"                                         # a known screen to come back to (the one before may still be changing)
    try:
        pieapp.service.set_mode(mode_before)
        settle(lambda: pieapp.service.mode_name == mode_before, 6)
        # the clock, read from words (a Sunday at 2 pm)
        now = datetime(2026, 10, 25, 14, 0, 0).timestamp()
        table = {"remind me in 20 minutes to check the oven": ("check the oven", "Sun 14:20", None),
                 "remind me to call mum at 5": ("call mum", "Sun 17:00", None),
                 "remind me tomorrow at 9 to take the bins out": ("take the bins out", "Mon 09:00", None),
                 "set a timer for 10 minutes": ("timer", "Sun 14:10", None), "10 minute timer": ("timer", "Sun 14:10", None),
                 "wake me at 7": ("wake up", "Mon 07:00", None), "every day at 8 remind me to take the pills": ("take the pills", "Sun 20:00", "daily"),
                 "remind me tonight at 8 that the match starts": ("the match starts", "Sun 20:00", None),
                 "remind me on friday to water the plants": ("water the plants", "Fri 09:00", None),
                 "in 2 hours remind me to stretch": ("stretch", "Sun 16:00", None), "remind me at 17:30 the match starts": ("the match starts", "Sun 17:30", None),
                 "remind me in an hour and a half to leave": ("leave", "Sun 15:30", None), "remember to call the dentist at 3": ("call the dentist", "Sun 15:00", None),
                 "remind me at half past 5 to leave": ("leave", "Sun 17:30", None), "set an alarm for 6am": ("wake up", "Mon 06:00", None),
                 "remind me every morning at 7 to stretch": ("stretch", "Mon 07:00", "daily"), "tell me at 4 that the cake is done": ("the cake is done", "Sun 16:00", None)}
        bad = {}
        for text, (what, when, rep_) in table.items():
            got = _rm.parse(text, now)
            if not got or got["kind"] != "set" or got["text"] != what or got.get("repeat") != rep_ \
                    or datetime.fromtimestamp(got["at"]).strftime("%a %H:%M") != when:
                bad[text] = got and {k: got[k] for k in ("text", "repeat")} | {"at": got["at"] and datetime.fromtimestamp(got["at"]).strftime("%a %H:%M")}
        expect("times read from words: in, at, am/pm, tomorrow, tonight, a weekday, half past, every day, a timer, an alarm", not bad, bad)
        others = {"what reminders do I have": "list", "cancel the timer": "cancel", "cancel all reminders": "cancel_all", "snooze": "snooze",
                  "again in 15 minutes": "snooze", "remind me to buy milk": "set", "timer": "set"}
        bad = {t: _rm.parse(t, now) for t, k in others.items() if (_rm.parse(t, now) or {}).get("kind") != k}
        expect("…and the other asks", not bad, bad)
        nots = ["remember that the bins go out on tuesday", "remember my sister is called ana", "take a picture", "show me the latest photo",
                "what are you doing", "turn on the tv", "what's the weather"]
        bad = [t for t in nots if _rm.parse(t, now)]
        expect("…leaving notes and the rest alone", not bad, bad)
        # the notebook
        expect("'remember that …' is kept", ask("remember that the bins go out on tuesday") == "Noted: the bins go out on tuesday.")
        expect("…and again", ask("remember my sister is called ana") == "Noted: my sister is called ana.")
        expect("'what do you remember' reads it back", ask("what do you remember?").startswith("I remember 2 things:\n• the bins go out on tuesday\n• my sister is called ana"))
        words = _mascot.context(pieapp.service.config)
        expect("the bot has it in every answer", any(ln.startswith(f"Things {owner} asked you to remember") and "sister is called ana" in ln for ln in words),
               [w for w in words if "remember" in w])
        expect("'forget the bins' takes one out", ask("forget the bins") == "Forgotten: the bins go out on tuesday." and len(_nb.notes()) == 1)
        expect("the page's list", [n["text"] for n in (c.get("/api/notes").get_json() or {}).get("notes", [])] == ["my sister is called ana"])
        r = c.post("/api/notes", json={"text": "the wifi password is on the fridge"})
        expect("…adds one", len((r.get_json() or {}).get("notes", [])) == 2)
        nid = (r.get_json() or {})["note"]["id"]
        expect("…and takes one out", len((c.delete(f"/api/notes/{nid}").get_json() or {}).get("notes", [])) == 1)
        expect("Telegram: /remember, /notes, /forget", pieapp._tg_command("/remember the car is in bay 12").startswith("Noted: the car is in bay 12")
               and "bay 12" in pieapp._tg_command("/notes") and pieapp._tg_command("/forget bay 12").startswith("Forgotten: the car is in bay 12"))
        expect("'forget everything'", ask("forget everything") == "Notebook cleared — 1 note gone." and not _nb.notes())
        # a reminder, due in a moment
        reply = ask("remind me in 3 seconds to test the kettle")
        expect("'remind me in 3 seconds to test the kettle'", reply == "I'll remind you in 3 s: test the kettle.", reply)
        now_r = (c.get("/api/now").get_json() or {}).get("reminder") or {}
        expect("…in the sidebar, with when", now_r.get("text") == "test the kettle" and now_r.get("words", "").startswith("test the kettle — in"), now_r)
        words = _mascot.context(pieapp.service.config)
        expect("…and the bot knows it's set", any(ln.startswith(f"Reminders you've set for {owner}: test the kettle") for ln in words))
        settle(lambda: pieapp.llm.latest().get("kind") == "reminder", 10)
        spoken = pieapp.llm.latest()
        expect("it comes round: on the bot's screen", spoken.get("kind") == "reminder" and spoken.get("text") == "Reminder: test the kettle.", spoken)
        expect("…out loud", said[-1:] == ["Reminder: test the kettle."], said)
        expect("…on your phone", sent[-1:] == ["⏰ Reminder: test the kettle."], sent)
        expect("…in the conversation and the events list", any(m["text"] == "Reminder: test the kettle." for m in pieapp.chat.history()[-3:] if m["role"] == "bot")
               and any(e["kind"] == "reminder" for e in pieapp.sight.events(limit=3)))
        expect("…as a card on the panel", pieapp.service.mode_name == "postcard")
        settle(lambda: pieapp.service.mode_name == mode_before, 6)
        expect("…and the panel goes back to what it showed", pieapp.service.mode_name == mode_before, pieapp.service.mode_name)
        expect("…and it's gone from the list", not R.pending())
        reply = ask("snooze")
        expect("'snooze' brings it back in ten minutes", reply == "Again in 10 min: test the kettle." and len(R.pending()) == 1, reply)
        reply = ask("cancel the reminder about the kettle")
        expect("'cancel the reminder about the kettle'", reply.startswith("Cancelled: test the kettle") and not R.pending(), reply)
        # timers, the page, Telegram, several at once
        reply = ask("set a timer for 10 minutes")
        expect("a timer", reply == "Timer set — in 10 min." and R.pending()[0]["text"] == "timer", reply)
        reply = ask("cancel the timer")
        expect("…cancelled by name", reply.startswith("Cancelled: 10-minute timer") and not R.pending(), reply)
        r = c.post("/api/reminders", json={"text": "tomorrow at 9 bins out"})
        expect("the page sets one in its own words", r.status_code == 200 and (r.get_json() or {}).get("reply", "").startswith("I'll remind you tomorrow at 9:00 am: bins out"), r.get_json())
        expect("…but not without a time", c.post("/api/reminders", json={"text": "bins out"}).status_code == 400)
        rid = R.pending()[0]["id"]
        expect("…and cancels one", not (c.delete(f"/api/reminders/{rid}").get_json() or {}).get("reminders"))
        tg = [pieapp._tg_command("/remind in 2 hours to stretch"), pieapp._tg_command("/timer 5"), pieapp._tg_command("/reminders")]
        expect("Telegram: /remind, /timer, /reminders", tg[0].startswith(("I'll remind you at", "I'll remind you tomorrow at"))
               and tg[0].endswith(": stretch.")
               and tg[1].startswith("Timer set — in 5 min") and tg[2].startswith("2 set:"), tg)
        reply = ask("cancel the reminder")
        expect("two set, no words: it asks which", reply.startswith("Which one?"), reply)
        expect("'cancel all reminders'", ask("cancel all reminders") == "Cancelled 2 reminders." and not R.pending())
        expect("a reminder with no time asks when", ask("remind me to buy milk") == "When? Say 'remind me in 20 minutes to buy milk' or 'at 5'.")
        # kept across a restart, and one missed while the Pi was off is said when it's back
        R.add("water the plants", _t.time() - 300, None, "test")
        again = _rm.Reminders()
        expect("kept on disk for a restart", [r["text"] for r in again.pending()] == ["water the plants"])
        fired = []
        again.on_due = lambda r: fired.append(again.due_words(r))
        again._tick()
        expect("…and one missed while the Pi was off is said as soon as it's back", fired == ["Reminder (from 5 min ago — I was off): water the plants."], fired)
        R.items = []
        R._save()
        # the face
        from pie_ink.modes.simple import bot_face
        expect("the face when one goes off", bot_face("Reminder: test the kettle.", "reminder")[1] == "surprised")
    finally:
        _nb.PATH, _rm.PATH, pieapp._tg_notify, pieapp.audio.say, _rm.SHOW_FOR = real
        R.items, R.last_fired = [], None
        pieapp.service.set_mode(mode_before)


FAKE_PIPER = r"""#!/usr/bin/env python3
# a stand-in for piper --output-raw: 0.8 s of tone per input line, streamed; "slow" is a voice that makes
# speech more slowly than it talks (a medium one on a Zero 2 W): 1.2 s to make each second, sent all at once
import sys, os, time, math, struct
mode = os.environ.get("FAKE_PIPER", "stream")           # stream | exit-after-one | slow
tone = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 22050))) for i in range(int(22050 * 0.8)))
time.sleep(0.2)                                          # loading the voice
for line in sys.stdin:
    if not line.strip():
        continue
    if mode == "slow":
        secs = max(0.3, 0.065 * len(line.strip()))
        time.sleep(1.2 * secs)
        n = int(22050 * secs) * 2
        sys.stdout.buffer.write((tone * (n // len(tone) + 1))[:n]); sys.stdout.buffer.flush()
        continue
    for i in range(0, len(tone), 4096):
        sys.stdout.buffer.write(tone[i:i + 4096]); sys.stdout.buffer.flush(); time.sleep(0.005)
    if mode == "exit-after-one":
        break
"""

FAKE_APLAY = r"""#!/usr/bin/env python3
# a stand-in for aplay: counts what it gets; FAKE_APLAY_FAIL="plughw:1,0=busy" refuses that output
import sys, os, time
if "-l" in sys.argv:
    print("**** List of PLAYBACK Hardware Devices ****")
    print("card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]")
    print("card 1: UACDemoV10 [UACDemoV1.0], device 0: USB Audio [USB Audio]")
    sys.exit(0)
dev = sys.argv[sys.argv.index("-D") + 1] if "-D" in sys.argv else ""
# a card by name is the same card as by number, as with the real aplay
by_name = {"plughw:CARD=Headphones,DEV=0": "plughw:0,0", "plughw:CARD=UACDemoV10,DEV=0": "plughw:1,0"}
if dev.startswith("plughw:CARD=") and dev not in by_name:
    sys.stderr.write(f"aplay: main:831: audio open error: No such file or directory\n")
    sys.exit(1)
for rule in filter(None, os.environ.get("FAKE_APLAY_FAIL", "").split(";")):
    d, _, how = rule.partition("=")
    if d == by_name.get(dev, dev):
        sys.stderr.write("aplay: main:834: audio open error: Device or resource busy\n")
        sys.exit(1)
n = 0
while True:
    b = sys.stdin.buffer.read(8192)
    if not b:
        break
    n += len(b)
with open(os.environ["FAKE_APLAY_LOG"], "a") as fh:
    fh.write(f"{dev or 'default'} {n}\n")
"""


def speaker_check():
    """The speaker's worker, against a stand-in Piper and aplay: every press
    plays (the 'works once' bug), every sentence plays even with a Piper that
    exits after each line, and a refused output is retried and then swapped
    for another — with the outcome reported, not guessed."""
    import json
    import os
    import stat
    import tempfile
    from pie_ink import audio
    root = tempfile.mkdtemp(prefix="pie-ink-speaker-")
    fakebin = os.path.join(root, "bin")
    os.makedirs(fakebin)
    for name, body in (("piper", FAKE_PIPER), ("aplay", FAKE_APLAY)):
        path = os.path.join(fakebin, name)
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    voices = os.path.join(root, "voices")
    os.makedirs(voices)
    with open(os.path.join(voices, "fake.onnx"), "wb") as fh:
        fh.write(b"not a model")
    with open(os.path.join(voices, "fake.onnx.json"), "w") as fh:
        json.dump({"audio": {"sample_rate": 22050}}, fh)
    log_path = os.path.join(root, "aplay.log")
    keep = {k: os.environ.get(k) for k in ("PATH", "FAKE_PIPER", "FAKE_APLAY_FAIL", "FAKE_APLAY_LOG")}
    old_dirs = audio.VOICE_DIR, audio.TTS_CACHE
    os.environ["PATH"] = fakebin + os.pathsep + os.environ.get("PATH", "")
    os.environ["FAKE_APLAY_LOG"] = log_path
    os.environ["FAKE_PIPER"] = "stream"
    os.environ.pop("FAKE_APLAY_FAIL", None)
    audio.VOICE_DIR, audio.TTS_CACHE = voices, os.path.join(root, "tts")
    conf = {"piper_voice": "fake", "device": "plughw:1,0", "piper_speed": 1.0}

    def played():
        try:
            with open(log_path) as fh:
                return [ln.split() for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            return []

    def clear():
        try:
            os.remove(log_path)
        except OSError:
            pass

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + detail}")
        if not cond:
            FAILED.append(name)

    spk = audio._Speaker()
    try:
        for n in range(3):
            okay, why = spk.say("Hello, this is a test.", conf, wait=True, timeout=30)
            expect(f"speaker press {n + 1}" + (" (from the cache)" if n else ""),
                   okay and len(played()) == n + 1 and played()[-1] == ["plughw:1,0", "35280"], f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_PIPER"] = "exit-after-one"
        spk.shutdown()                                     # so the next line starts a Piper of that kind
        okay, why = spk.say("One. Two, with version 2.5 in it! Three?", conf, wait=True, timeout=30)
        expect("three sentences with a Piper that exits after each line",
               okay and played() and played()[-1][1] == str(35280 * 3), f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_APLAY_FAIL"] = "plughw:1,0=busy"
        okay, why = spk.say("Busy output.", conf, wait=True, timeout=60)
        expect("a busy output is retried, then the default output takes the line",
               okay and "instead" in why and played() and played()[-1] == ["default", "35280"], f"{okay} {why} {played()}")
        clear()
        os.environ["FAKE_APLAY_FAIL"] = "plughw:1,0=busy;=busy;plughw:0,0=busy"
        okay, why = spk.say("Nothing works.", conf, wait=True, timeout=60)
        expect("when every output refuses, the button hears why", not okay and "busy" in why, f"{okay} {why}")
        expect("the worker is still alive and not 'speaking'", spk.worker.is_alive() and not spk.speaking)
        # outputs by card name: a camera's microphone plugged in first can't move them
        os.environ.pop("FAKE_APLAY_FAIL", None)
        clear()
        outs = audio.devices()
        expect("outputs are listed by card name, with their number as the alias",
               [d["id"] for d in outs] == ["", "plughw:CARD=Headphones,DEV=0", "plughw:CARD=UACDemoV10,DEV=0", "pitato"]
               and outs[2].get("alias") == "plughw:1,0", str(outs))
        expect("either spelling is the same device", audio.same_device("plughw:1,0", "plughw:CARD=UACDemoV10,DEV=0")
               and not audio.same_device("plughw:0,0", "plughw:CARD=UACDemoV10,DEV=0")
               and audio.card_of("plughw:CARD=UACDemoV10,DEV=0") == "UACDemoV10" and audio.card_of("plughw:1,0") == "1")
        by_name = dict(conf, device="plughw:CARD=UACDemoV10,DEV=0")
        okay, why = spk.say("By name.", by_name, wait=True, timeout=30)
        expect("an output picked by name plays", okay and played() and played()[-1][0] == "plughw:CARD=UACDemoV10,DEV=0",
               f"{okay} {why} {played()}")
        expect("the fallbacks don't try the same card twice under its other name",
               "plughw:CARD=UACDemoV10,DEV=0" not in audio._fallback_outputs("plughw:1,0"), str(audio._fallback_outputs("plughw:1,0")))
        # "Play on this device": while a page is listening, a line goes to it in pieces, not to aplay
        import time as _t
        from pie_ink import here as _here
        hooks = _here.HERE.on_start, _here.HERE.on_stop
        _here.HERE.on_start = _here.HERE.on_stop = None
        try:
            clear()
            seen = _here.HERE.seq
            _here.HERE.seen("smoke-speaker")
            t0 = _t.time()
            okay, why = spk.say("One. Two.", conf, wait=True, timeout=30)
            took = _t.time() - t0
            says = [e for e in _here.HERE.events if e["seq"] > seen and e["kind"] == "say"]
            clips = [_here.HERE.clip(e["clip"]) or b"" for e in says]
            expect("while a page plays the sound, a line goes to it as WAV pieces, not to aplay, and takes as long",
                   okay and "phone or computer" in why and not played() and len(says) >= 2
                   and sum(len(x) - 44 for x in clips) == 35280 * 2 and all(x[:4] == b"RIFF" and x[8:12] == b"WAVE" for x in clips)
                   and len({e["line"] for e in says}) == 1 and took >= 1.5,
                   f"{okay} {why} {played()} {len(says)} {[len(x) for x in clips]} {took:.2f}")
            before = _here.HERE.seq
            spk.say("A first line that runs on. And on. And on.", conf)
            _t.sleep(0.6)
            okay, why = spk.say("Second.", conf, wait=True, timeout=30)
            evs = [e for e in _here.HERE.events if e["seq"] > before]
            lines = sorted({e["line"] for e in evs if e["kind"] == "say"})
            expect("a newer line hushes the one before on the page", okay and len(lines) == 2
                   and any(e["kind"] == "hush" and e["line"] == lines[0] for e in evs), str(evs))
            # a voice slower than speech: it learns how slow from one line, then holds the next back just long
            # enough that, once it speaks, it doesn't stop between sentences (it used to, for seconds)
            os.environ["FAKE_PIPER"] = "slow"
            spk.shutdown()
            spk._paces = {}                              # a voice it hasn't heard yet
            spk.say("One. And then two.", conf, wait=True, timeout=60)
            pace = spk.learnt_pace()
            before = _here.HERE.seq
            okay, why = spk.say("Hey! This one is a good deal longer than the first.", conf, wait=True, timeout=60)
            pieces = [(e["at"], e["secs"]) for e in _here.HERE.events if e["seq"] > before and e["kind"] == "say"]
            gaps, end = [], None                        # the page plays each piece when it comes, or when the last ends
            for at, secs in pieces:
                if end is not None and at > end:
                    gaps.append(round(at - end, 2))
                end = max(end or 0.0, at) + secs
            expect(f"a slow voice waits a moment, then says the whole line without stopping (pace {pace}, "
                   f"first sound after {spk.first_ms / 1000:.1f}s)",
                   okay and pace and 1.0 < pace < 1.6 and pieces and not gaps and spk.first_ms > 2000,
                   f"{okay} {why} pace {pace} gaps {gaps} pieces {pieces}")
            os.environ["FAKE_PIPER"] = "stream"
            spk.shutdown()
            _here.HERE.leave("smoke-speaker")
            clear()
            okay, why = spk.say("Back.", conf, wait=True, timeout=30)
            expect("when the page goes, the Pi's own speaker has it again", okay and played() and played()[-1][0] == "plughw:1,0",
                   f"{okay} {why} {played()}")
        finally:
            _here.HERE.leave("smoke-speaker")
            _here.HERE.on_start, _here.HERE.on_stop = hooks
    finally:
        spk.shutdown()
        audio.VOICE_DIR, audio.TTS_CACHE = old_dirs
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def sound_probe_check(pieapp):
    """"Speaker or mic missing?" on Pis like the one it was written for: a Zero 2 W, a four-port hub, a
    memory stick and an OBSBOT on it, no speaker or mic on the bus. Once with the power sagging; once as the real one
    was — power fine, dwc2 switched on in config.txt, two ports failing with -32 while the camera streams."""
    from pie_ink import audio, camera
    hub = {"bus": "1-1", "vid": "1a40", "pid": "0101", "name": "USB 2.0 Hub", "classes": ["hub"], "speed": "480", "power_ma": "100mA"}
    stick = {"bus": "1-1.3", "vid": "0781", "pid": "5567", "name": "SanDisk Cruzer Blade", "classes": ["storage"], "speed": "480", "power_ma": "200mA"}
    obsbot = {"bus": "1-1.2", "vid": "3564", "pid": "fef8", "name": "Remo Tech OBSBOT Tiny 2", "classes": ["audio", "hid", "video"],
              "speed": "480", "power_ma": "500mA"}
    base = {"/proc/device-tree/model": "Raspberry Pi Zero 2 W Rev 1.0\0", "/sys/bus/usb/devices/1-1/maxchild": "4",
            "/sys/bus/usb/devices/1-1.2/product": "OBSBOT Tiny 2"}
    hdmi = [{"id": "", "label": "System default"}, {"id": "plughw:CARD=vc4hdmi,DEV=0", "label": "vc4-hdmi", "alias": "plughw:0,0"}]
    stock = "dtparam=audio=on\n[cm4]\notg_mode=1\n[cm5]\ndtoverlay=dwc2,dr_mode=host\n[all]\n"
    scenarios = [
        ("power sagging", [hub, stick, obsbot], dict(base, **{
            "/proc/asound/cards": (" 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n                      vc4-hdmi\n"
                                   " 1 [Tiny2          ]: USB-Audio - OBSBOT Tiny 2\n                      OBSBOT at usb-3f980000.usb-1.2\n"),
            "/proc/asound/card1/usbid": "3564:fef8", "/sys/devices/platform/soc/soc:firmware/get_throttled": "50005",
            "/boot/firmware/config.txt": stock}),
         ["usb 1-1.3: Product: UACDemoV1.0", "hwmon hwmon1: Undervoltage detected!", "usb 1-1.3: USB disconnect, device number 5",
          "usb 1-1.4: device descriptor read/64, error -71"],
         {"aplay": hdmi, "arecord": [{"id": "", "label": "System default"},
                                     {"id": "plughw:CARD=Tiny2,DEV=0", "label": "OBSBOT Tiny 2", "alias": "plughw:1,0"}]},
         ["doesn't see a USB speaker or microphone", "port 4 of the hub", "error -71", "UACDemoV1.0 came up earlier",
          "under-voltage", "hub with its own power adapter", "OBSBOT Tiny 2, under Microphone",
          "pick them again under Output and Microphone"], False),
        ("the real one: power fine, dwc2, -32", [hub, dict(obsbot, classes=["video"]), stick], dict(base, **{
            "/proc/asound/cards": " 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n                      vc4-hdmi\n",
            "/sys/devices/platform/soc/soc:firmware/get_throttled": "0",
            "/boot/firmware/config.txt": stock + "dtoverlay=dwc2\n"}),
         ["usb 1-1.4: device descriptor read/64, error -32", "usb 1-1-port4: attempt power cycle",
          "usb 1-1.4: new full-speed USB device number 13 using dwc2", "usb 1-1.4: device not accepting address 13, error -32",
          "usb 1-1-port4: unable to enumerate USB device", "usb 1-1.2: new high-speed USB device number 15 using dwc2",
          "usb 1-1.2: Product: OBSBOT Tiny 2", "usb 1-1.1: new full-speed USB device number 16 using dwc2",
          "usb 1-1.1: device descriptor read/64, error -32", "usb 1-1.1: device not accepting address 19, error -32",
          "usb 1-1-port1: unable to enumerate USB device"],
         {"aplay": hdmi, "arecord": [{"id": "", "label": "System default"}]},
         ["ports 1 and 4 of the hub", "error -32", "own power is fine", "full-speed", "dwc2 driver", "Watch the USB bus",
          "Pausing the camera", "take \u201cdtoverlay=dwc2\u201d out of /boot/firmware/config.txt"], True),
    ]
    saved = (camera.usb_devices, camera._read, camera._kernel_lines, audio.entries, camera.CAMERA.status, audio._kernel_64)
    audio._kernel_64 = lambda: False                     # a 32-bit system first, where dwc_otg is there to go back to
    try:
        for name, usb, files, kernel, lists, want, crowded in scenarios:
            camera.usb_devices = lambda usb=usb: [dict(u) for u in usb]
            camera._read = lambda p, files=files: files.get(p, "")
            camera._kernel_lines = lambda limit=30, keys=(), kernel=kernel: (kernel, None)
            audio.entries = lambda tool="aplay", fresh=False, lists=lists: lists[tool]
            camera.CAMERA.status = lambda: {"running": True, "id": "usb:/dev/video0", "age": 0.1}
            r = audio.probe({"audio": {"device": "plughw:1,0"}, "listen": {"device": "plughw:2,0"}}
                            if not crowded else {})
            v, text = r["verdict"], audio.probe_text(r)
            missing = [w for w in want if w not in v]
            okay = not missing and text.startswith(v) and "4 ports, 2 in use" in text and r["crowded"] == crowded
            print(f"  {'ok  ' if okay else 'FAIL'} 'Speaker or mic missing?' — {name}: "
                  + ("the right cause, and what to do" if okay else f"missing {missing}; crowded {r['crowded']}; {v}"))
            if not okay:
                FAILED.append(f"sound probe: {name}")
        files = dict(scenarios[1][2], **{"/boot/firmware/config.txt": stock})
        camera._read = lambda p: files.get(p, "")
        if audio.probe({})["overlay"] is not None:
            print("  FAIL a stock config.txt (dwc2 only under [cm5]) isn't this Pi switching to dwc2")
            FAILED.append("sound probe: stock config")
        # the same Pi on a 64-bit system: dwc_otg doesn't work there, so dwc2 stays — never "take it out"
        audio._kernel_64 = lambda: True
        files = dict(scenarios[1][2], **{"/boot/firmware/config.txt": stock + "dtoverlay=dwc2,dr_mode=host\n"})
        camera._read = lambda p: files.get(p, "")
        r = audio.probe({})
        v = r["verdict"]
        okay = ("Keep “dtoverlay=dwc2,dr_mode=host” in /boot/firmware/config.txt" in v and " out of " not in v
                and "worse at that" not in v and "Pausing the camera" in v and "Play on this device" in v
                and "64-bit" in audio.probe_text(r))
        print(f"  {'ok  ' if okay else 'FAIL'} 'Speaker or mic missing?' — the same Pi, 64-bit: it says to keep dwc2 "
              "(the driver there is), not to take it out")
        if not okay:
            print(f"       {v}")
            FAILED.append("sound probe: 64-bit keeps dwc2")
    finally:
        (camera.usb_devices, camera._read, camera._kernel_lines, audio.entries, camera.CAMERA.status,
         audio._kernel_64) = saved


def any_screen_check(pieapp):
    """Any of the screens on the second screen, laid out again for its size so it fills it — and "Same as
    the e-ink" draws what the e-ink is showing that way, not the e-ink's frame squeezed in with bars."""
    from PIL import Image
    from pie_ink import lcd_screens
    config = pieapp.service.config
    ids = [k for k, _ in lcd_screens.LABELS]
    okay = all(n in ids for n in ("mirror", "me", "message", "earnings", "music", "reader", "postcard")) and ids[0] == "mirror"
    print(f"  {'ok  ' if okay else 'FAIL'} the second screen offers every screen, and 'Same as the e-ink' first")
    if not okay:
        FAILED.append("lcd every screen")
    bad = []
    for w, h in ((240, 280), (170, 320), (280, 240), (320, 170)):
        for name in ("me", "message", "earnings", "music", "reader", "postcard"):
            img = lcd_screens.render({"screen": name, "light_mode": False}, config=config, size=(w, h))
            if img.size != (w, h) or img.mode != "RGB":
                bad.append(f"{name} {w}x{h}: {img.size} {img.mode}")
    print(f"  {'ok  ' if not bad else 'FAIL'} each drawn at the LCD's own size, both models, both ways round")
    if bad:
        FAILED.append("lcd screens sized")
        print("       " + "; ".join(bad[:6]))
    real = lcd_screens.MAIN_MODE
    real_screens = dict(lcd_screens.SCREENS)
    try:
        drawn = []

        def stand_in(name):
            def screen(conf, now=None, size=None, config=None):
                drawn.append((name, tuple(size)))
                return Image.new("RGB", size, (1, 2, 3))
            return screen
        for name in ("me", "weather", "chat"):
            lcd_screens.SCREENS[name] = stand_in(name)
        lcd_screens.WITH_CONFIG.update(lcd_screens.SCREENS[n] for n in ("me", "weather", "chat"))
        for main in ("me", "weather", "groupchat"):
            lcd_screens.MAIN_MODE = lambda main=main: main
            lcd_screens.render({"screen": "mirror", "light_mode": False}, config=config, size=(240, 280))
        okay = drawn == [("me", (240, 280)), ("weather", (240, 280)), ("chat", (240, 280))]
        lcd_screens.SCREENS.update(real_screens)
        lcd_screens.MAIN_MODE = lambda: "off"
        off = lcd_screens.render({"screen": "mirror", "light_mode": False}, config=config, size=(240, 280))
        okay = okay and off.getextrema() == ((0, 0), (0, 0), (0, 0))
        lcd_screens.MAIN_MODE = lambda: "me"
        me = lcd_screens.render({"screen": "mirror", "light_mode": False}, config=config, size=(170, 320))
        okay = okay and me.size == (170, 320) and me.getbbox() is not None
        print(f"  {'ok  ' if okay else 'FAIL'} 'Same as the e-ink' redraws the e-ink's screen for the LCD (its own colour version where it has one; dark when it's off)")
        if not okay:
            FAILED.append("lcd same as the e-ink")
            print(f"       {drawn}")
        okay = lcd_screens.interval({"screen": "me", "fps": 2}) >= lcd_screens.MAIN_EVERY
        print(f"  {'ok  ' if okay else 'FAIL'} …and isn't redrawn more often than it changes")
        if not okay:
            FAILED.append("lcd main screens interval")
    finally:
        lcd_screens.MAIN_MODE = real
        lcd_screens.SCREENS.update(real_screens)
    # Settings → Screen: one screen or two, and which LCD
    here = os.path.dirname(os.path.abspath(__file__))
    page = open(os.path.join(here, "templates", "index.html")).read()
    js = open(os.path.join(here, "static", "app.js")).read()
    okay = ('id="screens-count"' in page and 'data-value="2"' in page and 'id="lcd-model-pick"' in page
            and 'data-value="1.69"' in page and 'data-value="1.9"' in page
            and 'setDraft("lcd.available", two)' in js and 'setDraft("lcd.model", b.dataset.value)' in js)
    print(f"  {'ok  ' if okay else 'FAIL'} Settings → Screen asks one screen or two, and with two, the 1.69\" or the 1.9\"")
    if not okay:
        FAILED.append("screens count setting")


def share_footer_check():
    """A share's market — open or closed — is said in the footer, where there's room, not in the header."""
    import time as _t
    from pie_ink import settings as _cfg
    from pie_ink.modes import crypto as _cm
    config = _cfg.load()
    item = {"id": "AAPL", "symbol": "AAPL", "name": "Apple", "kind": "stock", "show": True}
    bad = []
    for opened, word in ((True, "OPEN"), (False, "CLOSED")):
        q = {"kind": "stock", "money": "$", "currency": "USD", "exchange": "NASDAQ", "open": opened, "price": 262.82,
             "prev_close": 259.58, "volume": 45_200_000, "ath": 277.32, "low_52w": 169.21, "from_ath": -5.2, "decimals": 2,
             "change": {"24h": 1.2, "7d": 2.0, "30d": 4.1, "200d": 12.0, "1y": 20.1}, "graph": [250 + i % 7 for i in range(96)],
             "fetched": _t.time()}
        for w, h in ((250, 122), (264, 176), (480, 280)):
            m = _cm.CryptoMode(config, {"width": w, "height": h, "kind": "eink"})
            f = m.frame()
            said = []
            real_text = f.draw.text
            f.draw.text = lambda xy, text, *a, **k: (said.append((xy[1], text)), real_text(xy, text, *a, **k))[1]
            m._render_large(f, item, q, 0, False)
            footer = [t for y, t in said if y > h - 30]
            header = [t for y, t in said if y < 2]
            if not any(word in t for t in footer) or any("CLOSED" in t or "OPEN" in t for t in header):
                bad.append(f"{w}x{h} {word}: header {header} footer {footer}")
    print(f"  {'ok  ' if not bad else 'FAIL'} a share's OPEN or CLOSED is in the footer, not the header, on every e-ink")
    if bad:
        FAILED.append("share market in the footer")
        print("       " + "; ".join(bad[:3]))


def moods_check(pieapp, c, ha):
    """"I'm bored": a game from a Pietato's Games suggested ("another", "yes" starts it), or the TV; "play a
    random game" starts one; "it's dark in here" puts this room's lights on (Home Assistant)."""
    import time as _t
    from pie_ink import moods as _mo, pitato as _pt, shelf as _sh
    print("how you feel: bored (a game, or the TV), dark in here (the lights)")

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def ask(text):
        return ((c.post("/api/llm/chat", json={"message": text}).get_json() or {}).get("reply") or "")
    table = {"i'm bored": "bored", "ugh, I'm so bored": "bored", "im bored": "bored", "nothing to do": "bored",
             "are you bored?": None, "the kids are bored of it": None, "play a random game": "random_game",
             "start a retro game on the pitato": "random_game", "it's dark in here": "dark", "it's getting dark": "dark",
             "is it dark outside?": None, "turn on dark mode": None, "the screen is too dark": None, "i can't see a thing": "dark"}
    got = {t: (_mo.parse(t) or {}).get("kind") for t in table}
    expect("the words for bored, a random game and dark — and not the questions", got == table,
           {t: (g, table[t]) for t, g in got.items() if g != table[t]})
    answers = {"yes": "yes", "sure, go on": "yes", "another one": "another", "something else": "another", "nah": "no",
               "the tv": "tv", "no, the tv please": "tv", "what's the weather like tomorrow in town": None}
    got = {t: _mo.answer(t) for t in answers}
    expect("…and the answers to a suggestion", got == answers, {t: (g, answers[t]) for t, g in got.items() if g != answers[t]})
    fake = _FakePitato()
    now = _t.time()
    fake.shelves["games"] = {"NES/Super Mario Bros. 3 (USA) (Rev 1).nes": (now - 9000, b"NES"),
                             "SNES/Super Metroid (Japan, USA).sfc": (now - 8000, b"SFC"),
                             "PS1/Crash Bandicoot (USA).cue": (now - 7000, b"FILE"), "PS1/Crash Bandicoot (USA).bin": (now - 7000, b"BIN")}
    fake.start()
    P = _pt.PITATO
    real = _pt.PORT, _pt._my_ips
    _pt.PORT = fake.port
    _pt._my_ips = lambda: set()
    P.seen.clear()
    P.seen["127.0.0.1"] = {"ip": "127.0.0.1", "name": "pitato-test", "web": True, "locked": False, "accent": "", "first": now,
                           "last": now, "asks": 1, "greeted": now, "probed": now, "here": False}
    _sh._cache.clear()
    try:
        games = _sh.games(fresh=True)
        titles = sorted(g["title"] for g in games)
        expect("a Pietato's games are on the shelves, named as they'd be said (a disc's track left out)",
               titles == ["Crash Bandicoot", "Super Mario Bros. 3", "Super Metroid"], titles)
        reply = ask("I'm bored")
        first = next((t for t in titles if t in reply), None)
        expect("'I'm bored': a game from its Games, and the TV too (Home Assistant has one)",
               reply.startswith("Bored? How about ") and first and "pitato-test" in reply and "TV" in reply, reply)
        reply = ask("another one")
        second = next((t for t in titles if t in reply), None)
        expect("…'another one': a different one", second and second != first, reply)
        n = len(fake.shown)
        reply = ask("yes")
        expect("…'yes': it starts on the Pietato", reply.startswith(f"Starting {second} on pitato-test")
               and len(fake.shown) == n + 1 and fake.shown[-1][0] == "games", (reply, fake.shown[-1:]))
        expect("…and a 'yes' later isn't taken for it", "Starting" not in ask("yes"))
        ask("I'm bored")
        calls = len(ha.calls)
        reply = ask("the tv")
        expect("…or 'the tv': the TV comes on", "TV" in reply and any(cl[1] == "turn_on" for cl in ha.calls[calls:]), (reply, ha.calls[calls:]))
        reply = ask("play a random game")
        expect("'play a random game' starts one", reply.startswith("Starting ") and any(t in reply for t in titles), reply)
        reply = ask("play super metroid")
        expect("'play super metroid' finds it in its Games and starts it", reply.startswith("Starting Super Metroid on pitato-test"), reply)
        # the lights
        calls = len(ha.calls)
        reply = ask("it's dark in here")
        expect("'it's dark in here': the lights that are off come on", reply == "Let there be light — Kitchen Light on."
               and ha.calls[calls:] == [("light", "turn_on", {"entity_id": ["light.kitchen_light"]})], (reply, ha.calls[calls:]))
        c.put("/api/settings", json={"me": {"location": "lamp"}})
        calls = len(ha.calls)
        reply = ask("it's so dark in here")
        expect("…this room's, when this Pi says where it is (and up to full when they're on already)",
               reply == "Lamp is on already — turned up full."
               and ha.calls[calls:] == [("light", "turn_on", {"entity_id": ["light.lamp"], "brightness_pct": 100})], (reply, ha.calls[calls:]))
        c.put("/api/settings", json={"me": {"location": ""}})
    finally:
        fake.stop()
        _pt.PORT, _pt._my_ips = real
        P.seen.clear()
        _sh._cache.clear()


def one_screen_check():
    """Most Pis have the one screen: a second one is only offered once setup.sh has been told of it
    (lcd.available). Out of the box it's off; a config that already had the LCD switched on from
    before the question existed keeps it; the page hides every second-screen part when it's off."""
    import tempfile as _tf
    import yaml as _yaml
    from pie_ink import settings as _cfg
    saved = _cfg.CONFIG_PATH
    folder = _tf.mkdtemp(prefix="pie-ink-screens-")
    try:
        _cfg.CONFIG_PATH = os.path.join(folder, "config.yaml")
        fresh = _cfg.load()["lcd"]
        _yaml.safe_dump({"lcd": {"enabled": True, "model": "1.9"}}, open(_cfg.CONFIG_PATH, "w"))
        kept = _cfg.load()["lcd"]
        _yaml.safe_dump({"lcd": {"enabled": True, "available": False}}, open(_cfg.CONFIG_PATH, "w"))
        said_no = _cfg.load()["lcd"]
        okay = fresh["available"] is False and kept["available"] is True and kept["enabled"] is True and said_no["available"] is False
        print(f"  {'ok  ' if okay else 'FAIL'} out of the box one screen; an LCD switched on before the question keeps its place; "
              f"setup.sh's 'no' is kept as said")
        if not okay:
            FAILED.append("one screen setting")
            print(f"       fresh {fresh.get('available')} kept {kept.get('available')} said_no {said_no.get('available')}")
    finally:
        _cfg.CONFIG_PATH = saved
    css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.css")).read()
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js")).read()
    okay = "body.one-screen #lcd-card" in css and "body.one-screen #draw-target-row" in css and 'getSetting("lcd.available")' in js
    print(f"  {'ok  ' if okay else 'FAIL'} the page hides the second screen's card, settings and drawing target with one screen")
    if not okay:
        FAILED.append("one screen page")
    sh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup.sh")).read()
    okay = "--lcd" in sh and "--no-lcd" in sh and "lcd" in sh and '"available"' in sh and "second screen" in sh.lower()
    print(f"  {'ok  ' if okay else 'FAIL'} setup.sh asks about a second screen (or takes --lcd / --no-lcd) and keeps the answer")
    if not okay:
        FAILED.append("one screen setup")


class _FakeRoom:
    """Another PiE-ink of yours, as far as pie_ink/household.py looks at it: its chat, its notebook
    and its reminders by the web API, and an inbox. What was deleted and what arrived is kept."""

    def __init__(self):
        import json
        import threading
        import time as _time
        from http.server import BaseHTTPRequestHandler, HTTPServer
        fake = self
        now = _time.time()
        self.chat = [{"role": "you", "text": "the oven is on", "how": "typed", "ts": now - 60},
                     {"role": "bot", "text": "I'll keep an eye on the clock.", "how": "typed", "ts": now - 50}]
        self.notes = [{"id": "n1", "text": "the bins go out on tuesday", "ts": now - 900, "how": "typed"},
                      {"id": "n2", "text": "the wifi password is on the fridge", "ts": now - 800, "how": "typed"}]
        self.reminders = [{"id": "r1", "text": "water the plants", "at": now + 3600, "made": now - 100, "words": "water the plants — at 5"}]
        self.deleted, self.inbox = [], []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/chat":
                    return self._send(200, {"ok": True, "history": fake.chat})
                if self.path == "/api/notes":
                    return self._send(200, {"ok": True, "notes": fake.notes})
                if self.path == "/api/reminders":
                    return self._send(200, {"ok": True, "reminders": fake.reminders})
                self._send(404, {"ok": False, "error": "not found"})

            def do_DELETE(self):
                fake.deleted.append(self.path)
                if self.path.startswith("/api/notes/"):
                    fake.notes = [n for n in fake.notes if n["id"] != self.path.rsplit("/", 1)[-1]]
                    return self._send(200, {"ok": True})
                if self.path.startswith("/api/reminders/"):
                    fake.reminders = [r for r in fake.reminders if r["id"] != self.path.rsplit("/", 1)[-1]]
                    return self._send(200, {"ok": True})
                self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/inbox":
                    fake.inbox.append(body)
                    return self._send(200, {"ok": True})
                self._send(404, {"ok": False, "error": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


def rooms_check(pieapp, c):
    """Another PiE-ink of yours on the network is another room of the one bot's house, not a
    second bot: it's welcomed once, the bot knows what's on there and what was said there, the
    notebook and the reminders are one across the rooms, and the one bot never chats with itself."""
    import tempfile as _tf
    import time as _t
    from pie_ink import friends as _fr, household as _hh, mascot as _mascot, notebook as _nb, reminders as _rm, social as _so
    from pie_ink.modes.simple import bot_face
    print("another PiE-ink of yours: the house got bigger, not the number of bots")
    H = _hh.HOUSE
    fake = _FakeRoom()
    beacon = {"pie": 1, "id": "room-kitchen-test", "host": "pie-kitchen", "port": fake.port, "version": "x",
              "panel": "2.13\" V4", "mode": "clock", "caps": {"location": "kitchen", "camera": True, "speaker": True}}
    real = _hh.PATH, _nb.PATH, pieapp.audio.say, dict(H.known), dict(H.live)
    tmp = _tf.mkdtemp(prefix="pie-ink-rooms-")
    _hh.PATH, _nb.PATH = os.path.join(tmp, "rooms.json"), os.path.join(tmp, "notes.json")
    H.known.clear(); H.live.clear()
    said = []
    pieapp.audio.say = lambda text, *a, **k: said.append(text)
    owner = _mascot._owner(pieapp.service.config)

    def expect(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
        if not cond:
            FAILED.append(name)

    def settle(cond, seconds=8):
        for _ in range(int(seconds * 4)):
            if cond():
                return True
            _t.sleep(0.25)
        return cond()

    def ask(text):
        return ((c.post("/api/llm/chat", json={"message": text}).get_json() or {}).get("reply") or "")

    def hellos():
        return [m["text"] for m in pieapp.chat.history(60) if "house just got bigger" in m["text"] or "whole again" in m["text"]]
    try:
        before = len(hellos())
        _fr.heard("127.0.0.1", beacon)
        H.start()                                                 # (app.py starts it when it serves; here we import it)
        for r in H.rooms():
            if r["host"] == "pie-kitchen":
                H._notice(r)
        settle(lambda: len(hellos()) > before, 6)
        expect("a new room is welcomed once — 'the house just got bigger: I'm in the kitchen now, too'",
               len(hellos()) == before + 1 and "I'm in the kitchen now, too" in hellos()[-1], str(hellos()[-1:]))
        expect("…with a starry-eyed face", bot_face(hellos()[-1], "room")[1] == "excited")
        expect("…and said out loud (no model here)", any("kitchen" in t for t in said), str(said))
        _fr.heard("127.0.0.1", beacon)
        for r in H.rooms():
            if r["host"] == "pie-kitchen":
                H._notice(r)
        _t.sleep(0.5)
        expect("the same room seen again is not welcomed again", len(hellos()) == before + 1)
        H.look_now()
        room = H.room("kitchen")
        expect("the room is known by its location, 'the kitchen'", room is not None and room["label"] == "the kitchen" and H.is_room("pie-kitchen"))
        ctx = "\n".join(_mascot.context(pieapp.service.config))
        expect("the bot is told it is one creature in several rooms, and what's on in the kitchen",
               "one creature living in" in ctx and "In the kitchen: showing the clock screen; a camera there" in ctx
               and f"{owner} said to you there just now: \u201cthe oven is on\u201d" in ctx and "you said there just now" in ctx, ctx[-600:])
        # one notebook
        _nb.clear()
        expect("the notebook reads the kitchen's notes too", "the bins go out on tuesday" in _nb.words(owner) and "fridge" in _nb.words(owner))
        reply = ask("what do you remember?")
        expect("'what do you remember?' answers for the whole house", "I remember 2 things" in reply and "bins" in reply, reply)
        reply = ask("forget the bins")
        expect("'forget the bins' reaches the kitchen", "Forgotten: the bins go out on tuesday." == reply and "/api/notes/n1" in fake.deleted, reply)
        expect("…and the kitchen's note is gone from what it knows", "bins" not in _nb.words(owner) and "fridge" in _nb.words(owner))
        # one list of reminders
        R = _rm.REMINDERS
        R.items = []
        expect("the reminders list has the kitchen's, marked with its room",
               "water the plants" in R.words(owner) and "(in the kitchen)" in R.words(owner), R.words(owner))
        reply = ask("what reminders do i have?")
        expect("'what reminders do I have?' lists it", "water the plants" in reply and "in the kitchen" in reply, reply)
        reply = ask("cancel the reminder about the plants")
        expect("'cancel the reminder about the plants' reaches the kitchen", reply.startswith("Cancelled: water the plants") and "/api/reminders/r1" in fake.deleted, reply)
        # the one bot never chats with itself
        r = c.post("/api/social/send", json={"kind": "ai", "text": "hello there", "to": "pie-kitchen"})
        expect("a line for 'their bot' isn't sent to a room — there is only the one bot", r.status_code == 400 and "same bot" in (r.get_json() or {}).get("error", ""), r.get_data(as_text=True)[:200])
        r = c.post("/api/social/send", json={"kind": "text", "text": "dinner is ready", "to": "pie-kitchen"})
        expect("a message to the room is delivered", r.status_code == 200 and fake.inbox and fake.inbox[-1]["text"] == "dinner is ready", r.get_data(as_text=True)[:200])
        n = len(pieapp.chat.history(60))
        pieapp._arrived({"kind": "text", "from": "pie-kitchen", "text": "hello from the other room", "id": "m1"})
        settle(lambda: len(pieapp.chat.history(60)) > n, 4)
        expect("a message arriving from the room is worded as from the kitchen", pieapp.chat.history(60)[-1]["text"] == "From the kitchen: hello from the other room", str(pieapp.chat.history(60)[-1]))
        n = len(pieapp.chat.history(60))
        pieapp._arrived({"kind": "ai", "from": "pie-kitchen", "text": "let's chat, bot", "id": "m2", "thread": "t1"})
        _t.sleep(0.5)
        expect("a line 'for the bot' arriving from a room is ignored", len(pieapp.chat.history(60)) == n and not pieapp.llm.busy())
        # the page
        d = c.get("/api/social").get_json() or {}
        expect("GET /api/social names the rooms", "pie-kitchen" in (d.get("rooms") or []), str(d.get("rooms")))
        d = c.get("/api/friends").get_json() or {}
        kitchen = [x for x in d.get("rooms", []) if x["host"] == "pie-kitchen"]
        expect("GET /api/friends has the room, with its notebook and reminders counted", kitchen and kitchen[0]["label"] == "the kitchen" and kitchen[0]["notes"] == 1 and kitchen[0]["reminders"] == 0, str(kitchen))
        d = c.get("/api/now").get_json() or {}
        expect("GET /api/now has the rooms for the Right now column", (d.get("rooms") or {}).get("count", 0) >= 1 and "the kitchen" in d["rooms"]["labels"], str(d.get("rooms")))
        reply = ask("list the pis")
        expect("'list the pis' calls them the rooms of the house", reply.startswith("The other rooms of the house") and "pie-kitchen (kitchen)" in reply, reply)
        expect("rooms.json remembers the room", os.path.exists(_hh.PATH) and "pie-kitchen" in open(_hh.PATH).read())
    finally:
        fake.stop()
        with _fr._lock:
            _fr._peers.pop("room-kitchen-test", None)
        _hh.PATH, _nb.PATH, pieapp.audio.say = real[0], real[1], real[2]
        H.known.clear(); H.known.update(real[3])
        H.live.clear(); H.live.update(real[4])


def whisplay_auto_check(pieapp, c):
    """A Whisplay HAT is used by itself: found (its EEPROM, its sound card, or its driver's overlay
    line in config.txt), its screen is the panel when the panel is still the stock e-ink; a panel
    picked by hand is left alone; a Whisplay picked this way goes back to the e-ink when the HAT
    is gone; a pick made on the page ends the picking."""
    import tempfile as _tf
    import time as _t
    import yaml as _yaml
    from pie_ink import settings as _cfg
    results = []

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"whisplay auto: {name}")

    # the marks it leaves, one at a time
    real_read = _cfg._read
    cases = [
        ("nothing", {}, False),
        ("its sound card", {"/proc/asound/cards": " 0 [whisplaysound  ]: whisplay-soundcard - whisplaysound\n"}, True),
        ("its HAT EEPROM", {"/proc/device-tree/hat/vendor": "PiSugar\0", "/proc/device-tree/hat/product_id": "0x0001\0"}, True),
        ("another HAT's EEPROM", {"/proc/device-tree/hat/vendor": "Waveshare\0", "/proc/device-tree/hat/product_id": "0x0001\0"}, False),
        ("its overlay in config.txt", {"/boot/firmware/config.txt": "dtparam=audio=on\ndtoverlay=whisplay-soundcard\n"}, True),
        ("the overlay commented out", {"/boot/firmware/config.txt": "#dtoverlay=whisplay-soundcard\n"}, False),
    ]
    for name, files, want in cases:
        _cfg._read = lambda path, files=files: files.get(path, "").replace("\0", "")
        try:
            got = _cfg.whisplay_here(fresh=True)
        finally:
            _cfg._read = real_read
        results.append(got == want)
        if got != want:
            print(f"       {name}: found {got}, expected {want}")
    note("the HAT is found by its sound card, its EEPROM or its overlay line, and by nothing else", all(results))

    # what that does to the panel setting
    saved_path, saved_env = _cfg.CONFIG_PATH, os.environ.pop("PIE_INK_DRIVER", None)
    real_here = _cfg.whisplay_here
    folder = _tf.mkdtemp(prefix="pie-ink-whisplay-")
    try:
        _cfg.CONFIG_PATH = os.path.join(folder, "config.yaml")
        _cfg.whisplay_here = lambda fresh=False: True
        fresh = _cfg.load()["display"]
        note("out of the box with a Whisplay: its screen is the panel, picked by itself",
             fresh["driver"] == "whisplay" and fresh["auto"] is True, str(fresh))
        _yaml.safe_dump({"display": {"driver": "epd2in13_V3"}}, open(_cfg.CONFIG_PATH, "w"))
        picked = _cfg.load()["display"]
        note("a panel you picked (another e-ink) is left alone", picked["driver"] == "epd2in13_V3" and not picked["auto"], str(picked))
        _cfg.whisplay_here = lambda fresh=False: False
        _yaml.safe_dump({"display": {"driver": "whisplay", "auto": True}}, open(_cfg.CONFIG_PATH, "w"))
        gone = _cfg.load()["display"]
        note("the HAT gone: a Whisplay picked by itself goes back to the e-ink", gone["driver"] == "epd2in13_V4" and gone["auto"] is False, str(gone))
        _yaml.safe_dump({"display": {"driver": "whisplay", "auto": False}}, open(_cfg.CONFIG_PATH, "w"))
        kept = _cfg.load()["display"]
        note("…but a Whisplay you picked yourself (setup.sh --whisplay, or the page) stays", kept["driver"] == "whisplay", str(kept))
        os.environ["PIE_INK_DRIVER"] = "mock"
        _cfg.whisplay_here = lambda fresh=False: True
        os.remove(_cfg.CONFIG_PATH)
        env = _cfg.load()["display"]
        note("PIE_INK_DRIVER set: no picking by itself", env["driver"] == "mock" and env["auto"] is False, str(env))
    finally:
        _cfg.CONFIG_PATH = saved_path
        _cfg.whisplay_here = real_here
        if saved_env is not None:
            os.environ["PIE_INK_DRIVER"] = saved_env
        else:
            os.environ.pop("PIE_INK_DRIVER", None)

    # a pick made on the page is yours: the picking by itself stops
    check("PUT /api/settings display auto (as if picked by itself)", c.put("/api/settings", json={"display": {"auto": True}}))
    auto_on = pieapp.service.config["display"].get("auto") is True
    check("PUT /api/settings display driver (your pick)", c.put("/api/settings", json={"display": {"driver": "mock", "rotation": 0}}))
    _t.sleep(0.3)
    disp = pieapp.service.config["display"]
    note("a panel picked on the page clears the picking by itself", auto_on and disp.get("auto") is False and disp["driver"] == "mock", str(disp))

    sh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup.sh")).read()
    note("setup.sh --whisplay (or its answer, an LCD: the Whisplay) makes the HAT's screen the panel, with no second screen",
         "--whisplay) SCREEN=whisplay" in sh and "whisplay) echo whisplay" in sh and "PiSugar Whisplay HAT" in sh)


def gpi_case_check(pieapp, c):
    """The RetroFlag GPi Case 2W: its screen (the framebuffer, 640×480 RGB565) as the panel with every
    screen filling it — no bars — laid out at half that both ways; its buttons (a game controller's
    input events) as the keys; the case found by itself from config.txt; and setup.sh asking about it,
    writing the case's block into config.txt once, and taking it out again."""
    import io as _io
    import struct as _st
    import subprocess as _sp
    import tempfile as _tf
    import time as _t
    import yaml as _yaml
    from PIL import Image as _I
    from pie_ink import gamepad as _gp, lcd_screens as _ls, settings as _cfg, vt as _vt
    from pie_ink.modes import MODES as _MODES
    from pie_ink.panels import PANELS as _PANELS, gpicase as _gc
    from pie_ink.service import CYCLE as _CYCLE
    here = os.path.dirname(os.path.abspath(__file__))
    folder = _tf.mkdtemp(prefix="pie-ink-gpi-")

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"gpi case: {name}")

    # -- the screen ---------------------------------------------------------------------------------
    note("the case's screen is a panel to pick (and a stand-in of its shape, for no case)",
         "gpicase" in _PANELS and "mock_gpicase" in _PANELS and _PANELS["gpicase"].colour and _PANELS["gpicase"].kind == "lcd")
    panel = _gc.MockGPiCase()
    note("the screens are laid out for 320×216: the 640×480 glass above its buttons, halved both ways", panel.size == (320, 216),
         str(panel.size))
    config = pieapp.service.config
    bad = []
    for name, cls in _MODES.items():
        try:
            phys = panel._physical(cls(config, panel.info()).render())
            if phys.size != (640, 480):
                bad.append(f"{name} {phys.size}")
        except Exception as e:
            bad.append(f"{name}: {type(e).__name__}: {e}")
    for name in ("system", "weather", "crypto", "camera", "paint", "clock", "testcard", "buddy"):
        try:
            frame = _ls.render({"screen": name, "light_mode": True, "accent": "#62d2ff"}, config=config, size=panel.size)
            phys = panel._physical(frame)
            if frame.size != (320, 216) or phys.size != (640, 480):
                bad.append(f"{name} (colour) {frame.size} -> {phys.size}")
        except Exception as e:
            bad.append(f"{name} (colour): {type(e).__name__}: {e}")
    note("every screen, the e-ink's and the colour ones, comes out 640×480", not bad, "; ".join(bad[:4]))
    whites = [panel._physical(_I.new("L", (960, 648), 255)), panel._physical(_I.new("RGB", (320, 216), (255, 255, 255))),
              panel._physical(_I.new("L", (250, 122), 255))]                 # (an e-ink-shaped frame: stretched, still no bars)
    note("no black bars: a white frame of any size is white edge to edge above the buttons",
         all(im.size == (640, 480) and im.crop((0, 0, 640, 432)).convert("L").getextrema() == (255, 255) for im in whites),
         str([im.crop((0, 0, 640, 432)).convert("L").getextrema() for im in whites]))
    panel.hints = ([("◀▶", "Next", False), ("▲▼", "1D", False), ("A", "Auto", True), ("X", "Ask", False)], "Finance", (98, 210, 255))
    foot = panel._physical(_I.new("RGB", (320, 216), (255, 255, 255))).crop((0, 432, 640, 480))
    colours = {c for _n, c in foot.getcolors(100000)}
    note("…the buttons along the bottom, the screen's own: dark, the keys drawn, the one that's on lit",
         (16, 19, 23) in colours and (98, 210, 255) in colours and len(colours) > 20, f"{len(colours)} colours")
    src = _I.new("RGB", (320, 216))
    src.putpixel((0, 0), (255, 0, 0))
    src.putpixel((319, 215), (0, 0, 255))
    src.putpixel((10, 20), (0, 255, 0))
    phys = panel._physical(src)
    note("the colour screens are doubled pixel for pixel (crisp)",
         all(phys.getpixel(p) == (255, 0, 0) for p in ((0, 0), (1, 0), (0, 1), (1, 1))) and phys.getpixel((2, 0)) == (0, 0, 0)
         and phys.getpixel((639, 431)) == (0, 0, 255) and phys.getpixel((21, 41)) == (0, 255, 0))
    turned = _gc.MockGPiCase(rotation=180)
    note("upside down (rotation 180): turned on the glass, not on the page's preview",
         turned._physical(src).getpixel((639, 479)) == (255, 0, 0) and turned._physical(src, turn=False).getpixel((0, 0)) == (255, 0, 0))
    note("…and only 0 or 180: the case's screen is landscape", _gc.MockGPiCase(rotation=90).rotation == 0)

    f565 = [(11, 5), (5, 6), (0, 5)]
    four = _I.new("RGB", (4, 1))
    for i, col in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        four.putpixel((i, 0), col)
    got = _st.unpack("<4H", _gc.pack(four, 16, f565))
    note("RGB565, the case's framebuffer's own pixels", got == (0xF800, 0x07E0, 0x001F, 0xFFFF), str([hex(v) for v in got]))
    b32 = _gc.pack(_I.new("RGB", (1, 1), (10, 20, 30)), 32, [(16, 8), (8, 8), (0, 8)])
    note("…and a 32-bit one (B G R x)", b32[:3] == bytes([30, 20, 10]), b32.hex())

    # a framebuffer stand-in, rows padded past 640 × 2 bytes the way some are
    fb = os.path.join(folder, "fb0")
    stride = 1344
    open(fb, "wb").write(bytes(stride * 480))
    saved = (_gc.FB, _gc.fb_geometry, _vt.take)
    took = []
    try:
        _gc.FB = fb
        _gc.fb_geometry = lambda f: (640, 480, 0, 0, 16, f565, stride)
        _vt.take = lambda quiet=False: took.append(quiet) or True
        real = _gc.GPiCase()
        real.open()
        frame = _I.new("RGB", (320, 216))
        frame.putpixel((0, 0), (255, 0, 0))
        frame.putpixel((319, 215), (0, 0, 255))
        sent, again = real.show(frame), real.show(frame)
        data = open(fb, "rb").read()
        first, row1 = _st.unpack_from("<H", data, 0)[0], _st.unpack_from("<H", data, stride)[0]
        last = _st.unpack_from("<H", data, 431 * stride + 639 * 2)[0]
        pad = data[1280:stride]
        note("drawn on the framebuffer row by row, past its padding; the same frame twice is drawn once; the console kept off it",
             sent and not again and first == 0xF800 and row1 == 0xF800 and last == 0x001F and pad == bytes(len(pad)) and took == [True],
             f"sent={sent} again={again} first={first:#x} row1={row1:#x} last={last:#x} took={took}")
        real.close(clear=True)
        note("closed: the screen left dark", open(fb, "rb").read()[:1280] == bytes(1280))
        cursor = []
        saved_cursor = (_vt.hide_cursor, _vt.show_cursor)
        try:
            _vt.take = lambda quiet=False: False                 # (run by hand, not as root)
            _vt.hide_cursor, _vt.show_cursor = lambda: cursor.append("hide") or True, lambda: cursor.append("show")
            by_hand = _gc.GPiCase()
            by_hand.open()
            by_hand.close(clear=False)
        finally:
            _vt.hide_cursor, _vt.show_cursor = saved_cursor
        note("run by hand (no root to switch consoles): the console's blinking cursor hidden, and back after", cursor == ["hide", "show"],
             str(cursor))
    finally:
        _gc.FB, _gc.fb_geometry, _vt.take = saved
    tty = os.path.join(folder, "tty3")
    open(tty, "wb").close()
    active = os.path.join(folder, "active")
    open(active, "w").write("tty3\n")
    real_active = _vt.ACTIVE
    try:
        _vt.ACTIVE = active
        front = _vt._front()
        wrote = _vt._write(tty, _vt.HIDE)
    finally:
        _vt.ACTIVE = real_active
    note("…by writing the cursor-off sequence to the console in front",
         front == "/dev/tty3" and wrote and open(tty, "rb").read() == b"\033[?25l\033[?1c", f"{front} {wrote}")
    vt_py = os.path.join(here, "pie_ink", "vt.py")
    runs = [_sp.run([sys.executable, vt_py, what], capture_output=True, text=True, timeout=20) for what in ("graphics", "text", "what")]
    note("vt.py, before and after the service: best effort (it starts either way), and says how it's used",
         [r.returncode for r in runs] == [0, 0, 2], str([(r.returncode, r.stderr.strip()[:60]) for r in runs]))

    # -- the buttons --------------------------------------------------------------------------------
    note("each device asked what it is with the kernel's own ioctls (EVIOCGNAME, EVIOCGBIT, EVIOCGABS)",
         (_gp.EVIOCGNAME, _gp.EVIOCGBIT_KEY, _gp.EVIOCGABS(0x10)) == (0x81004506, 0x80604521, 0x80184550),
         str([hex(v) for v in (_gp.EVIOCGNAME, _gp.EVIOCGBIT_KEY, _gp.EVIOCGABS(0x10))]))
    xbox = {0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e}   # an Xbox-style pad, as the case's is
    as64 = _st.pack("<12Q", *[sum(1 << (k - 64 * w) for k in xbox if k // 64 == w) for w in range(12)])
    as32 = _st.pack("<24I", *[sum(1 << (k - 32 * w) for k in xbox if k // 32 == w) for w in range(24)])
    note("its buttons read off the bitmap the same on a 64-bit Pi OS and a 32-bit one (where the old reading found none)",
         _gp.key_bits(as64) == xbox and _gp.key_bits(as32) == xbox, str(sorted(_gp.key_bits(as32))[:4]))
    keyboard = set(range(1, 120))
    note("a game controller is told from a keyboard, a mouse, the power button and a pad's motion sensors",
         _gp.kind("Microsoft X-Box 360 pad", xbox) == "pad" and _gp.kind("Generic USB Joystick", set(range(0x120, 0x12c))) == "joy"
         and _gp.kind("Pad", {0x220, 0x221}) == "pad" and _gp.kind("Logitech USB Keyboard", keyboard) is None
         and _gp.kind("USB Mouse", {0x110, 0x111, 0x112}) is None and _gp.kind("Power Button", {116}) is None
         and _gp.kind("Sony Controller Motion Sensors", {0x130}) is None)

    inputs = os.path.join(folder, "input")
    os.makedirs(inputs)
    for n in ("event0", "event2", "event10", "js0", "mice"):
        open(os.path.join(inputs, n), "w").close()
    what = {"event0": ("Logitech USB Keyboard", keyboard), "event2": ("Microsoft X-Box 360 pad", xbox),
            "event10": ("Generic   USB  Joystick", set(range(0x120, 0x12c))), "event9": ("Test pad", {0x130, 0x13b, 0x223})}
    asked = []

    def fake_caps(fd):
        name = os.path.basename(os.readlink(f"/proc/self/fd/{fd}"))
        asked.append(name)
        return what[name]
    real_caps, real_dir, real_every = _gp._caps, _gp.INPUT_DIR, _gp.SCAN_EVERY
    try:
        _gp._caps, _gp.INPUT_DIR = fake_caps, inputs
        found = _gp.controllers()
    finally:
        _gp._caps, _gp.INPUT_DIR = real_caps, real_dir
    note("the controllers plugged in, in order (event2 before event10), not the keyboard or the rest",
         found == [{"name": "Microsoft X-Box 360 pad", "event": os.path.join(inputs, "event2"), "joy": False},
                   {"name": "Generic   USB  Joystick", "event": os.path.join(inputs, "event10"), "joy": True}], str(found))
    note("no /dev/input, or a device that won't open: no controllers, no crash",
         _gp.controllers(os.path.join(folder, "nothing")) == [] and "error" in _gp.look(os.path.join(folder, "nothing", "event0")))
    cli = _sp.run([sys.executable, "-m", "pie_ink.gamepad"], cwd=here, capture_output=True, text=True, timeout=30)
    note("python3 -m pie_ink.gamepad says what's plugged in (here: nothing)",
         cli.returncode == 1 and ("Nothing in /dev/input" in cli.stdout or "No game controller" in cli.stdout), cli.stdout + cli.stderr)

    went = []
    pads = _gp.Pads()
    pads.on_press = lambda button: went.append(button) or "did it"
    pad = {"joy": False, "axes": {0x10: (-1, 1), 0x11: (-1, 1), 0x00: (0, 255)}, "held": {}}
    for typ, code, value in [(1, 0x130, 1), (1, 0x130, 2), (1, 0x130, 0),        # A: pressed, held, let go: once
                             (1, 0x131, 1), (1, 0x13b, 1), (1, 0x13a, 1), (1, 0x133, 1), (1, 0x134, 1),   # B START SELECT X Y
                             (3, 0x10, -1), (3, 0x10, -1), (3, 0x10, 0), (3, 0x10, 1),   # ← (once while held), →
                             (3, 0x11, 1), (3, 0x11, 0), (3, 0x11, -1),                 # ↓ ↑
                             (3, 0x00, 128), (3, 0x00, 250), (3, 0x00, 240),             # the stick: centred, then right, once
                             (1, 0x136, 1), (1, 0x137, 1),                               # L R
                             (1, 0x2c0, 1), (1, 0x2c3, 1)]:                              # a d-pad sent as buttons: ← ↓
        pads._event(-1, pad, typ, code, value)
    want = ["a", "b", "start", "select", "x", "y", "left", "right", "down", "up", "right", "l", "r", "left", "down"]
    note("each button by its name — the d-pad as a hat, a stick or buttons — once a press, and what it did kept for the page",
         went == want and pads.last[:2] == ("down", "did it"), f"{went} {pads.last}")
    joy = {"joy": True, "axes": {}, "held": {}}
    went.clear()
    other = []
    pads.on_other = other.append
    pads._event(-1, joy, 1, 0x121, 1)
    pads._event(-1, joy, 1, 0x129, 1)
    pads._event(-1, joy, 1, 0x2f0, 1)
    note("a plain USB pad that numbers its buttons: its A and START (and one it doesn't know, said for the diagnosis)",
         went == ["a", "start"] and other == [0x2f0], f"{went} {other}")
    went.clear()
    trig = {"joy": False, "axes": {0x02: (0, 255), 0x05: (0, 255)}, "held": {}, "triggers": {0x02: "l2", 0x05: "r2"}}
    for typ, code, value in [(3, 0x02, 0), (3, 0x02, 200), (3, 0x02, 255), (3, 0x02, 10), (3, 0x05, 220),   # L as a trigger: once a squeeze
                             (1, 0x138, 1), (1, 0x139, 1)]:                                                # L2 R2 as buttons
        pads._event(-1, trig, typ, code, value)
    note("L and R that come as triggers, or as L2 / R2",
         went == ["l2", "r2", "l2", "r2"], str(went))
    pads_json = os.path.join(folder, "pads.json")
    import json as _json
    open(pads_json, "w").write(_json.dumps({"045e:028e:Microsoft X-Box 360 pad": {
        "keys": {str(0x13d): "l", str(0x13e): "r", "999": "nonsense"}, "axes": {str(0x02): {"1": "l2"}}, "swap": True}}))
    mine = _gp.learned("045e:028e:Microsoft X-Box 360 pad", pads_json)
    note("buttons set up in Pietato for this model of controller are read (and nothing else)",
         mine == {"keys": {0x13d: "l", 0x13e: "r"}, "axes": {0x02: {1: "l2"}}, "swap": True}
         and _gp.learned("0000:0000:another", pads_json) is None and _gp.learned("x", os.path.join(folder, "none.json")) is None, str(mine))
    went.clear()
    taught = {"joy": False, "axes": {0x02: (0, 255)}, "held": {}, "learned": mine}
    for typ, code, value in [(1, 0x13d, 1), (1, 0x13e, 1), (1, 0x136, 1), (1, 0x130, 1), (1, 0x131, 1), (3, 0x02, 250)]:
        pads._event(-1, taught, typ, code, value)
    note("…so L and R on whatever the case sends for them, the usual codes for them let go, A and B swapped if they were",
         went == ["l", "r", "b", "a", "l2"], str(went))
    note("…triggers only where they are (beside a right stick, or a pad's brake and gas) — not a plain pad's right stick",
         _gp.triggers({0, 1, 2, 3, 4, 5, 16, 17}) == {2: "l2", 5: "r2"} and _gp.triggers({0, 1, 2, 5}) == {}
         and _gp.triggers({9, 10}) == {9: "r2", 10: "l2"}, str(_gp.triggers({0, 1, 2, 5})))

    # read for real: events through a FIFO standing in for /dev/input/event9, on the pads' own thread
    lives = os.path.join(folder, "live")
    os.makedirs(lives)
    open(os.path.join(lives, "event0"), "w").close()         # the keyboard
    fifo = os.path.join(lives, "event9")
    os.mkfifo(fifo)
    keep = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)          # (a writer all along: a FIFO without one reads as unplugged)
    heard = []
    try:
        asked.clear()
        _gp._caps, _gp.INPUT_DIR, _gp.SCAN_EVERY = fake_caps, lives, 0.3
        live = _gp.Pads()
        live.on_press = heard.append
        live.start()
        deadline = _t.time() + 5
        while not live.found and _t.time() < deadline:
            _t.sleep(0.05)
        ev = _gp.EVENT
        os.write(keep, ev.pack(0, 0, 1, 0x130, 1) + ev.pack(0, 0, 0, 0, 0) + ev.pack(0, 0, 1, 0x130, 0) + ev.pack(0, 0, 1, 0x223, 1))
        deadline = _t.time() + 5
        while len(heard) < 2 and _t.time() < deadline:
            _t.sleep(0.05)
        _t.sleep(0.8)                                         # (a few more looks round)
        named = sorted(live.found)
        once = asked.count("event0") == 1
        live.stop()
        live.start()                                          # (straight back on, as a settings change does)
        again = live.running() and not live._stop.is_set()
        live.stop()
        live._thread.join(5)
        note("pressed on a controller: read off its events on their own thread; the keyboard asked once, not every look; "
             "stopped and started again at once",
             named == ["Test pad"] and heard == ["a", "right"] and once
             and again and not live.running() and live.found == [] and not live.problem,
             f"found={named} heard={heard} asked={asked} again={again} running={live.running()}")
    finally:
        _gp._caps, _gp.INPUT_DIR, _gp.SCAN_EVERY = real_caps, real_dir, real_every
        os.close(keep)

    # -- found by itself ----------------------------------------------------------------------------
    real_read, real_exists, real_running = _cfg._read, os.path.exists, _cfg.pitato_running
    block = "dtparam=audio=on\n# >>> gpi-case setup >>>\n[all]\ndtoverlay=gpicase-dpi\n# <<< gpi-case setup <<<\n"
    cases = [("nothing", "", True, False, False, ""),
             ("the case's block in config.txt, its screen up", block, True, False, True, ""),
             ("…its screen not up yet (no /dev/fb0)", block, False, False, False, "no_screen"),
             ("just its overlay line, put in by hand", "dtoverlay=gpicase-dpi\n", True, False, True, ""),
             ("the overlay commented out", "#dtoverlay=gpicase-dpi\n", True, False, False, ""),
             ("Pietato running on this Pi (or started at boot): the screen is its", block, True, True, False, "pitato")]
    wrong = []
    for name, text, fb0, pitato, want_it, want_why in cases:
        _cfg._read = lambda path, text=text: text if path == _cfg.BOOT_CONFIGS[0] else ""
        os.path.exists = lambda p, fb0=fb0: fb0 if p == "/dev/fb0" else real_exists(p)
        _cfg.pitato_running = lambda pitato=pitato: pitato
        try:
            got_it = _cfg.gpi_here(fresh=True)
            got_why = _cfg._hat.get("gpi_why")
            trouble = _cfg.gpi_trouble()
        finally:
            _cfg._read, os.path.exists, _cfg.pitato_running = real_read, real_exists, real_running
        if got_it != want_it or got_why != want_why:
            wrong.append(f"{name}: {got_it} {got_why!r}")
        if text.startswith("#") or not text:
            if trouble:
                wrong.append(f"{name}: said {trouble!r}")
        elif "SPI is off" not in trouble or "raspi-config" in trouble or "spi=on" in trouble \
                or (want_why == "pitato" and "disable --now pitato" not in trouble) or (want_why == "no_screen" and "reboot" not in trouble) \
                or (not want_why and "Settings → Screen → Panel" not in trouble):
            wrong.append(f"{name}: said {trouble!r}")
    _cfg._hat["gpi_at"] = 0
    note("the case found by its block or its overlay in config.txt with its screen up and free — and, when it isn't, "
         "the e-ink's error says why (never to turn SPI on)", not wrong, "; ".join(wrong))
    eink_src = open(os.path.join(here, "pie_ink", "panels", "eink.py")).read()
    note("…the e-ink's error on a Pi set up for the case is that, not 'turn SPI on'", "settings.gpi_trouble()" in eink_src)

    # Pietato's own process, as the check sees it (a stand-in pitato.py that just waits)
    stand_in = os.path.join(folder, "pitato.py")
    open(stand_in, "w").write("import time\ntime.sleep(30)\n")
    real_wants = _cfg.PITATO_WANTS
    try:
        _cfg.PITATO_WANTS = (os.path.join(folder, "no-such.wants"),)
        before = _cfg.pitato_running()
        proc = _sp.Popen([sys.executable, stand_in])
        try:
            _t.sleep(0.3)
            during = _cfg.pitato_running()
        finally:
            proc.kill()
            proc.wait()
        after = _cfg.pitato_running()
        wants = os.path.join(folder, "pitato.service")
        open(wants, "w").close()
        _cfg.PITATO_WANTS = (wants,)
        at_boot = _cfg.pitato_running()
    finally:
        _cfg.PITATO_WANTS = real_wants
    note("Pietato is seen running (and when it's started at every boot), and not once it's stopped",
         during and after == before and at_boot, f"before={before} during={during} after={after} at_boot={at_boot}")

    saved_path, saved_env = _cfg.CONFIG_PATH, os.environ.pop("PIE_INK_DRIVER", None)
    real_gpi, real_wp = _cfg.gpi_here, _cfg.whisplay_here
    try:
        _cfg.CONFIG_PATH = os.path.join(folder, "config.yaml")
        _cfg.gpi_here = lambda fresh=False: True
        _cfg.whisplay_here = lambda fresh=False: True        # (both: the case wins; a HAT couldn't be on its pins)
        fresh = _cfg.load()
        note("out of the box in the case: its screen is the panel, picked by itself",
             fresh["display"]["driver"] == "gpicase" and fresh["display"]["auto"] is True, str(fresh["display"]))
        _yaml.safe_dump({"gpi": {"a": "next", "b": "prev", "start": "off", "select": "refresh"}}, open(_cfg.CONFIG_PATH, "w"))
        note("the buttons once set under Settings go (each screen has its own now)", "gpi" not in _cfg.load())
        _yaml.safe_dump({"display": {"driver": _cfg.DEFAULTS["display"]["driver"], "auto": False}}, open(_cfg.CONFIG_PATH, "w"))
        written = _cfg.load()["display"]
        note("…a config.yaml with the default e-ink written out in full (as setup.sh saves it) still gets the case's screen",
             written["driver"] == "gpicase", str(written))
        _cfg.whisplay_here = lambda fresh=False: False
        _yaml.safe_dump({"display": {"driver": "epd2in13_V3"}}, open(_cfg.CONFIG_PATH, "w"))
        picked = _cfg.load()["display"]
        note("a panel you picked is left alone", picked["driver"] == "epd2in13_V3" and not picked.get("auto"), str(picked))
        _cfg.gpi_here = lambda fresh=False: False
        _yaml.safe_dump({"display": {"driver": "gpicase", "auto": True}}, open(_cfg.CONFIG_PATH, "w"))
        gone = _cfg.load()["display"]
        note("out of the case (or Pietato has it): back to the e-ink", gone["driver"] == _cfg.DEFAULTS["display"]["driver"] and gone["auto"] is False, str(gone))
        _yaml.safe_dump({"display": {"driver": "gpicase", "auto": False}}, open(_cfg.CONFIG_PATH, "w"))
        kept = _cfg.load()["display"]
        note("…but the case's screen picked by setup.sh --gpi (or the page) stays", kept["driver"] == "gpicase", str(kept))
    finally:
        _cfg.CONFIG_PATH = saved_path
        _cfg.gpi_here, _cfg.whisplay_here = real_gpi, real_wp
        _cfg._hat["gpi_at"] = 0
        if saved_env is not None:
            os.environ["PIE_INK_DRIVER"] = saved_env
        else:
            os.environ.pop("PIE_INK_DRIVER", None)

    # -- on the page, through the service -----------------------------------------------------------
    from pie_ink import badge as _badge, llm as _llm, pad as _pad
    svc = pieapp.service
    mode_was = svc.mode_name
    check("PUT /api/settings display driver mock_gpicase", c.put("/api/settings", json={"display": {"driver": "mock_gpicase", "rotation": 0}}))
    deadline = _t.time() + 10
    while svc.panel.name != "mock_gpicase" and _t.time() < deadline:
        _t.sleep(0.1)
    p = svc.panel
    note("the case's shape picked on the page: the service draws for 320×216, its buttons below",
         p.name == "mock_gpicase" and p.size == (320, 216) and p.footer, f"{p.name} {p.size}")
    bad = []
    for name in _MODES:
        try:
            im = _I.open(_io.BytesIO(svc.render_preview(name)))
            if im.size != (960, 720):
                bad.append(f"{name} {im.size}")
        except Exception as e:
            bad.append(f"{name}: {type(e).__name__}: {e}")
    note("every screen previews at the case's shape, buttons and all", not bad, "; ".join(bad[:4]))

    def on(name):
        svc.set_mode(name)
        deadline = _t.time() + 5
        while svc.mode_name != name and _t.time() < deadline:
            _t.sleep(0.05)
        return svc.mode_name == name

    def keys():
        return {k: (label, lit) for k, label, lit in _pad.footer(svc)[0]}

    spoken = []
    real_say, real_volume = pieapp.audio.say, pieapp.audio.set_volume
    saved_llm = dict(svc.config.get("llm", {}))
    saved_speak = svc.config.get("audio", {}).get("speak_bot")
    saved_sections = {k: dict(svc.config.get(k) or {}) for k in ("clock", "message", "reader", "weather", "music")}
    saved_days, saved_dark = svc.config["display"].get("crypto_days", 1), bool(svc.config["display"].get("dark_mode"))
    try:
        pieapp.audio.say = lambda text, c=None, wait=False: spoken.append(text)
        pieapp.audio.set_volume = lambda *a, **k: None

        # the same everywhere: L R screens, B home, START off, SELECT light / dark
        on("clock")
        nxt = _CYCLE[(_CYCLE.index("clock") + 1) % len(_CYCLE)]
        _pad.press("r")
        on_next = on(nxt) or svc.mode_name == nxt
        _pad.press("l")
        deadline = _t.time() + 5
        while svc.mode_name != "clock" and _t.time() < deadline:
            _t.sleep(0.05)
        note("L R: the next screen and back", on_next and svc.mode_name == "clock", svc.mode_name)
        _pad.press("b")
        home_open, home_keys = _pad.menu_open(), [k for k, _l, _o in _pad.footer(svc)[0]]
        menu_img = svc._frame(svc._mode)
        _pad.press("right")
        _pad.press("a")
        picked = _pad.screens()[(_CYCLE.index("clock") + 1) % len(_pad.screens())][0]
        deadline = _t.time() + 5
        while svc.mode_name != picked and _t.time() < deadline:
            _t.sleep(0.05)
        note("B: home — every screen as a tile, ◀ ▶ ▲ ▼ to pick, A to open it",
             home_open and home_keys == ["PAD", "A", "B"] and menu_img.size == (320, 216) and not _pad.menu_open() and svc.mode_name == picked,
             f"{home_open} {home_keys} {svc.mode_name} {picked}")
        _pad.press("b")
        _pad.press("b")
        note("…and B again goes back, as it was", not _pad.menu_open() and svc.mode_name == picked)
        _pad.press("select")
        flipped = bool(svc.config["display"].get("dark_mode"))
        said_dark = _badge.toasting()
        _pad.press("select")
        note("SELECT: light / dark, and says which", flipped != saved_dark and bool(svc.config["display"].get("dark_mode")) == saved_dark
             and said_dark == ("Dark" if flipped else "Light"), said_dark)
        _pad.press("start")
        deadline = _t.time() + 5
        while svc.mode_name != "off" and _t.time() < deadline:
            _t.sleep(0.05)
        went_off = svc.mode_name == "off"
        _pad.press("a")
        deadline = _t.time() + 5
        while svc.mode_name == "off" and _t.time() < deadline:
            _t.sleep(0.05)
        note("START: the screen off; any button wakes it", went_off and svc.mode_name not in (None, "off"), svc.mode_name)

        # Finance: ◀ ▶ the coins and shares, ▲ ▼ the chart's range, A Auto
        on("crypto")
        svc.config["display"]["crypto_days"] = 1
        _pad.press("up")
        up_days, label = svc.config["display"].get("crypto_days"), keys().get("▲▼", ("", 0))[0]
        _pad.press("up")
        _pad.press("down")
        note("Finance ▲ ▼: the chart's range, 1D 1W 1M 6M 1Y, shown along the bottom", up_days == 7 and label == "1W"
             and svc.config["display"].get("crypto_days") == 7, f"{up_days} {label}")
        _pad.FIN.update(auto=True, skip=0, hold=None)
        before = _pad.finance_index(5, now=1000.0, per=90)
        _pad.press("right")
        after = _pad.finance_index(5, now=1000.0, per=90)
        _pad.press("a")
        held = _pad.finance_index(5, per=90)
        later = _pad.finance_index(5, now=_t.time() + 600, per=90)
        _pad.press("right")
        note("Finance ◀ ▶: the next coin or share; A: Auto off holds it there (lit while on)",
             after == (before + 1) % 5 and not _pad.FIN["auto"] and held == later and _pad.finance_index(5, per=90) == (held + 1) % 5
             and keys()["A"] == ("Auto", False), f"{before} {after} {held} {later} {keys().get('A')}")
        _pad.press("a")
        chips = _ls.crypto({"light_mode": True, "accent": "#62d2ff", "crypto_days": 30, "pad": True}, size=(320, 216), config=svc.config)
        plain = _ls.crypto({"light_mode": True, "accent": "#62d2ff", "crypto_days": 30}, size=(320, 216), config=svc.config)
        note("…and the colour Finance screen shows the range as chips (the one on, lit) in the case only",
             chips.size == (320, 216) and chips.tobytes() != plain.tobytes()
             and (98, 210, 255) in {col for _n, col in chips.crop((0, 150, 320, 172)).getcolors(100000)})

        # Weather: ◀ ▶ the next days, ▲ ▼ °F / °C
        on("weather")
        days = [{"date": f"2026-10-{26 + i}", "code": 1, "hi": 70 + i, "lo": 58, "rain": 10, "sunrise": "06:58", "sunset": "18:02"}
                for i in range(5)]
        real_wx = (_ls._WEATHER.data, _ls._WEATHER.place)
        try:
            _ls._WEATHER.place, _ls._WEATHER.data = ((32.7, -117.1), "F"), {"temp": 68, "code": 2, "units": "F", "days": days}
            svc.config["weather"].update(lat=32.7, lon=-117.1, units="F")
            _pad.WX["day"] = 0
            _pad.press("right")
            _pad.press("right")
            day_two, label = _pad.weather_day(), keys().get("◀▶", ("", 0))[0]
            big = _ls.weather({"light_mode": True, "accent": "#62d2ff", "pad": True}, size=(320, 216), config=svc.config)
            _pad.press("left")
            _pad.press("left")
            now_view = _ls.weather({"light_mode": True, "accent": "#62d2ff", "pad": True}, size=(320, 216), config=svc.config)
            note("Weather ◀ ▶: one of the next days, big, or now", day_two == 2 and label == "Wed" and big.size == (320, 216)
                 and big.tobytes() != now_view.tobytes() and keys()["◀▶"][0] == "Now", f"{day_two} {label}")
        finally:
            _ls._WEATHER.data, _ls._WEATHER.place = real_wx
        _pad.press("up")
        note("Weather ▲ ▼: °F / °C", svc.config["weather"].get("units") == "C" and keys()["▲▼"][0] == "°C",
             str(svc.config["weather"].get("units")))
        _pad.press("up")

        # Clock: ◀ ▶ font, ▲ ▼ size, A 24h, Y seconds
        on("clock")
        c0 = dict(svc.config["clock"])
        _pad.press("right")
        _pad.press("up")
        _pad.press("a")
        _pad.press("y")
        c1 = svc.config["clock"]
        note("Clock: ◀ ▶ the font, ▲ ▼ the size, A 24-hour, Y seconds (lit when on)",
             c1["font"] != c0.get("font") and int(c1["size"]) > int(c0.get("size", 36)) and c1["format"] != c0.get("format", "12")
             and bool(c1["show_seconds"]) != bool(c0.get("show_seconds")) and keys()["Y"][1] == bool(c1["show_seconds"]), str(c1))

        # Message: ▲ ▼ where its words come from, ◀ ▶ the next one and back, A reads it
        on("message")
        _pad.press("up")
        source = svc.config["message"].get("source")
        svc.config["message"]["source"] = "joke"
        svc._mode.update(svc.config)
        svc._mode.render()
        first = svc._mode._current
        _pad.press("right")
        svc._mode.render()
        second = svc._mode._current
        _pad.press("left")
        back = svc._mode._current
        spoken.clear()
        _pad.press("a")
        _t.sleep(0.3)
        note("Message: ▲ ▼ where its words come from, ◀ ▶ the next joke and the one before, A reads it out",
             source != saved_sections["message"].get("source", "message") and first and second and back == first
             and spoken and spoken[-1] == first.replace("\n", ". "), f"{source} {first!r} {second!r} {back!r} {spoken}")

        # Music: ◀ ▶ tracks, ▲ ▼ volume, A play / pause, Y shuffle
        on("music")
        v0 = int(svc.config["audio"].get("volume", 80))
        _pad.press("down")
        shuffle0 = bool(pieapp.music.PLAYER.shuffle)
        _pad.press("y")
        note("Music: ▲ ▼ the volume, Y shuffle (lit when on)", int(svc.config["audio"].get("volume")) == max(0, v0 - 10)
             and bool(pieapp.music.PLAYER.shuffle) != shuffle0 and keys()["Y"][1] == bool(pieapp.music.PLAYER.shuffle)
             and keys()["▲▼"][0] == f"Vol {max(0, v0 - 10)}%", str(keys()))
        _pad.press("y")
        _pad.press("up")
        _pad.press("a")
        note("…A: play / pause, and says which", _badge.toasting() in ("Playing", "Paused", "No music yet — add some on the page"),
             _badge.toasting())
        if pieapp.music.PLAYER.state != "stopped":
            pieapp.music.PLAYER.stop()

        # Read: ◀ ▶ pages, ▲ ▼ books, A auto, Y size
        on("reader")
        a0, s0 = int(svc.config["reader"].get("auto_seconds", 0) or 0), int(svc.config["reader"].get("size", 12))
        _pad.press("a")
        _pad.press("y")
        note("Read: A turns the pages by itself (lit), Y the size", bool(int(svc.config["reader"]["auto_seconds"])) != bool(a0)
             and int(svc.config["reader"]["size"]) != s0 and keys()["A"][1] == bool(int(svc.config["reader"]["auto_seconds"])),
             str(svc.config["reader"]))
        _pad.press("a")

        # Camera: A a photo
        on("camera")
        had = {ph["name"] for ph in pieapp.photos.listing()[0]}
        _pad.press("a")
        deadline = _t.time() + 15
        while _badge.toasting() == "Taking a photo…" and _t.time() < deadline:
            _t.sleep(0.1)
        note("Camera: A a photo, and says so", _badge.toasting() == "Photo saved" or _badge.toasting().startswith("No photo"),
             _badge.toasting())
        for ph in pieapp.photos.listing()[0]:
            if ph["name"] not in had:
                pieapp.photos.delete(ph["name"])        # (the shelves are counted later)

        # X: the bot, about this screen (a joke while it's off)
        svc.config["llm"]["enabled"] = False
        svc.config.setdefault("audio", {})["speak_bot"] = True
        spoken.clear()
        _pad.press("x")
        _t.sleep(0.3)
        joke = _badge.toasting()
        note("X with the bot off: a joke over the screen, and out loud", "\n" in joke and spoken and spoken[0] == joke.replace("\n", ". "),
             f"{joke!r} {spoken}")
        note("every screen has X for the bot, and its own buttons, shown along the bottom with L R and B",
             all(any(h[0] == "X" for h in (_pad.CONTROLS[n][1]() if n in _pad.CONTROLS else [("X", "Ask")]))
                 or n == "reader" for n, _label in _pad.screens())
             and [k for k, _l, _o in _pad.footer(svc)[0]][-2:] == ["L R", "B"])

        # the page
        r = c.get("/api/gpi")
        check("GET /api/gpi", r)
        d = r.get_json() or {}
        note("/api/gpi: this screen's buttons, and what the last one did", d.get("panel") is True and d.get("screen") == "Camera"
             and any(b["key"] == "A" and b["does"] == "Photo" for b in d.get("buttons", [])), str(d)[:300])
        _gp.PADS.on_press = _pad.press
        _gp.PADS.press("select")
        r = c.get("/api/gpi").get_json() or {}
        note("…the last press, and what it did", (r.get("last") or {}).get("button") == "select" and (r.get("last") or {}).get("did") in ("dark", "light"),
             str(r.get("last")))
        _gp.PADS.press("select")
    finally:
        pieapp.audio.say, pieapp.audio.set_volume = real_say, real_volume
        svc.config["llm"].update(saved_llm)
        if saved_speak is not None:
            svc.config.setdefault("audio", {})["speak_bot"] = saved_speak
        svc.apply_settings({**saved_sections, "display": {"crypto_days": saved_days, "dark_mode": saved_dark}})
        _pad.MENU["open"] = False
        _pad.FIN.update(auto=True, skip=0, hold=None)
        _pad.WX["day"] = 0
        _llm.set_speech("", "idle")

    # the toast itself: along the bottom, over any screen, then gone
    _badge.toast("Hello", 0.4)
    white_l, white_rgb = _I.new("L", (960, 720), 255), _I.new("RGB", (320, 240), (255, 255, 255))
    on_l, on_rgb = _badge.apply(white_l, {"display": {}}, "clock"), _badge.apply(white_rgb, {"display": {}}, "system")
    low = on_l.crop((0, 600, 960, 720)).getextrema()[0] < 50 and on_l.crop((0, 0, 960, 400)).getextrema() == (255, 255)
    off_screen = _badge.apply(white_l, {"display": {}}, "off").getextrema() == (255, 255)
    _t.sleep(0.5)
    gone = _badge.toasting() == "" and _badge.apply(white_l, {"display": {}}, "clock").getextrema() == (255, 255)
    note("a toast: a box along the bottom (grey or colour), not on a screen that's off, and gone after its time",
         low and on_rgb.crop((0, 200, 320, 240)).convert("L").getextrema()[0] < 50 and off_screen and gone, f"{low} {off_screen} {gone}")
    check("PUT /api/settings display driver back", c.put("/api/settings", json={"display": {"driver": "mock", "rotation": 0}}))
    deadline = _t.time() + 10
    while pieapp.service.panel.name != "mock" and _t.time() < deadline:
        _t.sleep(0.1)
    if mode_was:
        pieapp.service.set_mode(mode_was)

    # -- setup.sh -----------------------------------------------------------------------------------
    sh = open(os.path.join(here, "setup.sh")).read()
    note("setup.sh keeps the console off the case's screen (a drop-in for the service) and lets PiE-ink read the buttons",
         "gpi-console.conf" in sh and "vt.py graphics" in sh and "vt.py text" in sh and ",input," in sh)
    start = sh.index("SPI_REBOOT=0\nGPI_REBOOT=0\n")
    end = sh.index("\n", sh.index("sudo usermod -aG", start)) + 1
    open(os.path.join(folder, "block.sh"), "w").write(sh[start:end])
    boot = os.path.join(folder, "boot")
    os.makedirs(os.path.join(boot, "overlays"))
    open(os.path.join(boot, "config.txt"), "w").write("dtparam=spi=on\ndtoverlay=vc4-kms-v3d\ndtparam=audio=on\n")
    open(os.path.join(folder, "run.sh"), "w").write(
        'set -euo pipefail\nBOOT="$1"; CONFIG="$1/config.txt"; USER_NAME=nobody\n'
        'sudo() { case "$1" in apt-get|raspi-config|usermod|dtparam) return 0 ;; esac; "$@"; }\n'
        'dtc() { local out=""; while [ $# -gt 0 ]; do if [ "$1" = -o ]; then out="$2"; fi; shift; done; echo dtb > "$out"; }\n'
        'sleep() { :; }\n'
        'GPI=1; GPI_NOW=0; source "$2"; echo "reboot=$GPI_REBOOT"; cp "$CONFIG" "$1/after-1"\n'
        'GPI=1; GPI_NOW=1; source "$2"; echo "reboot=$GPI_REBOOT"; cp "$CONFIG" "$1/after-2"\n'
        'GPI=0; GPI_NOW=1; source "$2"; echo "reboot=$GPI_REBOOT"; cp "$CONFIG" "$1/after-3"\n')
    r = _sp.run(["bash", os.path.join(folder, "run.sh"), boot, os.path.join(folder, "block.sh")], capture_output=True, text=True, timeout=60)
    after = [open(os.path.join(boot, f"after-{i}")).read() if os.path.exists(os.path.join(boot, f"after-{i}")) else "" for i in (1, 2, 3)]
    lines = [a.splitlines() for a in after]
    note("…writes the case's block into config.txt: its screen (KMS off, SPI off), speaker and power switch, and its overlay",
         r.returncode == 0 and after[0].count("# >>> gpi-case setup >>>") == 1 and "#dtoverlay=vc4-kms-v3d" in lines[0]
         and "dtparam=spi=on" not in lines[0] and "dtoverlay=gpicase-dpi" in lines[0]
         and "dtoverlay=audremap,pins_18_19,enable_jack" in lines[0] and "gpio=27=op,dh" in lines[0]
         and any(l.startswith("dtoverlay=gpio-shutdown,gpio_pin=26") for l in lines[0])
         and os.path.exists(os.path.join(boot, "overlays", "gpicase-dpi.dtbo")) and "reboot=1" in r.stdout.splitlines(),
         f"exit {r.returncode}: {r.stderr.strip()[-200:]}")
    note("…once: run again, it's left as it is (no reboot needed)", after[1] == after[0] and r.stdout.splitlines().count("reboot=0") >= 1,
         r.stdout.strip()[-200:])
    note("…and --no-gpi takes it out again, KMS back on", after[2] and "# >>> gpi-case setup >>>" not in after[2]
         and "dtoverlay=vc4-kms-v3d" in lines[2] and "gpicase-dpi" not in after[2], after[2][:200])


def setup_screen_check():
    """setup.sh asks the one thing — the screen: an e-ink display, an LCD by itself, an e-ink with an LCD
    beside it, or the GPi Case 2W — and sets up and installs only what that screen needs."""
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    import yaml as _yaml
    from pie_ink import settings as _cfg
    here = os.path.dirname(os.path.abspath(__file__))
    folder = _tf.mkdtemp(prefix="pie-ink-setup-")
    sh = open(os.path.join(here, "setup.sh")).read()

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"setup.sh: {name}")

    copy = os.path.join(folder, "copy")                   # (a PiE-ink of its own: its own config.yaml)
    _sh.copytree(os.path.join(here, "pie_ink"), os.path.join(copy, "pie_ink"), ignore=_sh.ignore_patterns("__pycache__"))
    env = {k: v for k, v in os.environ.items() if k != "PIE_INK_DRIVER"}
    a, b = sh.index("# >>> which screen >>>"), sh.index("# <<< which screen <<<")
    open(os.path.join(folder, "which.sh"), "w").write(sh[a:b])
    open(os.path.join(folder, "run.sh"), "w").write(
        'set -euo pipefail\nDIR="$1"; SCREEN="$2"; SECOND="$3"; NO_GPI="$4"; GPI_NOW="$5"; MODEL="Raspberry Pi Zero 2 W Rev 1.0"\n'
        'source "$(dirname "$0")/which.sh"\necho "RESULT $SCREEN $DRIVER $LCD $LCD_MODEL $EINK $GPI $WHISPLAY"\n')

    def which(answers="", screen="", second="", no_gpi="0", gpi_now="0", ask=True):
        e = dict(env, PIE_INK_ASK="1") if ask else env
        r = _sp.run(["bash", os.path.join(folder, "run.sh"), copy, screen, second, no_gpi, gpi_now], input=answers,
                    capture_output=True, text=True, timeout=60, env=e)
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), "")
        return (line.split()[1:] if line else None), r

    got, r = which("1\n3\n")
    asked = r.stderr
    note("it asks what the screen is — an e-ink, an LCD, an e-ink with an LCD beside it, or the GPi Case 2W",
         "What screen is PiE-ink for?" in asked and "An e-ink display and an LCD beside it" in asked and "A RetroFlag GPi Case 2W" in asked
         and "Which e-ink display?" in asked, asked[-300:])
    cases = [("1\n3\n", ["27v2", "epd2in7_V2", "0", "1.69", "1", "0", "0"], "an e-ink: the 2.7\" V2, by itself"),
             ("1\n4\n", ["27v1", "epd2in7", "0", "1.69", "1", "0", "0"], "…the 2.7\" V1"),
             ("1\n5\n", ["37", "epd3in7", "0", "1.69", "1", "0", "0"], "…the 3.7\""),
             ("3\n2\n2\n", ["213v3", "epd2in13_V3", "1", "1.9", "1", "0", "0"], "an e-ink (2.13\" V3) and the 1.9\" LCD beside it"),
             ("3\n1\n1\n", ["213v4", "epd2in13_V4", "1", "1.69", "1", "0", "0"], "…the 2.13\" V4 and the 1.69\""),
             ("2\n1\n", ["lcd169", "lcd169", "0", "1.69", "0", "0", "0"], "an LCD by itself: the 1.69\""),
             ("2\n2\n", ["lcd19", "lcd19", "0", "1.69", "0", "0", "0"], "…the 1.9\""),
             ("2\n3\n", ["whisplay", "whisplay", "0", "1.69", "0", "0", "1"], "…the Whisplay HAT"),
             ("4\n", ["gpi", "gpicase", "0", "1.69", "0", "1", "0"], "the GPi Case 2W"),
             ("\n\n", ["213v4", "epd2in13_V4", "0", "1.69", "1", "0", "0"], "Enter, Enter: what it is now (a fresh one: the 2.13\" V4)"),
             ("7\nno\n1\n\n", ["213v4", "epd2in13_V4", "0", "1.69", "1", "0", "0"], "an answer that isn't one of them: asked again")]
    wrong = []
    for answers, want, name in cases:
        got, _r = which(answers)
        if got != want:
            wrong.append(f"{name}: {got}")
    note("each answer comes out as the screen to set up (and nothing beside a screen that has no room for one)", not wrong, "; ".join(wrong))
    flags = [(dict(screen="27v1", second="1.69"), ["27v1", "epd2in7", "1", "1.69", "1", "0", "0"]),
             (dict(screen="lcd19"), ["lcd19", "lcd19", "0", "1.69", "0", "0", "0"]),
             (dict(screen="gpi", second="1.9"), ["gpi", "gpicase", "0", "1.69", "0", "1", "0"]),
             (dict(screen="whisplay"), ["whisplay", "whisplay", "0", "1.69", "0", "0", "1"]),
             (dict(gpi_now="1"), ["gpi", "gpicase", "0", "1.69", "0", "1", "0"]),
             (dict(gpi_now="1", no_gpi="1"), ["213v4", "epd2in13_V4", "0", "1.69", "1", "0", "0"])]
    wrong = []
    for kw, want in flags:
        got, _r = which(ask=False, **kw)
        if got != want:
            wrong.append(f"{kw}: {got}")
    bad, r_bad = which(ask=False, screen="bogus")
    note("--screen= and --lcd= answer it without asking; a Pi set up for the case is the case; a screen it doesn't know: says so",
         not wrong and bad is None and r_bad.returncode == 1 and "Unknown screen" in r_bad.stdout, "; ".join(wrong) or r_bad.stdout)
    quiet, r_quiet = which("3\n", ask=False)
    note("…and run without a terminal, nothing's asked: it stays as it is", quiet == ["213v4", "epd2in13_V4", "0", "1.69", "1", "0", "0"]
         and "What screen" not in r_quiet.stderr, str(quiet))
    note("the older flags still work (--whisplay, --gpi, --no-gpi, --lcd, --no-lcd)",
         all(f in sh for f in ("--whisplay) SCREEN=whisplay", "--gpi) SCREEN=gpi", "--no-gpi) NO_GPI=1", "--lcd) SECOND=keep",
                                 "--no-lcd) SECOND=none")))

    # only what the screen needs
    pk = sh.index('PACKAGES=(')
    note("the SPI and GPIO libraries only for the screens on SPI; the GPi Case's overlay tools only in the case",
         'if [[ "$GPI" != "1" ]]; then\n  PACKAGES+=(python3-spidev python3-gpiozero python3-lgpio)' in sh
         and "python3-spidev" not in sh[pk:sh.index(")", pk)] and "device-tree-compiler" not in sh[pk:sh.index("sudo apt-get install", pk)]
         and sh.index("device-tree-compiler") > sh.index('if [[ "$GPI" == "1" ]]; then\n  echo "==> Setting up for the GPi Case 2W'))

    # what it saves
    a = sh.index("<<'PY'\n") + len("<<'PY'\n")
    open(os.path.join(copy, "patch.py"), "w").write(sh[a:sh.index("\nPY\n", a)] + "\n")

    def saved(driver, second="0", model="1.69"):
        r = _sp.run([sys.executable, "patch.py", copy, driver, second, model], cwd=copy, env=env, capture_output=True, text=True, timeout=60)
        return (_yaml.safe_load(open(os.path.join(copy, "config.yaml"))) or {}) if r.returncode == 0 else {"error": r.stderr[-300:]}

    two = saved("epd2in7_V2", "1", "1.9")
    lone = saved("lcd169")
    cased = saved("gpicase")
    uncased = saved("epd2in13_V4")
    hat = saved("whisplay")
    note("…saves the screen as the panel: an e-ink with the 1.9\" beside it, an LCD by itself, the case (its speaker for the voice), "
         "out of the case again (the default sound), a Whisplay",
         two.get("display", {}).get("driver") == "epd2in7_V2" and two.get("lcd", {}).get("available") is True
         and two.get("lcd", {}).get("model") == "1.9" and two.get("display", {}).get("auto") is False
         and lone.get("display", {}).get("driver") == "lcd169" and lone.get("lcd", {}).get("available") is False
         and lone.get("lcd", {}).get("enabled") is False
         and cased.get("display", {}).get("driver") == "gpicase" and cased.get("audio", {}).get("device") == "plughw:CARD=Headphones,DEV=0"
         and uncased.get("display", {}).get("driver") == "epd2in13_V4" and uncased.get("audio", {}).get("device") == ""
         and hat.get("display", {}).get("driver") == "whisplay",
         str({k: (v.get("display"), v.get("lcd", {}).get("available")) for k, v in
              (("two", two), ("lone", lone), ("cased", cased), ("uncased", uncased), ("hat", hat))})[:400])
    from pie_ink.panels import PANELS as _P
    note("every screen it offers is a panel to pick on the page too",
         all(d in _P for d in ("epd2in13_V4", "epd2in13_V3", "epd2in7_V2", "epd2in7", "epd3in7", "lcd169", "lcd19", "whisplay", "gpicase")))


def lcd_main_check(pieapp, c):
    """A Waveshare 1.69" or 1.9" LCD by itself as the main screen: every screen drawn for its shape (landscape
    by default, its rounded corners kept clear on the 1.69"), sent to the glass portrait through the same
    ST7789 driver the second screen uses, on the same pins; and no second screen beside it."""
    import io as _io
    import time as _t
    from PIL import Image as _I
    from pie_ink import lcd as _lcd
    from pie_ink.modes import MODES as _MODES
    from pie_ink.panels import waveshare_lcd as _wl

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"lcd main: {name}")

    sent = []

    class FakeST7789:
        def __init__(self, model_name="1.69", cs=1, dc=22, rst=27, bl=12, bus=0, speed=0):
            self.size = (_lcd.model(model_name)["width"], _lcd.model(model_name)["height"])
            sent.append(("open", model_name, cs, dc, rst, bl))

        def open(self):
            pass

        def show(self, image):
            sent.append(("show", image.size))

        def brightness(self, percent):
            sent.append(("bl", percent))

        def close(self):
            sent.append(("close",))
    real = _lcd.ST7789
    try:
        _lcd.ST7789 = FakeST7789
        for cls, glass in ((_wl.Lcd169, (240, 280)), (_wl.Lcd19, (170, 320))):
            sent.clear()
            p = cls()
            p.open()
            p.show(_I.new("RGB", p.size, (200, 30, 30)))
            p.set_brightness(40)
            p.close(clear=False)
            note(f"the {p.model}\" LCD by itself: landscape {p.size}, sent to its {glass[0]}×{glass[1]} glass on CE1 / DC 22 / RST 27 / BL 12",
                 sent[0][:2] == ("open", p.model) and sent[0][2:] == (1, 22, 27, 12) and ("show", glass) in sent and ("bl", 40) in sent
                 and sent[-1] == ("close",) and p.size[0] > p.size[1], str(sent))
    finally:
        _lcd.ST7789 = real
    svc = pieapp.service
    for driver, size in (("mock_lcd169", (260, 220)), ("mock_lcd19", (312, 162))):
        check(f"PUT /api/settings display driver {driver}", c.put("/api/settings", json={"display": {"driver": driver, "rotation": 90}}))
        deadline = _t.time() + 10
        while svc.panel.name != driver and _t.time() < deadline:
            _t.sleep(0.1)
        bad = []
        for name in _MODES:
            try:
                _I.open(_io.BytesIO(svc.render_preview(name)))
            except Exception as e:
                bad.append(f"{name}: {type(e).__name__}: {e}")
        note(f"{driver}: every screen drawn for {size[0]}×{size[1]}, in colour where there's a colour one",
             svc.panel.size == size and svc.panel.colour and not bad, f"{svc.panel.size} {'; '.join(bad[:3])}")
    r = c.get("/api/lcd/status") if any(rule.rule == "/api/lcd/status" for rule in pieapp.app.url_map.iter_rules()) else None
    note("…and no second screen beside it (the LCD is the screen)", not pieapp.lcd.SECOND.running(),
         str(r.get_json() if r is not None else ""))
    check("PUT /api/settings display driver back", c.put("/api/settings", json={"display": {"driver": "mock", "rotation": 0}}))
    deadline = _t.time() + 10
    while svc.panel.name != "mock" and _t.time() < deadline:
        _t.sleep(0.1)


def panel_check():
    """The e-ink HATs on a pretend Pi: fake lgpio and spidev modules under the real
    Waveshare drivers and the panel classes, every byte to the bus recorded. Every
    driver draws, the 2.7" frames are the bytes the stock driver would send, no SPI
    descriptor leaks, and the service swaps drivers while running without the page
    waiting on the old HAT."""
    import subprocess
    import threading
    import time as _t
    import types
    from PIL import Image, ImageDraw

    bus = {"spi": [], "claimed": set(), "opens": 0, "closes": 0, "busy": 0}

    class LgpioError(Exception):
        pass

    lg = types.ModuleType("lgpio")
    lg.error, lg.SET_PULL_DOWN, lg.SET_PULL_UP = LgpioError, 1, 2
    lg.gpiochip_open = lambda n: 100 if n == 0 else (_ for _ in ()).throw(LgpioError("no chip"))
    lg.gpio_get_chip_info = lambda h: (0, 54, "gpiochip0", "pinctrl-bcm2835")
    lg.gpiochip_close = lambda h: None
    lg.gpio_claim_output = lambda h, pin, level=0: bus["claimed"].add(pin)
    lg.gpio_claim_input = lambda h, pin, flags=0: bus["claimed"].add(pin)
    lg.gpio_free = lambda h, pin: bus["claimed"].discard(pin)
    lg.gpio_read = lambda h, pin: bus["busy"] if pin == 24 else 1

    def gpio_write(h, pin, v):
        if pin not in bus["claimed"]:                      # what real lgpio does with a line you let go of
            raise LgpioError("GPIO not allocated")
    lg.gpio_write = gpio_write

    class SpiDev:
        def __init__(self):
            self.max_speed_hz, self.mode, self.fd = 0, 0, -1

        def open(self, b, d):
            bus["opens"] += 1; self.fd = bus["opens"]

        def writebytes(self, data):
            if self.fd < 0:
                raise OSError("SPI not open")
            bus["spi"].append(bytes(data))
        writebytes2 = writebytes

        def close(self):
            if self.fd >= 0:
                bus["closes"] += 1; self.fd = -1

    sp = types.ModuleType("spidev"); sp.SpiDev = SpiDev
    sys.modules["lgpio"], sys.modules["spidev"] = lg, sp
    real_popen = subprocess.Popen

    class _Pi:                                               # epdconfig reads /proc/cpuinfo at import
        def communicate(self):
            return ("Model : Raspberry Pi Zero 2 W Rev 1.0\n", "")
    subprocess.Popen = lambda cmd, *a, **k: _Pi() if isinstance(cmd, str) and "cpuinfo" in cmd else real_popen(cmd, *a, **k)
    try:
        import waveshare_epd.epdconfig as epdconfig
    finally:
        subprocess.Popen = real_popen
    if type(epdconfig.implementation).__name__ != "LgpioPi":
        print(f"  FAIL epdconfig picked {type(epdconfig.implementation).__name__} on a Pi with lgpio")
        FAILED.append("epdconfig backend")
        return
    epdconfig.implementation.delay_ms = lambda ms: None      # the drivers' pauses, skipped
    epdconfig.delay_ms = epdconfig.implementation.delay_ms

    from pie_ink import panels
    from pie_ink.panels import base as _base, eink as _eink

    def picture(w, h, n):
        im = Image.new("L", (w, h), 255)
        d = ImageDraw.Draw(im)
        d.ellipse((5 + n * 9, 5, 70 + n * 9, 70), fill=0)
        d.text((4, h - 16), f"frame {n}", fill=0)
        return im

    def fresh():
        bus["spi"].clear(); bus["opens"] = bus["closes"] = 0

    # every driver, both ways up: open, five frames (the first full, then partial), the same frame
    # again (not sent), a forced full one, a clear, close — then nothing is left claimed or open
    for name in ("epd2in13_V4", "epd2in13_V3", "epd2in7_V2", "epd2in7", "epd3in7"):
        for rotation in (0, 180):
            fresh()
            bus["busy"] = 0 if name != "epd2in7" else 1    # the V1's controller says idle with a high line
            p = panels.make({"driver": name, "rotation": rotation, "full_refresh_every": 3})
            try:
                p.open()
                sent = [p.show(picture(p.width, p.height, n)) for n in range(5)]
                again = p.show(picture(p.width, p.height, 4))
                p.show(picture(p.width, p.height, 9), full=True)
                p.clear()
                p.close()
            except Exception as e:
                print(f"  FAIL {name} rotation {rotation}: {type(e).__name__}: {e}")
                FAILED.append(f"panel {name}")
                continue
            okay = all(sent) and not again and not bus["claimed"] and bus["opens"] == bus["closes"] and not p.warning
            print(f"  {'ok  ' if okay else 'FAIL'} {name} rotation {rotation}: {p.width}x{p.height}, 7 frames, "
                  f"{len(bus['spi'])} SPI transfers, SPI opened {bus['opens']} closed {bus['closes']}, lines left: {sorted(bus['claimed']) or 'none'}")
            if not okay:
                FAILED.append(f"panel {name}")

    # the 2.7" frames go in one transfer, packed with numpy: the same bytes the stock driver's loop makes
    import importlib
    for module in ("waveshare_epd.epd2in7_V2", "waveshare_epd.epd2in7"):
        epd = importlib.import_module(module).EPD()
        same = True
        for size in ((264, 176), (176, 264)):
            im = Image.effect_noise(size, 80).point(lambda v: 255 if v > 128 else 0).convert("1")
            if _eink.packed(im, epd) != epd.getbuffer(im):
                same = False
                print(f"  FAIL {module} {size[0]}x{size[1]}: the packed frame differs from the driver's own")
        if same:
            print(f"  ok   {module.split('.')[-1]}: the frame packed with numpy is byte for byte the driver's own, both ways up")
        else:
            FAILED.append(f"{module} packing")
    bus["busy"] = 0
    fresh()
    p = panels.make({"driver": "epd2in7_V2"}); p.open(); opened = len(bus["spi"])
    p.show(picture(264, 176, 1)); first = len(bus["spi"]) - opened
    p.show(picture(264, 176, 2)); second = len(bus["spi"]) - opened - first
    p.close()
    if first < 60 and second < 40:
        print(f"  ok   a 2.7\" V2 frame is {first} transfers for a full refresh and {second} for a partial one (the stock driver: 11,600 and 5,800)")
    else:
        print(f"  FAIL a 2.7\" V2 frame took {first} / {second} transfers")
        FAILED.append("2.7 transfers")

    # a panel that never answers: the page is told, and the word goes once the panel answers again
    class Slow(panels.MockEInk):
        name = "slow"
        took = 0.0

        def _push(self, image, full):
            Slow.clock += self.took

    Slow.clock = 1000.0
    real_mono = _base.time.monotonic
    _base.time.monotonic = lambda: Slow.clock
    try:
        s = Slow(); s.open()
        s.took = 31.0; s.show(picture(250, 122, 1))
        said = s.warning
        s.took = 0.5; s.show(picture(250, 122, 2))
        cleared = s.warning
    finally:
        _base.time.monotonic = real_mono
    if said == _base.NO_ANSWER and not cleared and "V1 or V2" in said:
        print("  ok   a frame the controller never finishes: 'The panel didn't answer — is the driver the right one…', gone once it answers")
    else:
        print(f"  FAIL the unanswered frame should warn, then clear: {said!r} / {cleared!r}")
        FAILED.append("panel warning")

    # the service, running: the driver changed under it, as from Settings → Screen. The request comes
    # back at once even while the old HAT is slow to answer; the old panel is released before the new
    # one claims the lines (they share them); the new one draws; the keys of a 2.7" come and go
    import tempfile
    from pie_ink import service as _svc, settings as _cfg
    saved_path = _cfg.CONFIG_PATH
    _cfg.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="pie-ink-panels-"), "config.yaml")
    saved_env = os.environ.pop("PIE_INK_DRIVER", None)
    svc = None
    try:
        svc = _svc.DisplayService()
        svc.apply_settings({"display": {"driver": "epd2in13_V4"}})
        svc.start()
        svc.set_mode("clock")
        end = _t.time() + 5
        while _t.time() < end and svc._frames < 1:
            _t.sleep(0.05)
        okay = svc.status()["panel"]["driver"] == "epd2in13_V4" and svc._frames >= 1 and svc.status()["error"] is None
        print(f"  {'ok  ' if okay else 'FAIL'} the service draws on the 2.13\" V4 ({svc._frames} frames, error {svc.status()['error']})")
        if not okay:
            FAILED.append("service on V4")
        for name, keys in (("epd2in7_V2", True), ("epd2in7", True), ("epd3in7", False), ("epd2in13_V3", False), ("epd2in13_V4", False)):
            bus["busy"] = 1 if name == "epd2in7" else 0
            before = svc._frames
            t0 = _t.time()
            svc.apply_settings({"display": {"driver": name}})
            took = _t.time() - t0
            at_once = svc.status()["panel"]["driver"]
            end = _t.time() + 6
            while _t.time() < end and (svc._frames <= before or not svc.panel.is_open):
                _t.sleep(0.05)
            st = svc.status()
            okay = (took < 0.5 and at_once == name and st["panel_open"] and st["frames"] > before and st["error"] is None
                    and (svc._buttons is not None) == keys and svc._retired is None)
            print(f"  {'ok  ' if okay else 'FAIL'} → {name}: the page is told at once ({took * 1000:.0f} ms), "
                  f"it draws ({st['frames'] - before} frames), {'keys on GPIO 5/6/13/19' if keys else 'no keys'}, error {st['error']}")
            if not okay:
                FAILED.append(f"switch to {name}")
        # the old HAT mid-frame and slow to answer: the change still comes back at once, and lands
        class Stuck(panels.MockEInk):
            name = "stuck"

            def _push(self, image, full):
                _t.sleep(1.2)
        panels.PANELS["stuck"] = Stuck
        try:
            svc.apply_settings({"display": {"driver": "stuck"}})
            end = _t.time() + 4
            while _t.time() < end and not (svc.panel.is_open and svc.panel.name == "stuck"):
                _t.sleep(0.05)
            _t.sleep(0.3)                                      # into a frame
            t0 = _t.time()
            svc.apply_settings({"display": {"driver": "epd2in7_V2"}})
            took = _t.time() - t0
            end = _t.time() + 6
            while _t.time() < end and not (svc.panel.name == "epd2in7_V2" and svc.panel.is_open and svc._retired is None):
                _t.sleep(0.05)
            st = svc.status()
            okay = took < 0.5 and st["panel"]["driver"] == "epd2in7_V2" and st["panel_open"] and st["error"] is None
            print(f"  {'ok  ' if okay else 'FAIL'} with the old HAT mid-frame and slow, the change comes back in {took * 1000:.0f} ms and the new one takes over")
            if not okay:
                FAILED.append("switch from a stuck panel")
        finally:
            panels.PANELS.pop("stuck", None)
    except Exception as e:
        print(f"  FAIL the service switching drivers: {type(e).__name__}: {e}")
        FAILED.append("service switching")
    finally:
        if svc is not None:
            svc.shutdown(); svc.join(5)
        _cfg.CONFIG_PATH = saved_path
        if saved_env is not None:
            os.environ["PIE_INK_DRIVER"] = saved_env
        if not bus["claimed"] and bus["opens"] == bus["closes"]:
            print("  ok   shut down: no GPIO line left claimed, every SPI descriptor closed")
        else:
            print(f"  FAIL after shutdown: lines {sorted(bus['claimed'])}, SPI opened {bus['opens']} closed {bus['closes']}")
            FAILED.append("panel shutdown")


def outside_config_check(pieapp, c):
    """An editor or a script writes config.yaml while PiE-ink runs: what they wrote
    must not be written over by the running copy, and should be taken up."""
    import json as _json
    import subprocess as _sp
    import sys as _sys
    import time as _t
    from pie_ink import settings as _cfg
    root = os.path.dirname(os.path.abspath(__file__))

    def outside(patch):                       # another process writes config.yaml, as an editor or a script would
        code = ("import json, sys; sys.path.insert(0, %r); from pie_ink import settings as s; "
                "s.save(s.merge(s.load(), json.loads(sys.argv[1])))" % root)
        _t.sleep(0.02)
        return _sp.run([_sys.executable, "-c", code, _json.dumps(patch)], capture_output=True, text=True, cwd=root)
    r = outside({"llm": {"host": "http://192.0.2.50:11434", "model": "pretend:7b", "enabled": True},
                 "home": {"enabled": True, "url": "http://192.0.2.60:8123", "token": "outside-token", "tv": "media_player.outside_tv"},
                 "duco": {"show": True}, "verus": {"show": False}})
    if r.returncode != 0:
        print(f"  FAIL the outside write: {r.stderr[-300:]}")
        FAILED.append("outside write")
        return
    if not _cfg.changed_on_disk():
        print("  FAIL the outside write should show as a change on disk")
        FAILED.append("config changed on disk")
    # a save from the page meanwhile keeps what was written outside, and adds its own change
    check("PUT /api/settings clock (with an outside change pending)", c.put("/api/settings", json={"clock": {"seconds": True}}))
    st = (c.get("/api/status").get_json() or {}).get("settings") or {}
    if st.get("llm", {}).get("host") != "http://192.0.2.50:11434" or st.get("llm", {}).get("model") != "pretend:7b" \
            or st.get("home", {}).get("token") != "outside-token" or st.get("clock", {}).get("seconds") is not True:
        print(f"  FAIL a save from the page wrote over what was written outside: llm {st.get('llm', {}).get('host')} "
              f"{st.get('llm', {}).get('model')}, home token {st.get('home', {}).get('token')!r}, clock {st.get('clock', {}).get('seconds')}")
        FAILED.append("outside config kept")
    else:
        print("  ok   what was written outside (the bot, Home Assistant) survives a save from the page, which adds its own change")
    if _cfg.changed_on_disk():
        FAILED.append("config stamp after save")
    # and a change made outside is taken up by itself, with the parts of the app that keep their own copy told
    outside({"home": {"tv": "media_player.other_tv", "remote": "remote.other_tv"}})
    took = pieapp._config_check()
    from pie_ink import home as _home
    if not took or _home.HOME.conf.get("tv") != "media_player.other_tv" or _home.HOME.conf.get("remote") != "remote.other_tv" \
            or pieapp.service.config["home"]["tv"] != "media_player.other_tv":
        print(f"  FAIL an outside change should be taken up while running: took {took}, home has {_home.HOME.conf.get('tv')}")
        FAILED.append("outside config taken up")
    else:
        print("  ok   a change written outside is taken up within seconds, and Home Assistant's module told")
    if pieapp._config_check():
        FAILED.append("config check idempotent")
    c.put("/api/settings", json={"home": {"enabled": False, "token": "", "tv": "", "remote": "", "url": "http://homeassistant.local:8123"},
                                 "llm": {"enabled": False, "host": "http://127.0.0.1:11434", "model": ""},
                                 "duco": {"show": True}, "verus": {"show": True}, "clock": {"seconds": False}})


def shared_check(pieapp, c):
    """/api/shared: what this Pi has fetched, with how old each piece is, for another Pi on
    the network (gpi-media asks it before fetching the same weather or coin itself). Only
    what's cached, in the shapes the fetchers keep; the bundled seed figures don't count;
    and asking fetches nothing."""
    import threading as _th
    from pie_ink import cache as _cache, coingecko as _cg, weather as _wx
    nets = []
    real = (_wx._get, _cg._get)

    def no_net(*a, **k):                                   # (a background refresh of its own doesn't count)
        if _th.current_thread() is _th.main_thread():
            nets.append(a)
        raise RuntimeError("no network in this check")
    _wx._get = _cg._get = no_net
    keys = ("weather_32.716_-117.161_F", "nws_32.716_-117.161", "coin_bitcoin", "hist_bitcoin_7")
    try:
        for k in keys:
            _cache.clear(k)
        d = (c.get("/api/shared").get_json() or {})
        coins0 = [x["id"] for x in d.get("coins") or []]
        okay = d.get("ok") and d.get("weather") is None and not coins0
        print(f"  {'ok  ' if okay else 'FAIL'} nothing fetched yet: /api/shared says so (no weather, no coins) rather than offering seed figures")
        if not okay:
            FAILED.append("shared empty")
            print(f"       {str(d)[:200]}")
        # as a fetch would leave the cache: the forecast, the US worded forecast, a coin and its week
        _cache.write(keys[0], {"temp": 71.2, "feels": 70.1, "humidity": 60, "wind": 8.3, "wind_dir": 250, "code": 1, "is_day": True,
                               "precip": 0, "units": "F", "tz": "America/Los_Angeles",
                               "days": [{"date": "2026-10-22", "code": 1, "hi": 74.0, "lo": 61.0, "rain": 5, "sunrise": "07:02", "sunset": "18:10"}]})
        _cache.write(keys[1], [["This Afternoon", "Sunny, with a high near 74."], ["Tonight", "Clear, with a low around 61."]])
        _cache.write(keys[2], {"price": 105000.0, "ath": 126000.0, "ath_date": "2026-01-10", "from_ath": -16.7, "market_cap": 2.1e12, "rank": 1,
                               "total_supply": 21000000, "circulating_supply": 19900000, "sentiment_up": 71.0, "high_24h": 106000.0,
                               "low_24h": 103500.0, "change": {"24h": 0.8, "7d": -2.1, "30d": 5.0, "200d": 20.0, "1y": 40.0}})
        _cache.write(keys[3], [103000.0 + i * 10 for i in range(168)])
        d = (c.get("/api/shared").get_json() or {})
        w = d.get("weather") or {}
        btc = next((x for x in d.get("coins") or [] if x.get("id") == "bitcoin"), None)
        okay = (d.get("ok") and abs(w.get("lat", 0) - 32.7157) < 1e-4 and w.get("units") == "F" and "San Diego" in w.get("place", "")
                and w.get("country") == "US" and isinstance(w.get("age"), (int, float)) and 0 <= w["age"] < 30
                and (w.get("data") or {}).get("temp") == 71.2 and len((w.get("data") or {}).get("days") or []) == 1
                and w.get("nws") and w["nws"][0][0] == "This Afternoon" and isinstance(w.get("nws_age"), (int, float))
                and btc and btc.get("symbol") == "BTC" and (btc.get("data") or {}).get("price") == 105000.0
                and (btc.get("data") or {}).get("change", {}).get("24h") == 0.8 and 0 <= btc.get("age", 99) < 30
                and len(btc.get("graph") or []) == 168 and btc.get("graph_days") == 7 and isinstance(btc.get("graph_age"), (int, float)))
        print(f"  {'ok  ' if okay else 'FAIL'} what's cached is shared with its age: the weather for the spot (with the worded forecast) "
              f"and bitcoin with its {btc and len(btc.get('graph') or [])}-point week")
        if not okay:
            FAILED.append("shared data")
            print(f"       weather {str(w)[:160]} … coin {str(btc)[:160]}")
        if nets:
            print(f"  FAIL /api/shared fetched from the internet for the asker ({len(nets)} calls); it should only hand over what it has")
            FAILED.append("shared fetches")
        else:
            print("  ok   it hands over what it has and fetches nothing for the asker")
    finally:
        _wx._get, _cg._get = real
        for k in keys:
            _cache.clear(k)


def full_share_check(pieapp, c):
    """What's shared is all there is: the forecast's hours and its week (fetched in the one request), and
    every chart of a coin or a share that's been fetched, whatever its range — thinned, so the answer stays
    small — beside the fields the other Pis already read, as they were."""
    import json as _json
    import time as _t
    from pie_ink import cache as _cache, weather as _wx

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"shared: {name}")

    # the forecast: hours from this one on, and the week, out of one request
    asked = []
    hours = [f"2026-10-28T{h:02d}:00" for h in range(24)] + [f"2026-10-29T{h:02d}:00" for h in range(24)]
    fake = {"timezone": "America/Los_Angeles",
            "current": {"time": "2026-10-28T18:45", "temperature_2m": 68.0, "apparent_temperature": 67.0, "relative_humidity_2m": 60,
                        "weather_code": 2, "wind_speed_10m": 8.0, "wind_direction_10m": 270, "is_day": 0, "precipitation": 0},
            "hourly": {"time": hours, "temperature_2m": [50 + i * 0.5 for i in range(48)], "weather_code": [i % 4 for i in range(48)],
                       "precipitation_probability": [i % 100 for i in range(48)], "is_day": [1 if 7 <= i % 24 < 18 else 0 for i in range(48)]},
            "daily": {"time": [f"2026-10-{28 + i}" if 28 + i <= 31 else f"2026-11-{28 + i - 31:02d}" for i in range(7)],
                      "weather_code": [1] * 7, "temperature_2m_max": [70 + i for i in range(7)], "temperature_2m_min": [55] * 7,
                      "sunrise": ["2026-10-28T07:05"] * 7, "sunset": ["2026-10-28T18:02"] * 7,
                      "precipitation_probability_max": [10] * 7}}
    real_get = _wx._get
    key = "weather_1.234_5.678_F"
    try:
        _cache.clear(key)
        _wx._get = lambda url, params, timeout=12: asked.append(params) or _json.loads(_json.dumps(fake))
        got = _wx.fetch(1.234, 5.678, "F")
    finally:
        _wx._get = real_get
        _cache.clear(key)
    h = (got or {}).get("hours") or []
    note("the forecast comes with the hours ahead (24, from this one on) and the week, in the one request",
         len(asked) == 1 and "temperature_2m" in asked[0].get("hourly", "") and asked[0].get("forecast_days") == 7
         and len(h) == 24 and h[0]["time"] == "2026-10-28T18:00" and h[0]["temp"] == 59.0 and h[0]["rain"] == 18
         and h[0]["is_day"] is False and h[-1]["time"] == "2026-10-29T17:00"
         and len(got.get("days") or []) == 7 and got["days"][6]["hi"] == 76 and got["days"][0]["sunrise"] == "07:05"
         and got.get("temp") == 68.0, f"{len(asked)} {asked[:1]} {h[:1]} {len(got.get('days') or [])}")

    # every chart: a coin's ranges as they were fetched
    keys = ["coin_bitcoin", "hist_bitcoin_1", "hist_bitcoin_7", "hist_bitcoin_30", "hist_bitcoin_365", "hist_bitcoin_x"]
    try:
        for k in keys:
            _cache.clear(k)
        _cache.write("coin_bitcoin", {"price": 105000.0, "market_cap": 2.1e12, "rank": 1, "change": {"24h": 0.8}})
        _cache.write("hist_bitcoin_1", [104000.0 + i for i in range(288)])
        _cache.write("hist_bitcoin_7", [103000.0 + i * 10 for i in range(168)])
        _cache.write("hist_bitcoin_30", [90000.0 + i * 1.23456789 for i in range(720)])
        _cache.write("hist_bitcoin_365", [60000.0 + i * 45 for i in range(1000)])
        _cache.write("hist_bitcoin_x", [1, 2, 3])
        saved_list = None
        from pie_ink import watchlist as _wl
        if not any(i["id"] == "bitcoin" for i in _wl.load()):
            saved_list = _wl.load()
            _wl.save(saved_list + [{"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "kind": "crypto"}])
        try:
            r = c.get("/api/shared")
            d = r.get_json() or {}
        finally:
            if saved_list is not None:
                _wl.save(saved_list)
        btc = next((x for x in d.get("coins") or [] if x.get("id") == "bitcoin"), {})
        graphs = {g["days"]: g for g in btc.get("graphs") or []}
        note("every chart a coin has, whatever its range (1D, 1W, 1M, 1Y), each with its age — in the shape a Pietato reads",
             all(set(g) == {"days", "v", "age"} for g in btc.get("graphs") or [{}]) and sorted(graphs) == [1, 7, 30, 365] and len(graphs[1]["v"]) == 288 and len(graphs[7]["v"]) == 168
             and all(isinstance(g["age"], (int, float)) and 0 <= g["age"] < 60 for g in graphs.values()), str(sorted(graphs)))
        note("…a long one thinned to 500 prices, its first and newest kept, each to six figures",
             len(graphs[30]["v"]) == 500 and len(graphs[365]["v"]) == 500 and graphs[365]["v"][0] == 60000.0
             and graphs[365]["v"][-1] == float(f"{60000.0 + 999 * 45:.6g}") and graphs[30]["v"][1] == float(f"{90000.0 + 1 * 1.23456789:.6g}"),
             f"{len(graphs.get(30, {}).get('v', []))} {graphs.get(365, {}).get('v', [None])[-1]}")
        note("…and what the other Pis read already, as it was: the Finance tab's own range in graph and graph_days",
             btc.get("graph_days") == int(pieapp.service.config.get("crypto", {}).get("graph_days", 7) or 7)
             and (btc.get("graph_days") != 7 or len(btc.get("graph") or []) == 168) and btc.get("data", {}).get("price") == 105000.0
             and len(r.data) < 3_000_000, f"{btc.get('graph_days')} {len(btc.get('graph') or [])} {len(r.data)}")
    finally:
        for k in keys:
            _cache.clear(k)

    # a share: its usual ranges, cut from the prices Yahoo gave
    now = _t.time()
    skeys = ["stock_TESTX", "stockhist_TESTX_1mo_1h", "stockhist_TESTX_1y_1d"]
    try:
        _cache.write("stock_TESTX", {"price": 10.0, "today": [9.9 + i * 0.001 for i in range(78)]})
        _cache.write("stockhist_TESTX_1mo_1h", [[now - (30 * 24 - i) * 3600, 9.0 + i * 0.001] for i in range(30 * 24)])
        _cache.write("stockhist_TESTX_1y_1d", [[now - (365 - i) * 86400, 5.0 + i * 0.01] for i in range(365)])
        sg = {g["days"]: g for g in pieapp._stock_graphs("TESTX")}
        note("a share's charts for the usual ranges, each cut from what Yahoo gave (no 3-month one: that wasn't fetched)",
             sorted(sg) == [1, 7, 30, 180, 365] and len(sg[1]["v"]) == 78 and 160 <= len(sg[7]["v"]) <= 170
             and len(sg[30]["v"]) == 500 and 175 <= len(sg[180]["v"]) <= 182 and 360 <= len(sg[365]["v"]) <= 365,
             str({k: len(v["v"]) for k, v in sg.items()}))
    finally:
        for k in skeys:
            _cache.clear(k)


def two_lens_check(pieapp, c):
    """A camera with two lenses sends both views side by side in one wide frame: that's
    noticed, the two can be swapped or one shown alone — on the page, the screen and in
    a photo — and a frame turned 180° with the swap has each view turned in place."""
    import io as _io
    import threading as _th
    import time as _t
    from PIL import Image as _Img, ImageDraw as _Draw
    from pie_ink import camera as _cam, photos as _photos

    def pair(w, h):                           # the left view blue (darker), the right red
        im = _Img.new("RGB", (w, h), (40, 60, 160))
        _Draw.Draw(im).rectangle([w // 2, 0, w, h], fill=(170, 50, 40))
        buf = _io.BytesIO(); im.save(buf, "JPEG", quality=85); return buf.getvalue()

    class Stereo:
        name, id, format, raw = "pretend dual lens (video9)", "usb:/dev/video9", "1280x480 MJPG, passed through", True
        def read(self): _t.sleep(0.04); return pair(1280, 480)
        def still(self, most=None): return pair(3840, 1080)
        def close(self): pass

    def sides(data):                          # (width, height, left half's colour, right half's)
        im = _Img.open(_io.BytesIO(data)); im.load(); w, h = im.size
        col = lambda box: "blue" if (p := im.crop(box).resize((1, 1)).getpixel((0, 0)))[2] > p[0] else "red"   # noqa: E731
        return w, h, col((0, 0, w // 2, h)), col((w // 2, 0, w, h))

    open0 = _cam.Camera._open
    _cam.Camera._open = lambda self, preferred: Stereo()
    had = set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()
    try:
        pieapp.CAMERA.switch("usb:/dev/video9")
        pieapp.CAMERA.touch(30)
        for _ in range(60):
            if pieapp.CAMERA.jpeg() and pieapp.CAMERA.status().get("stereo"):
                break
            _t.sleep(0.1)
        st = pieapp.CAMERA.status()
        got = sides(c.get("/api/camera.jpg").data)
        okay = st.get("stereo") and got == (1280, 480, "blue", "red")
        print(f"  {'ok  ' if okay else 'FAIL'} a 1280x480 frame is seen as two views side by side, passed on as it is: {got}")
        if not okay:
            FAILED.append("two lenses noticed")
        c.put("/api/settings", json={"camera": {"swap_lenses": True}})
        _t.sleep(0.3)
        got = sides(c.get("/api/camera.jpg").data)
        fmt = pieapp.CAMERA.status().get("format", "")
        okay = got == (1280, 480, "red", "blue") and "swapped" in fmt and "passed through" not in fmt
        print(f"  {'ok  ' if okay else 'FAIL'} Swap the two views: each on the other side, and the status says so: {got}, {fmt!r}")
        if not okay:
            FAILED.append("two lenses swapped")
        c.put("/api/settings", json={"camera": {"swap_lenses": False, "lens": "right"}})
        _t.sleep(0.3)
        got = sides(c.get("/api/camera.jpg").data)
        okay = got[:3] == (640, 480, "red")
        print(f"  {'ok  ' if okay else 'FAIL'} one lens alone: the right view, 640x480: {got[:3]}")
        if not okay:
            FAILED.append("one lens")
        r = c.post("/api/photos", json={"filter": "none", "rot": 0})
        name = ((r.get_json() or {}).get("photo") or {}).get("name")
        photo = sides(open(os.path.join(_photos.DIR, name), "rb").read()) if name else None
        okay = r.status_code == 200 and photo and photo[:3] == (960, 540, "red")
        print(f"  {'ok  ' if okay else 'FAIL'} a photo is that view too, from the camera's big 3840x1080 picture: {photo}")
        if not okay:
            FAILED.append("one lens photo")
        # turned 180° on the screen, with the swap: each view turned in place, so the left lens stays on the left
        c.put("/api/settings", json={"camera": {"lens": "both", "swap_lenses": True, "rotation": 180, "dither": False, "auto_contrast": True}})
        _t.sleep(0.3)
        pieapp.service.set_mode("camera")
        _t.sleep(0.5)
        r = c.post("/api/render.png", json={"mode": "camera"})
        im = _Img.open(_io.BytesIO(r.data)).convert("L"); w, h = im.size
        left = im.crop((0, 0, w // 3, h)).resize((1, 1)).getpixel((0, 0)); right = im.crop((2 * w // 3, 0, w, h)).resize((1, 1)).getpixel((0, 0))
        okay = r.status_code == 200 and left < right                 # the darker (blue, left-lens) view back on the left
        print(f"  {'ok  ' if okay else 'FAIL'} turned 180° with the swap, the screen has each view turned in place (left {left} < right {right})")
        if not okay:
            FAILED.append("two lenses turned")
    finally:
        _cam.Camera._open = open0
        c.put("/api/settings", json={"camera": {"lens": "both", "swap_lenses": False, "rotation": 0, "dither": True, "auto_contrast": True}})
        pieapp.CAMERA.switch("mock")
        pieapp.service.set_mode("clock")
        for n in (set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()) - had:
            _photos.delete(n)


def snake_check(pieapp, c, ollama):
    """The bot plays Snake on the little screen: the game runs while that screen is up, the
    model (the fake tev1) is asked as a choice and listened to, the arena is the screen,
    a crash starts a new game, and boredom puts it up — a touch takes it down again."""
    import io as _io
    import os as _os
    import time as _t
    from PIL import Image as _Image
    from pie_ink import attention as _attention, snake as _snake

    def status():
        return c.get("/api/snake").get_json() or {}

    def wait_for(cond, seconds):
        st = status()
        for _ in range(int(seconds * 10)):
            if cond(st):
                break
            _t.sleep(0.1)
            st = status()
        return st

    print("the bot plays Snake on the little screen")
    ollama.snake_play, ollama.snake_asks = "well", []
    check("PUT /api/settings lcd snake (fast, for the test)", c.put("/api/settings", json={
        "lcd": {"enabled": True, "model": "1.69", "rotation": 0, "screen": "snake"},
        "snake": {"model": "tev1", "speed": 150, "decide_ms": 120, "safety": False, "bored_minutes": 20}}))
    st = wait_for(lambda d: d.get("score", 0) >= 1 and not d.get("error"), 10)
    if not st.get("playing") or st.get("error") or st.get("score", 0) < 1 or st.get("arena") != [240, 280]:
        print(f"  FAIL the game should be running at the screen's size and eating: {st}")
        FAILED.append("snake plays")
    else:
        print(f"  ok   playing on {st['arena'][0]}x{st['arena'][1]}: score {st['score']} after {st['decisions']} decisions, {st['avg_ms']} ms each")
    ask = ollama.snake_asks[-1] if ollama.snake_asks else {}
    q = (ask.get("questions") or {}).get("move") or {}
    if ask.get("model") != "tev1" or q.get("type") != "choice" or "food" not in (q.get("criteria") or {}) or "arena" not in (ask.get("state") or ""):
        print(f"  FAIL the model should be asked for a choice, with the facts about each move: {str(ask)[:200]}")
        FAILED.append("snake question")
    else:
        print(f"  ok   asked through /v1/systemone as a choice of {len(q['criteria'])} moves, with the facts about each")
    if "playing Snake" not in _snake.GAME.words():
        print(f"  FAIL the bot should know it is playing: {_snake.GAME.words()!r}")
        FAILED.append("snake words")
    r = c.get("/api/lcd/preview.png")
    check("GET /api/lcd/preview.png (snake)", r)
    if r.status_code == 200:
        im = _Image.open(_io.BytesIO(r.data)).convert("RGB")
        colours = {colour for _, colour in (im.getcolors(maxcolors=1 << 20) or [])}
        if im.size != (240, 280) or _snake.COLOURS["food"] not in colours or _snake.COLOURS["head"] not in colours:
            print(f"  FAIL the LCD frame should be the arena with the snake and the food on it: {im.size}")
            FAILED.append("snake frame")
        else:
            print("  ok   the little screen shows the arena, the snake and the food")
    # the page previewing another panel, not saved: a scaled picture, not a new game
    r = c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": "snake", "model": "1.9", "rotation": 90}}})
    check("POST /api/lcd/preview.png snake (another panel, not saved)", r)
    if r.status_code == 200:
        got = _Image.open(_io.BytesIO(r.data)).size
        after = status()
        if got != (320, 170) or after.get("arena") != [240, 280]:
            print(f"  FAIL a preview at another size should be scaled, not a new game: {got}, arena {after.get('arena')}")
            FAILED.append("snake preview size")
        else:
            print("  ok   a preview for a panel not saved yet is scaled; the game goes on")
    # the screen itself changing shape: a new game that size
    check("PUT /api/settings lcd 1.9 sideways (snake)", c.put("/api/settings", json={"lcd": {"model": "1.9", "rotation": 90}}))
    st = wait_for(lambda d: d.get("arena") == [320, 170], 5)
    r = c.get("/api/lcd/preview.png")
    size = _Image.open(_io.BytesIO(r.data)).size if r.status_code == 200 else None
    if st.get("arena") != [320, 170] or size != (320, 170):
        print(f"  FAIL the arena should follow the screen's shape: arena {st.get('arena')}, frame {size}")
        FAILED.append("snake new size")
    else:
        print("  ok   the arena follows the screen: 320x170 sideways")
    r = c.get("/api/now")
    check("GET /api/now (playing)", r, body_has=["snake"])
    tile = (r.get_json() or {}).get("snake") or {}
    if "score" not in tile or tile.get("bored") is not False:
        print(f"  FAIL the Now tile should carry the score: {tile}")
        FAILED.append("snake now tile")
    # a crash: the game is recorded and a new one starts by itself
    games = status().get("games", 0)
    ollama.snake_play = "badly"
    st = wait_for(lambda d: d.get("games", 0) > games, 12)
    ollama.snake_play = "well"
    if st.get("games", 0) <= games:
        print(f"  FAIL driving into the wall should end the game: {st}")
        FAILED.append("snake dies")
    else:
        st = wait_for(lambda d: d.get("alive") and d.get("decisions", 0) >= 1, 8)
        if not st.get("alive"):
            print(f"  FAIL a new game should start after a crash: {st}")
            FAILED.append("snake restarts")
        elif not _os.path.exists(_snake.PATH) or st.get("best", 0) < 1:
            print(f"  FAIL the scores should be kept: best {st.get('best')}, file {_os.path.exists(_snake.PATH)}")
            FAILED.append("snake record")
        else:
            print(f"  ok   crashed ({st['games']} game{'s' if st['games'] != 1 else ''} recorded, best {st['best']}) and started again")
    # bored: nothing from anyone for a while and Snake goes up over whatever the screen showed; a touch puts that back
    check("PUT /api/settings lcd clock, bored after a minute", c.put("/api/settings", json={
        "lcd": {"model": "1.69", "rotation": 0, "screen": "clock"}, "snake": {"bored_minutes": 1}}))
    _t.sleep(0.3)
    if pieapp._bored_check() or pieapp._bored["from"] is not None:
        print("  FAIL just after a touch it is not bored")
        FAILED.append("snake bored too soon")
    _attention._last = _t.time() - 61
    went = pieapp._bored_check()
    _t.sleep(0.5)
    lcd = (c.get("/api/lcd").get_json() or {}).get("status", {})
    saved = ((c.get("/api/status").get_json() or {}).get("settings", {}).get("lcd", {})).get("screen")
    bored = status()
    if not went or pieapp._bored["from"] != "clock" or lcd.get("screen") != "snake" or saved != "clock" or not bored.get("bored") or not bored.get("playing"):
        print(f"  FAIL a minute of quiet should put Snake up over the clock: went {went}, from {pieapp._bored['from']}, lcd {lcd.get('screen')}, saved {saved}, {bored}")
        FAILED.append("snake bored")
    else:
        print("  ok   bored after a quiet minute: Snake is up, the saved screen is still the clock")
    # what it says it is doing: while Snake is up, and when the question itself ends the game (asking is a touch)
    from pie_ink import doing as _doing
    words = _doing.sentence(pieapp.service.config)
    if "playing Snake on the little screen to pass the time" not in words or "e-ink" not in words:
        print(f"  FAIL while bored it should say it is playing Snake to pass the time: {words!r}")
        FAILED.append("doing snake")
    r = c.post("/api/llm/chat", json={"message": "what are you doing?"})
    check("POST /api/llm/chat 'what are you doing?' (answered here, a touch)", r, body_has=["reply", "home"])
    reply = (r.get_json() or {}).get("reply") or ""
    if "just back from a game of Snake" not in reply or "ollama" in reply.lower() or "fake" in reply.lower():
        print(f"  FAIL asked what it is doing it should own up to the game it just left: {reply!r}")
        FAILED.append("doing asked")
    else:
        print(f"  ok   'what are you doing?' → {reply[:110]!r}")
    _t.sleep(0.6)
    lcd = (c.get("/api/lcd").get_json() or {}).get("status", {})
    if pieapp._bored["from"] is not None or lcd.get("screen") != "clock" or status().get("bored"):
        print(f"  FAIL a touch should bring the clock back: from {pieapp._bored['from']}, lcd {lcd.get('screen')}")
        FAILED.append("snake touch")
    else:
        print("  ok   the question counted as a touch: the clock is back")
    if not _doing.asked("whatcha up to") or not _doing.asked("are you busy?") or _doing.asked("what are you doing tomorrow") or _doing.asked("what's the weather"):
        print("  FAIL the question should be recognised in its usual forms, and not as a question about plans")
        FAILED.append("doing asked forms")
    check("PUT /api/settings snake bored 20 min, lcd off", c.put("/api/settings", json={"snake": {"bored_minutes": 20, "speed": 70, "decide_ms": 300}, "lcd": {"enabled": False}}))


def gesture_check(pieapp, c):
    """Hand gestures through the camera, for the TV and the lights: ha-gesture-remote's gestures (the knob,
    the fist's arrows, the taps, the holds) read from MediaPipe's 21 points per hand, at ten frames a second
    and at five; the lights added (the knob with the other hand open, both hands open); remappable; sent
    to Home Assistant from a thread of their own (an arrow never queued behind one on its way); the loop
    over the camera's picture (one lens of two, made small first); the page's report and picture; setup.sh
    installing the tracker only where it runs."""
    import io as _io
    import math as _m
    import threading as _th
    import time as _t
    from PIL import Image as _I
    from pie_ink import camera as _cam, gestures as _g, home as _home, llm as _llm
    from pie_ink.modes.simple import bot_face as _bf

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"gestures: {name}")

    # a hand, drawn: palm to the camera, fingers up, in hand sizes from the wrist
    OPEN = [(0, 0), (-0.35, -0.2), (-0.6, -0.45), (-0.8, -0.7), (-0.95, -0.95),
            (-0.3, -0.95), (-0.35, -1.45), (-0.38, -1.75), (-0.4, -2.0),
            (0.0, -1.0), (0.0, -1.55), (0.0, -1.85), (0.0, -2.15),
            (0.28, -0.95), (0.33, -1.45), (0.36, -1.72), (0.38, -1.95),
            (0.52, -0.85), (0.62, -1.2), (0.68, -1.42), (0.72, -1.6)]

    def shape(kind):
        p = list(OPEN)
        thumb = p[4]
        if kind == "fist":
            p[8], p[12], p[16], p[20] = (-0.3, -0.6), (0.0, -0.62), (0.25, -0.6), (0.42, -0.55)
            p[4] = (-0.1, -0.75)
        elif kind in ("index", "middle", "ring", "pinky"):
            tip = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}[kind]
            p[tip] = (thumb[0] + 0.05, thumb[1])
        return p

    def hand(kind="open", at=(0.5, 0.6), size=0.15, roll=0.0):
        cr, sr = _m.cos(roll), _m.sin(roll)
        return {"points": [(at[0] + size * (x * cr - y * sr), at[1] + size * (x * sr + y * cr)) for x, y in shape(kind)],
                "score": 0.95}

    class Act:
        def __init__(self, levels=None):
            self.pressed, self.levels, self.done, self.arrows, self.stops = [], [], [], [], 0
            self.known = dict(levels or {"volume": 0.3, "brightness": 0.5})
            self.busy = False

        def press(self, action):
            self.pressed.append(action)
            return action.upper()

        def level(self, what):
            return self.known.get(what)

        def set_level(self, what, level):
            self.levels.append((what, round(level, 3)))

        def levelled(self, what, level):
            self.done.append((what, round(level, 3)))

        def dpad(self, d):
            self.arrows.append(d)

        def dpad_busy(self):
            return self.busy

        def dpad_stop(self):
            self.stops += 1

        def limit(self, what):
            return 0.8 if what == "volume" else 1.0

    def play(script, fps=10, slots=None, levels=None, mirror=False, start=1000.0):
        """script: [(seconds, [hands] or a function of the time into it)] — run through the engine."""
        act = Act(levels)
        eng = _g.Engine(act, slots)
        t = start
        for secs, hands in script:
            n = max(1, int(round(secs * fps)))
            for i in range(n):
                hs = hands(i / n) if callable(hands) else hands
                eng.step(_g.analyze(hs, mirror=mirror), t)
                t += 1 / fps
        return act, eng

    o = [hand("open")]
    shapes = {k: _g.analyze([hand(k)], mirror=False)[0] for k in ("open", "fist", "index", "middle", "ring", "pinky")}
    note("the shapes, from the 21 points: open, a fist, each pinch",
         shapes["open"]["open"] and shapes["fist"]["curled"] == 4 and shapes["index"]["ratios"]["index"] < 0.2
         and _g.repeat_pinch(shapes["middle"]) == "middle" and _g.repeat_pinch(shapes["ring"]) == "ring"
         and shapes["pinky"]["ratios"]["pinky"] < 0.26 and abs(shapes["open"]["size"] - 0.15) < 0.01,
         str({k: (v["curled"], round(min(v["ratios"].values()), 2)) for k, v in shapes.items()}))

    for fps in (10, 5):
        idle, _e = play([(3.0, o)], fps)
        twist, _e = play([(0.5, o), (1.0, lambda f: [hand("index", roll=_m.radians(45 * f))]), (0.5, [hand("index", roll=_m.radians(45))]),
                          (0.6, [hand("open", roll=_m.radians(45))])], fps)
        vols = [lv for w, lv in twist.levels if w == "volume"]
        note(f"at {fps} a second: an open hand does nothing; pinch and twist 45° turns the volume up from where it was, and says so after",
             not idle.pressed and not idle.levels and vols and vols[-1] > 0.38 and vols[-1] < 0.45 and vols == sorted(vols)
             and twist.done and twist.done[-1][0] == "volume" and not twist.pressed, f"{vols[-3:]} {twist.done} {twist.pressed}")
        both = lambda h: [h, hand("open", at=(0.15, 0.6), size=0.14)]
        bright, _e = play([(0.3, both(hand("open"))), (1.0, lambda f: both(hand("index", roll=_m.radians(-60 * f)))),
                           (0.4, both(hand("index", roll=_m.radians(-60)))), (0.6, [hand("open", roll=_m.radians(-60))])], fps)
        lv = [l for w, l in bright.levels if w == "brightness"]
        note(f"…with the other hand open, the lights' brightness instead (twisted back: dimmer)",
             lv and lv[-1] < 0.40 and not [x for x in bright.levels if x[0] == "volume"], str(lv[-3:]))
        hold_tap = 0.3 if fps == 10 else 0.4
        tap, _e = play([(0.4, both(hand("open"))), (hold_tap, both(hand("index"))), (0.6, both(hand("open"))), (0.5, o)], fps)
        bare, _e = play([(0.5, o), (hold_tap, [hand("index")]), (0.8, o)], fps)
        note("…a quick pinch with the other hand open: play/pause (a bare one: nothing, so a quick volume touch can't pause)",
             tap.pressed == ["play_pause"] and not bare.pressed, f"{tap.pressed} {bare.pressed}")
        push, _e = play([(0.5, o), (0.4, [hand("fist")]), (0.5, lambda f: [hand("fist", at=(0.5 + 0.16 * f, 0.6))]),
                         (1.6, [hand("fist", at=(0.66, 0.6))]), (0.6, o)], fps)
        note("…a fist pushed right: the right arrow, again while it's held, stopped when it opens",
             push.arrows and set(push.arrows) == {"right"} and len(push.arrows) >= 3 and push.stops >= 1 and not push.pressed,
             f"{push.arrows} {push.stops} {push.pressed}")
        fist_tap, _e = play([(0.5, o), (0.3 if fps == 10 else 0.4, [hand("fist")]), (0.8, o)], fps)
        back, _e = play([(0.5, o), (0.3 if fps == 10 else 0.4, [hand("pinky")]), (0.8, o)], fps)
        mute, _e = play([(0.5, o), (1.5, [hand("pinky")]), (0.8, o)], fps)
        note("…a quick fist: OK; thumb to pinky, quick: back; held a second: mute (and not back)",
             fist_tap.pressed == ["select"] and back.pressed == ["back"] and mute.pressed == ["mute"],
             f"{fist_tap.pressed} {back.pressed} {mute.pressed}")
        two = lambda k: [hand(k, at=(0.7, 0.6)), hand(k, at=(0.3, 0.6))]
        power, _e = play([(0.5, o), (1.8, two("fist")), (0.8, o)], fps)
        lights, _e = play([(0.5, o), (1.9, two("open")), (0.8, o)], fps)
        brief, _e = play([(0.5, o), (1.0, two("open")), (0.8, o)], fps)
        note("…both fists held: the TV on or off, once (no arrows); both hands open held 1.5 s: the lights on or off, once "
             "(a second's two open hands — one of them just the other's modifier — isn't)",
             power.pressed == ["power"] and not power.arrows and lights.pressed == ["lights"] and not brief.pressed,
             f"{power.pressed} {power.arrows} {lights.pressed} {brief.pressed}")
        chan, _e = play([(0.5, o), (2.0, [hand("ring")]), (0.6, o)], fps)
        note("…thumb to ring, held: channel up, and again every 0.8 s", chan.pressed in (["next_track"] * 3, ["next_track"] * 2)
             and len(chan.pressed) >= 2, str(chan.pressed))
    far, _e = play([(0.5, [hand("open", size=0.03)]), (1.8, [hand("fist", size=0.03, at=(0.7, 0.6)), hand("fist", size=0.03, at=(0.3, 0.6))])])
    remap, _e = play([(0.5, o), (1.9, [hand("open", at=(0.7, 0.6)), hand("open", at=(0.3, 0.6))]), (0.8, o)],
                     slots={"two_open_hands_hold": "exit", "nonsense": "power"})
    wait, eng = play([(0.5, o), (1.0, lambda f: [hand("index", roll=_m.radians(40 * f))])], levels={"volume": None})
    note("hands too far off do nothing; any gesture can do another's job; the knob waits until it knows where it's starting",
         not far.pressed and remap.pressed == ["exit"] and not wait.levels and "a moment" in eng.mode, f"{far.pressed} {remap.pressed} {wait.levels} {eng.mode}")
    mirrored, _e = play([(0.5, o), (0.4, [hand("fist")]), (0.5, lambda f: [hand("fist", at=(0.5 - 0.16 * f, 0.6))]),
                         (0.4, [hand("fist", at=(0.34, 0.6))]), (0.3, o)], mirror=True)
    note("…and the camera faces you: a fist moving to your right (the picture's left) is the right arrow",
         mirrored.arrows[:1] == ["right"], str(mirrored.arrows))

    # through Home Assistant
    class House:
        def __init__(self):
            self.calls, self.lights_on, self.slow = [], False, 0.0
            self.tv_feats = 4 | 128 | 256

        def _tv(self):
            return "media_player.tv"

        def call(self, domain, service, data):
            _t.sleep(self.slow)
            self.calls.append((domain, service, dict(data)))
            if domain == "light":
                self.lights_on = service == "turn_on"
            return True, ""

        def key(self, name):
            _t.sleep(self.slow)
            self.calls.append(("key", name))
            return True, ""

        def tv_mute(self, flag):
            self.calls.append(("mute", flag))
            return True, ""

        def tv_volume(self, d):
            self.calls.append(("volume_step", d))
            return True, ""

        def tv_channel_step(self, d):
            self.calls.append(("channel", d))
            return True, ""

        def tv_power(self, want):
            self.calls.append(("power", want))
            return True, ""

        def state_of(self, eid):
            if eid == "media_player.tv":
                return {"state": "on", "attributes": {"volume_level": 0.25, "supported_features": self.tv_feats}}
            return {"state": "on" if self.lights_on else "off", "attributes": {"brightness": 128 if self.lights_on else None}}

        def features(self, entity_id=None, st=None):
            return int(((st or {}).get("attributes") or {}).get("supported_features") or 0)

        def _render(self, template):
            self.calls.append(("template", template))
            return '["light.sofa", "light.ceiling"]'

        def states(self):
            return [{"entity_id": "light.porch"}, {"entity_id": "light.sofa"}, {"entity_id": "light.ceiling"}]

    def settled(r, n, secs=3.0):
        deadline = _t.time() + secs
        while len(r.house.calls) < n and _t.time() < deadline:
            _t.sleep(0.02)
        _t.sleep(0.05)
        return r.house.calls

    said = []
    r = _g.Remote(House())
    r.on_said = lambda text, what: said.append(text)
    room = r.group()
    r.press("lights")
    calls = settled(r, 2)
    r.press("lights")
    calls = settled(r, 3)
    note("the lights: the TV's room's (as Home Assistant has the rooms), on, then off again, each said",
         room == ["light.sofa", "light.ceiling"] and ("light", "turn_on", {"entity_id": ["light.sofa", "light.ceiling"]}) in calls
         and calls[-1] == ("light", "turn_off", {"entity_id": ["light.sofa", "light.ceiling"]})
         and said[-2:] == ["The lights on.", "The lights off."] and "area_entities(area_id('media_player.tv'))" in calls[0][1]
         and "'light\\\\.'" in calls[0][1], f"{calls} {said}")
    r.lights_conf = ["light.porch"]
    r.set_level("brightness", 0.6)
    calls = settled(r, 4)
    r.set_level("brightness", 0.0)
    calls = settled(r, 5)
    note("…the ones ticked instead; the knob's brightness, and all the way down is off",
         calls[3] == ("light", "turn_on", {"entity_id": ["light.porch"], "brightness_pct": 60})
         and calls[4] == ("light", "turn_off", {"entity_id": ["light.porch"]}), str(calls[3:]))
    r2 = _g.Remote(House())
    r2.refresh()
    for lv in (0.30, 0.31, 0.32, 0.36):
        r2.set_level("volume", lv)
    deadline = _t.time() + 1.5
    while not any(x[:2] == ("media_player", "volume_set") and x[2]["volume_level"] == 0.36 for x in r2.house.calls) and _t.time() < deadline:
        _t.sleep(0.02)
    calls = r2.house.calls
    vs = [x for x in calls if x[:2] == ("media_player", "volume_set")]
    note("the volume: where the TV is, to start from; the newest level sent, a few a second at most",
         r2.volume == 0.36 and r2.tv_features & 4 and vs and vs[-1][2]["volume_level"] == 0.36 and len(vs) <= 3, str(calls))
    r3 = _g.Remote(House())
    r3.house.tv_feats = 1024 | 128
    r3.refresh()
    r3.set_level("volume", 0.30)
    settled(r3, 1, 0.5)
    r3.set_level("volume", 0.34)
    calls = settled(r3, 2, 0.8)
    note("…a TV that only steps its volume: a step up", ("volume_step", "up") in calls, str(calls))
    r4 = _g.Remote(House())
    r4.house.slow = 0.3
    r4.dpad("right")
    _t.sleep(0.05)
    busy = r4.dpad_busy()
    r4.dpad("right")
    r4.dpad("right")
    r4.dpad_stop()
    calls = settled(r4, 1, 1.0)
    _t.sleep(0.4)
    note("the arrows: the remote's own buttons; busy while one's on its way; let go, and none queued goes",
         busy and r4.house.calls == [("key", "right")], str(r4.house.calls))
    r5 = _g.Remote(House())
    r5.on_said = lambda text, what: said.append(text)
    for a in ("play_pause", "select", "power", "mute", "prev_track", "nothing-at-all"):
        r5.press(a)
    calls = settled(r5, 5)
    note("…play/pause, OK, the TV off (it's on), mute, channel down; what isn't one does nothing",
         calls == [("media_player", "media_play_pause", {"entity_id": "media_player.tv"}), ("key", "enter"), ("power", "off"),
                   ("mute", None), ("channel", "down")] and "The TV off." in said and "Play / pause." in said, f"{calls} {said}")

    # the loop, over the camera's picture
    seen = []

    class Tracker:
        def __init__(self):
            self.t0 = _t.time()

        def hands(self, img):
            seen.append(img.size)
            f = min((_t.time() - self.t0) / 1.2, 1.0)
            return [hand("index", roll=_m.radians(5 + 50 * f))] if _t.time() - self.t0 > 0.4 else [hand("open")]

        def close(self):
            pass
    G = _g.GESTURES
    keep = (G.make_tracker, G.remote, _cam.CAMERA.frame, _cam.CAMERA.touch, dict(_home.HOME.conf), G.conf)
    house = House()
    frames = [_I.new("RGB", (1280, 360), (40, 40, 40))]
    try:
        G.make_tracker = Tracker
        G.remote = _g.Remote(house)
        G.remote.on_said = pieapp._gesture_said
        _cam.CAMERA.frame = lambda: frames[0].copy()
        _cam.CAMERA.touch = lambda seconds=0: None
        _home.HOME.conf = {"enabled": True, "url": "http://127.0.0.1:9", "token": "x", "tv": "media_player.tv"}
        G.configure({"enabled": True})
        G.want_view(10)
        deadline = _t.time() + 8
        while not any(x[:2] == ("media_player", "volume_set") for x in house.calls) and _t.time() < deadline:
            _t.sleep(0.05)
        rep = c.get("/api/gestures").get_json() or {}
        view = c.get("/api/gestures/view")
        note("the loop: the camera's picture (one of two lenses, made 320 wide), the hands in it, the volume turned through Home Assistant",
             seen and seen[-1] == (320, 180) and any(x[:2] == ("media_player", "volume_set") for x in house.calls)
             and rep.get("state") == "watching" and rep.get("hands") == 1 and "VOLUME" in (rep.get("mode") or ""),
             f"{seen[-1:]} {house.calls[-2:]} {rep.get('state')} {rep.get('why')} {rep.get('mode')}")
        pic = _I.open(_io.BytesIO(view.data)) if view.status_code == 200 else None
        note("…and the page sees it as the gestures do (the hands drawn on), with what each gesture does to change",
             pic is not None and pic.size == (320, 180) and len(rep.get("slots") or []) == len(_g.SLOTS)
             and rep.get("presses", {}).get("lights") and rep.get("holds", {}).get("brightness"), f"{view.status_code}")
        frames[0] = _I.new("RGB", (640, 480))
        before = len(house.calls)
        G.remote.press("lights")
        settled(G.remote, before + 1)
        latest = _llm.latest()
        note("what a gesture did, on the bot's screen, pleased — and not said out loud over the TV",
             latest["text"] == "The lights on." and latest["kind"] == "gesture" and _bf("x", "gesture")[1] == "happy"
             and 'kind not in ("chat", "gesture")' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read(),
             str(latest))
        r = c.put("/api/settings", json={"gestures": {"enabled": False, "fist_tap": "home", "lights": ["light.sofa"]}})
        _t.sleep(0.6)
        note("settings: off stops the loop; a gesture remapped and the lights ticked are used from then on",
             r.status_code == 200 and G.state == "off" and G.engine.slots["fist_tap"] == "home" and G.remote.lights_conf == ["light.sofa"],
             f"{G.state} {G.engine.slots.get('fist_tap')} {G.remote.lights_conf}")
        G.make_tracker = keep[0]
        _home.HOME.conf = {"enabled": False}
        G.configure({"enabled": True})
        deadline = _t.time() + 4
        while G.state != "waiting" and _t.time() < deadline:
            _t.sleep(0.05)
        note("…and with Home Assistant not set up, it waits and says why", G.state == "waiting" and "Home Assistant" in G.why
             or "MediaPipe" in G.why or "model" in G.why or "64-bit" in G.why, f"{G.state} {G.why}")
        G.configure({"enabled": False})
    finally:
        G.configure({"enabled": False})
        _t.sleep(0.3)
        G.make_tracker, G.remote, _cam.CAMERA.frame, _cam.CAMERA.touch, hc, gc = keep
        _home.HOME.conf = hc
        G.conf = gc
        c.put("/api/settings", json={"gestures": {"fist_tap": "select", "lights": []}})

    here = os.path.dirname(os.path.abspath(__file__))
    sh = open(os.path.join(here, "setup.sh")).read()
    note("setup.sh installs the tracker on a 64-bit Pi only (its own packages left out, the system's numpy and OpenCV kept), "
         "with its model, and --no-gestures skips it",
         'if [[ "$(uname -m)" == "aarch64" ]]; then' in sh and '--no-deps "mediapipe==$MEDIAPIPE"' in sh and "MEDIAPIPE=1.2.2" in sh
         and "hand_landmarker/float16/1/hand_landmarker.task" in sh and _g.MODEL_URL.endswith("float16/1/hand_landmarker.task")
         and "--no-gestures) GESTURES=0" in sh and "python3-matplotlib" in sh)


def usb_check(pieapp, c):
    """Something plugged into a USB port — straight into the Pi or into a hub — and the bot's face
    changes: wide-eyed, starry for a drive, sad when it's pulled out, a hub's worth told as one; and a
    USB drive's photos, songs and documents are on the shelves, found, shown, played and read like
    the rest, never a file outside the drive; setup.sh has drives mounted (read-only) as they go in."""
    import io as _io
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    import time as _t
    from PIL import Image as _I
    from pie_ink import drives as _dr, llm as _llm, music as _mu, plugs as _pl, reader as _rd, shelf as _sf, sight as _si
    from pie_ink.modes import MODES as _MODES
    from pie_ink.modes.simple import BOT_MOODS as _BM, bot_face as _bf

    def note(name, okay, extra=""):
        print(f"  {'ok  ' if okay else 'FAIL'} {name}" + (f" — {extra}" if extra and not okay else ""))
        if not okay:
            FAILED.append(f"usb: {name}")

    tmp = _tf.mkdtemp(prefix="pie-ink-usb-")
    bus_root = os.path.join(tmp, "usb")

    def put(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def dev(bus, vid, pid, product="", maker="", dclass="00", ifaces=(), ports=0, serial=""):
        d = os.path.join(bus_root, bus)
        for k, v in (("idVendor", vid), ("idProduct", pid), ("product", product), ("manufacturer", maker),
                     ("bDeviceClass", dclass), ("maxchild", str(ports)), ("serial", serial)):
            if v:
                put(os.path.join(d, k), v + "\n")
        for n, (cl, sub, pr) in enumerate(ifaces):
            i = os.path.join(bus_root, f"{bus}:1.{n}")
            for k, v in (("bInterfaceClass", cl), ("bInterfaceSubClass", sub), ("bInterfaceProtocol", pr)):
                put(os.path.join(i, k), v + "\n")

    def pull(*buses):
        for b in buses:
            for e in os.listdir(bus_root):
                if e == b or e.startswith(b + ":"):
                    _sh.rmtree(os.path.join(bus_root, e))

    os.makedirs(bus_root)
    dev("usb1", "1d6b", "0002", "DWC OTG Controller", dclass="09", ports=1)
    dev("1-1", "1a40", "0101", "USB 2.0 Hub", dclass="09", ports=4, ifaces=[("09", "00", "00")])
    dev("1-1.1", "046d", "c31c", "USB Keyboard", "Logitech", ifaces=[("03", "01", "01"), ("03", "00", "00")])
    P = _pl.Plugs()
    P.root = bus_root
    heard = []
    P.on_change = lambda a, g, text, kind: heard.append((text, kind))
    P.look(now=0)
    note("the app hears of it (the watcher is started with the rest)", _pl.PLUGS.on_change is pieapp._usb_changed
         and "plugs.PLUGS.start()" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read())
    note("what was plugged in when it started isn't news (the hub, the keyboard on it)", P.look(now=1) is None and not heard
         and sorted(k.split()[0] for k in P.known) == ["1-1", "1-1.1"], str(P.known))

    dev("1-1.3", "0781", "5567", "Cruzer Blade", "SanDisk", ifaces=[("08", "06", "50")], serial="4C53")
    a = P.look(now=10)
    b = P.look(now=11)
    got = P.look(now=12.5)
    note("a USB drive into port 3 of the hub: a moment for the rest, then a line, starry-eyed",
         a is None and b is None and got and got["text"] == "Ooh — a USB drive (Cruzer Blade)! Let me look inside."
         and got["kind"] == "usb-drive" and heard[-1] == (got["text"], "usb-drive")
         and (P.showing(now=13) or {}).get("mood") == "excited" and P.showing(now=13)["face"] in _BM["excited"]
         and P.showing(now=13)["note"] == "A USB drive!" and P.showing(now=12.5 + _pl.FRESH + 1) is None, str(got))

    dev("1-1.4", "05e3", "0610", "USB2.0 Hub", dclass="09", ports=4, ifaces=[("09", "00", "00")])
    dev("1-1.4.1", "046d", "0825", "Webcam C270", ifaces=[("0e", "01", "00"), ("0e", "02", "00"), ("01", "01", "00"), ("01", "02", "00")])
    P.look(now=20)
    dev("1-1.4.2", "093a", "2510", "USB Optical Mouse", ifaces=[("03", "01", "02")])
    P.look(now=20.8)
    hub = P.look(now=23)
    note("a hub with a camera and a mouse on it (turning up a moment apart): one line for the lot, wide-eyed",
         hub and hub["text"] == "Ooh — a hub, with a camera and a mouse on it!" and hub["kind"] == "usb"
         and P.showing(now=23)["mood"] == "surprised" and len(heard) == 2, str(hub))

    pull("1-1.4.1", "1-1.4.2", "1-1.4")
    P.look(now=30)
    gone = P.look(now=33)
    note("…pulled out again: sad, the hub and what was on it in one breath",
         gone and gone["text"] == "Aw — the hub's gone, and the camera and mouse with it." and gone["kind"] == "usb-gone"
         and P.showing(now=33)["mood"] == "sorry" and P.showing(now=33)["note"].startswith("Bye, hub"), str(gone))

    pull("1-1.1")
    P.look(now=40)
    dev("1-1.1", "046d", "c31c", "USB Keyboard", "Logitech", ifaces=[("03", "01", "01")])
    P.look(now=40.6)
    note("the keyboard dropping off and straight back (a reset): let be", P.look(now=44) is None and len(heard) == 3, str(heard[3:]))

    dev("1-1.5", "090c", "1000", "USB DISK", ifaces=[("08", "06", "50")])
    dev("1-1.6", "13fe", "4200", "", ifaces=[("08", "06", "50")])
    P.look(now=50)
    two = P.look(now=53)
    pull("1-1.1")
    P.look(now=60)
    kbd = P.look(now=63)
    pull("1-1.5", "1-1.6")
    P.look(now=70)
    both = P.look(now=73)
    note("two drives at once; the keyboard pulled out; the two drives pulled out",
         two and two["text"] == "Ooh — two USB drives! Let me look inside." and kbd and kbd["text"] == "Aw — the keyboard's gone."
         and both and both["text"] == "Aw — the two USB drives are gone.", f"{two} {kbd} {both}")

    kinds = {name: _pl.kind_of({"product": prod, "maker": "", "class": cls, "ifaces": ifs}) for name, prod, cls, ifs in (
        ("speaker", "UACDemoV1.0", "00", [("01", "01", "00"), ("01", "02", "00"), ("03", "00", "00")]),
        ("xbox", "Xbox 360 Controller", "ff", [("ff", "5d", "01")]),
        ("8bitdo", "8BitDo SN30 Pro", "00", [("03", "00", "00")]),
        ("bt", "", "e0", [("e0", "01", "01")]),
        ("midi", "MPK Mini", "00", [("01", "01", "00"), ("01", "03", "00")]),
        ("wifi", "802.11n WLAN Adapter", "00", [("ff", "ff", "ff")]),
        ("reader", "Card Reader", "00", [("08", "06", "50")]),
        ("pico", "Pico", "ef", [("02", "02", "00"), ("0a", "00", "00")]))}
    note("what a thing is, from what it says it has: a speaker, a game pad, a Bluetooth dongle, a MIDI keyboard, Wi-Fi, a card reader, a Pico",
         kinds == {"speaker": "sound", "xbox": "pad", "8bitdo": "pad", "bt": "bluetooth", "midi": "midi", "wifi": "wifi",
                   "reader": "reader", "pico": "serial"}, str(kinds))
    moods = {k: _bf("Ooh — a keyboard!", k)[1] for k in ("usb", "usb-drive", "usb-gone", "usb-look")}
    note("the bot screen's face for each: wide-eyed, starry, sad, pleased",
         moods == {"usb": "surprised", "usb-drive": "excited", "usb-gone": "sorry", "usb-look": "happy"}, str(moods))

    # the Me screen's face and the Finance screen's, while it's news
    svc = pieapp.service
    was = (_pl.PLUGS.mood, _pl.PLUGS.note, _pl.PLUGS.until, _pl.PLUGS.last)
    try:
        _pl.PLUGS.mood, _pl.PLUGS.note, _pl.PLUGS.until = "excited", "A USB drive!", _t.time() + 30
        me = _MODES["me"](svc.config, svc.panel.info())
        face, notes = me._mood(None, None, None)
        fin = _MODES["crypto"](svc.config, svc.panel.info())
        bad = []
        for name in ("me", "crypto", "message"):
            try:
                _I.open(_io.BytesIO(svc.render_preview(name)))
            except Exception as e:
                bad.append(f"{name}: {e}")
        note("…and on the Me and Finance screens: the face changes, the Me screen says what came",
             face in _BM["excited"] and notes == ["A USB drive!"] and fin._pick_face(None, False) in _BM["excited"] and not bad,
             f"{face} {notes} {bad}")
    finally:
        _pl.PLUGS.mood, _pl.PLUGS.note, _pl.PLUGS.until, _pl.PLUGS.last = was

    # the drives: which are USB, mounted, and what's on them
    sysd = os.path.join(tmp, "sys", "devices", "platform", "soc", "3f980000.usb", "usb1", "1-1")
    blk = os.path.join(tmp, "class", "block")
    os.makedirs(blk)
    places = {"sda": "1-1.3", "sda1": "1-1.3/sda", "sdb": "1-1.4", "sdb1": "1-1.4/sdb", "sdc": "1-1.5", "sdc1": "1-1.5/sdc",
              "sdd": "1-1.2", "sdd2": "1-1.2/sdd", "sdd3": "1-1.2/sdd"}
    for name, at in places.items():
        port, _, parent = at.partition("/")
        full = os.path.join(sysd, port, port + ":1.0", "host0", "target0:0:0", "0:0:0:0", "block", *( [parent] if parent else []), name)
        os.makedirs(full, exist_ok=True)
        os.symlink(full, os.path.join(blk, name))
    for name, parent in (("mmcblk0", ""), ("mmcblk0p1", "mmcblk0"), ("mmcblk0p2", "mmcblk0")):
        full = os.path.join(tmp, "sys", "devices", "platform", "emmc2bus", "mmc0", "mmc0:0001", "block", *([parent] if parent else []), name)
        os.makedirs(full, exist_ok=True)
        os.symlink(full, os.path.join(blk, name))
    labels = os.path.join(tmp, "by-label")
    os.makedirs(labels)
    os.symlink("../../sda1", os.path.join(labels, "SANDISK\\x2032"))
    os.symlink("../../mmcblk0p1", os.path.join(labels, "bootfs"))
    one, two_ = os.path.join(tmp, "media", "sda1"), os.path.join(tmp, "media", "pi", "MY STUFF")
    buf = _io.BytesIO()
    _I.new("RGB", (64, 48), (30, 120, 200)).save(buf, "JPEG")
    for path, data in ((os.path.join(one, "DCIM", "100CANON", "beach day.jpg"), buf.getvalue()),
                       (os.path.join(one, "Music", "01 - Lighthouse Waltz.mp3"), b"ID3" + b"\0" * 64),
                       (os.path.join(one, "Docs", "packing list.txt"), b"socks, a hat, the charger\n"),
                       (os.path.join(one, "Docs", "taxes 2025.pdf"), b"%PDF-1.4\n"),
                       (os.path.join(one, ".Trashes", "old.jpg"), buf.getvalue()),
                       (os.path.join(one, "System Volume Information", "x.jpg"), buf.getvalue()),
                       (os.path.join(one, "notes.xyz"), b"?"),
                       (os.path.join(two_, "holiday", "sunset.png"), b"\x89PNG")):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    mounts = os.path.join(tmp, "mounts")
    put(mounts, "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n/dev/mmcblk0p1 /boot/firmware vfat rw 0 0\nproc /proc proc rw 0 0\n"
                f"/dev/sda1 {one} vfat ro,uid=1000,gid=1000 0 0\n/dev/sdb1 {two_.replace(' ', chr(92) + '040')} exfat rw 0 0\n")
    keep = (_dr.MOUNTS, _dr.SYS_BLOCK, _dr.BY_LABEL, _dr.SYS_USB, _pl.SYS_USB)
    _dr._cache.clear()
    _dr.MOUNTS, _dr.SYS_BLOCK, _dr.BY_LABEL, _dr.SYS_USB, _pl.SYS_USB = mounts, blk, labels, bus_root, bus_root
    real_play, real_load = _mu.PLAYER.play, _mu.PLAYER.load
    mode_was = svc.mode_name
    try:
        m = _dr.mounted()
        note("the USB drives mounted now: the SanDisk by its label, the other by its folder (a space in it) — not the SD card",
             [(d["label"], d["bus"], d["ro"]) for d in m] == [("SANDISK 32", "1-1.3", True), ("MY STUFF", "1-1.4", False)]
             and m[1]["path"] == two_, str(m))
        put(os.path.join(tmp, "mounts2"), f"/dev/sdd2 / ext4 rw 0 0\n/dev/sdd3 {os.path.join(tmp, 'media', 'data')} ext4 rw 0 0\n"
                                          f"/dev/sda1 {one} vfat ro 0 0\n")
        _dr.MOUNTS = os.path.join(tmp, "mounts2")
        booted = [d["name"] for d in _dr.mounted()]
        _dr.MOUNTS = mounts
        note("…and a Pi that boots off a USB disk: that disk isn't a drive to look through",
             booted == ["sda1"] and _dr.disks_on("1-1.5") == ["sdc", "sdc1"] and _dr.disks_on("1-1") == sorted(
                 n for n in places if n.startswith("sd")), f"{booted} {_dr.disks_on('1-1.5')}")
        items = _sf.on_drives()
        names = sorted((i["drive"], i["kind"], i["name"]) for i in items)
        note("what's on them: pictures, songs, documents (the trash and the system folders left out)",
             names == [("MY STUFF", "photo", "sunset.png"), ("SANDISK 32", "document", "packing list.txt"),
                       ("SANDISK 32", "document", "taxes 2025.pdf"), ("SANDISK 32", "music", "01 - Lighthouse Waltz.mp3"),
                       ("SANDISK 32", "photo", "beach day.jpg")], str(names))
        hit = _sf.find("the beach photo on the usb drive")
        plain = _sf.find("beach photo")
        note("found by a few words, \"on the usb drive\" or not; said where it is",
             hit and hit[0]["name"] == "beach day.jpg" and plain and plain[0]["name"] == "beach day.jpg"
             and _sf.describe(hit[0]).startswith("beach day — a photo on the USB drive SANDISK 32 (DCIM/100CANON)")
             and not _sf.find("the cat photo on the usb drive"), str([_sf.describe(i) for i in hit[:2]]))
        asks = {t: _sf.parse(t) for t in ("what's on the usb drive?", "what photos are on my thumb drive",
                                         "what songs are there on the usb stick", "what is on the drive")}
        note("\"what's on the usb drive\", \"what photos are on my thumb drive\": asked of the drives",
             all(a and a["kind"] == "list" and a.get("drive") for a in asks.values()), str(asks))
        said = pieapp._shelf_command("what's on the usb drive", "page")
        photos_said = pieapp._shelf_command("what photos are on the usb drive", "page")
        note("…answered with what's on each",
             said == "On the USB drive SANDISK 32: 1 photo, 1 song and 2 documents; on the USB drive MY STUFF: 1 photo — ask me for any of it."
             and photos_said.startswith("On the USB drive SANDISK 32: 1 photo; on the USB drive MY STUFF: 1 photo") and "Newest:" in photos_said,
             f"{said} | {photos_said}")
        shown = pieapp._shelf_command("show me the beach photo on the usb drive", "page")
        deadline = _t.time() + 5
        while svc.mode_name != "postcard" and _t.time() < deadline:
            _t.sleep(0.05)
        note("a photo on it, on the panel", "is on the panel" in shown and svc.mode_name == "postcard", f"{shown} {svc.mode_name}")
        played = []
        _mu.PLAYER.play = lambda index=None, conf=None, start=0.0: played.append(index)
        _mu.PLAYER.load = lambda tracks, conf: played.append([t["path"] for t in tracks])
        song = pieapp._shelf_command("play lighthouse waltz", "page")
        note("a song on it, played from the drive (its songs the playlist)",
             song == "Playing Lighthouse Waltz (from the USB drive SANDISK 32)." and played[0] == [os.path.join(os.path.realpath(one), "Music", "01 - Lighthouse Waltz.mp3")]
             and played[1] == 0, f"{song} {played}")
        _mu.PLAYER.play, _mu.PLAYER.load = real_play, real_load
        before = {b["id"] for b in _rd.library()}
        was_reading = pieapp.reading.book
        opened = pieapp._shelf_command("open the document called packing list", "page")
        new = [b for b in _rd.library() if b["id"] not in before]
        note("a text file on it, open in Read (copied over, so it stays when the drive goes)",
             opened == "packing list is open in Read (copied over from the USB drive)." and len(new) == 1, f"{opened} {new}")
        if mode_was:
            svc.set_mode(mode_was)
            deadline = _t.time() + 5
            while svc.mode_name != mode_was and _t.time() < deadline:
                _t.sleep(0.05)
        for b in new:
            _rd.remove(b["id"])
        with pieapp.reading.lock:
            pieapp.reading.book = was_reading                     # (as it was: the copy's gone again)
        try:
            _sf.fetch({**hit[0], "path": "../../../../etc/hostname"})
            outside = True
        except FileNotFoundError:
            outside = False
        note("never a file outside the drive", not outside and _sf.fetch(hit[0]) == buf.getvalue())
        words = _sf.words(svc.config)
        note("the bot knows what's on them (with the rest of the shelves)",
             "on the USB drive SANDISK 32 plugged in here 1 photo, 1 song and 2 documents" in words, words[:300])
        # a drive goes in: once it's mounted, what's on it is the bot's next line
        inside = pieapp._usb_look_inside([{"bus": "1-1.3"}], wait=0)
        latest = _llm.latest()
        note("a drive that went in: once mounted, what's on it, pleased",
             inside == "On the USB drive SANDISK 32: 1 photo, 1 song and 2 documents — ask me for any of it."
             and latest["kind"] == "usb-look" and latest["text"] == inside and _pl.PLUGS.showing()["mood"] == "happy", f"{inside} {latest}")
        dev("1-1.5", "090c", "1000", "USB DISK", ifaces=[("08", "06", "50")])
        unmounted = pieapp._usb_look_inside([{"bus": "1-1.5"}], wait=0)
        empty = pieapp._usb_look_inside([{"bus": "1-1.7"}], wait=0)
        _dr.MOUNTS = os.path.join(tmp, "no-mounts")
        none_in = _sf.drive_summary()
        pull("1-1.5", "1-1.3")
        nothing = _sf.drive_summary()
        _dr.MOUNTS = mounts
        note("…one nothing mounted: says so (and how); a card reader with no card: nothing to say",
             unmounted and "isn't mounted" in unmounted and "setup.sh" in unmounted and _llm.latest()["kind"] == "usb-gone"
             and empty is None and "isn't mounted" in none_in and nothing == "There's no USB drive plugged in.",
             f"{unmounted} | {empty} | {none_in} | {nothing}")
        # the app's side of a plug: the line, the face, the events list (with the hub's port)
        kb = {"bus": "1-1.1", "vid": "046d", "pid": "c31c", "product": "USB Keyboard", "maker": "Logitech", "class": "00",
              "ifaces": [("03", "01", "01")], "ports": 0}
        real_ask = pieapp.mascot.ask
        pieapp.mascot.ask = lambda *a, **k: False                 # (no model asked, here)
        try:
            pieapp._usb_changed([kb], [], "Ooh — a keyboard!", "usb")
        finally:
            pieapp.mascot.ask = real_ask
        ev = _si._load()[-1]
        note("the app: the line on the bot's screen, its face, the events list with the port",
             _llm.latest()["text"] == "Ooh — a keyboard!" and _llm.latest()["kind"] == "usb"
             and ev["text"] == "Ooh — a keyboard! (port 1 of the hub)" and ev["kind"] == "usb", f"{_llm.latest()} {ev}")
        _mu.PLAYER.tracks = [{"path": os.path.join(tmp, "gone", "song.mp3"), "title": "song", "folder": ""}]
        pieapp._usb_changed([], [{"bus": "1-1.3", "vid": "0781", "pid": "5567", "product": "Cruzer Blade", "class": "00",
                                  "ifaces": [("08", "06", "50")], "ports": 0}], "Aw — the USB drive's gone.", "usb-gone")
        note("…a drive pulled out while its songs played: back to the music folder's",
             not any(os.path.isabs(t["path"]) for t in _mu.PLAYER.tracks), str(_mu.PLAYER.tracks[:2]))
    finally:
        _mu.PLAYER.play, _mu.PLAYER.load = real_play, real_load
        _dr.MOUNTS, _dr.SYS_BLOCK, _dr.BY_LABEL, _dr.SYS_USB, _pl.SYS_USB = keep
        _dr._cache.clear()
        _pl.PLUGS.until = 0
        if mode_was and svc.mode_name != mode_was:
            svc.set_mode(mode_was)
        _sh.rmtree(tmp, ignore_errors=True)

    # setup.sh: drives mounted (read-only) as they go in, unless a desktop does it
    here = os.path.dirname(os.path.abspath(__file__))
    sh = open(os.path.join(here, "setup.sh")).read()
    script = os.path.join(here, "pie_ink", "usb-mount.sh")
    body = open(script).read()
    syntax = _sp.run(["sh", "-n", script], capture_output=True, text=True)
    note("setup.sh has USB drives mounted as they go in (read-only, by systemd, the Pi's own disk left alone; a desktop does its own)",
         'RUN+="/usr/local/sbin/pie-ink-usb-mount $devnode %%k $env{ID_FS_TYPE} %s %s"' in sh
         and 'ENV{ID_FS_USAGE}=="filesystem"' in sh and 'SUBSYSTEMS=="usb", SUBSYSTEM=="block"' in sh
         and '"$(systemctl get-default 2>/dev/null)" == "graphical.target"' in sh
         and 'sudo install -m 755 "$DIR/pie_ink/usb-mount.sh" /usr/local/sbin/pie-ink-usb-mount' in sh
         and syntax.returncode == 0 and 'opts="ro,uid=$uid,gid=$gid' in body and 'opts="ro"' in body
         and "systemd-mount --no-block --collect" in body and 'grep -q "^$dev " /proc/mounts && exit 0' in body
         and "findmnt -nvo SOURCE" in body, syntax.stderr)


def main():
    if True:
        import app as pieapp
        c = pieapp.app.test_client()

        print("pages")
        check("GET /", c.get("/"))
        check("GET /api/status", c.get("/api/status"), body_has=["settings", "service"])
        check("GET /api/preview.png", c.get("/api/preview.png"))
        check("GET /api/now", c.get("/api/now"), body_has=["mode", "speech"])
        check("GET /api/camera/probe", c.get("/api/camera/probe"), body_has=["report", "text"])
        # the weather spot is where we are, and the beacon tells the other Pis
        c.post("/api/weather/place", json={"lat": 32.7157, "lon": -117.1611, "name": "San Diego", "admin": "California", "country_code": "US"})
        import json as _json
        from pie_ink import friends as _friends
        beacon = _json.loads(_friends._beacon())
        wh = beacon.get("where") or {}
        if wh.get("lat") is None or "San Diego" not in (wh.get("place") or ""):
            print(f"  FAIL the beacon does not say where we are (the weather spot): {beacon}")
            FAILED.append("beacon where")
        else:
            print(f"  ok   the beacon says where we are: {wh.get('place')}")
        check("GET /api/map (gone)", c.get("/api/map"), want=(404,))
        shared_check(pieapp, c)
        full_share_check(pieapp, c)
        c.post("/api/weather/place", json={})

        print("screens")
        from pie_ink.modes import MODES
        for mode in MODES:
            r = c.post("/api/mode", json={"mode": mode})
            check(f"POST /api/mode {mode}", r)

        print("settings")
        for section, patch in (
            ("me", {"me": {"name": "Test"}}),
            ("earnings", {"earnings": {"salary": 1000}}),
            ("display", {"display": {"full_refresh_every": 40}}),
            ("audio", {"audio": {"speak_bot": True}}),
            ("listen", {"listen": {"wake": "hey there"}}),
            ("routines", {"routines": {"enabled": False}}),
            ("message", {"message": {"text": "hello"}}),
            ("schedule", {"schedule": {"workdays": ["mon", "tue"]}}),
            ("music", {"music": {"repeat": True}}),
            ("llm", {"llm": {"chatter_minutes": 0}}),
            ("lcd", {"lcd": {"enabled": True, "screen": "clock"}}),
        ):
            r = c.put("/api/settings", json=patch)
            got = (r.get_json() or {}).get("settings", {})
            key, value = next(iter(patch[section].items()))
            saved = got.get(section, {}).get(key)
            check(f"PUT /api/settings {section}", r, body_has=["settings"])
            if saved != value:
                print(f"  FAIL {section}.{key} came back as {saved!r}, not {value!r}")
                FAILED.append(f"{section}.{key}")

        print("the bot, sound and listening")
        check("GET /api/chat", c.get("/api/chat"), body_has=["history"])
        check("POST /api/llm/chat (no model)", c.post("/api/llm/chat", json={"message": "hi"}), want=(400,))
        r = c.post("/api/llm/chat", json={"message": "what are you up to?"})
        check("POST /api/llm/chat 'what are you up to?' (no model needed)", r, body_has=["reply"])
        if "screen" not in ((r.get_json() or {}).get("reply") or ""):
            print(f"  FAIL it should say what the screens show: {(r.get_json() or {}).get('reply')!r}")
            FAILED.append("doing without a model")
        check("GET /api/llm", c.get("/api/llm"))
        check("GET /api/audio", c.get("/api/audio"), body_has=["devices", "engine"])
        check("POST /api/audio/say", c.post("/api/audio/say", json={"text": "test"}), want=(200, 400))
        check("POST /api/audio/say (wait)", c.post("/api/audio/say", json={"wait": True}), want=(200, 400))
        r = c.post("/api/audio/volume", json={"volume": 40})
        check("POST /api/audio/volume", r, body_has=["applied", "how"])
        if (r.get_json() or {}).get("how") not in ("mixer", "software"):
            print(f"  FAIL the volume went nowhere: {r.get_json()}")
            FAILED.append("volume")
        vol = (c.get("/api/status").get_json() or {}).get("settings", {}).get("audio", {}).get("volume")
        if vol != 40:
            print(f"  FAIL the volume was not remembered: {vol}")
            FAILED.append("volume saved")
        check("GET /api/camera/list", c.get("/api/camera/list?refresh=1"), body_has=["cameras"])
        check("POST /api/message/say", c.post("/api/message/say", json={"text": "test"}), want=(200, 400))
        check("GET /api/listen", c.get("/api/listen"), body_has=["devices", "status"])
        check("GET /api/audio/probe", c.get("/api/audio/probe"), body_has=["report", "text"])
        sound_probe_check(pieapp)
        # watching the USB bus: the kernel's lines since the last look, in plain words
        from pie_ink import usbwatch as _uw
        raw_was, cards_was, devs_was = _uw._kernel_raw, _uw.audio.sound_cards, _uw.camera.usb_devices
        log = ["[  100.000000] usb 1-1.3: new full-speed USB device number 4 using dwc2", "[  100.200000] usb 1-1.3: Product: u-blox 7"]
        _uw._kernel_raw = lambda: list(log)
        _uw.camera.usb_devices = lambda: [{"bus": "1-1", "vid": "1a40", "pid": "0101", "name": "USB 2.0 Hub", "classes": ["hub"]},
                                          {"bus": "1-1.3", "vid": "1546", "pid": "01a7", "name": "u-blox 7", "classes": ["comms"]}]
        _uw.audio.sound_cards = lambda: [{"index": 0, "id": "vc4hdmi", "driver": "vc4-hdmi", "name": "vc4-hdmi", "usb": None}]
        try:
            first = c.get("/api/usb/watch?since=-1").get_json() or {}
            log += ["[  130.000000] usb 1-1.4: new full-speed USB device number 5 using dwc2",
                    "[  130.500000] usb 1-1.4: device descriptor read/64, error -32", "[  131.000000] usb 1-1-port4: attempt power cycle",
                    "[  133.000000] usb 1-1-port4: unable to enumerate USB device", "[  140.000000] usb 1-1.1: new full-speed USB device number 6 using dwc2",
                    "[  140.300000] usb 1-1.1: Product: UACDemoV1.0", "[  140.400000] random: crng init done"]
            _uw.audio.sound_cards = lambda: [{"index": 0, "id": "vc4hdmi", "driver": "vc4-hdmi", "name": "vc4-hdmi", "usb": None},
                                             {"index": 1, "id": "UACDemoV10", "driver": "USB-Audio", "name": "UACDemoV1.0", "usb": "1b3f:2008"}]
            then = c.get(f"/api/usb/watch?since={first.get('now')}").get_json() or {}
            said = [f"{e['where']}: {e['text']}" for e in then.get("events", [])]
            okay = (first.get("now") == 100.2 and not first.get("events") and then.get("now") == 140.4 and len(said) == 6
                    and said[0] == "port 4 of the hub: something plugged in (full speed)"
                    and said[1] == "port 4 of the hub: it wouldn't answer (error -32: no proper answer from it)"
                    and said[3] == "port 4 of the hub: the Pi gave up on it" and said[5] == "port 1 of the hub: it's UACDemoV1.0"
                    and [x["name"] for x in then.get("cards", [])] == ["UACDemoV1.0"]
                    and [u["where"] for u in first.get("devices", [])] == ["the Pi's port", "port 3 of the hub"])
            print(f"  {'ok  ' if okay else 'FAIL'} watching the USB bus: only what happens from now on, each line in plain words, "
                  f"and the sound card once it's there")
            if not okay:
                print(f"       {first} {then}")
                FAILED.append("usb watch")
        finally:
            _uw._kernel_raw, _uw.audio.sound_cards, _uw.camera.usb_devices = raw_was, cards_was, devs_was
        check("POST /api/draw/describe (no bot)", c.post("/api/draw/describe", json={"image": ""}), want=(400,))

        print("the rest")
        check("GET /api/music", c.get("/api/music"), body_has=["tracks", "now"])
        check("POST /api/music/toggle", c.post("/api/music/toggle", json={}))

        print("Play on this device (a page playing the music)")
        import tempfile as _tf
        import shutil as _sh
        import time as _t
        from pie_ink import here as _here
        mdir = _tf.mkdtemp(prefix="pie-ink-music-")
        for name in ("01 - One.mp3", "02 - Two.mp3"):
            with open(os.path.join(mdir, name), "wb") as fh:
                fh.write(b"ID3" + bytes(4000))
        folder_was = pieapp.service.config.get("music", {}).get("folder", "")
        try:
            c.put("/api/settings", json={"music": {"folder": mdir}})
            c.post("/api/music/stop", json={})
            d = c.get("/api/audio/here?id=smoke&after=-1&mv=-1").get_json() or {}
            okay = d.get("ok") and (d.get("music") or {}).get("state") == "stopped" and _here.HERE.listening()
            now = (c.post("/api/music/play", json={"index": 0}).get_json() or {}).get("now") or {}
            d2 = c.get(f"/api/audio/here?id=smoke&after={d.get('seq', 0)}&mv={d.get('mv', 0)}").get_json() or {}
            m = d2.get("music") or {}
            okay = okay and now.get("on_page") and m.get("state") == "playing" and m.get("path") == "01 - One.mp3"
            print(f"  {'ok  ' if okay else 'FAIL'} a page listening: the track plays in it (the Pi just keeps track), "
                  f"and the page hears at once")
            if not okay:
                print(f"       {d} {now} {d2}")
                FAILED.append("here music")
            f = c.get("/api/music/file?p=01%20-%20One.mp3", headers={"Range": "bytes=0-99"})
            bad = [c.get("/api/music/file?p=" + q).status_code for q in ("..%2Fconfig.yaml", "nothing.mp3", "")]
            okay = f.status_code == 206 and len(f.data) == 100 and bad == [404, 404, 404] \
                and c.get("/api/audio/here/clip/999999").status_code == 404
            print(f"  {'ok  ' if okay else 'FAIL'} the page gets the file (with ranges, to seek), and only files in the list")
            if not okay:
                FAILED.append("here music file")
            c.post("/api/music/here", json={"v": m.get("v"), "position": 3.0, "duration": 180.0})
            st = (c.get("/api/music").get_json() or {}).get("now") or {}
            stale = (c.post("/api/music/here", json={"v": (m.get("v") or 0) - 1, "ended": True}).get_json() or {}).get("took")
            c.post("/api/music/here", json={"v": m.get("v"), "ended": True})
            st2 = (c.get("/api/music").get_json() or {}).get("now") or {}
            okay = 3.0 <= st.get("position", 0) < 4.5 and st.get("duration") == 180.0 and stale is False \
                and st2.get("index") == 1 and st2.get("on_page") and st2.get("state") == "playing"
            print(f"  {'ok  ' if okay else 'FAIL'} the page says how far it got, and when the track ends the next one "
                  f"starts; a report about an older track is ignored")
            if not okay:
                print(f"       {st} {stale} {st2}")
                FAILED.append("here music reports")
            c.post("/api/audio/here/off", json={"id": "smoke"})
            for _ in range(30):
                if (c.get("/api/music").get_json() or {}).get("now", {}).get("state") == "paused":
                    break
                _t.sleep(0.1)
            st3 = (c.get("/api/music").get_json() or {}).get("now") or {}
            okay = not _here.HERE.listening() and st3.get("state") == "paused" and st3.get("index") == 1
            print(f"  {'ok  ' if okay else 'FAIL'} the page switches it off: the music pauses (not out of the Pi's "
                  f"speaker all of a sudden)")
            if not okay:
                FAILED.append("here off pauses")
        finally:
            _here.HERE.leave("smoke")
            c.post("/api/music/stop", json={})
            c.put("/api/settings", json={"music": {"folder": folder_was}})
            _sh.rmtree(mdir, ignore_errors=True)
        check("GET /api/weather", c.get("/api/weather"), want=(200, 400, 502))
        check("GET /api/books", c.get("/api/books"))
        check("GET /api/watchlist", c.get("/api/watchlist"))
        check("GET /api/system", c.get("/api/system"), body_has=["cpu"])
        check("GET /api/friends", c.get("/api/friends"))
        check("GET /api/social", c.get("/api/social"), body_has=["history", "talking"])
        check("POST /api/social/show", c.post("/api/social/show"))

        print("the second screen")
        import time as _t
        import threading as _th
        _t.sleep(1.0)                                  # the LCD thread draws its first frame
        r = c.get("/api/lcd")
        check("GET /api/lcd", r, body_has=["status", "screens"])
        st = (r.get_json() or {}).get("status", {})
        if not st.get("running") or st.get("frames", 0) < 1 or st.get("error"):
            print(f"  FAIL the LCD loop is not drawing: {st}")
            FAILED.append("lcd loop")
        check("GET /api/lcd/preview.png", c.get("/api/lcd/preview.png"))
        for screen in ("system", "bot", "chat", "crypto", "weather", "camera", "paint", "drawing", "mirror", "clock", "testcard"):
            r = c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": screen, "light_mode": screen == "bot"}}})
            check(f"POST /api/lcd/preview.png {screen}", r)
        # painting on it: a picture goes up, the LCD switches to its Paint screen, the picture comes back
        import base64 as _b64
        import io as _io
        from PIL import Image as _Image
        buf = _io.BytesIO()
        _Image.new("RGB", (240, 280), (255, 200, 0)).save(buf, "PNG")
        png = "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        r = c.post("/api/paint", json={"image": png})
        check("POST /api/paint", r, body_has=["version", "shown"])
        if not (r.get_json() or {}).get("shown"):
            print("  FAIL painting did not switch the LCD to its Paint screen")
            FAILED.append("paint shown")
        r = c.get("/api/paint.png")
        check("GET /api/paint.png", r)
        if r.status_code == 200:
            back = _Image.open(_io.BytesIO(r.data))
            if back.size != (240, 280) or back.convert("RGB").getpixel((5, 5)) != (255, 200, 0):
                print(f"  FAIL the painting came back different: {back.size} {back.convert('RGB').getpixel((5, 5))}")
                FAILED.append("paint round trip")
        check("POST /api/paint (junk)", c.post("/api/paint", json={"image": "data:image/png;base64,AAAA"}), want=(400,))
        # the other panel: the loop restarts on it and every screen fits its shape
        check("PUT /api/settings lcd model 1.9", c.put("/api/settings", json={"lcd": {"model": "1.9", "screen": "system"}}))
        _t.sleep(1.0)
        r = c.get("/api/lcd")
        st = (r.get_json() or {}).get("status", {})
        if st.get("size") != [170, 320] or st.get("error"):
            print(f"  FAIL the 1.9\" panel did not come up: {st}")
            FAILED.append("lcd 1.9")
        for rot in (0, 90):
            for screen in ("system", "clock", "weather", "crypto", "bot", "chat", "paint", "mirror", "testcard", "me", "message", "reader"):
                r = c.post("/api/lcd/preview.png", json={"settings": {"lcd": {"screen": screen, "rotation": rot}}})
                if r.status_code == 200:
                    got = _Image.open(_io.BytesIO(r.data)).size
                    want = (170, 320) if rot == 0 else (320, 170)
                    if got != want:
                        print(f"  FAIL {screen} at {rot}° came out {got}, not {want}")
                        FAILED.append(f"lcd 1.9 {screen}")
                else:
                    check(f"POST /api/lcd/preview.png 1.9 {screen} {rot}", r)
        print("  ok   every screen fits the 1.9\" panel both ways round")
        check("PUT /api/settings lcd model 1.69", c.put("/api/settings", json={"lcd": {"model": "1.69"}}))
        check("PUT /api/settings lcd off", c.put("/api/settings", json={"lcd": {"enabled": False}}))

        print("the Whisplay HAT as the screen")
        r = c.put("/api/settings", json={"display": {"driver": "mock_whisplay", "rotation": 90}})
        check("PUT /api/settings display whisplay", r, body_has=["settings"])
        _t.sleep(1.0)
        panel = (c.get("/api/status").get_json() or {}).get("service", {}).get("panel", {})
        if not panel.get("colour") or panel.get("glass") != [280, 240] or (panel.get("width"), panel.get("height")) != (260, 220):
            print(f"  FAIL the panel did not become the sideways Whisplay (glass 280x240, drawing area 260x220): {panel}")
            FAILED.append("whisplay panel")
        for mode in ("clock", "system", "weather", "crypto", "message", "image", "me"):
            r = c.post("/api/render.png", json={"mode": mode})
            check(f"POST /api/render.png {mode} (whisplay)", r)
            if r.status_code == 200:
                im = _Image.open(_io.BytesIO(r.data))
                if im.size != (280 * 3, 240 * 3):
                    print(f"  FAIL {mode} preview is {im.size}")
                    FAILED.append(f"whisplay {mode}")
                if mode == "system" and im.mode != "RGB":
                    print(f"  FAIL the system screen should be in colour here, got {im.mode}")
                    FAILED.append("whisplay colour")
        buf = _io.BytesIO()
        _Image.new("RGB", (260, 220), (30, 130, 230)).save(buf, "PNG")
        r = c.post("/api/paint", json={"image": "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()})
        check("POST /api/paint (whisplay)", r, body_has=["shown"])
        _t.sleep(1.5)
        mode_now = (c.get("/api/status").get_json() or {}).get("service", {}).get("mode")
        if mode_now != "image":
            print(f"  FAIL painting should have put the drawing on screen, mode is {mode_now}")
            FAILED.append("whisplay paint")
        r = c.get("/api/preview.png")
        if r.status_code == 200:
            im = _Image.open(_io.BytesIO(r.data)).convert("RGB")
            if im.getpixel((im.width // 2, im.height // 2)) != (30, 130, 230):
                print(f"  FAIL the painting is not on the screen: {im.getpixel((im.width // 2, im.height // 2))}")
                FAILED.append("whisplay paint shown")
        check("GET /api/whisplay", c.get("/api/whisplay"), body_has=["status", "panel", "sound"])
        check("POST /api/whisplay/sound (no card here)", c.post("/api/whisplay/sound"), want=(200, 400))
        check("GET /api/lcd (no second screen)", c.get("/api/lcd"))
        check("PUT /api/settings display mock again", c.put("/api/settings", json={"display": {"driver": "mock", "rotation": 0}}))
        _t.sleep(0.5)

        print("where the Pi is, without being told")
        check("POST /api/weather/place (cleared)", c.post("/api/weather/place", json={}))
        real_net = pieapp.locate.from_network
        answers = [{"lat": 34.0522, "lon": -118.2437, "place": "Los Angeles, California", "country": "US", "at": _t.time()}]
        pieapp.locate.from_network = lambda cached_only=False: answers[-1]
        try:
            got = pieapp._auto_locate()
            w = pieapp.service.config.get("weather", {})
            okay = got and w.get("location") == "Los Angeles, California" and w.get("source") == "network" and abs(float(w["lat"]) - 34.0522) < 0.001
            print(f"  {'ok  ' if okay else 'FAIL'} nothing set: the network puts it near {w.get('location')!r} and the weather goes there")
            if not okay:
                FAILED.append("locate from the network")
            if pieapp._auto_locate() is not None:
                print("  FAIL the same answer again should change nothing")
                FAILED.append("locate same place")
            answers.append({"lat": 32.7157, "lon": -117.1611, "place": "San Diego, California", "country": "US", "at": _t.time()})
            got = pieapp._auto_locate()
            w = pieapp.service.config.get("weather", {})
            okay = got and w.get("location") == "San Diego, California"
            print(f"  {'ok  ' if okay else 'FAIL'} the Pi moved house (the network says 180 km away): the weather follows — {w.get('location')!r}")
            if not okay:
                FAILED.append("locate moved")
            check("POST /api/weather/place (typed)", c.post("/api/weather/place", json={"name": "Portland", "admin": "Oregon", "country": "United States",
                                                                                       "country_code": "US", "lat": 45.5152, "lon": -122.6784}))
            w = pieapp.service.config.get("weather", {})
            answers.append({"lat": 34.0522, "lon": -118.2437, "place": "Los Angeles, California", "country": "US", "at": _t.time()})
            okay = w.get("source") == "typed" and pieapp._auto_locate() is None and pieapp.service.config["weather"]["location"] == "Portland, Oregon"
            print(f"  {'ok  ' if okay else 'FAIL'} a town you typed stays put whatever the network says ({pieapp.service.config['weather']['location']!r})")
            if not okay:
                FAILED.append("locate typed stays")
            r = c.post("/api/weather/locate", json={})
            check("POST /api/weather/locate (Find me)", r, body_has=["location", "source"])
            d = r.get_json() or {}
            if d.get("source") != "network" or d.get("location") != "Los Angeles, California":
                print(f"  FAIL Find me should take the network's answer: {d}")
                FAILED.append("locate find me")
            else:
                print(f"  ok   Find me: {d['location']} (from the network)")
        finally:
            pieapp.locate.from_network = real_net
        # the real lookup, when the network is reachable (it may not be here): a place and a position, or a quiet None
        try:
            loc = pieapp.locate.from_network()
            print(f"  ok   the network lookup itself: {loc['place']} ({loc['lat']}, {loc['lon']})" if loc else "  ok   the network lookup itself: no answer here (no internet in this sandbox) — fine")
        except Exception as e:
            print(f"  FAIL the lookup should never raise: {e}")
            FAILED.append("locate raises")
        check("POST /api/weather/place (cleared again)", c.post("/api/weather/place", json={}))


        print("what the camera sees, the agent, the mesh")
        ollama = _FakeOllama()
        ollama.start()
        try:
            from PIL import Image as _Image
            from pie_ink import agent as _agent, friends as _friends, sight as _sight
            check("PUT /api/settings llm (the fake model)", c.put("/api/settings", json={
                "llm": {"enabled": True, "host": f"http://127.0.0.1:{ollama.port}", "model": "fake:latest", "see": True},
                "camera_watch": {"events": True, "detector": "model"}, "me": {"name": "Tester", "location": "test bench"}}))
            _sight.clear()
            frame = _Image.new("RGB", (320, 240), (90, 120, 150))
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            texts = [e["text"] for e in entries]
            if obs is None or obs.get("people") != 1 or "Person detected" not in texts or "Cat spotted" not in texts:
                print(f"  FAIL the first look should log a person and a cat: {obs} {texts}")
                FAILED.append("sight first look")
            else:
                print(f"  ok   a look through the fake model: {_sight.describe(obs)} -> {', '.join(texts)}")
            ollama.scene = dict(ollama.scene, people=0, animals=[], packages=1, door="open")
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            texts = [e["text"] for e in entries]
            want = {"Nobody detected", "Cat left", "Package detected", "Door opened"}
            if not want.issubset(set(texts)):
                print(f"  FAIL the second look should log what changed: {texts}")
                FAILED.append("sight changes")
            else:
                print(f"  ok   only the changes become events: {', '.join(texts)}")
            ollama.scene = dict(ollama.scene, people=1)
            obs, entries = _sight.look(frame, pieapp.service.config, "Tester")
            if "Person returned" not in [e["text"] for e in entries]:
                print(f"  FAIL a person after a 'nobody' is a return: {[e['text'] for e in entries]}")
                FAILED.append("sight returned")
            else:
                print("  ok   a person after 'Nobody detected' is 'Person returned'")
            r = c.get("/api/events")
            check("GET /api/events", r, body_has=["events", "status", "detector"])
            if len((r.get_json() or {}).get("events") or []) < 6:
                FAILED.append("events listed")
            check("POST /api/events/look (no real camera here)", c.post("/api/events/look", json={}), want=(400,))
            if not _sight.summary():
                FAILED.append("events summary")
            # the agent: one check against the fake model, with the actions it is allowed
            check("PUT /api/settings agent on", c.put("/api/settings", json={"agent": {"enabled": True, "every_minutes": 30, "look": False,
                                                                                       "can_notify": True, "can_show": True, "can_say": False,
                                                                                       "goals": "Keep an eye on the room.\nTell me when the cat is about."}}))
            _t.sleep(0.3)
            entry = _agent.AGENT.tick("test")
            if not entry or entry.get("error") or entry.get("level") != "note" or "cat" not in entry.get("thought", "").lower():
                print(f"  FAIL the agent's check went wrong: {entry}")
                FAILED.append("agent tick")
            else:
                done = {a["do"]: a["result"] for a in entry["actions"]}
                if done.get("show") != "on the screen" or "Telegram" not in done.get("notify", ""):
                    print(f"  FAIL the agent's actions: {done}")
                    FAILED.append("agent actions")
                else:
                    print(f"  ok   the agent thought '{entry['thought']}' and acted: {done}")
            report = next((p for p in ollama.prompts if "Report at" in (p.get("prompt") or "")), None)
            if not report or "Events" not in report["prompt"] or "Keep an eye on the room" not in (report.get("system") or ""):
                print("  FAIL the agent's report should carry the events and the goals")
                FAILED.append("agent report")
            r = c.get("/api/agent")
            check("GET /api/agent", r, body_has=["status", "journal"])
            if not (r.get_json() or {}).get("journal"):
                FAILED.append("agent journal")
            check("POST /api/agent/run", c.post("/api/agent/run", json={}), body_has=["started"])
            _t.sleep(1.5)
            tg = pieapp._tg_command("/agent")
            if "cat" not in str(tg).lower():
                print(f"  FAIL /agent should tell the last thought: {tg!r}")
                FAILED.append("telegram /agent")
            check("GET /api/now (with the agent and the events)", c.get("/api/now"), body_has=["event", "agent"])
            check("PUT /api/settings agent off", c.put("/api/settings", json={"agent": {"enabled": False}}))
            check("DELETE /api/agent", c.delete("/api/agent"))
            # the mesh: another Pi on the network — this one, through a real socket — and what you can ask of it
            from werkzeug.serving import make_server
            srv = make_server("127.0.0.1", 0, pieapp.app, threaded=True)
            _th.Thread(target=srv.serve_forever, daemon=True).start()
            with _friends._lock:
                _friends._peers["fake-peer"] = {"id": "fake-peer", "host": "pie-up", "ip": "127.0.0.1", "port": srv.server_port, "version": "x",
                                                "panel": '2.13" e-ink', "mode": "clock", "where": None, "seen": _t.time(),
                                                "caps": {"location": "upstairs", "camera": True, "speaker": False, "mic": False,
                                                         "llm": True, "home": False, "agent": False, "lcd": "", "battery": None}}
            try:
                check("GET /api/mesh", c.get("/api/mesh"), body_has=["me", "pis"])
                asks = {"list the pis": "pie-up", "which pi has a camera": "pie-up", "find the pi with a speaker": "No Pi",
                        "where is the upstairs pi": "hasn't said", "show me the camera from upstairs": "on the panel",
                        "say hello on the upstairs pi": "pie-up", "show the clock on the upstairs pi": "Showing clock",
                        "send this drawing to the upstairs pi": "drawing"}
                for ask, want in asks.items():
                    r = c.post("/api/llm/chat", json={"message": ask})
                    reply = (r.get_json() or {}).get("reply") or (r.get_json() or {}).get("error") or ""
                    okay = r.status_code == 200 and want.lower() in reply.lower()
                    print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply[:90]!r}")
                    if not okay:
                        FAILED.append(f"mesh: {ask}")
                    if ask.startswith("show me the camera") and pieapp.service.mode_name != "postcard":
                        print(f"  FAIL the friend's camera should be on the panel (postcard), not {pieapp.service.mode_name}")
                        FAILED.append("mesh camera on panel")
                tg = pieapp._tg_command("show me the camera from upstairs")
                if not (isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff"):
                    print(f"  FAIL from Telegram the camera should come back as a picture: {str(tg)[:60]}")
                    FAILED.append("telegram mesh camera")
                else:
                    print("  ok   from Telegram the friend's camera comes back as a picture")
                if "pie-up" not in pieapp._tg_command("/pis"):
                    FAILED.append("telegram /pis")
                r = c.post("/api/llm/chat", json={"message": "what can you see"})     # not a mesh thing: the bot answers
                if "pie-up" in ((r.get_json() or {}).get("reply") or ""):
                    print(f"  FAIL 'what can you see' is for the bot itself, not the mesh: {r.get_json()}")
                    FAILED.append("mesh not greedy")
                else:
                    print("  ok   'what can you see' goes to the bot, not the mesh")
            finally:
                with _friends._lock:
                    _friends._peers.pop("fake-peer", None)
                srv.shutdown()
            snake_check(pieapp, c, ollama)
            check("PUT /api/settings llm off", c.put("/api/settings", json={"llm": {"enabled": False}}))
        finally:
            ollama.stop()


        print("the miners: duino-coin and verus")
        # the mining "show" switches are plain settings the page reads (both on from the start)
        c.put("/api/settings", json={"duco": {"show": False}, "verus": {"show": True}})
        shown = (c.get("/api/status").get_json() or {}).get("settings", {})
        if shown.get("duco", {}).get("show") is not False or shown.get("verus", {}).get("show") is not True:
            print(f"  FAIL the mining 'show' switches didn't stick: {shown.get('duco', {}).get('show')} / {shown.get('verus', {}).get('show')}")
            FAILED.append("mining show")
        else:
            print("  ok   'I mine Duino-Coin' off and 'I mine Verus' on are kept, and come out of the box on")
        c.put("/api/settings", json={"duco": {"show": True}})
        from pie_ink import mining as _mining
        fake_pool = _FakeDuco()
        fake_pool.start()
        real = (_mining.DUCO_USER_URL, _mining.DUCO_NET_URL, _mining.VERUS_URL, _mining.DATA_DIR)
        _mining.DUCO_USER_URL = f"http://127.0.0.1:{fake_pool.port}/v3/users/{{user}}"
        _mining.DUCO_NET_URL = f"http://127.0.0.1:{fake_pool.port}/api.json"
        _mining.VERUS_URL = f"http://127.0.0.1:{fake_pool.port}/verus/miner/{{wallet}}"
        _mining.DATA_DIR = tempfile.mkdtemp(prefix="pie-ink-mining-")
        try:
            check("POST /api/mining/check (no such user)", c.post("/api/mining/check", json={"pool": "duco", "who": "nobody"}), want=(400,))
            r = c.post("/api/mining/check", json={"pool": "duco", "who": "tester"})
            check("POST /api/mining/check duco", r, body_has=["data", "miners", "words"])
            if "450 H/s" not in ((r.get_json() or {}).get("words") or ""):
                print(f"  FAIL the look-up should add the miners up: {r.get_json()}")
                FAILED.append("duco check")
            r = c.post("/api/mining/check", json={"pool": "verus", "who": "RTESTWALLET"})
            check("POST /api/mining/check verus", r, body_has=["data", "miners", "words"])
            if "34.42 MH/s" not in ((r.get_json() or {}).get("words") or "") or len((r.get_json() or {}).get("miners") or []) != 2:
                print(f"  FAIL luckpool's answer should parse: {r.get_json()}")
                FAILED.append("verus check")
            else:
                print(f"  ok   luckpool parsed: {(r.get_json() or {}).get('words')}")
            check("POST /api/mining/check (unknown wallet)", c.post("/api/mining/check", json={"pool": "verus", "who": "RNOBODY"}), want=(400,))
            check("GET /api/mining (off)", c.get("/api/mining"), body_has=["pools"])
            check("PUT /api/settings both pools on", c.put("/api/settings", json={
                "duco": {"enabled": True, "username": "tester", "min_hashrate": 300},
                "verus": {"enabled": True, "wallet": "RTESTWALLET", "min_hashrate": 20000000}}))
            _t.sleep(2.5)                                     # the threads' first readings
            r = c.get("/api/mining")
            check("GET /api/mining (on)", r, body_has=["pools"])
            pools = (r.get_json() or {}).get("pools") or {}
            dd = (pools.get("duco") or {}).get("data") or {}
            vv = (pools.get("verus") or {}).get("data") or {}
            if dd.get("hashrate") != 450.5 or dd.get("workers") != 2 or dd.get("price") != 0.00041 or dd.get("stake_amount") is not None and dd.get("stake_amount") <= 0:
                print(f"  FAIL the duino reading is wrong: {dd}")
                FAILED.append("duco reading")
            else:
                print(f"  ok   duino: {dd['who']} {dd['balance']} DUCO, {dd['hashrate_text']} from {dd['workers']} workers")
            if abs(vv.get("hashrate", 0) - 34419715.2) > 1 or vv.get("workers") != 2 or vv.get("paid") != 12.3456 or vv.get("luck") != "87.5%" \
                    or [w["identifier"] for w in vv.get("miners", [])] != ["ryzen", "pi5"]:
                print(f"  FAIL the verus reading is wrong: {vv}")
                FAILED.append("verus reading")
            else:
                print(f"  ok   verus: {vv['who']} {vv['balance']} VRSC unpaid, {vv['hashrate_text']} from {vv['workers']} workers, luck {vv['luck']}")
            check("POST /api/render.png crypto (the mining pages are in the rotation)", c.post("/api/render.png", json={"mode": "crypto"}))
            from pie_ink.modes import crypto as _cm
            if [i.get("mining") for i in _cm.CryptoMode._items() if i.get("mining")] != ["duco", "verus"]:
                FAILED.append("mining in the e-ink rotation")
            from PIL import Image as _Image
            probe = _cm.CryptoMode(pieapp.service.config, pieapp.service.panel.info())
            for m in _mining.MINERS.values():
                try:
                    img = probe._render_mining(probe.frame(), m, _t.time(), False)
                    if not isinstance(img, _Image.Image):
                        FAILED.append(f"{m.kind} e-ink page")
                except Exception as e:
                    print(f"  FAIL the {m.kind} e-ink page: {e}")
                    FAILED.append(f"{m.kind} e-ink page")
            print("  ok   both mining pages draw on the e-ink")
            from pie_ink import lcd_screens as _ls
            for m in _mining.MINERS.values():
                for wh in ((240, 280), (170, 320), (320, 240)):
                    try:
                        _ls._crypto_mining({"model": "1.69"}, wh, ((0, 0, 0), (255, 255, 255), (98, 210, 255), (140, 140, 140), (0, 255, 0), (255, 80, 80)), m)
                    except Exception as e:
                        print(f"  FAIL the {m.kind} LCD page at {wh}: {e}")
                        FAILED.append(f"{m.kind} lcd page")
            print("  ok   both mining pages draw on the LCD at three sizes")
            # the alert, on verus this time: two readings under the line, then two back over it
            fired = []
            orig = _mining.VERUS.on_alert
            _mining.VERUS.on_alert = lambda kind, text, data: (fired.append((kind, text)), orig(kind, text, data))
            fake_pool.verus_sols = 8000000.0
            _mining.VERUS.fetch()
            _mining.VERUS.fetch()
            fake_pool.verus_sols = 34419715.2
            _mining.VERUS.fetch()
            _mining.VERUS.fetch()
            _mining.VERUS.on_alert = orig
            kinds = [k for k, _ in fired]
            if kinds[:1] != ["drop"] or "back" not in kinds or "Verus" not in fired[0][1]:
                print(f"  FAIL the verus hashrate alert: {fired}")
                FAILED.append("verus alert")
            else:
                print(f"  ok   verus under the line: '{fired[0][1][:70]}…' then '{[t for k, t in fired if k == 'back'][0][:44]}'")
            # and nothing mining at all on duino
            fired.clear()
            orig = _mining.DUCO.on_alert
            _mining.DUCO.on_alert = lambda kind, text, data: (fired.append((kind, text)), orig(kind, text, data))
            kept = fake_pool.miners
            fake_pool.miners = []
            _mining.DUCO.fetch()
            _mining.DUCO.fetch()
            fake_pool.miners = kept
            _mining.DUCO.on_alert = orig
            _mining.DUCO.fetch()                              # the miners are back for the rest of the checks
            if not fired or fired[0][0] != "drop" or "Nothing is mining Duino-Coin" not in fired[0][1]:
                print(f"  FAIL nothing-mining should be said: {fired}")
                FAILED.append("duco nothing mining")
            ev = [e["text"] for e in pieapp.sight.events(limit=6, kinds=["mining"])]
            if not any("dropped" in t for t in ev) or not any("Nothing is mining" in t for t in ev):
                print(f"  FAIL the drops should be in the events list: {ev}")
                FAILED.append("mining events")
            hist = [m["text"] for m in pieapp.chat.history()[-8:] if m["role"] == "bot"]
            if not any("dropped" in t for t in hist):
                print(f"  FAIL the bot should have remarked on it: {hist}")
                FAILED.append("mining remark")
            tg = pieapp._tg_command("/mining")
            if "tester" not in tg or "ESP32-1" not in tg or "ryzen" not in tg or "luck 87.5%" not in tg:
                print(f"  FAIL /mining: {tg!r}")
                FAILED.append("telegram /mining")
            if "ryzen" not in pieapp._tg_command("/verus") or "ESP32-1" in pieapp._tg_command("/verus"):
                FAILED.append("telegram /verus")
            words = pieapp.mascot.context(pieapp.service.config)
            if not any("Duino-Coin mining (tester)" in ln for ln in words) or not any("Verus mining (" in ln for ln in words):
                print(f"  FAIL both pools should be in the bot's context: {[w for w in words if 'mining' in w]}")
                FAILED.append("mining in the bot's context")
            r = c.get("/api/now")
            check("GET /api/now (with mining)", r, body_has=["mining"])
            if len((r.get_json() or {}).get("mining") or []) != 2:
                FAILED.append("both pools in the sidebar")
            check("PUT /api/settings pools off", c.put("/api/settings", json={"duco": {"enabled": False}, "verus": {"enabled": False}}))
        finally:
            for m in _mining.MINERS.values():
                m.stop()
            _mining.DUCO_USER_URL, _mining.DUCO_NET_URL, _mining.VERUS_URL, _mining.DATA_DIR = real
            fake_pool.stop()

        print("your crypto: what you hold, what it is worth, and a word when that moves")
        from pie_ink import holdings as _hold
        r = c.put("/api/watchlist/bitcoin", json={"amount": 0.5})
        check("PUT /api/watchlist/bitcoin (you hold 0.5)", r, body_has=["items"])
        btc = next((i for i in (r.get_json() or {}).get("items", []) if i["id"] == "bitcoin"), {})
        r = c.get("/api/holdings")
        check("GET /api/holdings", r, body_has=["any", "value", "history", "line", "share"])
        hd = r.get_json() or {}
        row = next((x for x in hd.get("value", {}).get("items", []) if x["id"] == "bitcoin"), None)
        okay = btc.get("amount") == 0.5 and hd.get("any") and row and row["amount"] == 0.5 and row.get("price") \
            and abs(row["value"] - 0.5 * row["price"]) < 0.01 and abs(hd["value"]["total"] - row["value"]) < 0.01 and hd["line"] == 1000000
        print(f"  {'ok  ' if okay else 'FAIL'} 0.5 BTC at {_hold.money(row['price']) if row else '?'} is worth "
              f"{_hold.money(row['value']) if row else '?'} — the total, with a $1,000,000 line")
        if not okay:
            FAILED.append("holdings value")
        check("PUT /api/watchlist/bitcoin (a bad amount)", c.put("/api/watchlist/bitcoin", json={"amount": "lots"}), want=(400,))
        # the moments worth a word, on a watcher of its own with made-up totals
        real_path = _hold.PATH
        _hold.PATH = os.path.join(tempfile.mkdtemp(prefix="pie-ink-holdings-"), "holdings.json")
        try:
            h = _hold.Holdings()
            h.configure({"milestone": 1000000, "move_pct": 10})
            said = []
            h.on_alert = lambda kind, text, data: said.append((kind, text))

            def look(total, pct=1.0, when=None):
                v = {"total": total, "day": total * pct / 100, "day_pct": pct, "when": when or _t.time(), "unknown": [], "coins": 2,
                     "items": [{"symbol": "BTC", "value": total * 0.8, "change_1d": pct, "amount": 1}, {"symbol": "ETH", "value": total * 0.2, "change_1d": pct, "amount": 5}]}
                h.value_now = v
                with h._lock:
                    h._sample(total, v["when"])
                    fire = h._watch(v, v["when"])
                    h._save()
                for kind, text in fire:
                    h.on_alert(kind, text, v)
            t0 = _t.time() - 86400 * 3
            at = lambda k: t0 + k * 4000                                 # readings well apart (the same word waits hours)  # noqa: E731
            look(900000, 2.0, at(0))                                     # the first look: under the line, no news
            look(1100000, 3.0, at(1)); look(1100000, 3.0, at(2))         # two readings over it: rich
            look(950000, -1.0, at(3)); look(950000, -1.0, at(4))         # and two back under (more than 2 % under)
            kinds = [k for k, _ in said]
            okay = kinds == ["rich", "under"] and "$1,000,000" in said[0][1] and "You're rich" in said[0][1] and "$950,000" in said[1][1]
            print(f"  {'ok  ' if okay else 'FAIL'} past the line and back under it: {[t[:60] for _, t in said]}")
            if not okay:
                FAILED.append("holdings milestone")
            said.clear()
            look(836000, -12.0, at(5)); look(836000, -12.0, at(6))       # a crash: two readings down 12 %
            look(830000, -12.5, at(7))                                   # still down: no more said
            look(700000, -26.0, at(8)); look(700000, -26.0, at(9))       # a lot further: said again
            look(920000, -3.0, at(10)); look(920000, -3.0, at(11))       # eased
            look(1050000, 14.0, at(12)); look(1050000, 14.0, at(13))     # a rally (and past the line again)
            kinds = [k for k, _ in said]
            okay = kinds == ["crash", "crash", "rich", "rally"]
            print(f"  {'ok  ' if okay else 'FAIL'} a crash, a deeper one (the slump under the peak keeps quiet on a crash day), "
                  f"then a rally that takes it past the line: {kinds}")
            if not okay:
                FAILED.append("holdings crash")
                print("       " + "\n       ".join(t for _, t in said))
            words = h.words()
            okay = words.startswith("Your crypto holdings: worth $1,050,000") and "up 14.0% today" in words and "most it has ever been worth is $1,100,000" in words \
                and "past the $1,000,000 line" in words and "RALLY" in words
            print(f"  {'ok  ' if okay else 'FAIL'} the bot's line: {words[:120]}…")
            if not okay:
                FAILED.append("holdings words")
            st = h.status()
            if not (st["peak"]["total"] == 1100000 and st["over"] is True and st["move"]["kind"] == "rally" and len(st["history"]) >= 10):
                print(f"  FAIL the status: peak {st['peak']}, over {st['over']}, move {st['move']}, {len(st['history'])} readings")
                FAILED.append("holdings status")
            said.clear()
            for i in range(3):                                              # a quiet slide: under the line, and 23 % below the peak
                look(850000, -2.0, at(14 + i))                              # (once the rally has eased, two readings)
            kinds = [k for k, _ in said]
            okay = kinds == ["under", "slump"] and "23% below the most it has been worth ($1,100,000" in said[-1][1]
            print(f"  {'ok  ' if okay else 'FAIL'} a quiet day well below the peak: {kinds} — '{said[-1][1][:64] if said else ''}…'")
            if not okay:
                FAILED.append("holdings slump")
            h2 = _hold.Holdings()                                           # the file remembers
            if not (h2.peak and h2.peak[0] == 1100000 and h2.over is False and h2.slump and len(h2.samples) == len(h.samples)):
                print(f"  FAIL not remembered: {h2.peak} {h2.over} {h2.slump} {len(h2.samples)}")
                FAILED.append("holdings file")
            n = len(said)
            h.configure({"milestone": 500000, "move_pct": 10})              # the line moved under the total: not news
            if h.over is not True or len(said) != n:
                FAILED.append("holdings line moved")
        finally:
            _hold.PATH = real_path
        # the page the screen shows, the bot's context, the sidebar, Telegram
        from pie_ink.modes import crypto as _cm2
        if not any(i.get("holdings") for i in _cm2.CryptoMode._items()):
            FAILED.append("holdings in the e-ink rotation")
        probe = _cm2.CryptoMode(pieapp.service.config, pieapp.service.panel.info())
        try:
            img = probe._render_holdings(probe.frame(), _t.time(), False)
            okay = isinstance(img, _Image.Image)
        except Exception as e:
            print(f"  FAIL the holdings e-ink page: {e}")
            okay = False
        if okay:
            print("  ok   the holdings page draws on the e-ink, and is in the rotation")
        else:
            FAILED.append("holdings e-ink page")
        words = pieapp.mascot.context(pieapp.service.config)
        if not any(ln.startswith("Your crypto holdings: worth") for ln in words):
            print(f"  FAIL the bot should know the holdings: {[w for w in words if 'crypto' in w]}")
            FAILED.append("holdings in the bot's context")
        tg = pieapp._tg_command("/holdings")
        if "Your crypto:" not in tg or "BTC: 0.5" not in tg or "Line: $1,000,000" not in tg:
            print(f"  FAIL /holdings: {tg!r}")
            FAILED.append("telegram /holdings")
        else:
            print(f"  ok   /holdings: {tg.splitlines()[0]}")
        r = c.get("/api/now")
        check("GET /api/now (with holdings)", r, body_has=["holdings"])
        r = c.post("/api/holdings/try", json={"kind": "rich"})
        check("POST /api/holdings/try", r, body_has=["text", "model"])
        _t.sleep(1.0)
        if "You're rich" not in (pieapp.llm.latest().get("text") or "") or "You're rich" not in [m["text"] for m in pieapp.chat.history()[-3:] if m["role"] == "bot"][-1]:
            print(f"  FAIL the try-out should reach the bot's screen: {pieapp.llm.latest().get('text')!r}")
            FAILED.append("holdings try")
        else:
            print(f"  ok   'Past the line' on the page: the bot says '{(r.get_json() or {}).get('text', '')[:60]}…'")
        check("POST /api/holdings/try (nonsense)", c.post("/api/holdings/try", json={"kind": "lambo"}), want=(400,))
        check("PUT /api/watchlist/bitcoin (none held)", c.put("/api/watchlist/bitcoin", json={"amount": 0}))
        if (c.get("/api/holdings").get_json() or {}).get("any") or any(i.get("holdings") for i in _cm2.CryptoMode._items()):
            FAILED.append("holdings cleared")

        stocks_check(pieapp, c)
        pitato_check(pieapp, c)
        shelf_check(pieapp, c)
        memory_check(pieapp, c)
        voice_check(pieapp, c)
        pitato_screen_check(pieapp, c)

        print("Pietato by its name now; what it has fetched, used here")
        pietato_check(pieapp, c)

        print("home assistant")
        fake = _FakeHome()
        fake.start()
        try:
            check("PUT /api/settings home", c.put("/api/settings", json={"home": {
                "enabled": True, "url": f"http://127.0.0.1:{fake.port}", "token": "test-token",
                "tv": "media_player.living_room_tv", "remote": "remote.living_room_tv", "preset": "samsung",
                "names": "living room tv, tv, television", "others": True, "say_back": True}}))
            r = c.post("/api/home/check", json={})
            check("POST /api/home/check", r, body_has=["ok", "players", "remotes"])
            d = r.get_json() or {}
            if not d.get("ok") or len(d.get("players", [])) != 1 or d.get("name") != "Test House":
                print(f"  FAIL the check did not see the fake house: {d}")
                FAILED.append("home check")
            check("GET /api/home", c.get("/api/home"), body_has=["configured", "players", "presets", "sources"])
            wanted = [
                ("turn on the living room tv", "TV on.", ("media_player", "turn_on", {"entity_id": "media_player.living_room_tv"})),
                ("go to channel five four five two", "Channel 5452.", ("media_player", "play_media",
                                                                        {"entity_id": "media_player.living_room_tv", "media_content_type": "channel", "media_content_id": "5452"})),
                ("volume up 2", "Volume up 2.", ("media_player", "volume_up", {"entity_id": "media_player.living_room_tv"})),
                ("mute", "Muted.", ("media_player", "volume_mute", {"entity_id": "media_player.living_room_tv", "is_volume_muted": True})),
                ("open netflix", "Netflix.", ("media_player", "select_source", {"entity_id": "media_player.living_room_tv", "source": "Netflix"})),
                ("channel up", "Channel up.", ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": "KEY_CHUP"})),
                ("press home", "Home.", ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": "KEY_HOME"})),
                ("turn off the lamp", "Lamp off.", ("homeassistant", "turn_off", {"entity_id": "light.lamp"})),
                ("turn on the kitchen lights", "Kitchen Light on.", ("homeassistant", "turn_on", {"entity_id": "light.kitchen_light"})),
                ("turn it off", "Kitchen Light off.", ("homeassistant", "turn_off", {"entity_id": "light.kitchen_light"})),
                ("movie night on", "Movie Night set.", ("scene", "turn_on", {"entity_id": "scene.movie_night"})),
                ("turn off the tv and the lamp", "TV off. Lamp off.", ("homeassistant", "turn_off", {"entity_id": "light.lamp"})),
            ]
            for said, want_reply, want_call in wanted:
                fake.calls.clear()
                r = c.post("/api/home/do", json={"text": said})
                reply = (r.get_json() or {}).get("reply")
                if r.status_code != 200 or reply != want_reply or want_call not in fake.calls:
                    print(f"  FAIL {said!r}: status {r.status_code}, reply {reply!r}, calls {fake.calls}")
                    FAILED.append(f"home {said}")
                else:
                    print(f"  ok   {said!r} → {reply}")
            fake.calls.clear()
            c.post("/api/home/do", json={"text": "volume up 2"})
            if fake.calls.count(("media_player", "volume_up", {"entity_id": "media_player.living_room_tv"})) != 2:
                print(f"  FAIL volume up 2 did not press twice: {fake.calls}")
                FAILED.append("home volume steps")
            check("POST /api/home/do (not a command)", c.post("/api/home/do", json={"text": "what is the weather"}), want=(400,))
            r = c.post("/api/home/do", json={"text": "is the tv on"})
            if (r.get_json() or {}).get("reply") != "The TV is on.":
                print(f"  FAIL asking about the tv: {r.get_json()}")
                FAILED.append("home query")
            r = c.post("/api/llm/chat", json={"message": "turn off the television"})
            d = r.get_json() or {}
            check("POST /api/llm/chat (a home command, no bot needed)", r, body_has=["reply", "home"])
            if d.get("reply") != "TV off.":
                print(f"  FAIL the chat bar did not do the tv: {d}")
                FAILED.append("home chat")
            if pieapp._tg_command("turn on the tv") != "TV on.":
                print("  FAIL telegram did not do the tv")
                FAILED.append("home telegram")
            else:
                print("  ok   telegram: 'turn on the tv' → TV on.")
            # the /tv remote in Telegram: a message with buttons, and the taps
            remote = pieapp._tg_command("/tv")
            labels = [b[0] for row in (remote.get("buttons") if isinstance(remote, dict) else []) for b in row]
            if not isinstance(remote, dict) or not {"▲", "▼", "◀", "▶", "OK", "On", "Off", "5"} <= set(labels):
                print(f"  FAIL /tv did not come back as a remote: {remote}")
                FAILED.append("telegram /tv")
            else:
                print(f"  ok   /tv → {len(labels)} buttons under '{remote['text'][:40]}…'")
            from pie_ink import telegram as _tg
            kb = _tg.keyboard(remote["buttons"]) if isinstance(remote, dict) else {}
            if not kb.get("inline_keyboard") or kb["inline_keyboard"][3][1] != {"text": "OK", "callback_data": "tv:ok"}:
                print(f"  FAIL the keyboard markup is off: {str(kb)[:200]}")
                FAILED.append("telegram keyboard")
            taps = [("tv:up", "Up.", "KEY_UP"), ("tv:ok", "OK.", "KEY_ENTER"), ("tv:5", "Pressed 5.", "KEY_5"),
                    ("tv:back", "Back.", "KEY_RETURN"), ("tv:mute", "Mute.", "KEY_MUTE")]
            for data, want_reply, want_key in taps:
                fake.calls.clear()
                got = pieapp._tg_tap(data)
                sent = ("remote", "send_command", {"entity_id": "remote.living_room_tv", "command": want_key})
                if got != want_reply or sent not in fake.calls:
                    print(f"  FAIL tap {data}: {got!r}, calls {fake.calls}")
                    FAILED.append(f"telegram tap {data}")
                else:
                    print(f"  ok   tap {data} → {got}")
            fake.calls.clear()
            if pieapp._tg_tap("tv:on") != "TV on." or ("media_player", "turn_on", {"entity_id": "media_player.living_room_tv"}) not in fake.calls:
                print("  FAIL tap tv:on")
                FAILED.append("telegram tap on")
            for said, want in (("/tv 5452", "Channel 5452."), ("/tv off", "TV off."), ("/tv volume up", "Volume up."),
                               ("/tv netflix", "Netflix."), ("/remote", None)):
                got = pieapp._tg_command(said)
                okay = (isinstance(got, dict) if want is None else got == want)
                print(f"  {'ok  ' if okay else 'FAIL'} {said} → {got if not isinstance(got, dict) else 'the remote'}")
                if not okay:
                    FAILED.append(f"telegram {said}")
            if not pieapp._tg_tap("tv:nothing").startswith("That button"):
                FAILED.append("telegram stale tap")
            # turning a TV on: Home Assistant says yes whether or not the TV heard. So it's asked whether it came
            # on, and everything that can wake one goes — the remote's turn_on, and Wake-on-LAN from this Pi
            import socket as _socket
            from pie_ink import home as _home
            wol = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            wol.bind(("127.0.0.1", 0))
            wol.settimeout(2.0)
            saved_wol = (_home.WOL_PORTS, _home.TV_ON_WAIT)
            _home.WOL_PORTS, _home.TV_ON_WAIT = (wol.getsockname()[1],), 0.4
            try:
                fake.tv_follows, fake.tv_stubborn = True, True
                _home.HOME._macs.clear()
                _home.HOME._registry = None
                c.post("/api/home/do", json={"text": "turn off the tv"})
                fake.calls.clear()
                r = c.post("/api/home/do", json={"text": "turn on the tv"})
                reply = (r.get_json() or {}).get("reply") or ""
                calls = [(d, sv) for d, sv, _ in fake.calls]
                if "hasn't said it's on" not in reply or "MAC address" not in reply or ("remote", "turn_on") not in calls:
                    print(f"  FAIL a TV that stays off should be reported, with the remote tried too: {reply!r} {calls}")
                    FAILED.append("home tv stays off")
                else:
                    print(f"  ok   a TV that doesn't come on: {reply[:70]}…")
                # Home Assistant knows the TV's MAC (its device registry): nothing to type — the wake-up goes from the Pi,
                # to the LAN and to wherever this Pi last saw that MAC (its ARP table: here, a pretend one)
                import tempfile as _tempfile
                fake.tv_mac = "aa:bb:cc:dd:ee:ff"
                _home.HOME._macs.clear()
                _home.HOME._registry = None
                with _tempfile.NamedTemporaryFile("w", suffix=".arp", delete=False) as fh:
                    fh.write("IP address       HW type     Flags       HW address            Mask     Device\n"
                             "127.0.0.1        0x1         0x2         aa:bb:cc:dd:ee:ff     *        lo\n")
                saved_arp, _home.ARP = _home.ARP, fh.name
                fake.tv_stubborn = False
                fake.calls.clear()
                r = c.post("/api/home/do", json={"text": "turn on the tv"})
                reply = (r.get_json() or {}).get("reply") or ""
                try:
                    packet, _ = wol.recvfrom(200)
                except OSError:
                    packet = b""
                calls = [(d, sv) for d, sv, _ in fake.calls]
                found = (c.get("/api/home").get_json() or {}).get("tv_mac_found")
                via = ((c.get("/api/home").get_json() or {}).get("players") or [{}])[0].get("via")
                if reply != "TV on." or packet != _home.magic_packet("AA-BB-CC-DD-EE-FF") or fake.tv()["state"] != "on" \
                        or ("wake_on_lan", "send_magic_packet") not in calls or found != "AA:BB:CC:DD:EE:FF" or via != "Samsung TV":
                    print(f"  FAIL the MAC should come from Home Assistant's registry and the wake-up go from the Pi: "
                          f"{reply!r}, {len(packet)} bytes, {calls}, found {found!r}, via {via!r}")
                    FAILED.append("home wake on lan")
                else:
                    print("  ok   the MAC comes from Home Assistant's own registry (nothing typed): a magic packet from the Pi "
                          "(6×FF + the MAC ×16), Home Assistant's own asked too, and 'TV on.' once it says so; the picker says 'Samsung TV'")
                fake.tv_stubborn = True
                c.post("/api/home/do", json={"text": "turn off the tv"})
                r = c.post("/api/home/do", json={"text": "turn on the tv"})
                reply = (r.get_json() or {}).get("reply") or ""
                trail = ((r.get_json() or {}).get("last") or {}).get("trail") or []
                if "didn't answer" not in reply or "Power On with Mobile" not in reply or not any("wake-on-lan from the Pi" in t for t in trail) \
                        or not any("the TV says off" in t for t in trail):
                    print(f"  FAIL with the MAC known and the TV still off, it should say the TV ignored the wake-up, and show what it did: {reply!r} {trail}")
                    FAILED.append("home tv slow")
                else:
                    print(f"  ok   the page is shown what was done: {' · '.join(trail)}")
                # the same TV through SmartThings as well: that one turns it on by the cloud — say so
                fake.registry_extra = {"media_player.living_room_tv_2": "smartthings"}
                _home.HOME._registry = None
                reply = (c.post("/api/home/do", json={"text": "turn on the tv"}).get_json() or {}).get("reply") or ""
                fake.registry_extra = {}
                _home.HOME._registry = None
                if "living_room_tv_2" not in reply or "SmartThings" not in reply:
                    print(f"  FAIL with a SmartThings entity for the TV about, it should be suggested: {reply!r}")
                    FAILED.append("home smartthings hint")
                else:
                    print("  ok   with a SmartThings entity for the same TV, it says to pick that one")
                # what the entity says it can do decides the call, as a remote that works does: a player that
                # doesn't claim turn_on gets toggle while it's off (its turn_on), one that does gets turn_on
                fake.tv_stubborn = False
                fake.tv()["attributes"]["supported_features"] = 256 | 4 | 8     # turn_off, volume, mute — no turn_on
                c.post("/api/home/do", json={"text": "turn off the tv"})
                fake.calls.clear()
                reply = (c.post("/api/home/do", json={"text": "turn on the tv"}).get_json() or {}).get("reply") or ""
                services = [(d, sv) for d, sv, _ in fake.calls if d == "media_player"]
                okay = reply == "TV on." and services == [("media_player", "toggle")] and fake.tv()["state"] == "on"
                print(f"  {'ok  ' if okay else 'FAIL'} a player without turn_on in its features is toggled on, as the gesture remote does: {reply} {services}")
                if not okay:
                    FAILED.append("home toggle when no turn_on")
                fake.tv()["attributes"]["supported_features"] = 256 | 128 | 4 | 8
                c.post("/api/home/do", json={"text": "turn off the tv"})
                fake.calls.clear()
                reply = (c.post("/api/home/do", json={"text": "turn on the tv"}).get_json() or {}).get("reply") or ""
                services = [(d, sv) for d, sv, _ in fake.calls if d == "media_player"]
                okay = reply == "TV on." and services == [("media_player", "turn_on")]
                print(f"  {'ok  ' if okay else 'FAIL'} one that claims turn_on gets turn_on: {reply} {services}")
                if not okay:
                    FAILED.append("home turn_on when supported")
                players = (c.post("/api/home/check", json={}).get_json() or {}).get("players") or []
                if not players or players[0].get("can_on") is not True:
                    print(f"  FAIL the picker should say whether a player can turn on from off: {players}")
                    FAILED.append("home can_on")
                del fake.tv()["attributes"]["supported_features"]
                fake.tv_follows, fake.tv_stubborn = False, False
                fake.tv()["state"] = "on"
                fake.tv_mac = ""
                _home.HOME._macs.clear()
                os.unlink(_home.ARP)
                _home.ARP = saved_arp
            finally:
                _home.WOL_PORTS, _home.TV_ON_WAIT = saved_wol
                wol.close()
            fake.token = "other"                          # a refused token is a message, not a crash
            r = c.post("/api/home/do", json={"text": "turn on the tv"})
            if not ((r.get_json() or {}).get("reply") or "").startswith("Couldn't"):
                print(f"  FAIL a bad token should be reported: {r.get_json()}")
                FAILED.append("home bad token")
            else:
                print("  ok   a refused token is reported, not raised")
            fake.token = "test-token"                     # the right one again, for how you feel
            moods_check(pieapp, c, fake)
            check("PUT /api/settings home off", c.put("/api/settings", json={"home": {"enabled": False}}))
            check("POST /api/home/do (off)", c.post("/api/home/do", json={"text": "turn on the tv"}), want=(400,))
        finally:
            fake.stop()


        print("a camera that turns (a pretend OBSBOT)")
        from pie_ink import ptz as _ptz, sight as _sight, watcher as _watcher
        from PIL import Image as _Img
        cam_state = {_ptz.CID["pan"]: 0, _ptz.CID["tilt"]: 0, _ptz.CID["zoom"]: 0}
        limits = {_ptz.CID["pan"]: (-504000, 504000, 3600), _ptz.CID["tilt"]: (-324000, 324000, 3600), _ptz.CID["zoom"]: (0, 100, 1)}
        real = (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
                _ptz.xu_query, _ptz.vendor_of, _ptz.Ptz._awake, _ptz.Ptz._wake)

        def fake_query(fd, cid):
            if cid not in limits:
                return None
            lo, hi, step = limits[cid]
            return {"id": cid, "name": "x", "min": lo, "max": hi, "step": step, "default": 0}
        _ptz.queryctrl = fake_query
        _ptz.g_ctrl = lambda fd, cid: cam_state[cid]
        _ptz.s_ctrl = lambda fd, cid, v: cam_state.__setitem__(cid, v)
        _ptz.s_ext_ctrls = lambda fd, pairs: [cam_state.__setitem__(c, v) for c, v in pairs]
        _ptz.Ptz._candidates = lambda self: ["/dev/null"]
        view = {"n": 0}

        def fake_settle(self, timeout=3.5):
            view["n"] += 1
            return _Img.new("RGB", (480, 360), (40 * (view["n"] % 5), 90, 120))
        _ptz.Ptz.settle = fake_settle
        _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0
        saved_hold, saved_rise = _ptz.SWEEP_HOLD, _ptz.RISE
        _ptz.SWEEP_HOLD, _ptz.RISE = 0.0, 0.0                  # no waiting about in a test
        ollama2 = _FakeOllama()
        ollama2.start()
        try:
            check("PUT /api/settings ptz (turning by itself, fast for the test)",
                  c.put("/api/settings", json={"ptz": {"gentle": 1000}}))
            r = c.get("/api/ptz")
            check("GET /api/ptz", r, body_has=["available", "position"])
            if not (r.get_json() or {}).get("available"):
                print(f"  FAIL the pretend camera should be found: {r.get_json()}")
                FAILED.append("ptz found")
            # a glide: many small steps, easing in and out, and it ends where it was sent
            steps, real_ext = [], _ptz.s_ext_ctrls
            _ptz.s_ext_ctrls = lambda fd, pairs: (steps.append(dict(pairs)), real_ext(fd, pairs))
            _ptz.PTZ.set(pan=0, tilt=0)
            steps.clear()
            _ptz.PTZ.glide(pan=40, speed=80)
            _ptz.s_ext_ctrls = real_ext
            pans = [s[_ptz.CID["pan"]] / 3600 for s in steps if _ptz.CID["pan"] in s]
            if len(pans) < 4 or pans[-1] != 40 or pans[0] > 5 or any(b < a for a, b in zip(pans, pans[1:])):
                print(f"  FAIL a glide should turn in small steps and end where it was sent: {pans}")
                FAILED.append("ptz glide")
            else:
                print(f"  ok   a glide to 40° went in {len(pans)} small steps, first {pans[0]:.0f}°, and ended at 40°")
            _ptz.PTZ.set(pan=0, tilt=0)
            # OBSBOT's framed command, byte for byte as captured from its own app (wake and sleep)
            pad = lambda h: (h + "0" * 120)[:120]                  # noqa: E731
            if _ptz.v3_frame(0x0C, 0xA0C2, 2, b"\0\0\0\0").hex() != pad("aa250c000c0089420a02c2a00400be07") \
                    or _ptz.v3_frame(0x12, 0xA0C2, 2, b"\1\0\0\0").hex() != pad("aa2512000c00e9220a02c2a00400bffb01000000"):
                print("  FAIL the wake frame doesn't match the captured one")
                FAILED.append("ptz wake frame")
            else:
                print("  ok   the wake and sleep frames match OBSBOT's own, byte for byte (CRC-16/USB and all)")
            r = c.post("/api/ptz/set", json={"pan": 90, "tilt": 30, "zoom": 0.5})
            check("POST /api/ptz/set", r, body_has=["position"])
            if cam_state[_ptz.CID["pan"]] != 324000 or cam_state[_ptz.CID["tilt"]] != 108000 or cam_state[_ptz.CID["zoom"]] != 50:
                print(f"  FAIL 90° right, 30° up, zoom half should be 324000/108000/50: {cam_state}")
                FAILED.append("ptz set units")
            else:
                print("  ok   90° right, 30° up, zoom 50% → pan_absolute=324000 tilt_absolute=108000 zoom_absolute=50")
            check("POST /api/ptz/set center", c.post("/api/ptz/set", json={"center": True}))
            c.post("/api/ptz/set", json={"zoom": 0})                  # zoomed in, the stick is gentler on purpose
            # the stick over HTTP: held for a second, then a stop; a late message can't restart it
            for i in range(1, 7):
                c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "t", "seq": i, "speed": 60})
                _t.sleep(0.17)
            c.post("/api/ptz/stop", json={"sid": "t", "seq": 20})
            c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "t", "seq": 19})
            _t.sleep(0.5)
            pan = cam_state[_ptz.CID["pan"]] / 3600
            if not 30 <= pan <= 75 or _ptz.PTZ.driving():
                print(f"  FAIL a second of full stick should turn it ~60° and then stop: {pan}° driving={_ptz.PTZ.driving()}")
                FAILED.append("ptz stick")
            else:
                print(f"  ok   a second of full stick turned it {pan:.0f}°, and it stopped (a late message didn't restart it)")
            if not _ptz.PTZ.quiet():
                FAILED.append("ptz quiet after moving")
            check("POST /api/ptz/home (save)", c.post("/api/ptz/home", json={"save": True}), body_has=["home"])
            c.post("/api/ptz/set", json={"center": True})
            r = c.post("/api/ptz/home", json={})
            if abs(((r.get_json() or {}).get("position") or {}).get("pan", 0) - pan) > 1:
                print(f"  FAIL home should come back to {pan}: {r.get_json()}")
                FAILED.append("ptz home")
            c.post("/api/ptz/set", json={"center": True, "zoom": 0})
            # in words: no model yet, so counts from OpenCV
            for ask, want in {"look left": "Looking left", "zoom in": "Zoomed in", "zoom out a bit": "Zoomed out",
                              "look straight ahead": "straight ahead", "look around": "Left:"}.items():
                r = c.post("/api/llm/chat", json={"message": ask})
                reply = (r.get_json() or {}).get("reply") or (r.get_json() or {}).get("error") or ""
                okay = r.status_code == 200 and want.lower() in reply.lower()
                print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply[:80]!r}")
                if not okay:
                    FAILED.append(f"ptz: {ask}")
            if cam_state[_ptz.CID["pan"]] != 0:
                print(f"  FAIL looking around should turn back to where it was: {cam_state}")
                FAILED.append("ptz sweep back")
            for words in ("look up the weather in paris", "turn left at the lights", "go home", "what can you see"):
                if _ptz.parse(words) is not None or pieapp._camera_command(words, "typed") is not None:
                    print(f"  FAIL '{words}' isn't a camera command")
                    FAILED.append("ptz not greedy")
            tg = pieapp._tg_command("/look left")
            if not (isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff" and "left" in tg[1].lower()):
                print(f"  FAIL /look left should be a picture and a line: {str(tg)[:80]}")
                FAILED.append("telegram /look")
            else:
                print(f"  ok   /look left → a picture: {tg[1]!r}")
            # the page's button: in the background, the answer in the conversation
            check("POST /api/ptz/look", c.post("/api/ptz/look", json={"where": "around"}), body_has=["started"])
            for _ in range(50):
                if (_ptz.PTZ.last_look or {}).get("text"):
                    break
                _t.sleep(0.1)
            if not (_ptz.PTZ.last_look or {}).get("text", "").startswith("Left:"):
                print(f"  FAIL the look's answer: {_ptz.PTZ.last_look}")
                FAILED.append("ptz look button")
            # the watcher leaves the picture alone while the camera turns
            _ptz.PTZ.set(pan=10)
            w = _watcher.Watcher()
            w._previous = "something"
            w.conf = {"enabled": True, "interval": 0.5}
            w.stop_flag.clear()
            th = _th.Thread(target=w._run, daemon=True)
            th.start()
            _t.sleep(1.2)
            w.stop()
            if w._previous is not None and w.level:
                print("  FAIL the watcher should start afresh while the camera turns")
                FAILED.append("watcher ptz")
            else:
                print("  ok   the watcher ignores the picture while the camera turns")
            # the Seen list starts a new baseline, quietly, when the view changed
            _sight.clear()
            check("PUT /api/settings llm (for the looks)", c.put("/api/settings", json={
                "llm": {"enabled": True, "host": f"http://127.0.0.1:{ollama2.port}", "model": "fake:latest", "see": True},
                "camera_watch": {"events": True, "detector": "model"}}))
            frame = _Img.new("RGB", (320, 240), (90, 120, 150))
            with _sight._lock:
                _sight._state["last"], _sight._state["view"] = None, None
            _, first = _sight.look(frame, pieapp.service.config, "T", view=(0, 0, 0))
            ollama2.scene = dict(ollama2.scene, people=0, animals=[])
            _, moved = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            _, same = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            ollama2.scene = dict(ollama2.scene, people=1)
            _, arrived = _sight.look(frame, pieapp.service.config, "T", view=(6, 0, 0))
            if not first or moved or same or not arrived:
                print(f"  FAIL views: first {[e['text'] for e in first]} moved {moved} same {same} arrived {arrived}")
                FAILED.append("sight view baseline")
            else:
                print(f"  ok   a new view is a quiet baseline (no 'Nobody detected' just because the camera moved); "
                      f"from the same view changes still count: {arrived[0]['text']!r}")
            # with a model: the look is described, and the agent turns to look before deciding
            r = c.post("/api/llm/chat", json={"message": "look right and tell me if the window is open"})
            if "window" not in ((r.get_json() or {}).get("reply") or ""):
                print(f"  FAIL the look should be described by the model: {r.get_json()}")
                FAILED.append("ptz describe")
            else:
                print(f"  ok   'look right and tell me if the window is open' → {r.get_json()['reply']!r}")
            # yes and no with its head: the decision model is asked first, the head moves, the words are told
            from pie_ink import pet as _pet
            try:
                check("PUT /api/settings pet on (quick, for the test)", c.put("/api/settings", json={"pet": {"enabled": True, "model": "tev1", "tempo": 3.0}}))
                for ask, want, axis, verb in (("are you happy?", "yes", "tilt", "nod"), ("do you like rain", "no", "pan", "shake"),
                                              ("is the window open?", "unsure", "tilt", "tilt")):
                    ollama2.yesno, ollama2.yesno_asks, ollama2.prompts = want, [], []
                    _ptz.PTZ.set(pan=20, tilt=5)
                    steps, real_ext = [], _ptz.s_ext_ctrls
                    _ptz.s_ext_ctrls = lambda fd, pairs: (steps.append(dict(pairs)), real_ext(fd, pairs))
                    r = c.post("/api/llm/chat", json={"message": ask})
                    for _ in range(80):                                  # the head moves in the background
                        if (_pet.status().get("done") or _pet.status().get("error")) and not _ptz.PTZ.gesturing:
                            break
                        _t.sleep(0.1)
                    _ptz.s_ext_ctrls = real_ext
                    last = _pet.status()
                    asked = ollama2.yesno_asks[-1] if ollama2.yesno_asks else {}
                    told = next((p.get("prompt", "") for p in ollama2.prompts if ask in p.get("prompt", "")), "")
                    cid = _ptz.CID[axis]
                    values = [st[cid] / 3600 for st in steps if cid in st]
                    centre = 5 if axis == "tilt" else 20
                    moved = (max(values) > centre + 3 and (min(values) < centre - 3 or verb == "tilt")) if values else False
                    back = cam_state[_ptz.CID["pan"]] == 72000 and cam_state[_ptz.CID["tilt"]] == 18000
                    head = (r.get_json() or {}).get("head") or {}
                    okay = (r.status_code == 200 and asked.get("model") == "tev1" and "yes" in (asked.get("questions", {}).get("answer", {}).get("criteria") or {})
                            and ask in asked.get("state", "") and last.get("answer") == want and last.get("gesture") == verb
                            and last.get("moved") and moved and back and "head" in told and head.get("answer") == want
                            and {"yes": "is YES", "no": "is NO", "unsure": "can't say for sure"}[want] in told)
                    print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → the model said {want}: it {dict(nod='nodded', shake='shook', tilt='tilted')[verb]} its head"
                          f" ({len(values)} moves on {axis}, back where it was: {back}); the words were told")
                    if not okay:
                        print(f"       status {r.status_code}, asked {str(asked)[:120]}, last {last}, {axis} {values}, told {told[-160:]!r}")
                        FAILED.append(f"pet {want}")
                # only tev1:0.8b pulled: "tev1" in the settings means that one (Ollama itself would say 404)
                from pie_ink import llm as _llm
                ollama2.tags, ollama2.yesno_asks = ["fake:latest", "tev1:0.8b", "gemma3:4b"], []
                _llm._tags.clear()
                c.post("/api/llm/chat", json={"message": "is it sunny?"})
                for _ in range(40):
                    if not _ptz.PTZ.gesturing and _pet.status().get("done"):
                        break
                    _t.sleep(0.1)
                sent = (ollama2.yesno_asks[-1] if ollama2.yesno_asks else {}).get("model")
                if sent != "tev1:0.8b" or _pet.status().get("model") != "tev1:0.8b":
                    print(f"  FAIL with only tev1:0.8b on the machine, 'tev1' should mean that: asked for {sent!r}")
                    FAILED.append("pet model by base name")
                else:
                    print("  ok   with only tev1:0.8b pulled, 'tev1' in the settings asks for tev1:0.8b")
                ollama2.tags = ["fake:latest", "tev1:latest"]
                _llm._tags.clear()
                # no bot model at all: the head still answers, with a plain word for the page
                check("PUT /api/settings llm (no model)", c.put("/api/settings", json={"llm": {"model": ""}}))
                ollama2.yesno = "yes"
                r = c.post("/api/llm/chat", json={"message": "are you there?"})
                for _ in range(40):
                    if not _ptz.PTZ.gesturing and _pet.status().get("done"):
                        break
                    _t.sleep(0.1)
                if r.status_code != 200 or (r.get_json() or {}).get("reply") != "Yes.":
                    print(f"  FAIL with no bot model the head should answer and the words be plain: {r.status_code} {r.get_json()}")
                    FAILED.append("pet no bot")
                else:
                    print("  ok   with no bot model: a nod, and 'Yes.' on the page")
                check("PUT /api/settings llm (the fake model again)", c.put("/api/settings", json={"llm": {"model": "fake:latest"}}))
                ollama2.yesno_asks = []
                c.post("/api/llm/chat", json={"message": "tell me a joke"})
                if ollama2.yesno_asks:
                    print("  FAIL 'tell me a joke' isn't a yes/no question")
                    FAILED.append("pet not greedy")
                check("PUT /api/settings pet off", c.put("/api/settings", json={"pet": {"enabled": False}}))
                ollama2.yesno_asks = []
                c.post("/api/llm/chat", json={"message": "are you happy?"})
                if ollama2.yesno_asks:
                    print("  FAIL switched off, the head shouldn't answer")
                    FAILED.append("pet off")
                check("PUT /api/settings pet on again", c.put("/api/settings", json={"pet": {"enabled": True}}))
                check("POST /api/pet/try", c.post("/api/pet/try", json={"gesture": "nod"}), body_has=["started"])
                for _ in range(40):
                    if not _ptz.PTZ.gesturing:
                        break
                    _t.sleep(0.1)
                check("GET /api/pet", c.get("/api/pet"), body_has=["available", "last", "model"])
                # from Telegram: /yes and /no
                _ptz.PTZ.set(pan=20, tilt=5)
                for cmd, want, axis in (("/yes", "Nodding.", "tilt"), ("/no", "Shaking its head.", "pan")):
                    steps, real_ext = [], _ptz.s_ext_ctrls
                    _ptz.s_ext_ctrls = lambda fd, pairs: (steps.append(dict(pairs)), real_ext(fd, pairs))
                    got = pieapp._tg_command(cmd)
                    for _ in range(60):
                        if not _ptz.PTZ.gesturing and len(steps) >= 5:
                            break
                        _t.sleep(0.1)
                    _ptz.s_ext_ctrls = real_ext
                    moved = [st[_ptz.CID[axis]] for st in steps if _ptz.CID[axis] in st]
                    okay = got == want and len(moved) >= 4 and not _ptz.PTZ.gesturing
                    print(f"  {'ok  ' if okay else 'FAIL'} {cmd} from Telegram → {got!r}, {len(moved)} moves on {axis}")
                    if not okay:
                        FAILED.append(f"telegram {cmd}")
                busy = pieapp._tg_command("/yes") if _ptz.PTZ.gesturing else None
                if busy and "busy" not in busy:
                    FAILED.append("telegram /yes busy")
            finally:
                c.put("/api/settings", json={"pet": {"tempo": 1.6}})
                _ptz.PTZ.set(pan=0, tilt=0)
            ollama2.decision = {"thought": "Something moved at the right edge.", "level": "note",
                                "actions": [{"do": "look", "text": "right"}], "remember": ""}
            check("PUT /api/settings agent on (may look)", c.put("/api/settings", json={
                "agent": {"enabled": True, "every_minutes": 30, "look": False, "can_look": True, "can_show": True}}))
            _t.sleep(0.3)
            before_pan = cam_state[_ptz.CID["pan"]]
            entry = pieapp.agent.AGENT.tick("test")
            if not entry or "looked" not in entry or "cat" not in entry.get("thought", "") or cam_state[_ptz.CID["pan"]] != before_pan:
                print(f"  FAIL the agent should look right, decide again, and turn back: {entry} pan {cam_state[_ptz.CID['pan']]} vs {before_pan}")
                FAILED.append("agent look")
            else:
                print(f"  ok   the agent looked {entry['looked'][:60]!r}… then thought {entry['thought']!r}, and turned back")
            check("PUT /api/settings agent off", c.put("/api/settings", json={"agent": {"enabled": False}, "llm": {"enabled": False}}))

            # Follow me: an OBSBOT's own tracking, through its vendor unit, as a switch that stays put
            import errno as _errno
            xu = {"mode": 2, "sets": [], "asleep": False, "frames": []}   # it starts up tracking, as a Tiny 2 can

            def fake_xu(fd, unit, selector, query, data=b""):
                if unit != 2:
                    raise OSError(_errno.ENOENT, "no such unit")
                if query == _ptz.UVC_SET_CUR and selector == _ptz.XU_COMMAND:
                    xu["frames"].append(bytes(data))      # a framed command: "run" wakes it
                    if data[10:12] == b"\xc2\xa0" and data[16:20] == b"\0\0\0\0":
                        xu["asleep"] = False
                    return bytes(60)
                if query == _ptz.UVC_SET_CUR:
                    if xu["asleep"]:
                        return bytes(60)                  # asleep, it takes nothing
                    xu["sets"].append(bytes(data[:4]).hex())
                    if data[:1] == b"\x16":
                        xu["mode"] = data[2]
                    return bytes(60)
                block = bytearray(60)
                block[24] = xu["mode"]
                block[2] = 1 if xu["asleep"] else 0
                block[5] = 1                                  # the rest of a real block isn't all zeros either
                return bytes(block)
            _ptz.xu_query, _ptz.vendor_of = fake_xu, (lambda path: "3564")
            _ptz.Ptz._awake = staticmethod(lambda: True)
            _ptz.Ptz._wake = lambda self, wait=4.0: None
            _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0       # found again, as an OBSBOT this time
            f = (c.get("/api/ptz").get_json() or {}).get("follow") or {}
            if not (f.get("can") and f.get("on") is True and f.get("want") is False):
                print(f"  FAIL an OBSBOT that is tracking should say so: {f}")
                FAILED.append("follow found")
            cam_state[_ptz.CID["pan"]] = 0
            _ptz.PTZ.keep_once()
            if xu["mode"] != 0 or xu["sets"][-1:] != ["16020000"]:
                print(f"  FAIL the switch says off, so the camera's own tracking should be switched off: {xu}")
                FAILED.append("follow kept off")
            else:
                print("  ok   the camera started up following; the switch says off, so it was switched off (16 02 00 00)")
            r = c.post("/api/ptz/follow", json={"on": True})
            if not ((r.get_json() or {}).get("follow") or {}).get("on") or xu["mode"] != 2 \
                    or pieapp.service.config["ptz"].get("follow") is not True:
                print(f"  FAIL Follow me on: {r.get_json()} {xu} {pieapp.service.config['ptz']}")
                FAILED.append("follow on")
            else:
                print("  ok   Follow me on → 16 02 02 00, and it's remembered")
            pan0 = cam_state[_ptz.CID["pan"]]
            r = c.post("/api/ptz/drive", json={"vx": 1, "vy": 0, "sid": "f", "seq": 1})
            _t.sleep(0.35)
            if not (r.get_json() or {}).get("following") or cam_state[_ptz.CID["pan"]] != pan0 or not _ptz.PTZ.quiet():
                print(f"  FAIL while following, the stick should rest (and say why): {r.get_json()}")
                FAILED.append("follow stick")
            else:
                print("  ok   while it follows you, the stick rests and the page is told why; the watcher waits")
            r = c.post("/api/llm/chat", json={"message": "look around"})
            reply = (r.get_json() or {}).get("reply") or ""
            if not reply.startswith("Left:") or xu["mode"] != 2 or xu["sets"][-2:] != ["16020000", "16020200"]:
                print(f"  FAIL a look around should stop following for the look and follow again after: {reply!r} {xu['sets'][-3:]}")
                FAILED.append("follow look around")
            else:
                print("  ok   'look around' while following: it stops, looks, and follows again")
            xu["mode"] = 0                                     # a raised palm switched it off on the camera
            _ptz.PTZ.keep_once()
            if xu["mode"] != 2:
                FAILED.append("follow gesture")
                print("  FAIL the switch says on: the camera's gesture should be undone")
            else:
                print("  ok   the camera's own gesture is undone: the switch here decides")
            for ask, want, mode in (("stop following me", "Stopped following you", 0), ("follow me", "Following you", 2)):
                reply = (c.post("/api/llm/chat", json={"message": ask}).get_json() or {}).get("reply") or ""
                okay = reply.startswith(want) and xu["mode"] == mode
                print(f"  {'ok  ' if okay else 'FAIL'} '{ask}' → {reply!r}")
                if not okay:
                    FAILED.append(f"follow: {ask}")
            r = c.post("/api/ptz/set", json={"center": True})
            if not (r.get_json() or {}).get("stopped_following") or xu["mode"] != 0 \
                    or pieapp.service.config["ptz"].get("follow") is not False:
                print(f"  FAIL Center while following should stop the following (and keep it off): {r.get_json()}")
                FAILED.append("follow center")
            else:
                print("  ok   Center while following stops it — you pointed it somewhere — and it stays off")
            tg_on, tg_off = pieapp._tg_command("/follow"), pieapp._tg_command("/follow off")
            if tg_on != "Following you." or tg_off != "Stopped following you." or xu["mode"] != 0:
                print(f"  FAIL /follow and /follow off: {tg_on!r} {tg_off!r}")
                FAILED.append("telegram /follow")
            else:
                print("  ok   /follow and /follow off from Telegram")
            # asleep, lens down: a move wakes it (OBSBOT's own "run") and then goes where it was sent
            xu["asleep"], xu["frames"] = True, []
            _ptz.PTZ._asked_at = 0.0
            c.post("/api/ptz/set", json={"pan": 20, "tilt": 5})
            _ptz.PTZ.wait_awake(10)
            woke = xu["frames"][0] if xu["frames"] else b""
            s = (c.get("/api/ptz").get_json() or {}).get("sleep") or {}
            if xu["asleep"] or woke[:2] != b"\xaa\x25" or woke[10:12] != b"\xc2\xa0" or woke[16:20] != b"\0\0\0\0" \
                    or cam_state[_ptz.CID["pan"]] != 20 * 3600 or not s.get("can") or s.get("asleep") or s.get("wakes", 0) < 1:
                print(f"  FAIL a move should wake a sleeping OBSBOT and then land: {xu['asleep']} {woke[:20].hex()} "
                      f"{cam_state} {s}")
                FAILED.append("ptz wake")
            else:
                print("  ok   asleep with its lens down: the move woke it (AA 25 … C2 A0 · 00 00 00 00) and it turned as asked")
            xu["asleep"], xu["frames"] = True, []
            check("PUT /api/settings ptz wake off", c.put("/api/settings", json={"ptz": {"wake": False}}))
            _ptz.PTZ._asked_at = 0.0
            c.post("/api/ptz/set", json={"pan": 10})
            _t.sleep(0.3)
            if xu["frames"]:
                print("  FAIL with waking off it should be left asleep")
                FAILED.append("ptz wake off")
            else:
                print("  ok   with 'Wake it when it's used' off, it's left asleep")
            c.put("/api/settings", json={"ptz": {"wake": True}})
            xu["asleep"] = False

            # turning by itself, an OBSBOT turns at a speed (its own command): one smooth turn, not steps
            import struct as _struct
            from pie_ink import camera as _cam2
            f = _ptz.v3_frame(1, _ptz.CMD_GIM_SPEED, _ptz.TO_GIMBAL, _struct.pack("<fff", 10, 20, 30))
            hdr, seg = bytearray(f[:12]), bytearray(f[12:28])
            hdr[6:8], seg[2:4] = b"\0\0", b"\0\0"
            if (_ptz.crc16_usb(b"123456789") != 0xB4C8 or f[:2] != b"\xaa\x25" or f[8:14] != b"\x0a\x04\x84\x64\x0c\x00"
                    or _struct.unpack_from("<fff", f, 16) != (10.0, 20.0, 30.0)
                    or int.from_bytes(f[6:8], "little") != _ptz.crc16_usb(bytes(hdr))
                    or int.from_bytes(f[14:16], "little") != _ptz.crc16_usb(bytes(seg))):
                print(f"  FAIL the speed frame: {f[:28].hex()}")
                FAILED.append("ptz speed frame")
            else:
                print("  ok   the speed frame: AA 25 … 0A 04 84 64, roll/pitch/yaw as float32, CRC-16/USB (B4C8 check) right")
            timed, touch0, awake0, wait0 = [], _cam2.CAMERA.touch, _ptz.Ptz.__dict__["_awake"], _ptz.STREAM_WAIT
            _ptz.xu_query = lambda fd, unit, sel, q, data=b"": (
                timed.append((_t.time(), bytes(data))) if (sel == _ptz.XU_COMMAND and q == _ptz.UVC_SET_CUR) else None,
                fake_xu(fd, unit, sel, q, data))[1]
            writes, real_ext3 = [], _ptz.s_ext_ctrls
            _ptz.s_ext_ctrls = lambda fd, pairs: (writes.append((_t.time(), dict(pairs))), real_ext3(fd, pairs))
            _cam2.CAMERA.touch = lambda seconds=20: None
            speeds = lambda: [(t, _struct.unpack_from("<fff", d, 16)) for t, d in timed if d[10:12] == b"\x84\x64"]  # noqa: E731
            try:
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                writes.clear()
                _ptz.PTZ.glide(pan=30, tilt=10, speed=40)
                spd = speeds()
                yaw = sum(-a[1][2] * (b[0] - a[0]) for a, b in zip(spd, spd[1:]))       # the speeds added up
                pitch = sum(-a[1][1] * (b[0] - a[0]) for a, b in zip(spd, spd[1:]))
                peak = max([-s[2] for _, s in spd] or [0])
                ends = [(w[pan_id] / 3600, w[tilt_id] / 3600) for _, w in writes
                        for pan_id, tilt_id in [(_ptz.CID["pan"], _ptz.CID["tilt"])] if pan_id in w]
                okay = (len(spd) >= 10 and all(s[2] <= 0 and s[1] <= 0 for _, s in spd)
                        and abs(yaw - 30) < 2.5 and abs(pitch - 10) < 1.5 and 40 < peak < 80 and -spd[0][1][2] < peak / 2
                        and [s for _, s in spd[-2:]] == [(0.0, 0.0, 0.0)] * 2
                        and ends == [(30.0, 10.0)] * 2 and writes[0][0] >= spd[-1][0])
                print(f"  {'ok  ' if okay else 'FAIL'} a glide on an OBSBOT is one turn at a speed: {len(spd)} speeds rising to "
                      f"{peak:.0f}°/s and falling, adding up to {yaw:.1f}° across and {pitch:.1f}° up, then a stop (twice), "
                      f"then the exact place (twice): {ends}")
                if not okay:
                    FAILED.append("ptz smooth glide")
                # a nod on an OBSBOT: one continuous to-and-fro at a speed — down, up, down, up — then back where it was
                _ptz.PTZ.set(pan=0, tilt=10)
                _ptz.PTZ.move_until = 0
                timed.clear()
                writes.clear()
                t_nod = _t.time()
                nodded = _ptz.PTZ.gesture("nod", tempo=2.0)
                took = _t.time() - t_nod
                spd = speeds()
                tilts = [-s[1] for _, s in spd if s != (0.0, 0.0, 0.0)]          # tilt speeds, the way the value rises
                swings = sum(1 for a, b in zip(tilts, tilts[1:]) if a * b < 0)   # changes of direction
                net = sum(-a[1][1] * (b[0] - a[0]) for a, b in zip(spd, spd[1:]))
                ends = [(w[_ptz.CID["pan"]] / 3600, w[_ptz.CID["tilt"]] / 3600) for _, w in writes if _ptz.CID["pan"] in w]
                okay = (nodded and len(tilts) >= 12 and swings >= 3 and tilts[0] < 0 and 40 < max(abs(t) for t in tilts) <= _ptz.GESTURE_PEAK + 1
                        and all(s[2] == 0 for _, s in spd) and abs(net) < 3 and [s for _, s in spd[-2:]] == [(0.0, 0.0, 0.0)] * 2
                        and ends == [(0.0, 10.0)] * 2 and took < 3.0 and _ptz.PTZ.gesturing is None)
                print(f"  {'ok  ' if okay else 'FAIL'} a nod on an OBSBOT is one to-and-fro at a speed: {len(tilts)} speeds, down first, "
                      f"{swings} turns of direction, up to {max([abs(t) for t in tilts] or [0]):.0f}°/s, net {net:.1f}°, then a stop and the "
                      f"exact place: {ends} ({took:.1f} s)")
                if not okay:
                    FAILED.append("ptz smooth nod")
                # another move in the middle stops it where it's got to: the stop first, then that move
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                writes.clear()
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": -40, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.5)
                got = _ptz.PTZ.set(pan=15)
                th.join(5)
                spd = speeds()
                first_stop = next((t for t, s in spd if s == (0.0, 0.0, 0.0)), None)
                later = [s for t, s in spd if writes and t > writes[0][0] and s != (0.0, 0.0, 0.0)]
                okay = (first_stop is not None and len(writes) == 1 and writes[0][0] >= first_stop and not later
                        and cam_state[_ptz.CID["pan"]] == 15 * 3600 and cam_state[_ptz.CID["tilt"]] == 0
                        and got["pan"] == 15 and not th.is_alive())
                print(f"  {'ok  ' if okay else 'FAIL'} Center (or any move) mid-turn: a stop goes first, then the move; "
                      f"the turn doesn't carry on after it")
                if not okay:
                    FAILED.append("ptz smooth interrupted")
                # not streaming, it wouldn't take speeds: one move instead
                _ptz.Ptz._awake, _ptz.STREAM_WAIT = staticmethod(lambda: False), 0.2
                timed.clear()
                writes.clear()
                _ptz.PTZ.glide(pan=40, speed=40)
                okay = not speeds() and len(writes) == 1 and cam_state[_ptz.CID["pan"]] == 40 * 3600
                _ptz.Ptz._awake = awake0
                # "smooth: false" puts the small steps back
                _ptz.PTZ.conf["smooth"] = False
                writes.clear()
                _ptz.PTZ.glide(pan=0, speed=80)
                _ptz.PTZ.conf.pop("smooth", None)
                okay = okay and not speeds() and len(writes) >= 4 and cam_state[_ptz.CID["pan"]] == 0
                print(f"  {'ok  ' if okay else 'FAIL'} not streaming, it goes in one move; with smooth: false, in steps as before")
                if not okay:
                    FAILED.append("ptz smooth fallback")
                # the gimbal must always get its stop: found afresh (a restart mid-turn), and after a turn whose
                # stop the link wouldn't take — owed, and sent by the keeper the moment the link is back
                stop_frames = lambda: [d for _, d in timed if d[10:12] == b"\x84\x64" and d[16:28] == bytes(12)]   # noqa: E731
                timed.clear()
                _ptz.PTZ.fd, _ptz.PTZ._looked_for = None, 0.0
                _ptz.PTZ.available()
                found_stop = len(stop_frames())
                broken = {"on": False}
                inner = _ptz.xu_query

                def flaky_xu(fd, unit, sel, q, data=b""):
                    if broken["on"] and sel == _ptz.XU_COMMAND and q == _ptz.UVC_SET_CUR:
                        raise OSError(_errno.EPIPE, "Broken pipe")
                    return inner(fd, unit, sel, q, data)
                _ptz.xu_query = flaky_xu
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                timed.clear()
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": 60, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.4)
                broken["on"] = True                              # the link goes bad mid-turn: no speed, no stop gets through
                th.join(6)
                owed_after = _ptz.PTZ.stop_owed
                n_before = len(stop_frames())
                broken["on"] = False
                _ptz.PTZ.keep_once()                             # the keeper's next round: the stop is sent
                okay = (found_stop >= 1 and not th.is_alive() and owed_after and not _ptz.PTZ.stop_owed
                        and len(stop_frames()) > n_before and _ptz.PTZ.status()["stop_owed"] is False)
                print(f"  {'ok  ' if okay else 'FAIL'} the gimbal always gets its stop: one when it's found ({found_stop}), and one owed "
                      f"from a turn the link broke during, sent by the keeper once the link is back")
                if not okay:
                    print(f"       found_stop {found_stop} alive {th.is_alive()} owed {owed_after} now {_ptz.PTZ.stop_owed} frames {n_before}→{len(stop_frames())}")
                    FAILED.append("ptz stop owed")
                _ptz.xu_query = inner
                # the service stopping mid-turn: the turn is stopped first
                _ptz.PTZ.set(pan=0, tilt=0)
                _ptz.PTZ.move_until = 0
                th = _th.Thread(target=_ptz.PTZ.glide, kwargs={"pan": -60, "speed": 40}, daemon=True)
                th.start()
                _t.sleep(0.4)
                timed.clear()
                _ptz.PTZ.shutdown()
                th.join(5)
                okay = bool(stop_frames()) and not th.is_alive() and not _ptz.PTZ._speeding
                print(f"  {'ok  ' if okay else 'FAIL'} the service stopping mid-turn stops the turn first")
                if not okay:
                    FAILED.append("ptz shutdown stop")
                _ptz.PTZ._stop.clear()
                _ptz.PTZ.start()
            finally:
                _ptz.Ptz._awake, _ptz.STREAM_WAIT = awake0, wait0
                _ptz.s_ext_ctrls, _cam2.CAMERA.touch = real_ext3, touch0
                _ptz.xu_query = fake_xu
        finally:
            (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
             _ptz.xu_query, _ptz.vendor_of, _ptz.Ptz._awake, _ptz.Ptz._wake) = real
            _ptz.PTZ.fd, _ptz.PTZ.ctrls, _ptz.PTZ._looked_for, _ptz.PTZ.device = None, {}, _t.time(), None
            _ptz.PTZ.xu_unit, _ptz.PTZ.follow_on, _ptz.PTZ.asleep = None, None, None
            _ptz.SWEEP_HOLD, _ptz.RISE = saved_hold, saved_rise
            ollama2.stop()
        r = c.post("/api/llm/chat", json={"message": "look left"})
        if "no camera here that turns" not in ((r.get_json() or {}).get("reply") or ""):
            print(f"  FAIL with no camera that turns, 'look left' should say so: {r.get_json()}")
            FAILED.append("ptz none")
        else:
            print("  ok   with no camera that turns, 'look left' says so instead of pretending")
        check("GET /api/ptz (none)", c.get("/api/ptz"), body_has=["available"])

        print("the little friend (a pretend room, a pretend OBSBOT)")
        import random as _rnd
        from PIL import ImageDraw as _Draw
        from pie_ink import buddy as _buddy, camera as _cam
        room = _Img.new("L", (3600, 1200), 128)
        rd, rr = _Draw.Draw(room), _rnd.Random(7)
        for _ in range(700):
            x, y = rr.randrange(0, 3600), rr.randrange(0, 1200)
            rd.rectangle([x, y, x + rr.randrange(20, 150), y + rr.randrange(20, 150)], fill=rr.randrange(20, 235))
        where = {"person": None}
        pos = {_ptz.CID["pan"]: 0, _ptz.CID["tilt"]: 0, _ptz.CID["zoom"]: 0}

        def room_view(pan, tilt):
            cx, cy = (pan + 180) * 10, (60 - tilt) * 10
            img = room.crop((cx - 350, cy - 210, cx + 350, cy + 210)).convert("RGB")
            p = where["person"]
            if p and abs(p[0] - pan) < 30 and abs(p[1] - tilt) < 18:
                x, y = (p[0] - pan + 35) * 10, (tilt - p[1] + 21) * 10
                _Draw.Draw(img).ellipse([x - 45, y - 45, x + 45, y + 45], fill=(10, 10, 10))
                img.info["person"] = (x / 700, y / 420)
            return img

        def here():
            return pos[_ptz.CID["pan"]] / 3600, pos[_ptz.CID["tilt"]] / 3600

        def fake_detect(self, frame, cats=True):
            self.took = 0.01
            p = frame.info.get("person")
            return ([(p[0] - 0.07, p[1] - 0.1, 0.14, 0.2)] if p else []), False
        saved_fns = (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
                     _cam.CAMERA.status, _cam.CAMERA.frame, _cam.CAMERA.touch, _buddy.Buddy._detect)
        _ptz.queryctrl = fake_query
        _ptz.g_ctrl = lambda fd, cid: pos[cid]
        _ptz.s_ext_ctrls = lambda fd, pairs: [pos.__setitem__(k, v) for k, v in pairs]
        _ptz.Ptz._candidates = lambda self: ["/dev/null"]
        _ptz.Ptz.settle = lambda self, timeout=3.5: room_view(*here())
        _cam.CAMERA.status = lambda: {"running": True, "age": 0.1, "id": "usb:/dev/video0"}
        _cam.CAMERA.frame = lambda: room_view(*here())
        _cam.CAMERA.touch = lambda seconds=20: None
        _buddy.Buddy._detect = fake_detect
        _ptz.PTZ.fd, _ptz.PTZ._looked_for, _ptz.PTZ.touched_at, _ptz.PTZ.follow_on = None, 0.0, 0.0, None
        B = _buddy.BUDDY
        try:
            r = c.post("/api/buddy", json={"on": True})
            if not (r.get_json() or {}).get("enabled") or not pieapp.service.config["buddy"]["enabled"] or not B.running():
                print(f"  FAIL the Camera tab's switch should turn it on, and remember: {r.get_json()}")
                FAILED.append("buddy on")
            else:
                print("  ok   switched on from the Camera tab: it starts, and the setting is kept")
            B.stop()                                   # from here it's driven by hand, a glance at a time
            for _ in range(50):
                if not B.running():
                    break
                _t.sleep(0.1)
            B.conf = dict(pieapp.service.config["buddy"], night=False, every_seconds=20, rest=600)
            B.state, B.why = "looking", ""

            def glance(n=1, move=False):
                for _ in range(n):
                    if move:
                        B.next_move = 0
                    B.tick()
            glance(24, move=True)
            seen_spots = {tuple(k) for k in B.spots}
            low = min(t for _, t in seen_spots)
            home_pan = float((pieapp.service.config["ptz"].get("home") or {}).get("pan", 0) or 0)
            if len(seen_spots) < 6 or low < -12 or max(abs(p - home_pan) for p, _ in seen_spots) > 70.5:
                print(f"  FAIL it should look around home, 70° each way, no lower than -12°: {sorted(seen_spots)}")
                FAILED.append("buddy wander")
            else:
                print(f"  ok   it looked around by itself: {len(seen_spots)} spots around home ({home_pan:.0f}°), "
                      f"lowest {low}°, nowhere near the floor")
            B.next_move, t_before, p_before = 0, _t.time(), here()
            B.tick()
            if here() == p_before or B.next_move - t_before < _buddy.HOLD:
                print(f"  FAIL at a new spot it should stay a while: moved {here() != p_before}, "
                      f"next move in {B.next_move - t_before:.1f}s")
                FAILED.append("buddy stays")
            else:
                print(f"  ok   at each spot it stays a while before the next ({B.next_move - t_before:.0f}s this time)")
            # its motors get a rest: past the turning allowed in ten minutes, it holds still and says so
            B.conf["rest"] = 30
            _ptz.PTZ.turns.append((_t.time() - 40, _t.time() - 5))          # 35 s of turning just now
            B.next_move, p_before = 0, here()
            B.tick()
            resting = here() == p_before and B.why.startswith("resting its motors")
            st = (c.get("/api/buddy").get_json() or {})
            print(f"  {'ok  ' if resting else 'FAIL'} past its turning allowance it holds still: {B.why!r}")
            if not resting or not (st.get("why") or "").startswith("resting"):
                FAILED.append("buddy rest")
            _ptz.PTZ.turns.clear()
            B.conf["rest"] = 600
            r = c.post("/api/ptz/set", json={"pan": 0, "tilt": -80})
            got = ((r.get_json() or {}).get("position") or {}).get("tilt")
            if got != -30:
                print(f"  FAIL nothing should take it lower than 'Lowest it looks' (-30°): {got}")
                FAILED.append("ptz floor")
            else:
                print("  ok   asked for 80° down, it stops at -30° (an OBSBOT pointed down goes to sleep)")
            _ptz.PTZ.touched_at = 0
            _ptz.PTZ.set(pan=0, tilt=0)
            _ptz.PTZ.move_until = 0                    # it's arrived (a real one takes a second)
            B.next_move, B.prev = _t.time() + 999, None
            pieapp.service.set_mode("clock")
            for _ in range(30):
                if pieapp.service.mode_name == "clock":
                    break
                _t.sleep(0.1)
            for i in range(6):
                where["person"] = (-20 + (i % 2) * 0.6, 3)          # people fidget; posters don't
                glance()
            last = (pieapp.chat.history(1) or [{}])[-1]
            said = [last.get("text")] if last.get("text") == B.line and B.line else []
            st = (c.get("/api/buddy").get_json() or {})
            ev = pieapp.sight.events(limit=3)
            for _ in range(30):
                if pieapp.service.mode_name == "buddy":
                    break
                _t.sleep(0.1)
            okay = (len(said) == 1 and st.get("mood") == "happy" and ev and ev[0]["text"] == "Person detected"
                    and ev[0].get("source") == "friend" and pieapp.service.mode_name == "buddy" and here()[0] < 0)
            print(f"  {'ok  ' if okay else 'FAIL'} someone walked in: it said {said!r}, face {st.get('face')} ({st.get('mood')}), "
                  f"'{ev[0]['text'] if ev else '-'}' in the Seen list, its face on the screen, and it turned to them")
            if not okay:
                FAILED.append("buddy hello")
            pieapp._buddy_face["until"] = _t.time() - 1
            pieapp._buddy_popdown()
            for _ in range(30):
                if pieapp.service.mode_name != "buddy":
                    break
                _t.sleep(0.1)
            if pieapp.service.mode_name == "buddy":
                print("  FAIL after a minute the screen should go back to what it showed")
                FAILED.append("buddy popdown")
            else:
                print(f"  ok   a minute later the screen goes back ({pieapp.service.mode_name})")
            tg = pieapp._tg_command("/friend")
            if st.get("face", "?") not in tg:
                print(f"  FAIL /friend should say how it is: {tg!r}")
                FAILED.append("telegram /friend")
            else:
                print(f"  ok   /friend → {tg!r}")
            where["person"] = None
            _ptz.PTZ.user_moved()
            p0 = here()
            glance(3, move=True)
            if here() != p0 or B.why != "you're steering":
                print(f"  FAIL you steered: it should leave the camera be: {here()} vs {p0}, {B.why!r}")
                FAILED.append("buddy steering")
            else:
                print("  ok   you pointed the camera: it leaves it be for a while")
            r = c.post("/api/buddy", json={"on": False})
            if (r.get_json() or {}).get("enabled") or pieapp.service.config["buddy"]["enabled"]:
                FAILED.append("buddy off")
        finally:
            B.stop()
            (_ptz.queryctrl, _ptz.g_ctrl, _ptz.s_ext_ctrls, _ptz.Ptz._candidates, _ptz.Ptz.settle,
             _cam.CAMERA.status, _cam.CAMERA.frame, _cam.CAMERA.touch, _buddy.Buddy._detect) = saved_fns
            _ptz.PTZ.fd, _ptz.PTZ.ctrls, _ptz.PTZ._looked_for, _ptz.PTZ.device = None, {}, _t.time(), None
            pieapp.service.set_mode("clock")

        print("photos (from the test pattern)")
        from pie_ink import photos as _photos
        had = set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()
        try:
            # the colour filters are CSS's own matrices: sepia(1) turns white to rgb(255, 255, 239), as a browser does
            white, red = _Img.new("RGB", (4, 4), (255, 255, 255)), _Img.new("RGB", (4, 4), (255, 0, 0))
            got = (_photos.apply(white, "sepia").getpixel((0, 0)), _photos._colour(white, [["sepia", 1]]).getpixel((0, 0)),
                   _photos._colour(red, [["grayscale", 1]]).getpixel((0, 0)))
            if got[1:] != ((255, 255, 239), (54, 54, 54)):
                print(f"  FAIL the colour filters should match CSS's: {got}")
                FAILED.append("photo css maths")
            else:
                print("  ok   the colour filters are CSS's own: the live view shows them as the photo comes out")
            scene = _Img.new("RGB", (640, 360), (90, 120, 150))
            _Draw.Draw(scene).ellipse([200, 80, 420, 300], fill=(230, 200, 60))
            odd = [f["id"] for f in _photos.FILTERS if abs(_photos.apply(scene, f["id"]).width - 640) > 12]
            if odd:
                FAILED.append("photo filters")
                print(f"  FAIL every filter should keep the picture's size: {odd}")
            r = c.get("/api/photos")
            check("GET /api/photos", r, body_has=["photos", "filters", "count"])
            ids = [f["id"] for f in (r.get_json() or {}).get("filters", [])]
            if ids[:2] != ["none", "bw"] or "trace" not in ids or "faded" in ids:
                print(f"  FAIL the filters: {ids}")
                FAILED.append("photo filter list")
            traced = _photos.apply(scene, "trace")
            if traced.getpixel((20, 20))[0] > 30 or traced.convert("L").getextrema()[1] < 200:
                print("  FAIL Edge trace should be bright outlines on black")
                FAILED.append("photo trace")
            else:
                print("  ok   Edge trace: the outlines, bright on black")
            r = c.post("/api/photos", json={"filter": "sepia", "rot": 90})
            check("POST /api/photos (sepia, the view turned)", r, body_has=["photo"])
            p = (r.get_json() or {}).get("photo") or {}
            if not (p.get("name", "").endswith("-sepia.jpg") and p.get("h", 0) > p.get("w", 0) > 0):
                print(f"  FAIL a sepia photo, turned like the view (taller than wide): {p}")
                FAILED.append("photo take")
            else:
                print(f"  ok   took {p['name']} ({p['w']}x{p['h']}, turned like the view)")
            r = c.get(p.get("url", "/api/photos/x.jpg"))
            if r.status_code != 200 or r.data[:3] != b"\xff\xd8\xff":
                FAILED.append("photo get")
            r = c.get(p.get("url", "/api/photos/x.jpg") + "?download=1")
            if "attachment" not in r.headers.get("Content-Disposition", ""):
                print(f"  FAIL download should come as a file: {r.headers.get('Content-Disposition')}")
                FAILED.append("photo download")
            r = c.get(p.get("thumb", "/api/photos/x.jpg/thumb"))
            if r.status_code != 200 or max(_Img.open(__import__("io").BytesIO(r.data)).size) > _photos.THUMB:
                FAILED.append("photo thumb")
            check("GET /api/photos/<none> (404)", c.get("/api/photos/..%2Fconfig.yaml"), want=(404,))
            # a drawn filter comes drawn from the Pi, in the live view's stream
            r = c.get("/api/camera/stream?filter=comic", buffered=False)
            first = next(iter(r.response), b"")
            r.close()
            if b"\xff\xd8" not in first:
                print(f"  FAIL the comic view should stream JPEGs: {first[:60]!r}")
                FAILED.append("photo stream")
            else:
                print("  ok   a drawn filter (comic) streams from the Pi for the live view")
            # in words, and from Telegram (a pretend camera, so it isn't only the test pattern)
            real_available = pieapp.camera.available
            pieapp.camera.available = lambda refresh=False: [{"id": "usb:/dev/video9", "label": "Pretend"},
                                                             {"id": "mock", "label": "Test pattern"}]
            try:
                reply = (c.post("/api/llm/chat", json={"message": "take a black and white photo"}).get_json() or {}).get("reply") or ""
                tg = pieapp._tg_command("/photo pixel")
                last = pieapp._tg_command("/photos")
                huh = pieapp._tg_command("/photo nonsense")
            finally:
                pieapp.camera.available = real_available
            okay = (reply.startswith("Took a black-and-white picture") and isinstance(tg, tuple) and tg[0][:3] == b"\xff\xd8\xff"
                    and "pixel-art" in tg[1] and isinstance(last, tuple) and str(huh).startswith("Which filter?"))
            print(f"  {'ok  ' if okay else 'FAIL'} 'take a black and white photo' → {reply!r}; /photo pixel → a picture; "
                  f"/photos → the latest")
            if not okay:
                print(f"       {str(tg)[:80]} {str(last)[:60]} {huh!r}")
                FAILED.append("photo words")
            for words in ("take a shot", "take a picture of the weather", "picture this", "take me home"):
                if _photos.parse(words) is not None:
                    FAILED.append("photo not greedy")
                    print(f"  FAIL '{words}' isn't a photo")
            r = c.post(f"/api/photos/{p.get('name', 'x.jpg')}/show")
            for _ in range(30):
                if pieapp.service.mode_name == "postcard":
                    break
                _t.sleep(0.1)
            if r.status_code != 200 or pieapp.service.mode_name != "postcard":
                FAILED.append("photo show")
                print(f"  FAIL a photo on the screen: {r.get_json()} {pieapp.service.mode_name}")
            else:
                print("  ok   a photo on the screen (as a postcard)")
            n = (c.get("/api/photos").get_json() or {}).get("count", 0)
            r = c.delete(p.get("url", "/api/photos/x.jpg"))
            if r.status_code != 200 or (r.get_json() or {}).get("count") != n - 1 or _photos.path_of(p.get("name")):
                FAILED.append("photo delete")
            else:
                print("  ok   deleted")
            # the camera paused (Settings → Sound): nothing starts it, a photo says why, the friend waits, and it comes back
            from pie_ink import buddy as _buddy2
            paused = (c.post("/api/camera/pause", json={"seconds": 60}).get_json() or {}).get("paused_for", 0)
            pieapp.CAMERA.touch(20)
            st = c.get("/api/camera/status").get_json() or {}
            took = c.post("/api/photos", json={"filter": "none"})
            friend = _buddy2.Buddy()
            friend.conf = {"enabled": True, "night": False}
            friend.tick()
            back = (c.post("/api/camera/pause", json={"seconds": 0}).get_json() or {}).get("paused_for")
            pieapp.CAMERA.touch(20)
            for _ in range(50):
                if pieapp.CAMERA.status()["running"]:
                    break
                _t.sleep(0.1)
            okay = (paused >= 59 and not st.get("running") and st.get("paused_for", 0) > 0 and took.status_code == 400
                    and "paused" in ((took.get_json() or {}).get("error") or "") and friend.why == "the camera's paused"
                    and back == 0 and pieapp.CAMERA.status()["running"])
            print(f"  {'ok  ' if okay else 'FAIL'} the camera paused for plugging a speaker in: it stays off, a photo says why, "
                  f"the friend waits; resumed, it's back")
            if not okay:
                print(f"       {paused} {st} {took.get_json()} {friend.why!r} {back}")
                FAILED.append("camera pause")
        finally:
            for name in (set(os.listdir(_photos.DIR)) if os.path.isdir(_photos.DIR) else set()) - had:
                _photos.delete(name)
            pieapp.service.set_mode("clock")

        print("a camera with two lenses, side by side")
        two_lens_check(pieapp, c)

        print("a Whisplay HAT, used by itself")
        whisplay_auto_check(pieapp, c)

        print("in a RetroFlag GPi Case 2W: its screen, its buttons, setup.sh")
        gpi_case_check(pieapp, c)

        print("setup.sh: the one question, the screen")
        setup_screen_check()

        print("a Waveshare LCD by itself, as the screen")
        lcd_main_check(pieapp, c)

        rooms_check(pieapp, c)

        print("config.yaml written from outside (a hand edit, a script) while it runs")
        outside_config_check(pieapp, c)

        print("something plugged into a USB port, or pulled out; what's on a USB drive")
        usb_check(pieapp, c)

        print("hand gestures through the camera: the TV and the lights")
        gesture_check(pieapp, c)

        pieapp.service.shutdown()

        print("one screen, or two")
        one_screen_check()
        any_screen_check(pieapp)
        share_footer_check()

        print("the speaker (stand-in Piper and aplay)")
        speaker_check()

        print("a camera picked by node that isn't there any more")
        from pie_ink import camera as _cam0, settings as _cfg0
        migrated = _cfg0._migrate({"camera": {"source": "hdmi", "hdmi_mode": "1080p30", "mirror": True}})["camera"]
        okay = migrated == {"source": "auto", "mirror": True}
        print(f"  {'ok  ' if okay else 'FAIL'} a saved source that no longer exists becomes Auto ({migrated})")
        if not okay:
            FAILED.append("camera source migrate")

        class _Answers:
            name, id, card, format, raw = "Some webcam (video3)", "usb:/dev/video3", "Some webcam", "640x480 MJPG", False

            def read(self):
                return _Img.new("RGB", (480, 360), (90, 120, 150))

            def close(self):
                pass
        usb_was = _cam0._UsbCamera
        _cam0._UsbCamera = lambda device=None, card=None: (_ for _ in ()).throw(RuntimeError(f"{device}: wouldn't open")) if device else _Answers()
        try:
            cam0 = _cam0.Camera()
            src = cam0._open("usb:/dev/video0")
            okay = getattr(src, "id", None) == "usb:/dev/video3" and cam0.error is None
            print(f"  {'ok  ' if okay else 'FAIL'} picked /dev/video0, which gives nothing now: the camera that answers is used instead "
                  f"({getattr(src, 'name', None)})")
            if not okay:
                FAILED.append("camera node fallback")
        finally:
            _cam0._UsbCamera = usb_was

        print("a webcam's formats")
        from pie_ink import camera as _camera
        cases = [({"MJPG": [(640, 480), (1280, 720), (1920, 1080), (3840, 2160)], "YUYV": [(640, 480)]}, ("MJPG", (640, 480))),
                 ({"MJPG": [(1920, 1080), (3840, 2160)], "YUYV": [(320, 240), (640, 480), (1280, 720)]}, ("YUYV", (640, 480))),
                 ({"MJPG": [(1920, 1080)], "YUYV": [(1920, 1080)]}, ("MJPG", (1920, 1080))),
                 ({"MJPG": [(320, 240), (640, 360)]}, ("MJPG", (640, 360))),
                 ({}, ("MJPG", _camera.STREAM_SIZE))]
        for offered, want in cases:
            got = _camera.choose_format(offered)
            okay = got == want
            print(f"  {'ok  ' if okay else 'FAIL'} {offered or 'nothing known'} -> {got}")
            if not okay:
                FAILED.append(f"choose_format {offered}")
        if _camera._fourcc_name(int.from_bytes(b"MJPG", "little")) != "MJPG":
            FAILED.append("fourcc name")

        print("a camera that stops sending (as one did while its gimbal turned)")
        import io as _io
        from PIL import Image as _Img2
        _b = _io.BytesIO()
        _Img2.new("RGB", (64, 48), (10, 120, 200)).save(_b, "JPEG")
        still_jpeg = _b.getvalue()

        class _Stuck:
            name, id, card, raw, format = "Stuck camera (video9)", "usb:/dev/video9", "Stuck camera", True, "64x48 MJPG"

            def __init__(self):
                self.n = 0

            def read(self):
                self.n += 1
                if self.n > 5:                                # sends a few frames, then nothing ever again
                    _t.sleep(0.3)
                    raise RuntimeError("USB camera read failed")
                _t.sleep(0.03)
                return still_jpeg

            def close(self):
                pass

        class _Fine(_Stuck):
            def read(self):
                _t.sleep(0.03)
                return still_jpeg

        stall_was = _camera.STALL_SECONDS
        _camera.STALL_SECONDS = 0.8
        cam = _camera.Camera()
        opened = []
        cam._open = lambda preferred: opened.append("first") or _Stuck()
        cam._open_again = lambda was, card: opened.append(("again", was, card)) or _Fine()
        cam.preferred = "usb:/dev/video9"
        try:
            cam.touch(10)
            seq, _ = cam.wait_jpeg(0, timeout=3)
            deadline = _t.time() + 8
            while _t.time() < deadline and not (cam.restarts and cam.status()["age"] is not None and cam.status()["age"] < 0.3):
                _t.sleep(0.1)
            st = cam.status()
            okay = (cam.restarts == 1 and st["running"] and not st["stalled"] and st["error"] is None
                    and opened[-1] == ("again", "usb:/dev/video9", "Stuck camera"))
            print(f"  {'ok  ' if okay else 'FAIL'} it stopped sending; it was closed and opened again, and the picture "
                  f"carries on (restarts {cam.restarts}, age {st['age'] and round(st['age'], 2)}, error {st['error']!r})")
            if not okay:
                FAILED.append("camera stall reopen")
        finally:
            cam._wanted_until = 0
            _camera.STALL_SECONDS = stall_was

        print("the live view on a slow link (it stays live: skips frames, then sends smaller ones)")
        # the server holds little back for a client that isn't taking it: what it did hold (16 MB) was
        # seconds of camera frames, and the picture on a phone ran that far behind, in slow motion
        mark = pieapp.SERVER_OPTIONS.get("outbuf_high_watermark", 1 << 30)
        try:
            from waitress.adjustments import Adjustments as _Adj
            _Adj(**pieapp._server_options())                    # every option is one this waitress knows
            known = True
        except ImportError:
            known = None
        except Exception as e:
            known = f"{e}"
        okay = mark <= 256 * 1024 and known in (True, None)
        note = "waitress takes every option" if known is True else "no waitress here" if known is None else known
        print(f"  {'ok  ' if okay else 'FAIL'} the server holds back at most {mark // 1024} KB of a response ({note})")
        if not okay:
            FAILED.append("server output buffer")
        _b = _io.BytesIO()
        big_scene = _Img2.radial_gradient("L").resize((1280, 720)).convert("RGB")
        _Draw.Draw(big_scene).rectangle([100, 100, 1100, 600], outline=(255, 80, 80), width=12)
        for i in range(0, 1280, 16):                            # detail, so the JPEG is a real camera's size
            _Draw.Draw(big_scene).line([i, 0, 1280 - i, 720], fill=(i % 255, 200, 80), width=2)
        big_scene.save(_b, "JPEG", quality=95)
        big_jpeg = _b.getvalue()
        cam2 = _camera.Camera()
        cam2._jpeg, cam2._seq = big_jpeg, 7
        seq, small = cam2.small_jpeg(640, 70)
        small_w = _Img2.open(_io.BytesIO(small)).size[0] if small else 0
        okay = seq == 7 and small_w == 640 and len(small) < len(big_jpeg) / 3 and cam2.small_jpeg(640, 70)[1] is small
        print(f"  {'ok  ' if okay else 'FAIL'} a smaller frame: {len(big_jpeg) // 1024} KB at 1280 wide -> "
              f"{len(small or b'') // 1024} KB at {small_w} wide, made once per frame")
        if not okay:
            FAILED.append("camera small frame")

        class _Link:
            """A camera as a viewer sees it, at 12 frames a second."""
            fps = 12.0

            def __init__(self, jpg):
                self.jpg, self.seq = jpg, 100

            def wait_jpeg(self, last_seq, timeout=1.0):
                self.seq += 1
                return self.seq, self.jpg

            def small_jpeg(self, width, quality):
                return self.seq, cam2.small_jpeg(width, quality)[1] if width == 640 else b"\xff\xd8" + b"s" * 12_000

        def feed(v, link, held, n):
            """n frames through the viewer, the socket holding each for `held` seconds."""
            out = []
            for _ in range(n):
                if v.yielded_at:
                    v.yielded_at = _t.time() - held
                got = len(v.next(link))
                out.append((v.level, got))                    # the level after that frame, and its size
            return out

        v, link = _camera.Viewer(), _Link(big_jpeg)
        v.WARM_UP = 0.0                                        # judged at once here, not after a moment
        steps = feed(v, link, 0.005, 20)                       # each frame gone in 5 ms: keeping up
        okay = v.level == -1 and {n for _, n in steps} == {len(big_jpeg)}
        print(f"  {'ok  ' if okay else 'FAIL'} a link that keeps up gets the camera's own frames")
        if not okay:
            FAILED.append("viewer fast link")
        v.sent = []                                            # (time passes: the window empties)
        steps = feed(v, link, 0.3, 12)                         # each frame held 0.3 s: saturated at 12 fps
        levels = [lv for lv, _ in steps]
        got640 = any(lv == 0 and n == len(small) for lv, n in steps)
        got480 = any(lv == 1 and n == 12_002 for lv, n in steps)
        okay = levels[0] == -1 and got640 and got480 and levels[-1] == 1 and v.changes == 2 and levels.index(0) == 1
        print(f"  {'ok  ' if okay else 'FAIL'} a link the socket can't keep up with gets 640 px frames, then 480 px "
              f"(after {levels.index(0) if 0 in levels else '-'} and {levels.index(1) if 1 in levels else '-'} frames)")
        if not okay:
            FAILED.append("viewer slow link")

        def until_own(most=10):
            """Frames gone in 5 ms (keeping up) until the camera's own frames are tried again."""
            for i in range(most):
                if feed(v, link, 0.005, 1)[0][1] == len(big_jpeg):
                    return i + 1
            return -1

        v.since, v.hold, v.sent = 0.0, 0.001, []               # keeping up with the small ones for long enough
        first = until_own()
        feed(v, link, 0.6, 2)                                  # the socket held two of them 0.6 s each: that's the link
        stayed, hold = v.level, v.hold
        v.since = 0.0
        second = until_own()
        feed(v, link, 0.02, 4)                                 # this time they go in 20 ms: they stay
        okay = first == 3 and stayed == 0 and hold == 0.002 and second == 3 and v.level == -1 and v.changes == 5
        print(f"  {'ok  ' if okay else 'FAIL'} after a while the camera's own frames are tried again: held 0.6 s each it goes back "
              f"to small ones (for twice as long), gone in 20 ms they stay (level {v.level}, {v.changes} changes)")
        if not okay:
            FAILED.append("viewer try again")
        v, link = _camera.Viewer(), _Link(b"\xff\xd8" + b"x" * 30_000)    # small frames held long: nothing to gain
        v.WARM_UP = 0.0
        feed(v, link, 0.3, 20)
        okay = v.level == -1 and v.changes == 0
        print(f"  {'ok  ' if okay else 'FAIL'} small frames that the socket holds stay as they are (a smaller one would gain nothing)")
        if not okay:
            FAILED.append("viewer small frames")
        r = c.get("/api/camera/stream", buffered=False)
        first = next(iter(r.response), b"")
        r.close()
        if b"\xff\xd8" not in first or b"Content-Length:" not in first:
            print(f"  FAIL the live view should stream JPEG frames: {first[:60]!r}")
            FAILED.append("camera stream")
        else:
            print("  ok   the live view streams frames through the viewer")

        print("the e-ink HATs on a pretend Pi (the real drivers over a fake bus)")
        panel_check()

    print()
    if FAILED:
        print(f"{len(FAILED)} problem(s): {', '.join(FAILED)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
