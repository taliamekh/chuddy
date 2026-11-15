"""External TensorFlow worker for a frozen desktop. No recording or playback."""
import argparse
import base64
import contextlib
import json
import sys
from pathlib import Path

sys.path = [p for p in sys.path if Path(p or '.').resolve() != Path(__file__).resolve().parent]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    protocol = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import tensorflow as tf
        tf.config.set_visible_devices([], 'GPU')
        model = tf.saved_model.load(args.model)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            pcm = base64.b64decode(request['float32'], validate=True)
            if not 15600 * 4 <= len(pcm) <= 16_000 * 15 * 4 or len(pcm) % 4:
                raise ValueError('Invalid bounded float32 audio.')
            samples = np.frombuffer(pcm, dtype='<f4')
            if not np.isfinite(samples).all():
                raise ValueError('Nonfinite audio.')
            with contextlib.redirect_stdout(sys.stderr):
                scores, _, _ = model(samples)
            result = {'id': request.get('id'), 'ok': True, 'scores': scores.numpy().tolist()}
        except Exception as error:
            result = {'id': request.get('id'), 'ok': False, 'error': str(error)[:300]}
        protocol.write(json.dumps(result) + '\n')
        protocol.flush()


if __name__ == '__main__':
    main()
