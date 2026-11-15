"""CPU-only transcription of a local WAV."""
import argparse
import json
import time
import sys
from pathlib import Path

sys.path = [p for p in sys.path if Path(p or '.').resolve() != Path(__file__).resolve().parent]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--timestamps', action='store_true')
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    started = time.perf_counter()
    model = WhisperModel(args.model, device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, _ = model.transcribe(args.input, beam_size=1, language='en', vad_filter=True)
    speech = [{'start_s': round(s.start, 3), 'end_s': round(s.end, 3), 'text': s.text.strip()} for s in segments]
    result = {'text': ' '.join(s['text'] for s in speech).strip(),
              'source': 'local faster-whisper tiny.en · CPU', 'seconds': time.perf_counter() - started}
    if args.timestamps:
        result['speech_segments'] = speech
    print(json.dumps(result))


if __name__ == '__main__':
    main()
