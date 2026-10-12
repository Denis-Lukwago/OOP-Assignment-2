#MSCS & MSDS OOP with Python Assignment 2 Advent 2026
#Mini-Project 1: Fetching and Displaying a Weather Forecast with Python Web APIs

import os
LOCATION = "Mpigi"
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "B40077@students.ucu.ac.ug")
FORECAST_DAYS = 7
PICK = 0  # index of the candidate to use when a name is ambiguous
USER_AGENT = f"UniWeatherAssignment/1.0 ({CONTACT_EMAIL})"

###
import json, time, urllib.parse, urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
params = {"name": LOCATION, "count": 5, "language": "en", "format": "json"}
url = GEO_URL + "?" + urllib.parse.urlencode(params)

req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=10) as resp:
    status_u = resp.status
    ctype_u = resp.headers.get("Content-Type")
    raw = resp.read()
elapsed_u = time.perf_counter() - t0
data_urllib = json.loads(raw.decode("utf-8"))
print(status_u, ctype_u, f"{elapsed_u:.3f}s", len(data_urllib.get("results", [])), "results")

###

import requests

t0 = time.perf_counter()
r = requests.get(GEO_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
elapsed_r = time.perf_counter() - t0
r.raise_for_status()
data_requests = r.json()
print("Status:", r.status_code)
print("Content-Type:", r.headers["Content-Type"])
print(f"Wall time: {elapsed_r:.3f}s | requests' own timer: {r.elapsed.total_seconds():.3f}s")
print("Same data from both approaches:", data_requests == data_urllib)

### urllib vs requests

| Aspect | `urllib` | `requests` |
|---|---|---|
| Code length | ~8 lines: build query, `Request`, `urlopen`, `read`, decode, `json.loads` | ~3 lines |
| Query string | manual `urlencode` | `params=` dict, encoded automatically |
| Error handling | raises `HTTPError`/`URLError`; the response body is awkward to read | `raise_for_status()`, `r.ok`, `r.json()`; a clear exception hierarchy |
| Headers | pass via `Request(headers=...)` | `headers=` argument, plus `Session` for defaults |
| Encoding | manual `decode("utf-8")` | automatic (`r.encoding`) |
| Timeouts / retries | `timeout=` only; retries are DIY | `timeout=`; retries via `Session`/adapters or a wrapper |

`urllib` needs no install, but `requests` is shorter and less error-prone.

###
    import json
geocode_sample = {"results": [
    {"name": "Dresden", "latitude": 51.05089, "longitude": 13.73832, "country": "Germany", "admin1": "Saxony", "timezone": "Europe/Berlin"},
    {"name": "Dresden", "latitude": 40.12, "longitude": -82.01, "country": "United States", "admin1": "Ohio", "timezone": "America/New_York"}],
    "generationtime_ms": 0.7}
forecast_sample = {"latitude": 51.05, "longitude": 13.74, "timezone": "Europe/Berlin",
    "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm", "wind_speed_10m_max": "km/h"},
    "daily": {"time": ["2026-10-11", "2026-10-12", "2026-10-13"], "weather_code": [3, 61, 95],
              "temperature_2m_max": [14.2, 12.8, 15.1], "temperature_2m_min": [6.1, 7.4, 8.0],
              "precipitation_sum": [0.0, 4.3, 11.2], "wind_speed_10m_max": [15.3, 22.0, 30.4]}}
met_sample = {"properties": {"timeseries": [
    {"time": "2026-10-11T10:00:00Z", "data": {"instant": {"details": {"air_temperature": 12.0, "wind_speed": 3.0}}, "next_1_hours": {"details": {"precipitation_amount": 0.2}}}},
    {"time": "2026-10-11T14:00:00Z", "data": {"instant": {"details": {"air_temperature": 15.0, "wind_speed": 5.0}}, "next_1_hours": {"details": {"precipitation_amount": 0.0}}}},
    {"time": "2026-10-12T10:00:00Z", "data": {"instant": {"details": {"air_temperature": 10.0, "wind_speed": 4.0}}, "next_6_hours": {"details": {"precipitation_amount": 1.5}}}}]}}
