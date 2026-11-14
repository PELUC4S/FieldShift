"""
crop_ranker.py – Motor de ranking probabilístico de cultivos.

Evalúa la aptitud de cada cultivo para un período y ubicación dados,
combinando fitness de temperatura, precipitación, riesgo de heladas,
estrés térmico y variabilidad de lluvias en un puntaje 0-100.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# ─── Cargar base de datos de cultivos ───────────────────────────────────────
_SEMILLAS_PATH = Path(__file__).resolve().parent.parent / "services" / "info_semillas.json"

def _load_crops() -> dict[str, dict]:
    """Carga y devuelve el diccionario de cultivos desde info_semillas.json."""
    with open(_SEMILLAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["crops"]


# ─── Pesos por defecto ──────────────────────────────────────────────────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "w_temp": 0.30,
    "w_precip": 0.30,
    "w_frost": 0.15,
    "w_heat": 0.10,
    "w_var": 0.15,
}


# ─── Funciones de fitness individuales ──────────────────────────────────────

def temperature_fitness(crop: dict, monthly_normals: dict[str, float],
                        planting_month: int, cycle_months: int) -> float:
    """
    Calcula qué tan bien la temperatura promedio durante el ciclo del cultivo
    se ajusta al rango óptimo.

    Retorna un puntaje 0-100:
    - 100 si todas las temperaturas mensuales están dentro del rango óptimo
    - Decae con distancia al rango, usando una función gaussiana suave
    """
    opt_min, opt_max = crop["optimal_temp_range_C"]
    abs_min = crop["absolute_min_temp_C"]
    abs_max = crop["absolute_max_temp_C"]

    # Meses del ciclo (con wrap-around de 12 a 1)
    cycle_month_list = [((planting_month - 1 + i) % 12) + 1 for i in range(cycle_months)]

    if not cycle_month_list:
        return 0.0

    scores: list[float] = []
    for m in cycle_month_list:
        temp = monthly_normals.get(str(m), monthly_normals.get(m))
        if temp is None:
            scores.append(50.0)  # Sin datos → puntaje neutro
            continue

        if opt_min <= temp <= opt_max:
            # Dentro del rango óptimo → puntaje alto
            # Bonus leve por estar en el centro del rango
            center = (opt_min + opt_max) / 2
            half_range = (opt_max - opt_min) / 2
            dist_from_center = abs(temp - center) / half_range if half_range > 0 else 0
            scores.append(100.0 - 10.0 * dist_from_center)
        elif temp < abs_min or temp > abs_max:
            # Fuera de rango absoluto → puntaje muy bajo
            scores.append(0.0)
        elif temp < opt_min:
            # Entre abs_min y opt_min → decaimiento proporcional
            range_width = opt_min - abs_min
            dist = opt_min - temp
            ratio = dist / range_width if range_width > 0 else 1.0
            scores.append(max(0.0, 80.0 * (1.0 - ratio)))
        else:
            # Entre opt_max y abs_max → decaimiento proporcional
            range_width = abs_max - opt_max
            dist = temp - opt_max
            ratio = dist / range_width if range_width > 0 else 1.0
            scores.append(max(0.0, 80.0 * (1.0 - ratio)))

    return sum(scores) / len(scores)


def precipitation_fitness(crop: dict, monthly_normals_precip: dict[str, float],
                          planting_month: int, cycle_months: int) -> float:
    """
    Evalúa si la precipitación acumulada durante el ciclo cumple con los
    requerimientos hídricos del cultivo.

    Retorna 0-100:
    - 100 si la precipitación es igual o mayor que la óptima
    - Decae linealmente hacia 0 si es menor que el mínimo
    - Pequeña penalización por exceso (>150% del óptimo)
    """
    cycle_month_list = [((planting_month - 1 + i) % 12) + 1 for i in range(cycle_months)]

    # Acumular precipitación durante los meses del ciclo
    total_precip = 0.0
    for m in cycle_month_list:
        p = monthly_normals_precip.get(str(m), monthly_normals_precip.get(m))
        if p is not None:
            total_precip += p

    min_precip = crop["min_precip_cycle_mm"]
    opt_precip = crop["optimal_precip_cycle_mm"]

    if total_precip >= opt_precip:
        # Suficiente agua; penalización leve por exceso (anegamiento)
        excess_ratio = total_precip / opt_precip if opt_precip > 0 else 1.0
        if excess_ratio > 1.5:
            # Exceso marcado → penalizar
            penalty = min(30.0, (excess_ratio - 1.5) * 40.0)
            return max(50.0, 100.0 - penalty)
        return 100.0
    elif total_precip <= min_precip * 0.5:
        # Muy por debajo del mínimo
        return 0.0
    elif total_precip <= min_precip:
        # Entre 50% del mínimo y el mínimo → puntaje bajo
        ratio = total_precip / min_precip if min_precip > 0 else 0
        return 40.0 * ratio
    else:
        # Entre mínimo y óptimo → interpolación lineal 40-100
        range_width = opt_precip - min_precip
        if range_width <= 0:
            return 70.0
        ratio = (total_precip - min_precip) / range_width
        return 40.0 + 60.0 * ratio


def frost_risk_penalty(crop: dict, frost_days_by_month: dict[str, float],
                       planting_month: int, cycle_months: int) -> float:
    """
    Calcula la penalización por riesgo de heladas durante el ciclo.

    Retorna 0-100:
    - 0 = sin riesgo de heladas
    - 100 = riesgo extremo (muchas heladas + cultivo sin tolerancia)
    """
    tolerance = crop["frost_tolerance"]
    # Factor de sensibilidad según tolerancia
    sensitivity = {
        "alta": 0.1,    # Muy tolerante → poca penalización
        "media": 0.4,
        "baja": 0.8,
        "nula": 1.0,    # Sin tolerancia → penalización máxima
    }.get(tolerance, 0.5)

    cycle_month_list = [((planting_month - 1 + i) % 12) + 1 for i in range(cycle_months)]

    total_frost_days = 0.0
    for m in cycle_month_list:
        fd = frost_days_by_month.get(str(m), frost_days_by_month.get(m, 0.0))
        total_frost_days += fd

    if total_frost_days == 0:
        return 0.0

    # Escala: 5 días de helada con sensibilidad 1.0 = penalización 100
    raw_penalty = (total_frost_days / 5.0) * sensitivity * 100.0
    return min(100.0, max(0.0, raw_penalty))


def heat_risk_penalty(crop: dict, heat_days_by_month: dict[str, float],
                      planting_month: int, cycle_months: int) -> float:
    """
    Calcula la penalización por estrés térmico durante el ciclo.

    Retorna 0-100:
    - 0 = sin estrés térmico
    - 100 = estrés extremo
    """
    tolerance = crop["heat_tolerance"]
    sensitivity = {
        "alta": 0.15,
        "media": 0.5,
        "baja": 1.0,
    }.get(tolerance, 0.5)

    cycle_month_list = [((planting_month - 1 + i) % 12) + 1 for i in range(cycle_months)]

    total_heat_days = 0.0
    for m in cycle_month_list:
        hd = heat_days_by_month.get(str(m), heat_days_by_month.get(m, 0.0))
        total_heat_days += hd

    if total_heat_days == 0:
        return 0.0

    # Escala: 15 días de calor extremo con sensibilidad 1.0 = penalización 100
    raw_penalty = (total_heat_days / 15.0) * sensitivity * 100.0
    return min(100.0, max(0.0, raw_penalty))


def rainfall_variability_risk(annual_rainfall_stats: dict) -> float:
    """
    Evalúa el riesgo por variabilidad interanual de las lluvias.

    Retorna 0-100:
    - 0 = lluvias muy estables (CV bajo)
    - 100 = alta variabilidad (CV alto + años de sequía)

    Usa coeficiente de variación (CV%) y proporción de años secos.
    """
    cv = annual_rainfall_stats.get("cv_percent", 0.0)
    mean_mm = annual_rainfall_stats.get("mean_mm", 0.0)
    min_mm = annual_rainfall_stats.get("min_mm", 0.0)

    # Componente 1: CV (un CV > 30% es muy variable para agricultura)
    cv_score = min(50.0, (cv / 30.0) * 50.0)

    # Componente 2: Severidad de la peor sequía
    if mean_mm > 0:
        drought_severity = max(0.0, 1.0 - (min_mm / mean_mm))
        drought_score = drought_severity * 50.0
    else:
        drought_score = 50.0

    return min(100.0, cv_score + drought_score)


# ─── Combinación y ranking ──────────────────────────────────────────────────

def compute_crop_score(
    crop_key: str,
    crop: dict,
    climate_data: dict,
    planting_month: int,
    cycle_months: int,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Calcula el puntaje probabilístico de aptitud agronómica de un cultivo
    para una ubicación y período determinados.
    """
    w = weights or DEFAULT_WEIGHTS.copy()
    monthly_temp = climate_data["climate_normal_monthly"]["temp_avg_c"]
    monthly_precip = climate_data["climate_normal_monthly"]["precip_mm"]
    frost_by_month = climate_data["frost_days"]["mean_by_month"]
    heat_by_month = climate_data["heat_stress_days"]["mean_by_month"]
    annual_rain = climate_data["annual_rainfall"]

    # Verificar si el mes de siembra es válido para este cultivo
    sowing_months = crop.get("sowing_months", [])
    is_valid_sowing = planting_month in sowing_months
    
    # Tolerancia de ventana: si está a 1 mes de la ventana óptima
    adjacent_sowing = [((m - 2) % 12) + 1 for m in sowing_months] + [(m % 12) + 1 for m in sowing_months]
    is_adjacent_sowing = (not is_valid_sowing) and (planting_month in adjacent_sowing)

    if is_valid_sowing:
        sowing_factor = 1.0
        sowing_penalty = 0.0
    elif is_adjacent_sowing:
        sowing_factor = 0.85
        sowing_penalty = 15.0
    else:
        sowing_factor = 0.40
        sowing_penalty = 50.0

    # Calcular componentes individuales de aptitud (0 a 100)
    temp_fit = temperature_fitness(crop, monthly_temp, planting_month, cycle_months)
    precip_fit = precipitation_fitness(crop, monthly_precip, planting_month, cycle_months)
    frost_pen = frost_risk_penalty(crop, frost_by_month, planting_month, cycle_months)
    heat_pen = heat_risk_penalty(crop, heat_by_month, planting_month, cycle_months)
    rain_var = rainfall_variability_risk(annual_rain)

    frost_safety = max(0.0, 100.0 - frost_pen)
    heat_safety = max(0.0, 100.0 - heat_pen)
    rain_stability = max(0.0, 100.0 - rain_var)

    # Suma ponderada de índices de seguridad y aptitud
    base_climate_fitness = (
        w["w_temp"] * temp_fit
        + w["w_precip"] * precip_fit
        + w["w_frost"] * frost_safety
        + w["w_heat"] * heat_safety
        + w["w_var"] * rain_stability
    )

    # Multiplicar por factor de ventana de siembra biológica
    final_score = base_climate_fitness * sowing_factor
    final_score = max(0.0, min(100.0, final_score))

    crop_name = crop_key.replace("_", " ").title()

    return {
        "crop": crop_key,
        "crop_key": crop_key,
        "name": crop_name,
        "crop_name": crop_name,
        "score": round(final_score, 1),
        "probability_score": round(final_score, 1),
        "crop_type": crop.get("type", ""),
        "type": crop.get("type", ""),
        "family": crop.get("family", ""),
        "water_consumption": crop.get("water_consumption", "Medio"),
        "cycle_days_mid": crop.get("cycle_days_mid", 120),
        "cycle_days_range": crop.get("cycle_days_range", [100, 140]),
        "notes": crop.get("notes", ""),
        "frost_tolerance": crop.get("frost_tolerance", "media"),
        "heat_tolerance": crop.get("heat_tolerance", "media"),
        "soil_nitrogen_effect": crop.get("soil_nitrogen_effect", "neutral"),
        "sowing_months": crop.get("sowing_months", []),
        "harvest_months": crop.get("harvest_months", []),
        "breakdown": {
            "temperature_fitness": round(temp_fit, 1),
            "precipitation_fitness": round(precip_fit, 1),
            "frost_risk_penalty": round(frost_pen, 1),
            "heat_risk_penalty": round(heat_pen, 1),
            "rainfall_variability_risk": round(rain_var, 1),
            "sowing_month_valid": is_valid_sowing,
            "sowing_penalty": round(sowing_penalty, 1),
            "frost_safety": round(frost_safety, 1),
            "heat_safety": round(heat_safety, 1),
            "rain_stability": round(rain_stability, 1),
        },
        "weights_used": w,
        "planting_month": planting_month,
        "cycle_months": cycle_months,
    }


