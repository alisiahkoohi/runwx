"""National Weather Service API access: points lookup (cached), forecasts, gusts."""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.weather.gov"
CACHE_DIR = Path.home() / ".cache" / "runwx"
POINTS_MAX_AGE = 7 * 24 * 3600  # grid assignments change rarely
KMH_TO_MPH = 0.621371
THUNDER = re.compile(r"thunder|lightning|t-storm|tstorm", re.IGNORECASE)
HEAVY = re.compile(r"heavy", re.IGNORECASE)
# Rain rate while it is raining, mm per hour (American Meteorological Society):
# light below 2.5, moderate 2.5 to 7.6, heavy above.
LIGHT_MM_H, MODERATE_MM_H = 2.5, 7.6


@dataclass
class Hour:
    start: datetime
    end: datetime
    temp_f: int
    pop: int  # chance of precipitation, percent
    wind_mph: int
    wind_dir: str
    gust_mph: int | None
    text: str
    rain_mm_h: float | None = None  # expected amount per hour, averaged over the grid cell

    @property
    def thunder(self) -> bool:
        return bool(THUNDER.search(self.text))

    @property
    def intensity(self) -> str | None:
        """'light', 'moderate' or 'heavy' if it rains; None without amount data.

        The NWS amount already folds in the chance of rain, so dividing by the
        chance gives the rate to expect if it does rain."""
        if HEAVY.search(self.text):
            return "heavy"
        if self.rain_mm_h is None or self.pop == 0:
            return None
        rate = self.rain_mm_h / max(self.pop / 100, 0.1)
        if rate < LIGHT_MM_H:
            return "light"
        return "moderate" if rate < MODERATE_MM_H else "heavy"

    @property
    def light_rain(self) -> bool:
        """True when any rain expected is light and there is no thunder."""
        return self.intensity == "light" and not self.thunder


@dataclass
class Period:
    """One period of the 12-hour daily forecast ("Tonight", "Saturday", ...)."""

    name: str
    start: datetime
    end: datetime
    is_day: bool
    temp_f: int
    pop: int
    text: str
    detail: str

    @property
    def thunder(self) -> bool:
        return bool(THUNDER.search(self.text) or THUNDER.search(self.detail))


class NWS:
    def __init__(self, lat: float, lon: float, user_agent: str):
        self.lat, self.lon = round(lat, 4), round(lon, 4)
        self.user_agent = user_agent
        self.points = self._points()

    # -- HTTP ---------------------------------------------------------------
    def _get(self, url: str, tries: int = 3) -> dict:
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "application/geo+json"}
        )
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as err:
                # NWS returns transient 500/503s fairly often; retry those only.
                if err.code < 500 or attempt == tries - 1:
                    raise SystemExit(f"NWS request failed ({err.code}) for {url}")
            except urllib.error.URLError as err:
                if attempt == tries - 1:
                    raise SystemExit(f"Could not reach NWS: {err.reason}")
            time.sleep(1.5 * (attempt + 1))
        raise AssertionError("unreachable")

    def _points(self) -> dict:
        cache = CACHE_DIR / f"points_{self.lat}_{self.lon}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < POINTS_MAX_AGE:
            return json.loads(cache.read_text())
        props = self._get(f"{API}/points/{self.lat},{self.lon}")["properties"]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(props))
        return props

    # -- Point metadata -----------------------------------------------------
    @property
    def timezone(self) -> str:
        return self.points["timeZone"]

    @property
    def radar_url(self) -> str:
        station = self.points.get("radarStation")
        if not station:
            return "https://radar.weather.gov/"
        return f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"

    # -- Forecasts ----------------------------------------------------------
    def hourly(self) -> list[Hour]:
        periods = self._get(self.points["forecastHourly"])["properties"]["periods"]
        gusts, rain = self._grid()
        return [
            Hour(
                start=(start := datetime.fromisoformat(p["startTime"])),
                end=datetime.fromisoformat(p["endTime"]),
                temp_f=p["temperature"],
                pop=(p.get("probabilityOfPrecipitation") or {}).get("value") or 0,
                wind_mph=_mph(p["windSpeed"]),
                wind_dir=p.get("windDirection") or "",
                gust_mph=None if (g := _lookup(gusts, start)) is None else round(g),
                text=p["shortForecast"],
                rain_mm_h=_lookup(rain, start),
            )
            for p in periods
        ]

    def daily(self) -> list[Period]:
        periods = self._get(self.points["forecast"])["properties"]["periods"]
        return [
            Period(
                name=p["name"],
                start=datetime.fromisoformat(p["startTime"]),
                end=datetime.fromisoformat(p["endTime"]),
                is_day=p["isDaytime"],
                temp_f=p["temperature"],
                pop=(p.get("probabilityOfPrecipitation") or {}).get("value") or 0,
                text=p["shortForecast"],
                detail=p.get("detailedForecast") or "",
            )
            for p in periods
        ]

    def _grid(self):
        """Gusts (mph) and rain amount (mm per hour) from the raw grid data,
        which the hourly forecast leaves out. Both are nice-to-haves, so a
        failed request gives empty lists instead of failing the command."""
        url = self.points.get("forecastGridData")
        if not url:
            return [], []
        try:
            grid = self._get(url)["properties"]
        except SystemExit:
            return [], []
        gusts = [(a, b, v * KMH_TO_MPH) for a, b, v in _intervals(grid, "windGust")]
        rain = [
            (a, b, v / ((b - a).total_seconds() / 3600))
            for a, b, v in _intervals(grid, "quantitativePrecipitation")
        ]
        return gusts, rain


