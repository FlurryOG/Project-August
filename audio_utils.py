"""
audio_utils.py — Microphone capture helpers for August.

Provides:
  - MicrophoneStream: A context manager that streams raw 16kHz mono PCM
    chunks as numpy int16 arrays (for wake word detection).
  - record_until_silence: Records audio after wake word fires, stopping
    when the user stops speaking. Returns a float32 numpy array for Whisper.
"""

import time
import logging
import queue
import threading
from typing import Generator

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# Audio format required by both openWakeWord and faster-whisper
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE_RECORD = "int16"    # openWakeWord expects int16
DTYPE_WHISPER = "float32" # faster-whisper expects float32


def list_input_devices() -> None:
    """Print all available input devices to help the user debug mic issues."""
    print("\n[AUDIO] Available input devices:")
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            print(f"  [{i}] {dev['name']}  (inputs: {dev['max_input_channels']})")
    print()


def get_default_input_device() -> int | None:
    """Return the index of the default input device, or None if not found."""
    try:
        device_info = sd.query_devices(kind="input")
        return device_info.get("index")
    except Exception:
        return None


class MicrophoneStream:
    """
    A context manager for streaming microphone audio in fixed-size chunks.

    Yields numpy int16 arrays of shape (chunk_samples,) — ready to be
    passed directly to openwakeword's Model.predict().

    Usage:
        with MicrophoneStream(chunk_ms=80) as stream:
            for chunk in stream:
                score = wakeword_model.predict(chunk)
    """

    def __init__(self, chunk_ms: int = 80, device: int | None = None):
        """
        Args:
            chunk_ms: Length of each audio chunk in milliseconds.
            device:   sounddevice input device index. None = system default.
        """
        self.chunk_ms = chunk_ms
        self.device = device
        self.chunk_samples = int(SAMPLE_RATE * chunk_ms / 1000)
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._stop_event = threading.Event()

    def _callback(self, indata: np.ndarray, frames: int,
                  time_info, status) -> None:
        """sounddevice callback — called from a background audio thread."""
        if status:
            logger.debug("sounddevice status: %s", status)
        # Copy is required because indata is a view into a C buffer
        self._queue.put(indata.copy().flatten())

    def __enter__(self) -> "MicrophoneStream":
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE_RECORD,
            blocksize=self.chunk_samples,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        logger.debug("MicrophoneStream started (chunk_ms=%d, device=%s)",
                     self.chunk_ms, self.device)
        return self

    def __iter__(self) -> Generator[np.ndarray, None, None]:
        while not self._stop_event.is_set():
            try:
                chunk = self._queue.get(timeout=0.5)
                yield chunk
            except queue.Empty:
                continue

    def stop(self) -> None:
        """Signal the stream to stop on the next iteration."""
        self._stop_event.set()

    def __exit__(self, *args) -> None:
        self._stop_event.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()
        logger.debug("MicrophoneStream closed.")


def record_until_silence(
    sample_rate: int = SAMPLE_RATE,
    silence_threshold_db: float = -40.0,
    silence_duration_s: float = 1.5,
    max_record_s: float = 30.0,
    device: int | None = None,
) -> np.ndarray:
    """
    Record audio from the microphone until the user stops speaking.

    Uses an energy-based voice activity detector: recording stops when
    the audio level drops below `silence_threshold_db` for `silence_duration_s`
    seconds, or when `max_record_s` is reached.

    Args:
        sample_rate:          Target sample rate in Hz (default: 16000).
        silence_threshold_db: RMS level in dB below which audio is silence.
        silence_duration_s:   Seconds of consecutive silence to stop.
        max_record_s:         Hard cap on recording length.
        device:               sounddevice device index (None = default).

    Returns:
        A float32 numpy array of shape (n_samples,) normalised to [-1.0, 1.0],
        suitable for passing directly to faster-whisper's transcribe().
    """
    chunk_ms = 30  # Small chunks for responsive silence detection
    chunk_samples = int(sample_rate * chunk_ms / 1000)
    silence_chunks_needed = int(silence_duration_s * 1000 / chunk_ms)
    max_chunks = int(max_record_s * 1000 / chunk_ms)

    audio_chunks: list[np.ndarray] = []
    silence_count = 0
    chunk_count = 0

    audio_queue: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            logger.debug("Record callback status: %s", status)
        audio_queue.put(indata.copy().flatten())

    with sd.InputStream(
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype=DTYPE_RECORD,
        blocksize=chunk_samples,
        device=device,
        callback=callback,
    ):
        while chunk_count < max_chunks:
            try:
                chunk = audio_queue.get(timeout=1.0)
            except queue.Empty:
                break

            audio_chunks.append(chunk)
            chunk_count += 1

            # Compute RMS energy in dB
            rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            if rms > 0:
                db = 20 * np.log10(rms / 32768.0)
            else:
                db = -120.0

            if db < silence_threshold_db:
                silence_count += 1
            else:
                silence_count = 0

            # Stop when sustained silence detected AND we have some audio
            if silence_count >= silence_chunks_needed and len(audio_chunks) > silence_chunks_needed:
                logger.debug("Silence detected — stopping recording.")
                break

    if not audio_chunks:
        logger.warning("No audio recorded!")
        return np.zeros(0, dtype=np.float32)

    # Concatenate and normalise to float32 [-1.0, 1.0] for Whisper
    raw = np.concatenate(audio_chunks).astype(np.float32)
    normalised = raw / 32768.0
    logger.debug("Recorded %.2f seconds of audio.", len(normalised) / sample_rate)
    return normalised
