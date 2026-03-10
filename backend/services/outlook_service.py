"""
Outlook Service - Microsoft Graph API integration
Handles OAuth device code flow, email, calendar, tasks, and contacts
"""
import asyncio
import json
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any, Tuple

try:
    import msal
    MSAL_AVAILABLE = True
except ImportError:
    MSAL_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User, Email, Task, TaskStatus, TaskPriority, CalendarEvent, Contact
from config import get_settings
from services.logger import get_email_logger

import logging as _logging

settings = get_settings()
logger = get_email_logger()

_logging.getLogger("msal").setLevel(_logging.DEBUG)


class OutlookService:
    """Service for Microsoft Graph API (Outlook) integration."""
    
    # Microsoft Graph API endpoints
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    SCOPES = [
        "User.Read",
        "Mail.Read",
        "Mail.Send",
        "Calendars.ReadWrite",
        "Contacts.Read",
        "Tasks.ReadWrite",
    ]
    
    def __init__(self):
        self.client_id = settings.azure_client_id
        self.tenant_id = settings.azure_tenant_id
        self._msal_app: Optional[msal.PublicClientApplication] = None
        self._device_code_flow: Optional[Dict] = None
    
    @property
    def msal_app(self) -> msal.PublicClientApplication:
        """Get or create MSAL application."""
        if not MSAL_AVAILABLE:
            raise RuntimeError("msal not installed. Install with: pip install msal")
        
        if self._msal_app is None:
            authority = f"https://login.microsoftonline.com/{self.tenant_id}"
            self._msal_app = msal.PublicClientApplication(
                client_id=self.client_id,
                authority=authority
            )
        return self._msal_app
    
    async def start_device_code_flow(self) -> Dict[str, Any]:
        """
        Start the device code flow for authentication.
        
        Returns:
            Dict with user_code, verification_uri, expires_in, message
        """
        logger.info(f"Starting device code flow | client_id={self.client_id[:8] if self.client_id else 'None'}... | tenant={self.tenant_id}")
        
        if not self.client_id:
            logger.error("Azure Client ID not configured")
            raise ValueError(
                "Azure Client ID not configured. "
                "Set AZURE_CLIENT_ID in your environment or .env file."
            )
        
        try:
            # Run in thread pool as MSAL is synchronous
            loop = asyncio.get_event_loop()
            flow = await loop.run_in_executor(
                None,
                lambda: self.msal_app.initiate_device_flow(scopes=self.SCOPES)
            )
            
            logger.debug(f"Device flow response: {flow}")
            
            if "error" in flow:
                logger.error(f"Device flow error: {flow.get('error_description', flow.get('error'))}")
                raise RuntimeError(f"Failed to start device flow: {flow.get('error_description', flow.get('error'))}")
            
            self._device_code_flow = flow
            
            logger.info(f"Device code flow started | code={flow['user_code']} | uri={flow['verification_uri']}")
            
            return {
                "user_code": flow["user_code"],
                "verification_uri": flow["verification_uri"],
                "expires_in": flow["expires_in"],
                "message": flow["message"]
            }
        except Exception as e:
            logger.error(f"Device code flow failed | error_type={type(e).__name__} | error={str(e)}", exc_info=True)
            raise
    
    async def complete_device_code_flow(
        self,
        db: AsyncSession,
        timeout: int = 300
    ) -> Tuple[bool, Optional[User]]:
        """
        Wait for user to complete device code authentication.
        
        Args:
            db: Database session
            timeout: Maximum seconds to wait
        
        Returns:
            Tuple of (success, user)
        """
        if not self._device_code_flow:
            raise RuntimeError("Device code flow not started")
        
        flow = self._device_code_flow
        
        # Poll for token (run in thread pool)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.msal_app.acquire_token_by_device_flow(
                flow,
                timeout=timeout
            )
        )
        
        self._device_code_flow = None
        
        if "error" in result:
            logger.error(
                "Device code token acquisition failed | error=%s | description=%s | correlation=%s",
                result.get("error"),
                result.get("error_description"),
                result.get("correlation_id"),
            )
            return False, None
        
        # Get user info from token
        access_token = result["access_token"]
        refresh_token = result.get("refresh_token")
        expires_in = result.get("expires_in", 3600)
        
        # Fetch user profile
        user_info = await self._get_user_profile(access_token)
        
        if not user_info:
            return False, None
        
        # Create or update user in database
        user = await self._get_or_create_user(
            db=db,
            email=user_info["mail"] or user_info["userPrincipalName"],
            name=user_info.get("displayName", ""),
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in
        )
        
        return True, user
    
    async def _get_user_profile(self, access_token: str) -> Optional[Dict]:
        """Fetch user profile from Microsoft Graph."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code == 200:
                return response.json()
            return None
    
    async def _get_or_create_user(
        self,
        db: AsyncSession,
        email: str,
        name: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_in: int
    ) -> User:
        """Get existing user or create new one."""
        # Check for existing user
        result = await db.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        if user:
            # Update tokens
            user.access_token = access_token
            user.refresh_token = refresh_token
            user.token_expires_at = expires_at
            user.name = name
        else:
            # Create new user
            user = User(
                email=email,
                name=name,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=expires_at
            )
            db.add(user)
        
        await db.commit()
        await db.refresh(user)
        
        return user
    
    async def refresh_token_if_needed(
        self,
        db: AsyncSession,
        user: User
    ) -> str:
        """
        Refresh the access token if it's expired or about to expire.
        
        Returns:
            Valid access token
        """
        if not user.token_expires_at:
            raise RuntimeError("No token expiration time set")
        
        # Check if token is still valid (with 5 min buffer)
        if datetime.utcnow() < user.token_expires_at - timedelta(minutes=5):
            return user.access_token
        
        if not user.refresh_token:
            raise RuntimeError("No refresh token available. Re-authentication required.")
        
        # Refresh the token
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.msal_app.acquire_token_by_refresh_token(
                user.refresh_token,
                scopes=self.SCOPES
            )
        )
        
        if "error" in result:
            raise RuntimeError(f"Token refresh failed: {result.get('error_description')}")
        
        # Update user tokens
        user.access_token = result["access_token"]
        user.refresh_token = result.get("refresh_token", user.refresh_token)
        user.token_expires_at = datetime.utcnow() + timedelta(
            seconds=result.get("expires_in", 3600)
        )
        
        await db.commit()
        
        return user.access_token
    
    async def fetch_emails(
        self,
        access_token: str,
        top: int = 20,
        skip: int = 0,
        filter_unread: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch emails from user's inbox.
        
        Args:
            access_token: Valid access token
            top: Number of emails to fetch
            skip: Number of emails to skip
            filter_unread: Only fetch unread emails
        
        Returns:
            List of email dictionaries
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        # Build query parameters
        params = {
            "$top": top,
            "$skip": skip,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,bodyPreview,body,receivedDateTime,isRead"
        }
        
        if filter_unread:
            params["$filter"] = "isRead eq false"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params
            )
            
            if response.status_code != 200:
                raise RuntimeError(f"Failed to fetch emails: {response.status_code}")
            
            data = response.json()
            return data.get("value", [])
    
    async def get_email(
        self,
        access_token: str,
        message_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single email by ID.
        
        Args:
            access_token: Valid access token
            message_id: Microsoft Graph message ID
        
        Returns:
            Email dictionary or None
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/messages/{message_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "$select": "id,subject,from,toRecipients,body,bodyPreview,receivedDateTime,isRead"
                }
            )
            
            if response.status_code == 200:
                return response.json()
            return None
    
    async def send_email(
        self,
        access_token: str,
        to: List[str],
        subject: str,
        body: str,
        reply_to_id: Optional[str] = None
    ) -> bool:
        """
        Send an email.
        
        Args:
            access_token: Valid access token
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (HTML supported)
            reply_to_id: Optional message ID to reply to
        
        Returns:
            True if sent successfully
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        message = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body
            },
            "toRecipients": [
                {"emailAddress": {"address": email}}
                for email in to
            ]
        }
        
        async with httpx.AsyncClient() as client:
            if reply_to_id:
                # Send as reply
                url = f"{self.GRAPH_BASE_URL}/me/messages/{reply_to_id}/reply"
                payload = {"message": message, "comment": body}
            else:
                # Send new email
                url = f"{self.GRAPH_BASE_URL}/me/sendMail"
                payload = {"message": message}
            
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            
            return response.status_code in [200, 202]
    
    async def sync_emails_to_db(
        self,
        db: AsyncSession,
        user: User,
        access_token: str,
        limit: int = 50
    ) -> List[Email]:
        """
        Sync emails from Outlook to local database.
        
        Returns:
            List of synced Email objects
        """
        # Fetch emails from Graph API
        graph_emails = await self.fetch_emails(access_token, top=limit)
        
        synced_emails = []
        
        for email_data in graph_emails:
            # Check if email already exists
            result = await db.execute(
                select(Email).where(Email.graph_id == email_data["id"])
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Update existing email
                existing.is_read = email_data.get("isRead", False)
                synced_emails.append(existing)
            else:
                # Create new email record
                sender = email_data.get("from", {}).get("emailAddress", {})
                body = email_data.get("body", {})
                
                email = Email(
                    user_id=user.id,
                    graph_id=email_data["id"],
                    subject=email_data.get("subject", "(No Subject)"),
                    sender_name=sender.get("name", "Unknown"),
                    sender_email=sender.get("address", ""),
                    body_preview=email_data.get("bodyPreview", ""),
                    body_content=body.get("content", ""),
                    received_at=datetime.fromisoformat(
                        email_data["receivedDateTime"].replace("Z", "+00:00")
                    ),
                    is_read=email_data.get("isRead", False)
                )
                db.add(email)
                synced_emails.append(email)
        
        await db.commit()
        
        return synced_emails
    
    # ============ Microsoft To-Do Tasks Methods ============
    
    async def fetch_task_lists(
        self,
        access_token: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all task lists from Microsoft To-Do.
        
        Returns:
            List of task list dictionaries
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/todo/lists",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch task lists: {response.status_code} - {response.text}")
                return []
            
            data = response.json()
            return data.get("value", [])
    
    async def fetch_tasks_from_list(
        self,
        access_token: str,
        list_id: str,
        include_completed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch tasks from a specific task list.
        
        Args:
            access_token: Valid access token
            list_id: The task list ID
            include_completed: Whether to include completed tasks
        
        Returns:
            List of task dictionaries
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        params = {
            "$orderby": "createdDateTime desc",
            "$top": 50
        }
        
        if not include_completed:
            params["$filter"] = "status ne 'completed'"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/todo/lists/{list_id}/tasks",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch tasks from list {list_id}: {response.status_code}")
                return []
            
            data = response.json()
            return data.get("value", [])
    
    async def fetch_all_tasks(
        self,
        access_token: str,
        include_completed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch all tasks from all task lists.
        
        Returns:
            List of all tasks with their list info
        """
        all_tasks = []
        
        # Get all task lists
        task_lists = await self.fetch_task_lists(access_token)
        logger.info(f"Found {len(task_lists)} task lists")
        
        for task_list in task_lists:
            list_id = task_list["id"]
            list_name = task_list.get("displayName", "Unknown List")
            
            # Fetch tasks from this list
            tasks = await self.fetch_tasks_from_list(
                access_token, 
                list_id, 
                include_completed
            )
            
            # Add list info to each task
            for task in tasks:
                task["_listId"] = list_id
                task["_listName"] = list_name
                all_tasks.append(task)
        
        logger.info(f"Fetched {len(all_tasks)} total tasks from Microsoft To-Do")
        return all_tasks
    
    def _map_todo_priority(self, importance: str) -> TaskPriority:
        """Map Microsoft To-Do importance to our TaskPriority."""
        mapping = {
            "high": TaskPriority.HIGH,
            "normal": TaskPriority.MEDIUM,
            "low": TaskPriority.LOW
        }
        return mapping.get(importance.lower(), TaskPriority.MEDIUM)
    
    def _map_todo_status(self, status: str) -> TaskStatus:
        """Map Microsoft To-Do status to our TaskStatus."""
        mapping = {
            "notStarted": TaskStatus.APPROVED,
            "inProgress": TaskStatus.APPROVED,
            "completed": TaskStatus.COMPLETED,
            "waitingOnOthers": TaskStatus.PENDING_APPROVAL,
            "deferred": TaskStatus.PENDING_APPROVAL
        }
        return mapping.get(status, TaskStatus.APPROVED)
    
    async def sync_tasks_to_db(
        self,
        db: AsyncSession,
        user: User,
        access_token: str,
        include_completed: bool = False
    ) -> List[Task]:
        """
        Sync tasks from Microsoft To-Do to local database.
        
        Returns:
            List of synced Task objects
        """
        # Fetch tasks from Graph API
        graph_tasks = await self.fetch_all_tasks(access_token, include_completed)
        
        synced_tasks = []
        
        for task_data in graph_tasks:
            task_id = task_data.get("id")
            
            # Check if task already exists (by graph_id stored in description or a new field)
            # We'll use a convention: store graph_id in description prefix
            graph_marker = f"[MSFT:{task_id[:8]}]"
            
            result = await db.execute(
                select(Task).where(
                    Task.user_id == user.id,
                    Task.description.like(f"{graph_marker}%")
                )
            )
            existing = result.scalar_one_or_none()
            
            # Parse due date if present
            due_date = None
            if task_data.get("dueDateTime"):
                try:
                    due_str = task_data["dueDateTime"]["dateTime"]
                    due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    pass
            
            # Get task details
            title = task_data.get("title", "Untitled Task")
            body_content = task_data.get("body", {}).get("content", "")
            importance = task_data.get("importance", "normal")
            status = task_data.get("status", "notStarted")
            list_name = task_data.get("_listName", "Tasks")
            
            # Build description with graph marker
            description = f"{graph_marker} [{list_name}] {body_content}".strip()
            
            if existing:
                # Update existing task
                existing.title = title
                existing.priority = self._map_todo_priority(importance)
                existing.status = self._map_todo_status(status)
                existing.due_date = due_date
                synced_tasks.append(existing)
            else:
                # Create new task
                task = Task(
                    user_id=user.id,
                    title=title,
                    description=description,
                    status=self._map_todo_status(status),
                    priority=self._map_todo_priority(importance),
                    due_date=due_date
                )
                db.add(task)
                synced_tasks.append(task)
        
        await db.commit()
        
        # Refresh to get IDs
        for task in synced_tasks:
            await db.refresh(task)
        
        logger.info(f"Synced {len(synced_tasks)} tasks to database")
        return synced_tasks
    
    # ============ Calendar Methods ============
    
    async def fetch_calendar_events(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        top: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch calendar events for a date range using calendarView."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        params = {
            "startDateTime": start_date,
            "endDateTime": end_date,
            "$top": top,
            "$orderby": "start/dateTime",
            "$select": "id,subject,organizer,start,end,location,isOnlineMeeting,onlineMeeting,bodyPreview,isAllDay,attendees,responseStatus"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/calendarView",
                headers={"Authorization": f"Bearer {access_token}",
                          "Prefer": 'outlook.timezone="UTC"'},
                params=params
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch calendar events: {response.status_code} - {response.text}")
                return []
            
            data = response.json()
            return data.get("value", [])
    
    async def create_calendar_event(
        self,
        access_token: str,
        subject: str,
        start: str,
        end: str,
        attendees: List[Dict[str, str]] = None,
        location: str = None,
        body: str = None,
        is_online: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Create a calendar event with optional Teams meeting link.
        
        Args:
            attendees: List of {"email": "...", "name": "..."} dicts
        """
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        event_body = {
            "subject": subject,
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
            "isOnlineMeeting": is_online,
            "onlineMeetingProvider": "teamsForBusiness" if is_online else None
        }
        
        if location:
            event_body["location"] = {"displayName": location}
        
        if body:
            event_body["body"] = {"contentType": "text", "content": body}
        
        if attendees:
            event_body["attendees"] = [
                {
                    "emailAddress": {
                        "address": a.get("email", ""),
                        "name": a.get("name", a.get("email", ""))
                    },
                    "type": "required"
                }
                for a in attendees
            ]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.GRAPH_BASE_URL}/me/events",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json=event_body
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Calendar event created: {subject}")
                return response.json()
            else:
                logger.error(f"Failed to create event: {response.status_code} - {response.text}")
                return None
    
    async def sync_events_to_db(
        self,
        db: AsyncSession,
        user: User,
        access_token: str,
        target_date: date = None,
        days_ahead: int = 14
    ) -> List[CalendarEvent]:
        """Sync calendar events from target_date through target_date + days_ahead."""
        if target_date is None:
            target_date = date.today()
        
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date + timedelta(days=days_ahead), datetime.max.time())
        
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.0000000")
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.0000000")
        
        graph_events = await self.fetch_calendar_events(access_token, start_str, end_str)
        
        synced = []
        for ev in graph_events:
            graph_id = ev.get("id")
            
            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.graph_id == graph_id)
            )
            existing = result.scalar_one_or_none()
            
            organizer = ev.get("organizer", {}).get("emailAddress", {})
            location_data = ev.get("location", {})
            online_meeting = ev.get("onlineMeeting") or {}
            
            attendees_list = []
            for att in ev.get("attendees", []):
                ea = att.get("emailAddress", {})
                attendees_list.append({
                    "name": ea.get("name", ""),
                    "email": ea.get("address", ""),
                    "status": att.get("status", {}).get("response", "none")
                })
            
            start_time = None
            end_time = None
            try:
                start_time = datetime.fromisoformat(
                    ev.get("start", {}).get("dateTime", "").replace("Z", "+00:00")
                )
                end_time = datetime.fromisoformat(
                    ev.get("end", {}).get("dateTime", "").replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
            
            response_status = ev.get("responseStatus", {}).get("response", "none")
            
            fields = dict(
                user_id=user.id,
                subject=ev.get("subject", "(No Subject)"),
                organizer_name=organizer.get("name", ""),
                organizer_email=organizer.get("address", ""),
                start_time=start_time,
                end_time=end_time,
                is_all_day=ev.get("isAllDay", False),
                location=location_data.get("displayName", ""),
                is_online=ev.get("isOnlineMeeting", False),
                online_meeting_url=online_meeting.get("joinUrl", ""),
                body_preview=ev.get("bodyPreview", ""),
                attendees=json.dumps(attendees_list),
                status=response_status
            )
            
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                synced.append(existing)
            else:
                event = CalendarEvent(graph_id=graph_id, **fields)
                db.add(event)
                synced.append(event)
        
        await db.commit()
        for ev in synced:
            await db.refresh(ev)
        
        logger.info(f"Synced {len(synced)} calendar events for {target_date}")
        return synced
    
    # ============ Contacts Methods ============
    
    async def fetch_contacts(
        self,
        access_token: str,
        search: str = None,
        top: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch user's contacts from Microsoft Graph."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")
        
        params = {
            "$top": top,
            "$orderby": "displayName",
            "$select": "id,displayName,emailAddresses,companyName,jobTitle"
        }
        
        if search:
            params["$filter"] = f"startswith(displayName,'{search}') or startswith(givenName,'{search}') or startswith(surname,'{search}')"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.GRAPH_BASE_URL}/me/contacts",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch contacts: {response.status_code} - {response.text}")
                return []
            
            data = response.json()
            return data.get("value", [])
    
    async def sync_contacts_to_db(
        self,
        db: AsyncSession,
        user: User,
        access_token: str
    ) -> List[Contact]:
        """Cache contacts in local DB for fast autocomplete."""
        graph_contacts = await self.fetch_contacts(access_token, top=200)
        
        synced = []
        for c in graph_contacts:
            graph_id = c.get("id")
            emails = c.get("emailAddresses", [])
            primary_email = emails[0].get("address", "") if emails else ""
            
            if not primary_email:
                continue
            
            result = await db.execute(
                select(Contact).where(Contact.graph_id == graph_id)
            )
            existing = result.scalar_one_or_none()
            
            fields = dict(
                user_id=user.id,
                display_name=c.get("displayName", ""),
                email=primary_email,
                company=c.get("companyName", ""),
                job_title=c.get("jobTitle", ""),
                synced_at=datetime.utcnow()
            )
            
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                synced.append(existing)
            else:
                contact = Contact(graph_id=graph_id, **fields)
                db.add(contact)
                synced.append(contact)
        
        await db.commit()
        logger.info(f"Synced {len(synced)} contacts to database")
        return synced


# Singleton instance
_outlook_service: Optional[OutlookService] = None


def get_outlook_service() -> OutlookService:
    """Get or create the Outlook service singleton."""
    global _outlook_service
    if _outlook_service is None:
        _outlook_service = OutlookService()
    return _outlook_service
