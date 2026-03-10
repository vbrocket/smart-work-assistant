#!/usr/bin/env bash
#
# setup-vastai.sh — Validate environment, stop conflicting services,
# install dependencies, download models, and run smoke tests on vast.ai.
#
# Run ONCE after SSH-ing into your vast.ai instance:
#   chmod +x setup-vastai.sh
#   ./setup-vastai.sh
#
# After this completes, run deploy-vastai.sh to start all services.
#
# GPU layout (TP=2):
#   GPU 0+1 → LLM     (Qwen3-32B, TP=2)     port 8001  (70% each GPU)
#   GPU 1   → Embedding (BGE-M3)             port 8002  (15%)
#              Reranker (bge-reranker-v2-m3)  port 8003  (15%)
#              Whisper + TTS (in-process)
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────
LLM_MODEL="${VLLM_LLM_MODEL:-Qwen/Qwen3-32B}"
LLM_PORT="${VLLM_LLM_PORT:-8001}"
LLM_GPU="${VLLM_LLM_GPU:-0,1}"

EMBED_MODEL="${VLLM_EMBED_MODEL:-BAAI/bge-m3}"
EMBED_PORT="${VLLM_EMBED_PORT:-8002}"
EMBED_GPU="${VLLM_EMBED_GPU:-1}"

RERANK_MODEL="${VLLM_RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
RERANK_PORT="${VLLM_RERANK_PORT:-8003}"
RERANK_GPU="${VLLM_RERANK_GPU:-1}"

WHISPER_MODEL="${WHISPER_MODEL:-large-v3-turbo}"

MIN_GPUS=2
MIN_VRAM_MB=79000
MIN_RAM_GB=120
MIN_DISK_GB=150
MIN_CUDA_MAJOR=12
MIN_CUDA_MINOR=4

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0; WARN=0; FAIL=0