def rank_crops(
    climate_data: dict,
    planting_month: int,
    cycle_months: int = 4,
    weights: dict[str, float] | None = None,
    crops_db: dict[str, dict] | None = None,
    previous_crops: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Rankea todos los cultivos disponibles para un mes de siembra y ubicación.
    Si se especifican cultivos anteriores, pondera la compatibilidad agronómica
    con el antecesor inmediato y penaliza repeticiones recientes.

    Retorna lista ordenada de mayor a menor puntaje con desglose completo.
    """
    crops = crops_db or _load_crops()
    results = []

    valid_prev = [c.strip() for c in (previous_crops or []) if c and c.strip() in crops][:3]
    immediate_key = valid_prev[0] if valid_prev else None
    immediate_crop = crops.get(immediate_key) if immediate_key else None

    for key, crop in crops.items():
        score_data = compute_crop_score(
            crop_key=key,
            crop=crop,
            climate_data=climate_data,
            planting_month=planting_month,
            cycle_months=cycle_months,
            weights=weights,
        )
        if immediate_key and immediate_crop:
            from .rotation import predecessor_compatibility
            rot_bonus, reasons = predecessor_compatibility(key, crop, immediate_key, immediate_crop)
            recency_penalty = 7.0 if key in valid_prev else 0.0
            if recency_penalty > 0:
                reasons.append("✗ Cultivo en historial reciente del lote")
            net_bonus = rot_bonus - recency_penalty
            raw_score = score_data["score"]
            adjusted = max(0.0, min(100.0, raw_score + net_bonus))
            score_data["raw_climate_score"] = raw_score
            score_data["rotation_bonus"] = round(net_bonus, 1)
            score_data["score"] = round(adjusted, 1)
            score_data["probability_score"] = round(adjusted, 1)
            score_data["predecessor_note"] = " | ".join(reasons) if reasons else None

        results.append(score_data)

    # Ordenar de mayor a menor puntaje
    results.sort(key=lambda x: x["score"], reverse=True)
    return results
