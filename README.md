# FieldShift 🌾 — Predicción Probabilística de Rotación de Cultivos

**FieldShift** es una aplicación web Full-Stack AgTech que combina datos meteorológicos e históricos de la API de **NASA POWER** con un motor de inferencia agronómica para:
1. **Evaluar y rankear de forma probabilística (0-100%)** la aptitud de diferentes semillas/cultivos según la ubicación geográfica y la ventana de siembra ingresada por el usuario.
2. **Definir la secuencia óptima de rotación de cultivos**, determinando qué semilla corresponde cultivar después de la anterior en base a compatibilidad de antecesores, fijación biológica de nitrógeno, rotación de familias botánicas y calendarios de siembra/cosecha.

---

## 🏛️ Arquitectura del MVP

```
FieldShift/
├── backend/
│   ├── main.py                  # API REST FastAPI + Servidor estático de Frontend
│   ├── analysis/
│   │   ├── indicators.py        # Cálculo de normales mensuales, heladas y anomalías de lluvia
│   ├── engine/
│   │   ├── crop_ranker.py       # Motor probabilístico de fitness agroclimático
│   │   └── rotation.py          # Motor de compatibilidad de antecesores y rotación multianual
│   ├── services/
│   │   ├── nasa_power.py        # Integración con NASA POWER API (con fallback de contingencia)
│   │   └── info_semillas.json   # Base agronómica de cultivos (Poaceae, Fabaceae, Asteraceae, etc.)
│   └── data/
│       ├── cache/               # Caché persistente JSON de NASA POWER por coordenadas
│       └── mock_comparison.json # Datos de referencia de comparación
├── frontend/
│   └── index.html               # SPA interactiva moderna (HTML5, CSS3 responsive, Canvas Map, JS)
├── requirements.txt             # Dependencias Python
├── start.sh                     # Script de inicio rápido
└── README.md
```

---

## 🔬 Metodología Agronómica y Cálculo Probabilístico

### 1. Ingesta Climatológica (NASA POWER API)
Se obtienen series de tiempo diarias de 20 años de las siguientes variables (comunidad `AG`):
- `T2M`: Temperatura media diaria a 2 metros (°C).
- `T2M_MIN`: Temperatura mínima diaria (°C).
- `T2M_MAX`: Temperatura máxima diaria (°C).
- `PRECTOTCORR`: Precipitación diaria corregida (mm).

A partir de estas series se calculan las normales agroclimáticas mensuales, la probabilidad y recurrencia de heladas ($T_{\min} \le 0^\circ\text{C}$), días de estrés térmico ($T_{\max} \ge 35^\circ\text{C}$) y el coeficiente de variación (CV%) de las precipitaciones interanuales.

### 2. Función de Aptitud Probabilística ($S_{\text{cultivo}}$)
Para cada cultivo candidato, se evalúa:
- **Aptitud Térmica ($S_{\text{temp}} \in [0, 100]$):** Función de proximidad al rango óptimo térmico y penalización por límites biológicos absolutos.
- **Aptitud Hídrica ($S_{\text{precip}} \in [0, 100]$):** Comparación de la lluvia acumulada esperada durante el ciclo respecto a la necesidad mínima y óptima del cultivo.
- **Seguridad frente a Heladas ($S_{\text{frost}} \in [0, 100]$):** Días con heladas esperados en los meses del ciclo ponderados por la tolerancia genética (`nula`, `baja`, `media`, `alta`).
- **Seguridad frente a Estrés Térmico ($S_{\text{heat}} \in [0, 100]$):** Días con calor extremo ponderados por la resistencia del cultivar.
- **Estabilidad de Lluvias ($S_{\text{var}} \in [0, 100]$):** Resiliencia frente a la variabilidad histórica y riesgo de sequía extrema.
- **Ventana Biológica de Siembra ($F_{\text{siembra}}$):** Penalización si el mes de siembra no coincide con la ventana recomendada por INTA/literatura agronómica.

$$\text{Aptitud Base} = \left( 0.30 S_{\text{temp}} + 0.30 S_{\text{precip}} + 0.15 S_{\text{frost}} + 0.10 S_{\text{heat}} + 0.15 S_{\text{var}} \right) \times F_{\text{siembra}}$$

### 3. Algoritmo de Rotación y Sucesión de Cultivos
Para definir qué cultivo sigue al anterior, se aplican las 5 reglas clásicas de rotación agronómica:
1. **Matriz de Antecesores:** Evaluación explícita de `good_predecessors` (+4 bonus) y `bad_predecessors` (-4 penalidad).
2. **Alternancia Funcional:** Gramínea $\leftrightarrow$ Leguminosa (+2.5 bonus) para optimizar la relación Carbono/Nitrógeno (C/N) del rastrojo.
3. **Rotación de Familias Botánicas:** Alternar Poaceae $\to$ Fabaceae $\to$ Asteraceae / Brassicaceae (+1.5 bonus) para romper ciclos biológicos de patógenos (Fusarium, Macrophomina) y malezas tolerantes.
4. **Dinámica del Nitrógeno:** Leguminosa fijadora previa a gramínea de alta demanda (+2.0 bonus) o fijadora tras consumidor alto (+1.0 bonus) para recuperar fertilidad.
5. **Compatibilidad Temporal y Estacional:** Verificación estricta de que la fecha de cosecha del antecesor permita implantar el siguiente cultivo respetando los ciclos de barbecho.

---

## 🚀 Instalación y Puesta en Marcha

### Requisitos
- Python 3.9 o superior.

### Pasos
1. **Activar el entorno virtual e instalar dependencias:**
   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Iniciar el servidor:**
   ```bash
   ./start.sh
   # o bien:
   uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Abrir en el navegador:**
   - Aplicación Web: [http://localhost:8000](http://localhost:8000)
   - Documentación Interactiva Swagger/OpenAPI: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 📡 Endpoints de la API

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/v1/ranking?lat=...&lon=...&planting_month=...&cycle_months=...` | Rankea probabilísticamente todas las semillas según clima y fecha de siembra. |
| `POST` | `/api/v1/rotation` | Genera la secuencia completa multi-ciclo de rotación con justificaciones agronómicas. |
| `GET` | `/api/v1/next-crop?predecessor=...&planting_month=...&lat=...&lon=...` | Define qué cultivo corresponde sembrar inmediatamente después de un antecesor específico. |
| `GET` | `/api/v1/climate?lat=...&lon=...` | Obtiene normales mensuales, heladas y lluvias históricas NASA POWER. |
| `GET` | `/api/v1/crops` | Catálogo de semillas y atributos agronómicos. |
| `GET` | `/health` | Healthcheck del sistema. |
