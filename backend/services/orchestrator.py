"""
Orchestrator Service - LLM-powered message router with keyword fallback.

Uses a small, fast LLM (e.g. Qwen2.5-3B) to classify user messages into
one of three intents: policy RAG, workspace assistant, or general chat.
Falls back to keyword regex routing if the LLM is unavailable or times out.
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Dict, Optional

from config import get_settings
from services.logger import get_llm_logger

logger = get_llm_logger()

ROUTE_POLICY_QA = "policy_qa"
ROUTE_WORKSPACE = "workspace"
ROUTE_GENERAL = "general"

_VALID_INTENTS = {ROUTE_POLICY_QA, ROUTE_WORKSPACE, ROUTE_GENERAL}


@dataclass
class RouteDecision:
    intent: str = ROUTE_GENERAL
    confidence: float = 0.5
    reasoning: str = ""


# ------------------------------------------------------------------ #
# LLM-based router
# ------------------------------------------------------------------ #

_ROUTER_SYSTEM_PROMPT = """\
You are a fast intent classifier. Given a user message (and optionally recent conversation context), reply with EXACTLY one word — the intent label. Nothing else.

Intents:
- policy_qa: Questions about company HR policies, rules, regulations, leave, salary, benefits, travel, training, contracts, employee handbook, disciplinary actions, promotions, working hours, insurance, allowances. Also Arabic equivalents: سياسة، لائحة، إجازة، راتب، ترقية، بدل، سفر، تأمين، تدريب، موظف، عقد، etc.
- workspace: Questions about the user's emails, inbox, calendar, meetings, schedule, tasks, to-do items, appointments, reminders, daily summary. Also Arabic equivalents: بريد، إيميل، رسالة، مهام، تقويم، اجتماع، موعد، ملخص، etc. IMPORTANT: Follow-up messages (e.g. "yes", "tell me more", "نعم", "أكثر", "تفاصيل", "من فضلك") that come after a workspace conversation should ALSO be classified as workspace.
- general: Greetings, general knowledge, anything not related to company policies or the user's workspace.

