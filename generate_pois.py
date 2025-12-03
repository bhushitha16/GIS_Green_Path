import os
import osmnx as ox
import geopandas as gpd

print("⏳ Fetching POIs from OpenStreetMap for Bengaluru…")

boundary = ox.geocode_to_gdf("Bengaluru, India")
polygon = boundary.geometry.iloc[0]

# Tags for all POIs we want
tags = {
    "leisure": "park",
    "amenity": "charging_station",
    "railway": ["station", "halt", "subway_entrance"],
    "public_transport": "station",
}

# Fetch features
pois = ox.features_from_polygon(polygon, tags)

os.makedirs("data/pois", exist_ok=True)

# ---- Parks ----
parks = pois[pois.get("leisure") == "park"].copy()
if not parks.empty:
    parks["geometry"] = parks.geometry.centroid
parks.to_file("data/pois/parks.geojson", driver="GeoJSON")
print(f"✔ Parks → {len(parks)} saved")

# ---- EV Charging ----
ev = pois[pois.get("amenity") == "charging_station"].copy()
if not ev.empty:
    ev["geometry"] = ev.geometry.centroid
ev.to_file("data/pois/ev.geojson", driver="GeoJSON")
print(f"✔ EV charging → {len(ev)} saved")

# ---- Metro / railway stations (wide match, not only "Metro") ----
metro = pois[
    (pois.get("railway").isin(["station", "halt", "subway_entrance"])) |
    (pois.get("public_transport") == "station")
].copy()

# Remove railway yards / depots (optional filter)
blacklist = ["yard", "depot", "workshop"]
metro = metro[~metro.get("name", "").str.contains("|".join(blacklist), case=False, na=False)]

if not metro.empty:
    metro["geometry"] = metro.geometry.centroid
metro.to_file("data/pois/metro.geojson", driver="GeoJSON")
print(f"✔ Metro stations → {len(metro)} saved")

print("\nPOI dataset generated successfully.\nRun next:")
print("   streamlit run app.py")
