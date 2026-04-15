#!/usr/bin/env bash
#
# setup-new-server.sh — One-shot script to bootstrap a new vast.ai server
#
# Expects pwa-server-snapshot.tar.gz in the same directory (or /workspace/).
#
# What it does:
#   1. Extracts the snapshot to /workspace/pwa-idea/
#   2. Fixes line endings on all .sh files
#   3. Verifies GPU is available and free
#   4. Installs system deps (ffmpeg, curl, git)
#   5. Installs Python deps from requirements.txt
#   6. Installs vLLM (if not already present)
#   7. Copies .env.vastai -> .env
#   8. Copies pre-built RAG data
#   9. Starts vLLM services (LLM, Embedding, Reranker)
#  10. Waits for all services to be ready
#  11. Starts the FastAPI app
#  12. Runs a health check
#
# Usage on a new server:
#   Upload this script + pwa-server-snapshot.tar.gz, then:
#     chmod +x setup-new-server.sh
#     ./setup-new-server.sh
#
#   Or from your local machine:
#     scp -i ~/.ssh/vast_ed25519 -P PORT setup-new-server.sh pwa-server-snapshot.tar.gz root@HOST:/workspace/
#     ssh -i ~/.ssh/vast_ed25519 -p PORT root@HOST "cd /workspace && chmod +x setup-new-server.sh && ./setup-new-server.sh"
#
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
WORKSPACE="/workspace"
PROJECT_DIR="$WORKSPACE/pwa-idea"
BACKEND_DIR="$PROJECT_DIR/backend"
MODEL_DIR="$WORKSPACE/models"
SNAPSHOT="pwa-server-snapshot.tar.gz"

# vLLM service definitions
LLM_MODEL="Qwen/Qwen3.5-27B"
LLM_PORT=8001
LLM_MEM=0.70

EMBED_MODEL="BAAI/bge-m3"
EMBED_PORT=8002
EMBED_MEM=0.10

RERANK_MODEL="BAAI/bge-reranker-v2-m3"
RERANK_PORT=8003
RERANK_MEM=0.05

APP_PORT=18000

# ── Logging helpers ────────────────────────────────────────────────────────
_ts()  { date '+%H:%M:%S'; }
log()  { echo "[$(_ts)] $*"; }
logr() { echo -e "[$(_ts)] \033[0;31m$*\033[0m"; }
logg() { echo -e "[$(_ts)] \033[0;32m$*\033[0m"; }
logb() { echo -e "[$(_ts)] \033[1;34m$*\033[0m"; }

# ═══════════════════════════════════════════════════════════════════════════
# Step 1: Extract snapshot
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 1: Extract project snapshot ═══"

SNAP_PATH=""
for p in "$WORKSPACE/$SNAPSHOT" "./$SNAPSHOT" "$BACKEND_DIR/$SNAPSHOT"; do
    [ -f "$p" ] && SNAP_PATH="$p" && break
done

if [ -z "$SNAP_PATH" ]; then
    logr "ERROR: Cannot find $SNAPSHOT"
    logr "Place it in $WORKSPACE/ or the same directory as this script."
    exit 1
fi

log "Extracting $SNAP_PATH to $WORKSPACE/ ..."
cd "$WORKSPACE"
tar -xzf "$SNAP_PATH"
logg "Extracted to $PROJECT_DIR"