pass()   { PASS=$((PASS+1)); echo -e "  ${GREEN}[PASS]${NC} $*"; }
warn()   { WARN=$((WARN+1)); echo -e "  ${YELLOW}[WARN]${NC} $*"; }
fail()   { FAIL=$((FAIL+1)); echo -e "  ${RED}[FAIL]${NC} $*"; }
info()   { echo -e "  ${CYAN}[INFO]${NC} $*"; }
header() { echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# Helper: detect HuggingFace cache dir (vast.ai may use /workspace/.hf_home)
detect_hf_cache() {
    if [ -d "/workspace/.hf_home/hub" ]; then
        echo "/workspace/.hf_home/hub"
    elif [ -d "$HOME/.cache/huggingface/hub" ]; then
        echo "$HOME/.cache/huggingface/hub"
    else
        echo "$HOME/.cache/huggingface/hub"
    fi
}
HF_CACHE=$(detect_hf_cache)

# Helper: kill any process listening on a given port
kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti ":$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        info "Killing process(es) on port $port: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

# ═════════════════════════════════════════════════════════════════════════
# PHASE 0: STOP CONFLICTING SERVICES
# ═════════════════════════════════════════════════════════════════════════

header "0/7  Stop Conflicting Services"

if command -v supervisorctl &>/dev/null; then
    info "Detected Supervisor — stopping vast.ai built-in vLLM..."
    supervisorctl stop vllm 2>/dev/null || true
    # Disable autostart so it doesn't come back
    for conf in /etc/supervisor/conf.d/*; do
        if grep -q "program:vllm" "$conf" 2>/dev/null; then
            sed -i 's/autostart=true/autostart=false/' "$conf"
            info "Disabled autostart in $conf"
        fi
    done
    supervisorctl reread 2>/dev/null || true
    supervisorctl update 2>/dev/null || true
    sleep 2
    pass "Supervisor vLLM stopped and disabled"
else
    info "No Supervisor found — skipping"
fi

# Kill any leftover GPU processes
GPU_PIDS=$(fuser /dev/nvidia* 2>/dev/null | tr -s ' ' '\n' | sort -u | tr '\n' ' ' || true)
if [ -n "$GPU_PIDS" ]; then
    info "Killing leftover GPU processes: $GPU_PIDS"
    for pid in $GPU_PIDS; do
        kill -9 "$pid" 2>/dev/null || true
    done
    sleep 3
    pass "Leftover GPU processes killed"
else
    pass "No leftover GPU processes"
fi

# Kill anything on our ports
for port in $LLM_PORT $EMBED_PORT $RERANK_PORT 8000; do
    kill_port "$port"
done

# Verify GPUs are free
GPU0_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0 2>/dev/null | xargs)
GPU1_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 1 2>/dev/null | xargs)
if [ "${GPU0_USED:-0}" -lt 1000 ] && [ "${GPU1_USED:-0}" -lt 1000 ]; then
    pass "GPUs are free (GPU0: ${GPU0_USED}MB, GPU1: ${GPU1_USED}MB)"
else
    warn "GPUs still have memory in use (GPU0: ${GPU0_USED}MB, GPU1: ${GPU1_USED}MB)"
    info "If vLLM fails to start, try rebooting the instance"
fi

# ═════════════════════════════════════════════════════════════════════════
# PHASE 1: SYSTEM VALIDATION
# ═════════════════════════════════════════════════════════════════════════

header "1/7  NVIDIA Driver & CUDA"

if ! command -v nvidia-smi &>/dev/null; then
    fail "nvidia-smi not found — NVIDIA driver not installed"
else
    DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -1)
    pass "NVIDIA driver: $DRIVER_VER"

    GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
    if [ "$GPU_COUNT" -ge "$MIN_GPUS" ]; then
        pass "GPUs detected: $GPU_COUNT (need >= $MIN_GPUS)"
    else
        warn "GPUs detected: $GPU_COUNT (need >= $MIN_GPUS) — adjust GPU assignments"
    fi

    echo ""
    info "GPU details:"
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | while read -r line; do
        idx=$(echo "$line" | cut -d',' -f1 | xargs)
        name=$(echo "$line" | cut -d',' -f2 | xargs)
        mem=$(echo "$line" | cut -d',' -f3 | xargs)
        info "  GPU $idx: $name ($mem)"
        mem_val=$(echo "$mem" | grep -oP '\d+')
        if [ "$mem_val" -ge "$MIN_VRAM_MB" ]; then
            pass "GPU $idx VRAM: ${mem_val}MB"
        else
            warn "GPU $idx VRAM: ${mem_val}MB (recommended >= ${MIN_VRAM_MB}MB)"
        fi
    done
    echo ""
    info "GPU assignment plan:"
    info "  GPU $LLM_GPU → LLM ($LLM_MODEL) on port $LLM_PORT"
    info "  GPU $EMBED_GPU → Embedding ($EMBED_MODEL) on port $EMBED_PORT"
    info "  GPU $RERANK_GPU → Reranker ($RERANK_MODEL) on port $RERANK_PORT"
    info "  GPU $EMBED_GPU → Whisper + TTS (in-process, shared)"
fi

if ! command -v nvcc &>/dev/null; then
    warn "nvcc not in PATH (vLLM bundles its own CUDA)"
else
    CUDA_VER=$(nvcc --version | grep -oP 'release \K[\d.]+')
    CUDA_MAJ=$(echo "$CUDA_VER" | cut -d. -f1)
    CUDA_MIN=$(echo "$CUDA_VER" | cut -d. -f2)
    if [ "$CUDA_MAJ" -gt "$MIN_CUDA_MAJOR" ] || \
       { [ "$CUDA_MAJ" -eq "$MIN_CUDA_MAJOR" ] && [ "$CUDA_MIN" -ge "$MIN_CUDA_MINOR" ]; }; then
        pass "CUDA toolkit: $CUDA_VER"
    else
        warn "CUDA toolkit: $CUDA_VER (vLLM needs >= $MIN_CUDA_MAJOR.$MIN_CUDA_MINOR but may use its bundled version)"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────
header "2/7  System Resources"

TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
if [ "$TOTAL_RAM_GB" -ge "$MIN_RAM_GB" ]; then
    pass "System RAM: ${TOTAL_RAM_GB}GB"
else
    warn "System RAM: ${TOTAL_RAM_GB}GB (recommended >= ${MIN_RAM_GB}GB)"
fi

DISK_AVAIL_GB=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')
if [ "$DISK_AVAIL_GB" -ge "$MIN_DISK_GB" ]; then
    pass "Disk available: ${DISK_AVAIL_GB}GB"
else
    fail "Disk available: ${DISK_AVAIL_GB}GB (need >= ${MIN_DISK_GB}GB)"
fi

info "HuggingFace cache: $HF_CACHE"

# ─────────────────────────────────────────────────────────────────────────
header "3/7  Python & System Tools"

PY_CMD=""
PIP_CMD=""

if command -v python3 &>/dev/null; then
    PY_CMD="python3"
elif command -v python &>/dev/null; then
    PY_CMD="python"
else
    fail "Python not found"
fi

if [ -n "$PY_CMD" ]; then
    PY_VER=$($PY_CMD --version 2>&1)
    pass "Python: $PY_VER"
fi

if command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
elif command -v pip &>/dev/null; then
    PIP_CMD="pip"
else
    fail "pip not found"
fi

if [ -n "$PIP_CMD" ]; then
    pass "pip: $($PIP_CMD --version | head -c 40)..."
fi

for tool in ffmpeg curl git; do
    if ! command -v "$tool" &>/dev/null; then
        warn "$tool not found — installing..."
        apt-get update -qq 2>/dev/null && apt-get install -y -qq "$tool" 2>/dev/null && pass "$tool installed" || fail "Could not install $tool"
    else
        pass "$tool found"
    fi
done

# ═════════════════════════════════════════════════════════════════════════
# PHASE 2: INSTALL PYTHON PACKAGES
# ═════════════════════════════════════════════════════════════════════════

header "4/7  Python Dependencies"

if [ -z "$PIP_CMD" ] || [ -z "$PY_CMD" ]; then
    fail "Cannot install packages — pip or python missing"
else
    info "Installing app requirements..."
    $PIP_CMD install -q -r requirements.txt 2>&1 | tail -5
    pass "App requirements installed"

    if ! $PY_CMD -c "import vllm" 2>/dev/null; then
        info "Installing vLLM..."
        $PIP_CMD install -q vllm 2>&1 | tail -5
    fi
    VLLM_VER=$($PY_CMD -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "unknown")
    pass "vLLM $VLLM_VER"

    info "Verifying critical imports..."
    $PY_CMD -c "
import sys
errors = []
for mod, label in [('torch','torch'), ('faster_whisper','faster_whisper'), ('fastapi','fastapi'), ('openai','openai')]:
    try:
        __import__(mod)
        print(f'  {label} OK')
    except ImportError as e:
        errors.append(f'{label}: {e}')
if errors:
    for e in errors: print(f'  MISSING: {e}')
    sys.exit(1)
"
    if [ $? -eq 0 ]; then
        pass "All critical Python packages importable"
    else
        fail "Some Python packages failed to import (see above)"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════
# PHASE 3: DOWNLOAD NON-VLLM MODELS (Whisper only)
# ═════════════════════════════════════════════════════════════════════════

header "5/7  Model Downloads (Whisper only)"

info "vLLM models download automatically on first 'vllm serve'."
info "Downloading non-vLLM models only..."
echo ""

download_model() {
    local model_id=$1
    local label=$2
    info "Downloading $label ($model_id)..."
    $PY_CMD -c "
from huggingface_hub import snapshot_download
import sys
try:
    path = snapshot_download('$model_id', resume_download=True)
    print(f'    Cached at: {path}')
except Exception as e:
    print(f'    ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1
    if [ $? -eq 0 ]; then
        pass "$label downloaded"
    else
        warn "$label download failed — will retry at runtime"
    fi
}

info "Downloading Whisper $WHISPER_MODEL..."
$PY_CMD -c "
from faster_whisper import WhisperModel
print('    Loading model to trigger download...')
model = WhisperModel('$WHISPER_MODEL', device='cpu', compute_type='int8')
del model
print('    Whisper model cached successfully')
" 2>&1
if [ $? -eq 0 ]; then
    pass "STT (Whisper $WHISPER_MODEL) downloaded"
else
    warn "STT (Whisper $WHISPER_MODEL) download failed — will retry at runtime"
fi

# ═════════════════════════════════════════════════════════════════════════
# PHASE 4: SMOKE TEST — vLLM servers on correct GPU + port
# ═════════════════════════════════════════════════════════════════════════

header "6/7  Smoke Tests — Start each vLLM server, verify GPU + port"

info "Testing PyTorch CUDA access..."
$PY_CMD -c "
import torch
assert torch.cuda.is_available(), 'CUDA not available'
for i in range(torch.cuda.device_count()):
    name = torch.cuda.get_device_name(i)
    mem = torch.cuda.get_device_properties(i).total_memory / 1024**3
    print(f'    GPU {i}: {name} ({mem:.0f}GB)')
t = torch.zeros(1, device='cuda'); del t
torch.cuda.empty_cache()
print('    CUDA tensor allocation: OK')
" 2>&1
if [ $? -eq 0 ]; then
    pass "PyTorch CUDA works"
else
    fail "PyTorch CUDA test failed"
fi

# Helper: start a vLLM server, wait for it, verify correct GPU, then stop it
smoke_test_vllm() {
    local model=$1 task=$2 port=$3 gpu=$4 label=$5 mem_util=$6 max_len=$7 dtype=$8 tp=${9:-1}
    local pid log_file

    info "Starting $label ($model) on GPU $gpu, port $port..."
    log_file="$LOG_DIR/smoke_${label// /_}.log"

    kill_port "$port"

    # Build vllm serve command:
    #   embed   → --convert embed --runner pooling
    #   score   → --convert classify --runner pooling
    #   generate → no extra flags (default)
    local vllm_args="--port $port --tensor-parallel-size $tp --gpu-memory-utilization $mem_util --max-model-len $max_len --dtype $dtype --disable-log-requests"
    if [ "$task" = "embed" ]; then
        vllm_args="--convert embed --runner pooling $vllm_args"
    elif [ "$task" = "score" ] || [ "$task" = "classify" ]; then
        vllm_args="--convert classify --runner pooling $vllm_args"
    fi

    CUDA_VISIBLE_DEVICES="$gpu" vllm serve "$model" $vllm_args \
        > "$log_file" 2>&1 &
    pid=$!

    local timeout=300
    if [ "$task" != "generate" ]; then
        timeout=120
    fi

    info "Waiting for $label (PID $pid, timeout ${timeout}s)..."
    local ready=0
    for i in $(seq 1 "$timeout"); do
        if ! kill -0 "$pid" 2>/dev/null; then
            fail "$label crashed during startup (see $log_file)"
            tail -10 "$log_file" 2>/dev/null | while read -r line; do echo "    $line"; done
            return 1
        fi
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done

    if [ "$ready" -eq 0 ]; then
        fail "$label did not start within ${timeout}s (see $log_file)"
        tail -10 "$log_file" 2>/dev/null | while read -r line; do echo "    $line"; done
        kill "$pid" 2>/dev/null || true
        return 1
    fi

    pass "$label started on port $port (${i}s)"

    # Verify model is listed
    local api_model
    api_model=$(curl -s "http://localhost:$port/v1/models" 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 || true)
    if [ -n "$api_model" ]; then
        pass "$label serving: $api_model"
    else
        warn "$label API responded but no model listed"
    fi

    # Verify correct GPU has memory usage
    local gpu_mem
    gpu_mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | xargs)
    if [ "${gpu_mem:-0}" -gt 500 ]; then
        pass "$label using GPU $gpu (${gpu_mem}MB)"
    else
        warn "$label may not be on GPU $gpu (only ${gpu_mem}MB used)"
    fi

    # Stop the smoke test server
    info "Stopping $label smoke test server..."
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    sleep 2

    # Free GPU memory
    kill_port "$port"
    return 0
}

echo ""
info "━━━ LLM Smoke Test ━━━"
if smoke_test_vllm "$LLM_MODEL" "generate" "$LLM_PORT" "$LLM_GPU" "LLM" "0.70" "32768" "bfloat16" "2"; then
    pass "LLM smoke test PASSED"
else
    fail "LLM smoke test FAILED"
    info "Check: $LOG_DIR/smoke_LLM.log"
fi

# Give GPU time to release memory
sleep 5

echo ""
info "━━━ Embedding Smoke Test ━━━"
if smoke_test_vllm "$EMBED_MODEL" "embed" "$EMBED_PORT" "$EMBED_GPU" "Embedding" "0.15" "8192" "float16"; then
    pass "Embedding smoke test PASSED"
else
    fail "Embedding smoke test FAILED"
    info "Check: $LOG_DIR/smoke_Embedding.log"
fi

sleep 3

echo ""
info "━━━ Reranker Smoke Test ━━━"
if smoke_test_vllm "$RERANK_MODEL" "score" "$RERANK_PORT" "$RERANK_GPU" "Reranker" "0.15" "512" "float16"; then
    pass "Reranker smoke test PASSED"
else
    fail "Reranker smoke test FAILED"
    info "Check: $LOG_DIR/smoke_Reranker.log"
fi

sleep 3

echo ""
info "━━━ Whisper CUDA Test ━━━"
$PY_CMD -c "
from faster_whisper import WhisperModel
import torch
if torch.cuda.is_available():
    model = WhisperModel('tiny', device='cuda', compute_type='float16')
    print('    faster-whisper CUDA: OK')
    del model
    torch.cuda.empty_cache()
else:
    print('    CUDA not available, will use CPU')
" 2>&1
if [ $? -eq 0 ]; then
    pass "Whisper CUDA test passed"
else
    warn "Whisper CUDA test had issues (will fall back to CPU)"
fi

# Clean up all GPU memory after smoke tests
sleep 3
for port in $LLM_PORT $EMBED_PORT $RERANK_PORT; do
    kill_port "$port"
done
GPU_PIDS=$(fuser /dev/nvidia* 2>/dev/null | tr -s ' ' '\n' | sort -u | tr '\n' ' ' || true)
if [ -n "$GPU_PIDS" ]; then
    for pid in $GPU_PIDS; do
        kill -9 "$pid" 2>/dev/null || true
    done
    sleep 3
fi

# ═════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════

header "7/7  Summary"

echo ""
echo -e "  ${GREEN}Passed: $PASS${NC}   ${YELLOW}Warnings: $WARN${NC}   ${RED}Failed: $FAIL${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "  ${RED}There were failures. Review the output above before running deploy-vastai.sh${NC}"
    echo ""
    echo "  Troubleshooting:"
    echo "    - Check smoke test logs: ls -la $LOG_DIR/smoke_*.log"
    echo "    - Verify GPU memory:     nvidia-smi"
    echo "    - Kill zombie processes:  fuser -k /dev/nvidia*"
    echo ""
    exit 1
fi

echo -e "  ${GREEN}All checks passed!${NC}"
echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  GPU Assignment (verified by smoke tests)                   │"
echo "  ├───────┬────────────────────────────────┬───────┬────────────┤"
echo "  │  GPU  │  Model                         │  Port │  Task      │"
echo "  ├───────┼────────────────────────────────┼───────┼────────────┤"
printf "  │  %-4s │  %-30s│  %-4s │  %-9s │\n" "$LLM_GPU" "$LLM_MODEL" "$LLM_PORT" "generate"
printf "  │  %-4s │  %-30s│  %-4s │  %-9s │\n" "$EMBED_GPU" "$EMBED_MODEL" "$EMBED_PORT" "embed"
printf "  │  %-4s │  %-30s│  %-4s │  %-9s │\n" "$RERANK_GPU" "$RERANK_MODEL" "$RERANK_PORT" "score"
printf "  │  %-4s │  %-30s│  %-4s │  %-9s │\n" "$EMBED_GPU" "Whisper ($WHISPER_MODEL)" "—" "in-process"
echo "  └───────┴────────────────────────────────┴───────┴────────────┘"
echo ""
echo "  Next steps:"
echo "    1. cp .env.vastai .env   (if not already done)"
echo "    2. ./deploy-vastai.sh    (starts all services)"
echo ""
