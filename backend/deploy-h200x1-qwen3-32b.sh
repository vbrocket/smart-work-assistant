#!/usr/bin/env bash
#
# deploy-h200x1-qwen3-32b.sh
#
# Full deploy for: 1x NVIDIA H200 NVL | Qwen3-32B
#
# Services:
#   Port 8001  vLLM LLM        Qwen/Qwen3-32B           70% VRAM
#   Port 8002  vLLM Embedding   BAAI/bge-m3              10% VRAM
#   Port 8003  vLLM Reranker    BAAI/bge-reranker-v2-m3   5% VRAM
#   Port 18000 FastAPI app
#
# Usage:
#   chmod +x deploy-h200x1-qwen3-32b.sh
#   ./deploy-h200x1-qwen3-32b.sh
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
# Phase 3: Environment file
# ═══════════════════════════════════════════════════════════════════════════════

setup_env

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3b: Pre-built RAG data (skip ingestion on startup)
# ═══════════════════════════════════════════════════════════════════════════════

setup_rag_data() {
    log "Phase 3b: Setting up pre-built RAG data..."
    local src="$DEPLOY_DIR/../data/data"
    local dst="$DEPLOY_DIR/data"

    if [ ! -d "$src" ]; then
        log "No pre-built RAG data at $src -- will ingest on first run"
        return 0
    fi

    mkdir -p "$dst/faiss_store"

    for f in bm25_index.pkl assistant.db; do
        [ -f "$src/$f" ] && cp -f "$src/$f" "$dst/$f"
    done
    [ -d "$src/faiss_store" ] && cp -f "$src/faiss_store/"* "$dst/faiss_store/" 2>/dev/null

    echo -n 'vllm:bge-m3' > "$dst/embed_backend.txt"

    logg "RAG data copied to $dst (embed marker: vllm:bge-m3)"
}

setup_rag_data

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4: Start vLLM services
# ═══════════════════════════════════════════════════════════════════════════════

log ""
logb "Phase 4: Starting vLLM services on single H200..."

# 4a. LLM -- Qwen3-32B (70% VRAM)
start_vllm_service "Qwen/Qwen3-32B" 8001 0 0.70 vllm_llm \
    --tensor-parallel-size 1 \
    --max-model-len 32768 \
    --dtype bfloat16 \
    --reasoning-parser qwen3 \
    --reasoning-config.reasoning_start_str '<think>' \
    --reasoning-config.reasoning_end_str '</think>'

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

wait_for_port 8001 "LLM (Qwen3-32B)" 600 || {
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
