"""Isolated local Chatterbox JSON-lines worker. This module never plays audio.

Run with the dedicated voice environment, not the desktop environment.
Model/source directories are explicit, with no runtime downloads in normal use.
"""
import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

# Standalone scripts must not shadow stdlib secrets with chuddy/secrets.py.
sys.path = [p for p in sys.path if Path(p or '.').resolve() != Path(__file__).resolve().parent]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--models', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--engine', choices=['turbo', 'nano'], default='turbo')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source) / 'src'))
    protocol = sys.stdout
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    model = None

    def emit(value):
        protocol.write(json.dumps(value) + '\n')
        protocol.flush()

    # Third-party logs must not corrupt the line protocol.
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import soundfile as sf
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS
    emit({'event': 'worker_started', 'torch': torch.__version__, 'cuda': torch.cuda.is_available()})
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            request_id = request.get('id')
            op = request.get('op')
            start = time.perf_counter()
            if op == 'shutdown':
                break
            if model is None:
                with contextlib.redirect_stdout(sys.stderr):
                    torch.set_num_threads(min(8, os.cpu_count() or 4))
                    model = ChatterboxTurboTTS.from_local(args.models, args.device, nano=args.engine == 'nano')
                    if args.reference:
                        model.prepare_conditionals(args.reference)
                load_seconds = time.perf_counter() - start
                if model.conds is None:
                    raise ValueError('A reference WAV longer than five seconds is required.')
            else:
                load_seconds = 0
            if op == 'warm':
                emit({'id': request_id, 'ok': True, 'load_seconds': load_seconds, 'device': args.device, 'engine': args.engine})
                continue
            if op != 'synthesize':
                raise ValueError('Unknown voice operation.')
            text = request.get('text')
            if not isinstance(text, str) or not 0 < len(text) <= 1400:
                raise ValueError('Invalid speech text.')
            torch.manual_seed(int(request.get('seed', 7)))
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                wave = model.generate(text, temperature=0.7, top_p=0.9)
            if args.device == 'cuda':
                torch.cuda.synchronize()
            samples = wave.detach().cpu().numpy().squeeze()
            filename = str(request_id)
            if not filename.replace('-', '').replace('_', '').isalnum() or len(filename) > 100:
                raise ValueError('Invalid request identifier.')
            path = output / (filename + '.wav')
            sf.write(path, samples, model.sr, subtype='PCM_16')
            generated = [p for p in output.glob('*.wav') if p.name != 'filler.wav' and not p.name.startswith('letter-')]
            for old in sorted(generated, key=lambda p: p.stat().st_mtime, reverse=True)[50:]:
                old.unlink(missing_ok=True)
            duration = len(samples) / model.sr
            elapsed = time.perf_counter() - start
            emit({'id': request_id, 'ok': True, 'path': str(path.resolve()), 'seconds': elapsed,
                  'duration': duration, 'rtf': elapsed / max(duration, 0.001), 'load_seconds': load_seconds,
                  'peak_vram_mib': torch.cuda.max_memory_allocated() / 1048576 if args.device == 'cuda' else 0,
                  'peak': float(np.max(np.abs(samples)))})
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            emit({'id': request.get('id') if isinstance(request, dict) else None, 'ok': False,
                  'error': type(error).__name__ + ': ' + str(error)[:500]})


if __name__ == '__main__':
    main()
