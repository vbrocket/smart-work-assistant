"""
LLM Service using Ollama
Supports bilingual (Arabic/English) conversation with smart prompts
"""
import asyncio
import json
from typing import Optional, List, Dict, Any
from datetime import datetime

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from config import get_settings
from services.logger import get_llm_logger

settings = get_settings()
logger = get_llm_logger()


class LLMService:
    """Service for LLM interactions using Ollama."""
    
    # System prompts for the assistant
    SYSTEM_PROMPTS = {
        'en': """You are a Smart Work Assistant, an AI-powered companion designed to help employees manage their work efficiently.

Your capabilities:
- Help manage and summarize emails
- Extract actionable tasks from communications
- Draft professional email replies
- Provide daily summaries and weekly planning
- Answer questions about tasks and priorities

Guidelines:
- Be concise and professional
- When extracting tasks, identify specific actions with clear descriptions
- For email summaries, highlight key points, sentiment, and urgency
- Match the user's language (respond in English if they write in English)
- Be helpful but respect that final decisions are made by the user

Current date: {date}""",

        'ar': """أنت مساعد العمل الذكي، رفيق مدعوم بالذكاء الاصطناعي مصمم لمساعدة الموظفين على إدارة عملهم بكفاءة.

قدراتك:
- المساعدة في إدارة وتلخيص رسائل البريد الإلكتروني
- استخراج المهام القابلة للتنفيذ من المراسلات
- صياغة ردود بريد إلكتروني احترافية
- تقديم ملخصات يومية وتخطيط أسبوعي
- الإجابة على الأسئلة حول المهام والأولويات

الإرشادات:
- كن موجزاً ومهنياً
- عند استخراج المهام، حدد إجراءات محددة بأوصاف واضحة
- لملخصات البريد الإلكتروني، أبرز النقاط الرئيسية والمشاعر ومستوى الإلحاح
- طابق لغة المستخدم (أجب بالعربية إذا كتبوا بالعربية)
- كن مفيداً مع احترام أن القرارات النهائية يتخذها المستخدم

التاريخ الحالي: {date}"""
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
            'en': """Create a daily summary based on the following information:

Tasks:
{tasks}

Emails requiring action:
{emails}

Provide a brief, actionable summary that helps the user prioritize their day.
Format as natural text, suitable for being read aloud.""",

            'ar': """أنشئ ملخصاً يومياً بناءً على المعلومات التالية:

المهام:
{tasks}

رسائل البريد الإلكتروني التي تتطلب إجراء:
{emails}

قدم ملخصاً موجزاً وقابلاً للتنفيذ يساعد المستخدم على تحديد أولويات يومه.
صِغه كنص طبيعي، مناسب للقراءة بصوت عالٍ."""
        }
    }
    
    def __init__(self):
        self.base_url = settings.ollama_host
        self.model = settings.ollama_model
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history = 10
    
    async def _make_request(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        stream: bool = False
    ) -> Dict[str, Any]:
        """Make a request to Ollama API."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed. Install with: pip install httpx")
        
        url = f"{self.base_url}{endpoint}"
        logger.debug(f"Ollama request | url={url} | model={payload.get('model')} | stream={stream}")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                if stream:
                    # For streaming responses
                    async with client.stream("POST", url, json=payload) as response:
                        logger.debug(f"Ollama stream response | status={response.status_code}")
                        response.raise_for_status()
                        full_response = ""
                        async for line in response.aiter_lines():
                            if line:
                                data = json.loads(line)
                                if "response" in data:
                                    full_response += data["response"]
                                if data.get("done"):
                                    break
                        logger.debug(f"Ollama stream complete | response_length={len(full_response)}")
                        return {"response": full_response}
                else:
                    response = await client.post(url, json=payload)
                    logger.debug(f"Ollama response | status={response.status_code}")
                    response.raise_for_status()
                    result = response.json()
                    logger.debug(f"Ollama success | response_keys={list(result.keys())}")
                    return result
        except httpx.ConnectError as e:
            logger.error(f"Ollama connection failed | url={url} | error={str(e)}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error | status={e.response.status_code} | url={url}")
            raise
        except Exception as e:
            logger.error(f"Ollama request failed | error_type={type(e).__name__} | error={str(e)}")
            raise
    
    def _get_system_prompt(self, language: str) -> str:
        """Get the system prompt for the given language."""
        prompt = self.SYSTEM_PROMPTS.get(language, self.SYSTEM_PROMPTS['en'])
        return prompt.format(date=datetime.now().strftime("%Y-%m-%d"))
    
    async def chat(
        self,
        message: str,
        language: str = "en",
        include_history: bool = True
    ) -> str:
        """
        Send a chat message and get a response.
        
        Args:
            message: User's message
            language: Language code ('en' or 'ar')
            include_history: Whether to include conversation history
        
        Returns:
            Assistant's response
        """
        logger.info(f"Chat request | language={language} | message_length={len(message)} | history={include_history}")
        
        # Build messages array
        messages = [
            {"role": "system", "content": self._get_system_prompt(language)}
        ]
        
        if include_history:
            messages.extend(self.conversation_history[-self.max_history:])
        
        messages.append({"role": "user", "content": message})
        logger.debug(f"Chat messages prepared | total_messages={len(messages)}")
        
        try:
            result = await self._make_request(
                "/api/chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9
                    }
                }
            )
            
            response_text = result.get("message", {}).get("content", "")
            logger.info(f"Chat response received | response_length={len(response_text)}")
            
            # Update conversation history
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            
            # Trim history if needed
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]
            
            return response_text
            
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to Ollama | base_url={self.base_url} | error={str(e)}")
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running with: ollama serve"
            )
        except Exception as e:
            logger.error(f"Chat failed | error_type={type(e).__name__} | error={str(e)}", exc_info=True)
            raise RuntimeError(f"LLM request failed: {str(e)}")
    
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
            result = await self._make_request(
                "/api/generate",
                {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature
                    }
                }
            )
            
            return result.get("response", "")
            
        except httpx.ConnectError:
            raise RuntimeError(f"Cannot connect to Ollama at {self.base_url}")
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
        language: str = "en"
    ) -> str:
        """
        Generate a daily summary for the user.
        
        Returns:
            Natural language summary suitable for TTS
        """
        prompt_template = self.TASK_PROMPTS['daily_summary'][language]
        
        # Format tasks
        tasks_text = "\n".join([
            f"- {t.get('title', 'Untitled')} ({t.get('priority', 'medium')} priority)"
            for t in tasks
        ]) or "No pending tasks"
        
        # Format emails
        emails_text = "\n".join([
            f"- From {e.get('sender_name', 'Unknown')}: {e.get('subject', 'No subject')}"
            for e in emails
        ]) or "No emails requiring immediate action"
        
        prompt = prompt_template.format(
            tasks=tasks_text,
            emails=emails_text
        )
        
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
        language: str = "en"
    ) -> str:
        """
        Chat with context about user's emails and tasks.
        
        Args:
            message: User's message/question
            emails: List of user's emails
            tasks: List of user's tasks
            language: Language code
        
        Returns:
            AI response with context awareness
        """
        logger.info(f"Contextual chat | language={language} | emails={len(emails)} | tasks={len(tasks)}")
        
        # Build context string
        if language == 'ar':
            context = self._build_arabic_context(emails, tasks)
            system_prompt = f"""{self.SYSTEM_PROMPTS['ar'].format(date=datetime.now().strftime("%Y-%m-%d"))}

