#!/usr/bin/env python3
"""Cloud forecast along the line of sight to a low sun, not overhead.

For a sunset eclipse the sun sits a few degrees above the horizon, so the
sightline travels ~10 km horizontally for every 1 km it climbs. Cloud that
blocks totality is therefore tens of kilometres away in the direction of the
sun, and cirrus can be a hundred. A forecast for the viewing site itself
describes the wrong air.

This samples each cloud layer where the sightline actually reaches it,
offsetting along the sun's azimuth and correcting for Earth curvature with the
standard refraction factor k = 7/6.

Usage:
  uv run horizon_weather.py 41.425 0.295
  uv run horizon_weather.py 41.425 0.295 --date 2026-08-12 --hour 20
"""

import argparse
import json
import math
import urllib.parse
import urllib.request

EARTH_RADIUS_KM = 6371.0
REFRACTION_K = 7.0 / 6.0

# Representative heights above ground for each layer, and the Open-Meteo field
# that reports it.
LAYERS = [
    ("low", 1.0, "cloud_cover_low"),
    ("low (high base)", 2.0, "cloud_cover_low"),
    ("mid", 4.0, "cloud_cover_mid"),
    ("high (cirrus)", 10.0, "cloud_cover_high"),
]

API = "https://api.open-meteo.com/v1/forecast"


def sightline_distance_km(sun_alt_deg, height_km):
    """Horizontal distance at which a sightline reaches a given height.

    Solves d*tan(alt) + d^2 / (2*R_eff) = height, the second term being the
    curvature drop of the Earth under the ray.
    """
    t = math.tan(math.radians(sun_alt_deg))
    r_eff = EARTH_RADIUS_KM * REFRACTION_K
    a = 1.0 / (2.0 * r_eff)
    return (-t + math.sqrt(t * t + 4.0 * a * height_km)) / (2.0 * a)


def offset(lat, lon, azimuth_deg, distance_km):
    """Point `distance_km` away along `azimuth_deg` from (lat, lon)."""
    az = math.radians(azimuth_deg)
    dlat = distance_km * math.cos(az) / 111.0
    dlon = distance_km * math.sin(az) / (111.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def fetch(lat, lon, date, model):
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility",
        "start_date": date,
        "end_date": date,
        "timezone": "Europe/Madrid",
        "models": model,
    })
    with urllib.request.urlopen(f"{API}?{q}", timeout=40) as r:
        return json.load(r)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("--azimuth", type=float, default=285.0, help="sun azimuth, degrees")
    p.add_argument("--altitude", type=float, default=5.5, help="sun altitude, degrees")
    p.add_argument("--date", default="2026-08-12")
    p.add_argument("--hour", type=int, default=20)
    p.add_argument("--model", default="ecmwf_ifs")
    args = p.parse_args()

    print(f"Sightline from {args.lat:.4f},{args.lon:.4f} "
          f"toward azimuth {args.azimuth:.1f} deg at {args.altitude:.1f} deg altitude")
    print(f"Model {args.model}, {args.date} {args.hour:02d}:00 local\n")

    site = fetch(args.lat, args.lon, args.date, args.model)
    idx = args.hour  # hourly arrays start at 00:00 local

    print(f"{'layer':18s}{'sample at':>11}  {'coords':>19}  {'cover':>6}   {'overhead':>8}")
    worst = 0
    for name, height, field in LAYERS:
        dist = sightline_distance_km(args.altitude, height)
        la, lo = offset(args.lat, args.lon, args.azimuth, dist)
        try:
            data = fetch(la, lo, args.date, args.model)
            v = data["hourly"][field][idx]
        except Exception as e:
            print(f"  {name:16s}{dist:8.0f} km  {la:8.3f},{lo:7.3f}   ERROR {e}")
            continue
        here = site["hourly"][field][idx]
        flag = "  <-- blocks the sun" if (v or 0) >= 40 and height <= 2.0 else ""
        print(f"  {name:16s}{dist:8.0f} km  {la:8.3f},{lo:7.3f}  {v:5.0f}%  {here:7.0f}%{flag}")
        if height <= 2.0:
            worst = max(worst, v or 0)

    print()
    if worst >= 40:
        print(f"  VERDICT: low cloud {worst:.0f}% on the sightline. The sun is likely to be")
        print( "           hidden even if it is clear overhead.")
    elif worst >= 15:
        print(f"  VERDICT: low cloud {worst:.0f}% on the sightline. Marginal, watch satellite.")
    else:
        print(f"  VERDICT: low cloud {worst:.0f}% on the sightline. Horizon should be clear.")


if __name__ == "__main__":
    main()
