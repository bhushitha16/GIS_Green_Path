import streamlit as st
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
import folium
from shapely.geometry import LineString
from shapely.ops import transform as shp_transform
from streamlit_folium import st_folium
import pyproj

# ---------- LOAD GRAPH ----------
@st.cache_resource
def load_graph():
    return ox.load_graphml("data/processed/roads_with_env.graphml")

G = load_graph()

# ---------- SANITIZE EDGE ATTRIBUTES ----------
for _, _, _, d in G.edges(keys=True, data=True):
    d["ndvi"] = float(d.get("ndvi") or 0.0)
    d["aqi"] = float(d.get("aqi") or 50.0)
    d["length"] = float(d.get("length") or 1.0)

def greenness_cost(ndvi, length):
    ndvi = max(-1, min(1, float(ndvi)))
    ndvi_norm = (ndvi + 1) / 2
    return length * (1 - ndvi_norm)

def pollution_cost(aqi, length):
    return length * (1 + float(aqi) / 100)

def hybrid_cost(ndvi, aqi, length):
    return 0.7 * greenness_cost(ndvi, length) + 0.3 * pollution_cost(aqi, length)

for _, _, _, d in G.edges(keys=True, data=True):
    d["green_cost"] = hybrid_cost(d["ndvi"], d["aqi"], d["length"])

# ---------- PROJECTIONS ----------
project_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
project_to_wgs = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform

def nearest_node(lat, lon):
    x, y = project_to_utm(lon, lat)
    return ox.distance.nearest_nodes(G, X=x, Y=y)

def compute_path(origin, destination, weight):
    o = nearest_node(*origin)
    d = nearest_node(*destination)
    return nx.shortest_path(G, o, d, weight=weight)

def path_to_wgs_linestring(path):
    coords = []
    for u, v in zip(path[:-1], path[1:]):
        edata = G.get_edge_data(u, v)[0]
        geom = edata.get("geometry", None)
        if geom:
            for x, y in geom.coords:
                lon, lat = project_to_wgs(x, y)
                coords.append((lon, lat))
    return LineString(coords)


# ---------- LOAD OFFLINE POIs ----------
@st.cache_resource
def load_pois():
    try:
        parks = gpd.read_file("data/pois/parks.geojson")
        ev = gpd.read_file("data/pois/ev.geojson")
        metro = gpd.read_file("data/pois/metro.geojson")
    except Exception:
        st.warning("⚠ POI files missing — run `python generate_pois.py` to create them.")
        return (
            gpd.GeoDataFrame(geometry=[]),
            gpd.GeoDataFrame(geometry=[]),
            gpd.GeoDataFrame(geometry=[]),
        )
    return parks, ev, metro

parks_all, ev_all, metro_all = load_pois()

def pois_along_route(line_wgs, buffer_m=300):
    if line_wgs is None or line_wgs.is_empty:
        return gpd.GeoDataFrame(), gpd.GeoDataFrame(), gpd.GeoDataFrame()

    line_utm = shp_transform(lambda x, y: project_to_utm(x, y), line_wgs)
    buffer_utm = line_utm.buffer(buffer_m)

    result = []
    for gdf in (parks_all, ev_all, metro_all):
        if gdf.empty:
            result.append(gpd.GeoDataFrame(columns=gdf.columns))
            continue
        gdf_utm = gdf.to_crs(32643)
        sel = gdf_utm[gdf_utm.intersects(buffer_utm)].copy()
        sel = sel.to_crs(4326)
        result.append(sel)

    return result  # (parks_near, ev_near, metro_near)


# ---------- STREAMLIT UI ----------
st.title("Green Routing System — Bengaluru")
st.caption("Click on the map to choose **Origin** and **Destination**. "
           "Shortest vs Greenest route + nearby parks / EV stations / metro.")

if "points" not in st.session_state:
    st.session_state.points = []

# Base map
m = folium.Map(location=(12.9716, 77.5946), zoom_start=13)
m.add_child(folium.LatLngPopup())

# Draw stored markers
for i, (lat, lon) in enumerate(st.session_state.points):
    folium.Marker(
        [lat, lon],
        tooltip="Origin" if i == 0 else "Destination",
        icon=folium.Icon(color="blue" if i == 0 else "red", icon="map-marker"),
    ).add_to(m)

green_route_wgs = None

# If two points exist, compute routes + POIs
if len(st.session_state.points) == 2:
    origin, destination = st.session_state.points
    shortest = compute_path(origin, destination, "length")
    greenest = compute_path(origin, destination, "green_cost")

    short_route_wgs = path_to_wgs_linestring(shortest)
    green_route_wgs = path_to_wgs_linestring(greenest)

    folium.GeoJson(short_route_wgs, style_function=lambda x: {"color": "red", "weight": 6}).add_to(m)
    folium.GeoJson(green_route_wgs, style_function=lambda x: {"color": "green", "weight": 6}).add_to(m)

    parks_near, ev_near, metro_near = pois_along_route(green_route_wgs, buffer_m=300)

    for _, row in parks_near.iterrows():
        folium.CircleMarker([row.geometry.y, row.geometry.x], radius=4,
                            color="darkgreen", fill=True, fill_color="green",
                            tooltip=row.get("name", "Park")).add_to(m)

    for _, row in ev_near.iterrows():
        folium.CircleMarker([row.geometry.y, row.geometry.x], radius=4,
                            color="gold", fill=True, fill_color="yellow",
                            tooltip=row.get("name", "EV Charging")).add_to(m)

    for _, row in metro_near.iterrows():
        folium.CircleMarker([row.geometry.y, row.geometry.x], radius=4,
                            color="blue", fill=True, fill_color="blue",
                            tooltip=row.get("name", "Metro / Station")).add_to(m)

# ---------- MAP LEGEND ----------
legend_html = """
<div style="
    position: fixed; bottom: 30px; left: 30px; z-index: 9999; 
    background: black; padding: 12px; border-radius: 8px; 
    font-size: 15px; box-shadow: 0 0 10px rgba(0,0,0,0.4);
">
<b>Legend</b><br>
<span style="color:red;">🟥</span> Shortest Route <br>
<span style="color:green;">🟩</span> Greenest Route <br>
🔵 Origin <br>
🔴 Destination <br>
🟢 Park <br>
🟡 EV Charging Station <br>
🔷 Metro Station
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# ---------- RENDER MAP ----------
loc = st_folium(m, height=500, returned_objects=["last_clicked"])

# Save up to 2 clicks
if loc and loc.get("last_clicked"):
    if len(st.session_state.points) < 2:
        st.session_state.points.append(
            (loc["last_clicked"]["lat"], loc["last_clicked"]["lng"])
        )
        st.rerun()


# ---------- ROUTE METRICS ----------
if len(st.session_state.points) == 2:
    def metrics(path):
        lens, nds, aqis = [], [], []
        for u, v in zip(path[:-1], path[1:]):
            d = G.get_edge_data(u, v)[0]
            lens.append(d["length"])
            nds.append(d["ndvi"])
            aqis.append(d["aqi"])
        return round(sum(lens) / 1000, 2), round(np.mean(nds), 3), round(np.mean(aqis), 2)

    s_dist, s_nd, s_aqi = metrics(shortest)
    g_dist, g_nd, g_aqi = metrics(greenest)

    st.subheader("Route Comparison")
    st.table({
        "Metric": ["Distance (km)", "Avg NDVI", "Avg AQI"],
        "Shortest Route": [s_dist, s_nd, s_aqi],
        "Greenest Route": [g_dist, g_nd, g_aqi],
    })

    if st.button("Reset"):
        st.session_state.points = []
        st.rerun()