for name, obj in [("geocode_sample", geocode_sample), ("forecast_sample", forecast_sample),
                  ("met_sample", met_sample), ("geocode_empty", {"generationtime_ms": 0.4})]:
    with open(f"data/{name}.json", "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

###
%%writefile src/weather.py
"""Weather helpers: geocoding, forecast retrieval, parsing, plotting (no wrapper libraries)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import matplotlib.pyplot as plt
import pandas as pd
import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
DAILY_VARS = ["weather_code", "temperature_2m_max", "temperature_2m_min",
              "precipitation_sum", "wind_speed_10m_max"]

WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


# ---------- exceptions ----------
class WeatherError(Exception):
    """Base class for all errors raised by this module."""

class InvalidInputError(WeatherError, ValueError):
    """User supplied an invalid city name, coordinate or parameter."""

class GeocodingError(WeatherError):
    """Geocoding returned no usable location."""

class APIRequestError(WeatherError):
    """A web request failed permanently (after retries where appropriate)."""

class ParseError(WeatherError):
    """An API response did not have the expected structure."""


# ---------- HTTP ----------
def make_headers(contact_email: str) -> dict[str, str]:
    """Return headers with an identifying User-Agent (required by MET Norway)."""
    return {"User-Agent": f"UniWeatherAssignment/1.0 ({contact_email})"}


def _reason(resp: Optional[requests.Response]) -> str:
    if resp is None:
        return "no response"
    try:
        return str(resp.json().get("reason", resp.text[:200]))
    except ValueError:
        return resp.text[:200]


def request_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
                 timeout: float = 10.0, max_retries: int = 3, backoff: float = 0.5) -> dict[str, Any]:
    """GET a URL and return parsed JSON.

    Retries connection errors, timeouts, HTTP 429 and 5xx with exponential backoff
    (backoff * 2**attempt), at most ``max_retries`` retries. Other 4xx errors fail at once.
    Raises APIRequestError on final failure.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and status < 500 and status != 429:
                raise APIRequestError(f"HTTP {status} from {url}: {_reason(exc.response)}") from exc
            last_exc = exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
        except ValueError as exc:  # invalid JSON body
            raise APIRequestError(f"Response from {url} was not valid JSON") from exc
        except requests.RequestException as exc:
            raise APIRequestError(f"Request to {url} failed: {exc}") from exc
        if attempt < max_retries:
            time.sleep(backoff * 2 ** attempt)
    raise APIRequestError(
        f"Request to {url} failed after {max_retries + 1} attempts: {last_exc!r}") from last_exc


# ---------- geocoding ----------
@dataclass(frozen=True)
class Location:
    """A geocoded place."""
    name: str
    latitude: float
    longitude: float
    country: str = ""
    admin1: str = ""
    timezone: str = "auto"

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.admin1, self.country) if p)


def parse_geocode(payload: dict[str, Any]) -> list[Location]:
    """Parse a geocoding response. The 'results' key is absent when nothing matches."""
    try:
        return [Location(name=r.get("name", ""), latitude=float(r["latitude"]),
                         longitude=float(r["longitude"]), country=r.get("country", ""),
                         admin1=r.get("admin1", ""), timezone=r.get("timezone", "auto"))
                for r in payload.get("results") or []]
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"Unexpected geocoding structure: {exc!r}") from exc


