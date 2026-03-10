#!/usr/bin/env bash
#
# deploy-h200.sh — Start all services on a vast.ai single-GPU H200 NVL instance.
#
# GPU layout (single GPU, all on GPU 0, ~141 GB VRAM):
#   GPU 0 → vLLM LLM       (Qwen3-32B, TP=1)     port 8001  (55%)
#            vLLM Embedding (BGE-M3)               port 8002  (10%)
#            vLLM Reranker  (bge-reranker-v2-m3)   port 8003  (5%)
#            Whisper + TTS (in-process in FastAPI app)
#
# Usage:
#   chmod +x deploy-h200.sh
#   ./deploy-h200.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Configurable via environment ──────────────────────────────────────────
LLM_MODEL="${VLLM_LLM_MODEL:-Qwen/Qwen3-32B}"
LLM_TP="${VLLM_TP:-1}"
LLM_PORT="${VLLM_LLM_PORT:-8001}"
LLM_GPU="${VLLM_LLM_GPU:-0}"

EMBED_MODEL="${VLLM_EMBED_MODEL:-BAAI/bge-m3}"
EMBED_PORT="${VLLM_EMBED_PORT:-8002}"
EMBED_GPU="${VLLM_EMBED_GPU:-0}"

RERANK_MODEL="${VLLM_RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
RERANK_PORT="${VLLM_RERANK_PORT:-8003}"
RERANK_GPU="${VLLM_RERANK_GPU:-0}"

APP_PORT="${APP_PORT:-18000}"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
logr() { echo -e "[$(date '+%H:%M:%S')] \033[0;31m$*\033[0m"; }
logg() { echo -e "[$(date '+%H:%M:%S')] \033[0;32m$*\033[0m"; }

kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti ":$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "Killing process(es) on port $port: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

wait_for_port() {
    local port=$1 name=$2 timeout=${3:-120}
    log "Waiting for $name on port $port (timeout ${timeout}s)..."
    for i in $(seq 1 "$timeout"); do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1 || \
           curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
            logg "$name is ready on port $port (${i}s)"
            return 0
        fi
        if [ -n "${4:-}" ] && ! kill -0 "$4" 2>/dev/null; then
            logr "$name process died (PID $4). Check $LOG_DIR/vllm_*.log"
            return 1
        fi
        sleep 1
    done
    logr "ERROR: $name failed to start within ${timeout}s"
    return 1
}

verify_gpu() {
    local gpu=$1 name=$2
    local gpu_mem
    gpu_mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | xargs)
    if [ "${gpu_mem:-0}" -gt 500 ]; then
        logg "$name is on GPU $gpu (${gpu_mem}MB VRAM used)"
    else
        log "WARNING: $name may not be on GPU $gpu (only ${gpu_mem}MB used)"
    fi
}

PID_LLM="" PID_EMBED="" PID_RERANK="" PID_APP=""
_OWNED_PIDS=""

cleanup() {
    log "Shutting down services started by this script..."
    for pid in $_OWNED_PIDS; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    log "Done."
}
trap cleanup EXIT INT TERM

# ═════════════════════════════════════════════════════════════════════════
# 0. STOP VAST.AI BUILT-IN VLLM + CHECK EXISTING SERVERS
# ═════════════════════════════════════════════════════════════════════════

