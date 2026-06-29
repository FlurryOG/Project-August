"""
commands.py — Command dispatcher for August.

Reads commands from config.yaml and dispatches them based on keyword
matching against the user's transcribed speech. Supports opening URLs
in Opera, running scripts, and executing shell commands.
"""

import os
import re
import logging
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ANSI colour helpers (used when ui.use_color is True)
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RED = "\033[91m"
_RESET = "\033[0m"


class CommandResult:
    """Describes the result of a command dispatch attempt."""

    def __init__(
        self,
        matched: bool,
        command_name: str = "",
        response_text: str = "",
        is_stop: bool = False,
        action_callback = None,
    ):
        self.matched = matched          # True if a command was found and dispatched
        self.command_name = command_name
        self.response_text = response_text  # What August should say/print
        self.is_stop = is_stop          # True if user asked August to stop
        self.action_callback = action_callback  # Executed after speaking


class CommandDispatcher:
    """
    Matches transcribed speech against a list of commands from config.yaml
    and executes the appropriate action.

    Matching is case-insensitive and uses substring containment, so the
    user doesn't need to say the exact phrase — just close enough.

    Usage:
        dispatcher = CommandDispatcher(config)
        result = dispatcher.dispatch("open youtube please")
        if result.matched:
            print(result.response_text)
    """

    def __init__(self, config: dict):
        """
        Args:
            config: Full config dict. Uses config["commands"] and config["ui"].
        """
        self.entries: list[dict] = config.get("commands", {}).get("entries", [])
        self.opera_path: str = config.get("commands", {}).get("opera_path", "")
        self.use_color: bool = config.get("ui", {}).get("use_color", True)
        self.project_root = Path(__file__).parent

        # Expand %USERNAME% and similar env vars in opera_path
        self.opera_path = os.path.expandvars(self.opera_path)

        # Initialize Spotify controller if section is present
        from spotify_control import SpotifyController
        self.spotify = SpotifyController(config.get("spotify", {}))
        if self.spotify.is_configured():
            self.spotify.load()

    def dispatch(self, text: str) -> CommandResult:
        """
        Attempt to match `text` against all registered commands.

        Args:
            text: The transcribed speech string from faster-whisper.

        Returns:
            CommandResult with matched=True if a command was found.
        """
        normalised = text.lower().strip()

        # ── Math expression check (runs before command matching) ─────────────────
        math_result = self._try_math(normalised)
        if math_result is not None:
            return math_result

        # ── Spotify control check (runs before command matching) ─────────────────
        spotify_result = self._try_spotify(normalised)
        if spotify_result is not None:
            return spotify_result

        for entry in self.entries:
            keywords: list[str] = entry.get("keywords", [])

            if self._matches_any(normalised, keywords):
                name = entry.get("name", "Unknown Command")
                cmd_type = entry.get("type", "")

                logger.info("Command matched: '%s' (type=%s)", name, cmd_type)

                if cmd_type == "url":
                    return self._handle_url(name, entry.get("target", ""))

                elif cmd_type == "script":
                    return self._handle_script(name, entry.get("target", ""))

                elif cmd_type == "shell":
                    return self._handle_shell(name, entry.get("target", ""))

                elif cmd_type == "builtin":
                    return self._handle_builtin(name, entry.get("action", ""), normalised)

                else:
                    logger.warning("Unknown command type '%s' for '%s'", cmd_type, name)
                    return CommandResult(
                        matched=True,
                        command_name=name,
                        response_text=f"I found the command '{name}' but I don't know how to run it.",
                    )

        return CommandResult(matched=False)

    def _try_math(self, text: str) -> "CommandResult | None":
        """Detect and safely evaluate spoken math expressions.
        Converts word-forms ('times', 'plus', 'divided by', 'minus', 'to the power of')
        into operators, then evaluates the expression.
        Returns a CommandResult if math was detected, else None.
        """
        import ast, math as _math

        # Convert spoken math words to symbols
        expr = text
        # Multi-pass preamble stripping — strips question words one at a time until stable
        preamble = re.compile(
            r"^(what'?s?\s+|what\s+|is\s+|are\s+|the\s+|a\s+|an\s+|me\s+|"
            r"calculate\s+|compute\s+|solve\s+|equals?\s+|how\s+much\s+is\s+|"
            r"tell\s+me\s+|can\s+you\s+|please\s+|the\s+answer\s+to\s+|answer\s+)"
        )
        prev = None
        while prev != expr:
            prev = expr
            expr = preamble.sub("", expr).strip()
        expr = expr.rstrip("?. ")

        # Replace word operators (order matters — longer phrases first)
        expr = re.sub(r"\bsquare\s+root\s+of\b",      "sqrt", expr)
        expr = re.sub(r"\bto\s+the\s+power\s+of\b",   "**",   expr)
        expr = re.sub(r"\bmultiplied\s+by\b",          "*",    expr)
        expr = re.sub(r"\bdivided\s+by\b",             "/",    expr)
        expr = re.sub(r"\btimes\b",                    "*",    expr)
        expr = re.sub(r"\bover\b",                     "/",    expr)
        expr = re.sub(r"\bplus\b",                     "+",    expr)
        expr = re.sub(r"\bminus\b",                    "-",    expr)
        expr = re.sub(r"\bsquared\b",                  "**2",  expr)
        expr = re.sub(r"\bcubed\b",                    "**3",  expr)
        # Handle spoken 'x' as multiply (only when surrounded by digits/spaces)
        expr = re.sub(r"(?<=\d)\s*x\s*(?=\d)",        "*",    expr)
        expr = re.sub(r"\bx\b",                        "*",    expr)

        # Check if the expression has digits AND an operator
        if not re.search(r"\d", expr):
            return None
        if not re.search(r"[\+\-\*/\^]|sqrt|\*\*", expr):
            return None

        # Handle sqrt -> _math.sqrt(x)
        expr = re.sub(r"sqrt\s*(\d+\.?\d*)", r"_math.sqrt(\1)", expr)

        # Whitelist: only digits, operators, spaces, dots, parens, _math references
        if not re.match(r"^[\d\s\+\-\*\/\(\)\.\^_a-z]+$", expr):
            return None

        # Replace ^ with ** for exponentiation
        expr = expr.replace("^", "**")

        try:
            # Use ast to safely evaluate (no builtins, only literals & ops)
            tree = ast.parse(expr, mode="eval")

            # Whitelist: only safe AST node types
            allowed = (
                ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
                ast.FloorDiv, ast.USub, ast.UAdd,
                ast.Call, ast.Attribute, ast.Name,  # for _math.sqrt
            )
            for node in ast.walk(tree):
                if not isinstance(node, allowed):
                    return None
                # Only allow _math attribute access for safety
                if isinstance(node, ast.Name) and node.id not in ("_math",):
                    return None

            result = eval(compile(tree, "<string>", "eval"), {"__builtins__": {}, "_math": _math})

            # Format result nicely — remove trailing .0 for whole numbers
            if isinstance(result, float) and result.is_integer():
                result_str = str(int(result))
            else:
                result_str = f"{result:,.6g}"

            return CommandResult(
                matched=True,
                command_name="Math",
                response_text=f"The answer is {result_str}.",
            )
        except Exception:
            return None

    def _try_spotify(self, text: str) -> "CommandResult | None":
        """Detect and route Spotify control commands.
        Returns a CommandResult if matching Spotify commands were processed, else None.
        """
        if not self.spotify.is_configured():
            return None
        
        # Ensure Spotipy client is loaded
        if not self.spotify._sp:
            if not self.spotify.load():
                return None

        t = text.lower().strip()

        # Direct Mention of Spotify (guarantees routing to Spotify)
        if "spotify" in t:
            # Extract query by removing "on spotify", "with spotify", "spotify"
            query_clean = re.sub(r"\b(on|with|to)?\s*spotify\b", "", t).strip()
            # Clean off preambles and fillers
            query_clean = re.sub(r"^(uh|um|ok|okay|so|like|well)\b[\s,]*", "", query_clean).strip()
            query_clean = re.sub(r"^(hey\s+august|august|please|can\s+you|tell\s+me)\b[\s,]*", "", query_clean).strip()
            query_clean = re.sub(r"^(uh|um|ok|okay|so|like|well)\b[\s,]*", "", query_clean).strip()
            query_clean = re.sub(r"^(hey\s+august|august|please|can\s+you|tell\s+me)\b[\s,]*", "", query_clean).strip()
            # Clean off leading standard verbs
            query_clean = re.sub(r"^(play|start|listen\s+to)\b[\s,]*", "", query_clean).strip()
            
            if query_clean:
                msg = self.spotify.search_and_play(query_clean)
                return CommandResult(matched=True, command_name="Spotify Direct Mention", response_text=msg)

        # Clean off common filler words and wake word prefixes with optional commas/whitespace
        t = re.sub(r"^(uh|um|ok|okay|so|like|well)\b[\s,]*", "", t).strip()
        t = re.sub(r"^(hey\s+august|august|please|can\s+you|tell\s+me)\b[\s,]*", "", t).strip()
        t = re.sub(r"^(uh|um|ok|okay|so|like|well)\b[\s,]*", "", t).strip()
        t = re.sub(r"^(hey\s+august|august|please|can\s+you|tell\s+me)\b[\s,]*", "", t).strip()

        # Easter Egg check: If user asks for August's favorite/preferred song (and NOT a literal song name query)
        if any(kw in t for kw in ("your favorite song", "your preferred song", "favorite song of yours", "favorite song in your opinion")):
            msg = self.spotify.search_and_play("never gonna give you up rick astley")
            return CommandResult(
                matched=True,
                command_name="Spotify Favorite Song",
                response_text="My absolute favorite song is 'Never Gonna Give You Up' by Rick Astley. Let's play it!"
            )

        # Skip track / Next song
        if any(kw in t for kw in ("next song", "skip song", "skip track", "next track", "skip the song", "skip")):
            msg = self.spotify.next()
            return CommandResult(matched=True, command_name="Spotify Next", response_text=msg)

        # Previous track / Go back
        if any(kw in t for kw in ("previous song", "previous track", "play previous song", "play previous track", "go back")):
            msg = self.spotify.previous()
            return CommandResult(matched=True, command_name="Spotify Previous", response_text=msg)

        # Pause / Stop
        if any(kw in t for kw in ("pause music", "pause spotify", "stop music", "stop spotify", "pause")):
            msg = self.spotify.pause()
            return CommandResult(matched=True, command_name="Spotify Pause", response_text=msg)

        # Play / Resume (exact short triggers)
        if t in ("play", "resume", "play music", "resume music", "play spotify", "resume spotify", "resume"):
            msg = self.spotify.play()
            return CommandResult(matched=True, command_name="Spotify Play", response_text=msg)

        # Catch ANY sentence containing play, start, or listen to and treat the rest as a search query
        play_match = re.search(r"\b(play|start|listen\s+to)\s+(.+)$", t)
        if play_match:
            song_query = play_match.group(2).strip()
            # Remove helper question words and trailing question marks if present
            song_query = re.sub(r"\b(for me|please)\b", "", song_query).strip()
            song_query = song_query.rstrip("?").strip()
            
            if song_query in ("music", "spotify", "some music"):
                msg = self.spotify.play()
            else:
                msg = self.spotify.search_and_play(song_query)
            return CommandResult(matched=True, command_name="Spotify Play Query", response_text=msg)

        # Fallback: Catch any request mentioning 'song' or 'track' (handles cases where the verb is misheard as 'stand', 'put', etc.)
        song_match = re.search(r"\b(song|track)\s+(.+)$", t)
        if song_match:
            song_query = song_match.group(2).strip()
            # Remove helper question words and trailing question marks
            song_query = re.sub(r"\b(for me|please)\b", "", song_query).strip()
            song_query = song_query.rstrip("?").strip()
            msg = self.spotify.search_and_play(song_query)
            return CommandResult(matched=True, command_name="Spotify Fallback Song Query", response_text=msg)

        return None



    # ------------------------------------------------------------------
    # Private dispatch handlers
    # ------------------------------------------------------------------

    def _handle_url(self, name: str, url: str) -> CommandResult:
        """Open a URL, preferring Opera browser."""
        if not url:
            return CommandResult(
                matched=True,
                command_name=name,
                response_text="I know that command but it has no URL configured.",
            )

        opera_exe = Path(self.opera_path)
        opened_with = "your default browser"
        if opera_exe.exists():
            opened_with = "Opera"

        def run_action():
            self._print_action(f"Opening {name} in {opened_with}...")
            if opera_exe.exists():
                try:
                    subprocess.Popen([str(opera_exe), url])
                    logger.info("Opened %s in Opera: %s", name, url)
                except Exception as e:
                    logger.warning("Failed to open Opera (%s), falling back to default browser.", e)
                    webbrowser.open(url)
            else:
                logger.debug("Opera not found at '%s'. Using default browser.", self.opera_path)
                webbrowser.open(url)

        return CommandResult(
            matched=True,
            command_name=name,
            response_text=f"Opening {name} in {opened_with}.",
            action_callback=run_action,
        )

    def _handle_script(self, name: str, script_path: str) -> CommandResult:
        """Run a script file (.bat, .py, .ps1, etc.)."""
        if not script_path:
            return CommandResult(
                matched=True,
                command_name=name,
                response_text="That command has no script path configured.",
            )

        # Resolve relative to the project root
        resolved = self.project_root / script_path
        if not resolved.exists():
            logger.error("Script not found: %s", resolved)
            return CommandResult(
                matched=True,
                command_name=name,
                response_text=f"I tried to run {name} but the script wasn't found at {script_path}.",
            )

        def run_action():
            self._print_action(f"Executing: {name}")
            try:
                ext = resolved.suffix.lower()
                if ext == ".py":
                    subprocess.Popen(["python", str(resolved)], shell=False)
                elif ext == ".bat" or ext == ".cmd":
                    subprocess.Popen(["cmd.exe", "/c", str(resolved)], shell=False)
                elif ext == ".ps1":
                    subprocess.Popen(
                        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(resolved)],
                        shell=False,
                    )
                else:
                    subprocess.Popen([str(resolved)], shell=True)
                logger.info("Script launched: %s", resolved)
            except Exception as e:
                logger.error("Failed to run script '%s': %s", resolved, e)

        return CommandResult(
            matched=True,
            command_name=name,
            response_text=f"Activating {name}.",
            action_callback=run_action,
        )

    def _handle_shell(self, name: str, command: str) -> CommandResult:
        """Run a raw shell command string."""
        if not command:
            return CommandResult(
                matched=True,
                command_name=name,
                response_text="That command has no shell command configured.",
            )

        def run_action():
            self._print_action(f"Running: {command}")
            try:
                subprocess.Popen(command, shell=True)
            except Exception as e:
                logger.error("Shell command failed: %s | error: %s", command, e)

        # Better spoken responses
        spoken = f"Opening {name}." if "opera.exe" in command else f"Done, running {name}."
        return CommandResult(
            matched=True,
            command_name=name,
            response_text=spoken,
            action_callback=run_action,
        )

    def _handle_builtin(self, name: str, action: str, text: str) -> CommandResult:
        """Handle built-in assistant actions."""
        if action == "stop":
            self._print_action("Going to sleep. Goodbye!")
            return CommandResult(
                matched=True,
                command_name=name,
                response_text="Going to sleep. Say my name when you need me.",
                is_stop=True,
            )
        elif action == "time":
            # If the user is asking for time in another place, let LLM handle it
            if " in " in text or " at " in text or " for " in text:
                return CommandResult(matched=False)

            import datetime
            now = datetime.datetime.now()
            time_str = now.strftime("%I:%M %p").lstrip('0')
            return CommandResult(
                matched=True,
                command_name=name,
                response_text=f"The current time is {time_str}.",
            )
        elif action == "date":
            # If the user is asking for date in another place, let LLM handle it
            if " in " in text or " at " in text or " for " in text:
                return CommandResult(matched=False)

            import datetime
            now = datetime.datetime.now()
            date_str = now.strftime("%A, %B %d, %Y")
            return CommandResult(
                matched=True,
                command_name=name,
                response_text=f"Today is {date_str}.",
            )
        return CommandResult(
            matched=True,
            command_name=name,
            response_text=f"I don't know how to handle built-in action '{action}'.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _matches_any(self, text: str, keywords: list[str]) -> bool:
        """Return True if `text` contains any keyword as a whole-word match (case-insensitive)."""
        for kw in keywords:
            # Escape special regex chars in keyword, then match as whole word / phrase
            pattern = r"(?<![\w])" + re.escape(kw.lower()) + r"(?![\w])"
            if re.search(pattern, text):
                return True
        return False

    def _print_action(self, message: str) -> None:
        if self.use_color:
            print(f"  {_CYAN}⚡ {message}{_RESET}")
        else:
            print(f"  >> {message}")
