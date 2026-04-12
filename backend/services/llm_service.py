"""
LLM Service - Bilingual (Arabic/English) conversation with smart prompts.

Delegates all model calls to an LLMProvider (Ollama, HuggingFace, or OpenRouter)
selected by the LLM_BACKEND env variable.
"""
import json
import re
from typing import AsyncIterator, Optional, List, Dict, Any
from datetime import datetime, date as date_type, timedelta

from config import get_settings
from services.llm_provider import create_llm_provider, LLMProvider
from services.logger import get_llm_logger

settings = get_settings()
logger = get_llm_logger()


def _log_messages(tag: str, messages: list):
    """Log the full messages array sent to the LLM (DEBUG level -> log file only)."""
    separator = "=" * 60
    parts = [f"\n{separator}", f"LLM PROMPT [{tag}]  ({len(messages)} messages)", separator]
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        parts.append(f"--- [{i}] {role} ({len(content)} chars) ---")
        parts.append(content)
    parts.append(separator)
    logger.debug("\n".join(parts))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_AR_MONTHS = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
    5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}

_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _parse_dt(value) -> Optional[datetime]:
    """Best-effort parse of a datetime value (str, datetime, or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _format_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "?"
    return dt.strftime("%I:%M %p").lstrip("0")


def _format_datetime_en(value) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return "Unknown"
    today = date_type.today()
    if dt.date() == today:
        return f"Today {_format_time(dt)}"
    if dt.date() == today - timedelta(days=1):
        return f"Yesterday {_format_time(dt)}"
    return f"{dt.strftime('%b %d')}, {_format_time(dt)}"


def _format_datetime_ar(value) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return "غير معروف"
    today = date_type.today()
    t = _format_time(dt)
    if dt.date() == today:
        return f"اليوم {t}"
    if dt.date() == today - timedelta(days=1):
        return f"أمس {t}"
    month = _AR_MONTHS.get(dt.month, str(dt.month))
    return f"{dt.day} {month}, {t}"


def _clean_preview(text: str, max_len: int = 150) -> str:
    """Strip HTML, collapse whitespace, truncate at a word boundary."""
    if not text:
        return ""
    t = re.sub(r'<[^>]+>', '', text)
    t = re.sub(r'[\r\n\t]+', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    if len(t) > max_len:
        t = t[:max_len].rsplit(' ', 1)[0] + '...'
    return t


class LLMService:
    """Service for LLM interactions via a pluggable provider."""
    
    SYSTEM_PROMPTS = {
        'en': """You are a Smart Work Assistant. You help the user manage their workday by answering questions about their emails, calendar, and tasks using ONLY the data provided below.

Rules:
- Be direct and concise. Lead with what matters most.
- Never invent, fabricate, or hallucinate any data.
- Use specific names, times, and subjects from the data.
- Match the user's language.

Today: {date}""",

        'ar': """أنت مساعد العمل الذكي. تساعد المستخدم في إدارة يوم عمله بالإجابة على أسئلته حول البريد الإلكتروني والتقويم والمهام باستخدام البيانات المقدمة أدناه فقط.

القواعد:
- كن مباشراً وموجزاً. ابدأ بالأهم.
- ممنوع اختراع أو تخيل أي بيانات.
- استخدم الأسماء والأوقات والمواضيع الفعلية من البيانات.
- طابق لغة المستخدم.

اليوم: {date}"""
    }

    GENERAL_SYSTEM_PROMPTS = {
        'en': """You are a friendly and helpful AI assistant. You can chat about any topic, answer general knowledge questions, and have natural conversations.

Rules:
- Be direct, concise, and helpful.
- Match the user's language.
- You do NOT have access to the user's emails, calendar, or tasks right now. If they ask about those, tell them you can help with that — just ask directly (e.g. "show me my emails" or "what meetings do I have today").
- NEVER invent, fabricate, or hallucinate any emails, meetings, tasks, or workspace data.

Today: {date}""",

        'ar': """أنت مساعد ذكي ودود. يمكنك الدردشة في أي موضوع والإجابة على الأسئلة العامة وإجراء محادثات طبيعية.

