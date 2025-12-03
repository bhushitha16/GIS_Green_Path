# Green Path Routing — Bengaluru

A GIS-based system that computes the **greenest** path across Bengaluru using **NDVI**, **AQI**, and **road network analysis**.  
The project builds an environmental road graph and provides a **Streamlit-based interactive map** to compare:

- Shortest Route
- Greenest Route (NDVI + AQI weighted)
- Parks, EV Charging Stations, and Metro Stations along the route

---

## Project Structure

```plaintext

GIS_Green_Path/
│
├── eco_graph_builder.py # Builds environmental graph (NDVI + AQI)
├── generate_pois.py # Generates parks, EV, metro POIs
├── app.py # Streamlit UI for interactive routing
├── readme.md # Project documentation
│
├── app/
│ └── requirements.txt # Dependencies for Streamlit app
│
└── data/
├── raw/
│ └── sentinel_ndvi/
│ └── ndvi_bengaluru_2024.tiff # NDVI raster (not included in repo)
│
├── pois/ # Generated POIs (must exist)
│ ├── parks.geojson
│ ├── ev.geojson
│ └── metro.geojson
│
└── processed/
├── edges_ndvi_aqi.geojson
├── edges_ndvi_aqi.csv
└── roads_with_env.graphml
```

> **Important:** > `data/raw/` is NOT included in GitHub because NDVI TIFFs are too large.
> Users must place the NDVI TIFF manually.

---

## Step 1 — Install Dependencies

Create a virtual environment:

python -m venv venv
source venv/bin/activate # Mac/Linux
venv\Scripts\activate # Windows

pip install -r app/requirements.txt

## Step 2 — Prepare Required Data

Download NDVI Raster
Place this file manually:

data/raw/sentinel_ndvi/ndvi_bengaluru_2024.tiff
This file is exported from Google Earth Engine as NDVI.

## Step 3 — Generate POIs

Run:

python generate_pois.py
This creates:

data/pois/parks.geojson
data/pois/ev.geojson
data/pois/metro.geojson
These are required by the Streamlit UI.

## Step 4 — Build Environmental Graph

Run:

python eco_graph_builder.py
This script:

Loads NDVI raster

Downloads Bengaluru road network from OSM

Fetches real AQI from multiple WAQI stations

Assigns NDVI + AQI to each road segment

Saves the enriched graph:

data/processed/roads_with_env.graphml

## Step 5 — Run the Streamlit App

Start the UI:

streamlit run app.py

You can now:

Click to select Origin and Destination

View:

Shortest path (red)

Greenest path (green)

See POIs near the route (parks, EV chargers, metro)

Compare route metrics (distance, NDVI, AQI)

Greenest Route Logic
Each road segment has three attributes:

Attribute Description
length Road length in meters
ndvi Greenness from -1 to +1
aqi Pollution level from WAQI nearest station

Hybrid cost used in routing:

green*cost = 0.7 * NDVI*cost + 0.3 * AQI_cost
This means:

Routes with more greenery get rewarded

Roads with higher pollution get penalized