لديك حق الوصول إلى البيانات التالية للمستخدم:

{context}

استخدم هذه المعلومات للإجابة على أسئلة المستخدم. كن محددًا وأشر إلى رسائل البريد الإلكتروني أو المهام ذات الصلة عند الإجابة.
إذا سأل المستخدم عن شيء غير موجود في البيانات، قل ذلك بوضوح."""
        else:
            context = self._build_english_context(emails, tasks)
            system_prompt = f"""{self.SYSTEM_PROMPTS['en'].format(date=datetime.now().strftime("%Y-%m-%d"))}

You have access to the following user data:

{context}

Use this information to answer user questions. Be specific and reference relevant emails or tasks when answering.
If the user asks about something not in the data, clearly say so."""
        
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Include recent history for context continuity
        messages.extend(self.conversation_history[-4:])
        messages.append({"role": "user", "content": message})
        
        try:
            result = await self._make_request(
                "/api/chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9
                    }
                }
            )
            
            response_text = result.get("message", {}).get("content", "")
            logger.info(f"Contextual chat response | length={len(response_text)}")
            
            # Update history
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]
            
            return response_text
            
        except httpx.ConnectError:
            raise RuntimeError(f"Cannot connect to Ollama at {self.base_url}")
        except Exception as e:
            logger.error(f"Contextual chat failed | error={str(e)}", exc_info=True)
            raise RuntimeError(f"LLM request failed: {str(e)}")
    
    def _build_english_context(self, emails: List[Dict], tasks: List[Dict]) -> str:
        """Build English context string from emails and tasks."""
        lines = []
        
        # Emails section
        lines.append("=== EMAILS ===")
        if emails:
            for i, email in enumerate(emails[:10], 1):
                lines.append(f"""
