#!/usr/bin/env python

"""
Environmental Graph Builder for Bengaluru
(NDVI + AQI per region)

Pipeline:
1. Ensure folder structure
2. Download Bengaluru boundary + road network (OSM)
3. Load Sentinel NDVI raster
4. Fetch AQI from WAQI (multiple stations across Bengaluru)
5. Assign to each road segment the AQI from the nearest station
6. Save:
   - data/processed/edges_ndvi_aqi.geojson  → full geodataframe
   - data/processed/roads_with_env.graphml  → environmental routing graph
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
PROJECT_CRS = 32643  # UTM zone for Bengaluru (metric)
NDVI_TIF_PATH = os.path.join("data", "raw", "sentinel_ndvi", "ndvi_bengaluru_2024.tiff")

# WAQI Credentials
WAQI_TOKEN = "4ac1e5e056104b066950e7745a5f582b8f00d482"

# ==================================================


def ensure_folders():
    os.makedirs("data/raw", exist_ok=True)
    os.makedirs("data/raw/sentinel_ndvi", exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    print("[OK] Folder structure ready.")


def download_boundary_and_roads():
    """
    Download Bengaluru boundary and road network from OSM.
    Saves:
      data/raw/bengaluru_boundary.geojson
      data/processed/bengaluru_boundary_utm.geojson
      data/processed/roads_base_utm.graphml
    """
    print(f"[INFO] Downloading boundary for {CITY_NAME}...")
    boundary = ox.geocode_to_gdf(CITY_NAME)
    boundary.to_file("data/raw/bengaluru_boundary.geojson", driver="GeoJSON")
    print("[OK] Saved boundary.")

    boundary_utm = boundary.to_crs(PROJECT_CRS)
    boundary_utm.to_file("data/processed/bengaluru_boundary_utm.geojson", driver="GeoJSON")

    print("[INFO] Downloading driveable road network...")
    G = ox.graph_from_polygon(
        boundary.geometry.iloc[0],
        custom_filter='["highway"~"motorway|trunk|primary|secondary|tertiary|residential"]'
    )
    print(f"[OK] Downloaded graph: {len(G.nodes)} nodes | {len(G.edges)} edges")

    print("[INFO] Projecting graph to UTM...")
    G_utm = ox.project_graph(G, to_crs=PROJECT_CRS)
    graph_path = "data/processed/roads_base_utm.graphml"
    ox.save_graphml(G_utm, graph_path)
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
            f"NDVI file missing: {NDVI_TIF_PATH}.\n"
            f"Place the NDVI TIFF here and re-run the script."
        )
    print(f"[OK] Found NDVI raster: {NDVI_TIF_PATH}")


def sample_ndvi_for_edges(edges):
    """
    Sample NDVI value from raster at each edge centroid.
    Assumes NDVI raster is in EPSG:4326 (default GEE export).
    """
    print("[INFO] Sampling NDVI for road segments...")

    edges_ll = edges.to_crs(4326)

    with rasterio.open(NDVI_TIF_PATH) as src:
        arr = src.read(1)
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999

    edges_ll["centroid"] = edges_ll.geometry.centroid

    def sample(pt):
        col, row = ~transform * (pt.x, pt.y)
        col, row = int(col), int(row)
        if 0 <= row < arr.shape[0] and 0 <= col < arr.shape[1]:
            v = arr[row, col]
            if v == nodata or np.isnan(v):
                return np.nan
            return float(v)
        return np.nan

    edges_ll["ndvi"] = edges_ll["centroid"].apply(sample).fillna(0.0)
    edges_ll = edges_ll.drop(columns=["centroid"])

    edges_proj = edges_ll.to_crs(PROJECT_CRS)
    print("[OK] NDVI sampling complete.")
    return edges_proj


def fetch_aqi_stations():
    """
    Fetch multiple AQI stations in Bengaluru (WAQI) using bounding box.
    Returns list of {"aqi": int, "lat": float, "lon": float}.
    """
    print("[INFO] Fetching AQI monitoring stations across Bengaluru...")

    south, west, north, east = 12.7, 77.4, 13.2, 77.9
    url = f"https://api.waqi.info/map/bounds/?token={WAQI_TOKEN}&latlng={south},{west},{north},{east}"

    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"WAQI API error: {data}")

    stations = [
        {"aqi": s.get("aqi"), "lat": s.get("lat"), "lon": s.get("lon")}
        for s in data.get("data", [])
        if s.get("aqi") not in ["", "-", None]
    ]

    with open("data/raw/aqi_stations.json", "w") as f:
        json.dump(stations, f, indent=2)

    print(f"[OK] Found {len(stations)} AQI stations in Bengaluru")
    return stations


def add_aqi_and_length(edges, stations):
    """
    For each road segment, find the nearest AQI station and attach AQI value.
    Add road length (in meters).
    """
    print("[INFO] Attaching AQI from nearest station and computing length...")

    edges_ll = edges.to_crs(4326)
    centroids = edges_ll.geometry.centroid

    aqi_vals = []
    for p in centroids:
        best_dist = float("inf")
        best_aqi = None
        for s in stations:
            d = (p.y - s["lat"]) ** 2 + (p.x - s["lon"]) ** 2  # squared-dist for speed
            if d < best_dist:
                best_dist = d
                best_aqi = float(s["aqi"])
        aqi_vals.append(best_aqi)

    edges["aqi"] = aqi_vals
    edges["length"] = edges.to_crs(PROJECT_CRS).geometry.length
    print("[OK] AQI and length added successfully.")
    return edges


def save_outputs(G, edges):
    """Save final GraphML + GeoJSON."""
    edges_path = "data/processed/edges_ndvi_aqi.geojson"
    edges.to_file(edges_path, driver="GeoJSON")
    print(f"[OK] Saved {edges_path}")

    edges_csv = "data/processed/edges_ndvi_aqi.csv"
    edges.to_csv(edges_csv, index=False)
    print(f"[OK] Saved {edges_csv}")

    # Ensure CRS of graph
    G = ox.project_graph(G, to_crs=PROJECT_CRS)

    if not {"u", "v", "key"}.issubset(edges.columns):
        edges = edges.reset_index()

    print("[INFO] Injecting NDVI, AQI, length into graph edges...")
    for _, row in edges.iterrows():
        u, v, k = row["u"], row["v"], row["key"]
        if G.has_edge(u, v, k):
            G[u][v][k]["ndvi"] = float(row["ndvi"])
            G[u][v][k]["aqi"] = float(row["aqi"])
            G[u][v][k]["length"] = float(row["length"])

    graph_env_path = "data/processed/roads_with_env.graphml"
    ox.save_graphml(G, graph_env_path)
    print(f"[OK] Saved {graph_env_path}")


# ==================================================

def main():
    print("\n=== Environmental Graph Builder (Bengaluru) ===\n")

    ensure_folders()
    check_ndvi_file()

    graph_path = "data/processed/roads_base_utm.graphml"
    if not os.path.exists(graph_path):
        graph_path = download_boundary_and_roads()
    else:
        print("[OK] Using existing road graph.")

    G, nodes, edges = load_graph(graph_path)
    edges_ndvi = sample_ndvi_for_edges(edges)
    stations = fetch_aqi_stations()
    edges_final = add_aqi_and_length(edges_ndvi, stations)
    save_outputs(G, edges_final)

    print("\n=== DONE ===")
    print("Next step → run routing using roads_with_env.graphml")


if __name__ == "__main__":
    main()
