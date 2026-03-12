#!/usr/bin/env python3
"""
Eclipse Terrain Obstruction Analyzer
=====================================
Computes where in Spain you can see the Aug 12, 2026 total solar eclipse
without terrain blocking the low sun.

Outputs:
  data/overlay.png   - colored grid overlay for the Leaflet map
  data/grid_data.json - per-cell data for click popups
  data/totality_outline.json - GeoJSON outline of the totality zone

Usage:
  uv run prepare.py
"""

import gzip
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

import ephem
import numpy as np
from PIL import Image
from tqdm import tqdm

# ============================================================
# Configuration
# ============================================================

DATA_DIR = Path(__file__).parent / "data"
SRTM_DIR = DATA_DIR / "srtm"

# Eclipse search window (UTC)
SEARCH_START_UTC = "2026/8/12 18:00"
SEARCH_END_UTC = "2026/8/12 20:45"

# Spain bounding box (generous, covers entire potential totality path + margin)
LAT_MIN, LAT_MAX = 39.5, 44.5
LON_MIN, LON_MAX = -10.5, 3.5

# Grid resolutions
COARSE_STEP = 0.1   # ~10 km, for finding totality zone
FINE_STEP = 0.01    # ~1 km, for terrain analysis

# Terrain ray-casting
RAY_MAX_DIST = 50_000  # meters
RAY_STEP = 500         # meters (100 samples per ray)
EARTH_RADIUS = 6_371_000
REFRACTION_K = 7.0 / 6.0  # standard atmospheric refraction
EYE_HEIGHT = 2.0           # meters above ground

# Number of sun-position samples during totality
N_SUN_SAMPLES = 7


# ============================================================
# SRTM Elevation Data
# ============================================================

class SRTMElevation:
    """Download and query SRTM3 elevation tiles (~90m resolution)."""

    SOURCES = [
        # AWS Terrain Tiles (free, no auth)
        "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{lat_dir}/{filename}.gz",
    ]

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._tiles = {}

    @staticmethod
    def _tile_name(lat_floor, lon_floor):
        ns = "N" if lat_floor >= 0 else "S"
        ew = "E" if lon_floor >= 0 else "W"
        return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}"

    def _download(self, lat_floor, lon_floor):
        name = self._tile_name(lat_floor, lon_floor)
        hgt_path = self.cache_dir / f"{name}.hgt"
        if hgt_path.exists():
            return hgt_path

        ns = "N" if lat_floor >= 0 else "S"
        lat_dir = f"{ns}{abs(lat_floor):02d}"
        filename = f"{name}.hgt"

        for src_template in self.SOURCES:
            url = src_template.format(lat_dir=lat_dir, filename=filename)
            gz_path = self.cache_dir / f"{filename}.gz"
            try:
                urllib.request.urlretrieve(url, gz_path)
                with gzip.open(gz_path, "rb") as fin:
                    with open(hgt_path, "wb") as fout:
                        fout.write(fin.read())
                gz_path.unlink()
                return hgt_path
            except Exception:
                if gz_path.exists():
                    gz_path.unlink()
                continue

        return None  # ocean or unavailable tile

    def _load(self, lat_floor, lon_floor):
        key = (lat_floor, lon_floor)
        if key in self._tiles:
            return self._tiles[key]

        hgt_path = self.cache_dir / f"{self._tile_name(lat_floor, lon_floor)}.hgt"
        if not hgt_path.exists():
            hgt_path = self._download(lat_floor, lon_floor)

        if hgt_path is None or not hgt_path.exists():
            self._tiles[key] = None
            return None

        data = np.fromfile(str(hgt_path), dtype=">i2")
        if data.size == 1201 * 1201:
            data = data.reshape(1201, 1201)
        elif data.size == 3601 * 3601:
            data = data.reshape(3601, 3601)
        else:
            self._tiles[key] = None
            return None

        data = np.where(data == -32768, 0, data).astype(np.int16)
        self._tiles[key] = data
        return data

    def get_elevation(self, lat, lon):
        tile_lat = int(math.floor(lat))
        tile_lon = int(math.floor(lon))
        data = self._load(tile_lat, tile_lon)
        if data is None:
            return 0
        size = data.shape[0] - 1
        row = int((tile_lat + 1 - lat) * size)
        col = int((lon - tile_lon) * size)
        row = max(0, min(row, size))
        col = max(0, min(col, size))
        return int(data[row, col])

    def preload_area(self, lat_min, lat_max, lon_min, lon_max):
        tiles = []
        for lat in range(int(math.floor(lat_min)), int(math.floor(lat_max)) + 1):
            for lon in range(int(math.floor(lon_min)), int(math.floor(lon_max)) + 1):
                tiles.append((lat, lon))
        print(f"  Pre-loading {len(tiles)} SRTM tiles...")
        for lat, lon in tqdm(tiles, desc="  Downloading"):
            self._load(lat, lon)
        loaded = sum(1 for v in self._tiles.values() if v is not None)
        print(f"  {loaded} land tiles loaded, {len(tiles) - loaded} ocean/missing")


