#!/usr/bin/env bash
#
# start_all_h200.sh — Quick-start all 3 vLLM servers on a single H200 NVL GPU.
# Use this to manually start servers outside of deploy-h200.sh.
#
set -euo pipefail
cd /workspace/pwa-idea/backend
mkdir -p logs

echo "[$(date '+%H:%M:%S')] Starting LLM (Qwen3-32B, TP=1, GPU 0, 55% VRAM)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen3-32B \
    --port 8001 \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.70 \
    --max-model-len 32768 \
    --dtype bfloat16 \
    --disable-log-requests \
    > logs/vllm_llm.log 2>&1 &
echo "  LLM PID=$!"

echo "[$(date '+%H:%M:%S')] Starting Embedding (bge-m3, GPU 0, 10% VRAM)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve BAAI/bge-m3 \
    --convert embed \
    --runner pooling \
    --port 8002 \
    --gpu-memory-utilization 0.10 \
    --max-model-len 8192 \
    --dtype float16 \
    --disable-log-requests \
    > logs/vllm_embed.log 2>&1 &
echo "  Embed PID=$!"

echo "[$(date '+%H:%M:%S')] Starting Reranker (bge-reranker-v2-m3, GPU 0, 5% VRAM)..."
CUDA_VISIBLE_DEVICES=0 nohup vllm serve BAAI/bge-reranker-v2-m3 \
    --convert classify \
    --runner pooling \
    --port 8003 \
    --gpu-memory-utilization 0.05 \
    --max-model-len 512 \
    --dtype float16 \
    --disable-log-requests \
    > logs/vllm_rerank.log 2>&1 &
echo "  Reranker PID=$!"

echo "[$(date '+%H:%M:%S')] All 3 vLLM servers started. Waiting for readiness..."

for port_name in "8001:LLM" "8002:Embedding" "8003:Reranker"; do
    port="${port_name%%:*}"
    name="${port_name##*:}"
    echo -n "  Waiting for $name (port $port)..."
    for i in $(seq 1 120); do
        if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
            echo " READY (${i}s)"
            break
        fi
        if [ "$i" -eq 120 ]; then
            echo " TIMEOUT after 120s"
            echo "  Check logs/vllm_${name,,}.log"
        fi
        sleep 5
    done
done

echo ""
echo "[$(date '+%H:%M:%S')] GPU Memory:"
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
echo ""
echo "All services ready. Now start the app:"
echo "  cd /workspace/pwa-idea/backend"
echo "  nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 18000 > logs/app.log 2>&1 &"
