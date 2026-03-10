/**
 * API Service - Handles all backend communication
 */
const API = {
    baseUrl: '/api',
    
    /**
     * Make an API request
     */
    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        };
        
        try {
            const response = await fetch(url, config);
            
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || error.message || `HTTP ${response.status}`);
            }
            
            // Handle empty responses
            const text = await response.text();
            return text ? JSON.parse(text) : null;
        } catch (error) {
            if (error.message === 'Failed to fetch') {
                throw new Error('Network error - please check your connection');
            }
            throw error;
        }
    },
    
    /**
     * Health check
     */
    async healthCheck() {
        return this.request('/health');
    },
    
    // ============ Voice Endpoints ============
    
    /**
     * Transcribe audio to text
     */
    async transcribe(audioBlob, language = null) {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'recording.webm');
        if (language) {
            formData.append('language', language);
        }
        
        const response = await fetch(`${this.baseUrl}/voice/transcribe`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Transcription failed');
        }
        
        return response.json();
    },
    
    /**
     * Text to speech
     */
    async speak(text, language = 'en', gender = 'male') {
        const response = await fetch(`${this.baseUrl}/voice/speak`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, language, gender })
        });
        
        if (response.status === 204) {
            return new Blob();
        }
        if (!response.ok) {
            throw new Error('TTS failed');
        }
        
        return response.blob();
    },
    
    /**
     * Send chat message (non-streaming)
     */
    async chat(message, language = 'en') {
        return this.request('/voice/chat', {
            method: 'POST',
            body: JSON.stringify({ message, language })
        });
    },

    /**
     * Stream chat response via SSE.
     * @param {string} message
     * @param {string} language
     * @param {object} callbacks  { onRoute(intent), onToken(text), onCitations(arr,refsText), onDone(fullResp,lang), onError(msg), onClear() }
     * @param {object} opts  { voiceMode: boolean }
     * @returns {AbortController} – call .abort() to cancel
     */
    chatStream(message, language = 'en', callbacks = {}, opts = {}) {
        const controller = new AbortController();
        const url = `${this.baseUrl}/voice/chat/stream`;

        (async () => {
            try {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message,
                        language,
                        voice_mode: !!opts.voiceMode,
                    }),
                    signal: controller.signal,
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const jsonStr = line.slice(6);
                        let evt;
                        try { evt = JSON.parse(jsonStr); } catch { continue; }

                        switch (evt.type) {
                            case 'route':
                                callbacks.onRoute?.(evt.intent);
                                break;
                            case 'token':
                                callbacks.onToken?.(evt.content);
                                break;
                            case 'thinking_start':
                                callbacks.onThinkingStart?.();
                                break;
                            case 'thinking':
                                callbacks.onThinking?.(evt.content);
                                break;
                            case 'thinking_end':
                                callbacks.onThinkingEnd?.();
                                break;
                            case 'citations':
                                callbacks.onCitations?.(evt.citations, evt.refs_text);
                                break;
                            case 'done':
                                callbacks.onDone?.(evt.full_response, evt.language);
                                break;
                            case 'clear':
                                callbacks.onClear?.();
                                break;
                            case 'auth_required':
                                callbacks.onAuthRequired?.();
                                break;
                            case 'error':
                                callbacks.onError?.(evt.message);
                                break;
                        }
                    }
                }
            } catch (err) {
                if (err.name !== 'AbortError') {
                    callbacks.onError?.(err.message);
                }
            }
        })();

        return controller;
    },
    
    // ============ Email Endpoints ============
    
    /**
     * Start OAuth device code flow
     */
    async startDeviceCodeFlow() {
        return this.request('/emails/auth/device-code');
    },
    
    /**
     * Check authentication status
     */
    async getAuthStatus() {
        return this.request('/emails/auth/status');
    },
    
    /**
     * Complete device code flow (triggers backend to wait for auth)
     */
    async completeDeviceCodeFlow() {
        return this.request('/emails/auth/complete', { method: 'POST' });
    },
    
    /**
     * Disconnect from Outlook
     */
    async disconnectOutlook() {
        return this.request('/emails/auth/logout', { method: 'DELETE' });
    },
    
    /**
     * Get emails
     */
    async getEmails(limit = 20, skip = 0, unreadOnly = false) {
        const params = new URLSearchParams({
            limit: limit.toString(),
            skip: skip.toString(),
            unread_only: unreadOnly.toString()
        });
        return this.request(`/emails/?${params}`);
    },
    
    /**
     * Get single email
     */
    async getEmail(emailId) {
        return this.request(`/emails/${emailId}`);
    },
    
    /**
     * Summarize email
     */
    async summarizeEmail(emailId) {
        return this.request(`/emails/${emailId}/summarize`, { method: 'POST' });
    },
    
    /**
     * Extract tasks from email
     */
    async extractTasks(emailId) {
        return this.request(`/emails/${emailId}/extract-tasks`, { method: 'POST' });
    },
    
    /**
     * Draft reply
     */
    async draftReply(emailId, tone = 'formal', language = 'en', additionalContext = null) {
        return this.request(`/emails/${emailId}/draft-reply`, {
            method: 'POST',
            body: JSON.stringify({ tone, language, additional_context: additionalContext })
        });
    },
    
    /**
     * Send reply
     */
    async sendReply(emailId) {
        return this.request(`/emails/${emailId}/send`, { method: 'POST' });
    },
    
    // ============ Task Endpoints ============
    
    /**
     * Get tasks
     */
    async getTasks(status = null, priority = null, limit = 50, skip = 0) {
        const params = new URLSearchParams({ limit: limit.toString(), skip: skip.toString() });
        if (status) params.append('status', status);
        if (priority) params.append('priority', priority);
        return this.request(`/tasks/?${params}`);
    },
    
    /**
     * Get pending tasks
     */
    async getPendingTasks() {
        return this.request('/tasks/pending');
    },
    
    /**
     * Create task
     */
    async createTask(title, description = null, priority = 'medium', dueDate = null) {
        return this.request('/tasks', {
            method: 'POST',
            body: JSON.stringify({ title, description, priority, due_date: dueDate })
        });
    },
    
    /**
     * Approve task
     */
    async approveTask(taskId) {
        return this.request(`/tasks/${taskId}/approve`, { method: 'POST' });
    },
    
    /**
     * Reject task
     */
    async rejectTask(taskId) {
        return this.request(`/tasks/${taskId}/reject`, { method: 'POST' });
    },
    
    /**
     * Complete task
     */
    async completeTask(taskId) {
        return this.request(`/tasks/${taskId}/complete`, { method: 'POST' });
    },
    
    /**
     * Delete task
     */
    async deleteTask(taskId) {
        return this.request(`/tasks/${taskId}`, { method: 'DELETE' });
    },
    
    // ============ Calendar Endpoints ============
    
    async getCalendarEvents(date = null) {
        const params = new URLSearchParams();
        if (date) params.set('date', date);
        params.set('sync', 'true');
        return this.request(`/calendar/?${params.toString()}`);
    },
    
    async getEventDetail(eventId) {
        return this.request(`/calendar/${eventId}`);
    },
    
    async createCalendarEvent(data) {
        return this.request('/calendar/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
    },
    
    // ============ Contacts Endpoints ============
    
    async getContacts(search = null) {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        return this.request(`/contacts/?${params.toString()}`);
    },
    
    async syncContacts() {
        return this.request('/contacts/sync', { method: 'POST' });
    },
    
    // ============ Policy / RAG Endpoints ============
    
    async uploadPolicyDoc(file) {
        const formData = new FormData();
        formData.append('file', file, file.name);
        
        const response = await fetch(`${this.baseUrl}/policy/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Upload failed');
        }
        
        return response.json();
    },
    
    async deletePolicyDoc(filename) {
        return this.request(`/policy/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    },
    
    async ingestPolicyDocs() {
        return this.request('/policy/ingest', { method: 'POST' });
    },
    
    async getPolicyStatus() {
        return this.request('/policy/status');
    },
    
    // ============ Summary Endpoints ============
    
    async getDailySummary() {
        return this.request('/summary/daily');
    }
};

// Export for use in other modules
window.API = API;
