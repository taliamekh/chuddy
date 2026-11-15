"""Generate local voice auditions and timing data. Never plays or records audio."""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import uuid
from .worker_process import terminate_worker

ROOT = Path(__file__).resolve().parents[1]
LINES = [
    ('filler', 'Umm. Lemme think.'),
    ('greeting', "Hey. I'm Chuddy. Your new supportive friend."),
    ('spelling', 'R... E... um. Bee. You. Tee. Tee. Report. Nailed it.'),
    ('arithmetic', 'Twenty two. I put them next to each other. Teamwork.'),
    ('deadline', "Your report? Easy. I finished it in my head. Hand in the idea of it."),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--engine', choices=['turbo', 'nano'], default='turbo')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--output', type=Path, default=ROOT / '.chuddy' / 'voice')
    parser.add_argument('--configure', action='store_true', help='Write desktop voice.json after a successful audition run.')
    parser.add_argument('--letters', action='store_true', help='Generate same-voice individual spelling-letter caches instead of general auditions.')
    args = parser.parse_args()
    python = ROOT / '.chuddy' / 'voice-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    model = ROOT / '.chuddy' / 'models' / ('chatterbox-' + args.engine)
    source = ROOT / '.chuddy' / 'chatterbox-src'
    args.output.mkdir(parents=True, exist_ok=True)
    command = [str(python), '-u', str(ROOT / 'chuddy' / 'voice_worker.py'), '--source', str(source), '--models', str(model),
               '--engine', args.engine, '--device', args.device, '--output', str(args.output / args.engine)]
    if args.reference:
        command += ['--reference', str(args.reference.resolve())]
    with (args.output / (args.engine + '-worker.log')).open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8')
        results = []
        try:
            lines = [(f'letter-{key}', value + '.') for key, value in [('A','Ay'),('B','Bee'),('E','Ee'),('F','Eff'),('O','Oh'),('P','Pee'),('R','Ar'),('T','Tee'),('U','You')]] if args.letters else LINES
            for label, text in lines:
                request_id = label + '-' + str(uuid.uuid4())
                submitted = time.perf_counter()
                process.stdin.write(json.dumps({'id': request_id, 'op': 'synthesize', 'text': text}) + '\n')
                process.stdin.flush()
                for line in process.stdout:
                    result = json.loads(line)
                    if result.get('id') == request_id:
                        break
                else:
                    raise RuntimeError('Voice worker exited. Read ' + str(args.output / (args.engine + '-worker.log')))
                result.update(label=label, text=text, wall_seconds=time.perf_counter() - submitted)
                results.append(result)
                print(json.dumps(result), flush=True)
                if not result.get('ok'):
                    raise RuntimeError(result.get('error', 'Synthesis failed.'))
            warm = [r['seconds'] for r in results[1:]]
            report = {'engine': args.engine, 'device': args.device, 'microphone_used': False, 'audio_played': False,
                      'similarity': 'unvalidated · user listening review required', 'results': results,
                      'warm_p50_seconds': statistics.median(warm),
                      'warm_p95_seconds': sorted(warm)[-1], 'p95_method': f'maximum of {len(warm)} warm audition samples'}
            (args.output / (args.engine + ('-letters' if args.letters else '') + '-benchmark.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
            if args.configure:
                cache = ROOT / '.chuddy' / 'voice-cache'
                cache.mkdir(exist_ok=True)
                if args.letters:
                    for result in results:
                        shutil.copyfile(result['path'], cache / (result['label'] + '.wav'))
                else:
                    shutil.copyfile(results[0]['path'], cache / 'filler.wav')
                config = {'python': str(python), 'source': str(source), 'models': str(model), 'engine': args.engine,
                          'device': args.device, 'reference': str(args.reference.resolve()) if args.reference else '', 'cache': str(cache)}
                (ROOT / '.chuddy' / 'voice.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
        finally:
            if process.poll() is None:
                terminate_worker(process)
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
