/**
 * WSVoice — Unified WebSocket voice client.
 *
 * Manages a single WebSocket connection to /ws/voice that streams audio chunks
 * to the backend and receives partial transcripts, LLM tokens, and TTS audio.
 *
 * Usage:
 *   WSVoice.connect();
 *   WSVoice.startListening();   // begins recording + streaming chunks
 *   WSVoice.stopListening();    // sends end_audio, triggers pipeline
 *   WSVoice.cancel();           // abort current cycle
 *   WSVoice.disconnect();
 */
const WSVoice = {
    ws: null,
    connected: false,
    reconnectTimer: null,
    reconnectDelay: 1000,
    maxReconnectDelay: 16000,

    mediaRecorder: null,
    audioContext: null,
    analyser: null,
    currentStream: null,
    isListening: false,

    silenceDetectionInterval: null,
    silenceStartTime: null,
    silenceThreshold: 0.01,
    silenceDuration: 1.5,
    maxRecordingTime: 15000,
    maxRecordingTimeout: null,
    _silenceTriggered: false,

    language: 'ar',
    voiceMode: true,

    _audioQueue: [],
    _audioPlaying: false,
    _ttsResolve: null,

    callbacks: {
        onPartialTranscript: null,
        onTranscript: null,
        onRoute: null,
        onToken: null,
        onThinkingStart: null,
        onThinking: null,
        onThinkingEnd: null,
        onCitations: null,
        onTTSStart: null,
        onDone: null,
        onError: null,
        onClear: null,
        onConnected: null,
        onDisconnected: null,
    },

    // ------------------------------------------------------------------
    // Connection management
    // ------------------------------------------------------------------

    connect() {
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws/voice`;
        this.ws = new WebSocket(url);
        this.ws.binaryType = 'arraybuffer';

        this.ws.onopen = () => {
            this.connected = true;
            this.reconnectDelay = 1000;
            console.log('[WSVoice] connected');
            this.sendConfig();
            this.callbacks.onConnected?.();
        };

        this.ws.onclose = () => {
            this.connected = false;
            console.log('[WSVoice] disconnected');
            this.callbacks.onDisconnected?.();
            this._scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            console.warn('[WSVoice] error', err);
        };

        this.ws.onmessage = (event) => this._handleMessage(event);
    },

    disconnect() {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
        this.connected = false;
        this._stopRecording();
    },

    _scheduleReconnect() {
        if (this.reconnectTimer) return;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    },

    sendConfig() {
        this._sendJSON({
            type: 'config',
            language: this.language,
            voice_mode: this.voiceMode,
        });
    },

    // ------------------------------------------------------------------
    // Recording + streaming audio chunks
    // ------------------------------------------------------------------

    async startListening() {
        if (this.isListening) return;
        if (!this.connected) {
            this.connect();
            await this._waitForConnection(3000);
            if (!this.connected) {
                console.warn('[WSVoice] not connected, cannot start');
                return false;
            }
        }
        this.sendConfig();
        this._audioQueue = [];
        this._audioPlaying = false;
        this._silenceTriggered = false;

        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
            this.currentStream = stream;

            if (!this.audioContext) {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }

            const source = this.audioContext.createMediaStreamSource(stream);
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 256;
            this.analyser.smoothingTimeConstant = 0.3;
            source.connect(this.analyser);

            const mimeTypes = [
                'audio/webm;codecs=opus',
                'audio/webm',
                'audio/ogg;codecs=opus',
                'audio/mp4',
            ];
            let mimeType = '';
            for (const t of mimeTypes) {
                if (MediaRecorder.isTypeSupported(t)) { mimeType = t; break; }
            }
            const opts = mimeType ? { mimeType } : {};
            this.mediaRecorder = new MediaRecorder(stream, opts);

            this._chunkCount = 0;
            this._totalBytesSent = 0;
            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0 && this.connected && this.ws.readyState === WebSocket.OPEN) {
                    e.data.arrayBuffer().then((buf) => {
                        this._chunkCount++;
                        this._totalBytesSent += buf.byteLength;
                        if (this._chunkCount === 1) {
                            console.log('[WSVoice] first audio chunk sent |', buf.byteLength, 'bytes');
                        }
                        if (this._chunkCount % 20 === 0) {
                            console.log('[WSVoice] chunks sent:', this._chunkCount, '| total:', this._totalBytesSent, 'bytes');
                        }
                        this.ws.send(buf);
                    });
                }
            };

            this.mediaRecorder.start(100);
            this.isListening = true;
            this._startSilenceDetection();

            this.maxRecordingTimeout = setTimeout(() => {
                if (this.isListening && !this._silenceTriggered) {
                    console.log('[WSVoice] max recording time reached');
                    this._silenceTriggered = true;
                    this.stopListening();
                }
            }, this.maxRecordingTime);

            return true;
        } catch (err) {
            console.error('[WSVoice] startListening failed:', err);
            return false;
        }
    },

    stopListening() {
        if (!this.isListening) return;
        console.log('[WSVoice] stopListening | chunks:', this._chunkCount, '| total bytes:', this._totalBytesSent);
        this._clearSilenceDetection();
        this._stopRecording();
        this._sendJSON({ type: 'end_audio' });
    },

    cancel() {
        this._clearSilenceDetection();
        this._stopRecording();
        this._cancelAudio();
        this._sendJSON({ type: 'cancel' });
    },

    _stopRecording() {
        this.isListening = false;
        if (this.maxRecordingTimeout) {
            clearTimeout(this.maxRecordingTimeout);
            this.maxRecordingTimeout = null;
        }
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            try { this.mediaRecorder.stop(); } catch (_) { /* noop */ }
        }
        if (this.currentStream) {
            this.currentStream.getTracks().forEach((t) => t.stop());
            this.currentStream = null;
        }
        this.mediaRecorder = null;
    },

    // ------------------------------------------------------------------
    // Silence detection (VAD)
    // ------------------------------------------------------------------

    _startSilenceDetection() {
        this.silenceStartTime = null;
        let hasDetectedSpeech = false;
        let consecutiveSilenceChecks = 0;
        const requiredChecks = Math.ceil((this.silenceDuration * 1000) / 100);
        const recordingStart = Date.now();
        const minRecordingTime = 1000;

        this.silenceDetectionInterval = setInterval(() => {
            if (!this.isListening || this._silenceTriggered) {
                this._clearSilenceDetection();
                return;
            }
            const level = this._getAudioLevel();
            const elapsed = Date.now() - recordingStart;

            if (level > this.silenceThreshold) {
                hasDetectedSpeech = true;
                consecutiveSilenceChecks = 0;
                this.silenceStartTime = null;
            } else {
                consecutiveSilenceChecks++;
                if (hasDetectedSpeech && elapsed >= minRecordingTime) {
                    if (!this.silenceStartTime) this.silenceStartTime = Date.now();
                    if (consecutiveSilenceChecks >= requiredChecks) {
                        console.log('[WSVoice] silence detected');
                        this._silenceTriggered = true;
                        this._clearSilenceDetection();
                        this.stopListening();
                    }
                }
            }
        }, 100);
    },

    _clearSilenceDetection() {
        if (this.silenceDetectionInterval) {
            clearInterval(this.silenceDetectionInterval);
            this.silenceDetectionInterval = null;
        }
        this.silenceStartTime = null;
    },

    _getAudioLevel() {
        if (!this.analyser) return 0;
        if (this.audioContext && this.audioContext.state !== 'running') {
            this.audioContext.resume();
            return 0;
        }
        const buf = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
        }
        return Math.sqrt(sum / buf.length);
    },

    // ------------------------------------------------------------------
    // Incoming message handler
    // ------------------------------------------------------------------

    _handleMessage(event) {
        if (event.data instanceof ArrayBuffer) {
            console.log('[WSVoice] <<< TTS audio |', event.data.byteLength, 'bytes | queue:', this._audioQueue.length + 1);
            this._enqueueAudio(event.data);
            return;
        }

        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }

        switch (msg.type) {
            case 'partial_transcript':
                console.log('[WSVoice] <<< partial_transcript |', msg.text);
                this.callbacks.onPartialTranscript?.(msg.text);
                break;
            case 'transcript':
                console.log('[WSVoice] <<< transcript |', msg.text, '| lang:', msg.language, '| conf:', msg.confidence);
                this.callbacks.onTranscript?.(msg.text, msg.language, msg.confidence);
                break;
            case 'route':
                console.log('[WSVoice] <<< route |', msg.intent);
                this.callbacks.onRoute?.(msg.intent);
                break;
            case 'token':
                this.callbacks.onToken?.(msg.content);
                break;
            case 'thinking_start':
                this.callbacks.onThinkingStart?.();
                break;
            case 'thinking':
                this.callbacks.onThinking?.(msg.content);
                break;
            case 'thinking_end':
                this.callbacks.onThinkingEnd?.();
                break;
            case 'citations':
                console.log('[WSVoice] <<< citations |', msg.citations?.length, 'refs');
                this.callbacks.onCitations?.(msg.citations, msg.refs_text);
                break;
            case 'tts_start':
                console.log('[WSVoice] <<< tts_start |', msg.sentence?.substring(0, 60));
                this.callbacks.onTTSStart?.(msg.sentence);
                break;
            case 'clear':
                console.log('[WSVoice] <<< clear');
                this.callbacks.onClear?.();
                break;
            case 'done':
                console.log('[WSVoice] <<< done | response length:', msg.full_response?.length || 0);
                this.callbacks.onDone?.(msg.full_response);
                this._finishAudioQueue();
                break;
            case 'error':
                console.error('[WSVoice] <<< error |', msg.message);
                this.callbacks.onError?.(msg.message);
                break;
        }
    },

    // ------------------------------------------------------------------
    // Audio playback queue (for TTS binary frames)
    // ------------------------------------------------------------------

    _enqueueAudio(arrayBuffer) {
        const blob = new Blob([arrayBuffer], { type: 'audio/mpeg' });
        this._audioQueue.push(blob);
        if (!this._audioPlaying) this._drainAudio();
    },

    async _drainAudio() {
        if (this._audioPlaying) return;
        this._audioPlaying = true;
        console.log('[WSVoice] audio drain started | queue:', this._audioQueue.length);

        let playedCount = 0;
        while (this._audioQueue.length > 0) {
            const blob = this._audioQueue.shift();
            try {
                playedCount++;
                console.log('[WSVoice] playing audio', playedCount, '| size:', blob.size, 'bytes | remaining:', this._audioQueue.length);
                await this._playBlob(blob);
            } catch (err) {
                console.warn('[WSVoice] audio playback error:', err);
            }
        }

        console.log('[WSVoice] audio drain complete | played:', playedCount);
        this._audioPlaying = false;
        this._ttsResolve?.();
        this._ttsResolve = null;
    },

    _playBlob(blob) {
        return new Promise((resolve, reject) => {
            const audio = new Audio();
            const url = URL.createObjectURL(blob);
            audio.src = url;

            audio.onended = () => {
                URL.revokeObjectURL(url);
                resolve();
            };
            audio.onerror = (e) => {
                URL.revokeObjectURL(url);
                reject(e);
            };
            audio.play().catch(reject);
        });
    },

    _cancelAudio() {
        this._audioQueue = [];
    },

    _finishAudioQueue() {
        // nothing special; drain will resolve naturally
    },

    waitForTTSComplete() {
        if (!this._audioPlaying && this._audioQueue.length === 0) {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            this._ttsResolve = resolve;
            if (!this._audioPlaying && this._audioQueue.length === 0) {
                resolve();
                this._ttsResolve = null;
            }
        });
    },

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    _sendJSON(obj) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(obj));
        }
    },

    _waitForConnection(timeoutMs) {
        return new Promise((resolve) => {
            if (this.connected) { resolve(); return; }
            const start = Date.now();
            const check = setInterval(() => {
                if (this.connected || Date.now() - start > timeoutMs) {
                    clearInterval(check);
                    resolve();
                }
            }, 100);
        });
    },

    isSupported() {
        return 'WebSocket' in window && 'MediaRecorder' in window;
    },
};

window.WSVoice = WSVoice;