Email {i}:
  From: {email.get('sender_name', 'Unknown')} <{email.get('sender_email', '')}>
  Subject: {email.get('subject', 'No subject')}
  Date: {email.get('received_at', 'Unknown')}
  Preview: {email.get('body_preview', '')[:200]}
  Status: {'Unread' if not email.get('is_read') else 'Read'}
  Urgency: {email.get('urgency', 'Not assessed')}""")
        else:
            lines.append("No emails found.")
        
        # Tasks section
        lines.append("\n=== TASKS ===")
        if tasks:
            for i, task in enumerate(tasks[:15], 1):
                lines.append(f"""
Task {i}:
  Title: {task.get('title', 'Untitled')}
  Description: {task.get('description', 'No description')}
  Status: {task.get('status', 'unknown')}
  Priority: {task.get('priority', 'medium')}
  Due: {task.get('due_date', 'No due date')}""")
        else:
            lines.append("No tasks found.")
        
        return "\n".join(lines)
    
    def _build_arabic_context(self, emails: List[Dict], tasks: List[Dict]) -> str:
        """Build Arabic context string from emails and tasks."""
        lines = []
        
        # Emails section
        lines.append("=== رسائل البريد الإلكتروني ===")
        if emails:
            for i, email in enumerate(emails[:10], 1):
                status = 'غير مقروء' if not email.get('is_read') else 'مقروء'
                lines.append(f"""
البريد {i}:
  من: {email.get('sender_name', 'غير معروف')} <{email.get('sender_email', '')}>
  الموضوع: {email.get('subject', 'بدون موضوع')}
  التاريخ: {email.get('received_at', 'غير معروف')}
  معاينة: {email.get('body_preview', '')[:200]}
  الحالة: {status}
  الإلحاح: {email.get('urgency', 'غير محدد')}""")
        else:
            lines.append("لا توجد رسائل بريد إلكتروني.")
        
        # Tasks section  
        lines.append("\n=== المهام ===")
        if tasks:
            status_ar = {
                'pending_approval': 'في انتظار الموافقة',
                'approved': 'معتمدة',
                'completed': 'مكتملة',
                'rejected': 'مرفوضة'
            }
            priority_ar = {
                'low': 'منخفضة',
                'medium': 'متوسطة', 
                'high': 'عالية',
                'urgent': 'عاجلة'
            }
            for i, task in enumerate(tasks[:15], 1):
                lines.append(f"""
المهمة {i}:
  العنوان: {task.get('title', 'بدون عنوان')}
  الوصف: {task.get('description', 'بدون وصف')}
  الحالة: {status_ar.get(task.get('status', ''), task.get('status', 'غير معروف'))}
  الأولوية: {priority_ar.get(task.get('priority', ''), task.get('priority', 'متوسطة'))}
  الاستحقاق: {task.get('due_date', 'بدون تاريخ')}""")
        else:
            lines.append("لا توجد مهام.")
        
        return "\n".join(lines)


# Singleton instance
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Get or create the LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
