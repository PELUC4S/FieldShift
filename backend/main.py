import json
from pathlib import Path
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from analysis.indicators import calcular_indicadores
from services.nasa_power import obtener_clima

INICIO, FIN = "20050101", "20251231"
DATA = Path(__file__).parent / "data"
CACHE = DATA / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="FieldShift AR")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/climate")
def climate(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    lat, lon = round(lat, 2), round(lon, 2)
    archivo = CACHE / f"clima_{lat}_{lon}.json"
    if archivo.exists():
        return json.loads(archivo.read_text(encoding="utf-8"))
    try:
        df = obtener_clima(lat, lon, INICIO, FIN)
    except (requests.RequestException, KeyError) as e:
        raise HTTPException(status_code=502, detail=f"No se pudo obtener datos de NASA POWER: {e}")
    
    resultado = calcular_indicadores(df, lat, lon, INICIO, FIN)
    archivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultado

class Prioridades(BaseModel):
    water_availability: int = 3
    climate_risk: int = 3
    soil_cover: int = 3
    diversification: int = 3
    nitrogen: int = 3

class CompareRequest(BaseModel):
    latitude: float
    longitude: float
    current_rotation: list[str]
    priorities: Prioridades = Prioridades()

@app.post("/compare")
def compare(req: CompareRequest):
    return json.loads((DATA / "mock_comparison.json").read_text(encoding="utf-8"))
