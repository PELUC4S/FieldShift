# FieldShift 🌾 — Predicción Probabilística de Rotación de Cultivos
### Desafío Oficial NASA Space Apps: *"Field Shift: Adapting Farms with NASA Data"*

**FieldShift** es una plataforma web AgTech de soporte a la toma de decisiones que combina observaciones de la Tierra y APIs de la **NASA** (climatología, humedad de suelo superficial y radicular, balance hídrico satelital y vegetación) con un motor de inferencia agronómica para:

1. **Evaluar y rankear de forma probabilística (0-100%)** la aptitud de 15 cultivos agrícolas según las coordenadas GPS exactas del lote y la ventana de siembra.
2. **Monitorear en tiempo real las condiciones satelitales del suelo:** Humedad superficial para cama de siembra ($0\text{--}5\text{ cm}$) y reserva en zona radicular ($0\text{--}100\text{ cm}$) con NASA SMAP, balance de 30 días con NASA GPM / OpenET e índice de vegetación NDVI con NASA HLS.
3. **Optimizar la secuencia de rotación de cultivos**, permitiendo al usuario ingresar de forma opcional hasta **3 cultivos antecesores** para calcular el cultivo más recomendable en el siguiente ciclo (balance de nitrógeno, alternancia gramínea-leguminosa, corte de monocultivo y rotación de familias botánicas).

---

## 🏛️ Arquitectura del Sistema

```
FieldShift/
├── backend/
│   ├── main.py                  # API REST FastAPI + Servidor estático
│   ├── analysis/
│   │   └── indicators.py        # Cálculo de normales mensuales, heladas y anomalías de lluvia
│   ├── engine/
│   │   ├── crop_ranker.py       # Motor probabilístico de fitness agroclimático y antecesores
│   │   └── rotation.py          # Motor de rotación sucesoria multianual con memoria de historial
│   ├── services/
│   │   ├── nasa_harvester.py    # Servicio de telemetría NASA (SMAP, GPM IMERG, OpenET, HLS)
│   │   ├── nasa_power.py        # Integración agroclimática NASA POWER (con fallback de contingencia)
│   │   └── info_semillas.json   # Catálogo agronómico de 15 cultivos calibrados
│   └── data/
│       ├── cache/               # Caché persistente JSON por celda geográfica (clima y satélite)
│       └── mock_comparison.json # Datos de referencia de comparación
├── frontend/
│   └── index.html               # SPA interactiva moderna (Leaflet.js, OpenStreetMap, Esri Satélite)
├── index.html                   # Versión raíz para despliegue directo en GitHub Pages
├── requirements.txt             # Dependencias Python
├── start.sh                     # Script de inicio rápido (puerto 8088)
└── README.md
```

---

## 🛰️ Ecosistema de APIs y Fuentes de Datos Satelitales

FieldShift se apoya en los repositorios abiertos del programa **NASA Earth Science Data Systems (EOSDIS)** y servicios asociados:

| API / Fuente | Misión / Sensor | Variables Clave | Propósito Agronómico |
| :--- | :--- | :--- | :--- |
| **NASA POWER API** | MERRA-2 / GEWEX (`power.larc.nasa.gov`) | `T2M`, `T2M_MIN`, `T2M_MAX`, `PRECTOTCORR`, `ALLSKY_SFC_SW_DWN` | Series históricas diarias de 20 años para calcular temperaturas normales, probabilidad de heladas agrometeorológicas ($T_{\min} \le 0^\circ\text{C}$), golpes de calor ($T_{\max} \ge 35^\circ\text{C}$) y variabilidad interanual de lluvias. |
| **NASA SMAP L3** | SMAP Enhanced Radiometer (9 km) | Humedad de suelo superficial ($0\text{--}5\text{ cm}$) en $\text{m}^3/\text{m}^3$ y % | Diagnóstico en tiempo real de la cama de siembra para asegurar una germinación y emergencia uniforme. |
| **NASA SMAP L4** | SMAP SPL4SMGP Global (9 km) | Humedad en zona radicular ($0\text{--}100\text{ cm}$) y anomalía $Z$-score | Disponibilidad de agua útil almacenada en el primer metro de suelo para sostener la evapotranspiración del cultivo. |
| **NASA GPM IMERG** | GPM Core Observatory (0.1° / 10 km) | Precipitación diaria acumulada (`precipitationCal` en mm) | Medición de la oferta hídrica reciente (últimos 30 días) y cálculo del índice de sequía estacional `SPI-3`. |
| **OpenET API** | Ensemble Landsat / Sentinel-2 (30 m) | Evapotranspiración Real ($ET_a$ en mm) | Cuantificación del consumo hídrico real del cultivo antecesor y balance hídrico neto ($P - ET_a$). |
| **ISRIC SoilGrids v2.0** | Global Soil Information System (250 m) | Textura (`clay`, `silt`, `sand`), Retención hídrica (`wv0033`, `wv1500`), Materia Orgánica (`soc`) y Acidez (`phh2o`) | Cuantificación de Agua Disponible ($AWC_{0-30\text{cm}}$ y $AWC_{30-60\text{cm}}$), amortiguación de sequía invernal, fertilidad biológica de nitrógeno y aptitud edafológica del lote. |
| **NASA HLS & ECOSTRESS** | Landsat 8/9, Sentinel-2 e ISS (30 m / 70 m) | Índice de Vegetación `NDVI` y Estrés Evaporativo de Canopia `ESI` | Estimación del vigor vegetativo foliar previo y volumen de rastrojo remanente en el lote. |
| **OpenStreetMap & CartoDB** | OpenStreetMap Community / CARTO | Teselas raster cartográficas | Capa base interactiva para geolocalización ágil del lote. |
| **Esri World Imagery** | Esri / Maxar / Earthstar Geographics | Ortofotos satelitales de alta resolución | Inspección visual satelital de parcelas y bordes del lote. |
| **W3C Geolocation API** | Navegador Web (`navigator.geolocation`) | Latitud y Longitud GPS | Detección automática en un clic de la ubicación del productor en el campo. |

