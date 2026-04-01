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
- policy_qa: ANY question about company/stc HR policies, rules, regulations, or employee affairs. This covers ALL of the following topics:
  Org structure & job classification (هيكل تنظيمي، تصنيف وظائف، تقييم وظائف، وصف وظيفي)
  Recruitment & hiring (توظيف، مقابلات، عرض وظيفي، فحص طبي، عقود عمل، فترة تجربة)
  Executive appointments & dismissals (تعيين، إعفاء، تكليف وظيفي)
  Promotions & transfers (ترقية، نقل، تدوير وظيفي)
  Succession planning (تعاقب وظيفي، خطة تعاقب، بدلاء)
  Work time management (ساعات عمل، دوام مرن، عمل إضافي، وقت تعويضي، عمل عن بعد، تطوع، زيارات ميدانية، ورديات)
  All leave types (إجازة سنوية، مرضية، زواج، وفاة، وضع/أمومة، حج، امتحانات، عدة، رياضية، ثقافية، تعلم لغات، غير مدفوعة، إجازة نقل)
  Rewards, benefits & compensation (مكافآت، مزايا، تعويضات، بدل سكن، بدل نقل، بدل انتداب، مكافأة وردية، مكافأة استدعاء، علاوة)
  Salary & pay (راتب، رواتب، سلم رواتب، إدارة رواتب)
  Loans & savings (قرض سكني، قرض سيارة، ادخار مالي)
  Healthcare & insurance (تأمين طبي، رعاية صحية، علاج)
  Employee recognition & awards (تكريم، جوائز، تميز، حوافز، برنامج ولاء)
  Performance management (إدارة أداء، تقييم أداء، تقييم مستدام، أهداف أداء)
  Education & development (تعليم، تدريب، تطوير كفاءات، تطوير ذاتي، نقل معرفة، ابتعاث، إلحاق، دورات، مؤتمرات)
  Innovation (ابتكار، تمكين الابتكار)
  Childcare & disability support (رعاية أطفال، ذوي الإعاقة، دعم مالي)
  Education support for children (مساعدات تعليمية، تعليم الأبناء)
  Social solidarity & charity (تكافل اجتماعي، خيري)
  Telecom product benefits (خدمات اتصالات، منتجات الشركة)
  Secondment & employee mobility (إعارة، حركة تنقل، إركاب، شحن، استقرار)
  End of service (انتهاء خدمة، مكافأة نهاية الخدمة، استقالة، تقاعد، فسخ عقد)
  Grievances & complaints (تظلم، شكوى، شكاوي)
  Disciplinary actions (تأديب، مخالفات، جزاءات، إيقاف، سلوك مهني)
  Contractors & supplementary staff (متعاقدون، تكميليين)
  International offices (مكاتب دولية)
  Relatives & conflict of interest (أقارب، قرابة، تعارض مصالح)
  Summer/cooperative training (تدريب صيفي، تدريب تعاوني)
  When in doubt between policy_qa and general, choose policy_qa.
