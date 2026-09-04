Custom-trained wake word model files go here.

`hey_gideon.onnx` is the model currently in use - trained with
`../training/train_wake_word.sh` and committed in `c8790d4`. It is what
`config/config.yaml`'s `wake_word.model` points at, and the `.deb` ships it
to `/opt/gideon/models/wake_word/hey_gideon.onnx`.

To train a different wake phrase, see `../training/README.md` for the full
hand-off steps, then drop the resulting `.onnx` file in this directory and
update `wake_word.model` (and `packaging/config.yaml` if you are rebuilding
the package).
