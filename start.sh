#!/usr/bin/env bash
# Script de inicio para FieldShift (Backend FastAPI + Frontend estático)

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/backend"

PORT=8088

echo "=========================================================="
echo "🌱 Iniciando FieldShift — Predicción de Rotación de Cultivos"
echo "=========================================================="
echo "Accedé a la aplicación en: http://localhost:$PORT"
echo "Documentación interactiva de la API: http://localhost:$PORT/docs"
echo "=========================================================="

if [ -f "$DIR/.venv/bin/uvicorn" ]; then
    "$DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port $PORT --reload
else
    uvicorn main:app --host 127.0.0.1 --port $PORT --reload
fi