- workspace: Questions about the user's emails, inbox, calendar, meetings, schedule, tasks, to-do items, appointments, reminders, daily summary. Also Arabic equivalents: بريد، إيميل، رسالة، مهام، تقويم، اجتماع، موعد، ملخص، etc. IMPORTANT: Follow-up messages (e.g. "yes", "tell me more", "نعم", "أكثر", "تفاصيل", "من فضلك") that come after a workspace conversation should ALSO be classified as workspace.
- general: ONLY greetings (hi, hello, السلام عليكم), general knowledge questions completely unrelated to the company, or casual conversation.

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
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
            timeout=8.0,
        )

        import re as _re
        _raw_content = response.choices[0].message.content or ""
        _raw_content = _re.sub(r"<think>[\s\S]*?</think>\s*", "", _raw_content)
        raw = _raw_content.strip().lower()
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
    # --- General policy terms ---
    r"سياس",           # سياسة / سياسات
    r"لائح",           # لائحة / لوائح
    r"نظام\b",
    r"موارد\s*بشري",
    r"دليل\s*الموظف",
    r"موظف",           # موظفين
    r"شرو[طض]",        # شروط
    r"حق\b",           # يحق / حقوق
    r"حقوق",
    r"يستحق",
    r"ضوابط",
    r"فرق\s*بين",
    r"مقارن[ةه]",
    # --- Org structure & job classification ---
    r"هيكل\s*تنظيم",
    r"تصنيف\s*وظ",
    r"تقييم\s*وظ",
    r"وصف\s*وظيف",
    r"عائل[ةه]\s*وظيف",
    # --- Recruitment & hiring ---
    r"توظيف",
    r"مقابل[ةه]",
    r"عرض\s*وظيف",
    r"فحص\s*طب",
    r"عقد\b",          # عقد عمل
    r"عقود",
    r"تجرب[ةه]",       # فترة التجربة
    r"مرشح",
    # --- Appointments, assignments, dismissals ---
    r"تعيين",
    r"إعفاء",
    r"تكليف",
    # --- Promotions & transfers ---
    r"ترقي",           # ترقية / ترقيات
    r"نقل\s*الموظف",
    r"تنقل",
    r"تدوير\s*وظيف",
    # --- Succession planning ---
    r"تعاقب",
    r"بدلاء",
    r"خط[ةه]\s*تعاقب",
    # --- Work time management ---
    r"ساع[اة]ت?\s*عمل",
    r"عمل\s*إضاف",
    r"دوام\s*مرن",
    r"عمل\s*عن\s*بعد",
    r"وقت\s*تعويض",
    r"وردي[ةه]",       # ورديات / مكافأة وردية
    r"تطوع",
    r"زيار[اة]ت?\s*ميداني",
    # --- Leave types ---
    r"إجاز",           # إجازة / إجازات
    r"اجاز",
    r"رصيد\s*الإجاز",
    r"إجاز[ةه]\s*سنوي",
    r"إجاز[ةه]\s*مرضي",
    r"إجاز[ةه]\s*زواج",
    r"إجاز[ةه]\s*وفا[ةه]",
    r"إجاز[ةه]\s*وضع",
    r"أموم[ةه]",
    r"إجاز[ةه]\s*حج",
    r"إجاز[ةه]\s*امتحان",
    r"عد[ةه]",         # إجازة العدة
    r"إجاز[ةه]\s*نقل",
    # --- Rewards, benefits & compensation ---
    r"مكاف[أآا][ةه]",  # مكافأة
    r"مزاي",           # مزايا
    r"تعويض",
    r"بدل",            # بدلات
    r"بدل\s*سكن",
    r"بدل\s*نقل",
    r"بدل\s*انتداب",
    r"انتداب",
    r"علاو[ةه]",       # علاوة
    r"استدعاء",
    # --- Salary & pay ---
    r"رات[بي]",        # راتب / رواتب
    r"رواتب",
    r"سلم\s*رواتب",
    # --- Loans & savings ---
    r"قرض",
    r"قروض",
    r"ادخار",
    # --- Healthcare & insurance ---
    r"تأمين",          # تأمين طبي
    r"طب[يّ]",
    r"علاج",
    r"رعاي[ةه]\s*صحي",
    # --- Recognition & awards ---
    r"تكريم",
    r"جائز",           # جائزة / جوائز
    r"جوائز",
    r"تميز",
    r"حافز",           # حافز / حوافز
    r"حوافز",
    r"ولاء",           # برنامج ولاء
    # --- Performance management ---
    r"إدار[ةه]\s*أداء",
    r"تقييم\s*أداء",
    r"تقييم\s*مستدام",
    r"أهداف\s*أداء",
    r"أداء\s*الموظف",
    # --- Education & development ---
    r"تعليم",
    r"تدريب",
    r"تطوير",
    r"كفاء[اة]ت",
    r"تطوير\s*ذات",
    r"نقل\s*معرف",
    r"ابتعاث",
    r"إلحاق",
    r"دور[اة]ت",       # دورات
    r"مؤتمر",
    # --- Innovation ---
    r"ابتكار",
    # --- Childcare & disability ---
    r"رعاي[ةه]\s*أطفال",
    r"ذوي\s*الإعاق",
    r"إعاق[ةه]",
    # --- Education support for children ---
    r"مساعد[اة]ت?\s*تعليم",
    r"تعليم\s*الأبناء",
    # --- Social solidarity ---
    r"تكافل",
    # --- Telecom benefits ---
    r"خدمات\s*اتصالات",
    r"منتجات\s*الشركة",
    # --- Secondment & mobility ---
    r"إعار[ةه]",
    r"إركاب",
    r"شحن",
    r"بدل\s*استقرار",
    # --- End of service ---
    r"انتهاء\s*خدم",
    r"إنها[ءئ]\s*خدم",
    r"نهاي[ةه]\s*الخدم",
    r"استقال",
    r"تقاعد",
    r"فسخ\s*عقد",
    r"فصل",
    # --- Grievances & complaints ---
    r"تظلم",
    r"شكو[ىي]",
    r"شكاو[يى]",
    # --- Disciplinary ---
    r"تأديب",
    r"مخالف",
    r"جزاء",
    r"إيقاف",
    r"سلوك",
    # --- Contractors ---
    r"متعاقد",
    r"تعاقد",
    r"تكميل",
    # --- Travel ---
    r"سفر",
    r"تذاكر",
    r"تذكر[ةه]",
    r"درج[ةه]",
    r"درجات",
    r"ضياف[ةه]",
    r"مصاري[فف]",
    # --- Relatives & conflict of interest ---
    r"أقارب",
    r"قراب[ةه]",
    r"تعارض\s*مصالح",
    # --- Summer/cooperative training ---
    r"تدريب\s*صيف",
    r"تدريب\s*تعاون",
    # --- International offices ---
    r"مكاتب\s*دولي",
]

