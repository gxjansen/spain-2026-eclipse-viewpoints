#!/usr/bin/env python3
"""
Add road proximity data to the eclipse grid.

Downloads road data from OpenStreetMap (Overpass API), computes distance
to nearest road for each grid cell, and updates the overlay + grid data.

Usage:
  uv run add_roads.py
"""

import gzip
import json
import math
import time
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm

DATA_DIR = Path(__file__).parent / "data"

# Road types to include (driveable or easily walkable)
ROAD_TYPES = (
    "motorway|motorway_link|trunk|trunk_link|"
    "primary|primary_link|secondary|secondary_link|"
    "tertiary|tertiary_link|unclassified|residential|"
    "living_street|service|track"
)

# Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Road proximity thresholds (meters)
ROADSIDE = 300      # can park and watch
SHORT_WALK = 1000   # short walk from parking
HIKE = 3000         # requires a proper hike
# > HIKE = remote / impractical

# Sampling: extract a point every N meters along road geometries
ROAD_SAMPLE_STEP = 300  # meters


def query_overpass(bbox, retries=3):
    """Query Overpass API for road geometries in a bounding box.

    bbox: (south, west, north, east)
    Returns list of (lat, lon) points sampled along roads.
    """
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:120];
    (
      way["highway"~"{ROAD_TYPES}"]({south},{west},{north},{east});
    );
    out geom;
    """

    data = urllib.parse.urlencode({"data": query}).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OVERPASS_URL, data=data,
                headers={"User-Agent": "EclipseViewer/1.0"}
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())
            return result.get("elements", [])
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 10
                print(f"    Retry in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"    Failed after {retries} attempts: {e}")
                return []


def sample_road_points(elements):
    """Extract sampled points along road geometries."""
    points = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        geom = el["geometry"]
        if len(geom) < 2:
            continue

        # Walk along the geometry, sampling every ROAD_SAMPLE_STEP meters
        accum = 0.0
        prev = geom[0]
        points.append((prev["lat"], prev["lon"]))

        for node in geom[1:]:
            dlat = (node["lat"] - prev["lat"]) * 111320
            dlon = (node["lon"] - prev["lon"]) * 111320 * math.cos(
                math.radians(node["lat"])
            )
            dist = math.sqrt(dlat**2 + dlon**2)
            accum += dist

            if accum >= ROAD_SAMPLE_STEP:
                points.append((node["lat"], node["lon"]))
                accum = 0.0

            prev = node

    return points


def haversine_approx(lat1, lon1, lat2, lon2):
    """Fast approximate distance in meters between two points."""
    dlat = (lat2 - lat1) * 111320
    dlon = (lon2 - lon1) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat**2 + dlon**2)


def margin_to_color(margin, road_dist):
    """Map margin + road distance to RGBA color."""
    # Base color from margin
    # Blue family = clear view, warm colors = blocked/partial
    if margin >= 3.0:
        r, g, b = 21, 101, 192      # deep blue
    elif margin >= 2.0:
        r, g, b = 30, 136, 229      # medium blue
    elif margin >= 1.0:
        r, g, b = 66, 165, 245      # light blue
    elif margin >= 0.0:
        r, g, b = 144, 202, 249     # pale blue (clear but tight)
    elif margin >= -1.0:
        r, g, b = 255, 152, 0       # orange (partial block)
    else:
        r, g, b = 244, 67, 54       # red (fully blocked)

    # Dim based on road distance
    if road_dist <= ROADSIDE:
        alpha = 200
    elif road_dist <= SHORT_WALK:
        alpha = 170
    elif road_dist <= HIKE:
        alpha = 120
        # Desaturate: blend toward gray
        gray = int(0.3 * r + 0.59 * g + 0.11 * b)
        r = int(r * 0.6 + gray * 0.4)
        g = int(g * 0.6 + gray * 0.4)
        b = int(b * 0.6 + gray * 0.4)
    else:
        alpha = 80
        gray = int(0.3 * r + 0.59 * g + 0.11 * b)
        r = int(r * 0.4 + gray * 0.6)
        g = int(g * 0.4 + gray * 0.6)
        b = int(b * 0.4 + gray * 0.6)

    return (r, g, b, alpha)


def main():
    print("=" * 60)
    print("Adding road proximity to eclipse data")
    print("=" * 60)
    print()

    # Load existing grid data
    gz_path = DATA_DIR / "grid_data.json.gz"
    if not gz_path.exists():
        print("Error: Run prepare.py first.")
        raise SystemExit(1)

    print("Loading existing grid data...")
    with gzip.open(gz_path, "rb") as f:
        data = json.loads(f.read())

    meta = data["meta"]
    grid = data["grid"]
    print(f"  {len(grid)} grid cells loaded")

    # Find bounding box of cells with data
    lats, lons = [], []
    for key in grid:
        img_row, col = map(int, key.split(","))
        row_from_south = meta["rows"] - 1 - img_row
        lat = meta["lat_min"] + row_from_south * meta["step"]
        lon = meta["lon_min"] + col * meta["step"]
        lats.append(lat)
        lons.append(lon)

    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    print(f"  Data bounds: {lat_min:.1f}-{lat_max:.1f}°N, {lon_min:.1f}-{lon_max:.1f}°E")

    # Download road data in 1° x 2° chunks
    print()
    print("Downloading road data from OpenStreetMap...")

    all_road_points = []
    chunks = []
    for lat in range(int(math.floor(lat_min)), int(math.ceil(lat_max))):
        for lon in range(int(math.floor(lon_min)), int(math.ceil(lon_max)), 2):
            lon_end = min(lon + 2, int(math.ceil(lon_max)) + 1)
            chunks.append((lat, lon, lat + 1, lon_end))

    print(f"  {len(chunks)} chunks to download")

    for i, (s, w, n, e) in enumerate(tqdm(chunks, desc="  Downloading")):
        elements = query_overpass((s, w, n, e))
        points = sample_road_points(elements)
        all_road_points.extend(points)
        # Be nice to the API
        if i < len(chunks) - 1:
            time.sleep(2)

    print(f"  {len(all_road_points)} road sample points collected")

    if not all_road_points:
        print("Error: No road data downloaded. Check internet connection.")
        raise SystemExit(1)

    # Build KD-tree for fast nearest-neighbor lookup
    print()
    print("Computing road proximity for each grid cell...")

    # Convert to numpy array in approximate meters (for distance calc)
    road_arr = np.array(all_road_points)
    # Use scaled coordinates for KD-tree (approximate equirectangular)
    mean_lat = (lat_min + lat_max) / 2
    cos_lat = math.cos(math.radians(mean_lat))

    road_scaled = np.column_stack([
        road_arr[:, 0] * 111320,
        road_arr[:, 1] * 111320 * cos_lat,
    ])

    # Use scipy if available, otherwise brute force with chunking
    try:
        from scipy.spatial import KDTree
        tree = KDTree(road_scaled)
        use_kdtree = True
        print("  Using KD-tree (scipy)")
    except ImportError:
        use_kdtree = False
        print("  scipy not available, using brute force (slower)")

    # Compute road distance for each grid cell
    road_distances = {}  # key -> distance in meters

    cell_keys = list(grid.keys())
    batch_size = 5000

    for batch_start in tqdm(range(0, len(cell_keys), batch_size),
                            desc="  Computing"):
        batch_keys = cell_keys[batch_start:batch_start + batch_size]
        batch_coords = []

        for key in batch_keys:
            img_row, col = map(int, key.split(","))
            row_from_south = meta["rows"] - 1 - img_row
            lat = meta["lat_min"] + (row_from_south + 0.5) * meta["step"]
            lon = meta["lon_min"] + (col + 0.5) * meta["step"]
            batch_coords.append((lat * 111320, lon * 111320 * cos_lat))

        batch_arr = np.array(batch_coords)

        if use_kdtree:
            dists, _ = tree.query(batch_arr)
            for j, key in enumerate(batch_keys):
                road_distances[key] = float(dists[j])
        else:
            # Brute force: find min distance to any road point
            for j, key in enumerate(batch_keys):
                diffs = road_scaled - batch_arr[j]
                d = np.min(np.sqrt(np.sum(diffs**2, axis=1)))
                road_distances[key] = float(d)

    # Add road distance to grid data
    print()
    print("Updating grid data with road proximity...")

    for key in grid:
        arr = grid[key]
        rd = road_distances.get(key, 99999)
        # Append road distance (meters, rounded) to the compact array
        arr.append(round(rd))

    # Regenerate overlay PNG with road proximity dimming
    print("Regenerating overlay with road proximity...")

    n_rows = meta["rows"]
    n_cols = meta["cols"]
    img = Image.new("RGBA", (n_cols, n_rows), (0, 0, 0, 0))
    pixels = img.load()

    for key, arr in grid.items():
        img_row, col = map(int, key.split(","))
        margin = arr[1]
        rd = arr[-1]  # road distance is the last element
        color = margin_to_color(margin, rd)
        pixels[col, img_row] = color

    img.save(DATA_DIR / "overlay.png")
    print(f"  Saved updated overlay.png")

    # Save updated grid data
    compact_json = json.dumps({"meta": meta, "grid": grid}, separators=(",", ":"))
    with gzip.open(DATA_DIR / "grid_data.json.gz", "wb") as f:
        f.write(compact_json.encode())

    size_mb = (DATA_DIR / "grid_data.json.gz").stat().st_size / 1024 / 1024
    print(f"  Saved updated grid_data.json.gz ({size_mb:.1f} MB)")

    # Print stats
    dists = list(road_distances.values())
    roadside = sum(1 for d in dists if d <= ROADSIDE)
    walk = sum(1 for d in dists if ROADSIDE < d <= SHORT_WALK)
    hike = sum(1 for d in dists if SHORT_WALK < d <= HIKE)
    remote = sum(1 for d in dists if d > HIKE)

    print()
    print(f"Road proximity stats ({len(dists)} cells):")
    print(f"  Roadside (<{ROADSIDE}m):    {roadside:>7} ({100*roadside/len(dists):.1f}%)")
    print(f"  Short walk (<{SHORT_WALK}m): {walk:>7} ({100*walk/len(dists):.1f}%)")
    print(f"  Hike (<{HIKE}m):        {hike:>7} ({100*hike/len(dists):.1f}%)")
    print(f"  Remote (>{HIKE}m):       {remote:>7} ({100*remote/len(dists):.1f}%)")
    print()
    print("Done! Restart the app to see the updated map.")


if __name__ == "__main__":
    main()
