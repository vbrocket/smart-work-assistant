#!/usr/bin/env bash
#
# server-setup.sh — One-shot script to reproduce the full server state.
#
# Assumptions:
#   - Fresh GPU server (e.g. Vast.ai, RunPod) with NVIDIA GPU(s) and CUDA
#   - Python 3.10+ already installed
#   - Code already present in the same directory as this script
#   - Internet access to download models from HuggingFace
#
# What it does:
#   1. Stops any conflicting services / frees GPU VRAM
#   2. Installs system packages (ffmpeg, curl, git)
#   3. Installs Python dependencies from requirements.txt + vllm
#   4. Copies .env.vastai -> .env (if .env doesn't exist)
#   5. Starts vLLM servers (LLM, Embedding, Reranker) with correct params
#   6. Starts the FastAPI application
#   7. Runs health checks and prints access instructions
#
# Usage:
#   chmod +x server-setup.sh
#   ./server-setup.sh
#
# On subsequent runs, it will kill existing services and restart cleanly.
#
set -uo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration — edit these if you change models or hardware
# ═══════════════════════════════════════════════════════════════════════════════

LLM_MODEL="Qwen/Qwen3.5-27B"
LLM_PORT=8001
LLM_GPU="0"
LLM_MEM_UTIL="0.70"
LLM_MAX_LEN=32768
LLM_DTYPE="bfloat16"
LLM_TP=1

EMBED_MODEL="BAAI/bge-m3"
EMBED_PORT=8002
EMBED_GPU="0"
EMBED_MEM_UTIL="0.10"
EMBED_MAX_LEN=8192
EMBED_DTYPE="float16"

RERANK_MODEL="BAAI/bge-reranker-v2-m3"
RERANK_PORT=8003
RERANK_GPU="0"
RERANK_MEM_UTIL="0.05"
RERANK_MAX_LEN=512
RERANK_DTYPE="float16"

APP_PORT=18000
MODEL_CACHE="/workspace/models"

# ═══════════════════════════════════════════════════════════════════════════════
# Internals — no need to edit below
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR" "$MODEL_CACHE"

_ts()  { date '+%H:%M:%S'; }
log()  { echo "[$(_ts)] $*"; }
logr() { echo -e "[$(_ts)] \033[0;31m$*\033[0m"; }
logg() { echo -e "[$(_ts)] \033[0;32m$*\033[0m"; }

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 0: Kill everything, free GPU
# ═══════════════════════════════════════════════════════════════════════════════

log "═══════════════════════════════════════════════"
log "Phase 0: Cleaning GPU and stopping services..."
log "═══════════════════════════════════════════════"

pkill -9 -f 'vllm serve' 2>/dev/null || true
pkill -f 'uvicorn main:app' 2>/dev/null || true
ray stop --force 2>/dev/null || true

