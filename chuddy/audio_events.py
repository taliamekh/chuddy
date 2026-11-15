"""Group overlapping detections and remove matches to known local effects."""
from functools import lru_cache
from pathlib import Path
import re
import wave

EFFECT_DIR = Path(__file__).resolve().parents[1] / 'assets' / 'audio'
ECHO_DELAY = .25  # seconds allowed for the output/input path and capture jitter


def effect_path(name):
    if isinstance(name, str) and re.fullmatch(r'(burp|fart|fart-youtube-(0[1-9]|1[0-3]))\.wav', name):
        return EFFECT_DIR / name
    return None


class SoundRuns:
    """One detection per continuous run of positive windows for each class."""
    def __init__(self):
        self.ends = {}

    def new_event(self, result, start, duration):
        if not result.get('detected'):
            return False
        kind = result['kind']
        previous = self.ends.get(kind, float('-inf'))
        self.ends[kind] = max(previous, start + duration)
        return start > previous + .02


@lru_cache(maxsize=16)
def _read_effect(path):
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getnframes() > 10 * wav.getframerate():
            raise ValueError('Effect reference must be a bounded PCM16 WAV.')
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        return data.reshape(-1, wav.getnchannels()).mean(axis=1) / 32768, wav.getframerate()


def remove_effect_echo(samples, rate, start, references):
    """Subtract an aligned copy only when waveform correlation supports it.

    References are (path, playback start, nominal end), in capture clock
    seconds. An unrelated sound during playback still reaches the classifier.
    """
    import numpy as np
    residual = np.asarray(samples, dtype=np.float32)
    end = start + len(residual) / rate
    radius = round(ECHO_DELAY * rate)
    for path, played_at, ends_at in references:
        if end < played_at - ECHO_DELAY or start > ends_at + ECHO_DELAY:
            continue
        try:
            reference, ref_rate = _read_effect(path)
        except (OSError, ValueError, wave.Error, EOFError):
            continue
        # Search short blocks so an independent sound in a different part of
        # the window does not overwhelm a match. Fit gain only where supported.
        padded_times = np.arange(-radius, len(residual) + radius) / rate
        candidates = np.interp((padded_times + start - played_at) * ref_rate,
                               np.arange(len(reference)), reference, left=0, right=0)
        energy = np.concatenate(([0.], np.cumsum(candidates ** 2)))
        best = (.72, 0, 0.)
        block = rate // 4
        for offset in range(0, len(residual) - rate // 8, block):
            segment = residual[offset:offset + block]
            crop = candidates[offset:offset + len(segment) + 2 * radius]
            ref_energy = energy[offset + len(segment):offset + len(crop) + 1] - energy[offset:offset + 2 * radius + 1]
            power = np.dot(segment, segment)
            if power < .0004:
                continue
            fft_size = 1 << (len(crop) - 1).bit_length()
            numerator = np.fft.irfft(np.fft.rfft(crop, fft_size) * np.fft.rfft(segment, fft_size).conj(),
                                     fft_size)[:len(ref_energy)]
            correlations = abs(numerator) / np.sqrt(np.maximum(ref_energy, .0004) * power)
            index = int(np.argmax(correlations))
            if correlations[index] > best[0] and ref_energy[index] > .0004:
                best = (correlations[index], index - radius, numerator[index] / ref_energy[index])
        _, lag, gain = best
        if gain:
            ref_samples = (np.arange(len(residual)) / rate + start - played_at + lag / rate) * ref_rate
            aligned = np.interp(ref_samples, np.arange(len(reference)), reference, left=0, right=0)
            residual = (residual - gain * aligned).astype(np.float32)
    return residual
