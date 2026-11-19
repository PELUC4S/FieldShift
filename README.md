# FieldShift 🌾 — Probabilistic Crop Rotation Prediction
### Official NASA Space Apps Challenge: *"Field Shift: Adapting Farms with NASA Data"*

**FieldShift** is an AgTech decision support web platform that combines Earth observations and **NASA** APIs (climatology, surface and root-zone soil moisture, satellite water balance, and vegetation) with an agronomic inference engine to:

1. **Probabilistically evaluate and rank (0–100%)** the suitability of 15 agricultural crops based on the field's exact GPS coordinates and the sowing window.
2. **Monitor real-time satellite soil conditions:** Surface moisture for seedbed conditions ($0\text{--}5\text{ cm}$) and root-zone water storage ($0\text{--}100\text{ cm}$) via NASA SMAP, 30-day water balance via NASA GPM / OpenET, and NDVI vegetation index via NASA HLS.
3. **Optimize the crop rotation sequence**, allowing users to enter predecessor crops to calculate the most recommended crop for the next cycle (nitrogen balance, grass-legume alternation, monoculture disruption, and botanical family rotation).

---

## 🏛️ System Architecture

```
FieldShift/
├── backend/
│   ├── main.py                  # FastAPI REST API + Security middlewares + Static server
│   ├── analysis/
│   │   └── indicators.py        # Monthly normals, frost probability, and rainfall anomaly calculations
│   ├── engine/
│   │   ├── crop_ranker.py       # Probabilistic agroclimatic fitness and predecessor scoring engine
│   │   └── rotation.py          # Multi-year succession rotation engine with history tracking
│   ├── services/
│   │   ├── nasa_harvester.py    # NASA telemetry service (SMAP, GPM IMERG, OpenET, HLS)
│   │   ├── nasa_power.py        # NASA POWER agroclimatic integration (with contingency fallback)
│   │   ├── soilgrids_service.py # ISRIC SoilGrids v2.0 REST client & pedological profiler
│   │   └── info_semillas.json   # Agronomic catalog of 15 calibrated crops
│   └── data/
│       ├── cache/               # Bounded LRU cache per spatial cell (climate and satellite)
│       └── mock_comparison.json # Benchmark comparison data
├── frontend/
│   └── index.html               # Modern interactive SPA (Leaflet.js, OpenStreetMap, Esri Satellite, Carousel)
├── tests/
│   └── test_soilgrids.py        # Unit and integration test suite
├── index.html                   # Root version for direct GitHub Pages deployment
├── render.yaml                  # Infrastructure as Code Blueprint for Render cloud deployment
├── requirements.txt             # Pinned and audited Python dependencies
├── start.sh                     # Quickstart local launch script (port 8088)
└── README.md
```

---

## 🛰️ Satellite APIs & Earth Observation Data Sources

FieldShift leverages open repositories from the **NASA Earth Science Data Systems (EOSDIS)** program and associated environmental services:

