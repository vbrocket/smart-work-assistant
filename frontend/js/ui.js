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
            'handsfree.title': 'Hands-Free Mode',
            'handsfree.listening': 'Listening...',
            'handsfree.processing': 'Processing...',
            'handsfree.thinking': 'Thinking...',
            'handsfree.speaking': 'Speaking...',
            'handsfree.error': 'Error occurred',
            'handsfree.exit': 'Exit',
            'handsfree.hint': 'Say "exit" or "stop" to end',
            'handsfree.start': 'Hands-Free'
        },
        ar: {
            'nav.chat': 'المحادثة',
            'nav.emails': 'البريد',
            'nav.tasks': 'المهام',
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
            'handsfree.title': 'وضع التحدث الحر',
            'handsfree.listening': 'جارٍ الاستماع...',
            'handsfree.processing': 'جارٍ المعالجة...',
            'handsfree.thinking': 'جارٍ التفكير...',
            'handsfree.speaking': 'جارٍ التحدث...',
            'handsfree.error': 'حدث خطأ',
            'handsfree.exit': 'خروج',
            'handsfree.hint': 'قل "توقف" أو "خروج" للإنهاء',
            'handsfree.start': 'تحدث حر'
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
     * Show auth modal with device code
     */
    showAuthModal(code, verificationUri) {
        const modal = document.getElementById('authModal');
        const codeEl = document.getElementById('authCode');
        const linkEl = document.getElementById('authLink');
        const statusContainer = document.getElementById('authStatusContainer');
        
        // Set the code and link
        codeEl.textContent = code;
        linkEl.href = verificationUri;
        
        // Reset status to waiting
        statusContainer.innerHTML = `
            <div class="auth-waiting">
                <div class="spinner"></div>
                <span>${this.t('auth.waiting')}</span>
            </div>
        `;
        
        modal.classList.add('visible');
    },
    
    /**
     * Hide auth modal
     */
    hideAuthModal() {
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
        
        // Format content for assistant messages
        const formattedContent = isUser ? this.escapeHtml(content) : this.formatAssistantMessage(content);
        
        if (!isUser) {
            messageRow.innerHTML = `
                <div class="assistant-avatar">🤖</div>
                <div class="message-bubble assistant">
                    <div class="message-content">${formattedContent}</div>
                </div>
            `;
        } else {
            messageRow.innerHTML = `
                <div class="message-bubble user">
                    <p>${formattedContent}</p>
                </div>
            `;
        }
        
        chatMessages.appendChild(messageRow);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    },
    
    /**
     * Format assistant message with proper structure
     */
    formatAssistantMessage(content) {
        let formatted = this.escapeHtml(content);
        
        // Convert **text** to bold
        formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // Convert *text* to italic
        formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // Convert numbered lists (1. 2. 3. etc) - handle "Email 1:" pattern
        formatted = formatted.replace(/(\d+)\.\s*\*\*([^*]+)\*\*/g, '<div class="list-item"><span class="item-number">$1.</span> <strong>$2</strong></div>');
        
        // Convert bullet points with dashes
        formatted = formatted.replace(/^- (.+)$/gm, '<div class="bullet-item">• $1</div>');
        formatted = formatted.replace(/\n- /g, '<div class="bullet-item">• ');
        
        // Convert sections with colons to structured format
        formatted = formatted.replace(/- (From|Subject|Date|Key Points|Urgency|Status|Priority|Description|Due):\s*/g, '<br><span class="label">$1:</span> ');
        
        // Handle newlines - convert to proper line breaks
        formatted = formatted.replace(/\n\n/g, '</p><p>');
        formatted = formatted.replace(/\n/g, '<br>');
        
        // Wrap in paragraph if not already structured
        if (!formatted.includes('<div') && !formatted.includes('<p>')) {
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
    }
};

// Export for use in other modules
window.UI = UI;
