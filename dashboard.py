import datetime
import os
import threading
import yaml
from pathlib import Path

# Load private keys/tokens from Tokens.txt into environment variables
import token_loader
token_loader.load_tokens()

from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# Global state
CONVERSATION_HISTORY = []
ASSISTANT_STATUS = "Idle"
CONFIG_UPDATED_FLAG = False
CONFIG_PATH = Path("config.yaml")

# A simple lock to prevent concurrent writes to config.yaml
CONFIG_LOCK = threading.Lock()

def add_history(speaker: str, text: str):
    """Add a message to the live conversation history."""
    CONVERSATION_HISTORY.append({
        "speaker": speaker,
        "text": text,
        "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p")
    })
    # Keep history to a reasonable size
    if len(CONVERSATION_HISTORY) > 100:
        CONVERSATION_HISTORY.pop(0)

def set_status(status_str: str):
    """Update the live status of the assistant."""
    global ASSISTANT_STATUS
    ASSISTANT_STATUS = status_str

def check_config_updated() -> bool:
    """Check if the config has been updated via the web panel. Resets the flag on read."""
    global CONFIG_UPDATED_FLAG
    if CONFIG_UPDATED_FLAG:
        CONFIG_UPDATED_FLAG = False
        return True
    return False

# Embedded premium dashboard HTML page
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>August — Control Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #0B0F19;
            --bg-card: rgba(17, 24, 39, 0.7);
            --bg-input: #1F2937;
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #F3F4F6;
            --text-secondary: #9CA3AF;
            --accent-cyan: #06B6D4;
            --accent-emerald: #10B981;
            --accent-red: #EF4444;
            --glow-cyan: rgba(6, 182, 212, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-main);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        header {
            background-color: rgba(11, 15, 25, 0.8);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border-color);
            padding: 1.25rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            width: 2.2rem;
            height: 2.2rem;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-emerald));
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            color: #000;
            font-size: 1.1rem;
            box-shadow: 0 0 15px rgba(6, 182, 212, 0.4);
        }

        h1 {
            font-size: 1.5rem;
            font-weight: 600;
            letter-spacing: -0.5px;
            background: linear-gradient(to right, #FFF, var(--text-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status-badge {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            padding: 0.5rem 1rem;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.9rem;
            font-weight: 500;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--accent-emerald);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--accent-emerald);
        }

        .status-dot.listening {
            animation: pulse 1.5s infinite alternate;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.6; box-shadow: 0 0 4px var(--accent-cyan); }
            100% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 12px var(--accent-cyan); }
        }

        .main-container {
            flex: 1;
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 2rem;
            padding: 2rem;
            max-width: 1600px;
            width: 100%;
            margin: 0 auto;
        }

        @media (max-width: 1024px) {
            .main-container {
                grid-template-columns: 1fr;
            }
        }

        .panel {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(8px);
        }

        .panel-header {
            padding: 1.5rem;
            border-bottom: 1px solid var(--border-color);
            font-weight: 600;
            font-size: 1.1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Chat Transcript CSS */
        .chat-body {
            flex: 1;
            padding: 1.5rem;
            overflow-y: auto;
            max-height: 60vh;
            min-height: 450px;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .chat-message {
            max-width: 85%;
            padding: 0.9rem 1.2rem;
            border-radius: 14px;
            line-height: 1.4;
            font-size: 0.95rem;
            animation: slideIn 0.3s ease-out;
        }

        @keyframes slideIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message-user {
            background-color: var(--bg-input);
            border: 1px solid var(--border-color);
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }

        .message-august {
            background: linear-gradient(135deg, rgba(6, 182, 212, 0.12), rgba(16, 185, 129, 0.08));
            border: 1px solid rgba(6, 182, 212, 0.2);
            align-self: flex-start;
            border-bottom-left-radius: 4px;
        }

        .msg-meta {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-bottom: 0.3rem;
            display: flex;
            gap: 0.5rem;
        }

        .msg-meta.user {
            justify-content: flex-end;
        }

        .empty-chat {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            gap: 0.5rem;
            text-align: center;
        }

        /* Settings CSS */
        .settings-body {
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            overflow-y: auto;
        }

        .setting-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .setting-label {
            font-size: 0.9rem;
            font-weight: 500;
            color: var(--text-secondary);
            display: flex;
            justify-content: space-between;
        }

        .setting-value {
            color: var(--accent-cyan);
            font-weight: 600;
        }

        .slider-container {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        input[type="range"] {
            -webkit-appearance: none;
            width: 100%;
            height: 6px;
            background: var(--bg-input);
            border-radius: 4px;
            outline: none;
        }

        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 18px;
            height: 18px;
            border-radius: 50%;
            background: var(--accent-cyan);
            cursor: pointer;
            box-shadow: 0 0 8px rgba(6, 182, 212, 0.6);
            transition: transform 0.1s;
        }

        input[type="range"]::-webkit-slider-thumb:hover {
            transform: scale(1.2);
        }

        .btn-save {
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-emerald));
            color: #000;
            border: none;
            padding: 0.9rem;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: 0 4px 15px rgba(6, 182, 212, 0.2);
            margin-top: 1rem;
        }

        .btn-save:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(6, 182, 212, 0.4);
        }

        .btn-save:active {
            transform: translateY(1px);
        }

        .notification {
            background-color: rgba(16, 185, 129, 0.15);
            border: 1px solid var(--accent-emerald);
            color: #34D399;
            padding: 0.8rem;
            border-radius: 8px;
            font-size: 0.9rem;
            text-align: center;
            display: none;
            animation: fadeIn 0.3s;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        /* Commands Section */
        .commands-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            max-height: 250px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            background-color: rgba(255, 255, 255, 0.01);
            border-radius: 10px;
            padding: 1rem;
        }

        .command-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            font-size: 0.88rem;
        }

        .command-keywords {
            color: var(--text-secondary);
            font-size: 0.8rem;
        }

        .badge-type {
            font-size: 0.75rem;
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            background-color: var(--bg-input);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.2);
        }
    </style>
