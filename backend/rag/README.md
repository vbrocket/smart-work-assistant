# RAG System: Qdrant + Hybrid Retrieval + Section-Based Chunking

## Overview

This RAG system provides grounded, citation-backed answers to HR policy questions.
It uses **hybrid retrieval** (dense vectors via Qdrant/FAISS + lexical BM25) with
**cross-encoder reranking** and **section-aware chunking** for Arabic policy PDFs.

## Architecture

```
PDF ─→ Section Chunker ─→ Embedder (BGE-M3) ─→ Qdrant (dense)
   └→ Table Extractor ─┘                    └→ BM25 (lexical)

Query ─→ Dense Search (top 30)  ─→ RRF Fusion ─→ Reranker (top 8) ─→ QA Prompt ─→ LLM
     └→ BM25 Search (top 30) ─┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Install & Start Qdrant (Linux)

**Option A: Download binary**

```bash
# Download latest Qdrant release
curl -LO https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-gnu.tar.gz
tar xzf qdrant-x86_64-unknown-linux-gnu.tar.gz
sudo mv qdrant /usr/local/bin/

# Create data directory and user
sudo useradd -r -s /bin/false qdrant
sudo mkdir -p /var/lib/qdrant /etc/qdrant
sudo chown qdrant:qdrant /var/lib/qdrant

# Create minimal config
sudo tee /etc/qdrant/config.yaml << 'EOF'
storage:
  storage_path: /var/lib/qdrant/storage
  snapshots_path: /var/lib/qdrant/snapshots
service:
  host: 0.0.0.0
  http_port: 6333
  grpc_port: 6334
EOF

# Install systemd service
sudo cp backend/services/qdrant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qdrant
sudo systemctl status qdrant
```

**Option B: Without systemd (development)**

```bash
./qdrant --config-path config.yaml
```

### 2b. Windows / No Docker (development)

Download the Windows binary from the [Qdrant releases page](https://github.com/qdrant/qdrant/releases)
and run it directly:

```powershell
.\qdrant.exe
```

If Qdrant is not available, the system automatically falls back to a local
FAISS + SQLite store (set `QDRANT_FALLBACK=true` in `.env`).

### 3. Pull Embedding Model

```bash
ollama pull bge-m3
```

### 4. Place Documents

Put your PDF policy files in `backend/documents/`.

### 5. Run Ingestion

```bash
# Via API
curl -X POST http://localhost:8000/api/policy/ingest

# Or start the server and use the UI
cd backend && python main.py
```

### 6. Query

```bash
curl -X POST http://localhost:8000/api/policy/query \
  -H "Content-Type: application/json" \
  -d '{"question": "ما هي نسبة بدل السكن؟"}'
```

Response:
```json
{
  "answer_ar": "...",
  "citations": [
    {"section_id": "4.2.1", "section_title": "بدل السكن", "page": 12, "quote": "..."}
  ],
  "confidence": "high",
  "retrieval_debug": { ... }
}
```

## Configuration

All settings can be set via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | localhost | Qdrant server host |
| `QDRANT_PORT` | 6333 | Qdrant server port |
| `QDRANT_COLLECTION` | hr_policy_rag | Collection name |
| `QDRANT_FALLBACK` | true | Fall back to FAISS if Qdrant unavailable |
| `BM25_INDEX_PATH` | data/bm25_index.pkl | BM25 index file path |
| `RERANKER_MODEL` | BAAI/bge-reranker-v2-m3 | Cross-encoder model |
| `RAG_FUSION_METHOD` | rrf | Fusion: "rrf" or "weighted" |
| `RAG_DENSE_TOP_K` | 30 | Dense retrieval candidates |
| `RAG_BM25_TOP_K` | 30 | BM25 retrieval candidates |
| `RAG_RERANK_TOP_K` | 20 | Candidates sent to reranker |
| `RAG_FINAL_TOP_K` | 8 | Final contexts for LLM |
| `RAG_CHUNK_MAX_TOKENS` | 1100 | Max tokens per chunk |
| `RAG_CHUNK_OVERLAP_TOKENS` | 100 | Overlap tokens within section |
| `EMBEDDING_BATCH_SIZE` | 8 | Embedding batch size |

## CPU/GPU

By default, embeddings run via Ollama (CPU or GPU as configured in Ollama).
The reranker uses CPU by default and auto-detects CUDA if available.

To force CPU-only for the reranker:
```bash
CUDA_VISIBLE_DEVICES="" python main.py
```

## Migration from Old RAG

See `backend/rag/migrate.py` for:
- `OldRagAdapter`: query the old ChromaDB-based system
- `ShadowMode`: run both systems and compare results
- `import_jsonl()`: bulk-import chunks from a JSONL file
