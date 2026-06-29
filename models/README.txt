Place your custom .onnx wake word model files here.

To generate hey_august.onnx, see: ../train_wakeword/README.md

Once you have the file:
  1. Copy hey_august.onnx into this folder
  2. Open config.yaml
  3. Change wake_word.model_path to: "models/hey_august.onnx"
  4. Change wake_word.display_name to: "Hey August"
  5. Restart August
