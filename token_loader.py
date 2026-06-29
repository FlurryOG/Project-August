import os
from pathlib import Path

def load_tokens():
    """Load private tokens from Tokens.txt into environment variables."""
    tokens_path = Path("Tokens.txt")
    if not tokens_path.exists():
        # Try resolving relative to the file location
        tokens_path = Path(__file__).parent / "Tokens.txt"
        
    if tokens_path.exists():
        try:
            with open(tokens_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        os.environ[key] = val
        except Exception as e:
            print(f"Error loading Tokens.txt: {e}")