# ============================================================
# Eclipse Computation
# ============================================================

def _is_totality(obs):
    """Check if the observer is currently experiencing totality."""
    s = ephem.Sun(obs)
    m = ephem.Moon(obs)
    sep = float(ephem.separation(s, m))
    moon_r = float(m.size) / 2.0 / 3600.0 * math.pi / 180.0
    sun_r = float(s.size) / 2.0 / 3600.0 * math.pi / 180.0
    return sep < (moon_r - sun_r) and moon_r > sun_r


def find_totality(lat, lon, t_start=None, t_end=None, dt_sec=20):
    """Find C2 and C3 contact times for totality at a location.

    Returns (c2, c3) as ephem.Date or (None, None) if no totality.
    """
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 0
    obs.pressure = 0

    if t_start is None:
        t_start = ephem.Date(SEARCH_START_UTC)
    if t_end is None:
        t_end = ephem.Date(SEARCH_END_UTC)

    dt = dt_sec / 86400.0
    c2 = None
    in_totality = False

    t = t_start
    while t <= t_end:
        obs.date = t
        total = _is_totality(obs)

        if total and not in_totality:
            c2 = t
            in_totality = True
        elif not total and in_totality:
            return c2, t

        t += dt

    if c2 is not None:
        return c2, t
    return None, None


def get_sun_position(lat, lon, t):
    """Get sun azimuth and altitude (degrees) at a given time and location."""
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 0
    obs.pressure = 0
    obs.date = t
    s = ephem.Sun(obs)
    return math.degrees(float(s.az)), math.degrees(float(s.alt))


# ============================================================
# Terrain Analysis
# ============================================================

def check_ray(viewer_lat, viewer_lon, viewer_elev, azimuth_deg, srtm):
    """Cast a ray and return the maximum terrain angle along it."""
    az_rad = math.radians(azimuth_deg)
    cos_lat = math.cos(math.radians(viewer_lat))

    max_angle = -90.0
    max_dist = 0.0

    for d in range(RAY_STEP, RAY_MAX_DIST + 1, RAY_STEP):
        dlat = d * math.cos(az_rad) / 111320.0
        dlon = d * math.sin(az_rad) / (111320.0 * cos_lat)

        elev = srtm.get_elevation(viewer_lat + dlat, viewer_lon + dlon)
        if elev <= 0:
            continue

        # Earth curvature + refraction correction
        drop = d * d / (2.0 * EARTH_RADIUS * REFRACTION_K)
        angle = math.degrees(math.atan2(elev - viewer_elev - drop, d))

        if angle > max_angle:
            max_angle = angle
            max_dist = d

    return max_angle, max_dist


