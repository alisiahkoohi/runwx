"""runwx: should I go for a run, and when? (National Weather Service data)"""

import argparse
import json
import os
import re
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import plan
from .nws import NWS, half_hourly
from .sun import sunset

DEFAULT_UA = "runwx (my-email@example.com)"
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None) -> dict:
    candidates = [Path(path)] if path else [
        *([Path(os.environ["RUNWX_CONFIG"])] if "RUNWX_CONFIG" in os.environ else []),
        Path.cwd() / "config.json",
        REPO_ROOT / "config.json",
    ]
    for c in candidates:
        if c.is_file():
            cfg = json.loads(c.read_text())
            if "lat" not in cfg or "lon" not in cfg:
                raise SystemExit(f"{c} needs both 'lat' and 'lon'.")
            return cfg
    raise SystemExit(
        "No config.json found. Copy config.example.json to config.json and set your lat/lon."
    )


def parse_deadline(text: str, now: datetime) -> datetime:
    """Accept 18:00, 18, 6pm or 6:30pm; today in the forecast's time zone."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip().lower())
    if not m:
        raise SystemExit(f"Can't read the time {text!r}; use e.g. 18:00 or 6pm.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if not 1 <= hour <= 12:
            raise SystemExit(f"Can't read the time {text!r}.")
        hour = hour % 12 + (12 if m.group(3) == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit(f"Can't read the time {text!r}.")
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        raise SystemExit(f"{plan.clock(deadline)} has already passed today.")
    return deadline


def wind_text(h) -> str:
    s = f"{h.wind_dir} {h.wind_mph} mph".strip()
    return s + (f", gusts {h.gust_mph}" if h.gust_mph else "")


def print_hours(hours) -> None:
    print(f"{'Time':<7} {'Temp':>5} {'Rain':>14}  {'Wind':<22} Forecast")
    for h in hours:
        flag = "  << THUNDER/LIGHTNING" if h.thunder else ""
        rain = f"{h.pop}%" + (f" {h.intensity}" if h.intensity and h.pop else "")
        print(
            f"{plan.clock(h.start):<7} {str(h.temp_f) + '°F':>5} {rain:>14}  "
            f"{wind_text(h):<22} {h.text}{flag}"
        )


def cmd_now(nws: NWS, now: datetime, cfg: dict, args) -> None:
    start = now.replace(minute=now.minute // 30 * 30, second=0, microsecond=0)
    print_hours(half_hourly(nws.hourly(), start, 6))
    print("(NWS forecasts hourly; the :30 rows average the hours on either side.)")
    ss = sunset(now.date(), cfg["lat"], cfg["lon"], nws.timezone)
    if ss:
        print(f"\nSunset today: {plan.clock(ss)}")


def cmd_before(nws: NWS, now: datetime, cfg: dict, args) -> None:
    deadline = parse_deadline(args.time, now)
    hours = [h for h in nws.hourly() if h.end > now and h.start < deadline]
    ss = sunset(now.date(), cfg["lat"], cfg["lon"], nws.timezone)
    if hours:
        print_hours(hours)
        print()
    advice = plan.best_hour(hours, ss, plan.clock(deadline))
    print(advice.verdict)


def cmd_week(nws: NWS, now: datetime, cfg: dict, args) -> None:
    # Today's morning counts only if it has not started yet.
    first = now.date() if now.hour < plan.MORNING_HOURS[0] else now.date() + timedelta(days=1)
    days = plan.mornings(nws.hourly(), nws.daily(), first)
    window = f"{plan.MORNING_HOURS[0]}-{plan.MORNING_HOURS[-1] + 1} am"
    print(f"Best mornings for a run ({window}), best first:\n")
    print(f"{'#':>2}  {'Day':<11} {'Rain':>5} {'Low':>5}  Note")
    for i, m in enumerate(sorted(days, key=lambda m: (m.penalty, m.day)), 1):
        notes = []
        if m.thunder:
            notes.append("thunder possible")
        elif m.light and m.pop >= plan.RAINY:
            notes.append("light rain only")
        if m.source == "daily":
            notes.append("from 12-hour forecast")
        print(
            f"{i:>2}  {m.day.strftime('%a %b %d'):<11} {str(m.pop) + '%':>5} "
            f"{str(m.low_f) + '°F':>5}  {', '.join(notes)}"
        )


def main(argv: list[str] | None = None) -> None:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS so a subcommand does not reset a flag given before it (runwx --open now).
    common.add_argument(
        "--open", action="store_true", default=argparse.SUPPRESS,
        help="open the radar loop in a browser",
    )
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.json")

    parser = argparse.ArgumentParser(prog="runwx", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "now", parents=[common],
        help="the next 3 hours in 30-minute steps and today's sunset (the default)",
    )
    p = sub.add_parser("before", parents=[common], help="best hour to run before a deadline")
    p.add_argument("time", help="deadline today, e.g. 18:00 or 6pm")
    sub.add_parser("week", parents=[common], help="rank the next 7 mornings for a run")
    args = parser.parse_args(argv)

    cfg = load_config(getattr(args, "config", None))
    nws = NWS(cfg["lat"], cfg["lon"], cfg.get("user_agent") or DEFAULT_UA)
    now = datetime.now(ZoneInfo(nws.timezone))

    {"now": cmd_now, "before": cmd_before, "week": cmd_week}[args.command or "now"](nws, now, cfg, args)

    print(f"\nRadar loop: {nws.radar_url}")
    if getattr(args, "open", False):
        webbrowser.open(nws.radar_url)
