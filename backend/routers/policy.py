"""Policy Router - Endpoints for document ingestion, upload, delete, index management, and grounded QA."""

import os
import re

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List

from services.rag_service import get_rag_service

router = APIRouter()

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md", "text"}


# ── Response models ──────────────────────────────────────────────

class IngestResponse(BaseModel):
    status: str
    documents: int
    chunks: int
    text_chunks: Optional[int] = None
    table_rows: Optional[int] = None
    source_files: Optional[List[str]] = None
    ingested_at: Optional[str] = None


class FileDetail(BaseModel):
    name: str
    size_bytes: int
    type: str


class PolicyStatusResponse(BaseModel):
    indexed_chunks: int
    bm25_docs: Optional[int] = None
    document_files: List[str]
    document_count: int
    last_ingestion: Optional[str] = None
    documents_dir: str
    file_details: List[FileDetail] = []


class UploadResponse(BaseModel):
    filename: str
    size_bytes: int


class DeleteResponse(BaseModel):
    deleted: str


class CitationItem(BaseModel):
    section_id: str = ""
    section_title: str = ""
    page: int = 0
    quote: str = ""


class RetrievalDebugResponse(BaseModel):
    dense_top: List[str] = []
    bm25_top: List[str] = []
    fused_top: List[str] = []
    reranked_top: List[str] = []


class QueryRequest(BaseModel):
    question: str
    language: Optional[str] = "ar"


class QueryResponse(BaseModel):
    answer_ar: str
    citations: List[CitationItem] = []
    confidence: str = "low"
    retrieval_debug: Optional[RetrievalDebugResponse] = None


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """Upload a policy document (PDF, DOCX, TXT, MD) to the documents folder."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    rag = get_rag_service()
    docs_dir = rag.documents_dir
    os.makedirs(docs_dir, exist_ok=True)

    safe_name = re.sub(r"[^\w.\-\u0600-\u06FF ]", "_", file.filename)
    dest = os.path.join(docs_dir, safe_name)
    counter = 1
    name_base, name_ext = os.path.splitext(safe_name)
    while os.path.exists(dest):
        safe_name = f"{name_base}_{counter}{name_ext}"
        dest = os.path.join(docs_dir, safe_name)
        counter += 1

    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    return UploadResponse(filename=safe_name, size_bytes=len(content))


@router.delete("/documents/{filename}", response_model=DeleteResponse)
async def delete_document(filename: str):
    """Delete a policy document from the documents folder."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    rag = get_rag_service()
    filepath = os.path.join(rag.documents_dir, filename)

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    os.remove(filepath)
    return DeleteResponse(deleted=filename)


@router.post("/ingest", response_model=IngestResponse)
async def ingest_documents():
    """Trigger ingestion: load -> section-chunk -> table-extract -> embed -> store in Qdrant + BM25."""
    try:
        rag = get_rag_service()
        result = await rag.ingest()
        return IngestResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@router.get("/status", response_model=PolicyStatusResponse)
async def get_policy_status():
    """Return the current state of the policy document index."""
    try:
        rag = get_rag_service()
        status = rag.get_status()

        file_details = []
        for fname in status.get("document_files", []):
            fpath = os.path.join(rag.documents_dir, fname)
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "unknown"
            size = os.path.getsize(fpath) if os.path.isfile(fpath) else 0
            file_details.append(FileDetail(name=fname, size_bytes=size, type=ext))

        return PolicyStatusResponse(
            indexed_chunks=status["indexed_chunks"],
            bm25_docs=status.get("bm25_docs"),
            document_files=status["document_files"],
            document_count=status["document_count"],
            last_ingestion=status.get("last_ingestion"),
            documents_dir=status["documents_dir"],
            file_details=file_details,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


@router.post("/query", response_model=QueryResponse)
async def query_policy(request: QueryRequest):
    """Grounded QA: retrieve relevant policy chunks and return an answer with citations."""
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question is required")

    try:
        rag = get_rag_service()
        qa_result = await rag.answer(request.question)

        debug = None
        if qa_result.retrieval_debug:
            debug = RetrievalDebugResponse(
                dense_top=qa_result.retrieval_debug.dense_top,
                bm25_top=qa_result.retrieval_debug.bm25_top,
                fused_top=qa_result.retrieval_debug.fused_top,
                reranked_top=qa_result.retrieval_debug.reranked_top,
            )

        return QueryResponse(
            answer_ar=qa_result.answer_ar,
            citations=[
                CitationItem(
                    section_id=c.section_id,
                    section_title=c.section_title,
                    page=c.page,
                    quote=c.quote,
                )
                for c in qa_result.citations
            ],
            confidence=qa_result.confidence,
            retrieval_debug=debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")
