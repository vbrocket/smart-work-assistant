# Smart Work Assistant

AI-powered assistant for managing meetings and emails with voice interaction in Arabic and English.

## Features

- **Voice Interaction**: Speak in Arabic or English using Whisper for transcription
- **Email Management**: Connect to Outlook to fetch, summarize, and reply to emails
- **Task Extraction**: Automatically extract actionable tasks from emails
- **Smart Replies**: AI-crafted email replies with tone control (formal, friendly, brief)
- **Daily Summaries**: Get a spoken summary of your tasks and pending emails
- **Bilingual Support**: Full Arabic and English support throughout

## Architecture

```
┌─────────────────┐     ┌─────────────────────────────────────┐
│  PWA Frontend   │────▶│         FastAPI Backend             │
│  (Vanilla JS)   │     │                                     │
└─────────────────┘     │  ┌─────────┐  ┌─────────┐          │
                        │  │ Whisper │  │  Ollama │          │
                        │  │  (STT)  │  │  (LLM)  │          │
                        │  └─────────┘  └─────────┘          │
                        │  ┌─────────┐  ┌─────────────────┐  │
                        │  │Edge TTS │  │ Microsoft Graph │  │
                        │  │ (Voice) │  │   (Outlook)     │  │
                        │  └─────────┘  └─────────────────┘  │
                        │           ┌──────────┐             │
                        │           │  SQLite  │             │
                        │           └──────────┘             │
                        └─────────────────────────────────────┘
```

## Prerequisites

1. **Python 3.10+**
2. **Ollama** - for local LLM inference
   ```bash
   # Install Ollama: https://ollama.ai
   ollama pull qwen2.5:7b  # or llama3.2:8b
   ```
3. **FFmpeg** - required by Whisper for audio processing
   ```bash
   # Windows (via chocolatey)
   choco install ffmpeg
   
   # macOS
   brew install ffmpeg
   
   # Linux
   sudo apt install ffmpeg
   ```

## Installation

1. **Clone and setup backend:**
   ```bash
   cd backend
   
   # Create virtual environment
   python -m venv venv
   
   # Activate (Windows)
   venv\Scripts\activate
   
   # Activate (macOS/Linux)
   source venv/bin/activate
   
   # Install dependencies
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Start Ollama:**
   ```bash
   ollama serve
   ```

4. **Run the backend:**
   ```bash
   cd backend
   python main.py
   ```

5. **Access the app:**
   Open http://localhost:8000 in your browser

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5:7b` | LLM model for chat and analysis |
| `WHISPER_MODEL` | `base` | Whisper model size (tiny/base/small/medium/large) |
| `AZURE_CLIENT_ID` | - | Azure AD app client ID for Outlook |
| `AZURE_TENANT_ID` | `common` | Azure AD tenant (use 'common' for personal accounts) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./smart_assistant.db` | Database connection string |

### Setting up Outlook Integration

1. Go to [Azure Portal](https://portal.azure.com)
2. Register a new app in Azure Active Directory
3. Add the following API permissions:
   - `User.Read`
   - `Mail.Read`
   - `Mail.Send`
4. Enable "Allow public client flows" in Authentication settings
5. Copy the Application (client) ID to your `.env` file

## API Endpoints

### Voice
- `POST /api/voice/transcribe` - Transcribe audio to text
- `POST /api/voice/speak` - Text to speech
- `POST /api/voice/chat` - Chat with AI assistant

### Emails
- `GET /api/emails/auth/device-code` - Start Outlook OAuth
- `GET /api/emails/auth/status` - Check auth status
- `GET /api/emails` - List emails
- `GET /api/emails/{id}` - Get email details
- `POST /api/emails/{id}/summarize` - AI summarize email
- `POST /api/emails/{id}/extract-tasks` - Extract tasks from email
- `POST /api/emails/{id}/draft-reply` - Generate reply draft
- `POST /api/emails/{id}/send` - Send approved reply

### Tasks
- `GET /api/tasks` - List tasks
- `GET /api/tasks/pending` - List pending approval
- `POST /api/tasks` - Create task
- `POST /api/tasks/{id}/approve` - Approve task
- `POST /api/tasks/{id}/reject` - Reject task
- `POST /api/tasks/{id}/complete` - Mark complete

### Summary
- `GET /api/summary/daily` - Get daily summary

## Usage

### Voice Commands (Examples)

**English:**
- "What emails need my attention?"
- "Summarize my latest email"
- "Extract tasks from this email"
- "Draft a formal reply"
- "What's my daily summary?"

**Arabic:**
- "ما هي الرسائل التي تحتاج اهتمامي؟"
- "لخص آخر بريد إلكتروني"
- "استخرج المهام من هذا البريد"
- "اكتب رداً رسمياً"
- "ما هو ملخصي اليومي؟"

### Workflow

1. **Connect Outlook**: Click "Connect Outlook" and follow the device code flow
2. **Review Emails**: View and summarize emails with AI
3. **Extract Tasks**: Let AI identify actionable items
4. **Approve Tasks**: Review and approve/reject proposed tasks
5. **Draft Replies**: Generate professional responses with your preferred tone
6. **Daily Summary**: Get a spoken overview of your day

## Technology Stack

- **Frontend**: Vanilla JavaScript, PWA with Service Worker
- **Backend**: Python FastAPI, SQLAlchemy (async)
- **Database**: SQLite with aiosqlite
- **Speech-to-Text**: OpenAI Whisper / faster-whisper
- **Text-to-Speech**: Microsoft Edge TTS
- **LLM**: Ollama with Qwen2.5 or Llama3.2
- **Email**: Microsoft Graph API

## PWA Installation

The app can be installed as a PWA on mobile devices:

1. Open the app in Chrome/Edge on mobile
2. Tap the browser menu
3. Select "Add to Home Screen" or "Install App"

## Development

```bash
# Run with auto-reload
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run Ollama in background
ollama serve &
```

## License

MIT License - See LICENSE file for details
