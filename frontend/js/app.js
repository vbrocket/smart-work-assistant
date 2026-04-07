/**
 * Smart Work Assistant - Main Application
 */
const App = {
    isAuthenticated: false,
    isHandsFreeMode: false,
    handsFreeTimeout: null,
    wakeLock: null,
    lastRecording: null,
    selectedAttendees: [],
    contactSearchTimeout: null,
    
    exitCommands: [
        'stop', 'exit', 'goodbye', 'quit', 'end', 'cancel',
        'توقف', 'خروج', 'مع السلامة', 'انهاء', 'إنهاء', 'وقف'
    ],
    
    QUICK_PROMPTS: {
        today_work: {
            en: "Give me a complete overview of my workday today: list all my meetings and appointments with times, all pending/active tasks with priorities and due dates, and all unread emails highlighting what action is needed from me for each one. Organize by priority and urgency.",
            ar: "أعطني نظرة شاملة على يوم عملي اليوم: اذكر جميع اجتماعاتي ومواعيدي مع الأوقات، وجميع المهام المعلقة/النشطة مع الأولويات وتواريخ الاستحقاق، وجميع رسائل البريد غير المقروءة مع توضيح الإجراء المطلوب مني في كل رسالة. رتبها حسب الأولوية والإلحاح."
        },
        unread_emails: {
            en: "Summarize all my unread emails. For each one, tell me who sent it, what it's about, and what action or response is expected from me.",
            ar: "لخص جميع رسائل البريد الإلكتروني غير المقروءة. لكل رسالة، أخبرني من أرسلها، وما موضوعها، وما الإجراء أو الرد المتوقع مني."
        },
        pending_tasks: {
            en: "List all my pending and active tasks. For each task, include the priority, due date, and a brief description. Sort by urgency.",
            ar: "اذكر جميع مهامي المعلقة والنشطة. لكل مهمة، اذكر الأولوية وتاريخ الاستحقاق ووصفاً مختصراً. رتبها حسب الإلحاح."
        },
        next_meeting: {
            en: "What is my next upcoming meeting? Give me the time, subject, attendees, location or Teams link, and any preparation I should do based on my emails and tasks.",
            ar: "ما هو اجتماعي القادم؟ أعطني الوقت والموضوع والحضور والمكان أو رابط Teams، وأي تحضيرات يجب أن أقوم بها بناءً على رسائلي ومهامي."
        },
        draft_replies: {
            en: "Review my unread emails and for each one that needs a response, draft a brief professional reply.",
            ar: "راجع رسائل بريدي غير المقروءة ولكل رسالة تحتاج إلى رد، اكتب مسودة رد مهني مختصر."
        },
        weekly_plan: {
            en: "Based on my current tasks, meetings, and email backlog, help me plan my week. Suggest what to prioritize and how to organize my time.",
            ar: "بناءً على مهامي الحالية واجتماعاتي ورسائل البريد المتراكمة، ساعدني في تخطيط أسبوعي. اقترح ما يجب أن أعطيه الأولوية وكيف أنظم وقتي."
        }
    },
    
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
        
        // Connect WebSocket voice pipeline (non-blocking)
        this._initWSVoice();
        
        // Set up event listeners
        this.setupEventListeners();
        
        // Check authentication status
        await this.checkAuthStatus();
        
        // Load initial data
        await this.loadInitialData();
        
        console.log('App initialized successfully');
    },

    /**
     * Initialize the WebSocket voice pipeline if supported.
     */
    _initWSVoice() {
        if (typeof WSVoice === 'undefined' || !WSVoice.isSupported()) {
            console.log('WSVoice not available, hands-free will use HTTP fallback');
            return;
        }
        WSVoice.language = UI.currentLanguage || 'ar';
        WSVoice.connect();
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
                if (view === 'chunks' && !this._chunksLoaded) {
                    this._chunksLoaded = true;
                    this.loadChunks(1);
                }
            });
        });
        
        // Quick prompt chips
        document.querySelectorAll('.prompt-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const key = chip.dataset.prompt;
                const prompts = this.QUICK_PROMPTS[key];
                if (!prompts) return;
                
                const lang = UI.currentLanguage || 'en';
                const message = prompts[lang] || prompts.en;
                
                const input = document.getElementById('chatInput');
                input.value = message;
                this.sendMessage({ autoSpeak: true });
            });
        });
        
        // Language toggle
        document.getElementById('langToggle').addEventListener('click', () => {
            UI.toggleLanguage();
            UI.updateSummaryDate();
            if (typeof WSVoice !== 'undefined' && WSVoice.connected) {
                WSVoice.language = UI.currentLanguage;
                WSVoice.sendConfig();
            }
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

        // Auth expiry banner buttons
        document.getElementById('authBannerReconnect')?.addEventListener('click', () => {
            UI.hideAuthBanner();
            this.isAuthenticated = false;
            this.connectOutlook();
        });
        document.getElementById('authBannerDismiss')?.addEventListener('click', () => {
            UI.hideAuthBanner();
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
        
        // Calendar: refresh
        const refreshCalBtn = document.getElementById('refreshCalendar');
        if (refreshCalBtn) {
            refreshCalBtn.addEventListener('click', () => this.refreshCalendar());
        }
        
        // Calendar: create event FAB
        const createEventBtn = document.getElementById('createEventBtn');
        if (createEventBtn) {
            createEventBtn.addEventListener('click', () => UI.showCreateEventModal());
        }
        
        // Calendar: close create modal
        const closeCreateModal = document.getElementById('closeCreateEventModal');
        if (closeCreateModal) {
            closeCreateModal.addEventListener('click', () => UI.hideCreateEventModal());
        }
        
        // Calendar: submit create form
        const createForm = document.getElementById('createEventForm');
        if (createForm) {
            createForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.createEvent();
            });
        }
        
        // Contact picker: search with debounce
        const attendeeInput = document.getElementById('attendeeSearch');
        if (attendeeInput) {
            attendeeInput.addEventListener('input', () => {
                clearTimeout(this.contactSearchTimeout);
                const query = attendeeInput.value.trim();
                if (query.length < 2) {
                    document.getElementById('contactDropdown').classList.remove('visible');
                    return;
                }
                this.contactSearchTimeout = setTimeout(() => this.searchContacts(query), 300);
            });
            
            attendeeInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const val = attendeeInput.value.trim();
                    if (val && val.includes('@')) {
                        this.addAttendee({ email: val, name: val });
                        attendeeInput.value = '';
                        document.getElementById('contactDropdown').classList.remove('visible');
                    }
                }
            });
            
            // Close dropdown when clicking outside
            document.addEventListener('click', (e) => {
                if (!e.target.closest('.attendee-picker')) {
                    document.getElementById('contactDropdown').classList.remove('visible');
                }
            });
        }
        
        // ---- Policy Documents ----
        const refreshPolicyBtn = document.getElementById('refreshPolicy');
        if (refreshPolicyBtn) {
            refreshPolicyBtn.addEventListener('click', () => this.refreshPolicyStatus());
        }
        
        const uploadZone = document.getElementById('uploadZone');
        const policyFileInput = document.getElementById('policyFileInput');
        const browseBtn = document.getElementById('policyBrowseBtn');
        
        if (uploadZone) {
            uploadZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadZone.classList.add('dragover');
            });
            uploadZone.addEventListener('dragleave', () => {
                uploadZone.classList.remove('dragover');
            });
            uploadZone.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadZone.classList.remove('dragover');
                const files = Array.from(e.dataTransfer.files);
                files.forEach(f => this.uploadPolicyDocument(f));
            });
            uploadZone.addEventListener('click', (e) => {
                if (e.target.closest('#policyBrowseBtn') || e.target === uploadZone || e.target.closest('.upload-text') || e.target.closest('.upload-icon') || e.target.closest('.upload-hint')) {
                    policyFileInput?.click();
                }
            });
        }
        
        if (policyFileInput) {
            policyFileInput.addEventListener('change', () => {
                const files = Array.from(policyFileInput.files);
                files.forEach(f => this.uploadPolicyDocument(f));
                policyFileInput.value = '';
            });
        }
        
        const ingestBtn = document.getElementById('ingestBtn');
        if (ingestBtn) {
            ingestBtn.addEventListener('click', () => this.ingestPolicyDocuments());
        }
        
        const policyDocList = document.getElementById('policyDocList');
        if (policyDocList) {
            policyDocList.addEventListener('click', (e) => {
                const deleteBtn = e.target.closest('.policy-doc-delete');
                if (deleteBtn) {
                    const filename = deleteBtn.dataset.name;
                    if (filename) this.deletePolicyDocument(filename);
                }
            });
        }

        // ---- Chunks Viewer ----
        const refreshChunksBtn = document.getElementById('refreshChunks');
        if (refreshChunksBtn) {
            refreshChunksBtn.addEventListener('click', () => this.loadChunks(1));
        }

        document.querySelectorAll('[data-chunk-filter]').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('[data-chunk-filter]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this._chunksFilter = btn.dataset.chunkFilter === 'all' ? null : btn.dataset.chunkFilter;
                this.loadChunks(1);
            });
        });

        const chunksSearchInput = document.getElementById('chunksSearchInput');
        if (chunksSearchInput) {
            let debounce = null;
            chunksSearchInput.addEventListener('input', () => {
                clearTimeout(debounce);
                debounce = setTimeout(() => {
                    this._chunksSearch = chunksSearchInput.value.trim() || null;
                    this.loadChunks(1);
                }, 400);
            });
        }

        const chunksPrev = document.getElementById('chunksPrev');
        const chunksNext = document.getElementById('chunksNext');
        if (chunksPrev) chunksPrev.addEventListener('click', () => this.loadChunks(this._chunksPage - 1));
        if (chunksNext) chunksNext.addEventListener('click', () => this.loadChunks(this._chunksPage + 1));

        const chunksList = document.getElementById('chunksList');
        if (chunksList) {
            chunksList.addEventListener('click', (e) => {
                const expandBtn = e.target.closest('.chunk-expand-btn');
                if (expandBtn) {
                    const body = expandBtn.previousElementSibling;
                    if (body) {
                        body.classList.toggle('expanded');
                        expandBtn.textContent = body.classList.contains('expanded') ? 'Collapse' : 'Expand';
                    }
                }
            });
        }
    },
    
    /**
     * Check authentication status
     */
    async checkAuthStatus() {
        try {
            const status = await API.getAuthStatus();
            const wasAuthenticated = this.isAuthenticated;
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
                UI.hideAuthBanner();
            } else {
                connectBtn.classList.remove('connected');
                connectBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" width="20" height="20">
                        <path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                    </svg>
                    <span>${UI.t('sidebar.connect')}</span>
                `;
                const reason = (wasAuthenticated || status.was_connected) ? 'expired' : 'not_connected';
                UI.showAuthBanner(reason);
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
            if (this.isAuthenticated) {
                await this.refreshEmails();
                await this.refreshCalendar();
            }
            
            await this.refreshTasks();
            await this.refreshSummary();
            await this.refreshPolicyStatus();
        } catch (error) {
            console.error('Failed to load initial data:', error);
        }
    },
    
    /**
     * Send chat message with streaming.
     * @param {Object} options - { autoSpeak: boolean }
     */
    async sendMessage(options = {}) {
        const input = document.getElementById('chatInput');
        const message = input.value.trim();

        if (!message) return;

        UI.addChatMessage(message, true);
        input.value = '';

        const loadingMessage = UI.addLoadingMessage();
        let handle = null;
        let speakHandle = null;

        const voiceMode = !!options.autoSpeak;

        return new Promise((resolve) => {
            API.chatStream(message, UI.currentLanguage, {
                onAuthRequired() {
                    App.isAuthenticated = false;
                    UI.showAuthBanner('expired');
                },
                onStatus(message) {
                    UI.removeLoadingMessage();
                    loadingMessage = UI.addLoadingMessage(message);
                },
                onRoute(_intent) {
                    if (voiceMode) {
                        speakHandle = Voice.speakStreaming(UI.currentLanguage);
                    }
                },
                onToken(token) {
                    if (!handle) {
                        UI.removeLoadingMessage();
                        handle = UI.addStreamingMessage();
                    }
                    UI.appendToStreamingMessage(handle, token);
                    if (speakHandle) speakHandle.feedToken(token);
                },
                onThinkingStart() {
                    if (!handle) {
                        UI.removeLoadingMessage();
                        handle = UI.addStreamingMessage();
                    }
                    UI.startThinkingBlock(handle);
                },
                onThinking(content) {
                    if (handle) UI.appendThinkingToken(handle, content);
                },
                onThinkingEnd() {
                    if (handle) UI.endThinkingBlock(handle);
                },
                onCitations(citations, refsText) {
                    if (handle && refsText) {
                        UI.appendToStreamingMessage(handle, refsText);
                    }
                },
                onClear() {
                    if (handle) UI.clearStreamingMessage(handle);
                    if (speakHandle) speakHandle.reset();
                },
                onDone(fullResponse, language) {
                    if (!handle) {
                        UI.removeLoadingMessage();
                        UI.addChatMessage(fullResponse || '', false);
                    } else {
                        UI.finalizeStreamingMessage(handle, fullResponse);
                    }
                    if (speakHandle) {
                        speakHandle.flush();
                    }
                    resolve();
                },
                onError(msg) {
                    UI.removeLoadingMessage();
                    if (handle) {
                        UI.finalizeStreamingMessage(handle, handle._raw);
                    }
                    UI.showToast(msg, 'error');
                    if (speakHandle) speakHandle.cancel();
                    resolve();
                }
            }, { voiceMode });
        });
    },
    
    /**
     * Start voice input (tap-to-record button).
     * Uses WSVoice when connected, HTTP fallback otherwise.
     */
    async startVoiceInput() {
        try {
            const hasPermission = await Voice.checkMicrophonePermission();
            if (!hasPermission) {
                UI.showToast(UI.t('error.microphone'), 'error');
                return;
            }

            if (this._useWSVoice()) {
                this._voiceInputViaWS = true;
                WSVoice.language = UI.currentLanguage || 'ar';
                WSVoice.voiceMode = false;

                WSVoice.callbacks.onPartialTranscript = (text) => {
                    if (text) {
                        const statusEl = document.querySelector('.voice-status');
                        if (statusEl) statusEl.textContent = text;
                    }
                };
                WSVoice.callbacks.onTranscript = (text) => {
                    UI.hideVoiceModal();
                    document.getElementById('voiceBtn').classList.remove('recording');
                    if (text && text.trim()) {
                        document.getElementById('chatInput').value = text.trim();
                        this.sendMessage();
                    }
                    this._clearVoiceInputWSCallbacks();
                };
                WSVoice.callbacks.onDone = () => {};
                WSVoice.callbacks.onError = (msg) => {
                    UI.hideVoiceModal();
                    document.getElementById('voiceBtn').classList.remove('recording');
                    UI.showToast(msg, 'error');
                    this._clearVoiceInputWSCallbacks();
                };

                await WSVoice.startListening();
                UI.showVoiceModal();
                UI.setVoiceStatus('listening');
                document.getElementById('voiceBtn').classList.add('recording');
            } else {
                this._voiceInputViaWS = false;
                await Voice.startRecording();
                UI.showVoiceModal();
                UI.setVoiceStatus('listening');
                document.getElementById('voiceBtn').classList.add('recording');
            }
        } catch (error) {
            UI.showToast(error.message, 'error');
        }
    },

    _clearVoiceInputWSCallbacks() {
        if (typeof WSVoice !== 'undefined') {
            WSVoice.callbacks.onPartialTranscript = null;
            WSVoice.callbacks.onTranscript = null;
            WSVoice.callbacks.onDone = null;
            WSVoice.callbacks.onError = null;
        }
    },

    /**
     * Stop voice input and process
     */
    async stopVoiceInput() {
        if (this._voiceInputViaWS && this._useWSVoice()) {
            UI.setVoiceStatus('processing');
            document.getElementById('voiceBtn').classList.remove('recording');
            WSVoice.stopListening();
            return;
        }

        try {
            UI.setVoiceStatus('processing');
            const audioBlob = await Voice.stopRecording();
            document.getElementById('voiceBtn').classList.remove('recording');
            const transcription = await Voice.transcribe(audioBlob, UI.currentLanguage);
            UI.hideVoiceModal();
            
            if (transcription.text) {
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
            this._authPollActive = false;
            const deviceCode = await API.startDeviceCodeFlow();
            UI.showAuthModal(deviceCode.user_code, deviceCode.verification_uri, deviceCode.expires_in || 900);
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
        this._authPollActive = true;
        const maxAttempts = 60;
        let attempts = 0;
        
        const poll = async () => {
            if (!this._authPollActive) return;
            try {
                const status = await API.getAuthStatus();
                
                if (status.authenticated) {
                    this._authPollActive = false;
                    this.isAuthenticated = true;
                    UI.showAuthSuccess();
                    UI.hideAuthBanner();
                    
                    const connectBtn = document.getElementById('connectOutlook');
                    connectBtn.classList.add('connected');
                    connectBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" width="20" height="20">
                            <path fill="currentColor" d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/>
                        </svg>
                        <span>${UI.t('connect.disconnect')}</span>
                    `;
                    
                    setTimeout(() => {
                        UI.hideAuthModal();
                        this.refreshEmails();
                        this.refreshCalendar();
                    }, 2000);
                    
                    return;
                }
                
                attempts++;
                if (attempts < maxAttempts && this._authPollActive) {
                    setTimeout(poll, 5000);
                }
            } catch (error) {
                console.error('Auth poll error:', error);
                attempts++;
                if (attempts < maxAttempts && this._authPollActive) {
                    setTimeout(poll, 5000);
                }
            }
        };
        
        try {
            API.completeDeviceCodeFlow();
        } catch (e) {
            console.log('Background auth flow started');
        }
        
        setTimeout(poll, 3000);
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
    
    // ============ Policy Documents ============
    
    async refreshPolicyStatus() {
        try {
            const status = await API.getPolicyStatus();
            UI.updatePolicyStats(status);
            UI.renderPolicyDocuments(status.file_details || []);
        } catch (error) {
            console.error('Failed to load policy status:', error);
        }
    },
    
    async uploadPolicyDocument(file) {
        try {
            await API.uploadPolicyDoc(file);
            UI.showToast(UI.t('policy.uploadSuccess'), 'success');
            await this.refreshPolicyStatus();
        } catch (error) {
            UI.showToast(error.message || 'Upload failed', 'error');
        }
    },
    
    async deletePolicyDocument(filename) {
        try {
            await API.deletePolicyDoc(filename);
            UI.showToast(UI.t('policy.deleteSuccess'), 'success');
            await this.refreshPolicyStatus();
        } catch (error) {
            UI.showToast(error.message || 'Delete failed', 'error');
        }
    },
    
    async ingestPolicyDocuments() {
        UI.setPolicyIngestProgress('loading');
        try {
            const result = await API.ingestPolicyDocs();
            UI.setPolicyIngestProgress('success', result);
            await this.refreshPolicyStatus();
        } catch (error) {
            UI.setPolicyIngestProgress('error', error.message || 'Ingestion failed');
        }
    },
    
    // ============ Chunks Viewer ============

    _chunksPage: 1,
    _chunksFilter: null,
    _chunksSearch: null,

    async loadChunks(page = 1) {
        if (page < 1) return;
        this._chunksPage = page;

        try {
            const data = await API.getPolicyChunks(page, 50, this._chunksFilter, this._chunksSearch);
            const totalPages = Math.ceil(data.total / data.page_size) || 1;

            document.getElementById('chunksShowing').textContent = data.chunks.length;
            document.getElementById('chunksTotal').textContent = data.total;
            document.getElementById('chunksPageInfo').textContent = `Page ${data.page} / ${totalPages}`;
            document.getElementById('chunksPrev').disabled = data.page <= 1;
            document.getElementById('chunksNext').disabled = data.page >= totalPages;

            const list = document.getElementById('chunksList');
            if (!data.chunks.length) {
                list.innerHTML = `<div class="chunks-empty">
                    <svg viewBox="0 0 24 24" width="64" height="64"><path fill="currentColor" d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/></svg>
                    <p>No chunks found</p>
                </div>`;
                return;
            }

            list.innerHTML = data.chunks.map(c => {
                const shortId = c.id ? c.id.substring(0, 8) : '';
                const badge = c.chunk_type || 'text_clause';
                const section = c.section_id ? `§${c.section_id}` : '';
                const title = c.section_title || c.table_title || '';
                const pageLabel = c.page_start ? `p.${c.page_start}` : '';
                const needsExpand = c.text.length > 400;
                const displayText = UI.escapeHtml(c.text);

                return `<div class="chunk-card" style="background:var(--bg-card,#fff);border:1px solid var(--border-color,#ccc);border-radius:10px;margin-bottom:8px;">
                    <div class="chunk-card-header" style="padding:10px 14px;background:var(--bg-tertiary,#f0f0f0);border-bottom:1px solid var(--border-color,#ccc);">
                        <div class="chunk-meta" style="display:flex;gap:8px;align-items:center;">
                            <span class="chunk-badge ${badge}" style="padding:2px 8px;border-radius:12px;font-size:0.7rem;font-weight:600;">${badge.replace('_', ' ')}</span>
                            ${section ? `<span class="chunk-section" style="color:var(--text-secondary,#666);font-size:0.78rem;">${UI.escapeHtml(section)}</span>` : ''}
                            ${title ? `<span class="chunk-section" style="color:var(--text-secondary,#666);font-size:0.78rem;">${UI.escapeHtml(title)}</span>` : ''}
                        </div>
                        <span class="chunk-page" style="font-size:0.72rem;color:var(--text-secondary,#999);">${pageLabel}</span>
                        <span class="chunk-id-text" title="${c.id}" style="font-size:0.65rem;color:var(--text-secondary,#aaa);font-family:monospace;">${shortId}</span>
                    </div>
                    <div class="chunk-card-body${needsExpand ? '' : ' expanded'}" style="padding:12px 14px;font-size:0.85rem;line-height:1.6;color:var(--text-primary,#333);white-space:pre-wrap;word-wrap:break-word;direction:rtl;text-align:right;max-height:${needsExpand ? '200px' : 'none'};overflow-y:auto;">${displayText}</div>
                    ${needsExpand ? '<button class="chunk-expand-btn" style="display:block;width:100%;padding:6px;border:none;background:var(--bg-tertiary,#f0f0f0);color:var(--primary-color,#4F008C);font-size:0.75rem;cursor:pointer;">Expand</button>' : ''}
                </div>`;
            }).join('');

        } catch (error) {
            console.error('Failed to load chunks:', error);
            document.getElementById('chunksList').innerHTML = `<div class="chunks-empty"><p>Failed to load chunks</p></div>`;
        }
    },

    // ============ Calendar ============
    
    async refreshCalendar() {
        if (!this.isAuthenticated) {
            UI.renderCalendarEvents([]);
            return;
        }
        
        try {
            const today = new Date().toISOString().split('T')[0];
            const events = await API.getCalendarEvents(today);
            UI.renderCalendarEvents(events);
        } catch (error) {
            console.error('Failed to load calendar:', error);
        }
    },
    
    async createEvent() {
        const subject = document.getElementById('eventSubject').value.trim();
        const start = document.getElementById('eventStart').value;
        const end = document.getElementById('eventEnd').value;
        const location = document.getElementById('eventLocation').value.trim();
        const body = document.getElementById('eventBody').value.trim();
        const isOnline = document.getElementById('eventOnline').checked;
        
        if (!subject || !start || !end) {
            UI.showToast('Please fill in subject, start, and end time', 'error');
            return;
        }
        
        try {
            const data = {
                subject,
                start: new Date(start).toISOString().replace('Z', ''),
                end: new Date(end).toISOString().replace('Z', ''),
                location: location || null,
                body: body || null,
                is_online: isOnline,
                attendees: this.selectedAttendees.length ? this.selectedAttendees : null
            };
            
            const result = await API.createCalendarEvent(data);
            
            UI.hideCreateEventModal();
            this.selectedAttendees = [];
            UI.showToast(UI.t('calendar.created'), 'success');
            
            // Show Teams link if created
            if (result.online_meeting_url) {
                UI.showToast(`Teams: ${result.online_meeting_url}`, 'info', 8000);
            }
            
            await this.refreshCalendar();
        } catch (error) {
            UI.showToast(error.message || 'Failed to create event', 'error');
        }
    },
    
    // ============ Contacts / Attendee Picker ============
    
    async searchContacts(query) {
        try {
            const contacts = await API.getContacts(query);
            const dropdown = document.getElementById('contactDropdown');
            
            if (!contacts || contacts.length === 0) {
                dropdown.classList.remove('visible');
                return;
            }
            
            // Filter out already-selected
            const selectedEmails = this.selectedAttendees.map(a => a.email.toLowerCase());
            const filtered = contacts.filter(c => c.email && !selectedEmails.includes(c.email.toLowerCase()));
            
            if (filtered.length === 0) {
                dropdown.classList.remove('visible');
                return;
            }
            
            dropdown.innerHTML = filtered.map(c => `
                <div class="contact-option" data-email="${UI.escapeHtml(c.email)}" data-name="${UI.escapeHtml(c.display_name)}">
                    <span class="contact-name">${UI.escapeHtml(c.display_name)}</span>
                    <span class="contact-email">${UI.escapeHtml(c.email)}</span>
                    ${c.company ? `<span class="contact-company">${UI.escapeHtml(c.company)}</span>` : ''}
                </div>
            `).join('');
            
            dropdown.classList.add('visible');
            
            // Click handlers for options
            dropdown.querySelectorAll('.contact-option').forEach(opt => {
                opt.addEventListener('click', () => {
                    this.addAttendee({
                        email: opt.dataset.email,
                        name: opt.dataset.name
                    });
                    document.getElementById('attendeeSearch').value = '';
                    dropdown.classList.remove('visible');
                });
            });
        } catch (error) {
            console.error('Contact search failed:', error);
        }
    },
    
    addAttendee(attendee) {
        if (this.selectedAttendees.find(a => a.email.toLowerCase() === attendee.email.toLowerCase())) {
            return;
        }
        
        this.selectedAttendees.push(attendee);
        this.renderAttendeeChips();
    },
    
    removeAttendee(email) {
        this.selectedAttendees = this.selectedAttendees.filter(a => a.email !== email);
        this.renderAttendeeChips();
    },
    
    renderAttendeeChips() {
        const container = document.getElementById('selectedAttendees');
        if (!container) return;
        
        container.innerHTML = this.selectedAttendees.map(a => `
            <div class="attendee-chip">
                <span>${UI.escapeHtml(a.name || a.email)}</span>
                <button type="button" class="remove-attendee" data-email="${UI.escapeHtml(a.email)}">&times;</button>
            </div>
        `).join('');
        
        container.querySelectorAll('.remove-attendee').forEach(btn => {
            btn.addEventListener('click', () => {
                this.removeAttendee(btn.dataset.email);
            });
        });
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
            const hasPermission = await Voice.checkMicrophonePermission();
            if (!hasPermission) {
                UI.showToast(UI.t('error.microphone'), 'error');
                return;
            }
            
            this.isHandsFreeMode = true;
            UI.showHandsFreeOverlay();
            UI.hideSidebar();
            
            await this.requestWakeLock();
            await Voice.playReadyBeep();
            
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
        
        if (this.handsFreeTimeout) {
            clearTimeout(this.handsFreeTimeout);
            this.handsFreeTimeout = null;
        }
        
        if (this._useWSVoice()) {
            WSVoice.cancel();
        } else {
            if (Voice.isRecording) Voice.stopRecording().catch(() => {});
            Voice.cancelSpeech();
            Voice.stopSilenceDetection();
        }
        
        this.releaseWakeLock();
        UI.clearLiveTranscript();
        UI.hideHandsFreeOverlay();
        
        console.log('Exited hands-free mode');
    },

    /**
     * Returns true if the WebSocket voice pipeline is available.
     */
    _useWSVoice() {
        return typeof WSVoice !== 'undefined' && WSVoice.isSupported() && WSVoice.connected;
    },

    /**
     * Start listening in hands-free mode.
     * Uses WebSocket pipeline when available, falls back to HTTP.
     */
    async startHandsFreeListening() {
        if (!this.isHandsFreeMode) return;

        if (this._useWSVoice()) {
            await this._startHandsFreeWS();
        } else {
            await this._startHandsFreeHTTP();
        }
    },

    // ---- WebSocket-based hands-free ----

    async _startHandsFreeWS() {
        if (!this.isHandsFreeMode) return;

        UI.setHandsFreeStatus('listening');
        UI.clearLiveTranscript();
        UI.showLiveTypingIndicator();
        await Voice.playListeningBeep();

        WSVoice.language = UI.currentLanguage || 'ar';
        WSVoice.voiceMode = true;

        let handle = null;

        WSVoice.callbacks.onPartialTranscript = (text) => {
            if (text) UI.updateLiveTranscript(text + ' ...', 'user');
        };

        WSVoice.callbacks.onTranscript = (text, _lang, _conf) => {
            if (!text || !text.trim()) {
                UI.clearLiveTranscript();
                if (this.isHandsFreeMode) {
                    setTimeout(() => this.startHandsFreeListening(), 300);
                }
                return;
            }
            const userMessage = text.trim();
            console.log('[WSVoice] transcript:', userMessage);
            UI.updateLiveTranscript(userMessage, 'user');

            if (this.isExitCommand(userMessage)) {
                const goodbye = UI.currentLanguage === 'ar' ? 'مع السلامة!' : 'Goodbye!';
                UI.setHandsFreeStatus('speaking');
                UI.updateLiveTranscript(goodbye, 'assistant');
                Voice.speak(goodbye, UI.currentLanguage).then(() => this.exitHandsFreeMode());
                WSVoice.cancel();
                return;
            }

            UI.addChatMessage(userMessage, true);
            UI.setHandsFreeStatus('thinking');
        };

        WSVoice.callbacks.onStatus = (message) => {
            UI.setHandsFreeStatus('thinking', message);
        };

        WSVoice.callbacks.onRoute = (_intent) => {};

        WSVoice.callbacks.onToken = (token) => {
            if (!handle) {
                handle = UI.addStreamingMessage();
                UI.setHandsFreeStatus('speaking');
            }
            UI.appendToStreamingMessage(handle, token);
            UI.updateLiveTranscript(handle._raw, 'assistant');
        };

        WSVoice.callbacks.onThinkingStart = () => {
            if (!handle) {
                handle = UI.addStreamingMessage();
            }
            UI.startThinkingBlock(handle);
        };

        WSVoice.callbacks.onThinking = (content) => {
            if (handle) UI.appendThinkingToken(handle, content);
        };

        WSVoice.callbacks.onThinkingEnd = () => {
            if (handle) UI.endThinkingBlock(handle);
        };

        WSVoice.callbacks.onCitations = (_cit, refsText) => {
            if (handle && refsText) {
                UI.appendToStreamingMessage(handle, refsText);
            }
        };

        WSVoice.callbacks.onClear = () => {
            if (handle) UI.clearStreamingMessage(handle);
        };

        WSVoice.callbacks.onDone = (fullResponse) => {
            if (handle) {
                UI.finalizeStreamingMessage(handle, fullResponse);
            } else if (fullResponse) {
                UI.addChatMessage(fullResponse, false);
            }
            UI.updateLiveTranscript(fullResponse || '', 'assistant');
            UI.setHandsFreeStatus('speaking');
            handle = null;

            WSVoice.waitForTTSComplete().then(() => {
                if (this.isHandsFreeMode) {
                    setTimeout(() => this.startHandsFreeListening(), 400);
                }
            });
        };

        WSVoice.callbacks.onError = (msg) => {
            console.error('[WSVoice] pipeline error:', msg);
            if (handle) UI.finalizeStreamingMessage(handle, handle._raw);
            handle = null;
            if (this.isHandsFreeMode) {
                UI.setHandsFreeStatus('error');
                Voice.playErrorBeep();
                setTimeout(() => this.startHandsFreeListening(), 2000);
            }
        };

        try {
            const ok = await WSVoice.startListening();
            if (!ok && this.isHandsFreeMode) {
                console.warn('[WSVoice] failed to start, falling back to HTTP');
                await this._startHandsFreeHTTP();
            }
        } catch (err) {
            console.error('[WSVoice] listen error:', err);
            if (this.isHandsFreeMode) {
                await this._startHandsFreeHTTP();
            }
        }
    },

    // ---- HTTP-based hands-free (fallback) ----

    async _startHandsFreeHTTP() {
        if (!this.isHandsFreeMode) return;

        try {
            UI.setHandsFreeStatus('listening');
            UI.clearLiveTranscript();
            UI.showLiveTypingIndicator();
            await Voice.playListeningBeep();

            await Voice.startRecordingWithSilenceDetection(
                async () => {
                    if (!this.isHandsFreeMode) return;
                    await this._processHandsFreeHTTP();
                },
                1.5
            );
        } catch (error) {
            console.error('Hands-free listening error:', error);
            if (this.isHandsFreeMode) {
                UI.setHandsFreeStatus('error');
                UI.clearLiveTranscript();
                await Voice.playErrorBeep();
                setTimeout(() => this.startHandsFreeListening(), 2000);
            }
        }
    },

    async _processHandsFreeHTTP() {
        if (!this.isHandsFreeMode) return;
        
        try {
            UI.setHandsFreeStatus('processing');
            const audioBlob = await Voice.stopRecording();
            this.lastRecording = audioBlob;
            const transcription = await Voice.transcribe(audioBlob, UI.currentLanguage);
            
            if (!transcription.text || transcription.text.trim() === '') {
                UI.clearLiveTranscript();
                if (this.isHandsFreeMode) {
                    await new Promise(r => setTimeout(r, 300));
                    this.startHandsFreeListening();
                }
                return;
            }
            
            const userMessage = transcription.text.trim();
            console.log('Hands-free input:', userMessage);
            UI.updateLiveTranscript(userMessage, 'user');
            
            if (this.isExitCommand(userMessage)) {
                const goodbye = UI.currentLanguage === 'ar' ? 'مع السلامة!' : 'Goodbye!';
                UI.setHandsFreeStatus('speaking');
                UI.updateLiveTranscript(goodbye, 'assistant');
                await Voice.speak(goodbye, UI.currentLanguage);
                this.exitHandsFreeMode();
                return;
            }
            
            UI.addChatMessage(userMessage, true);
            UI.setHandsFreeStatus('thinking');

            await new Promise((resolve) => {
                let handle = null;
                let speakHandle = null;

                API.chatStream(userMessage, UI.currentLanguage, {
                    onRoute(_intent) {
                        speakHandle = Voice.speakStreaming(UI.currentLanguage);
                    },
                    onToken(token) {
                        if (!handle) {
                            handle = UI.addStreamingMessage();
                            UI.setHandsFreeStatus('speaking');
                        }
                        UI.appendToStreamingMessage(handle, token);
                        UI.updateLiveTranscript(handle._raw, 'assistant');
                        if (speakHandle) speakHandle.feedToken(token);
                    },
                    onThinkingStart() {
                        if (!handle) {
                            handle = UI.addStreamingMessage();
                        }
                        UI.startThinkingBlock(handle);
                    },
                    onThinking(content) {
                        if (handle) UI.appendThinkingToken(handle, content);
                    },
                    onThinkingEnd() {
                        if (handle) UI.endThinkingBlock(handle);
                    },
                    onCitations(_cit, refsText) {
                        if (handle && refsText) {
                            UI.appendToStreamingMessage(handle, refsText);
                        }
                    },
                    onClear() {
                        if (handle) UI.clearStreamingMessage(handle);
                        if (speakHandle) speakHandle.reset();
                    },
                    onDone(fullResponse, language) {
                        if (handle) {
                            UI.finalizeStreamingMessage(handle, fullResponse);
                        } else {
                            UI.addChatMessage(fullResponse || '', false);
                        }
                        UI.updateLiveTranscript(fullResponse || '', 'assistant');
                        UI.setHandsFreeStatus('speaking');

                        if (speakHandle) {
                            speakHandle.flush();
                            speakHandle.waitForCompletion().then(resolve);
                        } else {
                            resolve();
                        }
                    },
                    onError(msg) {
                        if (handle) UI.finalizeStreamingMessage(handle, handle._raw);
                        if (speakHandle) speakHandle.cancel();
                        resolve();
                    }
                }, { voiceMode: true });
            });

            if (this.isHandsFreeMode) {
                await new Promise(r => setTimeout(r, 400));
                this.startHandsFreeListening();
            }
            
        } catch (error) {
            console.error('Hands-free processing error:', error);
            UI.clearLiveTranscript();
            if (this.isHandsFreeMode) {
                UI.setHandsFreeStatus('error');
                await Voice.playErrorBeep();
                const errorMsg = UI.currentLanguage === 'ar'
                    ? 'حدث خطأ. حاول مرة أخرى.'
                    : 'An error occurred. Please try again.';
                UI.updateLiveTranscript(errorMsg, 'assistant');
                await Voice.speak(errorMsg, UI.currentLanguage);
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