---

## 📡 Endpoints de la API REST (Backend FastAPI)

La API corre por defecto en `http://localhost:8088`. La documentación interactiva Swagger se encuentra en `/docs`.

### 1. Analítica Integral y Satelital
* **`GET /api/v1/field-analytics`**: Retorna el contrato integral de analítica del lote: metadatos geográficos, perfil de suelo, telemetría NASA (SMAP, GPM, OpenET, HLS), historial de antecesores, ranking y rotación sucesoria recomendada con justificación agronómica.  
  *Query params:* `lat`, `lon`, `planting_month` (1-12), `cycle_months` (2-8), `num_cycles` (1-10), `previous_crops` (ej. `soja,trigo,maiz`).
* **`GET /api/v1/satellite-readings`**: Lecturas satelitales en tiempo real de humedad de suelo, balance hídrico, sequía `SPI-3` y vigor vegetal por celda espacial con caché local.  
  *Query params:* `lat`, `lon`.
* **`GET /api/v1/soil-profile`**: Perfil edafológico normalizado de ISRIC SoilGrids v2.0 (textura USDA, porcentajes de arcilla/limo/arena en $0\text{--}30\text{ cm}$, retención hídrica $AWC$ en $0\text{--}30\text{ cm}$ y $30\text{--}60\text{ cm}$, carbono orgánico $SOC$ y $\text{pH}$).  
  *Query params:* `lat`, `lon`.

### 2. Predicción de Cultivos y Rotación
* **`GET /api/v1/ranking`**: Ranking probabilístico (0 a 100%) de las 15 semillas evaluando clima y bonificaciones de rotación sucesoria contra los cultivos anteriores.  
  *Query params:* `lat`, `lon`, `planting_month`, `cycle_months`, `previous_crops`.
* **`POST /api/v1/rotation`**: Genera la secuencia secuencial de rotación multi-ciclo optimizada agronómicamente (gramínea $\leftrightarrow$ leguminosa, reposición de N, corte de monocultivo).  
  *JSON Body:* `{"latitude": -31.42, "longitude": -64.18, "start_month": 10, "num_cycles": 3, "previous_crops": ["soja", "trigo"]}`.
* **`GET /api/v1/next-crop`**: Sugerencia directa del mejor cultivo sucesor inmediato tras un antecesor cosechado específico.  
  *Query params:* `predecessor`, `planting_month`, `lat`, `lon`, `cycle_months`.

### 3. Catálogo y Climatología
* **`GET /api/v1/crops`**: Catálogo completo de las 15 semillas agronómicas con sus requerimientos térmicos, hídricos, tolerancia a helada/calor, efectos en nitrógeno y compatibilidades.
* **`GET /api/v1/climate`** o **`GET /climate`**: Indicadores agroclimáticos históricos de 20 años de NASA POWER (temperaturas normales, lluvia anual, heladas agrometeorológicas, golpes de calor y lecturas satelitales integradas).  
  *Query params:* `lat`, `lon`.
* **`POST /compare`**: Simulación comparativa de escenarios de rotación y prioridades agronómicas.
* **`GET /health`**: Verificación de salud y estado operativo del servicio (`{"status": "ok"}`).

---

## 🌾 Catálogo Agronómico de Cultivos Soportados

El sistema evalúa 15 especies agrícolas estratégicas calibradas con datos del INTA y literatura agronómica:

* **Gramíneas / Cereales:** Maíz (`maiz`), Trigo (`trigo`), Sorgo (`sorgo`), Cebada (`cebada`), Avena (`avena`), Centeno de Cobertura (`centeno_cobertura`).
* **Leguminosas / Fijadoras de N:** Soja (`soja`), Garbanzo (`garbanzo`), Maní (`mani`), Arveja (`arveja`), Vicia (`vicia`).
* **Oleaginosas y Especiales:** Girasol (`girasol`), Colza / Canola (`colza`), Algodón (`algodon`), Papa (`papa`).

---

## 💻 Puesta en Marcha en Local

### Requisitos
* Python 3.9 o superior.

### Pasos
1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/PELUC4S/FieldShift.git
   cd FieldShift
   ```

2. **Instalar dependencias:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Iniciar el servidor:**
   ```bash
   ./start.sh
   # O manualmente:
   uvicorn backend.main:app --host 127.0.0.1 --port 8088 --reload
   ```

4. **Acceder en el navegador:**
   * **Aplicación Web:** [http://localhost:8088](http://localhost:8088)
   * **Documentación Interactiva Swagger:** [http://localhost:8088/docs](http://localhost:8088/docs)

---

## 🌐 Despliegue en GitHub Pages (Modo Standalone)

FieldShift cuenta con arquitectura de **conmutación transparente**:
* Si el backend FastAPI local no está activo (por ejemplo, en un entorno de demostración estático), el frontend activa automáticamente su **motor agroclimático y satelital integrado en JavaScript** con normales calibradas y modelos físicos locales.
* **Demo en Vivo:** [https://peluc4s.github.io/FieldShift/](https://peluc4s.github.io/FieldShift/)
