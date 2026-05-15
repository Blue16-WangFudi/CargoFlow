#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT_DIR/apps/api${PYTHONPATH:+:$PYTHONPATH}"

python3 -m cargoflow_api.server --host "$API_HOST" --port "$API_PORT" &
API_PID=$!

python3 - "$API_HOST" "$API_PORT" <<'PY'
import json
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

host, port = sys.argv[1], sys.argv[2]
url = f"http://{host}:{port}/health"
for _ in range(50):
    try:
        with urlopen(url, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "ok":
            raise SystemExit(0)
    except (OSError, URLError):
        time.sleep(0.1)
print(f"API did not become healthy at {url}", file=sys.stderr)
raise SystemExit(1)
PY

(
  cd "$ROOT_DIR/apps/web"
  python3 -m http.server "$FRONTEND_PORT" --bind "$FRONTEND_HOST"
) &
FRONTEND_PID=$!

cat <<EOF
CargoFlow local services are running.
API health: http://$API_HOST:$API_PORT/health
Demo shipment API: http://$API_HOST:$API_PORT/api/shipments/demo
Frontend console: http://$FRONTEND_HOST:$FRONTEND_PORT
Press Ctrl+C to stop both services.
EOF

wait -n "$API_PID" "$FRONTEND_PID"