القواعد:
- كن مباشراً وموجزاً ومفيداً.
- طابق لغة المستخدم.
- ليس لديك حالياً وصول لبريد المستخدم أو تقويمه أو مهامه. إذا سأل عنها، أخبره أنك تستطيع المساعدة — فقط يسأل مباشرة (مثلاً "وش عندي اليوم" أو "ورني ايميلاتي").
- ممنوع منعاً باتاً اختراع أو تخيل أي بريد أو اجتماعات أو مهام أو بيانات عمل.

اليوم: {date}"""
    }

    VOICE_ADDON = {
        'en': """

Voice mode – answer will be read aloud. Rules:
- Two to four sentences MAX. Answer only what was asked.
- No markdown, no lists, no JSON, no code, no extra details.
- Spell out numbers naturally.
- Natural spoken tone.""",

        'ar': """

وضع صوتي – ستُقرأ إجابتك بصوت عالٍ. القواعد:
- جملتان إلى أربع جمل كحد أقصى. أجب فقط عما سُئلت عنه بدون مقدمات أو تفاصيل إضافية.
- لا تنسيق، لا قوائم، لا JSON، لا أكواد.
- اكتب الأرقام بالكلمات.
- أسلوب كلام طبيعي سلس."""
    }
    
    # Prompt templates for specific tasks
    TASK_PROMPTS = {
        'summarize_email': {
            'en': """Summarize the following email. Provide:
1. A brief summary (2-3 sentences)
2. Key points (bullet list)
3. Sentiment (positive/negative/neutral)
4. Urgency level (low/medium/high)

Email:
From: {sender}
Subject: {subject}
Body:
{body}

Respond in JSON format:
{{"summary": "...", "key_points": ["..."], "sentiment": "...", "urgency": "..."}}""",
            
            'ar': """لخص البريد الإلكتروني التالي. قدم:
1. ملخص موجز (2-3 جمل)
2. النقاط الرئيسية (قائمة نقطية)
3. المشاعر (إيجابي/سلبي/محايد)
4. مستوى الإلحاح (منخفض/متوسط/عالي)

البريد الإلكتروني:
من: {sender}
الموضوع: {subject}
النص:
{body}

أجب بتنسيق JSON:
{{"summary": "...", "key_points": ["..."], "sentiment": "...", "urgency": "..."}}"""
        },
        
        'extract_tasks': {
            'en': """Extract actionable tasks from the following email.
For each task, provide:
- title: A clear, concise task title
- description: Brief description of what needs to be done
- priority: low/medium/high/urgent
- due_date: If mentioned (ISO format) or null

Email:
From: {sender}
Subject: {subject}
Body:
{body}

Respond in JSON format:
{{"tasks": [{{"title": "...", "description": "...", "priority": "...", "due_date": null}}]}}

If no actionable tasks found, return: {{"tasks": []}}""",

            'ar': """استخرج المهام القابلة للتنفيذ من البريد الإلكتروني التالي.
لكل مهمة، قدم:
- title: عنوان مهمة واضح وموجز
- description: وصف موجز لما يجب القيام به
- priority: منخفض/متوسط/عالي/عاجل
- due_date: إذا ذُكر (تنسيق ISO) أو null

البريد الإلكتروني:
من: {sender}
الموضوع: {subject}
النص:
{body}

أجب بتنسيق JSON:
{{"tasks": [{{"title": "...", "description": "...", "priority": "...", "due_date": null}}]}}

إذا لم توجد مهام قابلة للتنفيذ، أعد: {{"tasks": []}}"""
        },
        
        'draft_reply': {
            'en': """Draft a reply to the following email with a {tone} tone.

Original email:
From: {sender}
Subject: {subject}
Body:
{body}

{context}

Write a professional reply that:
- Addresses the main points
- Maintains a {tone} tone
- Is concise but complete

Provide only the reply text, no JSON formatting.""",

            'ar': """اكتب رداً على البريد الإلكتروني التالي بنبرة {tone}.

البريد الأصلي:
من: {sender}
الموضوع: {subject}
النص:
{body}

{context}

اكتب رداً مهنياً:
- يعالج النقاط الرئيسية
- يحافظ على نبرة {tone}
- موجز ولكن كامل

قدم نص الرد فقط، بدون تنسيق JSON."""
        },
        
        'daily_summary': {
            'en': """Summarize the user's workday based on the data below.
