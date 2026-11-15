"""Extract CC0 YouTube-origin sound-effect variants from a verified source WAV.

Fetch source from the URLs/hash in SOURCES.md, convert to mono PCM24k first.
This command neither downloads arbitrary media nor plays/records sound.
"""
import argparse
import json
from pathlib import Path
import shutil
import wave
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    with wave.open(str(args.source), 'rb') as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError('Use mono signed16-bit PCM WAV.')
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
    hop = int(.05 * rate)
    energy = [np.sqrt(np.mean(samples[i:i+hop] ** 2)) for i in range(0, len(samples), hop)]
    active = [i for i, value in enumerate(energy) if value > .03]
    groups = []
    for index in active:
        if not groups or index - groups[-1][-1] > 4:
            groups.append([index])
        else:
            groups[-1].append(index)
    output, manifest = Path(__file__).parent, []
    for index, group in enumerate(groups, 1):
        start = max(0, group[0] * hop - int(.08 * rate))
        end = min(len(samples), (group[-1] + 1) * hop + int(.12 * rate))
        data = samples[start:end].copy()
        if len(data) < rate * .15:
            continue
        data *= .75 / max(float(np.max(np.abs(data))), .01)
        fade = min(int(.008 * rate), len(data) // 2)
        data[:fade] *= np.linspace(0, 1, fade)
        data[-fade:] *= np.linspace(1, 0, fade)
        name = f'fart-youtube-{index:02}.wav'
        with wave.open(str(output / name), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes((data * 32767).astype('<i2').tobytes())
        manifest.append({'file': name, 'source_start_s': round(start / rate, 3), 'duration_s': round(len(data) / rate, 3), 'license': 'CC0-1.0'})
    (output / 'youtube-variants.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    if not manifest:
        raise ValueError('No sound regions found.')
    if (output / 'fart.wav').exists() and not (output / 'fart-synthetic.wav').exists():
        shutil.copyfile(output / 'fart.wav', output / 'fart-synthetic.wav')
    shutil.copyfile(output / manifest[0]['file'], output / 'fart.wav')
    print(json.dumps({'variants': len(manifest), 'default': manifest[0], 'audio_played': False}))


if __name__ == '__main__':
    main()
