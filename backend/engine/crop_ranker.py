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


def precipitation_fitness(
    crop: dict,
    monthly_normals_precip: dict[str, float],
    planting_month: int,
    cycle_months: int,
    soil_water_reserve_mm: float = 0.0,
) -> float:
    """
    Evalúa si la oferta hídrica compuesta (precipitación acumulada + agua útil
    almacenada en el perfil del suelo SoilGrids/SMAP) cumple con los
    requerimientos del cultivo.

    Retorna 0-100:
    - 100 si la oferta hídrica es igual o mayor que la óptima
    - Decae linealmente hacia 0 si es menor que el mínimo efectivo
    - Modulado por la eficiencia en uso del agua (water_consumption)
    """
    cycle_month_list = [((planting_month - 1 + i) % 12) + 1 for i in range(cycle_months)]

    # Acumular precipitación durante los meses del ciclo
    total_rain = 0.0
    for m in cycle_month_list:
        p = monthly_normals_precip.get(str(m), monthly_normals_precip.get(m))
        if p is not None:
            total_rain += p

    total_water = total_rain + soil_water_reserve_mm
    min_precip = crop["min_precip_cycle_mm"]
    opt_precip = crop["optimal_precip_cycle_mm"]

    # Ajuste por consumo hídrico genético (water_consumption)
    water_cons = crop.get("water_consumption", "Medio").lower()
    if water_cons in ["bajo", "muy bajo"]:
        effective_min = min_precip * 0.88
    elif water_cons in ["alto", "muy alto"]:
        effective_min = min_precip * 1.05
    else:
        effective_min = min_precip

    if total_water >= opt_precip:
        excess_ratio = total_water / opt_precip if opt_precip > 0 else 1.0
        if excess_ratio > 1.5:
            penalty = min(30.0, (excess_ratio - 1.5) * 40.0)
            return max(50.0, 100.0 - penalty)
        return 100.0
    elif total_water <= effective_min * 0.4:
        return 0.0
    elif total_water <= effective_min:
        ratio = total_water / effective_min if effective_min > 0 else 0
        return 40.0 * ratio
    else:
        range_width = opt_precip - effective_min
        if range_width <= 0:
            return 70.0
        ratio = (total_water - effective_min) / range_width
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
    soil_profile: dict[str, Any] | None = None,
    satellite_indicators: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Calcula el puntaje probabilístico de aptitud agronómica de un cultivo
    para una ubicación y período determinados, integrando:
    - Clima NASA POWER (Temperatura, Lluvia, Heladas, Calor, Variabilidad)
    - Humedad satelital NASA SMAP (Superficie y Zona Radicular)
    - Perfil edáfico SoilGrids ISRIC (Textura USDA, AWC 0-60cm, SOC y pH)
    """
    w = weights or DEFAULT_WEIGHTS.copy()
    monthly_temp = climate_data["climate_normal_monthly"]["temp_avg_c"]
    monthly_precip = climate_data["climate_normal_monthly"]["precip_mm"]
    frost_by_month = climate_data["frost_days"]["mean_by_month"]
    heat_by_month = climate_data["heat_stress_days"]["mean_by_month"]
    annual_rain = climate_data["annual_rainfall"]

    # Extraer perfil de suelo y satélite
    soil = soil_profile or climate_data.get("soil_profile") or climate_data.get("satellite_indicators", {}).get("soil_profile")
    sat = satellite_indicators or climate_data.get("satellite_indicators")

    # 1. Agua disponible compuesta (Lluvia acumulada + reserva hídrica SMAP / SoilGrids)
    awc_total_mm = 85.0
    usda_text = "Franco"
    if soil:
        if "water_retention" in soil:
            awc_total_mm = float(soil["water_retention"].get("total_awc_0_60cm_mm", 85.0))
        elif "available_water_capacity_mm" in soil:
            awc_total_mm = float(soil["available_water_capacity_mm"])
        tex = soil.get("texture", {})
        usda_text = tex.get("usda_class", "Franco") if isinstance(tex, dict) else (str(tex) if tex else "Franco")

    root_pct = 50.0
    if sat and "smap_soil_moisture" in sat:
        root_pct = float(sat["smap_soil_moisture"].get("rootzone_0_100cm", {}).get("percentage", 50.0))

    soil_water_reserve_mm = round(awc_total_mm * (root_pct / 100.0), 1)

    loc = climate_data.get("location", {})
    lat = loc.get("latitude", loc.get("lat", 0.0))

    # 2. Verificar ventana biológica de siembra con desfasaje hemisférico
    base_sowing_months = crop.get("sowing_months", [])
    base_harvest_months = crop.get("harvest_months", [])

    if lat >= 0:
        # Hemisferio Norte: desfasar 6 meses respecto al calendario base del Cono Sur
        sowing_months = [((m - 1 + 6) % 12) + 1 for m in base_sowing_months]
        harvest_months = [((m - 1 + 6) % 12) + 1 for m in base_harvest_months]
    else:
        sowing_months = list(base_sowing_months)
        harvest_months = list(base_harvest_months)

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
        sowing_factor = 0.35
        sowing_penalty = 50.0

    # Extraer temperatura del mes de siembra
    plant_temp = monthly_temp.get(str(planting_month), monthly_temp.get(planting_month, 20.0))

    # 3. Calcular componentes individuales de aptitud (0 a 100)
    temp_fit = temperature_fitness(crop, monthly_temp, planting_month, cycle_months)
    precip_fit = precipitation_fitness(crop, monthly_precip, planting_month, cycle_months, soil_water_reserve_mm)
    frost_pen = frost_risk_penalty(crop, frost_by_month, planting_month, cycle_months)
    heat_pen = heat_risk_penalty(crop, heat_by_month, planting_month, cycle_months)
    rain_var = rainfall_variability_risk(annual_rain)

    frost_safety = max(0.0, 100.0 - frost_pen)
    heat_safety = max(0.0, 100.0 - heat_pen)
    rain_stability = max(0.0, 100.0 - rain_var)

    # 4. Modificadores de fertilidad edáfica (SOC) y acidez (pH)
    soc_g_kg = 17.5
    om_pct = 3.0
    if soil and "organic_carbon" in soil:
        soc_g_kg = float(soil["organic_carbon"].get("soc_g_kg", 17.5))
        om_pct = float(soil["organic_carbon"].get("organic_matter_pct", 3.0))

    n_effect = crop.get("soil_nitrogen_effect", "neutral")
    soc_mod = 0.0
    soc_rationale = None

    if soc_g_kg < 15.0:
        factor = (1.0 - soc_g_kg / 15.0)
        if n_effect == "fijador":
            soc_mod = round(6.0 * factor, 1)
            soc_rationale = f"🌱 Fertilidad baja (SOC: {soc_g_kg} g/kg): cultivo fijador bonificado (+{soc_mod}) para aportar nitrógeno orgánico."
        elif n_effect == "consumidor_alto":
            soc_mod = round(-6.0 * factor, 1)
            soc_rationale = f"⚠️ Fertilidad baja (SOC: {soc_g_kg} g/kg): cultivo muy demandante de N; penalizado ({soc_mod}) por déficit nutricional."
        elif n_effect == "consumidor_medio":
            soc_mod = round(-2.5 * factor, 1)
            soc_rationale = f"⚠️ Fertilidad moderada-baja (SOC: {soc_g_kg} g/kg): requerirá fertilización nitrogenada."
    elif soc_g_kg >= 25.0:
        factor = min(1.0, (soc_g_kg - 25.0) / 15.0)
        if n_effect == "consumidor_alto":
            soc_mod = round(4.0 * factor, 1)
            soc_rationale = f"✓ Fertilidad alta (SOC: {soc_g_kg} g/kg, MO: {om_pct}%): aprovecha la excelente dotación biológica (+{soc_mod})."
        elif n_effect == "consumidor_medio":
            soc_mod = round(2.0 * factor, 1)
            soc_rationale = f"✓ Fertilidad alta (SOC: {soc_g_kg} g/kg): condiciones óptimas de materia orgánica."
        else:
            soc_rationale = f"✓ Fertilidad alta (SOC: {soc_g_kg} g/kg): suelo estructurado con buena actividad biológica."
    else:
        soc_rationale = f"✓ Fertilidad media (SOC: {soc_g_kg} g/kg, MO: {om_pct}%): dotación equilibrada."

    ph = 6.8
    if soil and "acidity" in soil:
        ph = float(soil["acidity"].get("ph_h2o", 6.8))

    ph_mod = 0.0
    ph_rationale = None
    if ph < 5.5:
        if n_effect == "fijador" or crop_key in ["cebada", "garbanzo"]:
            ph_mod = -4.0
            ph_rationale = f"⚠️ Acidez (pH {ph}): nodulación de fijadores e intercambio catiónico comprometidos (-4.0)."
        else:
            ph_mod = -2.0
            ph_rationale = f"⚠️ Acidez (pH {ph}): suelo ácido con posible fijación de fósforo."
    elif ph > 8.0:
        if crop_key in ["sorgo", "cebada"]:
            ph_mod = 0.0
            ph_rationale = f"✓ Alcalinidad (pH {ph}): cultivo con buena tolerancia genética a pH elevado."
        else:
            ph_mod = -3.0
            ph_rationale = f"⚠️ Alcalinidad (pH {ph}): riesgo de clorosis y menor asimilación de micronutrientes (-3.0)."
    else:
        ph_rationale = f"✓ Acidez (pH {ph}): rango óptimo para desarrollo radicular y absorción de nutrientes."

    # Humedad superficial (cama de siembra 0-5 cm) para germinación
    surf_pct = 50.0
    if sat and "smap_soil_moisture" in sat:
        surf_pct = float(sat["smap_soil_moisture"].get("surface_0_5cm", {}).get("percentage", 50.0))

    seedbed_mod = 0.0
    seedbed_rationale = None
    if surf_pct < 18.0:
        seedbed_mod = -4.0
        seedbed_rationale = f"⚠️ Cama de siembra seca ({surf_pct}% AU en 0-5cm): riesgo en emergencia; sembrar profundo o esperar recarga (-4.0)."
    elif surf_pct >= 90.0:
        seedbed_mod = -3.0
        seedbed_rationale = f"⚠️ Cama de siembra saturada ({surf_pct}% AU en 0-5cm): riesgo de anegamiento y asfixia de semilla (-3.0)."
    elif surf_pct >= 55.0:
        seedbed_rationale = f"✓ Cama de siembra óptima ({surf_pct}% AU en 0-5cm): excelente humedad para germinación uniforme."

    # Textura y drenaje (Arena vs Arcilla)
    sand_pct = 38.0
    clay_pct = 22.0
    if soil and "texture" in soil and isinstance(soil["texture"], dict):
        sand_pct = float(soil["texture"].get("sand_pct", 38.0))
        clay_pct = float(soil["texture"].get("clay_pct", 22.0))

    tex_mod = 0.0
    tex_rationale = None
    water_cons = crop.get("water_consumption", "Medio").lower()
    if sand_pct >= 65.0:
        if crop_key == "mani":
            tex_mod = 4.0
            tex_rationale = f"✓ Textura arenosa ({sand_pct}% arena): ideal para penetración de clavos y arrancado en maní (+4.0)."
        elif water_cons in ["alto", "muy alto"]:
            tex_mod = -3.5
            tex_rationale = f"⚠️ Textura arenosa ({sand_pct}% arena): drenaje excesivo; mayor vulnerabilidad a sequías cortas (-3.5)."
    elif clay_pct >= 40.0:
        if crop_key in ["mani", "papa"]:
            tex_mod = -4.0
            tex_rationale = f"⚠️ Textura arcillosa ({clay_pct}% arcilla): suelo pesado que dificulta desarrollo de tubérculos/vainas (-4.0)."

    # Balance hídrico antecedente de 30 días (SPI-3)
    spi_3 = 0.0
    if sat and "water_balance_gpm_openet" in sat:
        spi_3 = float(sat["water_balance_gpm_openet"].get("spi_3_months", 0.0))

    spi_mod = 0.0
    spi_rationale = None
    if spi_3 <= -1.2:
        if water_cons in ["alto", "muy alto"]:
            spi_mod = -4.0
            spi_rationale = f"⚠️ Sequía antecedente (SPI-3: {spi_3}): déficit acumulado; cultivo de alta demanda condicionado (-4.0)."
        else:
            spi_mod = -1.5
            spi_rationale = f"⚠️ Déficit hídrico antecedente (SPI-3: {spi_3}): reserva del perfil condicionada."
    elif spi_3 >= 1.0:
        spi_rationale = f"✓ Balance hídrico favorable (SPI-3: {spi_3}): perfil hídrico antecedente con buena recarga."

    water_rationale = f"Suelo {usda_text} (AWC: {awc_total_mm} mm en 0-60cm): reserva útil en perfil {soil_water_reserve_mm} mm (SMAP {root_pct}% AU)."

    # 5. Evaluación de Umbrales Críticos / Ley del Mínimo de Liebig
    frost_tol = crop.get("frost_tolerance", "media")
    is_frozen_soil = (plant_temp < 0.0) or ("permafrost" in usda_text.lower())

    plant_month_frost = frost_by_month.get(str(planting_month), frost_by_month.get(planting_month, 0.0))
    opt_min_temp = crop.get("optimal_temp_range_C", [15, 25])[0]

    # Criterio letal: heladas en mes de siembra o ciclo con baja tolerancia, o temperatura por debajo de la base fisiológica
    is_lethal_frost = (frost_tol == "nula" and (plant_month_frost >= 2.5 or frost_pen >= 65.0)) or \
                      (frost_tol == "baja" and (plant_month_frost >= 5.0 or frost_pen >= 80.0))
    is_lethal_temp = (temp_fit <= 0.0) or (plant_temp < 10.0 and (opt_min_temp >= 20.0 or frost_tol == "nula"))
    is_lethal_climate = is_lethal_frost or is_lethal_temp

    extra_notes = [n for n in [water_rationale, seedbed_rationale, tex_rationale, spi_rationale, soc_rationale, ph_rationale] if n]

    if is_frozen_soil or is_lethal_climate:
        base_climate_fitness = 0.0
        sowing_factor = 0.0
        soil_modifier = 0.0
        final_score = 0.0
        if is_frozen_soil:
            fatal_msg = f"❄️ Inviable por Suelo Congelado / Criosfera (T mes {planting_month}: {plant_temp}°C). Germinación e implantación biológicamente imposibles (Ley del Mínimo de Liebig)."
        elif is_lethal_frost:
            fatal_msg = f"❄️ Inviable por Riesgo Crítico de Heladas ({plant_month_frost} días/mes en siembra / Penalización: {round(frost_pen)}%). Daño por congelamiento letal para plántulas (Ley de Liebig)."
        else:
            fatal_msg = f"❄️ Inviable por Régimen Térmico Letal (T mes {planting_month}: {plant_temp}°C / Aptitud térmica: {round(temp_fit)}%). Temperatura inferior a la base fisiológica del cultivo (Ley de Liebig)."
        soil_rationale = [fatal_msg] + extra_notes
    else:
        soil_rationale = extra_notes

        # Modulación de aptitud climática mediante compuerta térmica (Thermal Gating):
        # La falta de temperatura adecuada no puede ser compensada linealmente por agua
        thermal_gate = (temp_fit / 100.0) ** 0.6
        base_climate_fitness = (
            w["w_temp"] * temp_fit
            + w["w_precip"] * precip_fit
            + w["w_frost"] * frost_safety
            + w["w_heat"] * heat_safety
            + w["w_var"] * rain_stability
        ) * thermal_gate

        # Multiplicar por factor de ventana de siembra biológica y aplicar suelo
        base_score = base_climate_fitness * sowing_factor
        total_soil_modifier = soc_mod + ph_mod + seedbed_mod + tex_mod + spi_mod
        soil_modifier = round(total_soil_modifier * sowing_factor, 1)
        final_score = max(0.0, min(100.0, base_score + soil_modifier))

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
        "sowing_months": sowing_months,
        "harvest_months": harvest_months,
        "breakdown": {
            "temperature_fitness": round(temp_fit, 1),
            "precipitation_fitness": round(precip_fit, 1),
            "soil_water_reserve_mm": soil_water_reserve_mm,
            "soil_score_modifier": soil_modifier,
            "soc_g_kg": soc_g_kg,
            "organic_matter_pct": om_pct,
            "ph_h2o": ph,
            "frost_risk_penalty": round(frost_pen, 1),
            "heat_risk_penalty": round(heat_pen, 1),
            "rainfall_variability_risk": round(rain_var, 1),
            "sowing_month_valid": is_valid_sowing,
            "sowing_factor": sowing_factor,
            "sowing_penalty": round(sowing_penalty, 1),
            "frost_safety": round(frost_safety, 1),
            "heat_safety": round(heat_safety, 1),
            "rain_stability": round(rain_stability, 1),
        },
        "soil_rationale": soil_rationale,
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
    soil_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Rankea todos los cultivos disponibles para un mes de siembra y ubicación.
    Integra datos climáticos NASA, satelitales SMAP y pedológicos SoilGrids.
    Si se especifican cultivos anteriores, pondera la compatibilidad agronómica
    modulada por época de siembra y penaliza repeticiones recientes.
    """
    crops = crops_db or _load_crops()
    results = []

    valid_prev = [c.strip() for c in (previous_crops or []) if c and c.strip() in crops][:3]
    immediate_key = valid_prev[0] if valid_prev else None
    immediate_crop = crops.get(immediate_key) if immediate_key else None

    # Obtener suelo y satélite
    soil = soil_profile or climate_data.get("soil_profile") or climate_data.get("satellite_indicators", {}).get("soil_profile")
    sat = climate_data.get("satellite_indicators")

    for key, crop in crops.items():
        score_data = compute_crop_score(
            crop_key=key,
            crop=crop,
            climate_data=climate_data,
            planting_month=planting_month,
            cycle_months=cycle_months,
            weights=weights,
            soil_profile=soil,
            satellite_indicators=sat,
        )
        if immediate_key and immediate_crop:
            from .rotation import predecessor_compatibility
            rot_bonus, reasons = predecessor_compatibility(key, crop, immediate_key, immediate_crop)
            recency_penalty = 7.0 if key in valid_prev else 0.0
            if recency_penalty > 0:
                reasons.append("✗ Cultivo en historial reciente del lote")
            net_bonus = rot_bonus - recency_penalty
            # Modular bono sucesorio por ventana de siembra
            sow_factor = score_data["breakdown"]["sowing_factor"]
            effective_bonus = round(net_bonus * sow_factor, 1)

            raw_score = score_data["score"]
            if raw_score <= 0.0:
                adjusted = 0.0
                effective_bonus = 0.0
            else:
                adjusted = max(0.0, min(100.0, raw_score + effective_bonus))
            score_data["raw_climate_score"] = raw_score
            score_data["rotation_bonus"] = effective_bonus
            score_data["score"] = round(adjusted, 1)
            score_data["probability_score"] = round(adjusted, 1)
            score_data["predecessor_note"] = " | ".join(reasons) if reasons else None

        results.append(score_data)

    # Ordenar de mayor a menor puntaje
    results.sort(key=lambda x: x["score"], reverse=True)
    return results
