"""Grounded QA engine: builds a strict prompt, calls the LLM, and parses structured JSON output."""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .models import Citation, DocHit, QAResponse, RetrievalDebug

logger = logging.getLogger("rag.qa")

QA_SYSTEM_PROMPT = """\
أنت مساعد متخصص في الإجابة عن أسئلة سياسات الموارد البشرية.

=== تعليمات صارمة ===
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، أجب بكلمة واحدة: "غير موجود".
3. يجب أن تكون الإجابة باللغة العربية.
4. كن شاملاً ومفصلاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات الموجودة في السياق. لا تختصر.
5. إذا وجدت أرقاماً أو مبالغ أو نسب مئوية، اذكرها جميعاً بالتفصيل.
6. لكل معلومة تذكرها، قدم اقتباسًا مع:
   - section_id: رقم البند/القسم (كنص بين علامتي تنصيص)
   - section_title: عنوان القسم (إن وجد)
   - page: رقم الصفحة
   - quote: اقتباس قصير (25 كلمة أو أقل) من النص الأصلي
7. حدد مستوى الثقة: "high" إذا كانت الإجابة واضحة، "medium" إذا كانت تقريبية، "low" إذا كنت غير متأكد.

=== التنسيق المطلوب ===
أجب حصريًا بكائن JSON بالشكل التالي (بدون أي نص إضافي قبله أو بعده):
{
  "answer_ar": "الإجابة الشاملة هنا مع جميع التفاصيل",
  "citations": [
    {"section_id": "...", "section_title": "...", "page": 0, "quote": "..."}
  ],
  "confidence": "high|medium|low"
}

=== السياق ===
"""

QA_CHAT_PROMPT = """\
أنت مساعد متخصص في الإجابة عن أسئلة سياسات الموارد البشرية.

=== تعليمات صارمة ===
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، قل بوضوح: "لم أجد معلومات عن هذا الموضوع في السياسات المتوفرة".
3. يجب أن تكون الإجابة باللغة العربية.
4. كن شاملاً ومفصلاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات الموجودة في السياق. لا تختصر.
5. إذا وجدت أرقاماً أو مبالغ أو نسب مئوية، اذكرها جميعاً بالتفصيل.
6. ادمج المراجع في النص بشكل طبيعي، مثلاً: "وفقاً للبند 4.2 (صفحة 5)..." أو "كما ينص البند 3.1 في صفحة 12...".
7. يمكنك استخدام قوائم مرقمة إذا كان ذلك يوضح الإجابة.
8. لا تستخدم JSON أو أي تنسيق برمجي. اكتب نصاً عربياً واضحاً ومقروءاً.
9. لا تستخدم ماركداون (لا نجوم، لا أقواس مربعة، لا روابط).

=== السياق ===
"""

QA_VOICE_PROMPT = """\
أنت مساعد متخصص في الإجابة عن أسئلة سياسات الموارد البشرية.
سيتم قراءة إجابتك بصوت عالٍ عبر محرك تحويل النص إلى كلام.

=== تعليمات صارمة ===
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، قل بوضوح: "لم أجد معلومات عن هذا الموضوع في السياسات المتوفرة".
3. يجب أن تكون الإجابة باللغة العربية.
4. كن شاملاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات.
5. اكتب الأرقام بالكلمات بشكل طبيعي (مثلاً "خمسة عشر يوماً" وليس "15 يوماً").
6. ادمج المراجع بشكل طبيعي في الكلام، مثل: "وفقاً للبند الرابع من سياسة الإجازات..." أو "كما ينص البند الثالث في الصفحة الخامسة...".
7. لا تستخدم أي تنسيق: لا ماركداون، لا نجوم، لا شرطات، لا أقواس، لا JSON، لا جداول، لا قوائم.
8. اكتب بأسلوب محادثة طبيعي سلس كأنك تتحدث مع زميل في العمل.
9. اجعل الإجابة في ثلاث إلى ثمان جمل متصلة ومترابطة.

=== السياق ===
"""


def _normalize_text(text: str) -> str:
    """Convert Arabic Presentation Forms to standard Arabic for LLM readability."""
    return unicodedata.normalize("NFKC", text)


def _build_context_block(hits: List[DocHit]) -> str:
    parts: List[str] = []
    for i, hit in enumerate(hits, 1):
        meta = hit.metadata
        sid = meta.get("section_id", "?")
        title = _normalize_text(meta.get("section_title", ""))
        page = meta.get("page_start", meta.get("page", 0))
        header = f"[{i}] section_id={sid}  page={page}"
        if title:
            header += f"  title={title}"
        parts.append(f"{header}\n{_normalize_text(hit.text)}")
    return "\n---\n".join(parts)


