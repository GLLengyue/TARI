#!/usr/bin/env bash
# Detached Odyssey compilation job.
#
# Runs the TARI `story-compile` against the local Qwen endpoint, fully
# decoupled from the calling shell: it survives session disconnects, writes
# its own pid/log, and can be inspected/resumed without this shell.
#
# Usage:
#   bash scripts/run_odyssey_compile.sh             # resumable run
#   bash scripts/run_odyssey_compile.sh --rebuild   # discard cache
#   bash scripts/run_odyssey_compile.sh --status    # tail latest log
#   bash scripts/run_odyssey_compile.sh --pid <pid> # watch a specific pid
#   bash scripts/run_odyssey_compile.sh --stop      # kill the latest pid
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO"

LOG_DIR="$REPO/logs/odyssey"
RUN_DIR="$REPO/runtime-data/sources/odyssey"
SOURCE="$RUN_DIR/odyssey.txt"
OUTPUT_DIR="$REPO/runtime-data/story-books/odyssey"
PID_FILE="$LOG_DIR/odyssey.pid"
LOG_FILE="$LOG_DIR/odyssey.log"
ENV_FILE="$REPO/.env"

mkdir -p "$LOG_DIR" "$RUN_DIR" "$OUTPUT_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo "[$(date -Iseconds)] loaded env from $ENV_FILE"
fi

latest_pid() {
  if [[ -f "$PID_FILE" ]]; then
    cat "$PID_FILE"
  fi
}

case "${1:-run}" in
  --status)
    pid=$(latest_pid || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "running pid=$pid log=$LOG_FILE"
      tail -n 20 "$LOG_FILE" 2>/dev/null || true
      exit 0
    fi
    echo "no live odyssey job (last pid=${pid:-none})"
    tail -n 20 "$LOG_FILE" 2>/dev/null || true
    exit 0
    ;;
  --stop)
    pid=$(latest_pid || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true
      echo "stopped pid=$pid"
    else
      echo "no live odyssey job"
    fi
    exit 0
    ;;
  --pid)
    pid="${2:-}"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      echo "pid $pid not alive"
      exit 1
    fi
    tail -n 20 "$LOG_FILE" 2>/dev/null || true
    exit 0
    ;;
esac

if [[ ! -s "$SOURCE" ]]; then
  echo "source missing: $SOURCE" >&2
  exit 1
fi

if [[ "${1:-run}" == "--rebuild" ]]; then
  extra_args=(--rebuild)
  mode=rebuild
else
  extra_args=()
  mode=resumable
fi

# shellcheck disable=SC2206  # word splitting is intentional here
cli_args=(
  "$SOURCE"
  --output-dir "$OUTPUT_DIR"
  --story-id odyssey
  --title "The Odyssey"
  --lang en
  --parallelism 2
  --chapter-chars 20000
  --window-chapters 4
  --max-arc-chapters 4
  --world-batch-chapters 6
  --volume-size 12
)
if [[ ${#extra_args[@]} -gt 0 ]]; then
  cli_args+=("${extra_args[@]}")
fi

export EVOT_LLM_PROVIDER=openai
export EVOT_LLM_OPENAI_PROTOCOL=openai
export EVOT_LLM_OPENAI_BASE_URL="${EVOT_LLM_OPENAI_BASE_URL:-http://127.0.0.1:8080/v1}"
# llama-server exposes the model as the on-disk GGUF filename, which varies per
# deployment. The generic alias below works for hosted endpoints; point
# EVOT_LLM_OPENAI_MODEL (or TARI_LLM_MODEL) at your local server's model id.
_base_host=$(printf '%s' "$EVOT_LLM_OPENAI_BASE_URL" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##')
case "$_base_host" in
  192.168.*|10.*|127.*|0.0.0.0|localhost|::1|*.local)
    : "${EVOT_LLM_OPENAI_MODEL:=qwen3.8-27b}"
    ;;
  *)
    : "${EVOT_LLM_OPENAI_MODEL:=qwen3.8-27b}"
    ;;
esac
export EVOT_LLM_OPENAI_MODEL

# Local llama-server (default 127.0.0.1) does not require a key. Only when
# pointing at a cloud endpoint (public DNS / 域名、host 中包含 .com/.cn 等)
# 才必须提供 API key。这样脚本默认可直接跑本地服务；外部注入时也能
# 一键切到云端 endpoint。
_base_host=$(printf '%s' "$EVOT_LLM_OPENAI_BASE_URL" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##')
case "$_base_host" in
  192.168.*|10.*|127.*|0.0.0.0|localhost|::1|*.local)
    : "${EVOT_LLM_OPENAI_API_KEY:=no-key-needed}"
    ;;
  *)
    : "${EVOT_LLM_OPENAI_API_KEY:?EVOT_LLM_OPENAI_API_KEY is required for non-local base URLs (set it in .env)}"
    ;;
esac
export EVOT_LLM_OPENAI_API_KEY
export TARI_LLM_BASE_URL="$EVOT_LLM_OPENAI_BASE_URL"
export TARI_LLM_MODEL="$EVOT_LLM_OPENAI_MODEL"
export TARI_LLM_API_KEY="$EVOT_LLM_OPENAI_API_KEY"
export TARI_LLM_TEMPERATURE="${TARI_LLM_TEMPERATURE:-0.2}"
export TARI_LLM_MAX_TOKENS="${TARI_LLM_MAX_TOKENS:-3500}"
export TARI_LLM_TIMEOUT="${TARI_LLM_TIMEOUT:-600}"
export TRPG_DB_PATH="$REPO/runtime-data/trpg.db"
PYTHON_BIN="${TARI_PYTHON:-$REPO/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN (set TARI_PYTHON to override)" >&2
  exit 1
fi

cat > "$LOG_DIR/odyssey.env" <<EOF
EVOT_LLM_OPENAI_BASE_URL=$EVOT_LLM_OPENAI_BASE_URL
EVOT_LLM_OPENAI_MODEL=$EVOT_LLM_OPENAI_MODEL
TARI_LLM_TEMPERATURE=$TARI_LLM_TEMPERATURE
TARI_LLM_MAX_TOKENS=$TARI_LLM_MAX_TOKENS
TARI_LLM_TIMEOUT=$TARI_LLM_TIMEOUT
EOF
unset EVOT_LLM_OPENAI_API_KEY TARI_LLM_API_KEY

echo "[$(date -Iseconds)] launching odyssey compile (mode=$mode)"
echo "  source:  $SOURCE ($(wc -c < "$SOURCE") bytes)"
echo "  output:  $OUTPUT_DIR"
echo "  log:     $LOG_FILE"
echo "  model:   $EVOT_LLM_OPENAI_MODEL @ $EVOT_LLM_OPENAI_BASE_URL"

nohup "$PYTHON_BIN" -u -m trpg_runtime.cli story-compile \
  "${cli_args[@]}" \
  > "$LOG_FILE" 2>&1 < /dev/null &

pid=$!
echo "$pid" > "$PID_FILE"
echo "[$(date -Iseconds)] pid=$pid — detached; tail with: tail -f $LOG_FILE"
disown "$pid" 2>/dev/null || true
