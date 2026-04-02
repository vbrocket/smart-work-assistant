#!/usr/bin/env bash
#
# deploy-h200x1-qwen35-27b-namaa.sh
#
# Full deploy for: 1x NVIDIA H200 NVL | Qwen3.5-27B | NAMAA Saudi TTS
#
# Services:
#   Port 8001  vLLM LLM        Qwen/Qwen3.5-27B         70% VRAM
#   Port 8002  vLLM Embedding   BAAI/bge-m3              10% VRAM
#   Port 8003  vLLM Reranker    BAAI/bge-reranker-v2-m3   5% VRAM
#   Port 18000 FastAPI app
#
# TTS: NAMAA-Saudi-TTS (chatterbox, local GPU)
# Router: shares main LLM on port 8001
#
# Usage:
#   chmod +x deploy-h200x1-qwen35-27b-namaa.sh
#   ./deploy-h200x1-qwen35-27b-namaa.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/deploy-base.sh"

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 0: Clean GPU
# ═══════════════════════════════════════════════════════════════════════════════

kill_gpu

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: System dependencies
# ═══════════════════════════════════════════════════════════════════════════════

install_system_deps

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Base Python dependencies
# ═══════════════════════════════════════════════════════════════════════════════

install_pip_base

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2b: NAMAA TTS dependencies
# ═══════════════════════════════════════════════════════════════════════════════

install_tts_deps() {
    log "Phase 2b: Installing NAMAA TTS dependencies..."

    if python3 -c "from chatterbox import mtl_tts" 2>/dev/null; then
        log "chatterbox-tts already importable -- skipping TTS deps"
        return 0
    fi

    log "Installing chatterbox-tts + torchcodec..."
    pip install -q chatterbox-tts torchcodec 2>&1 | tail -3

    log "Restoring torch==2.10.0 + torchaudio==2.10.0 (vLLM compat)..."
    pip install -q torch==2.10.0 torchaudio==2.10.0 \
        --index-url https://download.pytorch.org/whl/cu126 2>&1 | tail -3

    log "Pinning transformers<5 (vLLM compat)..."
    pip install -q 'transformers>=4.56.0,<5' 2>&1 | tail -3

    logg "NAMAA TTS deps installed"
}

install_tts_deps

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3: Environment file
# ═══════════════════════════════════════════════════════════════════════════════

setup_env

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4: Start vLLM services
# ═══════════════════════════════════════════════════════════════════════════════

log ""
logb "Phase 4: Starting vLLM services on single H200..."

# 4a. LLM -- Qwen3.5-27B (70% VRAM)
start_vllm_service "Qwen/Qwen3.5-27B" 8001 0 0.70 vllm_llm \
    --tensor-parallel-size 1 \
    --max-model-len 32768 \
    --dtype bfloat16

# 4b. Embedding -- BGE-M3 (10% VRAM)
start_vllm_service "BAAI/bge-m3" 8002 0 0.10 vllm_embed \
    --convert embed \
    --runner pooling \
    --max-model-len 8192 \
    --dtype float16

# 4c. Reranker -- bge-reranker-v2-m3 (5% VRAM)
start_vllm_service "BAAI/bge-reranker-v2-m3" 8003 0 0.05 vllm_rerank \
    --convert classify \
    --runner pooling \
    --max-model-len 512 \
    --dtype float16

# ═══════════════════════════════════════════════════════════════════════════════
# Wait for vLLM services
# ═══════════════════════════════════════════════════════════════════════════════

log ""
log "Waiting for vLLM services..."

wait_for_port 8001 "LLM (Qwen3.5-27B)" 600 || {
    logr "LLM failed. Last 20 lines:"
    tail -20 "$LOG_DIR/vllm_llm.log" 2>/dev/null
    exit 1
}

wait_for_port 8002 "Embedding (bge-m3)" 180 || {
    logr "Embedding failed. Last 20 lines:"
    tail -20 "$LOG_DIR/vllm_embed.log" 2>/dev/null
}

wait_for_port 8003 "Reranker (bge-reranker-v2-m3)" 180 || {
    logr "Reranker failed. Last 20 lines:"
    tail -20 "$LOG_DIR/vllm_rerank.log" 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5: Start FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

start_fastapi 18000

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6: Health check
# ═══════════════════════════════════════════════════════════════════════════════

health_check 8001 8002 8003 18000