Reply with one word only: policy_qa, workspace, or general."""


async def _llm_route(
    message: str,
    conversation_context: Optional[str] = None,
) -> Optional[RouteDecision]:
    """Call the small router LLM. Returns None on failure."""
    settings = get_settings()

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=settings.vllm_router_url,
            api_key="not-needed",
        )

        user_content = message
        if conversation_context:
            user_content = f"[Recent conversation context]\n{conversation_context}\n\n[Current message]\n{message}"

        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.vllm_router_model,
                messages=[
                    {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=20,
                temperature=0.0,
            ),
            timeout=3.0,
        )

        raw = response.choices[0].message.content.strip().lower()
        for token in raw.replace("\n", " ").split():
            cleaned = token.strip(".,;:!?\"'`")
            if cleaned in _VALID_INTENTS:
                return RouteDecision(
                    intent=cleaned,
                    confidence=0.9,
                    reasoning=f"LLM router: '{raw}'",
                )

        logger.warning("Router LLM returned unparsable response: '%s'", raw)
        return None

    except asyncio.TimeoutError:
        logger.warning("Router LLM timed out (3s)")
        return None
    except Exception as e:
        logger.warning("Router LLM call failed: %s", e)
        return None


# ------------------------------------------------------------------ #
# Keyword / regex fallback (Arabic + English)
# ------------------------------------------------------------------ #

_POLICY_KEYWORDS_AR = [
    r"سياس",           # سياسة / سياسات
    r"لائح",           # لائحة / لوائح
    r"نظام\b",
    r"إجاز",           # إجازة / إجازات
    r"اجاز",
    r"رصيد\s*الإجاز",
    r"موارد\s*بشري",
    r"بدل",            # بدلات
    r"رات[بي]",        # راتب / رواتب
    r"ترقي",           # ترقية / ترقيات
    r"تأديب",          # تأديبي / تأديبية
    r"سلوك",           # قواعد السلوك
    r"سفر",            # سياسة السفر / تذاكر سفر
    r"تذاكر",
    r"تذكر[ةه]",
    r"درج[ةه]",        # درجة أعمال / درجة سياحية
    r"درجات",
    r"ضياف[ةه]",       # درجة الضيافة
    r"مصاري[فف]",
    r"ساع[اة]ت?\s*عمل",
    r"عمل\s*إضاف",     # ساعات عمل إضافية
    r"تجرب[ةه]",       # فترة التجربة
    r"استقال",         # استقالة
    r"فصل",
    r"إنها[ءئ]\s*خدم",
    r"تعليم",
    r"تطوير\s*[اك]",   # تطوير الكفاءات
    r"كفاء[اة]ت",
    r"تدريب",
    r"تنقل",           # حركة تنقل
    r"نقل\s*الموظف",
    r"متعاقد",         # موظفين متعاقدين
    r"تعاقد",
    r"تكميل",          # تكميليين
    r"دليل\s*الموظف",
    r"حق\b",           # يحق / حقوق
    r"حقوق",
    r"يستحق",
    r"شرو[طض]",        # شروط
    r"فرق\s*بين",      # ما الفرق بين
    r"مقارن[ةه]",
    r"موظف",           # موظفين
    r"عقد\b",          # عقد عمل
    r"عقود",
    r"مكاف[أآا][ةه]",  # مكافأة
    r"تأمين",          # تأمين طبي
    r"طب[يّ]",
    r"علاج",
]

_POLICY_KEYWORDS_EN = [
    r"\bpolic(?:y|ies)\b",
    r"\bhr\b",
    r"\bhuman\s+resource",
    r"\bleave\b",
    r"\bvacation\b",
    r"\bannual\s+leave\b",
    r"\bsick\s+leave\b",
    r"\bmaternit",
    r"\bpaternit",
    r"\bbenefits?\b",
    r"\ballowan",
    r"\bsalar(?:y|ies)\b",
    r"\bpromot",
    r"\bdisciplin",
    r"\bcode\s+of\s+conduct\b",
    r"\btravel\b",
    r"\bticket",
    r"\breimburs",
    r"\bexpens",
    r"\bworking\s+hours?\b",
    r"\bovertime\b",
    r"\bprobation\b",
    r"\btermina",
    r"\bresign",
    r"\bhandbook\b",
    r"\bregulat",
    r"\bemployee\b",
    r"\bcontract",
    r"\bgrade\b",
    r"\bbusiness\s+class\b",
    r"\beconomy\s+class\b",
    r"\bhospitalit",
    r"\binsurance\b",
    r"\bmedical\b",
    r"\btraining\b",
    r"\btransfer\b",
    r"\bsupplement",
]

_WORKSPACE_KEYWORDS_AR = [
    r"بريد",
    r"إيميل",
    r"ايميل",
    r"اميل",
    r"إميل",
    r"اميلات",
    r"إيميلات",
    r"ايميلات",
    r"رسال[ةه]",       # رسالة / رسائل
    r"رسائل",
    r"صندوق\s*الوارد",
    r"مه[اّ]م",        # مهام / مهمة
    r"مهم[ةه]",
    r"مهمات",
    r"مقروء",          # غير مقروءة / مقروعة (common typo)
    r"مقروع",
    r"غير\s*مقرو",
    r"تقويم",
    r"جدول",
    r"اجتماع",
    r"اجتماعات",
    r"موعد",
    r"مواعيد",
    r"حجز\s*قاع",      # حجز قاعة
    r"تذكير",
    r"يوم[يّ]?\s*عمل",  # يوم عملي / يومي
    r"نظر[ةه]\s*شامل",  # نظرة شاملة
    r"ملخص",
    r"الوارد",
]

_WORKSPACE_KEYWORDS_EN = [
    r"\bemail",
    r"\binbox\b",
    r"\bmail\b",
    r"\btask",
    r"\btodo\b",
    r"\bto-?do\b",
    r"\bcalendar\b",
    r"\bmeeting",
    r"\bschedule\b",
    r"\bappointment",
    r"\bagenda\b",
    r"\breminder",
    r"\bbook(?:ing)?\s+(?:room|meeting)\b",
]

_policy_re = re.compile(
    "|".join(_POLICY_KEYWORDS_AR + _POLICY_KEYWORDS_EN),
    re.IGNORECASE | re.UNICODE,
)
_workspace_re = re.compile(
    "|".join(_WORKSPACE_KEYWORDS_AR + _WORKSPACE_KEYWORDS_EN),
    re.IGNORECASE | re.UNICODE,
)


def _keyword_route(message: str) -> RouteDecision:
    """Fast keyword / regex classifier — runs in <1 ms."""
    policy_hits = _policy_re.findall(message)
    workspace_hits = _workspace_re.findall(message)

    p_count = len(policy_hits)
    w_count = len(workspace_hits)

    if p_count > 0 and p_count >= w_count:
        return RouteDecision(
            intent=ROUTE_POLICY_QA,
            confidence=min(0.7 + 0.1 * p_count, 1.0),
            reasoning=f"keyword fallback: {policy_hits[:5]}",
        )

    if w_count > 0 and w_count > p_count:
        return RouteDecision(
            intent=ROUTE_WORKSPACE,
            confidence=min(0.7 + 0.1 * w_count, 1.0),
            reasoning=f"keyword fallback: {workspace_hits[:5]}",
        )

    return RouteDecision(
        intent=ROUTE_GENERAL,
        confidence=0.8,
        reasoning="keyword fallback: no policy/workspace keywords detected",
    )


_FOLLOW_UP_RE = re.compile(
    r"^(?:نعم|أيوا|اي|ايوه|أكيد|تمام|طبعاً|من فضلك|تفاصيل|أكثر|زيادة|وش بعد|كمّل|كمل"
    r"|yes|yeah|yep|sure|ok|okay|please|go ahead|tell me more|more details|continue"
    r"|what else|anything else|show me)[\s؟?!.]*$",
    re.IGNORECASE | re.UNICODE,
)


class OrchestratorService:
    """Routes user messages to the correct handler via LLM classification
    with keyword regex fallback."""

    def __init__(self):
        self.last_intent: Optional[str] = None

    async def route(
        self,
        message: str,
        language: str = "en",
        has_policy_docs: bool = False,
        conversation_history: Optional[list] = None,
    ) -> RouteDecision:
        # Build a short context summary from recent conversation history
        context_summary = None
        if conversation_history:
            recent = conversation_history[-4:]
            parts = []
            for msg in recent:
                role = msg.get("role", "?")
                content = msg.get("content", "")[:200]
                parts.append(f"{role}: {content}")
            context_summary = "\n".join(parts)

        # Try LLM-based routing first (with conversation context)
        decision = await _llm_route(message, conversation_context=context_summary)

        # Fall back to keyword routing if LLM failed
        if decision is None:
            decision = _keyword_route(message)

        # Sticky intent: if the current message is a short follow-up and
        # the previous turn was workspace or policy_qa, inherit that intent
        if (
            decision.intent == ROUTE_GENERAL
            and self.last_intent in (ROUTE_WORKSPACE, ROUTE_POLICY_QA)
            and _FOLLOW_UP_RE.match(message.strip())
        ):
            logger.info(
                "Sticky intent override: general -> %s (follow-up detected: '%s')",
                self.last_intent, message.strip()[:40],
            )
            decision = RouteDecision(
                intent=self.last_intent,
                confidence=0.85,
                reasoning=f"sticky follow-up (prev={self.last_intent})",
            )

        # Post-routing guard: don't route to policy_qa if no docs are indexed
        if decision.intent == ROUTE_POLICY_QA and not has_policy_docs:
            logger.info(
                "Overriding %s -> general (no policy docs indexed)",
                decision.intent,
            )
            decision = RouteDecision(
                intent=ROUTE_GENERAL,
                confidence=0.7,
                reasoning=f"override: no policy docs (was {decision.reasoning})",
            )

        self.last_intent = decision.intent

        logger.info(
            "Orchestrator DECISION | intent=%s | confidence=%.2f | reason=%s",
            decision.intent, decision.confidence, decision.reasoning,
        )
        return decision


_orchestrator: Optional[OrchestratorService] = None


def get_orchestrator() -> OrchestratorService:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = OrchestratorService()
    return _orchestrator
