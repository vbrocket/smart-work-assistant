/**
 * Voice Service - Handles voice recording and playback
 */
const Voice = {
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
    audioContext: null,
    analyser: null,
    silenceDetectionInterval: null,
    silenceStartTime: null,
    onSilenceCallback: null,
    silenceThreshold: 0.01,
    currentStream: null,
    currentAudio: null, // Reference to currently playing Audio element for cancellation
    maxRecordingTime: 15000,
    maxRecordingTimeout: null,
    
    /**
     * Initialize audio context
     */
    async init() {
        try {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        } catch (error) {
            console.warn('AudioContext not available:', error);
        }
    },
    
    /**
     * Check if microphone is available
     */
    async checkMicrophonePermission() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach(track => track.stop());
            return true;
        } catch (error) {
            console.error('Microphone permission denied:', error);
            return false;
        }
    },
    
    /**
     * Start recording
     */
    async startRecording() {
        if (this.isRecording) return;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            
            this.currentStream = stream;
            
            // Create audio context if needed
            if (!this.audioContext) {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
            
            // Resume if suspended (browser autoplay policy)
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }
            
            // Set up audio analyser for visualization and silence detection
            const source = this.audioContext.createMediaStreamSource(stream);
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 256;
            this.analyser.smoothingTimeConstant = 0.3;
            source.connect(this.analyser);
            
            // Prefer webm/opus, fall back to other formats
            const mimeTypes = [
                'audio/webm;codecs=opus',
                'audio/webm',
                'audio/ogg;codecs=opus',
                'audio/mp4'
            ];
            
            let mimeType = '';
            for (const type of mimeTypes) {
                if (MediaRecorder.isTypeSupported(type)) {
                    mimeType = type;
                    break;
                }
            }
            
            const options = mimeType ? { mimeType } : {};
            this.mediaRecorder = new MediaRecorder(stream, options);
            this.audioChunks = [];
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.start(100); // Collect data every 100ms
            this.isRecording = true;
            
            return true;
        } catch (error) {
            console.error('Failed to start recording:', error);
            throw error;
        }
    },
    
    /**
     * Stop recording and return audio blob
     */
    async stopRecording() {
        return new Promise((resolve, reject) => {
            if (!this.mediaRecorder || !this.isRecording) {
                reject(new Error('Not recording'));
                return;
            }
            
            this.mediaRecorder.onstop = () => {
                const mimeType = this.mediaRecorder.mimeType || 'audio/webm';
                const audioBlob = new Blob(this.audioChunks, { type: mimeType });
                
                // Stop all tracks
                this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
                
                this.isRecording = false;
                this.mediaRecorder = null;
                this.audioChunks = [];
                
                resolve(audioBlob);
            };
            
            this.mediaRecorder.onerror = (event) => {
                this.isRecording = false;
                reject(event.error);
            };
            
            this.mediaRecorder.stop();
        });
    },
    
    /**
     * Get audio level (0-1) for visualization
     */
    getAudioLevel() {
        if (!this.analyser) {
            console.warn('[VAD] No analyser available!');
            return 0;
        }
        
        if (this.audioContext && this.audioContext.state !== 'running') {
            console.warn('[VAD] AudioContext state:', this.audioContext.state);
            this.audioContext.resume();
            return 0;
        }
        
        const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteTimeDomainData(dataArray);
        
        // Calculate RMS (root mean square) for better voice detection
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const value = (dataArray[i] - 128) / 128;
            sum += value * value;
        }
        const rms = Math.sqrt(sum / dataArray.length);
        
        return rms;
    },
    
    /**
     * Play audio from blob or URL
     */
    async playAudio(audioSource) {
        return new Promise((resolve, reject) => {
            const audio = new Audio();
            this.currentAudio = audio;
            
            if (audioSource instanceof Blob) {
                audio.src = URL.createObjectURL(audioSource);
            } else {
                audio.src = audioSource;
            }
            
            audio.onended = () => {
                if (audioSource instanceof Blob) {
                    URL.revokeObjectURL(audio.src);
                }
                this.currentAudio = null;
                resolve();
            };
            
            audio.onerror = (error) => {
                this.currentAudio = null;
                reject(error);
            };
            
            audio.play().catch(reject);
        });
    },
    
    /**
     * Transcribe recorded audio
     */
    async transcribe(audioBlob, language = null) {
        try {
            const result = await API.transcribe(audioBlob, language);
            return result;
        } catch (error) {
            console.error('Transcription failed:', error);
            throw error;
        }
    },
    
    /**
     * Speak text using TTS
     */
    async speak(text, language = 'en') {
        try {
            // Try backend TTS first
            const audioBlob = await API.speak(text, language);
            await this.playAudio(audioBlob);
        } catch (error) {
            console.warn('Backend TTS failed, falling back to Web Speech API:', error);
            // Fallback to Web Speech API
            await this.speakWithWebSpeech(text, language);
        }
    },
    
    /**
     * Fallback: Use Web Speech API for TTS
     */
    async speakWithWebSpeech(text, language) {
        return new Promise((resolve, reject) => {
            if (!('speechSynthesis' in window)) {
                reject(new Error('Speech synthesis not supported'));
                return;
            }
            
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = language === 'ar' ? 'ar-SA' : 'en-US';
            utterance.rate = 1.0;
            utterance.pitch = 1.0;
            
            utterance.onend = resolve;
            utterance.onerror = reject;
            
            speechSynthesis.speak(utterance);
        });
    },
    
    /**
     * Cancel any ongoing speech
     */
    cancelSpeech() {
        // Stop backend edge-tts audio playback
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            if (this.currentAudio.src && this.currentAudio.src.startsWith('blob:')) {
                URL.revokeObjectURL(this.currentAudio.src);
            }
            this.currentAudio = null;
        }
        // Stop Web Speech API fallback
        if ('speechSynthesis' in window) {
            speechSynthesis.cancel();
        }
    },
    
    // ============ Hands-Free Mode Features ============
    
    /**
     * Start recording with automatic silence detection
     * @param {Function} onSilence - Callback when silence is detected
     * @param {number} silenceDuration - How long silence should last before triggering (seconds)
     */
    async startRecordingWithSilenceDetection(onSilence, silenceDuration = 1.5) {
        this.onSilenceCallback = onSilence;
        this._silenceTriggered = false;
        
        // Ensure audio context is running
        if (this.audioContext && this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
        
        // Start normal recording
        await this.startRecording();
        
        // Start silence detection
        this.startSilenceDetection(silenceDuration);
        
        // Set maximum recording timeout as fallback - save callback ref before clearing
        const savedCallback = onSilence;
        this.maxRecordingTimeout = setTimeout(() => {
            console.log('[VAD] Max recording time reached (15s), force-stopping');
            if (this.isRecording && !this._silenceTriggered) {
                this._silenceTriggered = true;
                this._cleanupSilenceDetection();
                savedCallback();
            }
        }, this.maxRecordingTime);
    },
    
    /**
     * Start monitoring audio levels for silence detection
     */
    startSilenceDetection(silenceDuration) {
        this.silenceStartTime = null;
        const silenceDurationMs = silenceDuration * 1000;
        let hasDetectedSpeech = false;
        let consecutiveSilenceChecks = 0;
        const requiredSilenceChecks = Math.ceil(silenceDurationMs / 100);
        let logCounter = 0;
        
        const minRecordingTime = 1000;
        const recordingStartTime = Date.now();
        
        console.log('[VAD] Starting silence detection, threshold:', this.silenceThreshold, 'required checks:', requiredSilenceChecks);
        
        this.silenceDetectionInterval = setInterval(() => {
            if (!this.isRecording || this._silenceTriggered) {
                this._cleanupSilenceDetection();
                return;
            }
            
            const level = this.getAudioLevel();
            const timeSinceStart = Date.now() - recordingStartTime;
            
            // Log every 500ms so we can see what's happening
            logCounter++;
            if (logCounter % 5 === 0) {
                console.log(`[VAD] level=${level.toFixed(4)} speech=${hasDetectedSpeech} silenceChecks=${consecutiveSilenceChecks}/${requiredSilenceChecks} time=${timeSinceStart}ms`);
            }
            
            if (level > this.silenceThreshold) {
                hasDetectedSpeech = true;
                consecutiveSilenceChecks = 0;
                this.silenceStartTime = null;
            } else {
                consecutiveSilenceChecks++;
                
                if (hasDetectedSpeech && timeSinceStart >= minRecordingTime) {
                    if (!this.silenceStartTime) {
                        this.silenceStartTime = Date.now();
                    }
                    
                    if (consecutiveSilenceChecks >= requiredSilenceChecks) {
                        console.log('[VAD] Silence detected after', timeSinceStart, 'ms, stopping recording');
                        if (!this._silenceTriggered) {
                            this._silenceTriggered = true;
                            const cb = this.onSilenceCallback;
                            this._cleanupSilenceDetection();
                            if (cb) cb();
                        }
                    }
                }
            }
        }, 100);
    },
    
    /**
     * Internal cleanup - clears intervals/timeouts without nullifying callback
     */
    _cleanupSilenceDetection() {
        if (this.silenceDetectionInterval) {
            clearInterval(this.silenceDetectionInterval);
            this.silenceDetectionInterval = null;
        }
        if (this.maxRecordingTimeout) {
            clearTimeout(this.maxRecordingTimeout);
            this.maxRecordingTimeout = null;
        }
        this.silenceStartTime = null;
    },
    
    /**
     * Stop silence detection (public API)
     */
    stopSilenceDetection() {
        this._silenceTriggered = true;
        this._cleanupSilenceDetection();
        this.onSilenceCallback = null;
    },
    
    // ============ Audio Feedback Tones ============
    
    /**
     * Play a ready/start beep (higher pitch, friendly)
     */
    async playReadyBeep() {
        await this.playTone(880, 0.15, 'sine', 0.3); // A5 note
    },
    
    /**
     * Play a listening beep (short double beep)
     */
    async playListeningBeep() {
        await this.playTone(660, 0.1, 'sine', 0.2);
        await this.delay(50);
        await this.playTone(880, 0.1, 'sine', 0.2);
    },
    
    /**
     * Play a processing beep (lower, subtle)
     */
    async playProcessingBeep() {
        await this.playTone(440, 0.1, 'sine', 0.15);
    },
    
    /**
     * Play an error beep (descending tone)
     */
    async playErrorBeep() {
        await this.playTone(440, 0.15, 'square', 0.2);
        await this.delay(50);
        await this.playTone(330, 0.2, 'square', 0.2);
    },
    
    /**
     * Play a success beep (ascending tone)
     */
    async playSuccessBeep() {
        await this.playTone(523, 0.1, 'sine', 0.2); // C5
        await this.delay(50);
        await this.playTone(659, 0.1, 'sine', 0.2); // E5
        await this.delay(50);
        await this.playTone(784, 0.15, 'sine', 0.2); // G5
    },
    
    /**
     * Play a tone using Web Audio API
     * @param {number} frequency - Frequency in Hz
     * @param {number} duration - Duration in seconds
     * @param {string} type - Oscillator type: 'sine', 'square', 'sawtooth', 'triangle'
     * @param {number} volume - Volume 0-1
     */
    async playTone(frequency, duration, type = 'sine', volume = 0.3) {
        return new Promise((resolve) => {
            try {
                // Create audio context if needed
                if (!this.audioContext) {
                    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                }
                
                // Resume context if suspended (browser autoplay policy)
                if (this.audioContext.state === 'suspended') {
                    this.audioContext.resume();
                }
                
                const oscillator = this.audioContext.createOscillator();
                const gainNode = this.audioContext.createGain();
                
                oscillator.type = type;
                oscillator.frequency.setValueAtTime(frequency, this.audioContext.currentTime);
                
                // Envelope for smooth sound
                gainNode.gain.setValueAtTime(0, this.audioContext.currentTime);
                gainNode.gain.linearRampToValueAtTime(volume, this.audioContext.currentTime + 0.01);
                gainNode.gain.linearRampToValueAtTime(0, this.audioContext.currentTime + duration);
                
                oscillator.connect(gainNode);
                gainNode.connect(this.audioContext.destination);
                
                oscillator.start(this.audioContext.currentTime);
                oscillator.stop(this.audioContext.currentTime + duration);
                
                oscillator.onended = resolve;
            } catch (error) {
                console.warn('Failed to play tone:', error);
                resolve();
            }
        });
    },
    
    /**
     * Simple delay helper
     */
    delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
};

// Export for use in other modules
window.Voice = Voice;
