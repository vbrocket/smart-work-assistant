/**
 * Smart Work Assistant - Main Application
 */
const App = {
    isAuthenticated: false,
    isHandsFreeMode: false,
    handsFreeTimeout: null,
    handsFreeTimeoutDuration: 30000, // 30 seconds of silence before auto-exit
    wakeLock: null,
    lastRecording: null, // Store last recording for playback
    
    // Exit commands in multiple languages
    exitCommands: [
        'stop', 'exit', 'goodbye', 'quit', 'end', 'cancel',
        'توقف', 'خروج', 'مع السلامة', 'انهاء', 'إنهاء', 'وقف'
    ],
    
    /**
     * Initialize the application
     */
    async init() {
        console.log('Initializing Smart Work Assistant...');
        
        // Register service worker
        this.registerServiceWorker();
        
        // Initialize UI
        UI.init();
        
        // Initialize Voice
        await Voice.init();
        
        // Set up event listeners
        this.setupEventListeners();
        
        // Check authentication status
        await this.checkAuthStatus();
        
        // Load initial data
        await this.loadInitialData();
        
        console.log('App initialized successfully');
    },
    
    /**
     * Register service worker for PWA
     */
    async registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            try {
                const registration = await navigator.serviceWorker.register('/sw.js');
                console.log('ServiceWorker registered:', registration.scope);
                
                // Handle updates
                registration.addEventListener('updatefound', () => {
                    const newWorker = registration.installing;
                    newWorker.addEventListener('statechange', () => {
                        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                            UI.showToast('New version available! Refresh to update.', 'info', 5000);
                        }
                    });
                });
            } catch (error) {
                console.error('ServiceWorker registration failed:', error);
            }
        }
    },
    
    /**
     * Set up event listeners
     */
    setupEventListeners() {
        // Menu button
        document.getElementById('menuBtn').addEventListener('click', () => {
            UI.showSidebar();
        });
        
        // Overlay click
        document.getElementById('overlay').addEventListener('click', () => {
            UI.hideSidebar();
        });
        
        // Navigation items
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                const view = item.dataset.view;
                UI.switchView(view);
            });
        });
        
        // Language toggle
        document.getElementById('langToggle').addEventListener('click', () => {
            UI.toggleLanguage();
            UI.updateSummaryDate();
        });
        
        // Chat input
        const chatInput = document.getElementById('chatInput');
        const sendBtn = document.getElementById('sendBtn');
        
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        sendBtn.addEventListener('click', () => {
            this.sendMessage();
        });
        
        // Voice button
        document.getElementById('voiceBtn').addEventListener('click', () => {
            this.startVoiceInput();
        });
        
        // Stop recording button
        document.getElementById('stopRecording').addEventListener('click', () => {
            this.stopVoiceInput();
        });
        
        // Connect Outlook button
        document.getElementById('connectOutlook').addEventListener('click', () => {
            this.connectOutlook();
        });
        
        // Auth modal close button
        document.getElementById('closeAuthModal').addEventListener('click', () => {
            UI.hideAuthModal();
        });
        
        // Copy code button
        document.getElementById('copyCodeBtn').addEventListener('click', () => {
            this.copyAuthCode();
        });
        
        // Refresh emails
        document.getElementById('refreshEmails').addEventListener('click', () => {
            this.refreshEmails();
        });
        
        // Task filters
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.filterTasks(btn.dataset.filter);
            });
        });
        
        // Play summary
        document.getElementById('playSummary').addEventListener('click', () => {
            this.playSummary();
        });
        
        // Task list delegation for approve/reject buttons
        document.getElementById('taskList').addEventListener('click', async (e) => {
            const actionBtn = e.target.closest('.action-btn');
            if (actionBtn) {
                const taskItem = actionBtn.closest('.task-item');
                const taskId = taskItem.dataset.id;
                const action = actionBtn.dataset.action;
                
                await this.handleTaskAction(taskId, action);
            }
        });
        
        // Hands-Free Mode button
        const handsFreeBtn = document.getElementById('handsFreeBtn');
        if (handsFreeBtn) {
            handsFreeBtn.addEventListener('click', () => {
                this.toggleHandsFreeMode();
            });
        }
        
        // Hands-Free Exit button (fallback)
        const exitHandsFreeBtn = document.getElementById('exitHandsFree');
        if (exitHandsFreeBtn) {
            exitHandsFreeBtn.addEventListener('click', () => {
                this.exitHandsFreeMode();
            });
        }
        
        // Replay recording button
        const replayBtn = document.getElementById('replayRecording');
        if (replayBtn) {
            replayBtn.addEventListener('click', () => {
                this.playLastRecording();
            });
        }
    },
    
    /**
     * Check authentication status
     */
    async checkAuthStatus() {
        try {
            const status = await API.getAuthStatus();
            this.isAuthenticated = status.authenticated;
            
            const connectBtn = document.getElementById('connectOutlook');
            if (this.isAuthenticated) {
                connectBtn.classList.add('connected');
                connectBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" width="20" height="20">
                        <path fill="currentColor" d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/>
                    </svg>
                    <span>${UI.t('connect.disconnect')}</span>
                `;
            }
        } catch (error) {
            console.error('Failed to check auth status:', error);
        }
    },
    
    /**
     * Load initial data
     */
    async loadInitialData() {
        try {
            // Load emails if authenticated
            if (this.isAuthenticated) {
                await this.refreshEmails();
            }
            
            // Load tasks
            await this.refreshTasks();
            
            // Load summary
            await this.refreshSummary();
        } catch (error) {
            console.error('Failed to load initial data:', error);
        }
    },
    
    /**
     * Send chat message
     */
    async sendMessage() {
        const input = document.getElementById('chatInput');
        const message = input.value.trim();
        
        if (!message) return;
        
        // Add user message to chat
        UI.addChatMessage(message, true);
        input.value = '';
        
        // Show loading
        const loadingMessage = UI.addLoadingMessage();
        
        try {
            const response = await API.chat(message, UI.currentLanguage);
            UI.removeLoadingMessage();
            UI.addChatMessage(response.response, false);
            
            // Optionally speak the response
            // await Voice.speak(response.response, response.language);
        } catch (error) {
            UI.removeLoadingMessage();
            UI.showToast(error.message, 'error');
            UI.addChatMessage('Sorry, I encountered an error. Please try again.', false);
        }
    },
    
    /**
     * Start voice input
     */
    async startVoiceInput() {
        try {
            const hasPermission = await Voice.checkMicrophonePermission();
            if (!hasPermission) {
                UI.showToast(UI.t('error.microphone'), 'error');
                return;
            }
            
            await Voice.startRecording();
            UI.showVoiceModal();
            UI.setVoiceStatus('listening');
            
            document.getElementById('voiceBtn').classList.add('recording');
        } catch (error) {
            UI.showToast(error.message, 'error');
        }
    },
    
    /**
     * Stop voice input and process
     */
    async stopVoiceInput() {
        try {
            UI.setVoiceStatus('processing');
            
            const audioBlob = await Voice.stopRecording();
            document.getElementById('voiceBtn').classList.remove('recording');
            
            // Transcribe with current language hint
            const transcription = await Voice.transcribe(audioBlob, UI.currentLanguage);
            UI.hideVoiceModal();
            
            if (transcription.text) {
                // Add to chat and process
                document.getElementById('chatInput').value = transcription.text;
                await this.sendMessage();
            }
        } catch (error) {
            UI.hideVoiceModal();
            document.getElementById('voiceBtn').classList.remove('recording');
            UI.showToast(error.message, 'error');
        }
    },
    
    /**
     * Connect or Disconnect from Outlook
     */
    async connectOutlook() {
        if (this.isAuthenticated) {
            // Already connected - disconnect
            await this.disconnectOutlook();
            return;
        }
        
        try {
            // Get device code from backend
            const deviceCode = await API.startDeviceCodeFlow();
            
            // Show auth modal with code and link
            UI.showAuthModal(deviceCode.user_code, deviceCode.verification_uri);
            UI.hideSidebar();
            
            // Start polling for auth completion in background
            this.pollAuthStatus();
        } catch (error) {
            UI.showToast(error.message, 'error');
        }
    },
    
    /**
     * Disconnect from Outlook
     */
    async disconnectOutlook() {
        try {
            await API.disconnectOutlook();
            this.isAuthenticated = false;
            
            // Update connect button
            const connectBtn = document.getElementById('connectOutlook');
            connectBtn.classList.remove('connected');
            connectBtn.innerHTML = `
                <svg viewBox="0 0 24 24" width="20" height="20">
                    <path fill="currentColor" d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
                </svg>
                <span>${UI.t('sidebar.connect')}</span>
            `;
            
            // Clear emails display
            UI.renderEmails([]);
            
            UI.showToast('Disconnected from Outlook', 'success');
            UI.hideSidebar();
        } catch (error) {
            UI.showToast(error.message, 'error');
        }
    },
    
    /**
     * Copy auth code to clipboard
     */
    async copyAuthCode() {
        const code = document.getElementById('authCode').textContent;
        const copyBtn = document.getElementById('copyCodeBtn');
        
        try {
            await navigator.clipboard.writeText(code);
            copyBtn.classList.add('copied');
            UI.showToast(UI.t('auth.copied'), 'success');
            
            setTimeout(() => {
                copyBtn.classList.remove('copied');
            }, 2000);
        } catch (error) {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = code;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            
            copyBtn.classList.add('copied');
            UI.showToast(UI.t('auth.copied'), 'success');
            
            setTimeout(() => {
                copyBtn.classList.remove('copied');
            }, 2000);
        }
    },
    
    /**
     * Poll for authentication completion
     */
    async pollAuthStatus() {
        const maxAttempts = 60; // 5 minutes with 5 second intervals
        let attempts = 0;
        
        const poll = async () => {
            try {
                const status = await API.getAuthStatus();
                
                if (status.authenticated) {
                    // Success!
                    this.isAuthenticated = true;
                    UI.showAuthSuccess();
                    
                    // Update connect button
                    const connectBtn = document.getElementById('connectOutlook');
                    connectBtn.classList.add('connected');
                    connectBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" width="20" height="20">
                            <path fill="currentColor" d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/>
                        </svg>
                        <span>${UI.t('connect.disconnect')}</span>
                    `;
                    
                    // Close modal after delay
                    setTimeout(() => {
                        UI.hideAuthModal();
                        this.refreshEmails();
                    }, 2000);
                    
                    return;
                }
                
                // Not authenticated yet, continue polling
                attempts++;
                if (attempts < maxAttempts) {
                    setTimeout(poll, 5000); // Poll every 5 seconds
                }
            } catch (error) {
                console.error('Auth poll error:', error);
                attempts++;
                if (attempts < maxAttempts) {
                    setTimeout(poll, 5000);
                }
            }
        };
        
        // Also trigger the complete endpoint to start waiting
        try {
            API.completeDeviceCodeFlow();
        } catch (e) {
            console.log('Background auth flow started');
        }
        
        // Start polling
        setTimeout(poll, 3000); // Initial delay
    },
    
    /**
     * Refresh emails
     */
    async refreshEmails() {
        if (!this.isAuthenticated) {
            UI.renderEmails([]);
            return;
        }
        
        try {
            const emails = await API.getEmails();
            UI.renderEmails(emails);
        } catch (error) {
            UI.showToast('Failed to load emails', 'error');
        }
    },
    
    /**
     * Refresh tasks
     */
    async refreshTasks() {
        try {
            const tasks = await API.getTasks();
            UI.renderTasks(tasks);
        } catch (error) {
            console.error('Failed to load tasks:', error);
        }
    },
    
    /**
     * Filter tasks by status
     */
    async filterTasks(filter) {
        try {
            let tasks;
            if (filter === 'all') {
                tasks = await API.getTasks();
            } else if (filter === 'pending') {
                tasks = await API.getTasks('pending_approval');
            } else if (filter === 'approved') {
                tasks = await API.getTasks('approved');
            }
            UI.renderTasks(tasks);
        } catch (error) {
            console.error('Failed to filter tasks:', error);
        }
    },
    
    /**
     * Handle task approve/reject
     */
    async handleTaskAction(taskId, action) {
        try {
            if (action === 'approve') {
                await API.approveTask(taskId);
                UI.showToast('Task approved', 'success');
            } else if (action === 'reject') {
                await API.rejectTask(taskId);
                UI.showToast('Task rejected', 'info');
            }
            await this.refreshTasks();
        } catch (error) {
            UI.showToast(error.message, 'error');
        }
    },
    
    /**
     * Refresh summary
     */
    async refreshSummary() {
        try {
            const summary = await API.getDailySummary();
            UI.updateSummaryStats({
                pending: summary.tasks?.filter(t => t.status === 'pending_approval').length || 0,
                active: summary.tasks?.filter(t => t.status === 'approved').length || 0,
                completed: summary.tasks?.filter(t => t.status === 'completed').length || 0
            });
        } catch (error) {
            console.error('Failed to load summary:', error);
        }
    },
    
    /**
     * Play daily summary via TTS
     */
    async playSummary() {
        try {
            const summary = await API.getDailySummary();
            const text = summary.summary_text || 'No summary available for today.';
            await Voice.speak(text, UI.currentLanguage);
        } catch (error) {
            UI.showToast('Failed to play summary', 'error');
        }
    },
    
    // ============ Hands-Free Mode ============
    
    /**
     * Toggle hands-free mode
     */
    async toggleHandsFreeMode() {
        if (this.isHandsFreeMode) {
            this.exitHandsFreeMode();
        } else {
            await this.enterHandsFreeMode();
        }
    },
    
    /**
     * Enter hands-free mode
     */
    async enterHandsFreeMode() {
        try {
            // Check microphone permission first
            const hasPermission = await Voice.checkMicrophonePermission();
            if (!hasPermission) {
                UI.showToast(UI.t('error.microphone'), 'error');
                return;
            }
            
            this.isHandsFreeMode = true;
            UI.showHandsFreeOverlay();
            UI.hideSidebar();
            
            // Request wake lock to keep screen on
            await this.requestWakeLock();
            
            // Play ready beep and announce
            await Voice.playReadyBeep();
            
            // Start the hands-free conversation loop
            this.startHandsFreeListening();
            
        } catch (error) {
            console.error('Failed to enter hands-free mode:', error);
            UI.showToast(error.message, 'error');
            this.exitHandsFreeMode();
        }
    },
    
    /**
     * Exit hands-free mode
     */
    exitHandsFreeMode() {
        this.isHandsFreeMode = false;
        
        // Clear any pending timeout
        if (this.handsFreeTimeout) {
            clearTimeout(this.handsFreeTimeout);
            this.handsFreeTimeout = null;
        }
        
        // Stop any ongoing recording
        if (Voice.isRecording) {
            Voice.stopRecording().catch(() => {});
        }
        
        // Stop any ongoing speech
        Voice.cancelSpeech();
        
        // Stop silence detection
        Voice.stopSilenceDetection();
        
        // Release wake lock
        this.releaseWakeLock();
        
        // Clear live transcript
        UI.clearLiveTranscript();
        
        // Hide overlay
        UI.hideHandsFreeOverlay();
        
        console.log('Exited hands-free mode');
    },
    
    /**
     * Start listening in hands-free mode
     */
    async startHandsFreeListening() {
        if (!this.isHandsFreeMode) return;
        
        try {
            UI.setHandsFreeStatus('listening');
            UI.clearLiveTranscript();
            UI.showLiveTypingIndicator();
            
            // Play listening beep
            await Voice.playListeningBeep();
            
            // Start recording with silence detection
            await Voice.startRecordingWithSilenceDetection(
                // On silence detected - auto stop and process
                async () => {
                    if (!this.isHandsFreeMode) return;
                    await this.processHandsFreeInput();
                },
                // Silence threshold in seconds
                1.5
            );
            
            // Set timeout for no speech
            this.resetHandsFreeTimeout();
            
        } catch (error) {
            console.error('Hands-free listening error:', error);
            if (this.isHandsFreeMode) {
                UI.setHandsFreeStatus('error');
                UI.clearLiveTranscript();
                await Voice.playErrorBeep();
                // Try to restart listening after error
                setTimeout(() => this.startHandsFreeListening(), 2000);
            }
        }
    },
    
    /**
     * Process hands-free voice input
     */
    async processHandsFreeInput() {
        if (!this.isHandsFreeMode) return;
        
        try {
            UI.setHandsFreeStatus('processing');
            
            // Get the recorded audio
            const audioBlob = await Voice.stopRecording();
            
            // Store the last recording for playback
            this.lastRecording = audioBlob;
            
            // Transcribe with current language hint
            const transcription = await Voice.transcribe(audioBlob, UI.currentLanguage);
            
            if (!transcription.text || transcription.text.trim() === '') {
                UI.clearLiveTranscript();
                if (this.isHandsFreeMode) {
                    this.startHandsFreeListening();
                }
                return;
            }
            
            const userMessage = transcription.text.trim();
            console.log('Hands-free input:', userMessage);
            
            // Show user's speech on screen
            UI.updateLiveTranscript(userMessage, 'user');
            
            // Check for exit commands
            if (this.isExitCommand(userMessage)) {
                // Say goodbye
                const goodbye = UI.currentLanguage === 'ar' 
                    ? 'مع السلامة!' 
                    : 'Goodbye!';
                UI.setHandsFreeStatus('speaking');
                UI.updateLiveTranscript(goodbye, 'assistant');
                await Voice.speak(goodbye, UI.currentLanguage);
                this.exitHandsFreeMode();
                return;
            }
            
            // Add to chat
            UI.addChatMessage(userMessage, true);
            
            // Get AI response
            UI.setHandsFreeStatus('thinking');
            const response = await API.chat(userMessage, UI.currentLanguage);
            
            // Show AI response on screen
            UI.updateLiveTranscript(response.response, 'assistant');
            
            // Add response to chat
            UI.addChatMessage(response.response, false);
            
            // Speak the response
            UI.setHandsFreeStatus('speaking');
            await Voice.speak(response.response, response.language || UI.currentLanguage);
            
            // Continue listening if still in hands-free mode
            if (this.isHandsFreeMode) {
                this.startHandsFreeListening();
            }
            
        } catch (error) {
            console.error('Hands-free processing error:', error);
            UI.clearLiveTranscript();
            if (this.isHandsFreeMode) {
                UI.setHandsFreeStatus('error');
                await Voice.playErrorBeep();
                
                // Announce error
                const errorMsg = UI.currentLanguage === 'ar'
                    ? 'حدث خطأ. حاول مرة أخرى.'
                    : 'An error occurred. Please try again.';
                UI.updateLiveTranscript(errorMsg, 'assistant');
                await Voice.speak(errorMsg, UI.currentLanguage);
                
                // Restart listening
                setTimeout(() => this.startHandsFreeListening(), 1000);
            }
        }
    },
    
    /**
     * Check if the message is an exit command
     */
    isExitCommand(message) {
        const lowerMessage = message.toLowerCase().trim();
        return this.exitCommands.some(cmd => 
            lowerMessage === cmd.toLowerCase() || 
            lowerMessage.includes(cmd.toLowerCase())
        );
    },
    
    /**
     * Reset the hands-free timeout
     */
    resetHandsFreeTimeout() {
        if (this.handsFreeTimeout) {
            clearTimeout(this.handsFreeTimeout);
        }
        
        this.handsFreeTimeout = setTimeout(async () => {
            if (this.isHandsFreeMode) {
                // Announce timeout
                const timeoutMsg = UI.currentLanguage === 'ar'
                    ? 'انتهت المهلة. إنهاء وضع التحدث الحر.'
                    : 'Session timed out. Exiting hands-free mode.';
                
                UI.setHandsFreeStatus('speaking');
                await Voice.speak(timeoutMsg, UI.currentLanguage);
                this.exitHandsFreeMode();
            }
        }, this.handsFreeTimeoutDuration);
    },
    
    /**
     * Request wake lock to keep screen on
     */
    async requestWakeLock() {
        if ('wakeLock' in navigator) {
            try {
                this.wakeLock = await navigator.wakeLock.request('screen');
                console.log('Wake lock acquired');
                
                // Re-acquire if released
                this.wakeLock.addEventListener('release', () => {
                    console.log('Wake lock released');
                    if (this.isHandsFreeMode) {
                        this.requestWakeLock();
                    }
                });
            } catch (error) {
                console.warn('Wake lock not available:', error);
            }
        }
    },
    
    /**
     * Release wake lock
     */
    releaseWakeLock() {
        if (this.wakeLock) {
            this.wakeLock.release();
            this.wakeLock = null;
        }
    },
    
    /**
     * Play the last recording
     */
    async playLastRecording() {
        if (!this.lastRecording) {
            UI.showToast('No recording available', 'info');
            return;
        }
        
        try {
            UI.showToast('Playing last recording...', 'info');
            await Voice.playAudio(this.lastRecording);
        } catch (error) {
            console.error('Failed to play recording:', error);
            UI.showToast('Failed to play recording', 'error');
        }
    }
};

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});

// Export for debugging
window.App = App;
