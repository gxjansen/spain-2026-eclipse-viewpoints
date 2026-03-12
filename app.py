#!/usr/bin/env python3
"""Serve the eclipse terrain obstruction map."""

from pathlib import Path
from flask import Flask, send_from_directory, render_template, Response

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data"


@app.route("/")
def index():
    return render_template("index.html")


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
