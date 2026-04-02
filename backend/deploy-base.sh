#!/usr/bin/env bash
#
# deploy-base.sh -- Shared functions for all deploy-*.sh config scripts.
#
# Usage: source this file from a config-specific deploy script, then call
#   the exported functions in order. Do NOT run this file directly.
#
# Exported functions:
#   kill_gpu            - Stop all GPU processes and free VRAM
#   install_system_deps - Install system packages (ffmpeg, curl, git)
#   install_pip_base    - Install Python deps from requirements.txt + vllm
#   setup_env           - Copy .env.vastai -> .env if missing
#   wait_for_port       - Block until a service responds on a given port
#   start_vllm_service  - Launch a vLLM serve process in the background
#   start_fastapi       - Launch the FastAPI app
#   health_check        - Verify services and print summary
#

set -uo pipefail

# Resolve paths relative to the backend directory
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$DEPLOY_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Logging helpers ──────────────────────────────────────────────────────────

_ts()  { date '+%H:%M:%S'; }
log()  { echo "[$(_ts)] $*"; }
logr() { echo -e "[$(_ts)] \033[0;31m$*\033[0m"; }
logg() { echo -e "[$(_ts)] \033[0;32m$*\033[0m"; }
logb() { echo -e "[$(_ts)] \033[0;34m$*\033[0m"; }

# ── Phase 0: Kill everything, free GPU ───────────────────────────────────────

kill_gpu() {
    log "Phase 0: Cleaning GPU and stopping services..."

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

    local gpu_mem
    gpu_mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)
    if [ "${gpu_mem:-0}" -lt 500 ]; then
        logg "GPU is clean (${gpu_mem:-0} MiB used)"
    else
        logr "WARNING: GPU still has ${gpu_mem} MiB in use"
    fi

    nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null
    echo ""
}

# ── Phase 1: System packages ─────────────────────────────────────────────────

install_system_deps() {
    log "Phase 1: Installing system dependencies..."

    if dpkg -l ffmpeg curl git 2>/dev/null | grep -q "^ii.*ffmpeg" && \
       dpkg -l ffmpeg curl git 2>/dev/null | grep -q "^ii.*curl" && \
       dpkg -l ffmpeg curl git 2>/dev/null | grep -q "^ii.*git"; then
        log "System deps already installed (ffmpeg, curl, git) -- skipping"
        return 0
    fi

    apt-get update -qq
    apt-get install -y -qq ffmpeg curl git
    logg "System deps installed"
}

# ── Phase 2: Base Python dependencies ────────────────────────────────────────

install_pip_base() {
    log "Phase 2: Installing base Python dependencies..."

    if [ -f "$DEPLOY_DIR/.deps_installed" ]; then
        log "Marker .deps_installed found -- skipping base pip install"
        log "(delete $DEPLOY_DIR/.deps_installed to force reinstall)"
        return 0
    fi

    cd "$DEPLOY_DIR"

    if [ -f requirements.txt ]; then
        log "Installing requirements.txt..."
        pip install -q -r requirements.txt 2>&1 | tail -5
    fi

    if ! python3 -c "import vllm" 2>/dev/null; then
        log "Installing vllm..."
        pip install -q vllm 2>&1 | tail -3
    else
        log "vllm already installed"
    fi

    touch "$DEPLOY_DIR/.deps_installed"
    logg "Base Python deps installed"
}

# ── Phase 3: Environment file ────────────────────────────────────────────────

setup_env() {
    log "Phase 3: Setting up environment file..."
    cd "$DEPLOY_DIR"

    if [ -f .env ]; then
        log ".env already exists -- keeping it"
    elif [ -f .env.vastai ]; then
        cp .env.vastai .env
        logg "Copied .env.vastai -> .env"
    else
        logr "WARNING: No .env or .env.vastai found"
    fi
}

# ── Helper: wait_for_port ────────────────────────────────────────────────────

