#!/usr/bin/env python3
"""Recompute totality contacts in an existing grid using Besselian elements.

The original prepare.py found totality by scanning PyEphem sun/moon disc
separation. That overestimated duration near the path limits and put the band
too far north. This rewrites c2/c3/duration for every cell from Besselian
elements, drops cells that are not actually total, and regenerates the overlay.

Terrain margin, road distance and forest flags are untouched: they do not
depend on the contact times.

Usage:
  uv run rebuild_contacts.py
"""

import gzip
import json
from pathlib import Path

from PIL import Image

from besselian import local_circumstances, hms

DATA_DIR = Path(__file__).parent / "data"


def margin_to_color(margin):
    if margin >= 3.0:
        return (13, 71, 161, 190)
    elif margin >= 2.0:
        return (25, 118, 210, 185)
    elif margin >= 1.0:
        return (66, 165, 245, 180)
    elif margin >= 0.0:
        return (144, 202, 249, 175)
    elif margin >= -1.0:
        return (255, 152, 0, 180)
    else:
        return (244, 67, 54, 170)


def main():
    src = DATA_DIR / "grid_data.json.gz"
    with gzip.open(src, "rt") as f:
        data = json.load(f)

    meta = data["meta"]
    grid = data["grid"]
    step = meta["step"]
    rows = meta["rows"]

    print(f"Loaded {len(grid)} cells")

    new_grid = {}
    dropped = 0
    shortened = 0
    delta_total = 0.0

    for key, arr in grid.items():
        img_row, col = (int(v) for v in key.split(","))
        row_from_south = rows - 1 - img_row
        lat = meta["lat_min"] + (row_from_south + 0.5) * step
        lon = meta["lon_min"] + (col + 0.5) * step

        # Cell elevation is field 2; use it so the contacts match the viewer.
        elev = arr[2] if len(arr) > 2 else 0

        circ = local_circumstances(lat, lon, elev)

        if not circ["is_total"] or circ["duration"] <= 0:
            dropped += 1
            continue

        old_dur = arr[7]
        new_dur = round(circ["duration"], 1)
        if new_dur < old_dur:
            shortened += 1
        delta_total += new_dur - old_dur

        arr = list(arr)
        arr[5] = hms(circ["c2"])
        arr[6] = hms(circ["c3"])
        arr[7] = new_dur
        new_grid[key] = arr

    kept = len(new_grid)
    print(f"  kept {kept}, dropped {dropped} cells that are not actually total")
    if kept:
        print(f"  {shortened} cells shortened, mean change {delta_total / kept:+.1f}s")

    # --- overlay PNG, same colour rules, minus the dropped cells ---
    img = Image.new("RGBA", (meta["cols"], rows), (0, 0, 0, 0))
    pixels = img.load()
    for key, arr in new_grid.items():
        img_row, col = (int(v) for v in key.split(","))
        pixels[col, img_row] = margin_to_color(arr[1])
    img.save(DATA_DIR / "overlay.png")
    print(f"  Saved overlay.png ({meta['cols']}x{rows})")

    out = {"meta": meta, "grid": new_grid}
    with gzip.open(src, "wt") as f:
        json.dump(out, f, separators=(",", ":"))
    size_mb = src.stat().st_size / 1024 / 1024
    print(f"  Saved grid_data.json.gz ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