# Fix line endings on all .sh files
find "$BACKEND_DIR" -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
chmod +x "$BACKEND_DIR"/*.sh 2>/dev/null || true

# ═══════════════════════════════════════════════════════════════════════════
# Step 2: Verify GPU
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 2: Verify GPU ═══"

if ! command -v nvidia-smi &>/dev/null; then
    logr "ERROR: nvidia-smi not found — this server has no GPU drivers"
    exit 1
fi

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
GPU_MEM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | xargs)
GPU_MEM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | xargs)
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)

log "GPU: $GPU_NAME"
log "VRAM: ${GPU_MEM_USED}MiB / ${GPU_MEM_TOTAL}MiB (${GPU_COUNT} GPU(s))"

if [ "${GPU_MEM_TOTAL:-0}" -lt 40000 ]; then
    logr "WARNING: GPU has less than 40GB VRAM. Qwen3.5-27B needs ~60GB+ total."
    logr "Consider a larger GPU or reduce model size."
fi

# Free GPU if anything is using it
if [ "${GPU_MEM_USED:-0}" -gt 500 ]; then
    log "GPU has ${GPU_MEM_USED}MiB in use — cleaning..."
    pkill -9 -f 'vllm serve' 2>/dev/null || true
    pkill -f 'uvicorn main:app' 2>/dev/null || true
    ray stop --force 2>/dev/null || true

    if command -v supervisorctl &>/dev/null; then
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
    sleep 1

    GPU_MEM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | xargs)
    if [ "${GPU_MEM_USED:-0}" -lt 500 ]; then
        logg "GPU cleaned (${GPU_MEM_USED}MiB used)"
    else
        logr "WARNING: GPU still has ${GPU_MEM_USED}MiB in use"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# Step 3: Install system deps
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 3: System dependencies ═══"

if dpkg -l ffmpeg curl git 2>/dev/null | grep -qc "^ii" | grep -q 3; then
    log "System deps already installed"
else
    apt-get update -qq
    apt-get install -y -qq ffmpeg curl git 2>&1 | tail -2
    logg "System deps installed (ffmpeg, curl, git)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Step 4: Install Python deps
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 4: Python dependencies ═══"

cd "$BACKEND_DIR"

if [ -f requirements.txt ]; then
    log "Installing requirements.txt..."
    pip install -q -r requirements.txt 2>&1 | tail -5
    logg "Python deps installed"
else
    logr "WARNING: No requirements.txt found"
fi

# Ensure vLLM is installed
if python3 -c "import vllm; print(f'vLLM {vllm.__version__}')" 2>/dev/null; then
    log "vLLM already installed: $(python3 -c 'import vllm; print(vllm.__version__)')"
else
    log "Installing vLLM..."
    pip install -q vllm 2>&1 | tail -3
    logg "vLLM installed: $(python3 -c 'import vllm; print(vllm.__version__)')"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Step 5: Setup .env
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 5: Environment config ═══"

cd "$BACKEND_DIR"
if [ -f .env.vastai ]; then
    cp .env.vastai .env
    logg "Copied .env.vastai -> .env"
else
    logr "WARNING: No .env.vastai found"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Step 6: Setup RAG data
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 6: RAG data ═══"

RAG_SRC="$PROJECT_DIR/data/data"
RAG_DST="$BACKEND_DIR/data"

if [ -d "$RAG_SRC" ]; then
    mkdir -p "$RAG_DST/faiss_store"
    for f in bm25_index.pkl assistant.db; do
        [ -f "$RAG_SRC/$f" ] && cp -f "$RAG_SRC/$f" "$RAG_DST/$f"
    done
    [ -d "$RAG_SRC/faiss_store" ] && cp -f "$RAG_SRC/faiss_store/"* "$RAG_DST/faiss_store/" 2>/dev/null || true
    echo -n 'vllm:bge-m3' > "$RAG_DST/embed_backend.txt"
    logg "RAG data copied (embed marker: vllm:bge-m3)"
else
    log "No pre-built RAG data found at $RAG_SRC — will ingest on first run"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Step 7: Start vLLM services
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 7: Starting vLLM services ═══"

mkdir -p "$BACKEND_DIR/logs"

# Helper to start a vLLM service
start_vllm() {
    local model=$1 port=$2 mem=$3 log_name=$4
    shift 4
    log "Starting $model on port $port (mem=$mem)..."
    CUDA_VISIBLE_DEVICES=0 nohup vllm serve "$model" \
        --port "$port" \
        --gpu-memory-utilization "$mem" \
        --download-dir "$MODEL_DIR" \
        "$@" \
        > "$BACKEND_DIR/logs/${log_name}.log" 2>&1 &
    log "  PID=$!  log=$BACKEND_DIR/logs/${log_name}.log"
}

# 7a. LLM — Qwen3.5-27B
start_vllm "$LLM_MODEL" $LLM_PORT $LLM_MEM vllm_llm \
    --tensor-parallel-size 1 \
    --max-model-len 32768 \
    --dtype bfloat16 \
    --reasoning-parser qwen3 \
    --reasoning-config.reasoning_start_str '<think>' \
    --reasoning-config.reasoning_end_str '</think>'

# 7b. Embedding — BGE-M3
start_vllm "$EMBED_MODEL" $EMBED_PORT $EMBED_MEM vllm_embed \
    --convert embed \
    --runner pooling \
    --max-model-len 8192 \
    --dtype float16

# 7c. Reranker — bge-reranker-v2-m3
start_vllm "$RERANK_MODEL" $RERANK_PORT $RERANK_MEM vllm_rerank \
    --convert classify \
    --runner pooling \
    --max-model-len 512 \
    --dtype float16

# ═══════════════════════════════════════════════════════════════════════════
# Step 8: Wait for vLLM services
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 8: Waiting for vLLM services ═══"
log "(Models will be downloaded on first run — this may take 10-30 min)"

wait_for_port() {
    local port=$1 name=$2 timeout=${3:-300}
    log "Waiting for $name on port $port (timeout ${timeout}s)..."
    for i in $(seq 1 "$timeout"); do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1 || \
           curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
            logg "$name ready on port $port (${i}s)"
            return 0
        fi
        [ $((i % 30)) -eq 0 ] && log "  still waiting... ${i}s"
        sleep 1
    done
    logr "ERROR: $name failed to start within ${timeout}s"
    return 1
}

wait_for_port $LLM_PORT "LLM ($LLM_MODEL)" 900 || {
    logr "LLM failed to start. Check: tail -50 $BACKEND_DIR/logs/vllm_llm.log"
    tail -20 "$BACKEND_DIR/logs/vllm_llm.log" 2>/dev/null
    exit 1
}

wait_for_port $EMBED_PORT "Embedding ($EMBED_MODEL)" 300 || {
    logr "Embedding failed. Check: tail -50 $BACKEND_DIR/logs/vllm_embed.log"
    tail -20 "$BACKEND_DIR/logs/vllm_embed.log" 2>/dev/null
}

wait_for_port $RERANK_PORT "Reranker ($RERANK_MODEL)" 300 || {
    logr "Reranker failed. Check: tail -50 $BACKEND_DIR/logs/vllm_rerank.log"
    tail -20 "$BACKEND_DIR/logs/vllm_rerank.log" 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════════════════
# Step 9: Start FastAPI
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 9: Starting FastAPI app ═══"

cd "$BACKEND_DIR"
find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

nohup python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port $APP_PORT \
    --workers 1 \
    --log-level info \
    > "$BACKEND_DIR/logs/app.log" 2>&1 &

log "FastAPI PID=$! — log: $BACKEND_DIR/logs/app.log"
sleep 3

# ═══════════════════════════════════════════════════════════════════════════
# Step 10: Health check
# ═══════════════════════════════════════════════════════════════════════════

logb "═══ Step 10: Health Check ═══"

echo ""
for port in $LLM_PORT $EMBED_PORT $RERANK_PORT; do
    if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
        model=$(curl -s "http://localhost:$port/v1/models" 2>/dev/null \
            | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data'][0]['id'])" 2>/dev/null \
            || echo "?")
        logg "  Port $port: OK ($model)"
    else
        logr "  Port $port: NOT RESPONDING"
    fi
done

if curl -sf "http://localhost:$APP_PORT/api/policy/status" >/dev/null 2>&1; then
    logg "  Port $APP_PORT: OK (FastAPI)"
else
    logr "  Port $APP_PORT: NOT RESPONDING (may still be starting)"
fi

echo ""
log "GPU Memory:"
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null

echo ""
log "vLLM version: $(python3 -c 'import vllm; print(vllm.__version__)' 2>/dev/null || echo 'unknown')"
log ""
logg "════════════════════════════════════════════════════════════"
logg "  Server setup complete!"
logg "════════════════════════════════════════════════════════════"
log ""
log "  Services:"
log "    LLM:      http://localhost:$LLM_PORT   ($LLM_MODEL)"
log "    Embed:    http://localhost:$EMBED_PORT   ($EMBED_MODEL)"
log "    Reranker: http://localhost:$RERANK_PORT   ($RERANK_MODEL)"
log "    App:      http://localhost:$APP_PORT"
log ""
log "  Logs:       $BACKEND_DIR/logs/"
log "  SSH tunnel: ssh -p <PORT> root@<HOST> -L 18000:localhost:18000 -N"
log ""
log "  To re-deploy code later, use sync-to-server.bat from your local machine."
log ""
