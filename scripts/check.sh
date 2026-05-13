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
require_path ".github/workflows/check.yml"

log "checking script syntax"
bash -n scripts/check.sh || fail "shell syntax failed: scripts/check.sh"

log "checking script executability"
if [[ ! -x "scripts/check.sh" ]]; then
  fail "script is not executable: scripts/check.sh"
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
python3 - <<'PY'
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

if [[ "$failures" -ne 0 ]]; then
  printf '[check] failed with %s issue(s)\n' "$failures" >&2
  exit 1
fi

log "all checks passed"