| API / Data Source | Mission / Sensor | Key Variables | Agronomic Purpose |
| :--- | :--- | :--- | :--- |
| **NASA POWER API** | MERRA-2 / GEWEX (`power.larc.nasa.gov`) | `T2M`, `T2M_MIN`, `T2M_MAX`, `PRECTOTCORR`, `ALLSKY_SFC_SW_DWN` | 20-year daily historical time series to calculate monthly normals, agrometeorological frost probability ($T_{\min} \le 0^\circ\text{C}$), heat stress ($T_{\max} \ge 35^\circ\text{C}$), and interannual rainfall variability. |
| **NASA SMAP L3** | SMAP Enhanced Radiometer (9 km) | Surface soil moisture ($0\text{--}5\text{ cm}$) in $\text{m}^3/\text{m}^3$ and % | Real-time seedbed diagnosis to ensure uniform germination and emergence. |
| **NASA SMAP L4** | SMAP SPL4SMGP Global (9 km) | Root-zone soil moisture ($0\text{--}100\text{ cm}$) and $Z$-score anomaly | Plant-available water storage in the first meter of soil to sustain crop evapotranspiration. |
| **NASA GPM IMERG** | GPM Core Observatory (0.1° / 10 km) | Daily accumulated precipitation (`precipitationCal` in mm) | Measurement of recent water supply (last 30 days) and seasonal drought index `SPI-3` computation. |
| **OpenET API** | Ensemble Landsat / Sentinel-2 (30 m) | Actual Evapotranspiration ($ET_a$ in mm) | Quantification of actual water consumption by predecessor crops and net water balance ($P - ET_a$). |
| **ISRIC SoilGrids v2.0** | Global Soil Information System (250 m) | Texture (`clay`, `silt`, `sand`), Water retention (`wv0033`, `wv1500`), Organic Matter (`soc`), and Acidity (`phh2o`) | Quantification of Available Water Capacity ($AWC_{0-30\text{cm}}$ and $AWC_{30-60\text{cm}}$), winter drought buffering, biological nitrogen fertility, and soil suitability. |
| **NASA HLS & ECOSTRESS** | Landsat 8/9, Sentinel-2, and ISS (30 m / 70 m) | Vegetation Index `NDVI` and Canopy Evaporative Stress Index `ESI` | Estimation of prior foliar vegetative vigor and remaining stubble/residue volume in the field. |
| **OpenStreetMap & CartoDB** | OpenStreetMap Community / CARTO | Cartographic raster tiles | Interactive basemap layer for fast, responsive field geolocation. |
| **Esri World Imagery** | Esri / Maxar / Earthstar Geographics | High-resolution satellite orthophotos | Visual satellite inspection of farm parcels and field boundaries. |
| **W3C Geolocation API** | Web Browser (`navigator.geolocation`) | GPS Latitude and Longitude | One-click automatic detection of the farmer's location in the field. |

---

## 📡 REST API Endpoints (FastAPI Backend)

The API runs locally by default on `http://localhost:8088`. Interactive Swagger documentation is available at `/docs` (in development mode).

### 1. Comprehensive Analytics & Satellite Telemetry
* **`GET /api/v1/field-analytics`**: Returns the complete field analytics contract: geographic metadata, soil profile, NASA telemetry (SMAP, GPM, OpenET, HLS), predecessor history, ranking, and recommended succession rotation with agronomic justification.  
  *Query params:* `lat`, `lon`, `planting_month` (1-12), `cycle_months` (2-8), `num_cycles` (1-10), `previous_crops` (e.g. `soja,trigo,maiz`).
* **`GET /api/v1/satellite-readings`**: Real-time satellite readings for soil moisture, water balance, `SPI-3` drought index, and vegetative vigor per spatial cell with local caching.  
  *Query params:* `lat`, `lon`.
* **`GET /api/v1/soil-profile`**: Normalized ISRIC SoilGrids v2.0 pedological profile (USDA texture class, clay/silt/sand percentages in $0\text{--}30\text{ cm}$, available water capacity $AWC$ in $0\text{--}30\text{ cm}$ and $30\text{--}60\text{ cm}$, soil organic carbon $SOC$, and $\text{pH}$).  
  *Query params:* `lat`, `lon`.

### 2. Crop Prediction & Rotation Sequence
* **`GET /api/v1/ranking`**: Probabilistic ranking (0 to 100%) for the 15 supported crops evaluating climate suitability and succession rotation bonuses against predecessor crops.  
  *Query params:* `lat`, `lon`, `planting_month`, `cycle_months`, `previous_crops`.
* **`POST /api/v1/rotation`**: Generates an agronomically optimized multi-cycle rotation sequence (grass $\leftrightarrow$ legume alternation, N replenishment, monoculture disruption).  
  *JSON Body:* `{"latitude": -31.42, "longitude": -64.18, "start_month": 10, "num_cycles": 3, "previous_crops": ["soja", "trigo"]}`.
* **`GET /api/v1/next-crop`**: Direct recommendation for the immediate successor crop following a specific harvested predecessor.  
  *Query params:* `predecessor`, `planting_month`, `lat`, `lon`, `cycle_months`.

### 3. Crop Catalog & Climatology
* **`GET /api/v1/crops`**: Full catalog of the 15 agronomic seeds with thermal and hydric requirements, frost/heat tolerance, soil nitrogen effects, and compatibility rules.
* **`GET /api/v1/climate`** or **`GET /climate`**: 20-year NASA POWER historical agroclimatic indicators (normal temperatures, annual rainfall, agrometeorological frost days, heat stress days, and integrated satellite readings).  
  *Query params:* `lat`, `lon`.
