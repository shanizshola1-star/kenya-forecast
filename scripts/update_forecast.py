"""Download the latest 7-day forecast for every ward in Kenya and write forecast.js
(daily figures) and forecast-hourly.js (3-hour steps for the animation).

The dashboard (index.html) reads forecast.js each time someone opens it.
This script runs automatically twice a day (see .github/workflows/update-forecast.yml).

Data source today: Open-Meteo (https://open-meteo.com), free for non-commercial use.
To switch to the KMD model output later, replace fetch_hourly() with a function that
returns the same shape: for each point, a list of hourly times, rainfall and temperature.

Each forecast day runs from 09:00 EAT to 09:00 EAT the next day, as in KMD forecasts.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POINTS = ROOT / "data" / "points.json"
OUT = ROOT / "forecast.js"
OUT_HOURLY = ROOT / "forecast-hourly.js"
STEP = 3            # hours per frame in the hour-by-hour animation
DAYS = 7
CHUNK = 100          # locations per request
EAT = timezone(timedelta(hours=3))
API = "https://api.open-meteo.com/v1/forecast"


def fetch_hourly(points):
    """Return, for each point, {'time': [...], 'rain': [...], 'temp': [...]} in EAT."""
    results = []
    for i in range(0, len(points), CHUNK):
        chunk = points[i:i + CHUNK]
        query = urllib.parse.urlencode({
            "latitude": ",".join(str(p["lat"]) for p in chunk),
            "longitude": ",".join(str(p["lon"]) for p in chunk),
            "hourly": "precipitation,temperature_2m",
            "timezone": "Africa/Nairobi",
            "forecast_days": DAYS + 1,
        })
        for attempt in range(4):
            try:
                with urllib.request.urlopen(f"{API}?{query}", timeout=60) as r:
                    data = json.load(r)
                break
            except Exception as e:  # network hiccup or rate limit: wait and retry
                if attempt == 3:
                    raise RuntimeError(f"Forecast download failed for points {i}-{i + len(chunk)}: {e}")
                time.sleep(10 * (attempt + 1))
        if isinstance(data, dict):
            data = [data]
        for loc in data:
            h = loc["hourly"]
            results.append({"time": h["time"], "rain": h["precipitation"], "temp": h["temperature_2m"]})
        time.sleep(1)
    return results


def daily_windows(series, start):
    """Rain total, max and min temperature for each 09:00-09:00 EAT window."""
    index = {t: n for n, t in enumerate(series["time"])}
    out = {"rain": [], "tmax": [], "tmin": []}
    for k in range(DAYS):
        t0 = start + timedelta(days=k)
        i0 = index.get(t0.strftime("%Y-%m-%dT%H:%M"))
        if i0 is None or i0 + 24 >= len(series["time"]):
            raise RuntimeError(f"Forecast does not cover {t0:%Y-%m-%d %H:%M} EAT")
        # Open-Meteo precipitation is the total over the preceding hour,
        # so hours 10:00 .. 09:00 next day make up the 09:00-09:00 window.
        rain = [v or 0 for v in series["rain"][i0 + 1:i0 + 25]]
        temp = [v for v in series["temp"][i0:i0 + 24] if v is not None]
        out["rain"].append(sum(rain))
        out["tmax"].append(max(temp))
        out["tmin"].append(min(temp))
    return out


def three_hourly(series, start):
    """Rain in each 3-hour step and temperature at the start of each step, x10 as whole numbers."""
    index = {t: n for n, t in enumerate(series["time"])}
    i0 = index[start.strftime("%Y-%m-%dT%H:%M")]
    rain, temp = [], []
    for s in range(DAYS * 24 // STEP):
        a = i0 + s * STEP
        rain.append(round(10 * sum(v or 0 for v in series["rain"][a + 1:a + 1 + STEP])))
        t = series["temp"][a]
        temp.append(None if t is None else round(10 * t))
    return rain, temp


def main():
    cfg = json.loads(POINTS.read_text())
    points, counties = cfg["points"], cfg["counties"]
    now = datetime.now(EAT)
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    start = start.replace(tzinfo=None)

    hourly = fetch_hourly(points)
    if len(hourly) != len(points):
        raise RuntimeError(f"Expected {len(points)} locations, got {len(hourly)}")

    r1 = lambda v: round(v, 1)
    ward_vals = [daily_windows(s, start) for s in hourly]

    # values per ward: [param][day] -> mean, extreme (same for a single point)
    wards = []
    for w in ward_vals:
        m = []
        for key in ("rain", "tmax", "tmin"):
            for k in range(DAYS):
                m += [r1(w[key][k]), r1(w[key][k])]
        wards.append(m)

    # county = average of its wards; extreme = highest ward (lowest for min temperature)
    county_m = []
    for ci in range(len(counties)):
        members = [ward_vals[n] for n, p in enumerate(points) if p["county"] == ci]
        m = []
        for key in ("rain", "tmax", "tmin"):
            for k in range(DAYS):
                vals = [w[key][k] for w in members]
                ext = min(vals) if key == "tmin" else max(vals)
                m += [r1(sum(vals) / len(vals)), r1(ext)]
        county_m.append(m)

    OUT.write_text("window.FORECAST = " + json.dumps({
        "start": start.strftime("%Y-%m-%d"),
        "days": DAYS,
        "issued": now.strftime("%Y-%m-%dT%H:%M%z"),
        "source": "Open-Meteo",
        "counties": county_m,
        "wards": wards,
    }, separators=(",", ":")) + ";\n")
    # frames for the hour-by-hour animation: one list of ward values per 3-hour step
    per_ward = [three_hourly(s, start) for s in hourly]
    steps = DAYS * 24 // STEP
    OUT_HOURLY.write_text("window.FORECAST_HOURLY = " + json.dumps({
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "step_hours": STEP,
        "steps": steps,
        "rain_x10": [[w[0][s] for w in per_ward] for s in range(steps)],
        "temp_x10": [[w[1][s] for w in per_ward] for s in range(steps)],
    }, separators=(",", ":")) + ";\n")

    print(f"Wrote {OUT.name}: {len(counties)} counties, {len(wards)} wards, from {start:%Y-%m-%d} 09:00 EAT")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Update failed, forecast.js left unchanged: {e}", file=sys.stderr)
        sys.exit(1)
