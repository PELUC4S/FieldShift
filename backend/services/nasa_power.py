import requests
import pandas as pd

URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

def obtener_clima(lat, lon, inicio, fin):
    params = {
        "parameters": "T2M,T2M_MIN,T2M_MAX,PRECTOTCORR",
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": inicio,
        "end": fin,
        "format": "JSON",
    }
    resp = requests.get(URL, params=params, timeout=120)
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
