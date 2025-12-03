#!/usr/bin/env python

"""
Build environmental routing graph for Bengaluru.

Pipeline (Option B: NDVI already downloaded):

1. Ensure folders exist.
2. Download Bengaluru boundary and road network from OSM.
3. Load NDVI raster (already downloaded from GEE).
4. Fetch AQI for Bengaluru (WAQI API).
5. Sample NDVI for each road segment + attach AQI.
6. Save:
   - data/processed/edges_ndvi_aqi.geojson
   - data/processed/roads_with_env.graphml
"""

import os
import json
import requests

import numpy as np
import geopandas as gpd
import rasterio
from shapely.geometry import Point
import osmnx as ox


# ===================== CONFIG =====================

CITY_NAME = "Bengaluru, India"
PROJECT_CRS = 32643             # UTM zone for Bengaluru
NDVI_TIF_PATH = os.path.join("data", "raw", "sentinel_ndvi", "ndvi_bengaluru_2024.tiff")

WAQI_TOKEN = "4ac1e5e056104b066950e7745a5f582b8f00d482"
WAQI_CITY = "Bengaluru"

# ==================================================


def ensure_folders():
    """Ensure basic data folder structure exists."""
    os.makedirs(os.path.join("data", "raw"), exist_ok=True)
    os.makedirs(os.path.join("data", "raw", "sentinel_ndvi"), exist_ok=True)
    os.makedirs(os.path.join("data", "processed"), exist_ok=True)
    print("[OK] Folder structure ready.")


def download_boundary_and_roads():
    """
    Download Bengaluru boundary and a drivable road network.
    Saves:
      - data/raw/bengaluru_boundary.geojson
      - data/processed/bengaluru_boundary_utm.geojson
      - data/processed/roads_base_utm.graphml
    """
    print(f"[INFO] Downloading boundary for {CITY_NAME}...")
    boundary = ox.geocode_to_gdf(CITY_NAME)
    boundary.to_file(os.path.join("data", "raw", "bengaluru_boundary.geojson"),
                     driver="GeoJSON")
    print("[OK] Saved data/raw/bengaluru_boundary.geojson")

    boundary_utm = boundary.to_crs(PROJECT_CRS)
    boundary_utm.to_file(os.path.join("data", "processed", "bengaluru_boundary_utm.geojson"),
                         driver="GeoJSON")
    print("[OK] Saved data/processed/bengaluru_boundary_utm.geojson")

    print("[INFO] Downloading OSM road network (driveable roads)...")
    # Slightly restricted set of highways for speed
    G = ox.graph_from_polygon(
        boundary.geometry.iloc[0],
        custom_filter='["highway"~"motorway|trunk|primary|secondary|tertiary|residential"]'
    )
    print(f"[OK] Downloaded graph: {len(G.nodes)} nodes | {len(G.edges)} edges")

    print("[INFO] Projecting road graph to UTM...")
    G_proj = ox.project_graph(G, to_crs=PROJECT_CRS)
    graph_path = os.path.join("data", "processed", "roads_base_utm.graphml")
    ox.save_graphml(G_proj, graph_path)
    print(f"[OK] Saved {graph_path}")

    return graph_path


def load_graph(graph_path):
    print(f"[INFO] Loading graph from {graph_path}...")
    G = ox.load_graphml(graph_path)
    nodes, edges = ox.graph_to_gdfs(G)
    print(f"[OK] Loaded: {len(nodes)} nodes | {len(edges)} edges")
    return G, nodes, edges


def check_ndvi_file():
    if not os.path.exists(NDVI_TIF_PATH):
        raise FileNotFoundError(
            f"NDVI file not found at {NDVI_TIF_PATH}\n"
            "Make sure you downloaded it from Google Drive and placed it here."
        )
    print(f"[OK] Found NDVI raster at {NDVI_TIF_PATH}")


def sample_ndvi_for_edges(edges):
    """
    Sample NDVI value at the centroid of each edge geometry.
    Assumes NDVI raster is in EPSG:4326.
    """
    print("[INFO] Sampling NDVI for each road segment...")

    # Reproject edges to match NDVI CRS (assumed 4326 from GEE export)
    edges_4326 = edges.to_crs(4326)

    with rasterio.open(NDVI_TIF_PATH) as src:
        ndvi = src.read(1)
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999

    edges_4326["centroid"] = edges_4326.geometry.centroid

    def sample_point(point):
        col, row = ~transform * (point.x, point.y)
        row, col = int(row), int(col)
        if 0 <= row < ndvi.shape[0] and 0 <= col < ndvi.shape[1]:
            value = ndvi[row, col]
            if value == nodata or np.isnan(value):
                return np.nan
            return float(value)
        return np.nan

    edges_4326["ndvi"] = edges_4326["centroid"].apply(sample_point).fillna(0)

    # Drop helper column
    edges_4326 = edges_4326.drop(columns=["centroid"])

    # Back to project CRS
    edges_proj = edges_4326.to_crs(PROJECT_CRS)
    print("[OK] NDVI sampling complete.")
    return edges_proj


