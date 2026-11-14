import pandas as pd

UMBRAL_HELADA_C = 0    # SUPUESTO, pendiente de validar
UMBRAL_CALOR_C = 35    # SUPUESTO, pendiente de validar

def a_dict(serie, decimales=1):
    return {int(k): round(float(v), decimales) for k, v in serie.items()}

def calcular_indicadores(df, lat, lon, inicio, fin):
    anios = df.index.year
    mensual = df.resample("ME").agg({"temp_media_C": "mean", "precip_mm": "sum"})
    normal = mensual.groupby(mensual.index.month).mean()
    
    lluvia_anual = df["precip_mm"].groupby(anios).sum()
    prom = lluvia_anual.mean()
    cv = lluvia_anual.std() / prom * 100
    
    es_helada = df["temp_min_C"] <= UMBRAL_HELADA_C
    heladas_anio = es_helada.groupby(anios).sum()
    heladas_mes = es_helada.groupby([anios, df.index.month]).sum().groupby(level=1).mean()
    
    es_calor = df["temp_max_C"] >= UMBRAL_CALOR_C
    calor_anio = es_calor.groupby(anios).sum()
    calor_mes = es_calor.groupby([anios, df.index.month]).sum().groupby(level=1).mean()
    
    return {
        "location": {"latitude": lat, "longitude": lon},
        "period": {"start": inicio, "end": fin, "years": int(anios.nunique())},
        "source": "NASA POWER (daily, community AG)",
        "thresholds": {
            "frost_tmin_c": UMBRAL_HELADA_C,
            "heat_tmax_c": UMBRAL_CALOR_C,
            "note": "Umbrales asumidos; pendientes de validar con bibliografía agronómica",
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