def analyze_point(lat, lon, c2, c3, srtm):
    """Full terrain obstruction analysis for one viewing location."""
    viewer_elev = srtm.get_elevation(lat, lon) + EYE_HEIGHT

    # Sample sun positions across totality
    samples = []
    for i in range(N_SUN_SAMPLES):
        frac = i / max(N_SUN_SAMPLES - 1, 1)
        t = ephem.Date(c2 + frac * (c3 - c2))
        az, alt = get_sun_position(lat, lon, t)
        samples.append((t, az, alt))

    blocked_count = 0
    worst_margin = 999.0
    worst_block_info = None

    for t, sun_az, sun_alt in samples:
        if sun_alt <= 0:
            blocked_count += 1
            worst_margin = min(worst_margin, sun_alt)
            continue

        terrain_angle, terrain_dist = check_ray(
            lat, lon, viewer_elev, sun_az, srtm
        )
        margin = sun_alt - terrain_angle

        if margin < worst_margin:
            worst_margin = margin
            if margin < 0:
                worst_block_info = {
                    "terrain_angle": round(terrain_angle, 2),
                    "terrain_dist_km": round(terrain_dist / 1000, 1),
                    "sun_alt": round(sun_alt, 2),
                    "sun_az": round(sun_az, 2),
                }

        if terrain_angle >= sun_alt:
            blocked_count += 1

    # Mid-totality sun position
    mid_t = ephem.Date((c2 + c3) / 2.0)
    mid_az, mid_alt = get_sun_position(lat, lon, mid_t)
    duration = (c3 - c2) * 86400.0  # seconds

    # Convert times to HH:MM:SS UTC strings
    c2_str = ephem.Date(c2).datetime().strftime("%H:%M:%S")
    c3_str = ephem.Date(c3).datetime().strftime("%H:%M:%S")

    if blocked_count == 0:
        status = "clear"
    elif blocked_count >= N_SUN_SAMPLES:
        status = "blocked"
    else:
        status = "partial"

    return {
        "status": status,
        "margin": round(worst_margin, 2),
        "elev": round(viewer_elev, 0),
        "sun_alt": round(mid_alt, 2),
        "sun_az": round(mid_az, 2),
        "c2": c2_str,
        "c3": c3_str,
        "dur": round(duration, 1),
        "block": worst_block_info,
    }


# ============================================================
# Output Generation
# ============================================================

def margin_to_color(margin):
    """Map margin (sun_alt - max_terrain_angle) to RGBA color."""
    if margin >= 3.0:
        return (21, 101, 192, 180)    # deep blue
    elif margin >= 2.0:
        return (30, 136, 229, 180)    # medium blue
    elif margin >= 1.0:
        return (66, 165, 245, 180)    # light blue
    elif margin >= 0.0:
        return (144, 202, 249, 180)   # pale blue (clear but tight)
    elif margin >= -1.0:
        return (255, 152, 0, 180)     # orange (partially blocked)
    else:
        return (244, 67, 54, 170)     # red (blocked)


