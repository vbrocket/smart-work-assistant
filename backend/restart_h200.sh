#!/bin/bash
# restart_h200.sh â€” Clean restart all services on H200 NVL
# Starts router AFTER other models to avoid memory contention
set -uo pipefail
cd /workspace/pwa-idea/backend
mkdir -p logs

echo "[$(date +%H:%M:%S)] Killing existing services..."
pkill -9 -f "uvicorn main:app" 2>/dev/null || true
pkill -9 -f "vllm serve" 2>/dev/null || true
sleep 3
fuser -k /dev/nvidia* 2>/dev/null || true
sleep 5
echo "[$(date +%H:%M:%S)] GPU cleared: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"

echo "[$(date +%H:%M:%S)] Starting LLM (Qwen3-32B, 60% VRAM, 24k ctx)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen3-32B \
    --port 8001 --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.60 --max-model-len 24000 \
    --dtype bfloat16 --disable-log-requests \
    > logs/vllm_llm.log 2>&1 &

echo "[$(date +%H:%M:%S)] Starting Embedding (bge-m3, 6% VRAM)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve BAAI/bge-m3 \
    --convert embed --runner pooling --port 8002 \
    --gpu-memory-utilization 0.06 --max-model-len 8192 \
    --dtype float16 --disable-log-requests \
    > logs/vllm_embed.log 2>&1 &

echo "[$(date +%H:%M:%S)] Starting Reranker (bge-reranker-v2-m3, 3% VRAM)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve BAAI/bge-reranker-v2-m3 \
    --convert classify --runner pooling --port 8003 \
    --gpu-memory-utilization 0.03 --max-model-len 512 \
    --dtype float16 --disable-log-requests \
    > logs/vllm_rerank.log 2>&1 &

echo "[$(date +%H:%M:%S)] Waiting for LLM to load (may take 3-5 min)..."
for i in $(seq 1 300); do
    if curl -sf http://localhost:8001/v1/models >/dev/null 2>&1; then
        echo "[$(date +%H:%M:%S)] LLM ready after ${i}s"
        break
    fi
    sleep 2
done

echo "[$(date +%H:%M:%S)] Starting Router (Qwen2.5-3B, 22% VRAM, after others loaded)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen2.5-3B-Instruct \
    --port 8004 --gpu-memory-utilization 0.22 \
    --max-model-len 2048 --dtype bfloat16 \
    --enforce-eager --disable-log-requests \
    > logs/vllm_router.log 2>&1 &

echo "[$(date +%H:%M:%S)] Waiting for Router..."
for i in $(seq 1 120); do
    if curl -sf http://localhost:8004/v1/models >/dev/null 2>&1; then
        echo "[$(date +%H:%M:%S)] Router ready after ${i}s"
        break
    fi
    sleep 2
done

echo "[$(date +%H:%M:%S)] Starting FastAPI app on port 18000..."
nohup python3 -m uvicorn main:app \
    --host 0.0.0.0 --port 18000 \
    --workers 1 --log-level info \
    > logs/app.log 2>&1 &

sleep 3
echo ""
echo "[$(date +%H:%M:%S)] === Status ==="
for p in 8001 8002 8003 8004; do
    printf "  Port %s: " "$p"
    curl -sf "http://localhost:$p/v1/models" >/dev/null 2>&1 && echo "UP" || echo "DOWN"
done
echo "  GPU: $(nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader)"
echo "[$(date +%H:%M:%S)] Done!"
