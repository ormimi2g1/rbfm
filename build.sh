#!/usr/bin/env bash
# Install dependencies and build the React frontend.
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -q -r requirements.txt
cd frontend
npm ci --no-audit --no-fund
npm run build
