#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

failures=0

log() {
  printf '[check] %s\n' "$*"
}

fail() {
  printf '[check] ERROR: %s\n' "$*" >&2
  failures=$((failures + 1))
}

require_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    fail "missing required path: $path"
  fi
}

log "checking required architecture docs"
require_path "README.md"
require_path "DESIGN.md"
require_path "docs/exec-plans/2026-05-13-tech-architecture-constraints.md"
require_path "scripts/check.sh"
require_path "scripts/start.sh"
require_path ".github/workflows/check.yml"
require_path "infra/compose/dev.yml"
require_path "apps/api/cargoflow_api/server.py"
require_path "apps/api/tests/test_server.py"
require_path "apps/web/index.html"
require_path "apps/web/app.js"
require_path "apps/web/navigation-rules.js"
require_path "apps/web/tests/navigation-rules.test.js"
require_path "apps/web/styles.css"

log "checking script syntax"
bash -n scripts/check.sh || fail "shell syntax failed: scripts/check.sh"
bash -n scripts/start.sh || fail "shell syntax failed: scripts/start.sh"

log "checking script executability"
if [[ ! -x "scripts/check.sh" ]]; then
  fail "script is not executable: scripts/check.sh"
fi
if [[ ! -x "scripts/start.sh" ]]; then
  fail "script is not executable: scripts/start.sh"
fi

log "checking architecture decision coverage"
for term in \
  "React" \
  "FastAPI" \
  "PostgreSQL" \
  "MQTT" \
  "pgvector" \
  "Docker Compose" \
  "本地启动命令契约"; do
  if ! grep -q "$term" README.md docs/exec-plans/2026-05-13-tech-architecture-constraints.md; then
    fail "missing architecture term: $term"
  fi
done

log "checking conflict markers"
if command -v rg >/dev/null 2>&1; then
  if rg -n '^(<<<<<<<|=======|>>>>>>>)($| )' .; then
    fail "conflict markers found"
  fi
else
  if grep -RInE '^(<<<<<<<|=======|>>>>>>>)($| )' --exclude-dir=.git .; then
    fail "conflict markers found"
  fi
fi

log "checking Markdown links"
if ! python3 - <<'PY'
import pathlib
import re
import sys
from urllib.parse import unquote, urlparse

root = pathlib.Path.cwd()
link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
missing = []

for path in root.rglob("*.md"):
    if ".git" in path.parts:
        continue
    text = path.read_text(encoding="utf-8")
    for match in link_pattern.finditer(text):
        raw = match.group(1).strip()
        if not raw or raw.startswith("#"):
            continue
        target = raw.split()[0].strip("<>")
        parsed = urlparse(target)
        if parsed.scheme in {"http", "https", "mailto"}:
            continue
        target_path = unquote(parsed.path)
        if not target_path:
            continue
        candidate = (path.parent / target_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            missing.append(f"{path.relative_to(root)}: outside repo link {raw}")
            continue
        if not candidate.exists():
            missing.append(f"{path.relative_to(root)}: missing link {raw}")

if missing:
    print("\n".join(missing), file=sys.stderr)
    sys.exit(1)
PY
then
  fail "Markdown link check failed"
fi

log "checking API skeleton"
export PYTHONPATH="$ROOT_DIR/apps/api${PYTHONPATH:+:$PYTHONPATH}"
python3 -m compileall -q apps/api || fail "python compile failed"
python3 -m unittest discover -s apps/api/tests -p 'test_*.py' || fail "python tests failed"

log "checking HTTP smoke path"
if ! python3 - <<'PY'
import json
from urllib.request import urlopen

from cargoflow_api.server import build_demo_shipment, build_health_payload, create_server

health = build_health_payload()
assert health["status"] == "ok", health

shipment = build_demo_shipment()
assert shipment["latestLocation"]["longitude"], shipment

server = create_server("127.0.0.1", 0)
try:
    host, port = server.server_address
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with urlopen(f"http://{host}:{port}/health", timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["service"] == "cargoflow-api", payload
    from urllib.request import Request

    request = Request(
        f"http://{host}:{port}/api/shipments/demo",
        headers={
            "X-CargoFlow-User-Id": "owner-acme",
            "X-CargoFlow-Role": "cargo_owner",
            "X-CargoFlow-Tenant-Id": "cgf-demo",
        },
    )
    with urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["shipmentId"] == "CGF-DEMO-001", payload
    assert payload["access"]["role"] == "cargo_owner", payload
    request = Request(
        f"http://{host}:{port}/api/shipments/CGF-DEMO-001/latest-location",
        headers={
            "X-CargoFlow-User-Id": "owner-acme",
            "X-CargoFlow-Role": "cargo_owner",
            "X-CargoFlow-Tenant-Id": "cgf-demo",
        },
    )
    with urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["latestLocation"]["updatedAt"], payload
    assert payload["transportStatus"] == "in_transit", payload
    request = Request(
        f"http://{host}:{port}/api/shipments/demo",
        method="OPTIONS",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-CargoFlow-User-Id",
        },
    )
    with urlopen(request, timeout=3) as response:
        assert response.status == 204, response.status
        assert "X-CargoFlow-User-Id" in response.headers["Access-Control-Allow-Headers"]
finally:
    server.shutdown()
    server.server_close()
PY
then
  fail "HTTP smoke path failed"
fi

log "checking frontend asset wiring"
if ! python3 - <<'PY'
from pathlib import Path

index = Path("apps/web/index.html").read_text(encoding="utf-8")
for asset in ("./styles.css", "./navigation-rules.js", "./app.js"):
    if asset not in index:
        raise SystemExit(f"apps/web/index.html does not reference {asset}")
if index.index("./navigation-rules.js") > index.index("./app.js"):
    raise SystemExit("navigation-rules.js must load before app.js")
PY
then
  fail "frontend asset wiring failed"
fi

log "checking frontend navigation rules"
node --check apps/web/navigation-rules.js || fail "navigation rules syntax failed"
node --check apps/web/app.js || fail "frontend app syntax failed"
node apps/web/tests/navigation-rules.test.js || fail "frontend navigation tests failed"

log "checking accidental secret patterns"
secret_terms=(
  "app""_secret"
  "access""_token"
  "-----BEGIN RSA ""PRIVATE KEY-----"
  "-----BEGIN OPENSSH ""PRIVATE KEY-----"
  "-----BEGIN EC ""PRIVATE KEY-----"
)

for term in "${secret_terms[@]}"; do
  if command -v rg >/dev/null 2>&1; then
    if rg -n --fixed-strings --glob '!.git/**' -- "$term" .; then
      fail "secret-like term found: $term"
    fi
  elif grep -RInF --exclude-dir=.git -- "$term" .; then
    fail "secret-like term found: $term"
  fi
done

if [[ "$failures" -ne 0 ]]; then
  printf '[check] failed with %s issue(s)\n' "$failures" >&2
  exit 1
fi

log "all checks passed"