wait_for_port() {
    local port=$1
    local name=$2
    local timeout=${3:-180}

    log "Waiting for $name on port $port (timeout ${timeout}s)..."
    for i in $(seq 1 "$timeout"); do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1 || \
           curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
            logg "$name ready on port $port (${i}s)"
            return 0
        fi
        if [ $((i % 30)) -eq 0 ]; then
            log "  still waiting... ${i}s"
        fi
        sleep 1
    done

    logr "ERROR: $name failed to start within ${timeout}s"
    return 1
}

# ── Helper: start_vllm_service ───────────────────────────────────────────────
#
# Usage: start_vllm_service MODEL PORT GPU MEM_UTIL LOG_NAME [EXTRA_ARGS...]
#
#   MODEL     - HuggingFace model id (e.g. Qwen/Qwen3.5-27B)
#   PORT      - Port to serve on
#   GPU       - CUDA_VISIBLE_DEVICES value (e.g. 0 or 0,1)
#   MEM_UTIL  - gpu-memory-utilization (e.g. 0.70)
#   LOG_NAME  - log filename without extension (e.g. vllm_llm)
#   EXTRA_ARGS - any additional vllm serve flags

start_vllm_service() {
    local model=$1
    local port=$2
    local gpu=$3
    local mem_util=$4
    local log_name=$5
    shift 5

    log "Starting vLLM: $model on port $port (GPU $gpu, mem $mem_util)..."

    CUDA_VISIBLE_DEVICES="$gpu" nohup vllm serve "$model" \
        --port "$port" \
        --gpu-memory-utilization "$mem_util" \
        --download-dir /workspace/models \
        "$@" \
        > "$LOG_DIR/${log_name}.log" 2>&1 &

    local pid=$!
    log "  PID=$pid  log=$LOG_DIR/${log_name}.log"
    echo "$pid"
}

# ── Phase 5: Start FastAPI ───────────────────────────────────────────────────

start_fastapi() {
    local port=${1:-18000}

    log "Starting FastAPI on port $port..."
    cd "$DEPLOY_DIR"

    pkill -f 'uvicorn main:app' 2>/dev/null || true
    sleep 1

    nohup python3 -m uvicorn main:app \
        --host 0.0.0.0 \
        --port "$port" \
        --workers 1 \
        --log-level info \
        > "$LOG_DIR/app.log" 2>&1 &

    local pid=$!
    log "  FastAPI PID=$pid  log=$LOG_DIR/app.log"

    sleep 3
    if curl -sf "http://localhost:$port/api/policy/status" >/dev/null 2>&1; then
        logg "FastAPI is responding on port $port"
    else
        log "FastAPI starting (may need a few more seconds)..."
    fi
}

# ── Phase 6: Health check ────────────────────────────────────────────────────
#
# Usage: health_check PORT1 PORT2 PORT3 ...

health_check() {
    echo ""
    log "============================================"
    log "  SERVICE HEALTH CHECK"
    log "============================================"

    local all_ok=1
    for port in "$@"; do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
            local model
            model=$(curl -s "http://localhost:$port/v1/models" 2>/dev/null \
                | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data'][0]['id'])" 2>/dev/null \
                || echo "?")
            logg "  Port $port: OK ($model)"
        elif curl -sf "http://localhost:$port/api/policy/status" >/dev/null 2>&1; then
            logg "  Port $port: OK (FastAPI)"
        else
            logr "  Port $port: NOT RESPONDING"
            all_ok=0
        fi
    done

    echo ""
    log "GPU Memory:"
    nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null | while read -r line; do
        log "  $line"
    done

    echo ""
    if [ "$all_ok" -eq 1 ]; then
        logg "All services are running!"
    else
        logr "Some services failed to start -- check logs in $LOG_DIR/"
    fi

    log ""
    log "Logs: $LOG_DIR/"
    log "Access: ssh -p <PORT> <HOST> -L 18000:localhost:18000 -N"
    log "============================================"
}