</head>
<body>

    <header>
        <div class="logo-section">
            <div class="logo-icon">A</div>
            <div>
                <h1>August Assistant</h1>
            </div>
        </div>
        <div style="display: flex; align-items: center; gap: 1rem;">
            <button id="tts-toggle-btn" onclick="toggleWebTTS()" class="status-badge" style="cursor: pointer; transition: all 0.3s ease; border-color: var(--accent-cyan); background: rgba(6, 182, 212, 0.08); color: #fff; outline: none; display: flex; align-items: center; gap: 0.5rem;">
                <span id="tts-icon">🔊</span>
                <span id="tts-text">Browser Audio: ON</span>
            </button>
            <div class="status-badge">
                <span class="status-dot listening" id="status-dot"></span>
                <span id="status-text">Listening...</span>
            </div>
        </div>
    </header>

    <div class="main-container">
        <!-- Chat Feed -->
        <div class="panel">
            <div class="panel-header">
                <span>Live Transcription Feed</span>
                <span style="font-size: 0.85rem; color: var(--text-secondary);" id="connection-status">Ollama Connected</span>
            </div>
            <div class="chat-body" id="chat-body">
                <!-- Live messages will render here -->
            </div>
        </div>

        <!-- Configurations -->
        <div class="panel">
            <div class="panel-header">Configuration & Tuning</div>
            <div class="settings-body">
                <div class="notification" id="save-notification">Settings updated successfully!</div>

                <!-- Wake Word Slider -->
                <div class="setting-group">
                    <div class="setting-label">
                        <span>Wake Word Sensitivity (Threshold)</span>
                        <span class="setting-value" id="ww-val">0.3</span>
                    </div>
                    <div class="slider-container">
                        <span style="font-size: 0.8rem; color: var(--text-secondary);">Quiet (0.9)</span>
                        <input type="range" id="ww-threshold" min="0.1" max="0.9" step="0.05" value="0.3" oninput="updateLabel('ww', this.value)">
                        <span style="font-size: 0.8rem; color: var(--text-secondary);">Sensitiv (0.1)</span>
                    </div>
                    <span style="font-size: 0.78rem; color: var(--text-secondary);">Lower threshold makes August easier to wake up (more sensitive).</span>
                </div>

                <!-- Silence Slider -->
                <div class="setting-group">
                    <div class="setting-label">
                        <span>Recording Silence Threshold</span>
                        <span class="setting-value" id="sil-val">-48 dB</span>
                    </div>
                    <div class="slider-container">
                        <span style="font-size: 0.8rem; color: var(--text-secondary);">-60 dB (Very Quiet)</span>
                        <input type="range" id="sil-threshold" min="-60" max="-20" step="1" value="-48" oninput="updateLabel('sil', this.value)">
                        <span style="font-size: 0.8rem; color: var(--text-secondary);">-20 dB (Loud)</span>
                    </div>
                    <span style="font-size: 0.78rem; color: var(--text-secondary);">Lower values prevent August from cutting off quiet or soft speech.</span>
                </div>

                <!-- Silence Duration -->
                <div class="setting-group">
                    <div class="setting-label">
                        <span>Silence Cutoff Duration</span>
                        <span class="setting-value" id="dur-val">1.5s</span>
                    </div>
                    <div class="slider-container">
                        <input type="range" id="sil-duration" min="0.5" max="4.0" step="0.1" value="1.5" oninput="updateLabel('dur', this.value)">
                    </div>
                    <span style="font-size: 0.78rem; color: var(--text-secondary);">Wait time after you stop speaking before August processes.</span>
                </div>

                <!-- AI Provider Selector -->
                <div class="setting-group" style="margin-top: 1rem;">
                    <div class="setting-label">
                        <span>AI Brain Provider</span>
                    </div>
                    <select id="llm-provider" onchange="toggleProviderFields(this.value)" style="width: 100%; padding: 0.6rem; border-radius: 8px; background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: #fff; font-size: 0.9rem; outline: none; margin-top: 0.2rem;">
                        <option value="ollama">Ollama (Local Offline)</option>
                        <option value="openai">ChatGPT (Cloud Online)</option>
                        <option value="gemini">Google Gemini (Cloud Online)</option>
                    </select>
                </div>

                <!-- OpenAI Key Input -->
                <div id="openai-key-group" class="setting-group" style="display: none; margin-top: 0.5rem;">
                    <div class="setting-label">
                        <span>OpenAI API Key</span>
                    </div>
                    <input type="password" id="openai-key" placeholder="sk-..." style="width: 100%; padding: 0.6rem; border-radius: 8px; background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: #fff; font-size: 0.9rem; outline: none; margin-top: 0.2rem;">
                </div>

                <!-- Gemini Key Input -->
                <div id="gemini-key-group" class="setting-group" style="display: none; margin-top: 0.5rem;">
                    <div class="setting-label">
                        <span>Gemini API Key</span>
                    </div>
                    <input type="password" id="gemini-key" placeholder="AIzaSy..." style="width: 100%; padding: 0.6rem; border-radius: 8px; background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: #fff; font-size: 0.9rem; outline: none; margin-top: 0.2rem;">
                </div>


                <!-- Registered Commands -->
                <div class="setting-group" style="margin-top: 1rem;">
                    <span class="setting-label">Registered Built-in & Custom Commands</span>
                    <div class="commands-list" id="commands-list">
                        <!-- Loaded from config -->
                    </div>
                </div>

                <button class="btn-save" onclick="saveSettings()">Apply & Save Configuration</button>
            </div>
        </div>
    </div>

    <script>
        let lastHistoryLength = 0;
        let webTtsEnabled = true;

        function updateLabel(type, val) {
            if (type === 'ww') {
                document.getElementById('ww-val').innerText = val;
            } else if (type === 'sil') {
                document.getElementById('sil-val').innerText = val + " dB";
            } else if (type === 'dur') {
                document.getElementById('dur-val').innerText = val + "s";
            }
        }

        function toggleWebTTS() {
            webTtsEnabled = !webTtsEnabled;
            const btn = document.getElementById('tts-toggle-btn');
            const icon = document.getElementById('tts-icon');
            const text = document.getElementById('tts-text');
            if (webTtsEnabled) {
                btn.style.borderColor = 'var(--accent-cyan)';
                btn.style.background = 'rgba(6, 182, 212, 0.08)';
                icon.innerText = '🔊';
                text.innerText = 'Browser Audio: ON';
                speakText("Browser audio feedback enabled");
            } else {
                btn.style.borderColor = 'var(--border-color)';
                btn.style.background = 'rgba(255, 255, 255, 0.02)';
                icon.innerText = '🔇';
                text.innerText = 'Browser Audio: OFF';
                if ('speechSynthesis' in window) {
                    window.speechSynthesis.cancel();
                }
            }
        }

        function speakText(txt) {
            if (!('speechSynthesis' in window)) return;
            window.speechSynthesis.cancel(); // cancel any active speech
            
            // Clean simple markdown formatting
            let clean = txt.replace(/[\\*\\_`#]/g, '').trim();
            if (!clean) return;

            const utterance = new SpeechSynthesisUtterance(clean);
            const voices = window.speechSynthesis.getVoices();
            // Try to select a natural Zira or standard English female voice
            let selectedVoice = voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('zira'));
            if (!selectedVoice) {
                selectedVoice = voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('google'));
            }
            if (!selectedVoice) {
                selectedVoice = voices.find(v => v.lang.startsWith('en'));
            }
            if (selectedVoice) {
                utterance.voice = selectedVoice;
            }
            utterance.rate = 1.05; // slightly faster for a more natural cadence
            window.speechSynthesis.speak(utterance);
        }

        // Pre-load voices list for speechSynthesis
        if ('speechSynthesis' in window) {
            window.speechSynthesis.getVoices();
            if (window.speechSynthesis.onvoiceschanged !== undefined) {
                window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
            }
        }

        // Fetch logs and conversation history
        function loadHistory() {
            fetch('/api/history')
                .then(res => res.json())
                .then(history => {
                    const body = document.getElementById('chat-body');
                    if (history.length === 0) {
                        body.innerHTML = `
                            <div class="empty-chat">
                                <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"/></svg>
                                <span>No speech events recorded yet. Say "Hey August" to begin!</span>
                            </div>
                        `;
                        return;
                    }

                    if (history.length !== lastHistoryLength) {
                        // Play Web Audio if a new message from August arrives
                        if (lastHistoryLength > 0 && history.length > lastHistoryLength) {
                            const lastMsg = history[history.length - 1];
                            if (lastMsg.speaker.toLowerCase() === 'august' && webTtsEnabled) {
                                speakText(lastMsg.text);
                            }
                        }

                        body.innerHTML = '';
                        history.forEach(msg => {
                            const isUser = msg.speaker.toLowerCase() === 'you';
                            const metaClass = isUser ? 'msg-meta user' : 'msg-meta';
                            const cardClass = isUser ? 'chat-message message-user' : 'chat-message message-august';
                            
                            body.innerHTML += `
                                <div class="${metaClass}">
                                    <strong>${msg.speaker}</strong>
                                    <span>${msg.timestamp}</span>
                                </div>
                                <div class="${cardClass}">
                                    ${msg.text}
                                </div>
                            `;
                        });
                        // Scroll to bottom
                        body.scrollTop = body.scrollHeight;
                        lastHistoryLength = history.length;
                    }
                });
        }

        function toggleProviderFields(provider) {
            const opGroup = document.getElementById('openai-key-group');
            const gemGroup = document.getElementById('gemini-key-group');
            opGroup.style.display = provider === 'openai' ? 'block' : 'none';
            gemGroup.style.display = provider === 'gemini' ? 'block' : 'none';
        }

        // Fetch config parameters
        function loadConfig() {
            fetch('/api/config')
                .then(res => res.json())
                .then(config => {
                    // Set inputs
                    const wwVal = config.wake_word.threshold;
                    document.getElementById('ww-threshold').value = wwVal;
                    updateLabel('ww', wwVal);

                    const silVal = config.recording.silence_threshold_db;
                    document.getElementById('sil-threshold').value = silVal;
                    updateLabel('sil', silVal);

                    const durVal = config.recording.silence_duration_s;
                    document.getElementById('sil-duration').value = durVal;
                    updateLabel('dur', durVal);

                    // Set provider and keys
                    const provider = (config.llm && config.llm.provider) || 'ollama';
                    document.getElementById('llm-provider').value = provider;
                    toggleProviderFields(provider);

                    document.getElementById('openai-key').value = (config.llm && config.llm.openai_api_key) || '';
                    document.getElementById('gemini-key').value = (config.llm && config.llm.gemini_api_key) || '';


                    // Load commands list
                    const cmdList = document.getElementById('commands-list');
                    cmdList.innerHTML = '';
                    config.commands.entries.forEach(entry => {
                        const keywords = entry.keywords.join(', ');
                        cmdList.innerHTML += `
                            <div class="command-item">
                                <div>
                                    <strong>${entry.name}</strong><br>
                                    <span class="command-keywords">Triggers: "${keywords}"</span>
                                </div>
                                <span class="badge-type">${entry.type}</span>
                            </div>
                        `;
                    });
                });
        }

        // Fetch Live Assistant Status
        function loadStatus() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('status-text').innerText = data.status;
                    const dot = document.getElementById('status-dot');
                    
                    if (data.status === 'Listening...') {
                        dot.className = 'status-dot listening';
                        dot.style.backgroundColor = 'var(--accent-emerald)';
                        dot.style.boxShadow = '0 0 10px var(--accent-emerald)';
                    } else if (data.status.includes('Listening') || data.status.includes('Active')) {
                        dot.className = 'status-dot';
                        dot.style.backgroundColor = 'var(--accent-emerald)';
                    } else if (data.status.includes('Offline') || data.status.includes('Error')) {
                        dot.className = 'status-dot';
                        dot.style.backgroundColor = 'var(--accent-red)';
                        dot.style.boxShadow = '0 0 10px var(--accent-red)';
                    } else {
                        dot.className = 'status-dot listening';
                        dot.style.backgroundColor = 'var(--accent-cyan)';
                        dot.style.boxShadow = '0 0 12px var(--accent-cyan)';
                    }
                });
        }

        // Save new config settings
        function saveSettings() {
            const wwVal = parseFloat(document.getElementById('ww-threshold').value);
            const silVal = parseInt(document.getElementById('sil-threshold').value);
            const durVal = parseFloat(document.getElementById('sil-duration').value);
            const providerVal = document.getElementById('llm-provider').value;
            const opKeyVal = document.getElementById('openai-key').value;
            const gemKeyVal = document.getElementById('gemini-key').value;

        // Save new config settings
        function saveSettings() {
            const wwVal = parseFloat(document.getElementById('ww-threshold').value);
            const silVal = parseInt(document.getElementById('sil-threshold').value);
            const durVal = parseFloat(document.getElementById('sil-duration').value);
            const providerVal = document.getElementById('llm-provider').value;
            const opKeyVal = document.getElementById('openai-key').value;
            const gemKeyVal = document.getElementById('gemini-key').value;

            fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    wake_word_threshold: wwVal,
                    silence_threshold_db: silVal,
                    silence_duration_s: durVal,
                    provider: providerVal,
                    openai_api_key: opKeyVal,
                    gemini_api_key: gemKeyVal
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const notif = document.getElementById('save-notification');
                    notif.style.display = 'block';
                    setTimeout(() => { notif.style.display = 'none'; }, 4000);
                }
            });
        }

        // Initialize polls
        loadConfig();
        setInterval(loadHistory, 1000);
        setInterval(loadStatus, 1000);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/status")
def status():
    return jsonify({
        "status": ASSISTANT_STATUS
    })

@app.route("/api/history")
def history():
    return jsonify(CONVERSATION_HISTORY)

@app.route("/api/config", methods=["GET", "POST"])
def config_api():
    global CONFIG_UPDATED_FLAG
    if request.method == "POST":
        data = request.json
        with CONFIG_LOCK:
            # Read current config
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
            else:
                config = {}

            # Update fields
            if "wake_word_threshold" in data:
                config.setdefault("wake_word", {})["threshold"] = data["wake_word_threshold"]
            if "silence_threshold_db" in data:
                config.setdefault("recording", {})["silence_threshold_db"] = data["silence_threshold_db"]
            if "silence_duration_s" in data:
                config.setdefault("recording", {})["silence_duration_s"] = data["silence_duration_s"]
            if "provider" in data:
                config.setdefault("llm", {})["provider"] = data["provider"]
            if "openai_api_key" in data:
                config.setdefault("llm", {})["openai_api_key"] = data["openai_api_key"]
            if "gemini_api_key" in data:
                config.setdefault("llm", {})["gemini_api_key"] = data["gemini_api_key"]


            # Save back to file
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        CONFIG_UPDATED_FLAG = True
        return jsonify({"success": True})

    # GET request: return YAML config parsed as JSON (excluding Spotify secrets)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    else:
        config = {}
    if "spotify" in config:
        config.pop("spotify")
    return jsonify(config)

@app.route("/api/history", methods=["POST"])
def add_history_api():
    data = request.json
    if "speaker" in data and "text" in data:
        add_history(data["speaker"], data["text"])
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid request body"}), 400

def _run_server(port: int):
    # Disable console logging of Flask requests to keep stdout clean for August
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def start_server(port: int = 8000):
    """Start the dashboard web server in a background thread."""
    t = threading.Thread(target=_run_server, args=(port,), daemon=True)
    t.start()
    print(f"  [DASHBOARD] Web panel started at http://localhost:{port}")
