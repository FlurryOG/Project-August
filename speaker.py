import logging
import re
import os
import ctypes
from ctypes import wintypes
import threading
import asyncio

logger = logging.getLogger(__name__)

# Windows native global hotkey constants
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_UP = 0x26

class HotkeyThread(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self.user32 = ctypes.windll.user32

    def run(self):
        # Register Ctrl + Shift + Up Arrow (ID=1)
        if not self.user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT, VK_UP):
            logger.warning("Failed to register Windows global hotkey Ctrl+Shift+Up Arrow!")
            return
        logger.info("Registered Windows native global hotkey (Ctrl + Shift + Up Arrow)")
        try:
            msg = wintypes.MSG()
            while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    if msg.wParam == 1:
                        self.callback()
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self.user32.UnregisterHotKey(None, 1)

class Speaker:
    """
    Handles Text-to-Speech playback. Supports two modes:
      1. "edge"  — Microsoft Edge neural voices (100% free, premium quality, online)
      2. "local" — Windows SAPI5 voice engine via pyttsx3 (100% offline, standard quality)
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The speaker section from config.yaml, containing:
                    rate, voice_type, mode, edge_voice
        """
        self.rate: int = config.get("rate", 185)
        self.voice_type: str = config.get("voice_type", "female").lower()
        self.mode: str = config.get("mode", "edge").lower()  # "edge" or "local"
        self.edge_voice: str = config.get("edge_voice", "en-US-AriaNeural")
        
        self._local_engine = None
        self._loop = None
        self._temp_file = "august_speech.mp3"
        self.is_speaking = False

    def load(self) -> None:
        """Initialize the speech engine based on mode."""
        # Register global hotkey via Windows native thread
        try:
            self._hotkey_thread = HotkeyThread(self._on_shut_up)
            self._hotkey_thread.start()
        except Exception as e:
            logger.warning("Failed to start native hotkey listener: %s", e)

        if self.mode == "edge":
            logger.info("TTS Speaker using Edge Neural voices (voice=%s)", self.edge_voice)
            self._loop = asyncio.new_event_loop()
            return

        # Local mode (pyttsx3)
        self._init_local()

    def _init_local(self) -> None:
        try:
            import win32com.client
            self._sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
            logger.info("TTS Speaker initialized successfully (SAPI.SpVoice mode)")
        except Exception as e:
            logger.error("Failed to initialize SAPI.SpVoice: %s. Falling back to Edge TTS.", e)
            self.mode = "edge"
            self._loop = asyncio.new_event_loop()

    def speak(self, text: str) -> None:
        """Speak the given text aloud."""
        clean_text = re.sub(r'[\\*\\_`#]', '', text).strip()
        if not clean_text:
            return

        self.is_speaking = True
        try:
            if self.mode == "edge":
                try:
                    if self._loop is None:
                        self._loop = asyncio.new_event_loop()
                    self._loop.run_until_complete(self._speak_edge(clean_text))
                except Exception as e:
                    logger.error("Edge TTS speak failed: %s. Trying local fallback...", e)
                    self._speak_local(clean_text)
            else:
                self._speak_local(clean_text)
        finally:
            self.is_speaking = False

    def _speak_local(self, clean_text: str) -> None:
        if not hasattr(self, "_sapi_voice") or self._sapi_voice is None:
            self._init_local()
        if not hasattr(self, "_sapi_voice") or self._sapi_voice is None:
            return

        self.is_speaking = True
        self._should_stop = False
        try:
            # 1 = SVSFlagsAsync
            self._sapi_voice.Speak(clean_text, 1)
            import time
            while self._sapi_voice.Status.RunningState != 1:  # 1 = SRSEnd
                if self._should_stop:
                    self._sapi_voice.Speak("", 3)  # 3 = SVSFPurgeBeforeSpeak
                    break
                time.sleep(0.05)
        except Exception as e:
            logger.error("SAPI5 speak failed: %s", e)
        finally:
            self.is_speaking = False

    async def _speak_edge(self, clean_text: str) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(clean_text, self.edge_voice)
        await communicate.save(self._temp_file)
        
        # Play the audio using native Windows MCI player
        abs_path = os.path.abspath(self._temp_file)
        open_cmd = f'open "{abs_path}" type mpegvideo alias august_audio'
        
        try:
            # Close first in case of leftover devices
            ctypes.windll.winmm.mciSendStringW("close august_audio", None, 0, 0)
            ctypes.windll.winmm.mciSendStringW(open_cmd, None, 0, 0)
            ctypes.windll.winmm.mciSendStringW("play august_audio", None, 0, 0)
            
            # Poll status in loop to remain non-blocking (allows interruption)
            status_buffer = ctypes.create_unicode_buffer(64)
            self._should_stop = False
            while True:
                if self._should_stop:
                    break
                ctypes.windll.winmm.mciSendStringW("status august_audio mode", status_buffer, 64, 0)
                mode = status_buffer.value.strip().lower()
                if mode not in ("playing", "paused"):
                    break
                await asyncio.sleep(0.05)
        finally:
            ctypes.windll.winmm.mciSendStringW("close august_audio", None, 0, 0)
            if os.path.exists(self._temp_file):
                try:
                    os.remove(self._temp_file)
                except Exception:
                    pass

    def _on_shut_up(self) -> None:
        """Callback to interrupt current speech and say a custom message."""
        # Only work if August is actually speaking/yapping
        if not getattr(self, "is_speaking", False):
            return

        logger.info("Global 'Shut Up' shortcut triggered while speaking.")
        
        # Set stop flag to interrupt the active speaking loop instantly
        self._should_stop = True
        
        # Purge SAPI SpVoice if active
        try:
            if hasattr(self, "_sapi_voice") and self._sapi_voice:
                self._sapi_voice.Speak("", 3)  # 3 = SVSFPurgeBeforeSpeak
        except Exception:
            pass

        # Small delay for cleanup before speaking apology
        import time
        time.sleep(0.1)

        try:
            self.speak_direct("sorry about that ima shut my mouth")
        except Exception as e:
            logger.error("Failed to speak shut up apology: %s", e)

    def speak_direct(self, text: str) -> None:
        """Plays speech directly without triggers or checks, used for internal system prompts."""
        clean_text = re.sub(r'[\\*\\_`#]', '', text).strip()
        if not clean_text:
            return

        if self.mode == "edge":
            try:
                if self._loop is None:
                    self._loop = asyncio.new_event_loop()
                import edge_tts
                communicate = edge_tts.Communicate(clean_text, self.edge_voice)
                self._loop.run_until_complete(communicate.save(self._temp_file))
                
                abs_path = os.path.abspath(self._temp_file)
                open_cmd = f'open "{abs_path}" type mpegvideo alias direct_audio'
                
                ctypes.windll.winmm.mciSendStringW(open_cmd, None, 0, 0)
                ctypes.windll.winmm.mciSendStringW("play direct_audio", None, 0, 0)
                
                # Poll status in loop
                import time
                status_buffer = ctypes.create_unicode_buffer(64)
                while True:
                    ctypes.windll.winmm.mciSendStringW("status direct_audio mode", status_buffer, 64, 0)
                    mode = status_buffer.value.strip().lower()
                    if mode != "playing":
                        break
                    time.sleep(0.05)
                
                ctypes.windll.winmm.mciSendStringW("close direct_audio", None, 0, 0)
                
                if os.path.exists(self._temp_file):
                    try:
                        os.remove(self._temp_file)
                    except Exception:
                        pass
            except Exception:
                self._speak_local(clean_text)
        else:
            self._speak_local(clean_text)
