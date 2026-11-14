"""
rotation.py – Motor de secuencias de rotación de cultivos.

Genera secuencias de rotación multi-ciclo considerando compatibilidad
de antecesores, alternancia gramínea/leguminosa, rotación de familias
botánicas, balance de nitrógeno y timing de siembra/cosecha.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .crop_ranker import rank_crops, compute_crop_score, _load_crops

# ─── Constantes ─────────────────────────────────────────────────────────────
ROTATION_BONUS = 15.0    # Bonus por buen antecesor
ROTATION_PENALTY = -15.0  # Penalización por mal antecesor

# Mapeo de tipos generales a categorías para alternancia
_TYPE_CATEGORY = {
    "Gramínea": "graminea",
    "Gramínea de invierno": "graminea",
    "Leguminosa": "leguminosa",
    "Leguminosa de invierno": "leguminosa",
    "Compuesta": "otra",
    "Crucífera de invierno": "otra",
}

# Definición de períodos estándar de siembra (verano / invierno)
SUMMER_MONTHS = [9, 10, 11, 12]   # Sep-Dic
WINTER_MONTHS = [3, 4, 5, 6, 7]    # Mar-Jul


def _get_category(crop: dict) -> str:
    """Obtiene la categoría funcional del cultivo (graminea/leguminosa/otra)."""
    return _TYPE_CATEGORY.get(crop.get("type", ""), "otra")


def _is_winter_crop(crop: dict) -> bool:
    """Determina si un cultivo es de invierno basado en sus meses de siembra."""
    sowing = set(crop.get("sowing_months", []))
    return bool(sowing & set(WINTER_MONTHS)) and not bool(sowing & {10, 11, 12})


def _is_summer_crop(crop: dict) -> bool:
    """Determina si un cultivo es de verano basado en sus meses de siembra."""
    sowing = set(crop.get("sowing_months", []))
    return bool(sowing & {9, 10, 11, 12})


# ─── Evaluación de compatibilidad entre cultivos ───────────────────────────

def predecessor_compatibility(
    current_crop_key: str,
    current_crop: dict,
    predecessor_key: str | None,
    predecessor_crop: dict | None,
) -> tuple[float, list[str]]:
    """
    Evalúa la compatibilidad del cultivo actual con su antecesor.

    Retorna:
    - bonus/penalty: valor numérico (+15 / -15 / 0)
    - razones: lista de strings explicando la decisión
    """
    if predecessor_key is None or predecessor_crop is None:
        return 0.0, ["Sin antecesor – primer cultivo de la secuencia"]

    bonus = 0.0
    reasons: list[str] = []

    # 1. good_predecessors / bad_predecessors explícitos
    good_preds = current_crop.get("good_predecessors", [])
    bad_preds = current_crop.get("bad_predecessors", [])

    if predecessor_key in good_preds:
        bonus += ROTATION_BONUS * 0.4
        reasons.append(f"✓ {predecessor_key} es buen antecesor para {current_crop_key}")
    if predecessor_key in bad_preds:
        bonus += ROTATION_PENALTY * 0.4
        reasons.append(f"✗ {predecessor_key} es mal antecesor para {current_crop_key}")

    # 2. Alternancia gramínea ↔ leguminosa
    curr_cat = _get_category(current_crop)
    pred_cat = _get_category(predecessor_crop)

    if curr_cat != pred_cat and curr_cat in ("graminea", "leguminosa") and pred_cat in ("graminea", "leguminosa"):
        bonus += ROTATION_BONUS * 0.25
        reasons.append("✓ Alternancia gramínea-leguminosa")
    elif curr_cat == pred_cat and curr_cat in ("graminea", "leguminosa"):
        bonus += ROTATION_PENALTY * 0.15
        reasons.append("✗ Misma categoría funcional consecutiva")

    # 3. Rotación de familias botánicas
    curr_family = current_crop.get("family", "")
    pred_family = predecessor_crop.get("family", "")

    if curr_family and pred_family:
        if curr_family == pred_family:
            bonus += ROTATION_PENALTY * 0.2
            reasons.append(f"✗ Misma familia botánica ({curr_family}) consecutiva")
        else:
            bonus += ROTATION_BONUS * 0.15
            reasons.append(f"✓ Rotación de familia ({pred_family} → {curr_family})")

    # 4. Balance de nitrógeno
    pred_nitrogen = predecessor_crop.get("soil_nitrogen_effect", "neutral")
    curr_nitrogen = current_crop.get("soil_nitrogen_effect", "neutral")

    if pred_nitrogen == "fijador" and curr_nitrogen in ("consumidor_alto", "consumidor_medio"):
        bonus += ROTATION_BONUS * 0.2
        reasons.append("✓ Aprovecha nitrógeno fijado por antecesor")
    elif pred_nitrogen == "consumidor_alto" and curr_nitrogen == "consumidor_alto":
        bonus += ROTATION_PENALTY * 0.15
        reasons.append("✗ Dos consumidores altos de N consecutivos")
    elif pred_nitrogen == "consumidor_alto" and curr_nitrogen == "fijador":
        bonus += ROTATION_BONUS * 0.1
        reasons.append("✓ Fijador después de consumidor alto: recupera N")

    # Limitar bonus total al rango ±15
    bonus = max(ROTATION_PENALTY, min(ROTATION_BONUS, bonus))

    if not reasons:
        reasons.append("Compatibilidad neutral")

    return round(bonus, 1), reasons


def _timing_compatible(predecessor_crop: dict, current_crop: dict) -> bool:
    """
    Verifica que exista al menos una ventana de siembra viable para el cultivo actual
    luego o coincidiendo con el final del ciclo del cultivo antecesor.
    """
    harvest_months = set(predecessor_crop.get("harvest_months", []))
    sowing_months = set(current_crop.get("sowing_months", []))

    if not harvest_months or not sowing_months:
        return True

    for h in harvest_months:
        for s in sowing_months:
            # Misma temporada hacia adelante (mismo año calendario)
            if s >= h:
                return True
            # Transición de fin de año al año siguiente (ej. cosecha sep-dic -> siembra ene-jul)
            if h >= 9 and s <= 7:
                return True
            # Transición de mitad de año a primavera (ej. cosecha jul-ago -> siembra sep-dic)
            if h <= 8 and s >= 9:
                return True

    return False


# ─── Generación de secuencias de rotación ───────────────────────────────────

def _determine_planting_month(crop: dict, is_summer_slot: bool) -> int:
    """Determina el mejor mes de siembra según el slot (verano/invierno)."""
    sowing = crop.get("sowing_months", [])
    if not sowing:
        return 10 if is_summer_slot else 5

    if is_summer_slot:
        # Preferir meses de primavera-verano
        summer_options = [m for m in sowing if m in SUMMER_MONTHS]
        return summer_options[0] if summer_options else sowing[0]
    else:
        # Preferir meses de otoño-invierno
        winter_options = [m for m in sowing if m in WINTER_MONTHS]
        return winter_options[0] if winter_options else sowing[0]


def build_rotation(
    climate_data: dict,
    num_cycles: int = 3,
    start_month: int = 10,
    first_crop_key: str | None = None,
    weights: dict[str, float] | None = None,
    crops_db: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Construye una secuencia de rotación completa.

    Cada ciclo anual tiene hasta 2 slots:
    - Slot de verano (siembra sep-dic)
    - Slot de invierno (siembra mar-jul)

    Args:
        climate_data: Datos climáticos de la ubicación
        num_cycles: Número de años (cada año = hasta 2 cultivos)
        start_month: Mes de inicio de la planificación
        first_crop_key: Clave del primer cultivo (opcional, se elige el mejor)
        weights: Pesos personalizados para el ranking
        crops_db: Base de datos de cultivos (opcional, carga automática)

    Returns:
        Diccionario con la secuencia de rotación completa y métricas.
    """
    crops = crops_db or _load_crops()
    sequence: list[dict[str, Any]] = []
    prev_key: str | None = None
    prev_crop: dict | None = None

    # Determinar si empezamos con slot de verano o invierno
    start_is_summer = start_month in SUMMER_MONTHS or start_month in [8, 9]

    total_slots = num_cycles * 2  # Dos cultivos por año (verano + invierno)

    for slot_idx in range(total_slots):
        # Alternar verano / invierno
        if start_is_summer:
            is_summer_slot = (slot_idx % 2 == 0)
        else:
            is_summer_slot = (slot_idx % 2 == 1)

        cycle_year = slot_idx // 2 + 1
        season = "verano" if is_summer_slot else "invierno"

        # Filtrar cultivos válidos para este slot
        valid_crops: dict[str, dict] = {}
        for key, crop in crops.items():
            sowing = set(crop.get("sowing_months", []))
            if is_summer_slot and sowing & set(SUMMER_MONTHS):
                valid_crops[key] = crop
            elif not is_summer_slot and sowing & set(WINTER_MONTHS):
                valid_crops[key] = crop

        if not valid_crops:
            continue

        # Determinar mes de siembra para el ranking
        if slot_idx == 0 and first_crop_key and first_crop_key in crops:
            # Primer cultivo especificado por el usuario
            chosen_key = first_crop_key
            chosen_crop = crops[first_crop_key]
            planting_m = _determine_planting_month(chosen_crop, is_summer_slot)
            cycle_m = chosen_crop["cycle_days_mid"] // 30 or 4

            base_score = compute_crop_score(
                chosen_key, chosen_crop, climate_data,
                planting_m, cycle_m, weights
            )
            rotation_bonus = 0.0
            compat_reasons = ["Primer cultivo seleccionado por el usuario"]
        else:
            # Rankear cultivos válidos y elegir el mejor considerando rotación
            best_choice: dict | None = None
            best_total_score = -999.0

            for key, crop in valid_crops.items():
                planting_m = _determine_planting_month(crop, is_summer_slot)
                cycle_m = crop["cycle_days_mid"] // 30 or 4

                # Verificar compatibilidad de timing con antecesor
                if prev_crop and not _timing_compatible(prev_crop, crop):
                    continue

                score_data = compute_crop_score(
                    key, crop, climate_data, planting_m, cycle_m, weights
                )
                rot_bonus, reasons = predecessor_compatibility(
                    key, crop, prev_key, prev_crop
                )

                # Penalización por repetición reciente en la secuencia (fomentar diversidad plurianual)
                recent_crops = [e["crop_key"] for e in sequence]
                recency_penalty = 0.0
                if key in recent_crops[-2:]:
                    recency_penalty = 7.0
                    reasons.append("✗ Cultivo utilizado recientemente en la rotación (desincentivo a la repetición)")

                total = score_data["score"] + rot_bonus - recency_penalty

                if total > best_total_score:
                    best_total_score = total
                    best_choice = {
                        "key": key,
                        "crop": crop,
                        "score_data": score_data,
                        "rotation_bonus": rot_bonus - recency_penalty,
                        "reasons": reasons,
                        "planting_m": planting_m,
                        "cycle_m": cycle_m,
                    }

            if best_choice is None:
                continue

            chosen_key = best_choice["key"]
            chosen_crop = best_choice["crop"]
            base_score = best_choice["score_data"]
            rotation_bonus = best_choice["rotation_bonus"]
            compat_reasons = best_choice["reasons"]
            planting_m = best_choice["planting_m"]
            cycle_m = best_choice["cycle_m"]

        # Calcular mes de cosecha estimado
        harvest_month = ((planting_m - 1 + (chosen_crop["cycle_days_mid"] // 30)) % 12) + 1

        # Construir entrada de la secuencia
        adjusted_score = max(0.0, min(100.0, base_score["score"] + rotation_bonus))
        crop_title = chosen_key.replace("_", " ").title()
        succ_reason = " | ".join(compat_reasons) if compat_reasons else "Secuencia agronómicamente recomendada"

        entry = {
            "slot": slot_idx + 1,
            "cycle_year": cycle_year,
            "season": season,
            "crop": chosen_key,
            "crop_key": chosen_key,
            "name": crop_title,
            "crop_name": crop_title,
            "crop_type": chosen_crop.get("type", ""),
            "family": chosen_crop.get("family", ""),
            "water_consumption": chosen_crop.get("water_consumption", "Medio"),
            "planting_month": planting_m,
            "harvest_month": harvest_month,
            "estimated_harvest_month": harvest_month,
            "base_score": base_score["score"],
            "rotation_bonus": round(rotation_bonus, 1),
            "bonus": round(rotation_bonus, 1),
            "score": round(adjusted_score, 1),
            "adjusted_score": round(adjusted_score, 1),
            "score_breakdown": base_score["breakdown"],
            "compatibility_notes": compat_reasons,
            "succession_reason": succ_reason,
            "nitrogen_effect": chosen_crop.get("soil_nitrogen_effect", "neutral"),
        }

        sequence.append(entry)
        prev_key = chosen_key
        prev_crop = chosen_crop

    # Métricas resumen de la rotación
    avg_score = sum(e["adjusted_score"] for e in sequence) / len(sequence) if sequence else 0
    families_used = set(e["family"] for e in sequence)
    categories_used = set(_get_category(crops.get(e["crop_key"], {})) for e in sequence)
    nitrogen_balance = sum(
        1 if e["nitrogen_effect"] == "fijador"
        else (-1 if e["nitrogen_effect"] == "consumidor_alto" else 0)
        for e in sequence
    )

    # Beneficios dinámicos agronómicos
    benefits: list[str] = []
    if any(e["nitrogen_effect"] == "fijador" for e in sequence):
        benefits.append("Fijación biológica de N: el uso de leguminosas repone nitrógeno y reduce la dependencia de fertilizantes de síntesis.")
    if len(families_used) >= 2:
        benefits.append(f"Diversificación biológica ({', '.join(sorted(families_used))}): corta ciclos de patógenos edáficos (ej. Fusarium) y malezas difíciles.")
    if any("Gramínea" in e["crop_type"] for e in sequence):
        benefits.append("Aporte de rastrojo y cobertura: las gramíneas incrementan la materia orgánica y protegen contra la erosión hídrica y eólica.")
    if any(e["crop_key"] in ["girasol", "colza", "papa"] for e in sequence):
        benefits.append("Exploración profunda del perfil: raíces pivotantes mejoran la porosidad y captación de agua en capas profundas.")
    if not benefits:
        benefits.append("Alternancia equilibrada entre ciclos de siembra y descansos estacionales.")

    # Advertencias agronómicas
    warnings: list[str] = []
    if nitrogen_balance < 0:
        warnings.append("Balance neto de nitrógeno negativo: prever fertilización estratégica en los cultivos demandantes.")
    high_water_count = sum(1 for e in sequence if e.get("water_consumption") == "Alto")
    if high_water_count >= 2:
        warnings.append("Alta demanda hídrica acumulada: verificar recarga del perfil de suelo antes de la siembra.")
    if len(families_used) == 1:
        warnings.append("Baja diversidad botánica: rotar familias para evitar incremento de plagas específicas.")
    if not warnings:
        warnings.append("Monitorear condiciones hídricas de la cama de siembra durante la ventana de implantación.")

    return {
        "num_cycles": num_cycles,
        "total_crops": len(sequence),
        "total_score": round(avg_score, 1),
        "average_score": round(avg_score, 1),
        "benefits": benefits,
        "warnings": warnings,
        "sequence": sequence,
        "summary": {
            "average_adjusted_score": round(avg_score, 1),
            "families_used": sorted(families_used),
            "functional_categories": sorted(categories_used),
            "nitrogen_balance": nitrogen_balance,
            "nitrogen_balance_label": (
                "positivo (acumula N)" if nitrogen_balance > 0
                else "negativo (consume N)" if nitrogen_balance < 0
                else "neutro"
            ),
            "diversity_index": len(set(e["crop_key"] for e in sequence)),
        },
    }


def suggest_next_crop(
    predecessor_key: str,
    climate_data: dict,
    next_planting_month: int,
    cycle_months: int = 4,
    weights: dict[str, float] | None = None,
    crops_db: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Dado un cultivo antecesor y un mes de siembra planificado, rankea probabilísticamente
    las mejores opciones de cultivo sucesor aplicando las reglas agronómicas de rotación.
    """
    crops = crops_db or _load_crops()
    if predecessor_key not in crops:
        raise ValueError(f"Cultivo antecesor '{predecessor_key}' no reconocido en base agronómica.")

    pred_crop = crops[predecessor_key]
    candidates = []

    for key, crop in crops.items():
        if key == predecessor_key:
            # Monocultivo directo fuertemente desaconsejado
            continue

        if not _timing_compatible(pred_crop, crop):
            continue

        score_data = compute_crop_score(
            key, crop, climate_data, next_planting_month, cycle_months, weights
        )
        rot_bonus, reasons = predecessor_compatibility(key, crop, predecessor_key, pred_crop)
        adjusted_score = max(0.0, min(100.0, score_data["score"] + rot_bonus))

        candidates.append({
            "crop": key,
            "crop_key": key,
            "name": key.replace("_", " ").title(),
            "crop_name": key.replace("_", " ").title(),
            "predecessor": predecessor_key,
            "base_score": score_data["score"],
            "rotation_bonus": rot_bonus,
            "adjusted_score": round(adjusted_score, 1),
            "score": round(adjusted_score, 1),
            "reasons": reasons,
            "nitrogen_effect": crop.get("soil_nitrogen_effect", "neutral"),
            "family": crop.get("family", ""),
            "water_consumption": crop.get("water_consumption", "Medio"),
            "breakdown": score_data["breakdown"],
        })

    candidates.sort(key=lambda x: x["adjusted_score"], reverse=True)
    return candidates
