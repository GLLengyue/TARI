#!/usr/bin/env bash
# Quick health check for the detached Odyssey compile job.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG_DIR=logs/odyssey
WS=runtime-data/story-books/odyssey
echo "=== odyssey job health ==="
if [[ -f "$LOG_DIR/odyssey.pid" ]]; then
  pid=$(cat "$LOG_DIR/odyssey.pid")
  if kill -0 "$pid" 2>/dev/null; then
    echo "process: alive pid=$pid"
  else
    echo "process: last pid=$pid is not running"
  fi
else
  echo "process: no pid file (never started?)"
fi
echo "log: $LOG_DIR/odyssey.log ($(wc -l < "$LOG_DIR/odyssey.log" 2>/dev/null || echo 0) lines)"
echo "--- last 8 log lines ---"
tail -n 8 "$LOG_DIR/odyssey.log" 2>/dev/null || echo "(no log)"
echo "--- workspace ---"
if [[ -d "$WS" ]]; then
  ls -la "$WS" 2>/dev/null | head
  if [[ -f "$WS/manifest.json" ]]; then
    .venv/bin/python - <<'PY'
import json, os
p = "runtime-data/story-books/odyssey/manifest.json"
if os.path.isfile(p):
    data = json.load(open(p, encoding="utf-8"))
    print("status:", data.get("status"))
    print("stages:", data.get("stages"))
    print("cards:", data.get("card_count"),
          "arcs:", data.get("arc_count"),
          "entities:", data.get("entity_count"),
          "facts:", data.get("fact_count"))
PY
  fi
else
  echo "(workspace not yet created)"
fi
