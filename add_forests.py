#!/usr/bin/env python3
"""
Add forest/woodland data to the eclipse grid.

Downloads forest polygons from OpenStreetMap (Overpass API), rasterizes them
onto the grid, and flags cells that fall inside forested areas.

Usage:
  uv run add_forests.py
"""

import gzip
import json
import math
import time
import urllib.request
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw
from tqdm import tqdm

DATA_DIR = Path(__file__).parent / "data"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def query_overpass(bbox, retries=3):
    """Query Overpass API for forest/woodland polygons in a bounding box."""
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:180];
    (
      way["landuse"="forest"]({south},{west},{north},{east});
      way["natural"="wood"]({south},{west},{north},{east});
      relation["landuse"="forest"]({south},{west},{north},{east});
      relation["natural"="wood"]({south},{west},{north},{east});
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
            with urllib.request.urlopen(req, timeout=240) as resp:
                result = json.loads(resp.read())
            return result.get("elements", [])
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 15
                print(f"    Retry in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"    Failed after {retries} attempts: {e}")
                return []


def close_enough(p1, p2, eps=1e-6):
    """Check if two lat/lon points are effectively the same."""
    return abs(p1[0] - p2[0]) < eps and abs(p1[1] - p2[1]) < eps


def join_ways(ways):
    """Join ways that share endpoints into closed rings."""
    if not ways:
        return []

    remaining = [list(w) for w in ways]
    rings = []

    while remaining:
        current = remaining.pop(0)
        changed = True
        while changed:
            changed = False
            for i, way in enumerate(remaining):
                if close_enough(current[-1], way[0]):
                    current.extend(way[1:])
                    remaining.pop(i)
                    changed = True
                    break
                elif close_enough(current[-1], way[-1]):
                    current.extend(list(reversed(way))[1:])
                    remaining.pop(i)
                    changed = True
                    break
                elif close_enough(current[0], way[-1]):
                    current = way[:-1] + current
                    remaining.pop(i)
                    changed = True
                    break
                elif close_enough(current[0], way[0]):
                    current = list(reversed(way))[:-1] + current
                    remaining.pop(i)
                    changed = True
                    break
        rings.append(current)

    return rings


def extract_polygons(elements):
    """Extract polygon rings from OSM elements.

    Returns (outer_rings, hole_rings) where each ring is [(lat, lon), ...].
    """
    outers = []
    holes = []

    for el in elements:
        if el.get("type") == "way" and "geometry" in el:
            geom = el["geometry"]
            if len(geom) >= 3:
                ring = [(n["lat"], n["lon"]) for n in geom]
                outers.append(ring)

        elif el.get("type") == "relation" and "members" in el:
            outer_ways = []
            inner_ways = []
            for member in el["members"]:
                if member.get("type") != "way" or "geometry" not in member:
                    continue
                geom = [(n["lat"], n["lon"]) for n in member["geometry"]]
                if len(geom) < 2:
                    continue
                if member.get("role") == "inner":
                    inner_ways.append(geom)
                else:
                    outer_ways.append(geom)

            for ring in join_ways(outer_ways):
                if len(ring) >= 3:
                    outers.append(ring)
            for ring in join_ways(inner_ways):
                if len(ring) >= 3:
                    holes.append(ring)

    return outers, holes


def ring_to_pixels(ring, meta):
    """Convert a lat/lon ring to pixel coordinates on the grid."""
    points = []
    for lat, lon in ring:
        px_col = (lon - meta["lon_min"]) / meta["step"]
        px_row = meta["rows"] - 1 - (lat - meta["lat_min"]) / meta["step"]
        points.append((px_col, px_row))
    return points


def main():
    print("=" * 60)
    print("Adding forest/woodland data to eclipse grid")
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
    rows = meta["rows"]
    cols = meta["cols"]
    print(f"  {len(grid)} grid cells, {cols}x{rows} grid")

    # Find bounding box of cells with data
    lats, lons = [], []
    for key in grid:
        img_row, col = map(int, key.split(","))
        row_from_south = rows - 1 - img_row
        lat = meta["lat_min"] + row_from_south * meta["step"]
        lon = meta["lon_min"] + col * meta["step"]
        lats.append(lat)
        lons.append(lon)

    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    print(f"  Data bounds: {lat_min:.1f}-{lat_max:.1f}°N, "
          f"{lon_min:.1f}-{lon_max:.1f}°E")

    # Download forest data in 1° x 2° chunks
    print()
    print("Downloading forest data from OpenStreetMap...")

    all_outers = []
    all_holes = []
    chunks = []
    for lat in range(int(math.floor(lat_min)), int(math.ceil(lat_max))):
        for lon in range(int(math.floor(lon_min)),
                         int(math.ceil(lon_max)), 2):
            lon_end = min(lon + 2, int(math.ceil(lon_max)) + 1)
            chunks.append((lat, lon, lat + 1, lon_end))

    print(f"  {len(chunks)} chunks to download")

    for i, (s, w, n, e) in enumerate(tqdm(chunks, desc="  Downloading")):
        elements = query_overpass((s, w, n, e))
        outers, holes = extract_polygons(elements)
        all_outers.extend(outers)
        all_holes.extend(holes)
        # Be nice to the API
        if i < len(chunks) - 1:
            time.sleep(3)

    print(f"  {len(all_outers)} outer polygons, {len(all_holes)} holes")

    if not all_outers:
        print("Warning: No forest data downloaded.")
        print("Skipping forest layer.")
        return

    # Rasterize forest polygons onto the grid
    print()
    print("Rasterizing forest polygons onto grid...")

    mask = Image.new("L", (cols, rows), 0)
    draw = ImageDraw.Draw(mask)

    # Draw outer polygons (filled)
    for ring in tqdm(all_outers, desc="  Outer rings"):
        pixels = ring_to_pixels(ring, meta)
        if len(pixels) >= 3:
            draw.polygon(pixels, fill=255)

    # Erase holes
    for ring in tqdm(all_holes, desc="  Hole rings"):
        pixels = ring_to_pixels(ring, meta)
        if len(pixels) >= 3:
            draw.polygon(pixels, fill=0)

    # Count forested cells
    forested = 0
    forest_flags = {}
    for key in grid:
        img_row, col = map(int, key.split(","))
        is_forest = mask.getpixel((col, img_row)) > 0
        forest_flags[key] = 1 if is_forest else 0
        if is_forest:
            forested += 1

    print(f"  {forested} cells in forest "
          f"({100 * forested / len(grid):.1f}%)")
    print(f"  {len(grid) - forested} cells not in forest")

    # Add forest flag to grid data
    # Append as the last element of each cell's array
    print()
    print("Updating grid data with forest info...")

    for key, arr in grid.items():
        arr.append(forest_flags[key])

    # Save updated grid data
    compact_json = json.dumps(
        {"meta": meta, "grid": grid}, separators=(",", ":"))
    with gzip.open(gz_path, "wb") as f:
        f.write(compact_json.encode())

    size_mb = gz_path.stat().st_size / 1024 / 1024
    print(f"  Saved grid_data.json.gz ({size_mb:.1f} MB)")

    print()
    print("Done! Restart the app to see the updated map.")


if __name__ == "__main__":
    main()