def geocode(name: str, count: int = 5, contact_email: str = "unknown@example.com",
            **request_kwargs: Any) -> list[Location]:
    """Return candidate locations for a place name (possibly an empty list)."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidInputError("Location name must be a non-empty string")
    payload = request_json(GEOCODE_URL,
                           params={"name": name.strip(), "count": count, "language": "en", "format": "json"},
                           headers=make_headers(contact_email), **request_kwargs)
    return parse_geocode(payload)


def choose_location(candidates: list[Location], name: str = "", choice: int = 0,
                    interactive: bool = False) -> Location:
    """Pick one candidate; list them when ambiguous. Set interactive=True to be asked."""
    if not candidates:
        raise GeocodingError(f"No locations found for {name!r}")
    if len(candidates) > 1:
        print(f"{len(candidates)} candidates for {name!r}:")
        for i, c in enumerate(candidates):
            print(f"  [{i}] {c.label} ({c.latitude:.3f}, {c.longitude:.3f})")
        if interactive:
            choice = int(input("Pick a number: "))
    if not 0 <= choice < len(candidates):
        raise InvalidInputError(f"Choice {choice} out of range 0..{len(candidates) - 1}")
    return candidates[choice]


# ---------- forecast ----------
def validate_coordinates(lat: float, lon: float) -> None:
    """Raise InvalidInputError unless -90<=lat<=90 and -180<=lon<=180."""
    for label, v, lim in (("latitude", lat, 90), ("longitude", lon, 180)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise InvalidInputError(f"{label} must be a number, got {v!r}")
        if not -lim <= v <= lim:
            raise InvalidInputError(f"{label} {v} outside [-{lim}, {lim}]")


def get_forecast(lat: float, lon: float, days: int = 7, timezone: str = "auto",
                 contact_email: str = "unknown@example.com", validate: bool = True,
                 **request_kwargs: Any) -> dict[str, Any]:
    """Fetch a daily forecast from Open-Meteo (JSON dict)."""
    if validate:
        validate_coordinates(lat, lon)
    if not isinstance(days, int) or not 1 <= days <= 16:
        raise InvalidInputError("days must be an integer between 1 and 16")
    params = {"latitude": lat, "longitude": lon, "daily": ",".join(DAILY_VARS),
              "timezone": timezone, "forecast_days": days, "temperature_unit": "celsius",
              "wind_speed_unit": "kmh", "precipitation_unit": "mm"}
    return request_json(FORECAST_URL, params=params, headers=make_headers(contact_email), **request_kwargs)


def decode_weather_code(code: Any) -> str:
    """Translate a WMO weather code into text."""
    try:
        return WMO_CODES.get(int(code), f"Unknown ({code})")
    except (TypeError, ValueError):
        return "Unknown"


def forecast_to_df(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert an Open-Meteo daily payload to a DataFrame with units in column names."""
    try:
        d = payload["daily"]
        return pd.DataFrame({
            "Date": pd.to_datetime(d["time"]),
            "Conditions": [decode_weather_code(c) for c in d["weather_code"]],
            "Max Temp (°C)": d["temperature_2m_max"],
            "Min Temp (°C)": d["temperature_2m_min"],
            "Precipitation (mm)": d["precipitation_sum"],
            "Max Wind (km/h)": d["wind_speed_10m_max"],
        })
    except (KeyError, ValueError, TypeError) as exc:
        raise ParseError(f"Unexpected forecast structure: {exc!r}") from exc


def summarize_forecast(df: pd.DataFrame, place: str) -> str:
    """Return a short text summary of the forecast."""
    if df.empty:
        raise ParseError("Empty forecast")
    hot = df.loc[df["Max Temp (°C)"].idxmax()]
    cold = df.loc[df["Min Temp (°C)"].idxmin()]
    windy = df.loc[df["Max Wind (km/h)"].idxmax()]
    return (f"{len(df)}-day forecast for {place}\n"
            f"- Warmest day: {hot['Date']:%a %d %b} ({hot['Max Temp (°C)']:.1f} °C)\n"
            f"- Coldest night: {cold['Date']:%a %d %b} ({cold['Min Temp (°C)']:.1f} °C)\n"
            f"- Total precipitation: {df['Precipitation (mm)'].sum():.1f} mm\n"
            f"- Windiest day: {windy['Date']:%a %d %b} ({windy['Max Wind (km/h)']:.0f} km/h)\n"
            f"Data: Open-Meteo.com (CC BY 4.0)")


def plot_forecast(df: pd.DataFrame, title: str) -> plt.Figure:
    """Temperature range (lines + band) with daily precipitation bars on a second axis."""
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.fill_between(df["Date"], df["Min Temp (°C)"], df["Max Temp (°C)"], alpha=0.2, color="tab:orange", label="Temp range")
    ax.plot(df["Date"], df["Max Temp (°C)"], "o-", color="tab:red", label="Max temp")
    ax.plot(df["Date"], df["Min Temp (°C)"], "o-", color="tab:blue", label="Min temp")
    ax.set_xlabel("Date")
    ax.set_ylabel("Temperature (°C)")
    ax2 = ax.twinx()
    ax2.bar(df["Date"], df["Precipitation (mm)"], width=0.5, alpha=0.35, color="gray", label="Precipitation")
    ax2.set_ylabel("Precipitation (mm)")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left")
    ax.set_title(title)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


