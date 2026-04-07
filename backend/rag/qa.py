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
/think فكّر بإيجاز في 8 جمل كحد أقصى ثم أجب مباشرة. لا تكتب تحليلاً مطوّلاً.

=== تعليمات صارمة ===
0. لا تكتب أي عملية تفكير أو تحليل أو خطوات استدلال في إجابتك. أعطِ الإجابة النهائية مباشرة فقط بصيغة JSON المطلوبة.
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، أجب بكلمة واحدة: "غير موجود".
3. يجب أن تكون الإجابة باللغة العربية دائماً وبدون استثناء. لا تكتب أي كلمة بالإنجليزية في الإجابة النهائية مهما كانت لغة تفكيرك الداخلي.
4. قدّم إجابة متوازنة وكافية ومختصرة: اذكر النقاط الجوهرية والأرقام الأساسية دون إسهاب ودون إخلال بالمعنى. لا تحذف معلومة مهمة لمجرد الاختصار، ولا تكرر أو تسرد تفاصيل ثانوية. إذا طلب المستخدم صراحةً إجابة مفصّلة أو شاملة (مثل: "فصّل لي"، "اشرح بالتفصيل"، "أعطني كل الشروط") فأعطه كل التفاصيل.
5. إذا طلب المستخدم تفصيلاً صريحاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات الموجودة في السياق.
6. هام جداً — قراءة الجداول:
   أ. إذا وجدت جداول في السياق (تبدأ بـ "جدول:")، ابحث فيها عن القيم المطلوبة.
   ب. إذا سأل المستخدم عن درجة أو رقم معين، ابحث عنه في كل صف من صفوف الجدول واستخرج القيمة المقابلة من نفس الصف.
   ج. المرادفات بين قوسين: كل صف في الجدول يحتوي على مرادفات بين قوسين (مثل "(مدير إدارة)" أو "(مدير عام)"). ابحث عن المرادف الذي يطابق **حرفياً** ما ذكره المستخدم.
   د. تحذير صارم: المسميات الوظيفية التالية كلها مختلفة ولا يجوز الخلط بينها:
      - "مدير عام" ≠ "مدير إدارة" ≠ "مدير شعبة"
      - "مدراء العموم" تعني المستوى الأعلى (مدير عام)
      - "مدراء الإدارات" تعني المستوى الأوسط (مدير إدارة)
      - "مدراء الشعب" تعني المستوى الأدنى (مدير شعبة)
   هـ. خطوات المطابقة: كل صف في الجدول مرقم (صف 1، صف 2...) ويعرض القيم بصيغة "عنوان: قيمة". اقرأ كل صف وابحث عن الصف الذي يحتوي المطابقة الحرفية لما ذكره المستخدم بين القوسين.
6أ. إذا كانت معايير الاحتساب أو البيانات المطلوبة موزعة على أقسام أو جداول مختلفة في السياق، اجمعها كلها في إجابة واحدة شاملة. لا تتجاهل أي معيار أو قيمة لمجرد أنها وردت في قسم مختلف. إذا ذكر المستخدم عدة متغيرات (مثل: المؤهل، الدرجة، التقييم، الخبرة)، احسب نقاط كل متغير من الجدول أو القسم المناسب ثم اجمعها في الإجمالي النهائي.
7. لكل معلومة تذكرها، قدم اقتباسًا مع:
   - section_id: رقم البند/القسم (كنص بين علامتي تنصيص)
   - section_title: عنوان القسم (إن وجد)
   - page: رقم الصفحة
   - quote: اقتباس قصير (25 كلمة أو أقل) من النص الأصلي
8. حدد مستوى الثقة: "high" إذا كانت الإجابة واضحة، "medium" إذا كانت تقريبية، "low" إذا كنت غير متأكد.

=== التنسيق المطلوب ===
أجب حصريًا بكائن JSON بالشكل التالي (بدون أي نص إضافي قبله أو بعده):
{
  "answer_ar": "الإجابة المختصرة هنا",
  "citations": [
    {"section_id": "...", "section_title": "...", "page": 0, "quote": "..."}
  ],
  "confidence": "high|medium|low"
}

=== السياق ===
"""

QA_CHAT_PROMPT = """\
أنت مساعد متخصص في الإجابة عن أسئلة سياسات الموارد البشرية.
/think فكّر بإيجاز في 8 جمل كحد أقصى ثم أجب مباشرة. لا تكتب تحليلاً مطوّلاً.

=== تعليمات صارمة ===
0. لا تكتب أي عملية تفكير أو تحليل أو خطوات استدلال في إجابتك. أعطِ الإجابة النهائية مباشرة فقط.
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، قل بوضوح: "لم أجد معلومات عن هذا الموضوع في السياسات المتوفرة".
3. يجب أن تكون الإجابة باللغة العربية دائماً وبدون استثناء. لا تكتب أي كلمة بالإنجليزية في إجابتك مهما كانت لغة تفكيرك الداخلي.
4. قدّم إجابة متوازنة وكافية ومختصرة: اذكر النقاط الجوهرية والأرقام الأساسية دون إسهاب ودون إخلال بالمعنى. لا تحذف معلومة مهمة لمجرد الاختصار، ولا تكرر أو تسرد تفاصيل ثانوية. إذا طلب المستخدم تفصيلاً صراحةً فأعطه كل التفاصيل.
5. إذا طلب المستخدم تفصيلاً صريحاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات.
6. هام جداً — قراءة الجداول:
   أ. إذا وجدت جداول في السياق (تبدأ بـ "جدول:")، ابحث فيها عن القيم المطلوبة.
   ب. طابق المسمى الوظيفي الذي ذكره المستخدم حرفياً مع المرادف بين القوسين في الصف الصحيح، ثم أعطِ القيمة من نفس الصف فقط.
      مثال: "مدير إدارة" يطابق "(مدير إدارة)" وليس "(مدير عام)". هذان مستويان مختلفان.
