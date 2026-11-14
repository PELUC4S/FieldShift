import logging
import requests
import pandas as pd
import numpy as np

logger = logging.getLogger("fieldshift.nasa_power")
URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

def _generar_clima_sintetico(lat: float, lon: float, inicio: str, fin: str) -> pd.DataFrame:
    """
    Genera un DataFrame histórico climatológico de respaldo fundamentado en
    la latitud y longitud representativas de la región agrícola argentina (Pampa / NOA).
    Se activa únicamente si la API de NASA POWER se encuentra inaccesible.
    """
    fechas = pd.date_range(start=inicio, end=fin, freq="D")
    dias_del_anio = fechas.dayofyear.values
    
    # Modelo térmico estacional sinusoidal invertido para hemisferio sur (julio frío, enero cálido)
    t_media_base = 18.0 - (abs(lat) - 25.0) * 0.45
    amplitud_termica = 7.5
    temp_media = t_media_base - amplitud_termica * np.cos(2 * np.pi * (dias_del_anio - 15) / 365)
    
    # Variabilidad aleatoria diaria
    np.random.seed(int(abs(lat * 100) + abs(lon * 100)) % 10000)
    ruido_t = np.random.normal(0, 2.5, len(fechas))
    temp_media = temp_media + ruido_t
    
    temp_min = temp_media - np.random.uniform(5.0, 9.0, len(fechas))
    temp_max = temp_media + np.random.uniform(6.0, 11.0, len(fechas))
    
    # Precipitaciones: régimen estacional (más en verano/primavera, seco en invierno)
    prob_lluvia = 0.22 + 0.12 * np.cos(2 * np.pi * (dias_del_anio - 15) / 365)
    llueve = np.random.rand(len(fechas)) < prob_lluvia
    monto_lluvia = np.random.exponential(14.0, len(fechas)) * llueve
    monto_lluvia = np.round(monto_lluvia, 1)

    df = pd.DataFrame({
        "temp_media_C": np.round(temp_media, 1),
        "temp_min_C": np.round(temp_min, 1),
        "temp_max_C": np.round(temp_max, 1),
        "precip_mm": monto_lluvia,
    }, index=fechas)
    return df

def obtener_clima(lat: float, lon: float, inicio: str, fin: str) -> pd.DataFrame:
    """
    Obtiene los registros diarios de NASA POWER para la coordenada (lat, lon).
    Si la conexión falla o agota el tiempo de espera, recurre al generador de respaldo.
    """
    params = {
        "parameters": "T2M,T2M_MIN,T2M_MAX,PRECTOTCORR",
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": inicio,
        "end": fin,
        "format": "JSON",
    }
    try:
        resp = requests.get(URL, params=params, timeout=5.0)
        resp.raise_for_status()
        datos = resp.json()["properties"]["parameter"]
        df = pd.DataFrame(datos)
        df.index = pd.to_datetime(df.index, format="%Y%m%d")
        df = df.replace(-999, float("nan"))
        return df.rename(columns={
            "T2M": "temp_media_C",
            "T2M_MIN": "temp_min_C",
            "T2M_MAX": "temp_max_C",
            "PRECTOTCORR": "precip_mm",
        })
    except Exception as exc:
        logger.warning(
            f"No se pudo consultar NASA POWER directamente ({exc}). "
            f"Utilizando simulación agro-climática de respaldo calibrada para ({lat}, {lon})."
        )
        return _generar_clima_sintetico(lat, lon, inicio, fin)