# ---------- second source: MET Norway ----------
def get_forecast_met(lat: float, lon: float, contact_email: str, **request_kwargs: Any) -> dict[str, Any]:
    """Fetch the MET Norway compact forecast (coordinates truncated to 4 decimals as required)."""
    validate_coordinates(lat, lon)
    return request_json(MET_URL, params={"lat": round(lat, 4), "lon": round(lon, 4)},
                        headers=make_headers(contact_email), **request_kwargs)


def met_to_daily_df(payload: dict[str, Any], tz: str = "UTC") -> pd.DataFrame:
    """Aggregate MET Norway hourly/6-hourly data to local-day max/min temp, rain and max wind."""
    try:
        rows = []
        for ts in payload["properties"]["timeseries"]:
            data = ts["data"]
            det = data["instant"]["details"]
            if "next_1_hours" in data:
                rain = data["next_1_hours"]["details"]["precipitation_amount"]
            elif "next_6_hours" in data:
                rain = data["next_6_hours"]["details"]["precipitation_amount"]
            else:
                rain = 0.0
            rows.append({"time": pd.Timestamp(ts["time"]), "t": det["air_temperature"],
                         "wind": det["wind_speed"] * 3.6, "rain": rain})
        df = pd.DataFrame(rows)
        df["Date"] = df["time"].dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"Unexpected MET structure: {exc!r}") from exc
    return (df.groupby("Date").agg(**{"Max Temp (°C)": ("t", "max"), "Min Temp (°C)": ("t", "min"),
                                      "Precipitation (mm)": ("rain", "sum"), "Max Wind (km/h)": ("wind", "max")})
            .reset_index())
####
import importlib, src.weather as W
importlib.reload(W)
from IPython.display import display

candidates = W.geocode(LOCATION, count=5, contact_email=CONTACT_EMAIL)
place = W.choose_location(candidates, LOCATION, choice=PICK)
print("Using:", place.label, place.latitude, place.longitude, place.timezone)

payload = W.get_forecast(place.latitude, place.longitude, days=FORECAST_DAYS,
                         timezone=place.timezone, contact_email=CONTACT_EMAIL)
df = W.forecast_to_df(payload)
print(W.summarize_forecast(df, place.label))
display(df.style.format({"Date": "{:%a %d %b}", "Max Temp (°C)": "{:.1f}", "Min Temp (°C)": "{:.1f}",
                         "Precipitation (mm)": "{:.1f}", "Max Wind (km/h)": "{:.0f}"}).hide(axis="index"))
fig = W.plot_forecast(df, f"{place.label}: temperature range and precipitation (Open-Meteo)")

###
met_payload = W.get_forecast_met(place.latitude, place.longitude, CONTACT_EMAIL)
met_df = W.met_to_daily_df(met_payload, tz=place.timezone if place.timezone != "auto" else "UTC")
cmp = df.merge(met_df, on="Date", suffixes=(" (Open-Meteo)", " (MET Norway)"))
display(cmp[["Date", "Max Temp (°C) (Open-Meteo)", "Max Temp (°C) (MET Norway)",
             "Precipitation (mm) (Open-Meteo)", "Precipitation (mm) (MET Norway)"]])
print("Sources: Open-Meteo.com (CC BY 4.0); MET Norway (CC BY 4.0). Edge days from MET may be partial.")

###
from unittest.mock import patch
import requests

def demo(label, fn):
    try:
        fn()
        print(f"{label}: no error (unexpected)")
    except W.WeatherError as e:
        print(f"{label}: {type(e).__name__}: {e}\n")

demo("Invalid city", lambda: W.choose_location(W.geocode("zzzzqqqxx", contact_email=CONTACT_EMAIL), "zzzzqqqxx"))
demo("Invalid latitude (caught client-side)", lambda: W.get_forecast(123, 10))
demo("Invalid latitude (sent to API, HTTP 400)", lambda: W.get_forecast(123, 10, validate=False, backoff=0.1))
with patch("src.weather.requests.get", side_effect=requests.ConnectionError("simulated outage")):
    demo("No network (simulated)", lambda: W.get_forecast(51.05, 13.74, backoff=0.01))
