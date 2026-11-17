# runwx

Tells you whether and when to go for a run, from National Weather Service
forecasts (US only). Needs Python 3.10+, nothing else.

## Setup

```bash
cp config.example.json config.json   # set your lat/lon, e.g. 40.7128, -74.0060
```

## Usage

```bash
python3 -m runwx               # next 3 hours, every 30 min, plus sunset
python3 -m runwx before 18:00  # best time to run before 6 pm
python3 -m runwx week          # best mornings this week
python3 -m runwx --open        # also open the radar loop in a browser
```

Light rain counts for little, and hours with thunder are flagged. For the next
hour or two, trust the radar over the forecast; the radar loop is picked from
your lat/lon.
