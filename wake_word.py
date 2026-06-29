"""
wake_word.py — openWakeWord wrapper for August.

Supports multiple wake phrase models loaded simultaneously.
August activates when ANY of the configured models fires above threshold.

Supported activation phrases (each needs its own .onnx model):
  - "Hey August"      → models/hey_august.onnx
  - "August Initiate" → models/august_initiate.onnx

Until custom models are trained, placeholder built-in models are used.
See train_wakeword/README.md for training instructions.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class _SingleModelHandle:
    """
    Internal: wraps one openWakeWord Model instance for a single phrase.
    Supports a per-model threshold that overrides the global one.
    """

    def __init__(self, path: str, display_name: str, phrase: str,
                 threshold: float, per_model_threshold: float | None = None):
        self.path = path
        self.display_name = display_name
        self.phrase = phrase
        # Per-model threshold takes priority over the global threshold
        self.threshold = per_model_threshold if per_model_threshold is not None else threshold
        self._model = None
        self._key: str | None = None

    def load(self) -> None:
        from openwakeword.model import Model

        is_custom = self.path.endswith(".onnx") or Path(self.path).is_file()

        if is_custom:
            resolved = str(Path(self.path).resolve())
            if not Path(resolved).exists():
                raise FileNotFoundError(
                    f"Wake word model not found: {resolved}\n"
                    f"  Phrase: '{self.phrase}'\n"
                    f"  See train_wakeword/README.md to generate it."
                )
            self._model = Model(
                wakeword_models=[resolved],
                inference_framework="onnx",
            )
            self._key = Path(self.path).stem
        else:
            # Pre-built model referenced by name (e.g. "hey_jarvis", "alexa")
            self._model = Model(
                wakeword_models=[self.path],
                inference_framework="onnx",
            )
            self._key = self.path

        logger.info(
            "  ↳ Loaded model for '%s'  (path=%r, key=%r)",
            self.display_name,
            self.path,
            self._key,
        )

    def predict(self, audio_chunk: np.ndarray) -> tuple[bool, float]:
        """Return (detected, score) for this model."""
        if self._model is None:
            raise RuntimeError(f"Model for '{self.display_name}' not loaded.")

        predictions = self._model.predict(audio_chunk)

        score = 0.0
        if self._key and self._key in predictions:
            score = float(predictions[self._key])
        elif predictions:
            score = float(max(predictions.values()))

        return score >= self.threshold, score

    def reset(self) -> None:
        """Reset internal frame buffer to prevent double-trigger."""
        if self._model is None:
            return
        try:
            self._model.reset()
        except AttributeError:
            if hasattr(self._model, "prediction_buffer"):
                for key in self._model.prediction_buffer:
                    self._model.prediction_buffer[key].clear()


class WakeWordDetector:
    """
    Manages multiple wake phrase models and fires when any one of them
    detects its phrase above the configured threshold.

    Activation phrases (both trigger August):
      - "Hey August"      — natural conversational prefix
      - "August Initiate" — deliberate command-style suffix

    Usage:
        detector = WakeWordDetector(config["wake_word"])
        detector.load()

        with MicrophoneStream() as mic:
            for chunk in mic:
                detected, phrase = detector.process_chunk(chunk)
                if detected:
                    print(f"Activated by: '{phrase}'")
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The wake_word section from config.yaml.
                    Must contain 'models' list, 'threshold', and 'chunk_ms'.
        """
        self.threshold: float = config.get("threshold", 0.5)
        self.chunk_ms: int = config.get("chunk_ms", 80)
        model_entries: list[dict] = config.get("models", [])

        self._handles: list[_SingleModelHandle] = [
            _SingleModelHandle(
                path=entry["path"],
                display_name=entry.get("display_name", entry["path"]),
                phrase=entry.get("phrase", entry["path"]),
                threshold=self.threshold,
                per_model_threshold=entry.get("threshold"),  # None if not set
            )
            for entry in model_entries
        ]

        if not self._handles:
            raise ValueError(
                "No wake word models configured. "
                "Add at least one entry to wake_word.models in config.yaml."
            )

    def load(self) -> None:
        """Load all configured wake word models. Call once at startup."""
        try:
            import openwakeword  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "openwakeword is not installed. Run: pip install openwakeword"
            )

        phrases = " | ".join(f"'{h.display_name}'" for h in self._handles)
        logger.info("Loading wake word models — listening for: %s", phrases)

        for handle in self._handles:
            handle.load()

        logger.info(
            "Wake word detector ready. %d phrase(s) active. Threshold=%.2f",
            len(self._handles),
            self.threshold,
        )

    def update_threshold(self, threshold: float) -> None:
        """Update the global threshold for all models dynamically."""
        self.threshold = threshold
        for handle in self._handles:
            handle.threshold = threshold
        logger.info("Wake word threshold updated dynamically to %.2f", threshold)

    @property
    def active_phrases(self) -> list[str]:
        """Return display names of all configured activation phrases."""
        return [h.display_name for h in self._handles]

    def process_chunk(self, audio_chunk: np.ndarray) -> tuple[bool, str]:
        """
        Run all models on a single audio chunk.

        Returns:
            (True, display_name) if any model fired above threshold.
            (False, "")          if no model detected its phrase.

        The first model to fire wins (handles are checked in config order).
        """
        for handle in self._handles:
            detected, score = handle.predict(audio_chunk)
            if detected:
                logger.debug(
                    "Wake phrase detected: '%s' (score=%.3f)",
                    handle.display_name,
                    score,
                )
                return True, handle.display_name

        return False, ""

    def reset(self) -> None:
        """
        Reset all models' internal state after a detection.
        Prevents the same utterance from triggering twice.
        """
        for handle in self._handles:
            handle.reset()
