"""Weather for the panel.

Numbers come from Open-Meteo (free, no key, worldwide): geocoding for the
place name in config.yaml, then current conditions, the next day hour by hour
and the week ahead — all of it in the one request, so another Pi of yours
that shows the hours and the week can take it from here (GET /api/shared)
rather than ask again. For places in the US, the National Weather Service forecast page
is scraped with BeautifulSoup for its worded forecast ("Sunny, with a high
near 75"), which no JSON feed offers. Everything is cached with backoff.
"""
import logging
import re

import requests

from . import cache

log = logging.getLogger(__name__)
UA = {"User-Agent": "pie-ink/3.0 (+https://github.com/frogCaller/piE-ink)"}
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 7    # today and the six after
HOURS = 24           # the hours kept, from this one on (a forecast is used for a while after it's fetched)
NWS = "https://forecast.weather.gov/MapClick.php"
TTL = 600            # ten minutes
NWS_TTL = 1800

# WMO weather codes -> (short text, category)
CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "cloud"), 3: ("Overcast", "cloud"),
    45: ("Fog", "fog"), 48: ("Icy fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm"),
}


def describe(code):
    return CODES.get(int(code or 0), ("Unknown", "cloud"))


def compass(degrees):
    if degrees is None:
        return ""
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((float(degrees) + 22.5) // 45) % 8]


def icon_name(code, is_day=True):
    """Which of the bundled icons fits a WMO code."""
    text, cat = describe(code)
    code = int(code or 0)
    if cat == "clear":
        if code == 0:
            return "sunny" if is_day else "moon"
        return "partly_day" if is_day else "partly_night"
    if cat == "cloud":
        if code == 3:
            return "overcast"
        return "cloud" if is_day else "cloud_night"
    if cat == "fog":
        return "fog_day" if is_day else "fog_night"
    if cat == "rain":
        heavy = code in (55, 57, 65, 67, 82)
        return ("heavyrain_day" if is_day else "heavyrain_night") if heavy else ("rain_day" if is_day else "rain_night")
    if cat == "snow":
        return "snow_day" if is_day else "snow_night"
    if cat == "storm":
        return "storm_day" if is_day else "storm_night"
    return "cloud"


def _get(url, params, timeout=12):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def geocode(name, limit=5):
    """Places matching a name: [{name, admin, country, country_code, lat, lon, tz}]."""
    data = _get(GEOCODE, {"name": name, "count": limit, "language": "en", "format": "json"})
    out = []
    for r in data.get("results", []):
        out.append({
            "name": r.get("name", ""), "admin": r.get("admin1", ""), "country": r.get("country", ""),
            "country_code": r.get("country_code", ""), "lat": r.get("latitude"), "lon": r.get("longitude"),
            "tz": r.get("timezone", "auto"),
        })
    return out


def _hours(c, hr):
    """The hours from this one on: [{"time", "temp", "code", "rain", "is_day"}] (rain: the chance, %)."""
    times = hr.get("time") or []
    now_hour = str(c.get("time", ""))[:13]
    start = next((i for i, t in enumerate(times) if str(t)[:13] >= now_hour), 0) if now_hour else 0

    def col(name, i, default=None):
        v = hr.get(name) or []
        return v[i] if i < len(v) else default
    return [{"time": times[i], "temp": col("temperature_2m", i), "code": col("weather_code", i),
             "rain": col("precipitation_probability", i), "is_day": bool(col("is_day", i, 1))}
            for i in range(start, min(start + HOURS, len(times)))]


def fetch(lat, lon, units="F", cached_only=False):
    """Current conditions, the hours ahead ("hours") and the week ("days"), in the chosen units."""
    key = f"weather_{lat:.3f}_{lon:.3f}_{units}"

    def go():
        params = {
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m,is_day,precipitation",
            "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max",
            "forecast_days": FORECAST_DAYS,
        }
        if units == "F":
            params.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
        else:
            params.update(wind_speed_unit="kmh")
        d = _get(FORECAST, params)
        c, dl = d.get("current", {}), d.get("daily", {})
        days = []
        for i in range(len(dl.get("time", []))):
            def col(name, default=None):
                v = dl.get(name) or []
                return v[i] if i < len(v) else default
            days.append({
                "date": dl["time"][i], "code": col("weather_code"),
                "hi": col("temperature_2m_max"), "lo": col("temperature_2m_min"),
                "rain": col("precipitation_probability_max"),
                "sunrise": str(col("sunrise", "") or "")[-5:], "sunset": str(col("sunset", "") or "")[-5:],
            })
        return {
            "temp": c.get("temperature_2m"), "feels": c.get("apparent_temperature"),
            "humidity": c.get("relative_humidity_2m"), "wind": c.get("wind_speed_10m"),
            "wind_dir": c.get("wind_direction_10m"),
            "code": c.get("weather_code"), "is_day": bool(c.get("is_day", 1)), "precip": c.get("precipitation"),
            "units": units, "hours": _hours(c, d.get("hourly") or {}), "days": days, "tz": d.get("timezone"),
        }
    return cache.cached(key, TTL, go, cached_only)


def age(lat, lon, units="F"):
    _, a = cache.read(f"weather_{lat:.3f}_{lon:.3f}_{units}")
    return a


def nws_forecast(lat, lon, cached_only=False):
    """The NWS worded forecast for a US point, scraped from forecast.weather.gov:
    [(period, text), ...] e.g. ("This Afternoon", "Sunny, with a high near 75...")."""
    key = f"nws_{lat:.3f}_{lon:.3f}"

    def go():
        from bs4 import BeautifulSoup
        html = requests.get(NWS, params={"lat": lat, "lon": lon, "unit": 0, "lang": "english"},
                            headers=UA, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for row in soup.select("#detailed-forecast-body .row-forecast"):
            label = row.select_one(".forecast-label")
            text = row.select_one(".forecast-text")
            if label and text:
                out.append([label.get_text(strip=True), re.sub(r"\s+", " ", text.get_text(strip=True))])
        return out[:4] or None
    return cache.cached(key, NWS_TTL, go, cached_only) or []