6أ. إذا كانت معايير الاحتساب أو البيانات المطلوبة موزعة على أقسام أو جداول مختلفة في السياق، اجمعها كلها في إجابة واحدة شاملة. لا تتجاهل أي معيار أو قيمة لمجرد أنها وردت في قسم مختلف. إذا ذكر المستخدم عدة متغيرات (مثل: المؤهل، الدرجة، التقييم، الخبرة)، احسب نقاط كل متغير من الجدول أو القسم المناسب ثم اجمعها في الإجمالي النهائي.
7. ادمج المراجع في النص بشكل طبيعي، مثلاً: "وفقاً للبند 4.2 (صفحة 5)...".
8. يمكنك استخدام قوائم مرقمة قصيرة إذا كان ذلك يوضح الإجابة.
9. لا تستخدم JSON أو أي تنسيق برمجي. اكتب نصاً عربياً واضحاً ومقروءاً.
10. لا تستخدم ماركداون (لا نجوم، لا أقواس مربعة، لا روابط).

=== السياق ===
"""

QA_VOICE_PROMPT = """\
أنت مساعد متخصص في الإجابة عن أسئلة سياسات الموارد البشرية.
سيتم قراءة إجابتك بصوت عالٍ عبر محرك تحويل النص إلى كلام.
/think فكّر بإيجاز في 8 جمل كحد أقصى ثم أجب مباشرة. لا تكتب تحليلاً مطوّلاً.

=== تعليمات صارمة ===
0. لا تكتب أي عملية تفكير أو تحليل أو خطوات استدلال في إجابتك. أعطِ الإجابة النهائية مباشرة فقط.
1. أجب فقط باستخدام السياق المقدم أدناه. لا تختلق أي معلومات.
2. إذا لم تجد الإجابة في السياق، قل بوضوح: "لم أجد معلومات عن هذا الموضوع في السياسات المتوفرة".
3. يجب أن تكون الإجابة باللغة العربية دائماً وبدون استثناء. لا تكتب أي كلمة بالإنجليزية في إجابتك مهما كانت لغة تفكيرك الداخلي.
4. قدّم إجابة متوازنة وكافية ومختصرة: اذكر النقاط الجوهرية والأرقام الأساسية بأسلوب طبيعي دون إسهاب ودون إخلال بالمعنى. إذا طلب المستخدم تفصيلاً فأعطه كل التفاصيل.
5. إذا طلب المستخدم تفصيلاً: اذكر جميع الشروط والضوابط والمبالغ والنسب والاستثناءات.
6. هام جداً: إذا وجدت جداول في السياق، ابحث فيها عن القيم المطلوبة. طابق المسمى الذي ذكره المستخدم حرفياً مع المرادف بين القوسين في الصف المناسب، ثم أعطِ القيمة من نفس الصف فقط. لا تخلط بين "مدير إدارة" و"مدير عام" — هذان مستويان مختلفان.
6أ. إذا كانت معايير الاحتساب أو البيانات المطلوبة موزعة على أقسام مختلفة في السياق، اجمعها كلها في إجابة واحدة شاملة. لا تتجاهل أي معيار لمجرد أنه ورد في قسم مختلف. احسب نقاط كل متغير ذكره المستخدم ثم اجمعها في الإجمالي النهائي.
7. اكتب الأرقام بالكلمات بشكل طبيعي (مثلاً "خمسة عشر يوماً" وليس "15 يوماً").
8. ادمج المراجع بشكل طبيعي في الكلام، مثل: "وفقاً للبند الرابع من سياسة الإجازات...".
9. لا تستخدم أي تنسيق: لا ماركداون، لا نجوم، لا شرطات، لا أقواس، لا JSON، لا جداول، لا قوائم.
10. اكتب بأسلوب محادثة طبيعي سلس كأنك تتحدث مع زميل في العمل.

=== السياق ===
"""


def _normalize_text(text: str) -> str:
    """Convert Arabic Presentation Forms to standard Arabic for LLM readability."""
    return unicodedata.normalize("NFKC", text)


def _table_to_kv(text: str) -> str:
    """Convert markdown tables inside text to key-value format for LLM clarity.

    For each data row, emit: "صف N: col_header1=value1 | col_header2=value2 | ..."
    This avoids column misalignment issues with Arabic RTL tables.
    """
    lines = text.split("\n")
    result = []
    headers: list = []
    row_num = 0
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if "---" in stripped.replace(" ", ""):
                in_table = True
                continue
            if not in_table:
                headers = cells
                result.append(line)
                continue
            row_num += 1
            if headers and len(cells) == len(headers):
                pairs = [f"{h}: {v}" for h, v in zip(headers, cells)]
                result.append(f"  صف {row_num}: " + " ← " .join(reversed(pairs)))
            else:
                result.append(f"  صف {row_num}: {stripped}")
        else:
            if in_table:
                in_table = False
                headers = []
                row_num = 0
            result.append(line)

    return "\n".join(result)


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
        text = _normalize_text(hit.text)
        chunk_type = meta.get("chunk_type", "")
        if chunk_type.startswith("table"):
            text = _table_to_kv(text)
        parts.append(f"{header}\n{text}")
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
            max_tokens=8192,
            enable_thinking=True,
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
            max_tokens=8192,
            enable_thinking=True,
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