def fetch_city_aqi():
    """
    Fetch AQI for the city using WAQI API.
    Returns a single numeric AQI value (simple model).
    """
    if WAQI_TOKEN == "YOUR_WAQI_TOKEN_HERE":
        raise ValueError(
            "Please set WAQI_TOKEN in this script with your actual WAQI API token."
        )

    print(f"[INFO] Fetching AQI for {WAQI_CITY} from WAQI API...")
    url = f"https://api.waqi.info/feed/{WAQI_CITY}/?token={WAQI_TOKEN}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"WAQI API error: {data}")

    aqi = data["data"]["aqi"]
    print(f"[OK] AQI for {WAQI_CITY}: {aqi}")
    # Save raw JSON for reference
    with open(os.path.join("data", "raw", "aqi_raw.json"), "w") as f:
        json.dump(data, f, indent=2)
    print("[OK] Saved data/raw/aqi_raw.json")
    return aqi


def add_aqi_and_length(edges, aqi_value):
    """
    Attach AQI and length to edges.
    AQI: same value for all edges (simple model).
    Length: computed from geometry in project CRS.
    """
    print("[INFO] Adding AQI and length attributes...")
    edges["aqi"] = float(aqi_value)
    # ensure project CRS for length
    edges = edges.to_crs(PROJECT_CRS)
    edges["length"] = edges.geometry.length
    print("[OK] AQI and length added.")
    return edges


def save_outputs(G, edges):
    """
    Save merged edge layer + environmental graph.
    """
    # Save GeoJSON and CSV of edges
    edges_out = os.path.join("data", "processed", "edges_ndvi_aqi.geojson")
    edges.to_file(edges_out, driver="GeoJSON")
    print(f"[OK] Saved {edges_out}")

    edges_csv = os.path.join("data", "processed", "edges_ndvi_aqi.csv")
    edges.to_csv(edges_csv, index=False)
    print(f"[OK] Saved {edges_csv}")

    # Inject attributes into graph
    print("[INFO] Injecting NDVI, AQI, length into graph edges...")
    # Make sure graph is in same CRS as edges
    G = ox.project_graph(G, to_crs=PROJECT_CRS)

    # edges GeoDataFrame index should be (u,v,key) if coming from graph_to_gdfs
    if not {"u", "v", "key"}.issubset(edges.columns):
        # If they are in index, restore as columns
        try:
            edges = edges.reset_index()
        except Exception as e:
            raise RuntimeError(
                "Could not find u, v, key columns on edges. "
                "Check how graph_to_gdfs was called."
            ) from e

    for _, row in edges.iterrows():
        u, v, k = row["u"], row["v"], row["key"]
        if G.has_edge(u, v, k):
            G[u][v][k]["ndvi"] = float(row["ndvi"])
            G[u][v][k]["aqi"] = float(row["aqi"])
            G[u][v][k]["length"] = float(row["length"])

    graph_env_path = os.path.join("data", "processed", "roads_with_env.graphml")
    ox.save_graphml(G, graph_env_path)
    print(f"[OK] Saved {graph_env_path}")


def main():
    print("=== Environmental Graph Builder (Bengaluru) ===")

    ensure_folders()
    check_ndvi_file()

    # 1. Download / load base roads
    graph_path = os.path.join("data", "processed", "roads_base_utm.graphml")
    if not os.path.exists(graph_path):
        graph_path = download_boundary_and_roads()
    else:
        print(f"[OK] Found existing {graph_path}, reusing.")

    G, nodes, edges = load_graph(graph_path)

    # 2. Sample NDVI
    edges_with_ndvi = sample_ndvi_for_edges(edges)

    # 3. Fetch AQI
    aqi_value = fetch_city_aqi()

    # 4. Attach AQI + length
    edges_final = add_aqi_and_length(edges_with_ndvi, aqi_value)

    # 5. Save outputs and enriched graph
    save_outputs(G, edges_final)

    print("\n=== DONE ===")
    print("You can now use 'data/processed/roads_with_env.graphml' in your routing notebook.")


if __name__ == "__main__":
    main()