if command -v supervisorctl &>/dev/null; then
    log "Stopping Supervisor vLLM (Vast.ai default)..."
    supervisorctl stop vllm 2>/dev/null || true
    for conf in /etc/supervisor/conf.d/*; do
        if grep -q "program:vllm" "$conf" 2>/dev/null; then
            sed -i 's/autostart=true/autostart=false/' "$conf"
        fi
    done
    supervisorctl reread 2>/dev/null || true
    supervisorctl update 2>/dev/null || true
fi

sleep 2
fuser -k /dev/nvidia* 2>/dev/null || true
sleep 2

for port in $LLM_PORT $EMBED_PORT $RERANK_PORT $APP_PORT 8000; do
    fuser -k "$port/tcp" 2>/dev/null || true
done
sleep 1

GPU_MEM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)
if [ "${GPU_MEM:-0}" -lt 500 ]; then
    logg "GPU is clean (${GPU_MEM:-0} MiB used)"
else
    logr "WARNING: GPU still has ${GPU_MEM} MiB in use — may need instance reboot"
fi
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: System packages
# ═══════════════════════════════════════════════════════════════════════════════

log "═══════════════════════════════════════════════"
log "Phase 1: System dependencies..."
log "═══════════════════════════════════════════════"

NEED_INSTALL=0
for tool in ffmpeg curl git; do
    if ! command -v "$tool" &>/dev/null; then
        NEED_INSTALL=1
        break
    fi
done

if [ "$NEED_INSTALL" -eq 1 ]; then
    apt-get update -qq && apt-get install -y -qq ffmpeg curl git
    logg "System deps installed"
else
    log "System deps already present (ffmpeg, curl, git)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Python dependencies
# ═══════════════════════════════════════════════════════════════════════════════

log "═══════════════════════════════════════════════"
log "Phase 2: Python dependencies..."
log "═══════════════════════════════════════════════"

cd "$SCRIPT_DIR"

if [ -f requirements.txt ]; then
    log "Installing requirements.txt..."
    pip install -q -r requirements.txt 2>&1 | tail -5
    logg "App requirements installed"
else
    logr "WARNING: requirements.txt not found in $SCRIPT_DIR"
fi

if ! python3 -c "import vllm" 2>/dev/null; then
    log "Installing vLLM..."
    pip install -q vllm 2>&1 | tail -3
fi

VLLM_VER=$(python3 -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "unknown")
logg "vLLM version: $VLLM_VER"

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3: Environment file
# ═══════════════════════════════════════════════════════════════════════════════

log "═══════════════════════════════════════════════"
log "Phase 3: Environment file..."
log "═══════════════════════════════════════════════"

cd "$SCRIPT_DIR"
if [ -f .env ]; then
    log ".env already exists — keeping it"
elif [ -f .env.vastai ]; then
    cp .env.vastai .env
    logg "Copied .env.vastai -> .env"
else
    logr "WARNING: No .env or .env.vastai found — app may not start correctly"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4: Start vLLM services
# ═══════════════════════════════════════════════════════════════════════════════

log "═══════════════════════════════════════════════"
log "Phase 4: Starting vLLM services..."
log "═══════════════════════════════════════════════"

# 4a. LLM — main generative model with Qwen3 reasoning parser
log "Starting LLM: $LLM_MODEL on port $LLM_PORT (GPU $LLM_GPU)..."
CUDA_VISIBLE_DEVICES="$LLM_GPU" nohup vllm serve "$LLM_MODEL" \
    --port "$LLM_PORT" \
    --tensor-parallel-size "$LLM_TP" \
    --gpu-memory-utilization "$LLM_MEM_UTIL" \
    --max-model-len "$LLM_MAX_LEN" \
    --dtype "$LLM_DTYPE" \
    --download-dir "$MODEL_CACHE" \
    --reasoning-parser qwen3 \
    --reasoning-config.reasoning_start_str '<think>' \
    --reasoning-config.reasoning_end_str '</think>' \
    > "$LOG_DIR/vllm_llm.log" 2>&1 &
LLM_PID=$!
log "  LLM PID=$LLM_PID  log=$LOG_DIR/vllm_llm.log"

# 4b. Embedding — BGE-M3
log "Starting Embedding: $EMBED_MODEL on port $EMBED_PORT (GPU $EMBED_GPU)..."
CUDA_VISIBLE_DEVICES="$EMBED_GPU" nohup vllm serve "$EMBED_MODEL" \
    --port "$EMBED_PORT" \
    --gpu-memory-utilization "$EMBED_MEM_UTIL" \
    --max-model-len "$EMBED_MAX_LEN" \
    --dtype "$EMBED_DTYPE" \
    --download-dir "$MODEL_CACHE" \
    --convert embed \
    --runner pooling \
    > "$LOG_DIR/vllm_embed.log" 2>&1 &
EMBED_PID=$!
log "  Embedding PID=$EMBED_PID  log=$LOG_DIR/vllm_embed.log"

# 4c. Reranker — bge-reranker-v2-m3
log "Starting Reranker: $RERANK_MODEL on port $RERANK_PORT (GPU $RERANK_GPU)..."
CUDA_VISIBLE_DEVICES="$RERANK_GPU" nohup vllm serve "$RERANK_MODEL" \
    --port "$RERANK_PORT" \
    --gpu-memory-utilization "$RERANK_MEM_UTIL" \
    --max-model-len "$RERANK_MAX_LEN" \
    --dtype "$RERANK_DTYPE" \
    --download-dir "$MODEL_CACHE" \
    --convert classify \
    --runner pooling \
    > "$LOG_DIR/vllm_rerank.log" 2>&1 &
RERANK_PID=$!
log "  Reranker PID=$RERANK_PID  log=$LOG_DIR/vllm_rerank.log"

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5: Wait for vLLM services
# ═══════════════════════════════════════════════════════════════════════════════

log ""
log "═══════════════════════════════════════════════"
log "Phase 5: Waiting for vLLM services..."
log "═══════════════════════════════════════════════"

wait_for_port() {
    local port=$1 name=$2 timeout=${3:-180}
    log "Waiting for $name on port $port (timeout ${timeout}s)..."
    for i in $(seq 1 "$timeout"); do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
            logg "$name ready on port $port (${i}s)"
            return 0
        fi
        if ! kill -0 "${4:-0}" 2>/dev/null && [ "${4:-0}" -ne 0 ]; then
            logr "$name process died. Last 20 lines of log:"
            tail -20 "$LOG_DIR/$5.log" 2>/dev/null
            return 1
        fi
        if [ $((i % 30)) -eq 0 ]; then
            log "  still waiting... ${i}/${timeout}s"
        fi
        sleep 1
    done
    logr "ERROR: $name failed to start within ${timeout}s"
    log "  Last 20 lines of $LOG_DIR/$5.log:"
    tail -20 "$LOG_DIR/$5.log" 2>/dev/null
    return 1
}

LLM_OK=0; EMBED_OK=0; RERANK_OK=0

wait_for_port $LLM_PORT "LLM ($LLM_MODEL)" 600 $LLM_PID vllm_llm && LLM_OK=1
wait_for_port $EMBED_PORT "Embedding ($EMBED_MODEL)" 180 $EMBED_PID vllm_embed && EMBED_OK=1
wait_for_port $RERANK_PORT "Reranker ($RERANK_MODEL)" 180 $RERANK_PID vllm_rerank && RERANK_OK=1

if [ "$LLM_OK" -eq 0 ]; then
    logr "FATAL: LLM did not start. Cannot continue."
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6: Start FastAPI application
# ═══════════════════════════════════════════════════════════════════════════════

log ""
log "═══════════════════════════════════════════════"
log "Phase 6: Starting FastAPI application..."
log "═══════════════════════════════════════════════"

cd "$SCRIPT_DIR"

nohup python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "$APP_PORT" \
    --workers 1 \
    --log-level info \
    > "$LOG_DIR/app.log" 2>&1 &
APP_PID=$!
log "  FastAPI PID=$APP_PID  log=$LOG_DIR/app.log"

sleep 5

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 7: Health check & summary
# ═══════════════════════════════════════════════════════════════════════════════

log ""
log "═══════════════════════════════════════════════"
log "Phase 7: Health check"
log "═══════════════════════════════════════════════"

check_service() {
    local port=$1 name=$2
    if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
        local model
        model=$(curl -s "http://localhost:$port/v1/models" 2>/dev/null \
            | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data'][0]['id'])" 2>/dev/null \
            || echo "?")
        logg "  Port $port: OK — $name ($model)"
        return 0
    elif curl -sf "http://localhost:$port/api/policy/status" >/dev/null 2>&1; then
        logg "  Port $port: OK — $name"
        return 0
    else
        logr "  Port $port: NOT RESPONDING — $name"
        return 1
    fi
}

ALL_OK=1
check_service $LLM_PORT "LLM" || ALL_OK=0
check_service $EMBED_PORT "Embedding" || ALL_OK=0
check_service $RERANK_PORT "Reranker" || ALL_OK=0
check_service $APP_PORT "FastAPI App" || ALL_OK=0

echo ""
log "GPU Memory:"
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null | while read -r line; do
    log "  $line"
done

echo ""
log "╔═══════════════════════════════════════════════════════════╗"
log "║  Service Summary                                         ║"
log "╠═══════╦═══════════════════════════════╦══════╦════════════╣"
log "║  GPU  ║  Model                        ║ Port ║  Task      ║"
log "╠═══════╬═══════════════════════════════╬══════╬════════════╣"
printf "[$(_ts)] ║  %-4s ║  %-28s ║ %-4s ║  %-9s ║\n" "$LLM_GPU" "$LLM_MODEL" "$LLM_PORT" "LLM"
printf "[$(_ts)] ║  %-4s ║  %-28s ║ %-4s ║  %-9s ║\n" "$EMBED_GPU" "$EMBED_MODEL" "$EMBED_PORT" "Embedding"
printf "[$(_ts)] ║  %-4s ║  %-28s ║ %-4s ║  %-9s ║\n" "$RERANK_GPU" "$RERANK_MODEL" "$RERANK_PORT" "Reranker"
printf "[$(_ts)] ║  %-4s ║  %-28s ║ %-4s ║  %-9s ║\n" "-" "FastAPI App" "$APP_PORT" "Web App"
log "╚═══════╩═══════════════════════════════╩══════╩════════════╝"
echo ""

if [ "$ALL_OK" -eq 1 ]; then
    logg "All services are running!"
else
    logr "Some services failed — check logs in $LOG_DIR/"
fi

echo ""
log "Logs directory: $LOG_DIR/"
log "  vllm_llm.log, vllm_embed.log, vllm_rerank.log, app.log"
echo ""
log "To access the app, set up SSH port forwarding:"
log "  ssh -p <SSH_PORT> <USER@HOST> -L $APP_PORT:localhost:$APP_PORT -N"
log "  Then open: http://localhost:$APP_PORT"
echo ""
logg "Setup complete!"
