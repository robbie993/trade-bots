#!/usr/bin/env bash
# Bring up the whole thing: schema, opening balance, web approval gate.
# Spec section 12.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

HOST="${MVV_GATE_HOST:-127.0.0.1}"
PORT="${MVV_GATE_PORT:-8000}"

echo "==> applying migrations"
python -m src.main init-db

echo "==> opening balance (no-op if already open)"
python -m src.main cash open --once

echo "==> configuration"
python -m src.main config

echo
echo "==> approval gate on http://${HOST}:${PORT}"
echo "    This page has no authentication. Keep it on localhost unless you"
echo "    have put your own auth in front of it."
exec python -m src.main serve --host "$HOST" --port "$PORT"
