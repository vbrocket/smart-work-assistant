"""RAG integration tests: 8+ HR policy queries verifying grounded answers and citations.

Usage:
    python -m pytest tests/test_rag.py -v
    # or standalone:
    python tests/test_rag.py

These tests require a running Ollama instance and at least one ingested policy document.
If the RAG index is empty the tests are skipped gracefully.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from rag.models import QAResponse

# ── Helpers ────────────────────────────────────────────────────────

def _get_rag_service():
    """Import lazily to avoid import-time failures when deps are missing."""
    from services.rag_service import get_rag_service
    return get_rag_service()


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _skip_if_empty():
    try:
        svc = _get_rag_service()
        status = svc.get_status()
        if status.get("indexed_chunks", 0) == 0:
            pytest.skip("RAG index is empty – ingest documents first")
    except Exception as e:
        pytest.skip(f"RAG service unavailable: {e}")


# ── Test queries ───────────────────────────────────────────────────

HR_QUERIES = [
    {
        "id": "allowances_housing",
        "question": "ما هي نسبة بدل السكن للموظفين؟",
        "description": "Housing allowance percentage",
        "expect_found": True,
    },
    {
        "id": "allowances_transport",
        "question": "كم بدل النقل أو المواصلات؟",
        "description": "Transport allowance amount",
        "expect_found": True,
    },
    {
        "id": "overtime_limits",
        "question": "ما هي ساعات العمل الإضافي المسموح بها؟",
        "description": "Overtime limits",
        "expect_found": True,
    },
    {
        "id": "termination_cases",
        "question": "ما هي حالات إنهاء خدمة الموظف؟",
        "description": "Termination cases",
        "expect_found": True,
    },
    {
        "id": "promotion_salary",
        "question": "ما هي شروط الترقية وزيادة الراتب؟",
        "description": "Promotion / salary increase rules",
        "expect_found": True,
    },
    {
        "id": "travel_perdiem",
        "question": "ما هي سياسة السفر وبدل الإقامة اليومي؟",
        "description": "Travel / per-diem rules",
        "expect_found": True,
    },
    {
        "id": "leave_annual",
        "question": "كم عدد أيام الإجازة السنوية المستحقة؟",
        "description": "Annual leave entitlement",
        "expect_found": True,
    },
    {
        "id": "probation_period",
        "question": "ما هي مدة فترة التجربة للموظف الجديد؟",
        "description": "Probation period",
        "expect_found": True,
    },
    {
        "id": "disciplinary",
        "question": "ما هي الإجراءات التأديبية المنصوص عليها في السياسة؟",
        "description": "Disciplinary procedures",
        "expect_found": True,
    },
    {
        "id": "not_found_topic",
        "question": "ما هي سياسة الشركة بخصوص تربية الحيوانات الأليفة في المكتب؟",
        "description": "Topic NOT in policy – expect Not Found",
        "expect_found": False,
    },
]


# ── Tests ──────────────────────────────────────────────────────────

class TestRAGQueries:
    """Test grounded QA for a set of HR policy questions."""

    @pytest.fixture(autouse=True)
    def check_index(self):
        _skip_if_empty()

    @pytest.mark.parametrize(
        "query_case",
        HR_QUERIES,
        ids=[q["id"] for q in HR_QUERIES],
    )
    def test_query(self, query_case):
        svc = _get_rag_service()
        result: QAResponse = _run(svc.answer(query_case["question"]))

        assert isinstance(result, QAResponse), "Expected QAResponse instance"
        assert result.answer_ar, "answer_ar should not be empty"
        assert result.confidence in ("high", "medium", "low"), "Invalid confidence level"

        if query_case["expect_found"]:
            assert result.answer_ar != "غير موجود", (
                f"Expected grounded answer for '{query_case['description']}', got 'Not Found'"
            )
            assert len(result.citations) > 0, (
                f"Expected at least one citation for '{query_case['description']}'"
            )
            for c in result.citations:
                assert c.page >= 0, "Citation page should be >= 0"
                assert len(c.quote) <= 200, "Citation quote should be reasonably short"
        else:
            is_not_found = (
                "غير موجود" in result.answer_ar
                or result.confidence == "low"
            )
            assert is_not_found, (
                f"Expected 'Not Found' or low confidence for missing topic, "
                f"got: {result.answer_ar[:100]}"
            )

        if result.retrieval_debug:
            assert isinstance(result.retrieval_debug.dense_top, list)
            assert isinstance(result.retrieval_debug.bm25_top, list)


class TestChunker:
    """Unit tests for the section-based chunker."""

    def test_multi_level_detection(self):
        from rag.chunker import SectionChunker

        pages = [
            {
                "text": (
                    "1.1 الغرض\n"
                    "هذه السياسة تحدد قواعد الموارد البشرية.\n"
                    "1.2 النطاق\n"
                    "تنطبق على جميع الموظفين.\n"
                    "1.2.1 الاستثناءات\n"
                    "لا تشمل المتعاقدين المؤقتين."
                ),
                "page": 1,
            }
        ]

        chunker = SectionChunker(max_tokens=5000)
        records = chunker.chunk_pages(pages, doc_id="test", doc_name="test.pdf")

        section_ids = [r.section_id for r in records]
        assert "1.1" in section_ids
        assert "1.2" in section_ids
        assert "1.2.1" in section_ids

    def test_sub_splitting(self):
        from rag.chunker import SectionChunker

        long_text = ". ".join([f"جملة رقم {i} في هذا القسم الطويل جدا" for i in range(200)])
        pages = [{"text": f"1.1 عنوان طويل\n{long_text}", "page": 1}]

        chunker = SectionChunker(max_tokens=100, overlap_tokens=20)
        records = chunker.chunk_pages(pages, doc_id="test", doc_name="test.pdf")

        assert len(records) > 1, "Long clause should be sub-split"
        assert all(r.section_id == "1.1" for r in records), "All sub-chunks should share section_id"
        sub_ids = [r.sub_id for r in records]
        assert "A" in sub_ids
        assert "B" in sub_ids


class TestArabicUtils:
    """Unit tests for Arabic text normalization."""

    def test_normalize(self):
        from rag.arabic_utils import normalize_arabic

        assert normalize_arabic("إبراهيم") == "ابراهيم"
        assert normalize_arabic("أحمد") == "احمد"
        assert normalize_arabic("كتـــاب") == "كتاب"  # tatweel
        assert normalize_arabic("مُحَمَّد") == "محمد"  # diacritics

    def test_tokenize(self):
        from rag.arabic_utils import tokenize_arabic

        tokens = tokenize_arabic("سياسة الموارد البشرية 2024")
        assert "سياسه" in tokens
        assert "الموارد" in tokens
        assert "2024" in tokens


class TestBM25Store:
    """Unit tests for BM25 store."""

    def test_build_and_search(self, tmp_path):
        from rag.bm25_store import BM25Store
        from rag.models import ChunkRecord

        store = BM25Store(index_path=str(tmp_path / "bm25.pkl"))

        records = [
            ChunkRecord(text="سياسة بدل السكن للموظفين الدائمين", section_id="1.1", doc_id="test"),
            ChunkRecord(text="إجراءات الترقية وزيادة الراتب", section_id="2.1", doc_id="test"),
            ChunkRecord(text="ساعات العمل الإضافي والتعويض", section_id="3.1", doc_id="test"),
        ]
        store.build(records)
        assert store.count() == 3

        hits = store.search("بدل السكن", top_k=2)
        assert len(hits) > 0
        assert "سكن" in hits[0].text or "السكن" in hits[0].text

        # Test persistence
        store2 = BM25Store(index_path=str(tmp_path / "bm25.pkl"))
        assert store2.count() == 3


# ── Standalone runner ──────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