demo("Tiny timeout", lambda: W.get_forecast(51.05, 13.74, timeout=0.001, max_retries=2, backoff=0.1))

###
%%writefile tests/test_weather.py
import json, sys
from pathlib import Path
from unittest.mock import patch
import pytest, requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import weather as W

def load(name):
    return json.loads(Path(__file__).resolve().parents[1].joinpath("data", name).read_text(encoding="utf-8"))

def test_parse_geocode_results():
    locs = W.parse_geocode(load("geocode_sample.json"))
    assert len(locs) == 2 and locs[0].country == "Germany" and locs[0].label.startswith("Dresden")

def test_parse_geocode_missing_results_key():
    assert W.parse_geocode(load("geocode_empty.json")) == []
    with pytest.raises(W.GeocodingError):
        W.choose_location([], "nowhere")

def test_forecast_to_df_columns_and_values():
    df = W.forecast_to_df(load("forecast_sample.json"))
    assert list(df.columns) == ["Date", "Conditions", "Max Temp (°C)", "Min Temp (°C)", "Precipitation (mm)", "Max Wind (km/h)"]
    assert len(df) == 3 and df["Conditions"].tolist() == ["Overcast", "Slight rain", "Thunderstorm"]

def test_decode_weather_code():
    assert W.decode_weather_code(0) == "Clear sky"
    assert W.decode_weather_code(1234).startswith("Unknown")
    assert W.decode_weather_code(None) == "Unknown"

@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), ("a", 0)])
def test_invalid_coordinates(lat, lon):
    with pytest.raises(W.InvalidInputError):
        W.validate_coordinates(lat, lon)

def test_bad_forecast_structure():
    with pytest.raises(W.ParseError):
        W.forecast_to_df({"nope": 1})

def test_met_daily_aggregation():
    df = W.met_to_daily_df(load("met_sample.json"), tz="UTC")
    first = df.iloc[0]
    assert first["Max Temp (°C)"] == 15.0 and first["Min Temp (°C)"] == 12.0
    assert first["Precipitation (mm)"] == pytest.approx(0.2)
    assert first["Max Wind (km/h)"] == pytest.approx(18.0)

def test_retry_cap_and_custom_exception():
    with patch("src.weather.requests.get", side_effect=requests.ConnectionError("x")) as m:
        with pytest.raises(W.APIRequestError):
            W.request_json("http://x", max_retries=2, backoff=0)
    assert m.call_count == 3

def test_summary_has_attribution():
    df = W.forecast_to_df(load("forecast_sample.json"))
    assert "Open-Meteo" in W.summarize_forecast(df, "Dresden")

#Data flow
"""   config cell (LOCATION, EMAIL)
        │
   geocode(name) ──► Open-Meteo Geocoding API ──► list[Location]
        │                                              │
   choose_location (ambiguity / zero results) ◄────────┘
        │ lat, lon, timezone
   get_forecast ──► request_json (timeout, retry, backoff) ──► Open-Meteo Forecast API
        │ JSON
   forecast_to_df (units, WMO decode) ──► table + summary + chart
        └─ (optional) MET Norway ──► met_to_daily_df ──► cross-check"""

#Licensing and terms
"""Open-Meteo: the free API is for non-commercial useand there are rate limits. A commercial product needs a pain for plan. Geocoding results come from GeoNames which needs attribution as well. MET Norway: CC BY 4.0 and NLOD, an identifying user-agent with contact details is mandatory, coordinates are limited to 4 decimals.Heavy or anonymous clients get blocked. For a real product, discuss attribution in the user interface, SLA and uptime(free APIs have none), caching and vendor lock-in."""

#Reliability issues
"""Cover ambigous names, missing results key, 400 for dad coordinates, timesout, partial first and last days in MET data, timezome mismatches and different precipitation definitions between sources."""

#Privacy
"""The name of the place or coordinates sent to a third party can reveal home or work location, travel plans and IP address. Mention the user-agent email being sent, the provider's logging policies, data minimisation (round coordinates, send city only), caching, and GDPR or Uganda's Data Protection and Privacy Act 2019 for a real product."""