def generate_output(grid, lat_min, lon_min, n_rows, n_cols, step):
    """Create overlay PNG and data JSON from the results grid."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- PNG overlay ---
    img = Image.new("RGBA", (n_cols, n_rows), (0, 0, 0, 0))
    pixels = img.load()

    for r in range(n_rows):
        for c in range(n_cols):
            key = f"{r},{c}"
            if key in grid:
                color = margin_to_color(grid[key]["margin"])
                # PNG row 0 = north = highest latitude
                pixels[c, r] = color

    img.save(DATA_DIR / "overlay.png")
    print(f"  Saved overlay.png ({n_cols}x{n_rows})")

    # --- JSON data ---
    meta = {
        "lat_min": lat_min,
        "lon_min": lon_min,
        "lat_max": lat_min + n_rows * step,
        "lon_max": lon_min + n_cols * step,
        "step": step,
        "rows": n_rows,
        "cols": n_cols,
    }
    output = {"meta": meta, "grid": grid}

    json_path = DATA_DIR / "grid_data.json"
    with open(json_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_mb = json_path.stat().st_size / 1024 / 1024
    print(f"  Saved grid_data.json ({size_mb:.1f} MB, {len(grid)} cells)")


# ============================================================
# Main Pipeline
# ============================================================

def main():
    t_total = time.time()
    print("=" * 60)
    print("Eclipse Terrain Obstruction Analyzer")
    print("Total Solar Eclipse — August 12, 2026 — Spain")
    print("=" * 60)
    print()

    srtm = SRTMElevation(SRTM_DIR)

    # ---- Phase 1: Coarse scan for totality zone ----
    print("Phase 1: Finding totality zone (coarse scan)...")
    t0 = time.time()

    coarse_lats = np.arange(LAT_MIN, LAT_MAX, COARSE_STEP)
    coarse_lons = np.arange(LON_MIN, LON_MAX, COARSE_STEP)
    coarse_totality = {}  # (lat_idx, lon_idx) -> {c2, c3, mid_time}

    total_coarse = len(coarse_lats) * len(coarse_lons)
    with tqdm(total=total_coarse, desc="  Scanning") as pbar:
        for i, lat in enumerate(coarse_lats):
            for j, lon in enumerate(coarse_lons):
                c2, c3 = find_totality(lat, lon)
                if c2 is not None:
                    mid = ephem.Date((c2 + c3) / 2.0)
                    coarse_totality[(i, j)] = {
                        "c2": c2, "c3": c3, "mid": mid,
                        "lat": lat, "lon": lon,
                    }
                pbar.update(1)

    print(f"  Found {len(coarse_totality)} coarse cells with totality "
          f"(out of {total_coarse}) in {time.time() - t0:.0f}s")

    if not coarse_totality:
        print("ERROR: No totality found in search area. Check coordinates.")
        sys.exit(1)

    # ---- Phase 2: Pre-download SRTM tiles ----
    print()
    print("Phase 2: Downloading elevation data...")

    # Find lat/lon bounds of totality zone + ray margin
    tot_lats = [v["lat"] for v in coarse_totality.values()]
    tot_lons = [v["lon"] for v in coarse_totality.values()]
    ray_margin = RAY_MAX_DIST / 111320.0 + 0.1  # degrees

    srtm.preload_area(
        min(tot_lats) - ray_margin,
        max(tot_lats) + ray_margin,
        min(tot_lons) - ray_margin,
        max(tot_lons) + ray_margin,
    )

    # ---- Phase 3: Fine grid analysis ----
    print()
    print("Phase 3: Fine grid terrain analysis...")
    t0 = time.time()

    # Build set of coarse cells with totality, expanded by 1 cell
    coarse_set = set()
    for (i, j) in coarse_totality:
        for di in range(-1, 2):
            for dj in range(-1, 2):
                coarse_set.add((i + di, j + dj))

    # Fine grid parameters
    fine_lats = np.arange(LAT_MIN, LAT_MAX, FINE_STEP)
    fine_lons = np.arange(LON_MIN, LON_MAX, FINE_STEP)
    n_rows = len(fine_lats)
    n_cols = len(fine_lons)

    # Determine which fine cells fall within the (expanded) totality zone
    fine_cells = []
    for r, lat in enumerate(fine_lats):
        ci = int((lat - LAT_MIN) / COARSE_STEP)
        for c, lon in enumerate(fine_lons):
            cj = int((lon - LON_MIN) / COARSE_STEP)
            if (ci, cj) in coarse_set:
                fine_cells.append((r, c, lat, lon))

    print(f"  {len(fine_cells)} fine cells to analyze "
          f"(grid {n_rows}x{n_cols}, step {FINE_STEP}°)")

    # For each fine cell, find nearest coarse cell to get approximate eclipse time
    coarse_mids = {}
    for (i, j), v in coarse_totality.items():
        coarse_mids[(i, j)] = v["mid"]

    grid = {}
    skipped = 0
    with tqdm(total=len(fine_cells), desc="  Analyzing") as pbar:
        for r, c_col, lat, lon in fine_cells:
            # Find nearest coarse cell with totality data
            ci = int((lat - LAT_MIN) / COARSE_STEP)
            cj = int((lon - LON_MIN) / COARSE_STEP)
            approx_mid = None
            for di in range(0, 3):
                for dj in range(0, 3):
                    for si in [0, -1, 1]:
                        for sj in [0, -1, 1]:
                            key = (ci + si * di, cj + sj * dj)
                            if key in coarse_mids:
                                approx_mid = coarse_mids[key]
                                break
                        if approx_mid:
                            break
                    if approx_mid:
                        break
                if approx_mid:
                    break

            if approx_mid is None:
                skipped += 1
                pbar.update(1)
                continue

            # Narrow search for exact totality times
            window = 5.0 / (24.0 * 60.0)  # 5 minutes in days
            c2, c3 = find_totality(
                lat, lon,
                t_start=ephem.Date(approx_mid - window),
                t_end=ephem.Date(approx_mid + window),
                dt_sec=5,
            )

            if c2 is None:
                skipped += 1
                pbar.update(1)
                continue

            # Terrain analysis
            result = analyze_point(lat, lon, c2, c3, srtm)
            # Row 0 of image = north edge, so invert row index
            img_row = n_rows - 1 - r
            grid[f"{img_row},{c_col}"] = result

            pbar.update(1)

    print(f"  Analyzed {len(grid)} cells, skipped {skipped} "
          f"in {time.time() - t0:.0f}s")

    # ---- Phase 4: Generate output ----
    print()
    print("Phase 4: Generating output files...")
    generate_output(grid, LAT_MIN, LON_MIN, n_rows, n_cols, FINE_STEP)

    print()
    print(f"Total time: {time.time() - t_total:.0f}s")
    print()
    print("Done! Start the viewer with:")
    print("  uv run app.py")


if __name__ == "__main__":
    main()
