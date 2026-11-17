"""Sunset time from the NOAA general solar position equations.

Reference: NOAA Global Monitoring Laboratory, "General Solar Position
Calculations" (https://gml.noaa.gov/grad/solcalc/solareqns.PDF). Accurate to
about a minute at low and middle latitudes, which is plenty for planning a run.
"""

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ZENITH = 90.833  # degrees; includes refraction and the sun's apparent radius


def sunset(day: date, lat: float, lon: float, tz: str) -> datetime | None:
    """Local sunset on `day`, or None during polar day/night."""
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1 + (18 - 12) / 24)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    phi = math.radians(lat)
    cos_ha = math.cos(math.radians(ZENITH)) / (math.cos(phi) * math.cos(decl)) - math.tan(
        phi
    ) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    minutes_utc = 720 - 4 * (lon - ha) - eqtime  # lon is positive east
    utc = datetime.combine(day, time(), timezone.utc) + timedelta(minutes=minutes_utc)
    return utc.astimezone(ZoneInfo(tz))