class QAEngine:
    """Build grounded QA prompts and parse LLM output into structured QAResponse."""

    def __init__(self, provider: Any = None):
        """Accept an LLMProvider.  When None, a provider is created lazily."""
        self._provider = provider

    async def answer(
        self,
        question: str,
        hits: List[DocHit],
        debug: Optional[RetrievalDebug] = None,
        voice_mode: bool = False,
    ) -> QAResponse:
        """Generate a grounded answer from retrieved hits."""
        if not hits:
            return QAResponse(
                answer_ar="غير موجود",
                citations=[],
                confidence="low",
                retrieval_debug=debug,
            )

        context = _build_context_block(hits)
        base = QA_VOICE_PROMPT if voice_mode else QA_SYSTEM_PROMPT
        system_prompt = base + context

        user_msg = f"السؤال: {question}"

        logger.info("QA calling LLM | hits=%d | context_chars=%d | voice=%s", len(hits), len(context), voice_mode)
        raw = await self._call_llm(system_prompt, user_msg)
        logger.info("QA LLM raw response length: %d", len(raw))

        if voice_mode:
            return QAResponse(
                answer_ar=raw.strip(),
                citations=[],
                confidence="high",
                retrieval_debug=debug,
            )

        response = self._parse_response(raw, debug)
        logger.info(
            "QA parsed | confidence=%s | citations=%d | answer_len=%d",
            response.confidence, len(response.citations), len(response.answer_ar),
        )
        return response

    @property
    def provider(self):
        if self._provider is None:
            from services.llm_provider import create_llm_provider
            self._provider = create_llm_provider()
        return self._provider

    async def answer_stream(
        self,
        question: str,
        hits: List[DocHit],
        debug: Optional[RetrievalDebug] = None,
        voice_mode: bool = False,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream the QA answer token-by-token, then emit a metadata event.

        Yields dicts:
            {"type": "token", "content": "..."} for each LLM token
            {"type": "meta", "citations": [...], "confidence": "..."}  at the end

        When voice_mode=True, the LLM is prompted to produce natural spoken
        prose (no JSON), so every streamed token is TTS-safe.
        """
        if not hits:
            not_found = ("لم أجد معلومات عن هذا الموضوع في السياسات المتوفرة"
                         if voice_mode else "غير موجود")
            yield {"type": "token", "content": not_found}
            yield {"type": "meta", "citations": [], "confidence": "low",
                   "answer_ar": not_found}
            return

        context = _build_context_block(hits)
        if voice_mode:
            base = QA_VOICE_PROMPT
        else:
            base = QA_CHAT_PROMPT
        system_prompt = base + context
        user_msg = f"السؤال: {question}"

        logger.info("QA stream | hits=%d | context_chars=%d | voice=%s",
                     len(hits), len(context), voice_mode)

        full = ""
        async for token in self.provider.chat_stream(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=2048,
        ):
            full += token
            yield {"type": "token", "content": token}

        yield {
            "type": "meta",
            "citations": [],
            "confidence": "high",
            "answer_ar": full.strip(),
        }

    async def _call_llm(self, system: str, user: str) -> str:
        return await self.provider.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=2048,
        )

    @staticmethod
    def _fix_json(text: str) -> str:
        """Fix common LLM JSON mistakes like bare multi-dot numbers (4.4.2)."""
        import re
        return re.sub(
            r':\s*(\d+\.\d+\.\d[\d.]*)',
            r': "\1"',
            text,
        )

    @staticmethod
    def _parse_response(raw: str, debug: Optional[RetrievalDebug] = None) -> QAResponse:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            json_str = text[start:end]
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                data = json.loads(QAEngine._fix_json(json_str))
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("Could not parse QA JSON: %s | raw: %s", e, text[:300])
            return QAResponse(
                answer_ar=text or "غير موجود",
                citations=[],
                confidence="low",
                retrieval_debug=debug,
            )

        citations = []
        for c in data.get("citations", []):
            citations.append(
                Citation(
                    section_id=str(c.get("section_id", "")),
                    section_title=str(c.get("section_title", "")),
                    page=int(c.get("page", 0)),
                    quote=str(c.get("quote", "")),
                )
            )

        confidence = data.get("confidence", "low")
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        return QAResponse(
            answer_ar=data.get("answer_ar", "غير موجود"),
            citations=citations,
            confidence=confidence,
            retrieval_debug=debug,
        )
