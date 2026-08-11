#!/usr/bin/env python3
"""Serve the eclipse terrain obstruction map."""

import math
from functools import lru_cache
from pathlib import Path

import ephem
from flask import Flask, send_from_directory, render_template, Response, request, jsonify

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data"

# Partial-phase search window (UTC), wide enough to bracket C1 and C4 anywhere
# in the Spanish totality zone.
PARTIAL_SEARCH_START = "2026/8/12 15:00"
PARTIAL_SEARCH_END = "2026/8/12 21:00"
COARSE_STEP_SEC = 30


def _observer(lat, lon):
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 0
    obs.pressure = 0
    return obs


def _overlap(obs, t):
    """Signed disc overlap in radians: positive while the discs intersect.

    Zero crossings are the partial contacts C1 (first) and C4 (last).
    """
    obs.date = t
    s = ephem.Sun(obs)
    m = ephem.Moon(obs)
    sep = float(ephem.separation(s, m))
    sun_r = float(s.size) / 2.0 / 3600.0 * math.pi / 180.0
    moon_r = float(m.size) / 2.0 / 3600.0 * math.pi / 180.0
    return (sun_r + moon_r) - sep


def _bisect(obs, t_lo, t_hi, tol_sec=1.0):
    """Refine a bracketed sign change of _overlap down to tol_sec."""
    tol = tol_sec / 86400.0
    f_lo = _overlap(obs, t_lo)
    while (t_hi - t_lo) > tol:
        t_mid = (t_lo + t_hi) / 2.0
        f_mid = _overlap(obs, t_mid)
        if (f_mid >= 0) == (f_lo >= 0):
            t_lo, f_lo = t_mid, f_mid
        else:
            t_hi = t_mid
    return ephem.Date((t_lo + t_hi) / 2.0)


def _find_contacts(lat, lon):
    """Return (C1, C4) as ephem.Date, or (None, None) if no eclipse here."""
    obs = _observer(lat, lon)
    dt = COARSE_STEP_SEC / 86400.0
    t_end = ephem.Date(PARTIAL_SEARCH_END)

    c1 = c4 = None
    t_prev = ephem.Date(PARTIAL_SEARCH_START)
    f_prev = _overlap(obs, t_prev)

    t = ephem.Date(t_prev + dt)
    while t <= t_end:
        f = _overlap(obs, t)
        if f_prev < 0 <= f and c1 is None:
            c1 = _bisect(obs, t_prev, t)
        elif f_prev >= 0 > f and c1 is not None:
            c4 = _bisect(obs, t_prev, t)
            break
        t_prev, f_prev = t, f
        t = ephem.Date(t + dt)

    return c1, c4


def _hms(t):
    """Format an ephem.Date as UTC HH:MM:SS."""
    if t is None:
        return None
    _, _, _, h, mi, s = t.tuple()
    s = int(round(s))
    if s == 60:
        s = 0
        mi += 1
    if mi == 60:
        mi = 0
        h += 1
    return f"{h % 24:02d}:{mi:02d}:{s:02d}"


def _sun_alt(lat, lon, t):
    obs = _observer(lat, lon)
    obs.date = t
    return math.degrees(float(ephem.Sun(obs).alt))


@lru_cache(maxsize=8192)
def _eclipse_times(lat, lon):
    """C1/C4/sunset for one location. Cached on the rounded coordinate."""
    c1, c4 = _find_contacts(lat, lon)

    obs = _observer(lat, lon)
    obs.horizon = "-0:34"  # standard refraction at the horizon
    obs.date = "2026/8/12 12:00"
    try:
        sunset = obs.next_setting(ephem.Sun(), use_center=False)
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        sunset = None

    return {
        "c1": _hms(c1),
        "c4": _hms(c4),
        "c4_below_horizon": (c4 is not None and _sun_alt(lat, lon, c4) < 0),
        "sunset": _hms(sunset),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/eclipse_times")
def eclipse_times():
    try:
        lat = round(float(request.args["lat"]), 2)
        lon = round(float(request.args["lon"]), 2)
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lon required"}), 400
    return jsonify(_eclipse_times(lat, lon))


@app.route("/data/grid_data.json")
def serve_grid():
    gz_path = DATA_DIR / "grid_data.json.gz"
    data = gz_path.read_bytes()
    return Response(data, mimetype="application/json",
                    headers={"Content-Encoding": "gzip"})


@app.route("/data/<path:filename>")
def serve_data(filename):
    return send_from_directory(DATA_DIR, filename)


if __name__ == "__main__":
    if not (DATA_DIR / "grid_data.json.gz").exists():
        print("Error: No data found. Run 'uv run prepare.py' first.")
        raise SystemExit(1)
    print("Opening map at http://localhost:8026")
    app.run(debug=False, port=8026)
