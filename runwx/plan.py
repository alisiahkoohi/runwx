"""Scoring: which hour today, and which morning this week, is best for a run.

Every score is a penalty (lower is better) built from plain, readable terms, so
the reason printed for a recommendation can be read straight off the numbers.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .nws import Hour, Period

RAINY = 50  # percent; at or above this an hour counts as rainy
LIGHT_RAIN_WEIGHT = 0.2  # light rain is fine to run in, so its chance counts for a fifth
RUN_MINUTES = 30  # an hour counts as "before sunset" if a run started then ends in daylight
COOL_F = 60  # warmer than this costs a point per degree
CALM_MPH = 8  # sustained wind above this costs half a point per mph
GUSTY_MPH = 15  # gusts above this cost a point per mph
THUNDER_PENALTY = 200
DARK_PENALTY = 40

MORNING_HOURS = (6, 7, 8)  # starting hours of the morning-run window
COOL_MORNING_F = 55


def rain_penalty(pop: int, light: bool) -> float:
    return pop * (LIGHT_RAIN_WEIGHT if light else 1.0)


def hour_penalty(h: Hour, sunset: datetime | None) -> float:
    p = rain_penalty(h.pop, h.light_rain)
    p += THUNDER_PENALTY if h.thunder else 0
    p += max(0, h.temp_f - COOL_F)
    p += 0.5 * max(0, h.wind_mph - CALM_MPH)
    p += max(0, (h.gust_mph or 0) - GUSTY_MPH)
    p += 0 if in_daylight(h, sunset) else DARK_PENALTY
    return p


def in_daylight(h: Hour, sunset: datetime | None) -> bool:
    return sunset is None or h.start + timedelta(minutes=RUN_MINUTES) <= sunset


@dataclass
class Advice:
    hour: Hour | None
    verdict: str  # one line


def best_hour(hours: list[Hour], sunset: datetime | None, deadline_label: str) -> Advice:
    if not hours:
        return Advice(None, f"No forecast hours left before {deadline_label}.")
    if all(h.thunder for h in hours):
        return Advice(None, f"Thunder is forecast every hour before {deadline_label}; skip the outdoor run.")
    if all(h.pop >= RAINY and not h.light_rain for h in hours) and not any(
        h.thunder for h in hours
    ):
        return Advice(hours[0], "Just go now, it won't clear up.")

    best = min(hours, key=lambda h: (hour_penalty(h, sunset), h.start))
    return Advice(best, _reason(best, hours, sunset))


def _reason(best: Hour, hours: list[Hour], sunset: datetime | None) -> str:
    parts = [f"{best.pop}% chance of {best.intensity or 'some'} rain"]
    if best.pop == min(h.pop for h in hours) and len(hours) > 1:
        parts[0] += f" (lowest of the {len(hours)} hours left)"
    elif best.light_rain and best.pop >= RAINY:
        parts[0] += ", but light rain is fine"
    parts.append("thunder possible" if best.thunder else "no thunder")
    parts.append(f"{best.temp_f}°F")
    wind = f"wind {best.wind_mph} mph"
    if best.gust_mph:
        wind += f" gusting {best.gust_mph}"
    parts.append(wind)
    if sunset is not None:
        parts.append(
            f"done before sunset at {clock(sunset)}"
            if in_daylight(best, sunset)
            else f"after sunset at {clock(sunset)}, bring a light"
        )
    return f"Go at {clock(best.start)}: " + ", ".join(parts) + "."


@dataclass
class Morning:
    day: date
    pop: int
    low_f: int
    thunder: bool
    source: str  # "hourly" or "daily"
    light: bool = False  # any rain expected is light

    @property
    def penalty(self) -> float:
        return (
            rain_penalty(self.pop, self.light)
            + max(0, self.low_f - COOL_MORNING_F)
            + (100 if self.thunder else 0)
        )


def mornings(hours: list[Hour], periods: list[Period], first: date, n: int = 7) -> list[Morning]:
    """One Morning per day from `first`, from the hourly forecast where it reaches,
    otherwise from the 12-hour periods (overnight low and the daytime rain chance)."""
    out = []
    for i in range(n):
        day = first + timedelta(days=i)
        window = [h for h in hours if h.start.date() == day and h.start.hour in MORNING_HOURS]
        if len(window) == len(MORNING_HOURS):
            out.append(
                Morning(
                    day,
                    max(h.pop for h in window),
                    min(h.temp_f for h in window),
                    any(h.thunder for h in window),
                    "hourly",
                    all(h.light_rain or h.pop == 0 for h in window),
                )
            )
            continue
        night = next((p for p in periods if not p.is_day and p.end.date() == day), None)
        daytime = next((p for p in periods if p.is_day and p.start.date() == day), None)
        if night is None and daytime is None:
            continue
        used = [p for p in (night, daytime) if p]
        out.append(
            Morning(
                day,
                max(p.pop for p in used),
                night.temp_f if night else daytime.temp_f,
                any(p.thunder for p in used),
                "daily",
            )
        )
    return out


def clock(t: datetime) -> str:
    """'5:29 pm', or '5 pm' on the hour."""
    h = t.hour % 12 or 12
    suffix = "am" if t.hour < 12 else "pm"
    return f"{h} {suffix}" if t.minute == 0 else f"{h}:{t.minute:02d} {suffix}"