_POLICY_KEYWORDS_EN = [
    # --- General policy terms ---
    r"\bpolic(?:y|ies)\b",
    r"\bhr\b",
    r"\bhuman\s+resource",
    r"\bhandbook\b",
    r"\bregulat",
    r"\bemployee\b",
    r"\bguidelines?\b",
    # --- Org structure & jobs ---
    r"\borg\w*\s+structur",
    r"\bjob\s+classif",
    r"\bjob\s+(?:description|evaluation)\b",
    # --- Recruitment & hiring ---
    r"\brecruit",
    r"\bhir(?:e|ing)\b",
    r"\bcontract",
    r"\bprobation\b",
    r"\binterview\b",
    r"\bjob\s+offer\b",
    # --- Appointments ---
    r"\bappoint",
    r"\bassign",
    r"\bdismiss",
    # --- Promotions & transfers ---
    r"\bpromot",
    r"\btransfer\b",
    r"\bjob\s+rotation\b",
    # --- Succession ---
    r"\bsuccession\b",
    # --- Work time ---
    r"\bworking\s+hours?\b",
    r"\bovertime\b",
    r"\bflexible?\s+(?:hours?|schedule|work)",
    r"\bremote\s+work",
    r"\bwork\s+from\s+home\b",
    r"\bshift\b",
    r"\bvolunteer",
    # --- Leave ---
    r"\bleave\b",
    r"\bvacation\b",
    r"\bannual\s+leave\b",
    r"\bsick\s+leave\b",
    r"\bmaternit",
    r"\bpaternit",
    r"\bbereavement\b",
    r"\bmarriage\s+leave\b",
    r"\bhajj\s+leave\b",
    r"\bexam\s+leave\b",
    r"\bunpaid\s+leave\b",
    # --- Rewards & compensation ---
    r"\bbenefits?\b",
    r"\ballowan",
    r"\bsalar(?:y|ies)\b",
    r"\bcompensation\b",
    r"\brewards?\b",
    r"\bbonus\b",
    r"\bpay\s*(?:roll|scale|grade)\b",
    r"\breimburs",
    r"\bexpens",
    r"\bper\s+diem\b",
    # --- Loans & savings ---
    r"\bloan\b",
    r"\bhousing\s+loan\b",
    r"\bcar\s+loan\b",
    r"\bsavings?\b",
    # --- Healthcare & insurance ---
    r"\binsurance\b",
    r"\bmedical\b",
    r"\bhealthcare\b",
    r"\bhealth\s+care\b",
    # --- Recognition & awards ---
    r"\brecognition\b",
    r"\bawards?\b",
    r"\bhonor",
    r"\bincentiv",
    r"\bloyalty\b",
    r"\bexcellence\b",
    # --- Performance ---
    r"\bperformance\b",
    r"\bappraisal\b",
    r"\bevaluation\b",
    r"\bkpi\b",
    r"\bobjectives?\b",
    # --- Education & development ---
    r"\btraining\b",
    r"\beducation\b",
    r"\bdevelopment\b",
    r"\bscholarship\b",
    r"\bknowledge\s+transfer\b",
    r"\bconference\b",
    # --- Innovation ---
    r"\binnovation\b",
    # --- Childcare & disability ---
    r"\bchildcare\b",
    r"\bdisabilit",
    # --- End of service ---
    r"\btermina",
    r"\bresign",
    r"\bretir",
    r"\bend\s+of\s+service\b",
    r"\bgratuity\b",
    # --- Grievances ---
    r"\bgrievance\b",
    r"\bcomplaint\b",
    # --- Disciplinary ---
    r"\bdisciplin",
    r"\bcode\s+of\s+conduct\b",
    r"\bviolation\b",
    r"\bpenalt",
    r"\bsuspension\b",
    # --- Travel ---
    r"\btravel\b",
    r"\bticket",
    r"\bbusiness\s+class\b",
    r"\beconomy\s+class\b",
    r"\bhospitalit",
    # --- Secondment ---
    r"\bsecondment\b",
    r"\brelocat",
    # --- Contractors ---
    r"\bcontractor\b",
    r"\bsupplement",
    # --- Conflict of interest ---
    r"\bconflict\s+of\s+interest\b",
    r"\brelatives?\b",
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
