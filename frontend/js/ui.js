/**
 * UI Service - Handles UI components and interactions
 */
const UI = {
    currentLanguage: 'en',
    
    // Internationalization strings
    i18n: {
        en: {
            'nav.chat': 'Chat',
            'nav.emails': 'Emails',
            'nav.tasks': 'Tasks',
            'nav.summary': 'Daily Summary',
            'connect.outlook': 'Connect Outlook',
            'connect.connected': 'Connected',
            'connect.disconnect': 'Disconnect',
            'sidebar.connect': 'Connect Outlook',
            'welcome.message': "Hello! I'm your Smart Work Assistant. I can help you manage emails, extract tasks, and prepare summaries. How can I help you today?",
            'chat.placeholder': 'Type a message...',
            'voice.listening': 'Listening...',
            'voice.processing': 'Processing...',
            'voice.stop': 'Tap to stop',
            'emails.title': 'Emails',
            'emails.connect': 'Connect Outlook to view emails',
            'tasks.title': 'Tasks',
            'tasks.all': 'All',
            'tasks.pending': 'Pending',
            'tasks.approved': 'Active',
            'tasks.empty': 'No tasks yet',
            'summary.title': 'Daily Summary',
            'summary.listen': 'Listen',
            'summary.date': 'Today',
            'summary.tasksSection': 'Tasks Overview',
            'summary.emailsSection': 'Emails Requiring Action',
            'summary.pendingApproval': 'Pending Approval',
            'summary.active': 'Active',
            'summary.completed': 'Completed',
            'summary.noEmails': 'No emails require immediate action',
            'error.network': 'Network error - please check your connection',
            'error.microphone': 'Microphone access denied',
            'auth.title': 'Connect to Outlook',
            'auth.step1': 'Click the link below to open Microsoft login',
            'auth.step2': 'Enter this code when prompted:',
            'auth.step3': 'Sign in with your Outlook account and approve',
            'auth.openLink': 'Open Microsoft Login',
            'auth.waiting': 'Waiting for authentication...',
            'auth.success': 'Connected successfully!',
            'auth.close': 'Close',
            'auth.copied': 'Code copied!',
            'auth.expired': 'Outlook session expired. Reconnect to sync emails, calendar, and tasks.',
            'auth.reconnect': 'Reconnect',
            'auth.notConnected': 'Outlook not connected. Connect to access emails, calendar, and tasks.',
            'auth.codeExpired': 'Code expired. Please try again.',
            'auth.retry': 'Get New Code',
            'handsfree.title': 'Hands-Free Mode',
            'handsfree.listening': 'Listening...',
            'handsfree.processing': 'Processing...',
            'handsfree.thinking': 'Thinking...',
            'handsfree.speaking': 'Speaking...',
            'handsfree.error': 'Error occurred',
            'handsfree.exit': 'Exit',
            'handsfree.hint': 'Say "exit" or "stop" to end',
            'handsfree.start': 'Hands-Free',
            'nav.calendar': 'Calendar',
            'calendar.title': 'Calendar',
            'calendar.today': 'Today',
            'calendar.noEvents': 'No meetings today',
            'calendar.create': 'Create Meeting',
            'calendar.subject': 'Subject',
            'calendar.start': 'Start',
            'calendar.end': 'End',
            'calendar.location': 'Location',
            'calendar.attendees': 'Attendees',
            'calendar.teams': 'Add Teams Meeting Link',
            'calendar.notes': 'Notes',
            'calendar.allDay': 'All Day',
            'calendar.join': 'Join Teams',
            'calendar.created': 'Meeting created!',
            'prompt.todayWork': "Today's Workload",
            'prompt.unreadEmails': 'Unread Emails',
            'prompt.pendingTasks': 'Pending Tasks',
            'prompt.nextMeeting': 'Next Meeting',
            'prompt.draftReplies': 'Draft Replies',
            'prompt.weeklyPlan': 'Weekly Plan',
            'nav.policy': 'Policy Docs',
            'policy.title': 'Policy Documents',
            'policy.upload': 'Browse Files',
            'policy.dragDrop': 'Drag & drop files here or click to browse',
            'policy.ingest': 'Ingest All Documents',
            'policy.ingesting': 'Ingesting...',
            'policy.noDocuments': 'No documents uploaded yet',
            'policy.deleteConfirm': 'Delete this document?',
            'policy.stats.documents': 'Documents',
            'policy.stats.chunks': 'Indexed Chunks',
            'policy.stats.lastIngestion': 'Last Ingestion',
            'policy.stats.never': 'Never',
            'policy.uploadSuccess': 'Document uploaded successfully',
            'policy.deleteSuccess': 'Document deleted',
            'policy.ingestSuccess': 'Ingestion complete!'
        },
        ar: {
            'nav.chat': 'المحادثة',
            'nav.emails': 'البريد',
            'nav.tasks': 'المهام',
            'nav.calendar': 'التقويم',
            'nav.summary': 'الملخص اليومي',
            'connect.outlook': 'ربط Outlook',
            'connect.connected': 'متصل',
            'connect.disconnect': 'قطع الاتصال',
            'sidebar.connect': 'ربط Outlook',
            'welcome.message': 'مرحباً! أنا مساعدك الذكي. يمكنني مساعدتك في إدارة البريد الإلكتروني، واستخراج المهام، وإعداد الملخصات. كيف يمكنني مساعدتك اليوم؟',
            'chat.placeholder': 'اكتب رسالة...',
            'voice.listening': 'جارٍ الاستماع...',
            'voice.processing': 'جارٍ المعالجة...',
            'voice.stop': 'اضغط للإيقاف',
            'emails.title': 'البريد الإلكتروني',
            'emails.connect': 'اربط Outlook لعرض البريد',
            'tasks.title': 'المهام',
            'tasks.all': 'الكل',
            'tasks.pending': 'معلقة',
            'tasks.approved': 'نشطة',
            'tasks.empty': 'لا توجد مهام بعد',
            'summary.title': 'الملخص اليومي',
            'summary.listen': 'استمع',
            'summary.date': 'اليوم',
            'summary.tasksSection': 'نظرة عامة على المهام',
            'summary.emailsSection': 'رسائل تتطلب إجراء',
            'summary.pendingApproval': 'في انتظار الموافقة',
            'summary.active': 'نشطة',
            'summary.completed': 'مكتملة',
            'summary.noEmails': 'لا توجد رسائل تتطلب إجراء فوري',
            'error.network': 'خطأ في الشبكة - تحقق من اتصالك',
            'error.microphone': 'تم رفض الوصول إلى الميكروفون',
            'auth.title': 'الاتصال بـ Outlook',
            'auth.step1': 'اضغط على الرابط أدناه لفتح تسجيل الدخول',
            'auth.step2': 'أدخل هذا الرمز عند الطلب:',
            'auth.step3': 'سجّل الدخول بحسابك وقم بالموافقة',
            'auth.openLink': 'فتح تسجيل دخول Microsoft',
            'auth.waiting': 'في انتظار المصادقة...',
            'auth.success': 'تم الاتصال بنجاح!',
            'auth.close': 'إغلاق',
            'auth.copied': 'تم نسخ الرمز!',
            'auth.expired': 'انتهت جلسة Outlook. أعد الاتصال لمزامنة البريد والتقويم والمهام.',
            'auth.reconnect': 'إعادة الاتصال',
            'auth.notConnected': 'Outlook غير متصل. اتصل للوصول إلى البريد والتقويم والمهام.',
            'auth.codeExpired': 'انتهت صلاحية الرمز. حاول مرة أخرى.',
            'auth.retry': 'رمز جديد',
            'handsfree.title': 'وضع التحدث الحر',
            'handsfree.listening': 'جارٍ الاستماع...',
            'handsfree.processing': 'جارٍ المعالجة...',
            'handsfree.thinking': 'جارٍ التفكير...',
            'handsfree.speaking': 'جارٍ التحدث...',
            'handsfree.error': 'حدث خطأ',
            'handsfree.exit': 'خروج',
            'handsfree.hint': 'قل "توقف" أو "خروج" للإنهاء',
            'handsfree.start': 'تحدث حر',
            'calendar.title': 'التقويم',
            'calendar.today': 'اليوم',
            'calendar.noEvents': 'لا توجد اجتماعات اليوم',
            'calendar.create': 'إنشاء اجتماع',
            'calendar.subject': 'الموضوع',
            'calendar.start': 'البداية',
            'calendar.end': 'النهاية',
            'calendar.location': 'المكان',
            'calendar.attendees': 'الحضور',
            'calendar.teams': 'إضافة رابط Teams',
            'calendar.notes': 'ملاحظات',
            'calendar.allDay': 'طوال اليوم',
            'calendar.join': 'انضم عبر Teams',
            'calendar.created': 'تم إنشاء الاجتماع!',
            'prompt.todayWork': 'عمل اليوم',
            'prompt.unreadEmails': 'بريد غير مقروء',
            'prompt.pendingTasks': 'مهام معلقة',
            'prompt.nextMeeting': 'الاجتماع القادم',
            'prompt.draftReplies': 'مسودات الرد',
            'prompt.weeklyPlan': 'خطة الأسبوع',
            'nav.policy': 'وثائق السياسة',
            'policy.title': 'وثائق السياسة',
            'policy.upload': 'استعراض الملفات',
            'policy.dragDrop': 'اسحب وأفلت الملفات هنا أو اضغط للاستعراض',
            'policy.ingest': 'معالجة جميع الوثائق',
            'policy.ingesting': 'جارٍ المعالجة...',
            'policy.noDocuments': 'لم يتم رفع أي وثائق بعد',
            'policy.deleteConfirm': 'حذف هذه الوثيقة؟',
            'policy.stats.documents': 'الوثائق',
            'policy.stats.chunks': 'الأجزاء المفهرسة',
            'policy.stats.lastIngestion': 'آخر معالجة',
            'policy.stats.never': 'لم تتم بعد',
            'policy.uploadSuccess': 'تم رفع الوثيقة بنجاح',
            'policy.deleteSuccess': 'تم حذف الوثيقة',
            'policy.ingestSuccess': 'تمت المعالجة بنجاح!'
        }
    },
    
    /**
     * Initialize UI
     */
    init() {
        // Load saved language preference
        const savedLang = localStorage.getItem('language') || 'en';
        this.setLanguage(savedLang);
        
        // Set current date in summary
        this.updateSummaryDate();
    },
    
    /**
     * Get translated string
     */
    t(key) {
        return this.i18n[this.currentLanguage][key] || this.i18n['en'][key] || key;
    },
    
    /**
     * Set language
     */
    setLanguage(lang) {
        this.currentLanguage = lang;
        localStorage.setItem('language', lang);
        
        // Update document direction
        document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
        document.documentElement.lang = lang;
        
        // Update language toggle button
        const langToggle = document.getElementById('currentLang');
        if (langToggle) {
            langToggle.textContent = lang === 'ar' ? 'ع' : 'EN';
        }
        
        // Update all i18n elements
        this.updateI18n();
    },
    
    /**
     * Toggle language
     */
    toggleLanguage() {
        const newLang = this.currentLanguage === 'en' ? 'ar' : 'en';
        this.setLanguage(newLang);
    },
    
    /**
     * Update all i18n elements in the DOM
     */
    updateI18n() {
        // Update text content
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            el.textContent = this.t(key);
        });
        
        // Update placeholders
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            el.placeholder = this.t(key);
        });
    },
    
    /**
     * Update summary date
     */
    updateSummaryDate() {
        const dateEl = document.querySelector('.summary-date');
        if (dateEl) {
            const options = { 
                weekday: 'long', 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
            };
            const locale = this.currentLanguage === 'ar' ? 'ar-SA' : 'en-US';
            dateEl.textContent = new Date().toLocaleDateString(locale, options);
        }
    },
    
    /**
     * Show toast notification
     */
    showToast(message, type = 'info', duration = 3000) {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        
        container.appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = 'toast-in 0.3s ease reverse';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },
    
    /**
     * Show sidebar
     */
    showSidebar() {
        document.getElementById('sidebar').classList.add('open');
        document.getElementById('overlay').classList.add('visible');
    },
    
    /**
     * Hide sidebar
     */
    hideSidebar() {
        document.getElementById('sidebar').classList.remove('open');
        document.getElementById('overlay').classList.remove('visible');
    },
    
    /**
     * Switch view
     */
    switchView(viewName) {
        // Hide all views
        document.querySelectorAll('.view').forEach(view => {
            view.classList.remove('active');
        });
        
        // Show selected view
        const targetView = document.getElementById(`${viewName}View`);
        if (targetView) {
            targetView.classList.add('active');
        }
        
        // Update nav items
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('active');
            if (item.dataset.view === viewName) {
                item.classList.add('active');
            }
        });
        
        // Hide sidebar on mobile
        this.hideSidebar();
    },
    
    /**
     * Show voice modal
     */
    showVoiceModal() {
        document.getElementById('voiceModal').classList.add('visible');
    },
    
    /**
     * Hide voice modal
     */
    hideVoiceModal() {
        document.getElementById('voiceModal').classList.remove('visible');
    },
    
    /**
     * Update voice modal status
     */
    setVoiceStatus(status) {
        const statusEl = document.querySelector('.voice-status');
        if (statusEl) {
            statusEl.textContent = this.t(`voice.${status}`) || status;
        }
    },
    
    /**
     * Show the auth-expired / not-connected banner above chat.
     * @param {'expired'|'not_connected'} reason
     */
    showAuthBanner(reason = 'expired') {
        const banner = document.getElementById('authBanner');
        if (!banner) return;
        const textEl = banner.querySelector('.auth-banner-text');
        if (textEl) {
            textEl.textContent = reason === 'expired'
                ? this.t('auth.expired')
                : this.t('auth.notConnected');
        }
        const btn = banner.querySelector('.auth-banner-btn');
        if (btn) btn.textContent = this.t('auth.reconnect');
        banner.classList.remove('hidden');
    },

    hideAuthBanner() {
        const banner = document.getElementById('authBanner');
        if (banner) banner.classList.add('hidden');
    },

    /**
     * Show auth modal with device code
     */
    showAuthModal(code, verificationUri, expiresIn = 900) {
        const modal = document.getElementById('authModal');
        const codeEl = document.getElementById('authCode');
        const linkEl = document.getElementById('authLink');
        const statusContainer = document.getElementById('authStatusContainer');

        codeEl.textContent = code;
        linkEl.href = verificationUri;

        statusContainer.innerHTML = `
            <div class="auth-waiting">
                <div class="spinner"></div>
                <span>${this.t('auth.waiting')}</span>
                <span class="auth-timer" id="authTimer"></span>
            </div>
        `;

        if (this._authTimerInterval) clearInterval(this._authTimerInterval);
        let remaining = expiresIn;
        const timerEl = document.getElementById('authTimer');
        const tick = () => {
            if (remaining <= 0) {
                clearInterval(this._authTimerInterval);
                this._authTimerInterval = null;
                statusContainer.innerHTML = `
                    <div class="auth-expired-notice">
                        <span>${this.t('auth.codeExpired')}</span>
                        <button class="auth-retry-btn" id="authRetryBtn">${this.t('auth.retry')}</button>
                    </div>
                `;
                document.getElementById('authRetryBtn')?.addEventListener('click', () => {
                    if (typeof App !== 'undefined') {
                        this.hideAuthModal();
                        App.isAuthenticated = false;
                        App.connectOutlook();
                    }
                });
                return;
            }
            const m = Math.floor(remaining / 60);
            const s = remaining % 60;
            if (timerEl) timerEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
            remaining--;
        };
        tick();
        this._authTimerInterval = setInterval(tick, 1000);

        modal.classList.add('visible');
    },
    
    /**
     * Hide auth modal
     */
    hideAuthModal() {
        if (this._authTimerInterval) {
            clearInterval(this._authTimerInterval);
            this._authTimerInterval = null;
        }
        document.getElementById('authModal').classList.remove('visible');
    },
    
    /**
     * Update auth modal to show success
     */
    showAuthSuccess() {
        const statusContainer = document.getElementById('authStatusContainer');
        statusContainer.innerHTML = `
            <div class="auth-success">
                <svg viewBox="0 0 24 24" width="24" height="24">
                    <path fill="currentColor" d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                </svg>
                <span>${this.t('auth.success')}</span>
            </div>
        `;
    },
    
    /**
     * Add message to chat
     */
    addChatMessage(content, isUser = false) {
        const chatMessages = document.getElementById('chatMessages');
        
        const messageRow = document.createElement('div');
        messageRow.className = `message-row ${isUser ? 'user' : 'assistant'}`;

        let thinkHtml = '';
        let cleanContent = content;
        if (!isUser) {
            const thinkMatch = content.match(/<think>([\s\S]*?)<\/think>/);
            if (thinkMatch) {
                const escapedThink = this.escapeHtml(thinkMatch[1].trim());
                thinkHtml = `<details class="thinking-block"><summary class="thinking-summary"><span class="thinking-icon">💭</span> Thought process</summary><pre class="thinking-content">${escapedThink}</pre></details>`;
                cleanContent = content.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
            }
        }

        const formattedContent = isUser ? this.escapeHtml(cleanContent) : this.formatAssistantMessage(cleanContent);
        
        if (!isUser) {
            messageRow.innerHTML = `
                <div class="assistant-avatar">🤖</div>
                <div class="message-bubble assistant">
                    ${thinkHtml}
                    <div class="message-content">${formattedContent}</div>
                    <button class="speak-msg-btn" aria-label="Listen" title="Listen">
                        <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
                    </button>
                </div>
            `;
            messageRow._rawContent = cleanContent;
        } else {
            messageRow.innerHTML = `
                <div class="message-bubble user">
                    <p>${formattedContent}</p>
                </div>
            `;
        }
        
        chatMessages.appendChild(messageRow);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        if (!isUser) {
            const speakBtn = messageRow.querySelector('.speak-msg-btn');
            if (speakBtn) {
                speakBtn.addEventListener('click', () => {
                    if (typeof Voice !== 'undefined' && Voice.speak) {
                        Voice.cancelSpeech();
                        Voice.speak(Voice.cleanForTTS(cleanContent), this.currentLanguage);
                    }
                });
            }
        }
    },
    
    /**
     * Create an empty assistant message bubble for streaming.
     * Returns a handle object used by appendToStreamingMessage / finalizeStreamingMessage.
     */
    addStreamingMessage() {
        const chatMessages = document.getElementById('chatMessages');

        const messageRow = document.createElement('div');
        messageRow.className = 'message-row assistant';
        messageRow.innerHTML = `
            <div class="assistant-avatar">🤖</div>
            <div class="message-bubble assistant">
                <div class="message-content streaming-content"></div>
            </div>
        `;
        chatMessages.appendChild(messageRow);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        const contentEl = messageRow.querySelector('.message-content');
        return { messageRow, contentEl, _raw: '', _thinking: false, _thinkBuf: '' };
    },

    appendToStreamingMessage(handle, token) {
        handle._raw += token;
        handle.contentEl.textContent = handle._raw;
        const chatMessages = document.getElementById('chatMessages');
        chatMessages.scrollTop = chatMessages.scrollHeight;
    },

    startThinkingBlock(handle) {
        handle._thinking = true;
        handle._thinkBuf = '';
        let thinkEl = handle.messageRow.querySelector('.thinking-block');
        if (!thinkEl) {
            thinkEl = document.createElement('details');
            thinkEl.className = 'thinking-block';
            thinkEl.innerHTML = '<summary class="thinking-summary"><span class="thinking-icon">💭</span> Thinking…</summary><pre class="thinking-content"></pre>';
            handle.contentEl.parentNode.insertBefore(thinkEl, handle.contentEl);
        }
    },

    appendThinkingToken(handle, token) {
        handle._thinkBuf += token;
        const thinkContentEl = handle.messageRow.querySelector('.thinking-content');
        if (thinkContentEl) {
            thinkContentEl.textContent = handle._thinkBuf;
        }
        const chatMessages = document.getElementById('chatMessages');
        chatMessages.scrollTop = chatMessages.scrollHeight;
    },

    endThinkingBlock(handle) {
        handle._thinking = false;
        const summary = handle.messageRow.querySelector('.thinking-summary');
        if (summary) {
            summary.innerHTML = '<span class="thinking-icon">💭</span> Thought process';
        }
    },

    clearStreamingMessage(handle) {
        handle._raw = '';
        handle._thinkBuf = '';
        handle.contentEl.textContent = '';
        const thinkEl = handle.messageRow.querySelector('.thinking-block');
        if (thinkEl) thinkEl.remove();
    },

    /**
     * Finalize a streaming message: replace raw text with fully formatted HTML, add speak button.
     */
    finalizeStreamingMessage(handle, fullContent) {
        const raw = fullContent || handle._raw;
        const thinkContent = handle._thinkBuf || '';

        const cleanRaw = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
        const formatted = this.formatAssistantMessage(cleanRaw);

        let thinkHtml = '';
        if (thinkContent.trim()) {
            const escapedThink = this.escapeHtml(thinkContent.trim());
            thinkHtml = `<details class="thinking-block"><summary class="thinking-summary"><span class="thinking-icon">💭</span> Thought process</summary><pre class="thinking-content">${escapedThink}</pre></details>`;
        }

        handle.messageRow.querySelector('.message-bubble').innerHTML = `
            ${thinkHtml}
            <div class="message-content">${formatted}</div>
            <button class="speak-msg-btn" aria-label="Listen" title="Listen">
                <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
            </button>
        `;
        handle.messageRow._rawContent = cleanRaw;

        const speakBtn = handle.messageRow.querySelector('.speak-msg-btn');
        if (speakBtn) {
            speakBtn.addEventListener('click', () => {
                if (typeof Voice !== 'undefined' && Voice.speak) {
                    Voice.cancelSpeech();
                    Voice.speak(Voice.cleanForTTS(cleanRaw), this.currentLanguage);
                }
            });
        }

        const chatMessages = document.getElementById('chatMessages');
        chatMessages.scrollTop = chatMessages.scrollHeight;
    },

    /**
     * Strip markdown/formatting characters for clean TTS reading
     */
    stripMarkdownForTTS(text) {
        let clean = text;
        // Remove code block fences and their content markers
        clean = clean.replace(/```[\s\S]*?```/g, '');
        // Remove inline code backticks
        clean = clean.replace(/`([^`]+)`/g, '$1');
        // Remove images before links
        clean = clean.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
        // Remove links, keep text: [text](url) -> text
        clean = clean.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
        // Remove markdown headers (# ## ### etc.)
        clean = clean.replace(/^#{1,6}\s*/gm, '');
        // Remove bold markers (** or __)
        clean = clean.replace(/\*\*([^*]+)\*\*/g, '$1');
        clean = clean.replace(/__([^_]+)__/g, '$1');
        // Remove italic markers (* or _)
        clean = clean.replace(/\*([^*]+)\*/g, '$1');
        clean = clean.replace(/(?<!\w)_([^_]+)_(?!\w)/g, '$1');
        // Remove strikethrough
        clean = clean.replace(/~~([^~]+)~~/g, '$1');
        // Clean any remaining stray asterisks used as formatting
        clean = clean.replace(/\*+/g, '');
        // Clean any remaining stray underscores between words
        clean = clean.replace(/(?<=\s)_+|_+(?=\s)/g, '');
        // Remove horizontal rules
        clean = clean.replace(/^[-*_]{3,}\s*$/gm, '');
        // Remove blockquote markers
        clean = clean.replace(/^>\s*/gm, '');
        // Clean bullet/dash markers at line start
        clean = clean.replace(/^\s*[-*+]\s+/gm, '');
        // Clean numbered list markers but keep the number
        clean = clean.replace(/^(\s*\d+)\.\s+/gm, '$1: ');
        // Remove hash symbols that may remain
        clean = clean.replace(/#/g, '');
        // Collapse multiple blank lines
        clean = clean.replace(/\n{3,}/g, '\n\n');
        // Clean up multiple spaces
        clean = clean.replace(/  +/g, ' ');
        return clean.trim();
    },

    /**
     * Format assistant message with proper structure
     */
    formatAssistantMessage(content) {
        let formatted = this.escapeHtml(content);
        
        // Convert ### headers (do before bold/italic to avoid conflicts)
        formatted = formatted.replace(/^###\s+(.+)$/gm, '<h4 class="msg-heading">$1</h4>');
        formatted = formatted.replace(/^##\s+(.+)$/gm, '<h3 class="msg-heading">$1</h3>');
        formatted = formatted.replace(/^#\s+(.+)$/gm, '<h3 class="msg-heading msg-heading-lg">$1</h3>');
        
        // Convert **text** to bold
        formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // Convert *text* to italic
        formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // Convert numbered lists (1. 2. 3. etc) - handle "Email 1:" pattern
        formatted = formatted.replace(/(\d+)\.\s*<strong>([^<]+)<\/strong>/g, '<div class="list-item"><span class="item-number">$1.</span> <strong>$2</strong></div>');
        
        // Convert bullet points with dashes
        formatted = formatted.replace(/^- (.+)$/gm, '<div class="bullet-item">• $1</div>');
        formatted = formatted.replace(/\n- /g, '<div class="bullet-item">• ');
        
        // Convert sections with colons to structured format
        formatted = formatted.replace(/- (From|Subject|Date|Key Points|Urgency|Status|Priority|Description|Due|Time|Location|Attendees|Organizer|Teams Link|Action|Response Needed):\s*/gi, '<br><span class="label">$1:</span> ');
        
        // Handle newlines - convert to proper line breaks
        formatted = formatted.replace(/\n\n/g, '</p><p>');
        formatted = formatted.replace(/\n/g, '<br>');
        
        // Wrap in paragraph if not already structured
        if (!formatted.includes('<div') && !formatted.includes('<h3') && !formatted.includes('<h4') && !formatted.includes('<p>')) {
            formatted = `<p>${formatted}</p>`;
        } else if (!formatted.startsWith('<')) {
            formatted = `<p>${formatted}</p>`;
        }
        
        // Clean up empty paragraphs
        formatted = formatted.replace(/<p>\s*<\/p>/g, '');
        formatted = formatted.replace(/<p><br>/g, '<p>');
        formatted = formatted.replace(/<br><\/p>/g, '</p>');
        
        return formatted;
    },
    
    /**
     * Add loading message
     */
    addLoadingMessage() {
        const chatMessages = document.getElementById('chatMessages');
        
        const messageRow = document.createElement('div');
        messageRow.className = 'message-row assistant loading-message';
        messageRow.innerHTML = `
            <div class="assistant-avatar">🤖</div>
            <div class="message-bubble assistant">
                <div class="spinner"></div>
            </div>
        `;
        
        chatMessages.appendChild(messageRow);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        return messageRow;
    },
    
    /**
     * Remove loading message
     */
    removeLoadingMessage() {
        const loading = document.querySelector('.loading-message');
        if (loading) loading.remove();
    },
    
    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
    
    /**
     * Render email list
     */
    renderEmails(emails) {
        const emailList = document.getElementById('emailList');
        
        if (!emails || emails.length === 0) {
            emailList.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" width="64" height="64">
                        <path fill="currentColor" d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
                    </svg>
                    <p>${this.t('emails.connect')}</p>
                </div>
            `;
            return;
        }
        
        emailList.innerHTML = emails.map(email => `
            <div class="email-item" data-id="${email.id}">
                <div class="email-header">
                    <span class="email-sender">${this.escapeHtml(email.sender_name)}</span>
                    <span class="email-time">${this.formatTime(email.received_at)}</span>
                </div>
                <div class="email-subject">${this.escapeHtml(email.subject)}</div>
                <div class="email-preview">${this.escapeHtml(email.body_preview)}</div>
                ${email.urgency ? `
                    <div class="email-badges">
                        <span class="badge urgency-${email.urgency}">${email.urgency}</span>
                    </div>
                ` : ''}
            </div>
        `).join('');
    },
    
    /**
     * Render task list
     */
    renderTasks(tasks) {
        const taskList = document.getElementById('taskList');
        
        if (!tasks || tasks.length === 0) {
            taskList.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" width="64" height="64">
                        <path fill="currentColor" d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                    </svg>
                    <p>${this.t('tasks.empty')}</p>
                </div>
            `;
            return;
        }
        
        taskList.innerHTML = tasks.map(task => `
            <div class="task-item" data-id="${task.id}">
                <div class="task-checkbox ${task.status === 'completed' ? 'checked' : ''}"></div>
                <div class="task-content">
                    <div class="task-title">${this.escapeHtml(task.title)}</div>
                    <div class="task-meta">
                        ${task.priority} priority
                        ${task.due_date ? ` • Due ${this.formatDate(task.due_date)}` : ''}
                    </div>
                </div>
                ${task.status === 'pending_approval' ? `
                    <div class="task-actions">
                        <button class="action-btn approve" data-action="approve">Approve</button>
                        <button class="action-btn reject" data-action="reject">Reject</button>
                    </div>
                ` : ''}
            </div>
        `).join('');
    },
    
    /**
     * Render calendar events
     */
    renderCalendarEvents(events) {
        const list = document.getElementById('calendarEventList');
        if (!list) return;
        
        // Update date header
        const dateEl = document.getElementById('calendarDate');
        if (dateEl) {
            const locale = this.currentLanguage === 'ar' ? 'ar-SA' : 'en-US';
            dateEl.textContent = new Date().toLocaleDateString(locale, {
                weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
            });
        }
        
        if (!events || events.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" width="64" height="64">
                        <path fill="currentColor" d="M17 12h-5v5h5v-5zM16 1v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2h-1V1h-2zm3 18H5V8h14v11z"/>
                    </svg>
                    <p>${this.t('calendar.noEvents')}</p>
                </div>
            `;
            return;
        }
        
        list.innerHTML = events.map(ev => {
            const start = ev.start_time ? new Date(ev.start_time) : null;
            const end = ev.end_time ? new Date(ev.end_time) : null;
            const startStr = start ? start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
            const endStr = end ? end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
            
            const status = ev.status || 'none';
            const attendees = ev.attendees ? JSON.parse(ev.attendees) : [];
            
            let teamsLink = '';
            if (ev.is_online && ev.online_meeting_url) {
                teamsLink = `<a href="${this.escapeHtml(ev.online_meeting_url)}" target="_blank" class="event-teams-link">
                    <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19 19H5V5h7V3H5a2 2 0 00-2 2v14a2 2 0 002 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>
                    ${this.t('calendar.join')}
                </a>`;
            }
            
            const timeBadge = ev.is_all_day
                ? `<div class="event-time-badge all-day"><span class="time-start">${this.t('calendar.allDay')}</span></div>`
                : `<div class="event-time-badge"><span class="time-start">${startStr}</span><span class="time-end">${endStr}</span></div>`;
            
            const locationHtml = ev.location
                ? `<span class="event-meta-item"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z"/></svg>${this.escapeHtml(ev.location)}</span>`
                : '';
            
            const attendeeHtml = attendees.length
                ? `<span class="event-meta-item"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>${attendees.length}</span>`
                : '';
            
            return `
                <div class="event-card" data-id="${ev.id}">
                    <div class="event-status-indicator ${status}"></div>
                    ${timeBadge}
                    <div class="event-details">
                        <div class="event-subject">${this.escapeHtml(ev.subject)}</div>
                        <div class="event-meta">
                            ${locationHtml}
                            ${attendeeHtml}
                        </div>
                        ${teamsLink}
                    </div>
                </div>
            `;
        }).join('');
    },
    
    showCreateEventModal() {
        const modal = document.getElementById('createEventModal');
        if (modal) {
            modal.classList.add('visible');
            // Set default start time to next hour
            const now = new Date();
            now.setMinutes(0, 0, 0);
            now.setHours(now.getHours() + 1);
            const end = new Date(now.getTime() + 60 * 60 * 1000);
            
            const fmt = d => d.toISOString().slice(0, 16);
            document.getElementById('eventStart').value = fmt(now);
            document.getElementById('eventEnd').value = fmt(end);
        }
    },
    
    hideCreateEventModal() {
        const modal = document.getElementById('createEventModal');
        if (modal) {
            modal.classList.remove('visible');
            document.getElementById('createEventForm').reset();
            document.getElementById('selectedAttendees').innerHTML = '';
            document.getElementById('contactDropdown').classList.remove('visible');
        }
    },
    
    /**
     * Update summary stats
     */
    updateSummaryStats(stats) {
        document.getElementById('pendingCount').textContent = stats.pending || 0;
        document.getElementById('activeCount').textContent = stats.active || 0;
        document.getElementById('completedCount').textContent = stats.completed || 0;
    },
    
    /**
     * Format time for display
     */
    formatTime(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diff = now - date;
        
        // Less than 24 hours ago
        if (diff < 86400000) {
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        
        // Less than 7 days ago
        if (diff < 604800000) {
            return date.toLocaleDateString([], { weekday: 'short' });
        }
        
        return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    },
    
    /**
     * Format date for display
     */
    formatDate(dateString) {
        const date = new Date(dateString);
        return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    },
    
    // ============ Hands-Free Mode UI ============
    
    /**
     * Show hands-free mode overlay
     */
    showHandsFreeOverlay() {
        const overlay = document.getElementById('handsFreeOverlay');
        if (overlay) {
            overlay.classList.add('visible');
            this.setHandsFreeStatus('listening');
        }
    },
    
    /**
     * Hide hands-free mode overlay
     */
    hideHandsFreeOverlay() {
        const overlay = document.getElementById('handsFreeOverlay');
        if (overlay) {
            overlay.classList.remove('visible');
        }
    },
    
    /**
     * Set hands-free status display
     */
    setHandsFreeStatus(status) {
        const statusText = document.getElementById('handsFreeStatus');
        const micIcon = document.querySelector('.handsfree-mic');
        
        if (statusText) {
            statusText.textContent = this.t(`handsfree.${status}`) || status;
        }
        
        if (micIcon) {
            // Remove all status classes
            micIcon.classList.remove('listening', 'processing', 'thinking', 'speaking', 'error');
            // Add current status class
            micIcon.classList.add(status);
        }
    },
    
    // ============ Live Transcript Functions ============
    
    /**
     * Update live transcript display
     */
    updateLiveTranscript(text, type = 'user') {
        const transcriptEl = document.getElementById('liveTranscript');
        if (!transcriptEl) return;
        
        if (!text || text.trim() === '') {
            transcriptEl.innerHTML = '';
            transcriptEl.classList.remove('has-content');
            transcriptEl.style.display = 'none';
            return;
        }
        
        const className = type === 'user' ? 'user-text' : 'assistant-text';
        const icon = type === 'user' ? '🎤' : '🤖';
        const label = type === 'user' ? 'You said:' : 'Assistant:';
        
        transcriptEl.innerHTML = `<span class="${className}">${icon} <strong>${label}</strong> ${this.escapeHtml(text)}</span>`;
        transcriptEl.classList.add('has-content');
        transcriptEl.style.display = 'block';
        
        // Auto-scroll if content is long
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
    },
    
    /**
     * Show typing indicator in live transcript
     */
    showLiveTypingIndicator() {
        const transcriptEl = document.getElementById('liveTranscript');
        if (!transcriptEl) return;
        
        transcriptEl.innerHTML = `<span class="user-text">🎤 Listening... <span class="typing-indicator">●●●</span></span>`;
        transcriptEl.classList.add('has-content');
        transcriptEl.style.display = 'block';
    },
    
    /**
     * Clear live transcript
     */
    clearLiveTranscript() {
        const transcriptEl = document.getElementById('liveTranscript');
        if (transcriptEl) {
            transcriptEl.innerHTML = '';
            transcriptEl.classList.remove('has-content');
            transcriptEl.style.display = 'none';
        }
    },
    
    /**
     * Append to live transcript (for streaming responses)
     */
    appendLiveTranscript(text, type = 'assistant') {
        const transcriptEl = document.getElementById('liveTranscript');
        if (!transcriptEl) return;
        
        const existingText = transcriptEl.textContent || '';
        const icon = type === 'user' ? '🎤' : '🤖';
        const className = type === 'user' ? 'user-text' : 'assistant-text';
        
        // If there's existing user text, show it above the assistant response
        transcriptEl.innerHTML = `<span class="${className}">${icon} ${this.escapeHtml(text)}</span>`;
        transcriptEl.classList.add('has-content');
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
    },
    
    // ============ Policy Documents UI ============
    
    renderPolicyDocuments(fileDetails) {
        const list = document.getElementById('policyDocList');
        if (!list) return;
        
        if (!fileDetails || fileDetails.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" width="64" height="64">
                        <path fill="currentColor" d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/>
                    </svg>
                    <p>${this.t('policy.noDocuments')}</p>
                </div>
            `;
            return;
        }
        
        list.innerHTML = fileDetails.map(f => {
            const sizeStr = f.size_bytes < 1024
                ? f.size_bytes + ' B'
                : f.size_bytes < 1048576
                    ? (f.size_bytes / 1024).toFixed(1) + ' KB'
                    : (f.size_bytes / 1048576).toFixed(1) + ' MB';
            
            return `
                <div class="policy-doc-item" data-filename="${this.escapeHtml(f.name)}">
                    <div class="policy-doc-icon ${this.escapeHtml(f.type)}">${this.escapeHtml(f.type)}</div>
                    <div class="policy-doc-info">
                        <div class="policy-doc-name" title="${this.escapeHtml(f.name)}">${this.escapeHtml(f.name)}</div>
                        <div class="policy-doc-size">${sizeStr}</div>
                    </div>
                    <button class="policy-doc-delete" data-name="${this.escapeHtml(f.name)}" title="Delete">
                        <svg viewBox="0 0 24 24" width="18" height="18">
                            <path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                    </button>
                </div>
            `;
        }).join('');
    },
    
    updatePolicyStats(status) {
        const docCount = document.getElementById('policyDocCount');
        const chunkCount = document.getElementById('policyChunkCount');
        const lastIngestion = document.getElementById('policyLastIngestion');
        
        if (docCount) docCount.textContent = status.document_count || 0;
        if (chunkCount) chunkCount.textContent = status.indexed_chunks || 0;
        if (lastIngestion) {
            if (status.last_ingestion) {
                const d = new Date(status.last_ingestion);
                const locale = this.currentLanguage === 'ar' ? 'ar-SA' : 'en-US';
                lastIngestion.textContent = d.toLocaleString(locale, {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                });
            } else {
                lastIngestion.textContent = this.t('policy.stats.never');
            }
        }
    },
    
    setPolicyIngestProgress(state, result) {
        const btn = document.getElementById('ingestBtn');
        const resultEl = document.getElementById('ingestResult');
        if (!btn) return;
        
        switch (state) {
            case 'loading':
                btn.disabled = true;
                btn.innerHTML = `<div class="spinner"></div> ${this.t('policy.ingesting')}`;
                if (resultEl) { resultEl.textContent = ''; resultEl.className = 'ingest-result'; }
                break;
            case 'success':
                btn.disabled = false;
                btn.textContent = this.t('policy.ingest');
                if (resultEl && result) {
                    resultEl.className = 'ingest-result success';
                    resultEl.textContent = `${this.t('policy.ingestSuccess')} ${result.chunks} chunks from ${result.documents} file(s)`;
                }
                break;
            case 'error':
                btn.disabled = false;
                btn.textContent = this.t('policy.ingest');
                if (resultEl) {
                    resultEl.className = 'ingest-result error';
                    resultEl.textContent = result || 'Ingestion failed';
                }
                break;
            default:
                btn.disabled = false;
                btn.textContent = this.t('policy.ingest');
                if (resultEl) { resultEl.textContent = ''; resultEl.className = 'ingest-result'; }
        }
    }
};

// Export for use in other modules
window.UI = UI;