* **`POST /compare`**: Comparative simulation of rotation scenarios and agronomic priorities.
* **`GET /health`**: Fast health check and operational status of the service (`{"status": "ok"}`).

---

## 🛡️ Security & Hardening Architecture

FieldShift complies with production pre-launch security standards:

* **Sliding Window Rate Limiter:** Protects endpoints at 60 requests/minute per IP, mitigating abuse and DoS attacks (returns `HTTP 429` with `Retry-After: 60`).
* **Active Bot & Scanner Protection:** Middleware automatically intercepts and blocks common automated vulnerability scanners (`sqlmap`, `nikto`, `masscan`, `nmap`, `dirbuster`, `nuclei`) returning `HTTP 403 Forbidden`.
* **Input Validation & Extra Field Rejection:** Strict Pydantic models with `ConfigDict(extra="forbid")` reject parameter tampering, unauthorized payload injection, and out-of-range coordinates (`HTTP 422`).
* **Bounded LRU Disk Cache:** Cache size capped at 500 files with automated LRU eviction to prevent storage and inode exhaustion DoS.
* **Restricted CORS & Security Headers:** Explicit origin whitelist (`peluc4s.github.io`, `fieldshift-api.onrender.com`, controlled local ports) without wildcard credentials (`allow_credentials=False`). All HTTP responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, and `Permissions-Policy`.
* **DOM XSS Sanitization:** All dynamic user and telemetry data rendered into the DOM are sanitized via contextual HTML entity escaping.
* **Safe Error Handling:** Production exception handling traps unexpected server errors, logging details securely on the server while returning sanitized, generic responses (`HTTP 500`) to the client without leaking internal stack traces.
* **Production Debug Isolation:** Interactive Swagger docs (`/docs`, `/redoc`, `/openapi.json`) are disabled automatically in production (`ENVIRONMENT=production` or `RENDER=true`).
* **Pinned Dependencies:** All runtime libraries in `requirements.txt` are pinned to exact audited versions to prevent supply chain drift.

---

## 🌾 Agronomic Catalog of Supported Crops

The system evaluates 15 strategic agricultural crops calibrated with INTA data and agronomic literature:

* **Grasses / Cereals:** Corn (`maiz`), Wheat (`trigo`), Sorghum (`sorgo`), Barley (`cebada`), Oats (`avena`), Cover Rye (`centeno_cobertura`).
* **Legumes / N-Fixers:** Soybean (`soja`), Chickpea (`garbanzo`), Peanut (`mani`), Pea (`arveja`), Vetch (`vicia`).
* **Oilseeds & Specialty Crops:** Sunflower (`girasol`), Rapeseed / Canola (`colza`), Cotton (`algodon`), Potato (`papa`).

---

## 💻 Local Setup & Installation

### Requirements
* Python 3.9 or higher.

### Quick Start
1. **Clone the repository:**
   ```bash
   git clone https://github.com/PELUC4S/FieldShift.git
   cd FieldShift
   ```

2. **Install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Run automated test suite:**
   ```bash
   python -m unittest tests/test_soilgrids.py
   ```

4. **Start the server:**
   ```bash
   ./start.sh
   # Or manually:
   uvicorn backend.main:app --host 127.0.0.1 --port 8088 --reload
   ```

5. **Open in your browser:**
   * **Web Application:** [http://localhost:8088](http://localhost:8088)
   * **Interactive Swagger Docs (Dev only):** [http://localhost:8088/docs](http://localhost:8088/docs)

---

## 🌐 Production Deployments & Cloud Architecture

FieldShift utilizes a robust hybrid deployment:

* **Frontend SPA (GitHub Pages):** [https://peluc4s.github.io/FieldShift/](https://peluc4s.github.io/FieldShift/)
  * Deployed directly from the `main` branch.
  * Connects over HTTPS to the cloud backend API, with automatic fallback to its built-in client-side engine if offline.
* **Backend REST API (Render):** [https://fieldshift-api.onrender.com](https://fieldshift-api.onrender.com)
  * Managed via [render.yaml](render.yaml) Infrastructure as Code (`runtime: python`, health check at `/health`).
  * Deploys automatically on git pushes to `version2` and `main`.
