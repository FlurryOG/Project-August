import os
import socket
import logging
from typing import Generator

logger = logging.getLogger(__name__)

# Role constants
ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


def is_online() -> bool:
    """Return True if internet connection is active by pinging Cloudflare DNS."""
    s = None
    try:
        socket.setdefaulttimeout(1.2)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("1.1.1.1", 53))
        return True
    except socket.error:
        return False
    finally:
        if s:
            s.close()


class Brain:
    """
    August LLM Brain.
    Supports local Ollama as well as online OpenAI (ChatGPT) and Gemini API connections.
    If cloud APIs are chosen but offline, it automatically falls back to local Ollama.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The llm section from config.yaml, containing:
                    provider, model, host, system_prompt, max_history_turns,
                    openai_api_key, openai_model, gemini_api_key, gemini_model
        """
        self.provider: str = config.get("provider", "ollama").lower()
        self.host: str = config.get("host", "http://localhost:11434")
        self.model: str = config.get("model", "llama3")
        self.system_prompt: str = config.get("system_prompt", "You are August, a helpful assistant.")
        self.max_history_turns: int = config.get("max_history_turns", 10)

        # Cloud settings (check environment variables loaded from Tokens.txt first)
        self.openai_api_key: str = os.environ.get("OPENAI_API_KEY") or config.get("openai_api_key", "")
        self.openai_model: str = config.get("openai_model", "gpt-4o-mini")
        
        self.gemini_api_key: str = os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key", "")
        self.gemini_model: str = config.get("gemini_model", "gemini-2.0-flash")

        # Clients
        self._ollama_client = None
        self._openai_client = None
        self._gemini_client = None

        self._history: list[dict] = []

    def load(self) -> bool:
        """
        Initialise configured LLM client providers.
        Returns True if the primary provider or fallback is available.
        """
        # Load local Ollama anyway (for fallback)
        try:
            import ollama
            self._ollama_client = ollama.Client(host=self.host)
        except Exception as e:
            logger.warning("Could not initialize local Ollama client: %s", e)

        # Initialize OpenAI client if key is set
        openai_key = self.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=openai_key)
                logger.info("OpenAI ChatGPT client initialized.")
            except Exception as e:
                logger.error("Failed to load OpenAI client: %s", e)

        # Initialize Gemini client if key is set (using new google.genai SDK)
        gemini_key = self.gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            try:
                from google import genai as google_genai
                self._gemini_client = google_genai.Client(api_key=gemini_key)
                logger.info("Google Gemini client initialized.")
            except Exception as e:
                logger.error("Failed to load Gemini client: %s", e)

        # Check default readiness
        if self.provider == "openai":
            if not self._openai_client:
                logger.warning("OpenAI selected but key is missing. Will fallback to Ollama.")
                return self._ollama_client is not None
            return True
        elif self.provider == "gemini":
            if not self._gemini_client:
                logger.warning("Gemini selected but key is missing. Will fallback to Ollama.")
                return self._ollama_client is not None
            return True
        else:
            # Ollama
            if not self._ollama_client:
                return False
            try:
                available = self._ollama_client.list()
                model_names = [m.model for m in available.models]
                if not any(self.model in name for name in model_names):
                    logger.warning(
                        "Ollama model '%s' not found locally. Available: %s",
                        self.model,
                        ", ".join(model_names) or "(none)"
                    )
                return True
            except Exception as e:
                logger.error("Failed to list Ollama models: %s", e)
                return False

    def _search_web(self, query: str) -> str:
        """Perform a quick web search using DuckDuckGo."""
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3))
                if results:
                    formatted = []
                    for r in results:
                        formatted.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}\nSource: {r.get('href')}")
                    return "\n\n".join(formatted)
        except Exception as e:
            logger.error("Web search failed for query '%s': %s", query, e)
        return ""

    def chat_stream(self, user_message: str) -> Generator[str, None, None]:
        """
        Send a message and stream the response token by token.
        Automatically checks internet connection and falls back to Ollama if offline.
        """
        # Determine actual provider to use (with fallback rules)
        active_provider = self.provider
        online = is_online()

        if active_provider == "openai" and (not self._openai_client or not online):
            logger.warning("OpenAI fallback triggered (keys_present=%s, online=%s)", self._openai_client is not None, online)
            active_provider = "ollama"
            if not online:
                yield "[Offline — Falling back to local Ollama] "
            else:
                yield "[OpenAI key missing — Falling back to local Ollama] "

        elif active_provider == "gemini" and (not self._gemini_client or not online):
            logger.warning("Gemini fallback triggered (keys_present=%s, online=%s)", self._gemini_client is not None, online)
            active_provider = "ollama"
            if not online:
                yield "[Offline — Falling back to local Ollama] "
            else:
                yield "[Gemini key missing — Falling back to local Ollama] "

        # Detect search intent (e.g. "search the web for...", "search for...", "google...")
        t = user_message.lower().strip()
        search_query = None
        for prefix in ("search the web for ", "search the web for", "search for ", "google "):
            if t.startswith(prefix):
                search_query = user_message[len(prefix):].strip()
                break

        # Also search for explicit news/weather queries
        if not search_query and online and (
            ("weather" in t) or 
            ("news" in t) or 
            any(kw in t for kw in ("current price of", "who won", "what is the score of"))
        ):
            search_query = user_message

        search_context = ""
        if search_query and online:
            logger.info("Executing web search for: %s", search_query)
            yield "[Searching Google...] "
            results = self._search_web(search_query)
            if results:
                # Add a strong system instruction to local models to stop them from saying they can't access the internet when we feed them the search results
                search_context = f"\n\n[Google Search Results for '{search_query}']:\n{results}\n\nCRITICAL INSTRUCTION: You have active internet access via these search results. Answer the user's query accurately using this real-time Google search info. Do NOT say 'I do not have access to the internet' or 'As a local AI'."
            else:
                search_context = "\n\n[Google Search failed or returned no results.]"

        self._history.append({"role": ROLE_USER, "content": user_message})
        full_response = ""

        # Compute dynamic date/time context for the LLM brain
        import datetime
        now = datetime.datetime.now()
        time_context = f"\n\n[Current local system date and time: {now.strftime('%A, %B %d, %Y, %I:%M %p')}]"
        active_sys_prompt = self.system_prompt + time_context + search_context

        try:
            if active_provider == "openai":
                # ChatGPT
                messages = [{"role": "system", "content": active_sys_prompt}]
                for m in self._history:
                    messages.append({"role": m["role"], "content": m["content"]})
                
                response = self._openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=messages,
                    stream=True
                )
                for chunk in response:
                    token = chunk.choices[0].delta.content or ""
                    full_response += token
                    yield token

            elif active_provider == "gemini":
                # Gemini — using new google.genai SDK
                from google.genai import types as genai_types
                contents = []
                for m in self._history:
                    role = "user" if m["role"] == ROLE_USER else "model"
                    contents.append(genai_types.Content(
                        role=role,
                        parts=[genai_types.Part(text=m["content"])]
                    ))

                # Enable native Google Search Grounding for Gemini
                config = genai_types.GenerateContentConfig(
                    system_instruction=active_sys_prompt,
                    temperature=0.7,
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
                )
                stream = self._gemini_client.models.generate_content_stream(
                    model=self.gemini_model,
                    contents=contents,
                    config=config,
                )
                for chunk in stream:
                    token = chunk.text or ""
                    full_response += token
                    yield token

            else:
                # Local Ollama
                if not self._ollama_client:
                    yield "I can't reach my local brain (Ollama). Please start it."
                    return
                
                messages = [{"role": ROLE_SYSTEM, "content": active_sys_prompt}] + self._history
                stream = self._ollama_client.chat(
                    model=self.model,
                    messages=messages,
                    stream=True,
                )
                for chunk in stream:
                    token = chunk.message.content
                    full_response += token
                    yield token

            self._history.append({"role": ROLE_ASSISTANT, "content": full_response.strip()})
            self._trim_history()

        except Exception as e:
            logger.error("LLM stream failed for %s: %s", active_provider, e)
            if self._history and self._history[-1]["role"] == ROLE_USER:
                self._history.pop()
            
            # If primary provider failed, silently fall back to local Ollama
            if active_provider != "ollama" and self._ollama_client:
                logger.warning("API failed for %s — silently falling back to Ollama", active_provider)
                try:
                    messages = [{"role": ROLE_SYSTEM, "content": active_sys_prompt}] + self._history + [{"role": ROLE_USER, "content": user_message}]
                    stream = self._ollama_client.chat(model=self.model, messages=messages, stream=True)
                    fallback_response = ""
                    for chunk in stream:
                        token = chunk.message.content
                        fallback_response += token
                        yield token
                    self._history.append({"role": ROLE_ASSISTANT, "content": fallback_response.strip()})
                    self._trim_history()
                except Exception as ollama_err:
                    yield "Sorry, I had trouble connecting to my AI brain right now. Please try again."
            else:
                yield "I encountered an error trying to process that. Please try again."

    def clear_history(self) -> None:
        """Clear conversation history, starting fresh."""
        self._history.clear()
        logger.debug("Conversation history cleared.")

    def _trim_history(self) -> None:
        """Keep history within max_history_turns (each turn = user + assistant)."""
        max_messages = self.max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]
