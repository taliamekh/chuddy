"""Explicit, microphone-free model preparation and inference smoke test."""
import argparse
import json
import tarfile
import tempfile
import urllib.request
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('.chuddy/models/yamnet'))
    args = parser.parse_args()
    import tensorflow as tf
    import numpy as np
    tf.config.set_visible_devices([], 'GPU')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not (args.output / 'saved_model.pb').is_file():
        # Official TF Hub archive; extract only safe data members (Python 3.12+).
        with tempfile.TemporaryDirectory(prefix='chuddy-yamnet-') as temp:
            archive = Path(temp) / 'yamnet.tar.gz'
            urllib.request.urlretrieve('https://tfhub.dev/google/yamnet/1?tf-hub-format=compressed', archive)
            with tarfile.open(archive) as bundle:
                bundle.extractall(args.output, filter='data')
    restored = tf.saved_model.load(str(args.output))
    scores, embedding, spectrogram = restored(np.zeros(16000, dtype=np.float32))
    assert scores.shape[1] == 521
    config = args.output.parents[1] / 'audio.json'
    config.write_text(json.dumps({'python': sys.executable, 'model_path': str(args.output.resolve())}, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': str(args.output.resolve()), 'scores_shape': list(scores.shape),
                      'silence_burp': float(scores.numpy()[:, 53].mean()), 'silence_fart': float(scores.numpy()[:, 55].mean()),
                      'calibrated': False, 'microphone_used': False}))


if __name__ == '__main__':
    main()
