# Spain 2026 Eclipse Viewpoints

Interactive map showing where in Spain you can see the **August 12, 2026 total solar eclipse** without mountains blocking the view.

The sun will be only ~5° above the horizon during totality in NE Spain, making terrain obstruction a real concern. This tool computes line-of-sight from every point in the totality zone and shows which locations have a clear view of the sun throughout the entire eclipse.

![Eclipse viewer map showing terrain analysis with optimal viewing spots](screenshot.png)

## What it does

- Downloads 30m-resolution elevation data (SRTM1) for the totality zone in Spain
- Computes sun/moon positions using PyEphem to find exact totality windows
- Casts rays along the sun's azimuth through the DEM to detect terrain obstructions
- Accounts for Earth curvature and atmospheric refraction
- Overlays road proximity data from OpenStreetMap to filter for accessible locations
- Detects forest/woodland areas from OpenStreetMap to exclude tree-blocked spots
- Marks optimal viewing spots (clear view + near a road + not in forest) with star markers

## Quick start

Pre-computed data is included, so you can view the map immediately:

```bash
# Install dependencies
uv sync

# Start the map viewer
uv run app.py
```

Open `http://localhost:8026` in your browser.

## Recomputing from scratch

If you want to regenerate the data (takes ~70 minutes total):

```bash
# Step 1: Download elevation data and compute terrain analysis (~15 min)
uv run prepare.py

# Step 2: Add road proximity data from OpenStreetMap (~25 min)
uv run add_roads.py

# Step 3: Add forest/woodland detection from OpenStreetMap (~25 min)
uv run add_forests.py

# Step 4: View the result
uv run app.py
```

## Map features

- **Visibility overlay**: Blue = clear view (4 shades by margin), orange/red = blocked by terrain (toggleable)
- **Road reachability overlay**: Hatching pattern showing distance to nearest road (toggleable)
- **Star markers**: Optimal spots combining clear view (3°+ margin), road access, and no forest — adjustable cluster spacing slider
- **Forest detection**: Cells inside mapped forest/woodland are flagged and excluded from optimal spots
- **Search**: Find places by name (top-left search box)
- **Click**: Get detailed info for any point (totality times, sun position, margin, road distance, forest status)
- **Google Maps link**: Open clicked location directly in Google Maps with a pin
- **Persistent markers**: Right-click to place named, draggable markers — survive page refresh
- **Layer toggle**: Switch between street map and terrain view

## Color scale

| Color | Meaning |
|-------|---------|
| Deep blue | Clear view, 3°+ margin above terrain |
| Medium blue | Clear view, 2-3° margin |
| Light blue | Clear view, 1-2° margin |
| Pale blue | Clear view, 0-1° margin (tight) |
| Orange | Partially blocked during totality |
| Red | Fully blocked by terrain |

Road reachability is shown as a separate hatching overlay (no hatching = roadside, sparse stripes = short walk, dense stripes = remote).

## How it works

For each 0.01° grid cell (~1.1 km) in the totality zone:

1. **Find totality**: Scan for the time window when the moon fully covers the sun
2. **Sample sun positions**: Take 7 time samples across the totality window
3. **Cast rays**: For each sample, cast a ray along the sun's azimuth through the elevation model (up to 50 km)
4. **Compute margin**: The difference between the sun's altitude and the maximum terrain angle along the ray
5. **Earth curvature**: Apply correction with atmospheric refraction (k = 7/6)
6. **Road proximity**: Find distance to nearest road using a KD-tree over OpenStreetMap road data
7. **Forest detection**: Rasterize OSM forest/woodland polygons onto the grid to flag tree-covered areas

## Data sources

- **Elevation**: [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (SRTM1, 30m resolution)
- **Eclipse computation**: [PyEphem](https://rhodesmill.org/pyephem/)
- **Road + forest data**: [OpenStreetMap](https://www.openstreetmap.org/) via [Overpass API](https://overpass-api.de/)
- **Base maps**: OpenStreetMap and OpenTopoMap
- **Geocoding**: Nominatim (OSM)

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## License

MIT