def _intervals(grid: dict, key: str) -> list[tuple[datetime, datetime, float]]:
    out = []
    for v in (grid.get(key) or {}).get("values", []):
        if v["value"] is None:
            continue
        start_s, dur_s = v["validTime"].split("/")
        start = datetime.fromisoformat(start_s)
        out.append((start, start + _duration(dur_s), v["value"]))
    return out


def half_hourly(hours: list[Hour], start: datetime, n: int) -> list[Hour]:
    """`n` half-hour rows from `start` (on :00 or :30). NWS forecasts only hourly,
    so a :30 row averages the hour it falls in with the next one; its forecast
    text and thunder flag come from the hour it falls in."""
    rows = []
    for i in range(n):
        t = start + timedelta(minutes=30 * i)
        k = next((j for j, h in enumerate(hours) if h.start <= t < h.end), None)
        if k is None:
            break
        a = hours[k]
        if t == a.start or k + 1 == len(hours):
            rows.append(Hour(t, t + timedelta(minutes=30), a.temp_f, a.pop, a.wind_mph,
                             a.wind_dir, a.gust_mph, a.text, a.rain_mm_h))
            continue
        b = hours[k + 1]
        mid = lambda x, y: round((x + y) / 2)
        gust = mid(a.gust_mph, b.gust_mph) if a.gust_mph and b.gust_mph else a.gust_mph
        rows.append(Hour(t, t + timedelta(minutes=30), mid(a.temp_f, b.temp_f),
                         mid(a.pop, b.pop), mid(a.wind_mph, b.wind_mph), a.wind_dir,
                         gust, a.text, a.rain_mm_h))
    return rows


def _mph(speed: str) -> int:
    """'15 mph' -> 15; '10 to 15 mph' -> 15 (the upper end)."""
    nums = [int(n) for n in re.findall(r"\d+", speed or "")]
    return max(nums) if nums else 0


def _duration(iso: str) -> timedelta:
    """ISO 8601 duration as used by NWS grid data, e.g. PT1H, P1DT6H."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", iso)
    if not m:
        return timedelta(hours=1)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def _lookup(intervals, when: datetime) -> float | None:
    when = when.astimezone(timezone.utc)
    for start, end, value in intervals:
        if start <= when < end:
            return value
    return None
