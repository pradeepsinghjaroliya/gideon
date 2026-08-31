Custom-trained wake word model files go here (e.g. `hey_gideon.onnx`).

Not checked in yet - train one with `../training/hey_gideon_training.ipynb`
(see `../training/README.md` for the full hand-off steps), then drop the
resulting `.onnx` file in this directory and point `config/config.yaml`'s
`wake_word.model` at it.
