import logging
import re
import os
import ctypes
import asyncio

logger = logging.getLogger(__name__)

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

    def load(self) -> None:
        """Initialize the speech engine based on mode."""
        if self.mode == "edge":
            logger.info("TTS Speaker using Edge Neural voices (voice=%s)", self.edge_voice)
            self._loop = asyncio.new_event_loop()
            return

        # Local mode (pyttsx3)
        self._init_local()

    def _init_local(self) -> None:
        try:
            import pyttsx3
            self._local_engine = pyttsx3.init()
            self._local_engine.setProperty("rate", self.rate)
            self._local_engine.setProperty("volume", 1.0)
            
            voices = self._local_engine.getProperty("voices")
            selected_voice = None
            for voice in voices:
                name_lower = voice.name.lower()
                if self.voice_type not in ("female", "male") and self.voice_type in name_lower:
                    selected_voice = voice.id
                    break
                elif self.voice_type == "female" and "zira" in name_lower:
                    selected_voice = voice.id
                    break
                elif self.voice_type == "male" and "david" in name_lower:
                    selected_voice = voice.id
                    break
            
            if selected_voice:
                self._local_engine.setProperty("voice", selected_voice)
                logger.info("TTS Speaker using local voice ID: %s", selected_voice)
            elif voices:
                self._local_engine.setProperty("voice", voices[0].id)
            
            logger.info("TTS Speaker initialized successfully (local SAPI5 mode)")
        except Exception as e:
            logger.error("Failed to initialize pyttsx3 TTS: %s. Falling back to Edge TTS.", e)
            self.mode = "edge"
            self._loop = asyncio.new_event_loop()

    def speak(self, text: str) -> None:
        """Speak the given text aloud."""
        # Clean markdown formatting
        clean_text = re.sub(r'[\\*\\_`#]', '', text).strip()
        if not clean_text:
            return

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

    def _speak_local(self, clean_text: str) -> None:
        if self._local_engine is None:
            self._init_local()
        if self._local_engine is None:
            return

        try:
            self._local_engine.say(clean_text)
            self._local_engine.runAndWait()
        except Exception as e:
            logger.error("Local SAPI5 speak failed: %s", e)

    async def _speak_edge(self, clean_text: str) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(clean_text, self.edge_voice)
        await communicate.save(self._temp_file)
        
        # Play the audio using native Windows MCI player
        abs_path = os.path.abspath(self._temp_file)
        open_cmd = f'open "{abs_path}" type mpegvideo alias august_audio'
        play_cmd = 'play august_audio wait'
        close_cmd = 'close august_audio'
        
        try:
            ctypes.windll.winmm.mciSendStringW(open_cmd, None, 0, 0)
            ctypes.windll.winmm.mciSendStringW(play_cmd, None, 0, 0)
        finally:
            ctypes.windll.winmm.mciSendStringW(close_cmd, None, 0, 0)
            if os.path.exists(self._temp_file):
                try:
                    os.remove(self._temp_file)
                except Exception:
                    pass
