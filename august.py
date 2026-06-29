"""
august.py — Main entry point for the August voice assistant.

Architecture:
  1. [STARTUP]  Load config, initialise all components
  2. [LISTEN]   Stream mic audio through wake word detector
  3. [WAKE]     On detection, record user's speech
  4. [TRANSCRIBE] Convert speech to text via faster-whisper (CUDA)
  5. [DISPATCH] Check if it's a system command
  6. [LLM]      If not a command, send to Ollama/llama3
  7. [LOOP]     Return to LISTEN

Press Ctrl+C to quit cleanly.
"""

import sys
import time
import logging
import warnings

# Suppress noisy python resource leak warnings from socket/file libraries
warnings.simplefilter("ignore", ResourceWarning)

# Load private keys/tokens from Tokens.txt into environment variables
import token_loader
token_loader.load_tokens()

import argparse
from pathlib import Path

import yaml
import dashboard

# ── Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError) ──────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING, # Keep root logger at WARNING to prevent spam
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("august")
logger.setLevel(logging.INFO) # Allow only August's own logs

# Silence verbose libraries
logging.getLogger("faster_whisper").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("comtypes").setLevel(logging.WARNING)

# ── ANSI colour helpers ─────────────────────────────────────────────────────────
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_BLUE    = "\033[94m"
C_GREEN   = "\033[92m"
C_YELLOW  = "\033[93m"
C_CYAN    = "\033[96m"
C_MAGENTA = "\033[95m"
C_RED     = "\033[91m"
C_DIM     = "\033[2m"


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    """Load and return the YAML config file."""
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def print_banner(config: dict, ollama_ok: bool, active_phrases: list[str]) -> None:
    """Print the startup status banner."""
    stt_model = config["stt"]["model_size"]
    stt_device = config["stt"]["device"].upper()
    llm_model = config["llm"]["model"]
    llm_host = config["llm"]["host"]
    cmd_count = len(config.get("commands", {}).get("entries", []))

    llm_status = f"{C_GREEN}✔ Connected{C_RESET}" if ollama_ok else f"{C_RED}✘ Offline{C_RESET}"
    phrase_list = "  |  ".join(f"{C_CYAN}'{p}'{C_RESET}" for p in active_phrases)

    print()
    print(f"  {C_BOLD}{C_BLUE}╔══════════════════════════════════════════════╗{C_RESET}")
    print(f"  {C_BOLD}{C_BLUE}║          A U G U S T  —  Voice AI            ║{C_RESET}")
    print(f"  {C_BOLD}{C_BLUE}╚══════════════════════════════════════════════╝{C_RESET}")
    print()
    print(f"  {C_DIM}Wake Phrases {C_RESET}  {phrase_list}")
    print(f"  {C_DIM}Speech-to-Text{C_RESET}  Whisper {C_CYAN}{stt_model!r}{C_RESET} on {C_CYAN}{stt_device}{C_RESET}")
    print(f"  {C_DIM}LLM Brain    {C_RESET}  {llm_model} @ {llm_host}  {llm_status}")
    print(f"  {C_DIM}Commands     {C_RESET}  {cmd_count} registered")
    print()
    print(f"  {C_GREEN}● Listening — say {phrase_list} to activate...{C_RESET}")
    print(f"  {C_DIM}Press Ctrl+C to quit.{C_RESET}")
    print()


def cprint(color: str, *args, **kwargs) -> None:
    """Print with ANSI colour, then reset."""
    print(color, end="")
    print(*args, **kwargs)
    print(C_RESET, end="", flush=True)


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def clean_transcription(text: str, active_phrases: list[str]) -> str:
    """Remove wake phrase prefixes and leading/trailing punctuation/spaces."""
    import re
    text_lower = text.lower().strip()
    
    # Strip any active wake phrases
    for phrase in active_phrases:
        p_low = phrase.lower()
        if text_lower.startswith(p_low):
            text = text[len(p_low):].strip()
            text_lower = text.lower().strip()
            
    # Also strip raw "august" if transcribed alone at the start
    if text_lower.startswith("august"):
        text = text[len("august"):].strip()
        
    # Strip leading/trailing punctuation (like commas, spaces, questions)
    text = re.sub(r"^[,\s\.\?!]+", "", text)
    text = re.sub(r"[,\s\.\?!]+$", "", text)
    
    return text.strip()