if command -v supervisorctl &>/dev/null; then
    log "Stopping vast.ai built-in vLLM service..."
    supervisorctl stop vllm 2>/dev/null || true
    for conf in /etc/supervisor/conf.d/*; do
        if grep -q "program:vllm" "$conf" 2>/dev/null; then
            sed -i 's/autostart=true/autostart=false/' "$conf"
        fi
    done
    supervisorctl reread 2>/dev/null || true
    supervisorctl update 2>/dev/null || true
    sleep 2
fi

is_vllm_serving() {
    local port=$1 expected_model=$2
    if ! curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
        return 1
    fi
    local served_model
    served_model=$(curl -s "http://localhost:$port/v1/models" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null || echo "")
    if [ "$served_model" = "$expected_model" ]; then
        return 0
    fi
    return 1
}

SKIP_LLM=0 SKIP_EMBED=0 SKIP_RERANK=0

if is_vllm_serving "$LLM_PORT" "$LLM_MODEL"; then
    logg "LLM already running on port $LLM_PORT ($LLM_MODEL) — skipping"
    SKIP_LLM=1
    PID_LLM=$(lsof -ti ":$LLM_PORT" 2>/dev/null | head -1 || true)
fi

if is_vllm_serving "$EMBED_PORT" "$EMBED_MODEL"; then
    logg "Embedding already running on port $EMBED_PORT ($EMBED_MODEL) — skipping"
    SKIP_EMBED=1
    PID_EMBED=$(lsof -ti ":$EMBED_PORT" 2>/dev/null | head -1 || true)
fi

if is_vllm_serving "$RERANK_PORT" "$RERANK_MODEL"; then
    logg "Reranker already running on port $RERANK_PORT ($RERANK_MODEL) — skipping"
    SKIP_RERANK=1
    PID_RERANK=$(lsof -ti ":$RERANK_PORT" 2>/dev/null | head -1 || true)
fi

kill_port "$APP_PORT"

log "GPU memory check:"
nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv,noheader

# ═════════════════════════════════════════════════════════════════════════
# 1. INSTALL DEPS (first run only)
# ═════════════════════════════════════════════════════════════════════════

if [ ! -f "$SCRIPT_DIR/.deps_installed" ]; then
    log "Installing Python dependencies (first run)..."
    pip install -q -r requirements.txt 2>&1 | tail -3
    touch "$SCRIPT_DIR/.deps_installed"
    log "Dependencies installed."
fi

# ═════════════════════════════════════════════════════════════════════════
# 2. COPY .env IF MISSING
# ═════════════════════════════════════════════════════════════════════════

if [ ! -f "$SCRIPT_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env.vastai" ]; then
    log "No .env found — copying .env.vastai"
    cp "$SCRIPT_DIR/.env.vastai" "$SCRIPT_DIR/.env"
fi

# ═════════════════════════════════════════════════════════════════════════
# 3. START VLLM — LLM (GPU 0, TP=1, 55% VRAM)
# ═════════════════════════════════════════════════════════════════════════

if [ "$SKIP_LLM" -eq 0 ]; then
    log "━━━ Starting LLM: $LLM_MODEL on GPU $LLM_GPU, port $LLM_PORT (TP=$LLM_TP) ━━━"
    kill_port "$LLM_PORT"
    CUDA_VISIBLE_DEVICES="$LLM_GPU" vllm serve "$LLM_MODEL" \
        --port "$LLM_PORT" \
        --tensor-parallel-size "$LLM_TP" \
        --gpu-memory-utilization 0.70 \
        --max-model-len 32768 \
        --dtype bfloat16 \
        --disable-log-requests \
        > "$LOG_DIR/vllm_llm.log" 2>&1 &
    PID_LLM=$!
    _OWNED_PIDS="$_OWNED_PIDS $PID_LLM"
    log "LLM server started (PID $PID_LLM, log: $LOG_DIR/vllm_llm.log)"
fi

# ═════════════════════════════════════════════════════════════════════════
# 4. START VLLM — EMBEDDING (GPU 0, 10% VRAM)
# ═════════════════════════════════════════════════════════════════════════

if [ "$SKIP_EMBED" -eq 0 ]; then
    log "━━━ Starting Embedding: $EMBED_MODEL on GPU $EMBED_GPU, port $EMBED_PORT ━━━"
    kill_port "$EMBED_PORT"
    CUDA_VISIBLE_DEVICES="$EMBED_GPU" vllm serve "$EMBED_MODEL" \
        --convert embed \
        --runner pooling \
        --port "$EMBED_PORT" \
        --gpu-memory-utilization 0.10 \
        --max-model-len 8192 \
        --dtype float16 \
        --disable-log-requests \
        > "$LOG_DIR/vllm_embed.log" 2>&1 &
    PID_EMBED=$!
    _OWNED_PIDS="$_OWNED_PIDS $PID_EMBED"
    log "Embedding server started (PID $PID_EMBED, log: $LOG_DIR/vllm_embed.log)"
fi

# ═════════════════════════════════════════════════════════════════════════
# 5. START VLLM — RERANKER (GPU 0, 5% VRAM)
# ═════════════════════════════════════════════════════════════════════════

if [ "$SKIP_RERANK" -eq 0 ]; then
    log "━━━ Starting Reranker: $RERANK_MODEL on GPU $RERANK_GPU, port $RERANK_PORT ━━━"
    kill_port "$RERANK_PORT"
    CUDA_VISIBLE_DEVICES="$RERANK_GPU" vllm serve "$RERANK_MODEL" \
        --convert classify \
        --runner pooling \
        --port "$RERANK_PORT" \
        --gpu-memory-utilization 0.05 \
        --max-model-len 512 \
        --dtype float16 \
        --disable-log-requests \
        > "$LOG_DIR/vllm_rerank.log" 2>&1 &
    PID_RERANK=$!
    _OWNED_PIDS="$_OWNED_PIDS $PID_RERANK"
    log "Reranker server started (PID $PID_RERANK, log: $LOG_DIR/vllm_rerank.log)"
fi

# ═════════════════════════════════════════════════════════════════════════
# 6. WAIT FOR ALL VLLM SERVERS
# ═════════════════════════════════════════════════════════════════════════

LLM_OK=$SKIP_LLM EMBED_OK=$SKIP_EMBED RERANK_OK=$SKIP_RERANK

NEED_WAIT=0
[ "$SKIP_LLM" -eq 0 ] && NEED_WAIT=1
[ "$SKIP_EMBED" -eq 0 ] && NEED_WAIT=1
[ "$SKIP_RERANK" -eq 0 ] && NEED_WAIT=1

if [ "$NEED_WAIT" -eq 1 ]; then
    log ""
    log "Waiting for newly started vLLM servers to become ready..."
    log "(LLM may take 3-5 minutes on first run while downloading + compiling)"
    log ""
fi

if [ "$SKIP_LLM" -eq 0 ]; then
    if wait_for_port "$LLM_PORT" "vLLM LLM" 600 "$PID_LLM"; then
        verify_gpu "$LLM_GPU" "LLM"
        LLM_OK=1
    else
        logr "LLM failed to start. Last 20 lines of log:"
        tail -20 "$LOG_DIR/vllm_llm.log" 2>/dev/null
    fi
fi

if [ "$SKIP_EMBED" -eq 0 ]; then
    if wait_for_port "$EMBED_PORT" "vLLM Embedding" 180 "$PID_EMBED"; then
        verify_gpu "$EMBED_GPU" "Embedding"
        EMBED_OK=1
    else
        logr "Embedding failed to start. Last 20 lines of log:"
        tail -20 "$LOG_DIR/vllm_embed.log" 2>/dev/null
    fi
fi

if [ "$SKIP_RERANK" -eq 0 ]; then
    if wait_for_port "$RERANK_PORT" "vLLM Reranker" 180 "$PID_RERANK"; then
        verify_gpu "$RERANK_GPU" "Reranker"
        RERANK_OK=1
    else
        logr "Reranker failed to start. Last 20 lines of log:"
        tail -20 "$LOG_DIR/vllm_rerank.log" 2>/dev/null
    fi
fi

if [ "$LLM_OK" -eq 0 ]; then
    logr "CRITICAL: LLM server is not running — the app will not function."
    logr "Fix the issue and re-run this script."
    exit 1
fi

# ═════════════════════════════════════════════════════════════════════════
# 7. VERIFY GPU ASSIGNMENT
# ═════════════════════════════════════════════════════════════════════════

log ""
log "━━━ GPU Memory Usage ━━━"
nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader | while read -r line; do
    log "  $line"
done

# ═════════════════════════════════════════════════════════════════════════
# 8. START FASTAPI APP
# ═════════════════════════════════════════════════════════════════════════

log ""
log "━━━ Starting FastAPI app on port $APP_PORT ━━━"
python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "$APP_PORT" \
    --workers 1 \
    --log-level info \
    > "$LOG_DIR/app.log" 2>&1 &
PID_APP=$!
_OWNED_PIDS="$_OWNED_PIDS $PID_APP"
log "App started (PID $PID_APP, log: $LOG_DIR/app.log)"

# ═════════════════════════════════════════════════════════════════════════
# 9. SUMMARY
# ═════════════════════════════════════════════════════════════════════════

sleep 2

_status_label() { [ "$1" -eq 1 ] && echo "kept" || echo "started"; }

log ""
log "┌─────────────────────────────────────────────────────────────────────┐"
log "│  All services running on single H200 NVL GPU!                      │"
log "├───────────────┬──────────────────────────────┬───────┬───────┬──────┤"
log "│  Service      │  Model                       │  Port │  GPU  │ Note │"
log "├───────────────┼──────────────────────────────┼───────┼───────┼──────┤"
printf "[$(date '+%H:%M:%S')] │  %-13s │  %-28s │  %-5s│  %-5s│ %-5s│\n" "LLM" "$LLM_MODEL" "$LLM_PORT" "$LLM_GPU" "$(_status_label $SKIP_LLM)"
printf "[$(date '+%H:%M:%S')] │  %-13s │  %-28s │  %-5s│  %-5s│ %-5s│\n" "Embedding" "$EMBED_MODEL" "$EMBED_PORT" "$EMBED_GPU" "$(_status_label $SKIP_EMBED)"
printf "[$(date '+%H:%M:%S')] │  %-13s │  %-28s │  %-5s│  %-5s│ %-5s│\n" "Reranker" "$RERANK_MODEL" "$RERANK_PORT" "$RERANK_GPU" "$(_status_label $SKIP_RERANK)"
printf "[$(date '+%H:%M:%S')] │  %-13s │  %-28s │  %-5s│  %-5s│ %-5s│\n" "App" "FastAPI" "$APP_PORT" "—" "started"
log "└───────────────┴──────────────────────────────┴───────┴───────┴──────┘"
log ""
log "PIDs: LLM=$PID_LLM  Embed=$PID_EMBED  Reranker=$PID_RERANK  App=$PID_APP"
log "Logs: $LOG_DIR/"
log ""
log "Access the app:"
log "  Tunnel:  ssh -i vast_ed25519 -p <PORT> <HOST> -L 18000:localhost:18000 -N"
log ""
log "Press Ctrl+C to stop services started by this script."

wait
