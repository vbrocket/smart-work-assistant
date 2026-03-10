"""
Orchestrator Service - Fast keyword-based message router.

Receives every user message and classifies it into one of three intents
(policy RAG, workspace assistant, or general chat) using pattern matching.
Falls back to LLM routing only when the keyword classifier is uncertain.
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional

from services.logger import get_llm_logger

logger = get_llm_logger()

ROUTE_POLICY_QA = "policy_qa"
ROUTE_WORKSPACE = "workspace"
ROUTE_GENERAL = "general"


@dataclass
class RouteDecision:
    intent: str = ROUTE_GENERAL
    confidence: float = 0.5
    reasoning: str = ""


# ------------------------------------------------------------------ #
# Keyword / regex patterns (Arabic + English)
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


def _keyword_route(
    message: str, available_context: Dict[str, bool]
) -> RouteDecision:
    """Fast keyword / regex classifier — runs in <1 ms."""
    policy_hits = _policy_re.findall(message)
    workspace_hits = _workspace_re.findall(message)

    has_policy = available_context.get("has_policy_docs", False)
    has_workspace = (
        available_context.get("has_emails")
        or available_context.get("has_tasks")
        or available_context.get("has_calendar")
    )

    p_count = len(policy_hits)
    w_count = len(workspace_hits)

    if p_count > 0 and has_policy and p_count >= w_count:
        return RouteDecision(
            intent=ROUTE_POLICY_QA,
            confidence=min(0.7 + 0.1 * p_count, 1.0),
            reasoning=f"keyword hits: {policy_hits[:5]}",
        )

    if w_count > 0 and has_workspace and w_count > p_count:
        return RouteDecision(
            intent=ROUTE_WORKSPACE,
            confidence=min(0.7 + 0.1 * w_count, 1.0),
            reasoning=f"keyword hits: {workspace_hits[:5]}",
        )

    if p_count > 0 and has_policy:
        return RouteDecision(
            intent=ROUTE_POLICY_QA,
            confidence=0.6,
            reasoning=f"weak policy match: {policy_hits[:3]}",
        )

    if w_count > 0 and has_workspace:
        return RouteDecision(
            intent=ROUTE_WORKSPACE,
            confidence=0.6,
            reasoning=f"weak workspace match: {workspace_hits[:3]}",
        )

    return RouteDecision(
        intent=ROUTE_GENERAL,
        confidence=0.8,
        reasoning="no policy/workspace keywords detected",
    )


class OrchestratorService:
    """Routes user messages to the correct handler via keyword matching."""

    async def route(
        self,
        message: str,
        language: str = "en",
        available_context: Optional[Dict[str, bool]] = None,
    ) -> RouteDecision:
        ctx = available_context or {}
        decision = _keyword_route(message, ctx)
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
