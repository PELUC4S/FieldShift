import json
from backend.services.soilgrids_service import query_soilgrids
from backend.services.nasa_harvester import get_satellite_readings
from backend.engine.crop_ranker import rank_crops
from backend.main import get_climate_data

def test_soilgrids_and_engine():
    lat, lon = -31.42, -64.18
    print("1. Testing query_soilgrids...")
    soil = query_soilgrids(lat, lon)
    assert "texture" in soil
    assert "water_retention" in soil
    assert "organic_carbon" in soil
    assert "acidity" in soil

    tex = soil["texture"]
    wr = soil["water_retention"]
    soc = soil["organic_carbon"]
    acid = soil["acidity"]

    print(f"   Texture USDA: {tex['usda_class']}")
    print(f"   Clay: {tex['clay_pct']}%, Silt: {tex['silt_pct']}%, Sand: {tex['sand_pct']}%")
    print(f"   AWC 0-30cm: {wr['topsoil_0_30cm']['awc_mm']} mm")
    print(f"   AWC 30-60cm: {wr['subsoil_30_60cm']['awc_mm']} mm")
    print(f"   Total AWC 0-60cm: {wr['total_awc_0_60cm_mm']} mm")
    print(f"   SOC: {soc['soc_g_kg']} g/kg (MO: {soc['organic_matter_pct']}%)")
    print(f"   pH: {acid['ph_h2o']} ({acid['ph_class']})")

    print("2. Testing get_climate_data...")
    climate = get_climate_data(lat, lon)

    print("3. Testing rank_crops (May / Winter)...")
    ranking_may = rank_crops(climate, planting_month=5, cycle_months=4, soil_profile=soil)
    top_may = ranking_may[0]
    print(f"   Top May crop: {top_may['crop']} (Score: {top_may['score']})")
    print(f"   Breakdown: {top_may['breakdown']}")
    print(f"   Soil Rationale: {top_may.get('soil_rationale')}")
    assert top_may["score"] > 50, "Winter crop score should not collapse to 0"

    print("4. Testing rank_crops (October / Summer)...")
    ranking_oct = rank_crops(climate, planting_month=10, cycle_months=4, soil_profile=soil)
    top_oct = ranking_oct[0]
    print(f"   Top Oct crop: {top_oct['crop']} (Score: {top_oct['score']})")
    print(f"   Breakdown: {top_oct['breakdown']}")
    print(f"   Soil Rationale: {top_oct.get('soil_rationale')}")

    print("5. Testing satellite readings...")
    sat = get_satellite_readings(lat, lon, climate)
    assert "soil_profile" in sat
    print("ALL TESTS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_soilgrids_and_engine()
