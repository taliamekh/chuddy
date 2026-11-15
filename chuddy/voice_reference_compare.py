"""Generate controlled local voice-reference comparisons, never play audio.

Run with .chuddy/voice-venv Python. This does not read/write active voice.json or
voice-cache, does not use the desktop's warm worker, and defaults to CPU.
"""
import argparse
import contextlib
import html
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path = [p for p in sys.path if Path(p or '.').resolve() != Path(__file__).resolve().parent]
ROOT = Path(__file__).resolve().parents[1]
PROMPTS = [
    ('greeting', "Hey. I'm Chuddy. Your new supportive friend."),
    ('spelling', 'Ar... Ee... um. Bee. You. Tee. Tee. Report. Nailed it.'),
    ('deadline', 'Your report? Easy. I finished it in my head. Hand in the idea of it.'),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_metrics(path):
    import numpy as np
    import soundfile as sf
    samples, rate = sf.read(path, dtype='float32')
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    f0, voiced, centroids = [], 0, []
    frame, hop = int(.04 * rate), int(.02 * rate)
    rms = []
    for start in range(0, len(samples) - frame, hop):
        x = samples[start:start+frame].astype(np.float64)
        power = float(np.sqrt(np.mean(x * x)))
        rms.append(power)
        if power < .012:
            continue
        x -= x.mean()
        spectrum = np.abs(np.fft.rfft(x * np.hanning(frame)))
        centroids.append(float(np.dot(np.fft.rfftfreq(frame, 1 / rate), spectrum) / max(spectrum.sum(), 1e-12)))
        corr = np.correlate(x, x, mode='full')[frame-1:]
        low, high = int(rate / 400), min(frame - 1, int(rate / 65))
        lag = low + int(np.argmax(corr[low:high+1]))
        strength = float(corr[lag] / max(corr[0], 1e-12))
        if strength >= .45:
            f0.append(rate / lag)
            voiced += 1
    return {'duration_s': len(samples) / rate, 'sample_rate': rate,
            'rms': float(np.sqrt(np.mean(samples * samples))),
            'pitch_median_hz': float(np.median(f0)) if f0 else None,
            'pitch_p10_hz': float(np.quantile(f0, .1)) if f0 else None,
            'pitch_p90_hz': float(np.quantile(f0, .9)) if f0 else None,
            'voiced_frame_fraction': voiced / max(1, len(rms)),
            'spectral_centroid_median_hz': float(np.median(centroids)) if centroids else None,
            'heuristic_only': True,
            'limitations': 'Pitch/centroid cannot identify the character or prove music/overlap absence.'}


def write_review_page(folder, manifest):
    cards = []
    for candidate in manifest['candidates']:
        name = candidate['id']
        players = ''.join('<label>' + html.escape(label.title()) + '<audio controls preload="none" src="' + html.escape(name + '-' + label + '.wav', quote=True) + '"></audio></label>' for label, _ in PROMPTS)
        cards.append('<section><h2>' + html.escape(name.replace('-', ' ').title()) + '</h2><label>Source reference<audio controls preload="none" src="' + html.escape(candidate['reference'], quote=True) + '"></audio></label><div class="samples">' + players + '</div></section>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chuddy voice auditions</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#fff5f6;color:#37252f;max-width:1100px;margin:35px auto;padding:0 22px}h1{font-size:36px}section{background:white;border:1px solid #e9cdd4;border-radius:20px;padding:20px;margin:22px 0}h2{margin-top:0}.samples{display:flex;flex-wrap:wrap;gap:18px;margin-top:14px}label{display:flex;flex-direction:column;gap:5px;font-weight:600}audio{width:250px}p{max-width:780px}.note{background:#e9f5de;border-radius:12px;padding:14px}</style>
<h1>Chuddy voice auditions</h1><p>The current voice has not matched what you want. These are alternatives for listening, with the same words in every comparison. Listen to the source reference and then the generated samples. You can choose one, ask for another direction, or reject them all.</p><p class="note">Nothing plays automatically. The live app and its selected voice are unchanged.</p>''' + ''.join(cards) + '<p>Source: <a href="https://www.youtube.com/watch?v=2vqW0q7yZj8">official Nickelodeon UK compilation</a>. Local private audition material; no reference audio was added to the distributed repository.</p></html>'
    (folder / 'listen.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--page-only', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    folder = args.manifest.parent
    if args.page_only:
        write_review_page(folder, manifest)
        return
    config = ROOT / '.chuddy/voice.json'
    config_before = digest(config) if config.is_file() else None
    cache = ROOT / '.chuddy/voice-cache'
    cache_before = {p.name: digest(p) for p in cache.glob('*.wav') if p.name == 'filler.wav' or p.name.startswith('letter-')}
    sys.path.insert(0, str(ROOT / '.chuddy/chatterbox-src/src'))
    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    torch.set_num_threads(max(1, min(8, args.threads)))
    started = time.perf_counter()
    model = ChatterboxTurboTTS.from_local(str(ROOT / '.chuddy/models/chatterbox-turbo'), args.device)
    report = {'model': 'Chatterbox Turbo', 'device': args.device, 'seed': 7, 'threads': args.threads,
              'loaded_seconds': time.perf_counter() - started, 'prompts': PROMPTS, 'candidates': [],
              'audio_played': False, 'microphone_used': False, 'quality_verdict': 'Awaiting user listening; existing voice rejected as inaccurate.'}
    for candidate in manifest['candidates']:
        reference = folder / candidate['reference']
        candidate_report = {**candidate, 'reference_sha256': digest(reference), 'source_metrics': source_metrics(reference), 'outputs': []}
        prepared = time.perf_counter()
        model.prepare_conditionals(str(reference))
        candidate_report['conditioning_seconds'] = time.perf_counter() - prepared
        for label, text in PROMPTS:
            torch.manual_seed(7)
            begin = time.perf_counter()
            with torch.inference_mode():
                audio = model.generate(text, temperature=.7, top_p=.9)
            samples = audio.detach().cpu().numpy().squeeze()
            path = folder / (candidate['id'] + '-' + label + '.wav')
            sf.write(path, samples, model.sr, subtype='PCM_16')
            result = {'prompt': label, 'path': str(path.resolve()), 'seconds': time.perf_counter() - begin,
                      'duration': len(samples) / model.sr, 'peak': float(np.max(np.abs(samples)))}
            candidate_report['outputs'].append(result)
            print(json.dumps({'candidate': candidate['id'], **result}), flush=True)
        report['candidates'].append(candidate_report)
        (folder / 'comparison-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    report['active_config_unchanged'] = config_before == (digest(config) if config.is_file() else None)
    report['active_cached_units_unchanged'] = cache_before == {p.name: digest(p) for p in cache.glob('*.wav') if p.name in cache_before}
    (folder / 'comparison-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    write_review_page(folder, manifest)
    print(json.dumps({'complete': True, 'active_config_unchanged': report['active_config_unchanged'], 'active_cached_units_unchanged': report['active_cached_units_unchanged']}), flush=True)


if __name__ == '__main__':
    main()
