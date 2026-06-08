#!/usr/bin/env bash
# Start backend (FastAPI) + frontend (Vite) for local development.
set -e
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then echo "missing .env"; exit 1; fi
set -a; source .env; set +a

.venv/bin/python backend/server.py &
BACKEND=$!
echo "backend pid=$BACKEND (http://127.0.0.1:8000)"

( cd web && npm run dev ) &
FRONT=$!
echo "frontend pid=$FRONT (http://127.0.0.1:5173)"

trap "kill $BACKEND $FRONT 2>/dev/null" EXIT INT TERM
wait
