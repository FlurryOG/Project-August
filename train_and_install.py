import os
import shutil
import subprocess
import sys
import yaml
from pathlib import Path

# Force UTF-8 encoding for stdout/stderr
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

print("[TRAIN & INSTALL] Starting training pipeline for 'Hey August'...")

# Step 1: Run the training pipeline
env = os.environ.copy()
env["PYTHONUTF8"] = "1"

# Add local portable espeak-ng to PATH
project_root = Path(__file__).parent.resolve()
local_espeak = project_root / "train_wakeword" / "espeak-ng" / "eSpeak NG"
existing_path = env.get("PATH", "")
if local_espeak.exists():
    env["PATH"] = str(local_espeak) + ";" + existing_path
    print(f"[TRAIN & INSTALL] Added local espeak-ng to PATH: {local_espeak}")
else:
    print(f"[TRAIN & INSTALL] Warning: Local espeak-ng not found at {local_espeak}!", file=sys.stderr)

cmd = ["livekit-wakeword", "run", "train_wakeword/hey_august.yaml"]
print(f"[TRAIN & INSTALL] Running command: {' '.join(cmd)}")

result = subprocess.run(cmd, env=env, capture_output=False)

if result.returncode != 0:
    print("[TRAIN & INSTALL] Error: Training pipeline failed!", file=sys.stderr)
    sys.exit(1)

# Step 2: Copy the generated model to models/
src_model = Path("train_wakeword/output/hey_august/hey_august.onnx")
dest_dir = Path("models")
dest_model = dest_dir / "hey_august.onnx"

if not src_model.exists():
    print(f"[TRAIN & INSTALL] Error: Trained model not found at {src_model}!", file=sys.stderr)
    sys.exit(1)

dest_dir.mkdir(exist_ok=True)
shutil.copy2(src_model, dest_model)
print(f"[TRAIN & INSTALL] Model successfully copied to {dest_model}")

# Step 3: Update config.yaml to point to the new model
config_path = Path("config.yaml")
if config_path.exists():
    print("[TRAIN & INSTALL] Updating config.yaml to use the new model...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Find the "Hey August" model entry and update its path
    models = config.get("wake_word", {}).get("models", [])
    updated = False
    for model in models:
        if model.get("phrase") == "hey august":
            model["path"] = "models/hey_august.onnx"
            updated = True
            break

    if updated:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
        print("[TRAIN & INSTALL] config.yaml successfully updated!")
    else:
        print("[TRAIN & INSTALL] Warning: Could not find 'hey august' phrase in config.yaml models list.", file=sys.stderr)
else:
    print("[TRAIN & INSTALL] Error: config.yaml not found!", file=sys.stderr)

print("[TRAIN & INSTALL] Done! August is now trained and configured to wake up on 'Hey August'.")
