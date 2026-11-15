"""Draft editable local WAV annotations or compare them against YAMNet."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chuddy.audio import AudioPipeline
from chuddy.audio_events import SoundRuns


def read_wav(path):
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        rate, frames, channels = wav.getframerate(), wav.getnframes(), wav.getnchannels()
        if wav.getsampwidth() != 2 or channels not in (1, 2) or not 8000 <= rate <= 96000 or not .2 * rate <= frames <= 120 * rate:
            raise ValueError('Use a 0.2–120 second PCM16 WAV, mono/stereo at 8–96 kHz.')
        pcm = np.frombuffer(wav.readframes(frames), dtype='<i2')
        if len(pcm) != frames * channels:
            raise ValueError('WAV data is shorter than its declared duration.')
    return pcm.reshape(-1, channels).mean(axis=1).astype(np.float32) / 32768, rate


def draft(args, duration):
    document = {'clip': args.wav.name, 'duration_s': duration, 'speech_draft': None, 'annotations': []}
    if args.transcribe:
        audio = AudioPipeline(None, args.data_dir / 'models' / 'yamnet')
        audio.require_transcriber()
        python, model = audio._asr_paths()
        result = subprocess.run(
            [str(python), str(ROOT / 'chuddy' / 'asr_worker.py'), '--model', str(model),
             '--input', str(args.wav.resolve()), '--timestamps'],
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=180,
        )
        speech = json.loads(result.stdout)
        segments = [segment for segment in speech['speech_segments'] if segment['start_s'] < duration]
        for segment in segments:
            segment['end_s'] = min(segment['end_s'], duration)
        document['speech_draft'] = {'source': speech['source'], 'segments': segments}
    return document


def compare(document, duration, predictions):
    """One prediction per label. Report extra matches as duplicates."""
    if not isinstance(document.get('annotations'), list) or not document['annotations']:
        raise ValueError('Add manually reviewed annotations before comparing detections.')
    if document.get('duration_s') != duration:
        raise ValueError('Annotation duration does not match the WAV.')
    labels = []
    for label in document['annotations']:
        start, end, kind = label['start_s'], label['end_s'], label['kind']
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= duration or kind not in ('fart', 'burp', 'other')):
            raise ValueError('Each annotation needs start_s, end_s and kind (fart, burp or other).')
        if kind == 'fart':
            labels.append(label)
    matched = set()
    false_positives, duplicates = [], []
    for detection in predictions:
        overlaps = [(min(label['end_s'], detection['end_s']) - max(label['start_s'], detection['start_s']), i)
                    for i, label in enumerate(labels) if label['kind'] == detection['kind']]
        candidates = [(overlap, i) for overlap, i in overlaps if overlap > 0]
        available = [(overlap, i) for overlap, i in candidates if i not in matched]
        if available:
            matched.add(max(available)[1])
        elif candidates:
            duplicates.append(detection)
        else:
            false_positives.append(detection)
    return {'expected': len(labels), 'matched': len(matched), 'duplicates': duplicates,
            'missed': [label for i, label in enumerate(labels) if i not in matched],
            'false_positives': false_positives, 'detections': predictions}


def evaluate(args, samples, rate, duration):
    document = json.loads(args.labels.read_text(encoding='utf-8-sig'))
    if document.get('clip') != args.wav.name:
        raise ValueError('Annotation clip name does not match the WAV.')
    if not .01 <= args.threshold <= 1:
        raise ValueError('Classifier threshold must be between 0.01 and 1.')

    class Settings:
        def settings(self):
            return {'audio_threshold': args.threshold}

    audio = AudioPipeline(Settings(), args.data_dir / 'models' / 'yamnet')
    runs, predictions = SoundRuns(), []
    for frame in range(0, len(samples), rate // 2):
        window = samples[frame:frame + rate]
        if len(window) < .2 * rate:
            break
        start, length = frame / rate, len(window) / rate
        result = audio.detect_window(window, rate, start)
        if not result['available']:
            raise ValueError(result['error'] + '. ' + audio.error)
        if runs.new_event(result, start, length):
            predictions.append({'start_s': start, 'end_s': start + length, 'kind': result['kind'],
                                'confidence': result['confidence']})
    return compare(document, duration, predictions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    for name in ('draft', 'check'):
        command = subcommands.add_parser(name)
        command.add_argument('wav', type=Path)
        command.add_argument('--data-dir', type=Path, default=ROOT / '.chuddy',
                             help='Local Chuddy data directory containing prepared models')
    subcommands.choices['draft'].add_argument('--transcribe', action='store_true')
    subcommands.choices['draft'].add_argument('--output', type=Path, required=True)
    subcommands.choices['check'].add_argument('labels', type=Path)
    subcommands.choices['check'].add_argument('--threshold', type=float, default=.35)
    args = parser.parse_args()
    try:
        samples, rate = read_wav(args.wav)
        duration = len(samples) / rate
        if args.command == 'draft':
            document = draft(args, duration)
            with args.output.open('x', encoding='utf-8') as file:
                json.dump(document, file, indent=2)
                file.write('\n')
        else:
            print(json.dumps(evaluate(args, samples, rate, duration), indent=2))
    except (OSError, EOFError, ValueError, KeyError, TypeError, wave.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
