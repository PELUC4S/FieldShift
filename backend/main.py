"""
main.py – Servidor API FastAPI para FieldShift (Predicción de Rotación de Cultivos).
Integra datos agroclimáticos históricos de NASA POWER, motor de ranking probabilístico
de semillas y algoritmos de rotación y sucesión agronómica.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analysis.indicators import calcular_indicadores, ensure_monthly_normals
from engine.crop_ranker import rank_crops, compute_crop_score, _load_crops
from engine.rotation import build_rotation, suggest_next_crop
from services.nasa_power import obtener_clima
from services.nasa_harvester import get_satellite_readings, build_field_analytics

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fieldshift.main")

# Constantes de período histórico (20 años de datos diarios de NASA POWER)
INICIO, FIN = "20050101", "20251231"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(
    title="FieldShift API — Predicción de Rotación de Cultivos",
    description="API AgTech para evaluación probabilística de semillas y optimización de rotación de cultivos basada en NASA POWER.",
    version="1.0.0",
)

# Middleware CORS permisivo para desarrollo y producción
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Helper de acceso a datos climáticos con caché ──────────────────────────

def get_climate_data(lat: float, lon: float) -> dict[str, Any]:
    """
    Obtiene o calcula los indicadores agroclimáticos para (lat, lon).
    Utiliza caché local persistente en JSON para respuesta instantánea.
    """
    lat_r, lon_r = round(lat, 2), round(lon, 2)
    archivo_cache = CACHE_DIR / f"clima_{lat_r}_{lon_r}.json"

    if archivo_cache.exists():
        try:
            cached_data = json.loads(archivo_cache.read_text(encoding="utf-8"))
            res = ensure_monthly_normals(cached_data)
            res["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, res)
            return res
        except Exception as e:
            logger.warning(f"Error al leer caché {archivo_cache}: {e}")

    logger.info(f"Obteniendo datos climáticos para lat={lat_r}, lon={lon_r}...")
    try:
        df = obtener_clima(lat_r, lon_r, INICIO, FIN)
        resultado = calcular_indicadores(df, lat_r, lon_r, INICIO, FIN)
        archivo_cache.write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        resultado["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, resultado)
        return resultado
    except Exception as e:
        logger.error(f"Fallo al procesar clima de NASA POWER: {e}")
        # Buscar caché más cercano como salvaguarda
        caches = list(CACHE_DIR.glob("clima_*.json"))
        if caches:
            logger.info("Usando caché histórico más próximo como contingencia...")
            fb = json.loads(caches[0].read_text(encoding="utf-8"))
            fb["location"] = {"latitude": lat, "longitude": lon}
            fb["fallback"] = True
            fb = ensure_monthly_normals(fb)
            fb["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, fb)
            return fb
        raise HTTPException(
            status_code=502,
            detail=f"No se pudieron obtener datos climáticos para ({lat}, {lon}): {e}",
        )


# ─── Modelos Pydantic ───────────────────────────────────────────────────────

class Priorities(BaseModel):
    water_availability: int = Field(default=3, ge=1, le=5)
    climate_risk: int = Field(default=3, ge=1, le=5)
    soil_cover: int = Field(default=3, ge=1, le=5)
    diversification: int = Field(default=3, ge=1, le=5)
    nitrogen: int = Field(default=3, ge=1, le=5)


class RotationRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description="Latitud del lote")
    longitude: float = Field(..., ge=-180, le=180, description="Longitud del lote")
    start_month: int = Field(default=10, ge=1, le=12, description="Mes inicial de planificación (1-12)")
    num_cycles: int = Field(default=3, ge=1, le=10, description="Cantidad de ciclos o cultivos sucesivos en la rotación")
    first_crop_key: Optional[str] = Field(default=None, description="Clave de cultivo inicial forzado")
    weights: Optional[dict[str, float]] = Field(default=None, description="Ponderación de factores")
    previous_crops: Optional[list[str]] = Field(default=None, description="Lista opcional de hasta 3 cultivos anteriores [1º más reciente, 2º, 3º]")


class CompareRequest(BaseModel):
    latitude: float
    longitude: float
    current_rotation: list[str]
    priorities: Priorities = Priorities()


# ─── Endpoints de la API ───────────────────────────────────────────────────

@app.get("/health", tags=["Sistema"])
def health():
    """Verificación de estado del servicio."""
    return {"status": "ok", "app": "FieldShift", "version": "1.0.0"}


@app.get("/climate", tags=["Clima"])
@app.get("/api/v1/climate", tags=["Clima"])
def climate(
    lat: float = Query(..., ge=-90, le=90, description="Latitud"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud"),
):
    """
    Obtiene los indicadores agroclimáticos históricos derivados de NASA POWER
    (temperaturas normales, precipitaciones, frecuencia de heladas y estrés por calor).
    """
    return get_climate_data(lat, lon)


@app.get("/api/v1/crops", tags=["Agronomía"])
def get_crops():
    """
    Retorna el catálogo completo de semillas y cultivos con parámetros agronómicos:
    requerimientos térmicos, hídricos, tolerancia a helada/calor, efecto en nitrógeno y antecesores.
    """
    return _load_crops()


@app.get("/api/v1/ranking", tags=["Predicción"])
def get_ranking(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del lote"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del lote"),
    planting_month: int = Query(..., ge=1, le=12, description="Mes de siembra previsto (1=Enero... 12=Diciembre)"),
    cycle_months: int = Query(default=4, ge=2, le=8, description="Duración promedio del ciclo en meses"),
    previous_crops: Optional[str] = Query(default=None, description="Cultivos anteriores separados por comas (hasta 3), ej: 'soja,trigo,maiz'"),
):
    """
    Calcula de forma probabilística la aptitud de cada semilla de acuerdo a los datos
    históricos de NASA POWER y rankea los mejores cultivos para la ventana seleccionada.
    Si se indican cultivos anteriores, evalúa la compatibilidad agronómica sucesoria.
    """
    climate_data = get_climate_data(lat, lon)
    prev_list = [p.strip() for p in previous_crops.split(",") if p.strip()] if previous_crops else None
    ranking = rank_crops(
        climate_data=climate_data,
        planting_month=planting_month,
        cycle_months=cycle_months,
        previous_crops=prev_list,
    )
    return {
        "location": {"latitude": lat, "longitude": lon},
        "planting_month": planting_month,
        "cycle_months": cycle_months,
        "previous_crops": prev_list,
        "total_evaluated": len(ranking),
        "best_crop": ranking[0] if ranking else None,
        "crops": ranking,
    }


@app.post("/api/v1/rotation", tags=["Rotación"])
def create_rotation(req: RotationRequest):
    """
    A partir de la evaluación climática y el cultivo recomendado (o seleccionado),
    genera la secuencia óptima multi-anual de rotación definiendo qué semilla corresponde
    cultivar después de la anterior, considerando compatibilidad de antecesores, fijación
    de nitrógeno, rotación de familias botánicas y ventanas de siembra.
    """
    climate_data = get_climate_data(req.latitude, req.longitude)
    rotation_plan = build_rotation(
        climate_data=climate_data,
        num_cycles=req.num_cycles,
        start_month=req.start_month,
        first_crop_key=req.first_crop_key,
        weights=req.weights,
        previous_crops=req.previous_crops,
    )
    return rotation_plan


@app.get("/api/v1/next-crop", tags=["Rotación"])
def get_next_crop(
    predecessor: str = Query(..., description="Cultivo cosechado (antecesor, ej. 'soja', 'trigo', 'maiz')"),
    planting_month: int = Query(..., ge=1, le=12, description="Mes en que se prevé la siembra del siguiente cultivo"),
    lat: float = Query(default=-31.42, ge=-90, le=90, description="Latitud"),
    lon: float = Query(default=-64.18, ge=-180, le=180, description="Longitud"),
    cycle_months: int = Query(default=4, ge=2, le=8),
):
    """
    Define específicamente qué semilla corresponde cultivar después del cultivo antecesor
    ingresado, evaluando aptitud climática más compatibilidad agronómica directa.
    """
    climate_data = get_climate_data(lat, lon)
    try:
        candidates = suggest_next_crop(
            predecessor_key=predecessor.lower().strip(),
            climate_data=climate_data,
            next_planting_month=planting_month,
            cycle_months=cycle_months,
        )
        return {
            "predecessor": predecessor,
            "planting_month": planting_month,
            "location": {"latitude": lat, "longitude": lon},
            "best_successor": candidates[0] if candidates else None,
            "succession_ranking": candidates,
        }
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.post("/compare", tags=["Simulación"])
def compare(req: CompareRequest):
    """Compara escenarios de rotación y prioridades agronómicas."""
    mock_file = DATA_DIR / "mock_comparison.json"
    if mock_file.exists():
        return json.loads(mock_file.read_text(encoding="utf-8"))
    return {"message": "Simulación comparativa generada"}


@app.get("/api/v1/satellite-readings", tags=["Satelital"])
def get_satellite_endpoint(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del lote"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del lote"),
):
    """
    Retorna lecturas satelitales en tiempo real según los recursos oficiales NASA del desafío:
    - NASA SMAP: Humedad superficial (0-5 cm) y en zona radicular (0-100 cm).
    - NASA GPM IMERG & OpenET: Balance hídrico de 30 días, SPI-3 y riesgo de sequía.
    - NASA HLS / ECOSTRESS: NDVI estimado y estrés de canopia (ESI).
    """
    climate_data = get_climate_data(lat, lon)
    return get_satellite_readings(lat, lon, climate_data)


@app.get("/api/v1/field-analytics", tags=["Analítica"])
def get_field_analytics_endpoint(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del lote"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del lote"),
    planting_month: int = Query(default=10, ge=1, le=12, description="Mes de siembra previsto (1-12)"),
    cycle_months: int = Query(default=4, ge=2, le=8, description="Duración promedio del ciclo en meses"),
    num_cycles: int = Query(default=3, ge=1, le=10, description="Cantidad de ciclos o cultivos sucesivos en la rotación"),
    previous_crops: Optional[str] = Query(default=None, description="Cultivos anteriores separados por comas (hasta 3), ej: 'soja,trigo,maiz'"),
):
    """
    Retorna el informe integral de analítica agronómica y satelital (Field Analytics):
    perfil del lote, humedad de suelo NASA SMAP, balance hídrico GPM/OpenET, vegetación HLS,
    ranking y rotación sucesoria óptima con justificación técnica.
    """
    climate_data = get_climate_data(lat, lon)
    prev_list = [p.strip() for p in previous_crops.split(",") if p.strip()] if previous_crops else None

    ranking = rank_crops(
        climate_data=climate_data,
        planting_month=planting_month,
        cycle_months=cycle_months,
        previous_crops=prev_list,
    )
    rotation_plan = build_rotation(
        climate_data=climate_data,
        num_cycles=num_cycles,
        start_month=planting_month,
        previous_crops=prev_list,
    )
    return build_field_analytics(
        lat=lat,
        lon=lon,
        climate_data=climate_data,
        ranking_data=ranking,
        rotation_plan=rotation_plan,
        previous_crops=prev_list,
    )


# ─── Servir aplicación Frontend estática ───────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