Structure: start with today's meetings (by time), then urgent/unread emails, then pending tasks.
Be concise (3-5 sentences). Use specific names, times, and subjects.

{context}""",

            'ar': """لخّص يوم عمل المستخدم بناءً على البيانات أدناه.
الترتيب: ابدأ باجتماعات اليوم (حسب الوقت)، ثم البريد العاجل/غير المقروء، ثم المهام المعلقة.
كن موجزاً (٣-٥ جمل). استخدم الأسماء والأوقات والمواضيع الفعلية.

{context}"""
        }
    }
    
    def __init__(self, provider: Optional[LLMProvider] = None):
        self.provider: LLMProvider = provider or create_llm_provider()
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history = 10
        self.employee_profile: Dict[str, str] = {}
    
    def _get_system_prompt(self, language: str, voice_mode: bool = False, general: bool = False) -> str:
        """Get the system prompt for the given language, with optional voice addon."""
        lang_key = language if language in self.SYSTEM_PROMPTS else 'en'
        prompts = self.GENERAL_SYSTEM_PROMPTS if general else self.SYSTEM_PROMPTS
        prompt = prompts[lang_key].format(date=datetime.now().strftime("%Y-%m-%d"))
        if voice_mode:
            prompt += self.VOICE_ADDON.get(lang_key, self.VOICE_ADDON['en'])
        return prompt
    
    async def chat(
        self,
        message: str,
        language: str = "en",
        include_history: bool = True,
        voice_mode: bool = False,
    ) -> str:
        """
        Send a chat message and get a response.
        
        Args:
            message: User's message
            language: Language code ('en' or 'ar')
            include_history: Whether to include conversation history
            voice_mode: When True, instruct LLM to return voice-friendly prose
        
        Returns:
            Assistant's response
        """
        logger.info(f"Chat request | language={language} | message_length={len(message)} | history={include_history} | voice={voice_mode}")
        
        messages = [
            {"role": "system", "content": self._get_system_prompt(language, voice_mode, general=True)}
        ]
        
        if include_history:
            messages.extend(self.conversation_history[-self.max_history:])
        
        messages.append({"role": "user", "content": message})
        _log_messages("chat", messages)
        
        try:
            response_text = await self.provider.chat(
                messages, temperature=0.7, top_p=0.9, enable_thinking=False,
            )
            logger.info(f"Chat response received | response_length={len(response_text)}")
            
            # Update conversation history
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            
            # Trim history if needed
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]
            
            return response_text

        except Exception as e:
            logger.error(f"Chat failed | error_type={type(e).__name__} | error={str(e)}", exc_info=True)
            raise RuntimeError(f"LLM request failed: {str(e)}")
    
    async def chat_stream(
        self,
        message: str,
        language: str = "en",
        include_history: bool = True,
        voice_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Stream tokens for a chat message."""
        messages = [
            {"role": "system", "content": self._get_system_prompt(language, voice_mode, general=True)}
        ]
        if include_history:
            messages.extend(self.conversation_history[-self.max_history:])
        messages.append({"role": "user", "content": message})
        _log_messages("chat_stream", messages)

        full = ""
        async for token in self.provider.chat_stream(messages, temperature=0.7, top_p=0.9, enable_thinking=False):
            full += token
            yield token

        self.conversation_history.append({"role": "user", "content": message})
        self.conversation_history.append({"role": "assistant", "content": full})
        if len(self.conversation_history) > self.max_history * 2:
            self.conversation_history = self.conversation_history[-self.max_history * 2:]

    # ----- shared prompt builder for contextual chat -----------------------

    def _build_contextual_system_prompt(
        self,
        emails: List[Dict],
        tasks: List[Dict],
        events: List[Dict],
        language: str,
        voice_mode: bool,
    ) -> str:
        voice_addon = ""
        if voice_mode:
            lang_key = "ar" if language == "ar" else "en"
            voice_addon = self.VOICE_ADDON.get(lang_key, self.VOICE_ADDON['en'])

        has_any_data = bool(emails) or bool(tasks) or bool(events)
        date_str = datetime.now().strftime("%Y-%m-%d")

        if language == "ar":
            context = self._build_arabic_context(emails, tasks, events)
            if has_any_data:
                data_block = f"""بيانات المستخدم الحالية:

{context}

تعليمات:
- أجب باستخدام البيانات أعلاه فقط. لا تخترع بيانات.
- ابدأ بالأكثر إلحاحاً (اجتماعات قريبة، مهام عاجلة).
- لأسئلة الملخص: ابدأ بجدول اليوم، ثم البريد غير المقروء/العاجل، ثم المهام المعلقة.
- استخدم الأسماء والأوقات والمواضيع الفعلية من البيانات.
- إذا سأل المستخدم عن شيء غير موجود في البيانات، قل ذلك بوضوح."""
            else:
                data_block = """لا تتوفر حالياً أي بيانات (لا بريد، لا مهام، لا اجتماعات).
يبدو أن حساب Outlook غير متصل أو أن الجلسة انتهت.
أخبر المستخدم بذلك واطلب منه إعادة الاتصال.
ممنوع اختراع أو تخيل أي بيانات."""
            base = self.SYSTEM_PROMPTS['ar'].format(date=date_str)
        else:
            context = self._build_english_context(emails, tasks, events)
            if has_any_data:
                data_block = f"""USER DATA:

{context}

INSTRUCTIONS:
- Answer using ONLY the data above. Never invent data.
- Lead with the most time-sensitive items (upcoming meetings, urgent tasks).
- For workload/summary questions: start with today's schedule, then highlight unread/urgent emails, then pending tasks.
- Use specific names, times, and subjects from the data.
- If asked about something not in the data, say so clearly."""
            else:
                data_block = """NO DATA AVAILABLE (no emails, no tasks, no meetings).
The Outlook account is not connected or the session has expired.
Tell the user and ask them to reconnect their Outlook account.
Do NOT invent, fabricate, or hallucinate any data."""
            base = self.SYSTEM_PROMPTS['en'].format(date=date_str)

        return f"{base}{voice_addon}\n\n{data_block}"

    async def contextual_chat_stream(
        self,
        message: str,
        emails: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        events: List[Dict[str, Any]] = None,
        language: str = "en",
        voice_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Stream tokens for a contextual chat message."""
        events = events or []
        system_prompt = self._build_contextual_system_prompt(
            emails, tasks, events, language, voice_mode,
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.conversation_history[-4:])
        messages.append({"role": "user", "content": message})
        _log_messages("contextual_chat_stream", messages)

        full = ""
        async for token in self.provider.chat_stream(messages, temperature=0.7, top_p=0.9, enable_thinking=False):
            full += token
            yield token

        self.conversation_history.append({"role": "user", "content": message})
        self.conversation_history.append({"role": "assistant", "content": full})
        if len(self.conversation_history) > self.max_history * 2:
            self.conversation_history = self.conversation_history[-self.max_history * 2:]

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7
    ) -> str:
        """
        Generate a response for a single prompt (no conversation history).
        
        Args:
            prompt: The complete prompt
            temperature: Sampling temperature
        
        Returns:
            Generated text
        """
        try:
            return await self.provider.generate(prompt, temperature=temperature)
        except Exception as e:
            raise RuntimeError(f"LLM generation failed: {str(e)}")
    
    async def summarize_email(
        self,
        sender: str,
        subject: str,
        body: str,
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Summarize an email using LLM.
        
        Returns:
            Dict with summary, key_points, sentiment, urgency
        """
        prompt_template = self.TASK_PROMPTS['summarize_email'][language]
        prompt = prompt_template.format(
            sender=sender,
            subject=subject,
            body=body
        )
        
        response = await self.generate(prompt, temperature=0.3)
        
        # Parse JSON response
        try:
            # Find JSON in response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
        
        # Fallback if JSON parsing fails
        return {
            "summary": response,
            "key_points": [],
            "sentiment": "neutral",
            "urgency": "medium"
        }
    
    async def extract_tasks(
        self,
        sender: str,
        subject: str,
        body: str,
        language: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Extract actionable tasks from an email.
        
        Returns:
            List of task dictionaries
        """
        prompt_template = self.TASK_PROMPTS['extract_tasks'][language]
        prompt = prompt_template.format(
            sender=sender,
            subject=subject,
            body=body
        )
        
        response = await self.generate(prompt, temperature=0.3)
        
        # Parse JSON response
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return data.get("tasks", [])
        except json.JSONDecodeError:
            pass
        
        return []
    
    async def draft_reply(
        self,
        sender: str,
        subject: str,
        body: str,
        tone: str = "formal",
        additional_context: Optional[str] = None,
        language: str = "en"
    ) -> str:
        """
        Draft an email reply.
        
        Args:
            tone: 'formal', 'friendly', or 'brief'
            additional_context: Extra context for the reply
        
        Returns:
            Draft reply text
        """
        prompt_template = self.TASK_PROMPTS['draft_reply'][language]
        
        context = ""
        if additional_context:
            context = f"Additional context: {additional_context}"
        
        tone_translations = {
            'formal': 'رسمية' if language == 'ar' else 'formal',
            'friendly': 'ودية' if language == 'ar' else 'friendly',
            'brief': 'موجزة' if language == 'ar' else 'brief'
        }
        
        prompt = prompt_template.format(
            sender=sender,
            subject=subject,
            body=body,
            tone=tone_translations.get(tone, tone),
            context=context
        )
        
        return await self.generate(prompt, temperature=0.7)
    
    async def generate_daily_summary(
        self,
        tasks: List[Dict],
        emails: List[Dict],
        events: List[Dict] = None,
        language: str = "en",
    ) -> str:
        """Generate a daily summary using the compact context format."""
        events = events or []
        if language == "ar":
            context = self._build_arabic_context(emails, tasks, events)
        else:
            context = self._build_english_context(emails, tasks, events)

        prompt_template = self.TASK_PROMPTS['daily_summary'][language]
        prompt = prompt_template.format(context=context)
        return await self.generate(prompt, temperature=0.7)
    
    def detect_language(self, text: str) -> str:
        """Detect if text is Arabic or English."""
        arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        total_alpha = sum(1 for c in text if c.isalpha())
        
        if total_alpha == 0:
            return 'en'
        
        return 'ar' if arabic_chars / total_alpha > 0.3 else 'en'
    
    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
    
    async def contextual_chat(
        self,
        message: str,
        emails: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        events: List[Dict[str, Any]] = None,
        language: str = "en",
        voice_mode: bool = False,
    ) -> str:
        """Chat with context about user's emails, tasks, and calendar events."""
        events = events or []
        logger.info(f"Contextual chat | language={language} | emails={len(emails)} | tasks={len(tasks)} | events={len(events)} | voice={voice_mode}")

        system_prompt = self._build_contextual_system_prompt(
            emails, tasks, events, language, voice_mode,
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.conversation_history[-4:])
        messages.append({"role": "user", "content": message})
        _log_messages("contextual_chat", messages)

        try:
            response_text = await self.provider.chat(
                messages, temperature=0.7, top_p=0.9, enable_thinking=False,
            )
            logger.info(f"Contextual chat response | length={len(response_text)}")

            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]

            return response_text

        except Exception as e:
            logger.error(f"Contextual chat failed | error={str(e)}", exc_info=True)
            raise RuntimeError(f"LLM request failed: {str(e)}")
    
    # ------------------------------------------------------------------
    # Policy / RAG methods
    # ------------------------------------------------------------------

    async def extract_needed_employee_info(
        self,
        policy_chunks: List[Dict[str, Any]],
        message: str,
        language: str = "en",
    ) -> Optional[List[str]]:
        """Given retrieved policy chunks, determine what employee info is needed to answer accurately.

        Returns a list of missing field names (e.g. ["job_title", "grade"]) or None if
        no additional info is required.
        """
        chunks_text = "\n---\n".join(c["text"] for c in policy_chunks[:5])

        known = json.dumps(self.employee_profile) if self.employee_profile else "None"

        prompt = (
            "You are an assistant helping answer a company policy question.\n"
            "Below are relevant policy excerpts and the employee's question.\n\n"
            f"=== POLICY EXCERPTS ===\n{chunks_text}\n\n"
            f"=== EMPLOYEE QUESTION ===\n{message}\n\n"
            f"=== KNOWN EMPLOYEE INFO ===\n{known}\n\n"
            "Based on the policy excerpts, does the answer depend on the employee's "
            "job title, rank/grade, department, or any other personal attribute that "
            "we do NOT already know?\n\n"
            "If YES, respond ONLY with a JSON list of the missing fields needed, e.g. "
            '[\"job_title\", \"grade\"].\n'
            "If NO (we have enough info or the policy applies universally), respond with "
            "the word NONE."
        )

        try:
            response = await self.generate(prompt, temperature=0.0)
            cleaned = response.strip()
            if cleaned.upper().startswith("NONE"):
                return None
            start = cleaned.find("[")
            end = cleaned.rfind("]") + 1
            if start >= 0 and end > start:
                fields = json.loads(cleaned[start:end])
                if isinstance(fields, list) and fields:
                    return fields
            return None
        except Exception as e:
            logger.error(f"extract_needed_employee_info failed: {e}")
            return None

    def build_employee_info_question(
        self, needed_fields: List[str], language: str = "en"
    ) -> str:
        """Build a natural-language question asking the employee for missing profile info."""
        field_labels = {
            "en": {
                "job_title": "job title",
                "grade": "grade/rank level",
                "department": "department",
                "rank": "rank",
                "years_of_service": "years of service",
                "employment_type": "employment type (full-time/part-time/contract)",
            },
            "ar": {
                "job_title": "المسمى الوظيفي",
                "grade": "الدرجة/المرتبة",
                "department": "القسم",
                "rank": "الرتبة",
                "years_of_service": "سنوات الخدمة",
                "employment_type": "نوع التوظيف (دوام كامل/جزئي/عقد)",
            },
        }
        labels = field_labels.get(language, field_labels["en"])
        items = [labels.get(f, f) for f in needed_fields]

        if language == "ar":
            joined = "، ".join(items)
            return (
                f"للإجابة بدقة على سؤالك حول السياسة، أحتاج لمعرفة: {joined}. "
                "هل يمكنك تزويدي بهذه المعلومات؟"
            )
        joined = ", ".join(items)
        return (
            f"To answer your policy question accurately, I need to know your: {joined}. "
            "Could you please share this information?"
        )

    def update_employee_profile(self, info: Dict[str, str]) -> None:
        """Merge new employee info into the cached profile."""
        for k, v in info.items():
            if v and str(v).strip():
                self.employee_profile[k] = str(v).strip()
        logger.debug(f"Employee profile updated | keys={list(self.employee_profile.keys())}")

    _PROFILE_HINT_RE = None

    async def try_extract_profile_from_message(self, message: str) -> Dict[str, str]:
        """Attempt to extract employee profile fields from a free-text message.

        Only invokes the LLM when the message contains profile-related keywords
        to avoid wasting ~5 s on every policy question.
        """
        import re as _re
        if self._PROFILE_HINT_RE is None:
            type(self)._PROFILE_HINT_RE = _re.compile(
                r"درج[ةه]|رتب[ةه]|مسمى|وظيف|قسم|إدار[ةه]|سنوات|خبر|تعاقد"
                r"|grade|rank|title|department|years|experience|position|role",
                _re.IGNORECASE | _re.UNICODE,
            )
        if not self._PROFILE_HINT_RE.search(message):
            return {}

        prompt = (
            "The user was asked to provide their employee information. "
            "Extract any of these fields from their response: "
            "job_title, grade, department, rank, years_of_service, employment_type.\n\n"
            f"User response: \"{message}\"\n\n"
            "Return ONLY a JSON object with the extracted fields. "
            "Only include fields that are clearly stated. "
            "Example: {{\"job_title\": \"Senior Engineer\", \"grade\": \"7\"}}\n"
            "If nothing can be extracted, return: {{}}"
        )
        try:
            response = await self.generate(prompt, temperature=0.0)
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception as e:
            logger.error(f"Profile extraction failed: {e}")
        return {}

    async def policy_chat(
        self,
        message: str,
        policy_chunks: List[Dict[str, Any]],
        language: str = "en",
    ) -> str:
        """Generate an answer to a policy question using retrieved chunks and employee profile.

        This method is kept for backward compatibility.  The primary path now uses
        the grounded QA engine in backend/rag/qa.py via RAGService.answer().
        """
        chunks_text = "\n---\n".join(
            f"[section_id={c.get('section_id','?')} page={c.get('page',0)}]\n{c['text']}"
            for c in policy_chunks
        )

        profile_text = (
            json.dumps(self.employee_profile, ensure_ascii=False)
            if self.employee_profile
            else "Not provided"
        )

        if language == "ar":
            system_prompt = (
                f"{self.SYSTEM_PROMPTS['ar'].format(date=datetime.now().strftime('%Y-%m-%d'))}\n\n"
                "أنت الآن تجيب على سؤال يتعلق بسياسة الشركة.\n"
                "استخدم المقاطع التالية من وثائق السياسة للإجابة بدقة.\n"
                "إذا كانت السياسة تختلف حسب الرتبة أو المسمى الوظيفي، استخدم معلومات الموظف أدناه.\n"
                "لا تختلق معلومات غير موجودة في المقاطع. إذا لم تجد الإجابة، قل ذلك بوضوح.\n"
                "عند الإجابة، اذكر رقم البند والصفحة كمرجع.\n\n"
                f"=== مقاطع السياسة ===\n{chunks_text}\n\n"
                f"=== معلومات الموظف ===\n{profile_text}"
            )
        else:
            system_prompt = (
                f"{self.SYSTEM_PROMPTS['en'].format(date=datetime.now().strftime('%Y-%m-%d'))}\n\n"
                "You are now answering a question about company policy.\n"
                "Use the following policy document excerpts to answer accurately.\n"
                "If the policy varies by rank or job title, use the employee info below.\n"
                "Do NOT invent information not present in the excerpts. "
                "If you cannot find the answer, say so clearly.\n"
                "Cite the section_id and page number for each piece of information.\n\n"
                f"=== POLICY EXCERPTS ===\n{chunks_text}\n\n"
                f"=== EMPLOYEE INFO ===\n{profile_text}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
        ]
        messages.extend(self.conversation_history[-4:])
        messages.append({"role": "user", "content": message})
        _log_messages("policy_chat", messages)

        try:
            response_text = await self.provider.chat(
                messages, temperature=0.3, top_p=0.9,
            )
            logger.info(f"Policy chat response | length={len(response_text)}")

            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]

            return response_text

        except Exception as e:
            logger.error(f"Policy chat failed | error={str(e)}", exc_info=True)
            raise RuntimeError(f"LLM request failed: {str(e)}")

    # ----- context builders ------------------------------------------------

    @staticmethod
    def _sort_events(events: List[Dict]) -> List[Dict]:
        return sorted(events, key=lambda e: _parse_dt(e.get('start_time')) or datetime.max)

    @staticmethod
    def _sort_emails(emails: List[Dict]) -> List[Dict]:
        def _key(e):
            read = 0 if not e.get('is_read') else 1
            dt = _parse_dt(e.get('received_at')) or datetime.min
            return (read, -dt.timestamp())
        return sorted(emails, key=_key)

    @staticmethod
    def _sort_tasks(tasks: List[Dict]) -> List[Dict]:
        return sorted(tasks, key=lambda t: _PRIORITY_ORDER.get(t.get('priority', 'medium'), 2))

    def _build_english_context(self, emails: List[Dict], tasks: List[Dict], events: List[Dict] = None) -> str:
        events = self._sort_events(events or [])
        emails = self._sort_emails(emails or [])
        tasks = self._sort_tasks(tasks or [])

        n_unread = sum(1 for e in emails if not e.get('is_read'))
        n_urgent = sum(1 for t in tasks if t.get('priority') in ('urgent', 'high'))
        header = (
            f"OVERVIEW: {len(events)} meeting(s) today | "
            f"{len(emails)} email(s) ({n_unread} unread) | "
            f"{len(tasks)} task(s) ({n_urgent} urgent/high)"
        )

        lines = [header, ""]

        # Calendar
        lines.append("CALENDAR (today, by time):")
        if events:
            for i, ev in enumerate(events[:20], 1):
                start = _format_time(_parse_dt(ev.get('start_time')))
                end = _format_time(_parse_dt(ev.get('end_time')))
                loc = ev.get('location') or ("Online" if ev.get('is_online') else "")
                org = ev.get('organizer', '')
                subj = ev.get('subject', 'No subject')
                parts = [f"{start}-{end}", subj]
                if loc:
                    parts.append(loc)
                if org:
                    parts.append(f"by {org}")
                lines.append(f"{i}. {' | '.join(parts)}")
        else:
            lines.append("  No meetings today.")

        # Emails
        lines.append("")
        lines.append("EMAILS (newest first, unread on top):")
        if emails:
            for i, em in enumerate(emails[:12], 1):
                tag = "[UNREAD] " if not em.get('is_read') else ""
                sender = em.get('sender_name', 'Unknown')
                subj = em.get('subject', 'No subject')
                dt_str = _format_datetime_en(em.get('received_at'))
                preview = _clean_preview(em.get('body_preview', ''))
                line = f'{i}. {tag}{sender} — "{subj}" — {dt_str}'
                if preview:
                    line += f" — {preview}"
                lines.append(line)
        else:
            lines.append("  No emails.")

        # Tasks
        lines.append("")
        lines.append("TASKS (by priority):")
        if tasks:
            for i, t in enumerate(tasks[:15], 1):
                pri = (t.get('priority') or 'medium').upper()
                title = t.get('title', 'Untitled')
                due = t.get('due_date')
                due_str = f"due {_format_datetime_en(due)}" if due else "no due date"
                lines.append(f"{i}. [{pri}] {title} — {due_str}")
        else:
            lines.append("  No tasks.")

        return "\n".join(lines)

    def _build_arabic_context(self, emails: List[Dict], tasks: List[Dict], events: List[Dict] = None) -> str:
        events = self._sort_events(events or [])
        emails = self._sort_emails(emails or [])
        tasks = self._sort_tasks(tasks or [])

        _pri_ar = {"urgent": "عاجلة", "high": "عالية", "medium": "متوسطة", "low": "منخفضة"}

        n_unread = sum(1 for e in emails if not e.get('is_read'))
        n_urgent = sum(1 for t in tasks if t.get('priority') in ('urgent', 'high'))
        header = (
            f"نظرة عامة: {len(events)} اجتماع(ات) اليوم | "
            f"{len(emails)} بريد ({n_unread} غير مقروء) | "
            f"{len(tasks)} مهمة ({n_urgent} عاجلة/عالية)"
        )

        lines = [header, ""]

        # Calendar
        lines.append("التقويم (اليوم، حسب الوقت):")
        if events:
            for i, ev in enumerate(events[:20], 1):
                start = _format_time(_parse_dt(ev.get('start_time')))
                end = _format_time(_parse_dt(ev.get('end_time')))
                loc = ev.get('location') or ("عبر الإنترنت" if ev.get('is_online') else "")
                org = ev.get('organizer', '')
                subj = ev.get('subject', 'بدون موضوع')
                parts = [f"{start}-{end}", subj]
                if loc:
                    parts.append(loc)
                if org:
                    parts.append(f"المنظم: {org}")
                lines.append(f"{i}. {' | '.join(parts)}")
        else:
            lines.append("  لا اجتماعات اليوم.")

        # Emails
        lines.append("")
        lines.append("البريد الإلكتروني (الأحدث أولاً):")
        if emails:
            for i, em in enumerate(emails[:12], 1):
                tag = "[غير مقروء] " if not em.get('is_read') else ""
                sender = em.get('sender_name', 'غير معروف')
                subj = em.get('subject', 'بدون موضوع')
                dt_str = _format_datetime_ar(em.get('received_at'))
                preview = _clean_preview(em.get('body_preview', ''))
                line = f'{i}. {tag}{sender} — "{subj}" — {dt_str}'
                if preview:
                    line += f" — {preview}"
                lines.append(line)
        else:
            lines.append("  لا يوجد بريد.")

        # Tasks
        lines.append("")
        lines.append("المهام (حسب الأولوية):")
        if tasks:
            for i, t in enumerate(tasks[:15], 1):
                pri = _pri_ar.get(t.get('priority', 'medium'), 'متوسطة')
                title = t.get('title', 'بدون عنوان')
                due = t.get('due_date')
                due_str = f"الاستحقاق: {_format_datetime_ar(due)}" if due else "بدون تاريخ استحقاق"
                lines.append(f"{i}. [{pri}] {title} — {due_str}")
        else:
            lines.append("  لا مهام.")

        return "\n".join(lines)


# Singleton instance
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Get or create the LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