def contains_wake_word(text: str) -> bool:
    """Return True only if the transcription contains a 'hey august' style wake trigger.
    We require 'hey' (or a phonetic variant) before 'august' so that merely saying
    the month 'august' or the word 'august' in conversation does not trigger the assistant.
    """
    import re
    t_low = text.lower()

    # Phrases that represent "hey august" phonetically
    hey_august_variants = [
        r"hey\s+august",
        r"hay\s+august",
        r"hair\s+august",
        r"hey\s+all\s+guest",
        r"hey\s+our\s+guest",
        r"hey\s+are\s+guest",
        r"hey\s+august",
        r"hey,\s+august",
    ]
    for pattern in hey_august_variants:
        if re.search(pattern, t_low):
            return True
    return False




# ── Main assistant loop ────────────────────────────────────────────────────────

def run(config: dict) -> None:
    """Initialise all components and run the main listen → transcribe → respond loop."""
    from audio_utils import MicrophoneStream, record_until_silence, list_input_devices
    from wake_word import WakeWordDetector
    from transcriber import Transcriber
    from brain import Brain
    from commands import CommandDispatcher

    use_color = config.get("ui", {}).get("use_color", True)
    show_scores = config.get("ui", {}).get("show_scores", False)
    show_timestamps = config.get("ui", {}).get("show_timestamps", True)

    # ── 1. Load all components ────────────────────────────────────────
    cprint(C_DIM, "  [1/5] Loading wake word detector...")
    detector = WakeWordDetector(config["wake_word"])
    try:
        detector.load()
    except FileNotFoundError as e:
        print(f"\n  {C_RED}ERROR:{C_RESET} {e}")
        sys.exit(1)

    cprint(C_DIM, "  [2/5] Loading speech-to-text (Whisper)...")
    transcriber = Transcriber(config["stt"])
    transcriber.load()

    cprint(C_DIM, "  [3/5] Connecting to Ollama LLM...")
    brain = Brain(config["llm"])
    ollama_ok = brain.load()

    cprint(C_DIM, "  [4/5] Loading command dispatcher...")
    dispatcher = CommandDispatcher(config)

    cprint(C_DIM, "  [5/5] Initializing audio speaker...")
    from speaker import Speaker
    speaker = Speaker(config.get("speaker", {}))
    speaker.load()

    # ── Start dashboard server ────────────────────────────────────────
    dashboard.start_server(port=8000)
    dashboard.set_status("Listening...")

    # ── 2. Print startup banner ───────────────────────────────────────
    print_banner(config, ollama_ok, active_phrases=detector.active_phrases)

    # ── 3. Main loop ─────────────────────────────────────────────────
    rec_config = config.get("recording", {})
    chunk_ms = config["wake_word"].get("chunk_ms", 80)

    with MicrophoneStream(chunk_ms=chunk_ms) as mic:
        for audio_chunk in mic:
            # Check if config was updated via the dashboard panel
            if dashboard.check_config_updated():
                try:
                    with open("config.yaml", "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                    detector.update_threshold(config["wake_word"]["threshold"])
                    rec_config = config.get("recording", {})
                    
                    # Update brain settings live
                    if "llm" in config:
                        brain.provider = config["llm"].get("provider", "ollama")
                        brain.openai_api_key = config["llm"].get("openai_api_key", "")
                        brain.gemini_api_key = config["llm"].get("gemini_api_key", "")
                        brain.load()

                    # Update Spotify settings live
                    if "spotify" in config and hasattr(dispatcher, "spotify"):
                        dispatcher.spotify.client_id = config["spotify"].get("client_id", "")
                        dispatcher.spotify.client_secret = config["spotify"].get("client_secret", "")
                        dispatcher.spotify.redirect_uri = config["spotify"].get("redirect_uri", "http://localhost:9000")
                        dispatcher.spotify.enabled = config["spotify"].get("enabled", False)
                        if dispatcher.spotify.is_configured():
                            dispatcher.spotify.load()

                    cprint(C_YELLOW, "  [DASHBOARD] Settings reloaded live!")
                except Exception as e:
                    logger.error("Failed to reload config: %s", e)

            detected, triggered_phrase = detector.process_chunk(audio_chunk)

            if not detected:
                continue

            # ── WAKE WORD DETECTED ───────────────────────────────────
            detector.reset()
            ts = f"{C_DIM}[{timestamp()}]{C_RESET} " if show_timestamps else ""

            print()
            cprint(C_MAGENTA, f"  {ts}{C_BOLD}🎤 '{triggered_phrase}' — Listening...{C_RESET}")
            dashboard.set_status("Recording speech...")

            # ── RECORD SPEECH ────────────────────────────────────────
            audio = record_until_silence(
                sample_rate=rec_config.get("sample_rate", 16000),
                silence_threshold_db=rec_config.get("silence_threshold_db", -40.0),
                silence_duration_s=rec_config.get("silence_duration_s", 1.5),
                max_record_s=rec_config.get("max_record_s", 30.0),
            )

            # ── TRANSCRIBE ───────────────────────────────────────────
            dashboard.set_status("Transcribing speech...")
            text = transcriber.transcribe(audio)

            # Verification check: ignore false acoustic triggers (must contain wake word in transcription)
            if not text or not contains_wake_word(text):
                dashboard.set_status("Listening...")
                continue

            # Clean and validate speech transcription (ignore noise like sole periods or question marks)
            cleaned_text = clean_transcription(text, detector.active_phrases)

            import re
            if not cleaned_text or not re.search(r"[a-zA-Z0-9]", cleaned_text):
                dashboard.set_status("Listening...")
                speaker.speak("Sorry, I didn't catch that.")
                continue

            ts_str = f"{C_DIM}[{timestamp()}]{C_RESET} " if show_timestamps else ""
            print(f"\n  {ts_str}{C_CYAN}{C_BOLD}You:{C_RESET}{C_CYAN} {text}{C_RESET}")
            dashboard.add_history("You", text)

            # ── DISPATCH COMMAND ─────────────────────────────────────
            dashboard.set_status("Processing command...")
            result = dispatcher.dispatch(cleaned_text)

            if result.matched:
                ts_str = f"{C_DIM}[{timestamp()}]{C_RESET} " if show_timestamps else ""
                print(f"  {ts_str}{C_GREEN}{C_BOLD}August:{C_RESET}{C_GREEN} {result.response_text}{C_RESET}")
                dashboard.add_history("August", result.response_text)
                
                # Speak response before running action
                speaker.speak(result.response_text)

                if result.action_callback:
                    try:
                        result.action_callback()
                    except Exception as e:
                        logger.error("Failed to run action: %s", e)

                if result.is_stop:
                    print()
                    cprint(C_BLUE, "  August is going to sleep. Goodbye!")
                    dashboard.set_status("Offline")
                    break
            else:
                # ── SEND TO LLM ──────────────────────────────────────────────────
                # Try to connect live each time — auto-recovers if Ollama starts later
                dashboard.set_status("Generating response...")
                if not brain.load():
                    response_text = "I can't reach Ollama right now. Please open the Ollama app and try again."
                    cprint(C_RED, f"  August: {response_text}")
                    dashboard.add_history("August", response_text)
                    speaker.speak(response_text)
                else:
                    ts_str = f"{C_DIM}[{timestamp()}]{C_RESET} " if show_timestamps else ""
                    print(f"  {ts_str}{C_GREEN}{C_BOLD}August:{C_RESET}{C_GREEN} ", end="", flush=True)

                    full_response = ""
                    for token in brain.chat_stream(cleaned_text):
                        print(token, end="", flush=True)
                        full_response += token

                    print(C_RESET)
                    if not full_response.strip():
                        full_response = "I encountered an error trying to think. Please try again."
                    dashboard.add_history("August", full_response)
                    speaker.speak(full_response)

            dashboard.set_status("Listening...")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="August — Local Offline Voice Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python august.py                    # Run with default config.yaml
  python august.py --config my.yaml   # Run with a custom config file
  python august.py --list-devices     # Show available microphone devices
  python august.py --debug            # Enable verbose debug logging
        """,
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available microphone input devices and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled.")

    if args.list_devices:
        from audio_utils import list_input_devices
        list_input_devices()
        sys.exit(0)

    config = load_config(Path(args.config))

    try:
        run(config)
    except KeyboardInterrupt:
        print(f"\n\n  {C_BLUE}August shutting down. Goodbye!{C_RESET}\n")
    except Exception as e:
        logger.exception("Unhandled error in August main loop: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
