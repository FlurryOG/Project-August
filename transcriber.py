"""
transcriber.py — faster-whisper speech-to-text wrapper for August.

Loads the Whisper model once at startup and provides a simple
transcribe() method that takes a float32 numpy audio array and returns
the transcribed text string.

Hardware: Configured for RTX 3060 with CUDA + float16 compute.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class Transcriber:
    """
    Wraps faster-whisper for local, GPU-accelerated speech recognition.

    Usage:
        transcriber = Transcriber(config["stt"])
        transcriber.load()
        text = transcriber.transcribe(audio_float32_array)
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The stt section from config.yaml, containing:
                    model_size, device, compute_type, language, beam_size
        """
        self.model_size: str = config.get("model_size", "base")
        self.device: str = config.get("device", "cuda")
        self.compute_type: str = config.get("compute_type", "float16")
        self.language: str | None = config.get("language", "en") or None
        self.beam_size: int = config.get("beam_size", 5)
        self._model = None

    def load(self) -> None:
        """Load the Whisper model. This may take a few seconds on first run."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            )

        logger.info(
            "Loading Whisper model '%s' on %s (%s)...",
            self.model_size,
            self.device,
            self.compute_type,
        )

        try:
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("Whisper model loaded successfully.")
        except Exception as e:
            if self.device == "cuda":
                logger.warning(
                    "CUDA load failed (%s). Falling back to CPU (int8).", e
                )
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                )
                self.device = "cpu"
                self.compute_type = "int8"
                logger.info("Whisper model loaded on CPU fallback.")
            else:
                raise

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe a float32 audio array to text.

        Args:
            audio: float32 numpy array, shape (n_samples,), normalised to [-1, 1].
                   Must be sampled at 16kHz. Produced by audio_utils.record_until_silence().

        Returns:
            Transcribed text string, stripped of leading/trailing whitespace.
            Returns empty string if nothing was heard or transcription failed.
        """
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if audio is None or len(audio) == 0:
            logger.warning("Received empty audio — skipping transcription.")
            return ""

        # Minimum audio length check (~0.3s) to avoid hallucinations on silence
        if len(audio) < 16000 * 0.3:
            logger.debug("Audio too short to transcribe (%.2f s).", len(audio) / 16000)
            return ""

        try:
            segments, info = self._model.transcribe(
                audio,
                beam_size=self.beam_size,
                language=self.language,
                vad_filter=True,          # Built-in VAD to filter non-speech
                vad_parameters={
                    "min_silence_duration_ms": 500,
                },
            )

            logger.debug(
                "Detected language '%s' with probability %.2f",
                info.language,
                info.language_probability,
            )

            # Collect all segment texts
            texts = [segment.text.strip() for segment in segments]
            result = " ".join(t for t in texts if t)
            logger.debug("Transcription result: %r", result)
            return result

        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return ""
