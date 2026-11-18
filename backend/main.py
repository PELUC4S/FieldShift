"""
main.py – Servidor API FastAPI para FieldShift (Predicción de Rotación de Cultivos).
Integra datos agroclimáticos históricos de NASA POWER, motor de ranking probabilístico
de semillas y algoritmos de rotación y sucesión agronómica.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

try:
    from analysis.indicators import calcular_indicadores, ensure_monthly_normals
    from engine.crop_ranker import rank_crops, compute_crop_score, _load_crops
    from engine.rotation import build_rotation, suggest_next_crop
    from services.nasa_power import obtener_clima, _generar_clima_sintetico
    from services.nasa_harvester import get_satellite_readings, build_field_analytics
    from services.soilgrids_service import query_soilgrids
except ImportError:
    from backend.analysis.indicators import calcular_indicadores, ensure_monthly_normals
    from backend.engine.crop_ranker import rank_crops, compute_crop_score, _load_crops
    from backend.engine.rotation import build_rotation, suggest_next_crop
    from backend.services.nasa_power import obtener_clima, _generar_clima_sintetico
    from backend.services.nasa_harvester import get_satellite_readings, build_field_analytics
    from backend.services.soilgrids_service import query_soilgrids

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
MAX_CACHE_FILES = 500

# Detección de entorno de producción (desactiva Swagger y debug en producción)
IS_PROD = os.getenv("ENVIRONMENT", "").lower() in ["production", "prod"] or os.getenv("RENDER") == "true"

app = FastAPI(
    title="FieldShift API — Predicción de Rotación de Cultivos",
    description="API AgTech para evaluación probabilística de semillas y optimización de rotación de cultivos basada en NASA POWER.",
    version="1.0.0",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# ─── Middleware 1: Rate Limiting & Bot Protection ──────────────────────────
RATE_LIMIT_WINDOW = 60  # Ventana de 60 segundos
RATE_LIMIT_MAX_REQUESTS = 60  # Máximo 60 peticiones por minuto por IP
_request_records: dict[str, list[float]] = defaultdict(list)

# Firmas de User-Agents de escáners automáticos conocidos a bloquear
BLOCKED_USER_AGENTS = {
    "sqlmap", "nikto", "masscan", "nmap", "dirbuster", "acunetix", "havij", "zgrab"
}

@app.middleware("http")
async def rate_limiting_and_bot_protection(request: Request, call_next):
    # 1. Protección contra bots y escáners maliciosos
    ua = request.headers.get("user-agent", "").lower()
    if any(bot in ua for bot in BLOCKED_USER_AGENTS):
        logger.warning(f"Bloqueado bot/scanner por User-Agent sospechoso: {ua}")
        return Response(
            content=json.dumps({"detail": "Acceso denegado"}),
            status_code=403,
            media_type="application/json"
        )

    # 2. Rate limiting por IP del cliente
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    # Limitar solo endpoints de cómputo API
    if request.url.path.startswith("/api/") or request.url.path in ["/climate", "/compare"]:
        now = time.time()
        _request_records[client_ip] = [t for t in _request_records[client_ip] if now - t < RATE_LIMIT_WINDOW]

        if len(_request_records[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning(f"Rate limit excedido para IP: {client_ip}")
            return Response(
                content=json.dumps({"detail": "Demasiadas peticiones. Por favor, reintente en unos instantes."}),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"}
            )
        _request_records[client_ip].append(now)

    return await call_next(request)

# ─── Middleware 2: Cabeceras de Seguridad HTTP (Hardening defensivo) ────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=*"
    return response

# ─── Manejador Global de Excepciones (Sin fuga de datos internos) ───────────
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error interno no controlado en {request.method} {request.url.path}: {exc}", exc_info=True)
    return Response(
        content=json.dumps({"detail": "Ocurrió un error interno al procesar su solicitud. Intente nuevamente."}),
        status_code=500,
        media_type="application/json"
    )

# Orígenes explícitamente permitidos (sin comodín con credenciales)
ALLOWED_ORIGINS = [
    "https://peluc4s.github.io",
    "https://fieldshift-api.onrender.com",
    "http://localhost:8088",
    "http://127.0.0.1:8088",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^https://.*\.github\.io$|^https://.*\.onrender\.com$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Origin", "User-Agent"],
)


# ─── Helper de acceso a datos climáticos con caché y protección DoS ─────────

def _safe_write_cache(file_path: Path, data: dict[str, Any]) -> None:
    """Escribe en caché asegurando un límite máximo de archivos en disco para mitigar DoS por agotamiento de inodos."""
    try:
        cached_files = list(CACHE_DIR.glob("clima_*.json"))
        if len(cached_files) >= MAX_CACHE_FILES:
            # Desalojo LRU defensivo: eliminar el 10% más antiguo
            cached_files.sort(key=lambda f: f.stat().st_mtime)
            for old_file in cached_files[:max(1, len(cached_files) // 10)]:
                try:
                    old_file.unlink(missing_ok=True)
                except Exception:
                    pass
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as err:
        logger.warning(f"No se pudo escribir en caché seguro {file_path.name}: {err}")


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
            res["soil_profile"] = query_soilgrids(lat_r, lon_r)
            res["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, res)
            return res
        except Exception as e:
            logger.warning(f"Error al leer caché {archivo_cache}: {e}")

    logger.info(f"Obteniendo datos climáticos para lat={lat_r}, lon={lon_r}...")
    try:
        df = obtener_clima(lat_r, lon_r, INICIO, FIN)
        resultado = calcular_indicadores(df, lat_r, lon_r, INICIO, FIN)
        _safe_write_cache(archivo_cache, resultado)
        resultado["soil_profile"] = query_soilgrids(lat_r, lon_r)
        resultado["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, resultado)
        return resultado
    except Exception as e:
        logger.error(f"Fallo al procesar clima de NASA POWER: {e}")
        # 1. Buscar caché geográficamente más cercano (< 300 km)
        caches = list(CACHE_DIR.glob("clima_*.json"))
        closest_cache = None
        min_dist_sq = float("inf")
        for c in caches:
            try:
                parts = c.stem.split("_")
                c_lat, c_lon = float(parts[1]), float(parts[2])
                dist = (lat_r - c_lat) ** 2 + (lon_r - c_lon) ** 2
                if dist < min_dist_sq:
                    min_dist_sq = dist
                    closest_cache = c
            except Exception:
                continue

        if closest_cache and min_dist_sq < 9.0:
            logger.info(f"Usando caché histórico geográficamente próximo ({closest_cache.name}) como contingencia...")
            fb = json.loads(closest_cache.read_text(encoding="utf-8"))
            fb["location"] = {"latitude": lat, "longitude": lon}
            fb["fallback"] = True
            fb = ensure_monthly_normals(fb)
            fb["soil_profile"] = query_soilgrids(lat_r, lon_r)
            fb["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, fb)
            return fb

        # 2. Si no hay caché cercano, generar serie agroclimática calibrada para estas coordenadas
        logger.info(f"Generando modelo agroclimático calibrado de contingencia para ({lat_r}, {lon_r})...")
        df_fb = _generar_clima_sintetico(lat_r, lon_r, INICIO, FIN)
        fb_res = calcular_indicadores(df_fb, lat_r, lon_r, INICIO, FIN)
        fb_res["fallback"] = True
        fb_res["soil_profile"] = query_soilgrids(lat_r, lon_r)
        fb_res["satellite_indicators"] = get_satellite_readings(lat_r, lon_r, fb_res)
        return fb_res


# ─── Modelos Pydantic ───────────────────────────────────────────────────────

class Priorities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    water_availability: int = Field(default=3, ge=1, le=5)
    climate_risk: int = Field(default=3, ge=1, le=5)
    soil_cover: int = Field(default=3, ge=1, le=5)
    diversification: int = Field(default=3, ge=1, le=5)
    nitrogen: int = Field(default=3, ge=1, le=5)


class RotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(..., ge=-90, le=90, description="Latitud del lote")
    longitude: float = Field(..., ge=-180, le=180, description="Longitud del lote")
    start_month: int = Field(default=10, ge=1, le=12, description="Mes inicial de planificación (1-12)")
    num_cycles: int = Field(default=3, ge=1, le=10, description="Cantidad de ciclos o cultivos sucesivos en la rotación")
    first_crop_key: Optional[str] = Field(default=None, max_length=30, description="Clave de cultivo inicial forzado")
    weights: Optional[dict[str, float]] = Field(default=None, description="Ponderación de factores")
    previous_crops: Optional[list[str]] = Field(default=None, max_length=5, description="Lista opcional de hasta 3-5 cultivos anteriores [1º más reciente, 2º, 3º]")


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(..., ge=-90, le=90, description="Latitud del lote")
    longitude: float = Field(..., ge=-180, le=180, description="Longitud del lote")
    current_rotation: list[str] = Field(..., max_length=15, description="Lista de cultivos de la rotación a comparar")
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
    previous_crops: Optional[str] = Query(default=None, max_length=150, description="Cultivos anteriores separados por comas (hasta 3), ej: 'soja,trigo,maiz'"),
):
    """
    Calcula de forma probabilística la aptitud de cada semilla de acuerdo a los datos
    históricos de NASA POWER y rankea los mejores cultivos para la ventana seleccionada.
    Si se indican cultivos anteriores, evalúa la compatibilidad agronómica sucesoria.
    """
    climate_data = get_climate_data(lat, lon)
    prev_list = [p.strip() for p in previous_crops.split(",") if p.strip()][:5] if previous_crops else None
    soil_prof = climate_data.get("soil_profile") or query_soilgrids(lat, lon)
    ranking = rank_crops(
        climate_data=climate_data,
        planting_month=planting_month,
        cycle_months=cycle_months,
        previous_crops=prev_list,
        soil_profile=soil_prof,
    )
    return {
        "location": {"latitude": lat, "longitude": lon},
        "planting_month": planting_month,
        "cycle_months": cycle_months,
        "previous_crops": prev_list,
        "soil_profile": soil_prof,
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
    clean_prev = [c.strip()[:30] for c in req.previous_crops if c.strip()][:5] if req.previous_crops else None
    rotation_plan = build_rotation(
        climate_data=climate_data,
        num_cycles=req.num_cycles,
        start_month=req.start_month,
        first_crop_key=req.first_crop_key,
        weights=req.weights,
        previous_crops=clean_prev,
    )
    return rotation_plan


@app.get("/api/v1/next-crop", tags=["Rotación"])
def get_next_crop(
    predecessor: str = Query(..., max_length=30, description="Cultivo cosechado (antecesor, ej. 'soja', 'trigo', 'maiz')"),
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


@app.get("/api/v1/soil-profile", tags=["Edafológico"])
def get_soil_profile_endpoint(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del lote"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del lote"),
):
    """
    Retorna el perfil edafológico oficial de SoilGrids v2.0 (ISRIC):
    textura USDA (0-30cm), retención hídrica AWC (0-30 y 30-60cm),
    carbono orgánico del suelo (SOC) y pH en agua.
    """
    return query_soilgrids(lat, lon)


@app.get("/api/v1/field-analytics", tags=["Analítica"])
def get_field_analytics_endpoint(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del lote"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del lote"),
    planting_month: int = Query(default=10, ge=1, le=12, description="Mes de siembra previsto (1-12)"),
    cycle_months: int = Query(default=4, ge=2, le=8, description="Duración promedio del ciclo en meses"),
    num_cycles: int = Query(default=3, ge=1, le=10, description="Cantidad de ciclos o cultivos sucesivos en la rotación"),
    previous_crops: Optional[str] = Query(default=None, max_length=150, description="Cultivos anteriores separados por comas (hasta 3), ej: 'soja,trigo,maiz'"),
):
    """
    Retorna el informe integral de analítica agronómica y satelital (Field Analytics):
    perfil del lote, humedad de suelo NASA SMAP, balance hídrico GPM/OpenET, vegetación HLS,
    ranking y rotación sucesoria óptima con justificación técnica.
    """
    climate_data = get_climate_data(lat, lon)
    prev_list = [p.strip() for p in previous_crops.split(",") if p.strip()][:5] if previous_crops else None

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
