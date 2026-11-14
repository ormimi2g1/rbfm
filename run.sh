#!/usr/bin/env bash
# Start the app (serves the API and the built frontend on one port).
set -euo pipefail
cd "$(dirname "$0")"
python -c "import fastapi, pdfplumber" 2>/dev/null || python -m pip install -q -r requirements.txt
[ -f frontend/dist/index.html ] || (cd frontend && npm ci --no-audit --no-fund && npm run build)
cd backend
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
