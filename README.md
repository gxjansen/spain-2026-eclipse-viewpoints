# Spain 2026 Eclipse Viewpoints

Interactive map showing where in Spain you can see the **August 12, 2026 total solar eclipse** without mountains blocking the view.

The sun will be only ~5° above the horizon during totality in NE Spain, making terrain obstruction a real concern. This tool computes line-of-sight from every point in the totality zone and shows which locations have a clear view of the sun throughout the entire eclipse.

## What it does

- Downloads 30m-resolution elevation data (SRTM1) for the totality zone in Spain
- Computes sun/moon positions using PyEphem to find exact totality windows
- Casts rays along the sun's azimuth through the DEM to detect terrain obstructions
- Accounts for Earth curvature and atmospheric refraction
- Overlays road proximity data from OpenStreetMap to filter for accessible locations
- Marks optimal viewing spots (clear view + near a road) with star markers

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

If you want to regenerate the data (takes ~45 minutes total):

```bash
# Step 1: Download elevation data and compute terrain analysis (~15 min)
uv run prepare.py

# Step 2: Add road proximity data from OpenStreetMap (~25 min)
uv run add_roads.py

# Step 3: View the result
uv run app.py
```

## Map features

- **Color overlay**: Blue = clear view, amber = risky, orange/red = blocked by terrain
- **Road proximity**: Bright = near a road, faded = remote/inaccessible
- **Star markers**: Optimal spots combining clear view (3°+ margin) and road access
- **Search**: Find places by name (top-left search box)
- **Click**: Get detailed info for any point (totality times, sun position, margin, road distance)
- **Right-click**: Place a named, draggable marker
- **Layer toggle**: Switch between street map and terrain view

## Color scale

| Color | Meaning |
|-------|---------|
| Deep blue | Clear view, 3°+ margin above terrain |
| Medium blue | Clear view, 2-3° margin |
| Light blue | Clear view, 1-2° margin |
| Amber | Risky, 0-1° margin |
| Orange | Partially blocked during totality |
| Red | Fully blocked by terrain |

Faded/desaturated colors indicate locations far from roads.

## How it works

For each 0.01° grid cell (~1.1 km) in the totality zone:

1. **Find totality**: Scan for the time window when the moon fully covers the sun
2. **Sample sun positions**: Take 7 time samples across the totality window
3. **Cast rays**: For each sample, cast a ray along the sun's azimuth through the elevation model (up to 50 km)
4. **Compute margin**: The difference between the sun's altitude and the maximum terrain angle along the ray
5. **Earth curvature**: Apply correction with atmospheric refraction (k = 7/6)
6. **Road proximity**: Find distance to nearest road using a KD-tree over OpenStreetMap road data

## Data sources

- **Elevation**: [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (SRTM1, 30m resolution)
- **Eclipse computation**: [PyEphem](https://rhodesmill.org/pyephem/)
- **Road data**: [OpenStreetMap](https://www.openstreetmap.org/) via [Overpass API](https://overpass-api.de/)
- **Base maps**: OpenStreetMap and OpenTopoMap
- **Geocoding**: Nominatim (OSM)

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## License

MIT
