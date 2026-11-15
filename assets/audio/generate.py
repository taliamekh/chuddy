"""Generate Chuddy's original procedural sound-test assets; no playback."""
import math
import random
import struct
import wave
from pathlib import Path


def generate(output=None):
    path = Path(output or Path(__file__).parent)
    path.mkdir(parents=True, exist_ok=True)
    rate, rng = 24000, random.Random(2400)
    for kind, duration in [('fart', 1.1), ('burp', .85)]:
        pcm = []
        filtered = 0.0
        for index in range(int(rate * duration)):
            t = index / rate
            envelope = min(1, t / .035) * max(0, 1 - t / duration) ** .7
            base = 70 - 35 * t / duration if kind == 'fart' else 125 - 70 * t / duration
            filtered = .84 * filtered + .16 * rng.uniform(-1, 1)
            voiced = math.sin(2 * math.pi * (base * t + 3 * math.sin(13 * t)))
            buzz = math.tanh(3 * voiced) if kind == 'fart' else voiced + .3 * math.sin(4 * math.pi * base * t)
            value = .58 * envelope * (.65 * buzz + .35 * filtered)
            pcm.append(struct.pack('<h', int(max(-1, min(1, value)) * 32767)))
        filename = 'fart-synthetic.wav' if kind == 'fart' else 'burp.wav'
        with wave.open(str(path / filename), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(b''.join(pcm))


if __name__ == '__main__':
    generate()
    print('Generated fart-synthetic.wav and burp.wav; nothing played.')
