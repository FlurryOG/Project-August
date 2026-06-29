# August — Local Offline Voice Assistant

A fully private, fully local voice assistant that runs on your own hardware.
No cloud, no subscriptions, no data leaving your machine.

| Component | Technology |
|---|---|
| Wake Word | openWakeWord (`hey_jarvis` → `hey_august` when trained) |
| Speech-to-Text | faster-whisper `base` model on CUDA (RTX 3060) |
| LLM Brain | Ollama running `llama3` (8B) locally |
| Audio | sounddevice (16kHz mono) |
| Commands | YAML-configured URL / script / shell dispatcher |

---

## Quick Start

### 1. Install Dependencies

```powershell
pip install -r requirements.txt
```

> **GPU (CUDA) note:** faster-whisper uses your RTX 3060 via CUDA.
> If you haven't already, install the [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads)
> (version 11.8+ recommended). faster-whisper handles cuDNN automatically.

### 2. Install & Start Ollama

1. Download Ollama: https://ollama.ai
2. Open Ollama (it runs in the system tray)
3. Pull the Llama 3 model:
   ```powershell
   ollama pull llama3
   ```

### 3. Run August

```powershell
python august.py
```

August will print a startup banner and begin listening.
**Say "Hey Jarvis"** (the placeholder wake word) to activate.

---

## Getting "Hey August" Working

By default, August listens for **"Hey Jarvis"** because that's a pre-trained
openWakeWord model. To use **"Hey August"**, you need to train a custom model.

See: [`train_wakeword/README.md`](train_wakeword/README.md)

It's a ~30 minute one-time process in Google Colab (free).
Once done, drop the `.onnx` file in `models/` and update `config.yaml`.

---

## Configuration

Everything is configured in [`config.yaml`](config.yaml):

| Setting | What it controls |
|---|---|
| `wake_word.model_path` | Wake word model (`hey_jarvis` or path to `.onnx`) |
| `wake_word.threshold` | Sensitivity (lower = easier to trigger) |
| `stt.model_size` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `stt.device` | `cuda` (GPU) or `cpu` |
| `llm.model` | Ollama model name (`llama3`, `llama3:8b`, etc.) |
| `llm.system_prompt` | August's personality |
| `commands.entries` | List of voice commands |

---

## Adding Commands

In `config.yaml`, add a new entry to `commands.entries`:

```yaml
- name: "Open Netflix"
  keywords: ["open netflix", "netflix"]
  type: url
  target: "https://www.netflix.com"
```

Or run a script:

```yaml
- name: "Start Work Mode"
  keywords: ["start work mode", "work mode", "focus mode"]
  type: script
  target: "scripts/work_mode.bat"
```

### Command Types
| Type | What it does |
|---|---|
| `url` | Opens a URL in Opera (or default browser) |
| `script` | Runs a `.bat`, `.py`, or `.ps1` file |
| `shell` | Runs a raw shell command string |
| `builtin` | Built-in actions (currently: `stop`) |

---

## Barn Door Protocol

The "Activate Barn Door Protocol" command runs:
`scripts/barn_door_protocol.bat`

Open that file and replace the placeholder with whatever you want it to do
(lock the PC, kill processes, start a VPN, etc.).

---

## CLI Flags

```
python august.py --help
python august.py --list-devices    # Show available microphones
python august.py --debug           # Verbose logging
python august.py --config my.yaml  # Use a different config file
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot connect to Ollama" | Open the Ollama app, or run `ollama serve` in a terminal |
| "Wake word model not found" | Check the path in `config.yaml` → `wake_word.model_path` |
| CUDA errors on Whisper | Change `stt.device` to `cpu` in `config.yaml` |
| Wrong microphone used | Run `python august.py --list-devices` and note the device index |
| August wakes too often | Increase `wake_word.threshold` (e.g., `0.6`) |
| August misses wake word | Decrease `wake_word.threshold` (e.g., `0.4`) |

---

## Project Structure

```
Project---AUGEST/
├── august.py              ← Main entry point
├── config.yaml            ← All settings (edit this)
├── requirements.txt       ← pip dependencies
├── audio_utils.py         ← Microphone capture
├── wake_word.py           ← openWakeWord wrapper
├── transcriber.py         ← faster-whisper STT
├── brain.py               ← Ollama LLM client
├── commands.py            ← Command dispatcher
├── models/                ← Drop your .onnx file here
├── scripts/
│   └── barn_door_protocol.bat
└── train_wakeword/
    └── README.md          ← How to train "Hey August"
```
