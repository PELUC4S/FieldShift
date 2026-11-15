try:
    import pandas as pd
except ImportError:
    pd = None

UMBRAL_HELADA_C = 0    # Umbral meteorológico de helada estándar en superficie
UMBRAL_CALOR_C = 35    # Umbral de estrés por golpe de calor en floración/llenado

MONTH_NAMES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

def a_dict(serie, decimales=1):
    return {int(k): round(float(v), decimales) for k, v in serie.items()}

def ensure_monthly_normals(data: dict) -> dict:
    """Asegura que el diccionario climático contenga la lista 'monthly_normals' para el frontend."""
    if "monthly_normals" in data and data["monthly_normals"]:
        return data

    temps = data.get("climate_normal_monthly", {}).get("temp_avg_c", {})
    rains = data.get("climate_normal_monthly", {}).get("precip_mm", {})
    frosts = data.get("frost_days", {}).get("mean_by_month", {})
    heats = data.get("heat_stress_days", {}).get("mean_by_month", {})

    normals = []
    for m in range(1, 13):
        t = float(temps.get(str(m), temps.get(m, 20.0)))
        r = float(rains.get(str(m), rains.get(m, 50.0)))
        f = float(frosts.get(str(m), frosts.get(m, 0.0)))
        h = float(heats.get(str(m), heats.get(m, 0.0)))
        normals.append({
            "month": m,
            "month_name": MONTH_NAMES_ES[m - 1],
            "temp_mean": round(t, 1),
            "temperature": round(t, 1),
            "precipitation": round(r, 1),
            "rainfall": round(r, 1),
            "frost_days": round(f, 1),
            "heat_days": round(h, 1),
            "heat_stress_days": round(h, 1),
        })
    data["monthly_normals"] = normals
    return data

def calcular_indicadores(df, lat, lon, inicio, fin):
    if pd is None:
        raise RuntimeError("El paquete 'pandas' es requerido para calcular indicadores.")
    anios = df.index.year
    mensual = df.resample("ME").agg({"temp_media_C": "mean", "precip_mm": "sum"})
    normal = mensual.groupby(mensual.index.month).mean()
    
    lluvia_anual = df["precip_mm"].groupby(anios).sum()
    prom = lluvia_anual.mean()
    cv = (lluvia_anual.std() / prom * 100) if prom > 0 else 0.0
    
    es_helada = df["temp_min_C"] <= UMBRAL_HELADA_C
    heladas_anio = es_helada.groupby(anios).sum()
    heladas_mes = es_helada.groupby([anios, df.index.month]).sum().groupby(level=1).mean()
    
    es_calor = df["temp_max_C"] >= UMBRAL_CALOR_C
    calor_anio = es_calor.groupby(anios).sum()
    calor_mes = es_calor.groupby([anios, df.index.month]).sum().groupby(level=1).mean()
    
    data_quality = df.attrs.get("data_quality", "unknown")
    data_source = df.attrs.get("source", "Fuente no identificada")
    result = {
        "location": {"latitude": lat, "longitude": lon},
        "period": {"start": inicio, "end": fin, "years": int(anios.nunique())},
        "source": data_source,
        "data_quality": data_quality,
        "data_notice": (
            "Datos sintéticos de contingencia; no son observaciones de NASA POWER."
            if data_quality == "simulated" else None
        ),
        "thresholds": {
            "frost_tmin_c": UMBRAL_HELADA_C,
            "heat_tmax_c": UMBRAL_CALOR_C,
            "note": "Umbrales agronómicos estándar para helada meteorológica y estrés térmico estival",
        },
        "climate_normal_monthly": {
            "temp_avg_c": a_dict(normal["temp_media_C"]),
            "precip_mm": a_dict(normal["precip_mm"]),
        },
        "annual_rainfall": {
            "mean_mm": round(float(prom), 0),
            "cv_percent": round(float(cv), 1),
            "min_mm": round(float(lluvia_anual.min()), 0),
            "max_mm": round(float(lluvia_anual.max()), 0),
            "by_year_mm": a_dict(lluvia_anual, 0),
            "anomaly_by_year_mm": a_dict(lluvia_anual - prom, 0),
        },
        "frost_days": {
            "mean_per_year": round(float(heladas_anio.mean()), 1),
            "mean_by_month": a_dict(heladas_mes),
        },
        "heat_stress_days": {
            "mean_per_year": round(float(calor_anio.mean()), 1),
            "mean_by_month": a_dict(calor_mes),
        },
        "indicators_pending": {"water_availability": None, "soil_moisture": None},
    }
    return ensure_monthly_normals(result)
