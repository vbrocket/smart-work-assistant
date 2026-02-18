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
    async speak(text, language = 'en') {
        const response = await fetch(`${this.baseUrl}/voice/speak`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, language })
        });
        
        if (!response.ok) {
            throw new Error('TTS failed');
        }
        
        return response.blob();
    },
    
    /**
     * Send chat message
     */
    async chat(message, language = 'en') {
        return this.request('/voice/chat', {
            method: 'POST',
            body: JSON.stringify({ message, language })
        });
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
    
    // ============ Summary Endpoints ============
    
    /**
     * Get daily summary
     */
    async getDailySummary() {
        return this.request('/summary/daily');
    }
};

// Export for use in other modules
window.API = API;
