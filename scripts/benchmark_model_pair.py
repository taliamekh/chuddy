"""Opt-in full-GPU conversation/voice coexistence check; no playback or app writes.

Default is read-only preflight. --run is for a coordinator-approved idle window:
temporarily unload only the original primary Qwen runner, exercise the already
downloaded candidate, then restore the original resident model in finally.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chuddy.agent import DEFAULT_MODEL, local_endpoint, local_request
from chuddy.controller import Controller
from chuddy.store import Store
from chuddy.worker_process import terminate_worker
from tests.helpers import FakeAudio, FakeVoice, FakeSimulator, FakeChromeLauncher

CANDIDATE = 'llama3.1:8b-instruct-q4_K_M'
PROMPTS = [
    'Hey Chuddy. I spent the morning on the couch. How was your day?',
    'Actually I liked being lazy. I just want to complain about my noisy neighbour.',
    'They played the same loud song all morning. I do not need advice, just let me complain.',
    'Tell me a short joke about a cat.',
    'Can you spell report?',
    'What is 7 times 6?',
    'Please remember that my favourite snack is pretzels.',
    'What is my favourite snack?',
]


def call(endpoint, path, payload=None, timeout=30):
    with local_request(endpoint, path, payload, timeout) as response:
        return json.load(response)


def gpu():
    result = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free,utilization.gpu', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, timeout=5, creationflags=0x08000000 if sys.platform == 'win32' else 0)
    if result.returncode:
        return {'error': result.stderr[-200:]}
    used, free, utilization = [int(x.strip()) for x in result.stdout.splitlines()[0].split(',')]
    return {'used_mib': used, 'free_mib': free, 'utilization_percent': utilization}


def ps(endpoint):
    return call(endpoint, '/api/ps')['models']


def protected():
    config_path = ROOT / '.chuddy/voice.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    files = [config_path, Path(config['reference']), ROOT / '.chuddy/voice/source.webm', ROOT / 'CHUDDY_CONCEPT.md', ROOT / 'inputs/chuddy-reference.png']
    cache = Path(config.get('cache', ROOT / '.chuddy/voice-cache'))
    files += [p for p in cache.glob('*.wav') if p.name == 'filler.wav' or p.name.startswith('letter-')]
    files += list((ROOT / 'assets/audio').glob('fart-youtube-*.wav'))
    return {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def assert_full_gpu(endpoint, model):
    residents = ps(endpoint)
    if any(m['name'] != model for m in residents):
        raise RuntimeError('Benchmark window changed: another model became resident; stop the candidate check.')
    current = next((m for m in residents if m['name'] == model), None)
    if not current or current.get('size_vram', 0) < current.get('size', 1) * .98:
        raise RuntimeError('Candidate is not fully GPU-resident according to /api/ps: ' + json.dumps(current))
    return current


def direct_stream(endpoint, model, prompt):
    start, first, content, final = time.perf_counter(), None, '', {}
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'stream': True, 'think': False,
        'keep_alive': '30m', 'options': {'num_ctx': 4096, 'num_gpu': 999, 'num_predict': 60, 'temperature': .65}}
    with local_request(endpoint, '/api/chat', payload, 120) as response:
        for raw in response:
            item = json.loads(raw)
            if item.get('error'):
                raise RuntimeError(item['error'])
            token = item.get('message', {}).get('content', '')
            if token and first is None:
                first = time.perf_counter() - start
            content += token
            if item.get('done'):
                final = item
    return {'first_token_s': first, 'wall_total_s': time.perf_counter() - start, 'answer': content,
        'load_s': final.get('load_duration', 0) / 1e9,
        'generation_tokens': final.get('eval_count'), 'generation_s': final.get('eval_duration', 0) / 1e9,
        'tokens_per_second': final.get('eval_count', 0) / max(final.get('eval_duration', 0) / 1e9, .0001)}


class IsolatedVoice:
    def __init__(self, output):
        config = json.loads((ROOT / '.chuddy/voice.json').read_text(encoding='utf-8'))
        if config.get('engine') != 'turbo' or config.get('device') != 'cuda':
            raise ValueError('Current prepared voice is not the requested CUDA Turbo configuration.')
        output.mkdir(parents=True, exist_ok=True)
        self.log = (output / 'voice-worker.log').open('w', encoding='utf-8')
        command = [config['python'], '-u', str(ROOT / 'chuddy/voice_worker.py'), '--source', config['source'],
            '--models', config['models'], '--engine', 'turbo', '--device', 'cuda', '--reference', config['reference'], '--output', str(output)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', creationflags=0x08000000 if sys.platform == 'win32' else 0)
        self.responses = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        threading.Thread(target=read, daemon=True).start()

    def request(self, op, text=None):
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        request = {'id': request_id, 'op': op}
        if text is not None:
            request['text'] = text
        self.process.stdin.write(json.dumps(request) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                line = self.responses.get(timeout=.5)
            except queue.Empty:
                continue
            if line is None:
                break
            result = json.loads(line)
            if result.get('id') == request_id:
                if not result.get('ok'):
                    raise RuntimeError(result.get('error'))
                return {**result, 'wall_s': time.perf_counter() - started}
        raise TimeoutError('Isolated voice worker timed out or exited.')

    def close(self):
        terminate_worker(self.process)
        self.log.close()


def agent_fixture(folder, endpoint, model):
    stack = ExitStack()
    for target, replacement in [
        ('chuddy.voice.Voice', FakeVoice), ('chuddy.controller.AudioPipeline', FakeAudio),
        ('chuddy.hardware.Simulator', FakeSimulator), ('chuddy.pi_bridge.PiBridge', MagicMock()),
        ('chuddy.interception.PreservingWindows', MagicMock()), ('chuddy.interception.ChromeLauncher', FakeChromeLauncher),
    ]:
        stack.enter_context(patch(target, replacement))
    for target in ('chuddy.controller.windows.foreground', 'chuddy.controller.windows.minimize', 'chuddy.controller.windows.restore'):
        stack.enter_context(patch(target, side_effect=AssertionError('Real windows are forbidden in this check.')))
    store = Store(folder / 'state.sqlite3')
    store.update_settings({'ai_endpoint': endpoint, 'ai_model': model, 'voice': False})
    controller = Controller(store, folder)
    return stack, store, controller


def turn(controller, store, prompt):
    submitted = time.perf_counter()
    controller.agent.begin(prompt)
    controller.agent.worker.join(timeout=45)
    if controller.agent.worker.is_alive():
        controller.agent.cancel()
        raise TimeoutError('Actual agent did not finish within its budget.')
    state = controller.agent.info()
    result = {'prompt': prompt, 'answer': store.messages()[-1]['text'], 'source': state['source'],
        'metrics': state['metrics'], 'trace': state['trace'], 'wall_total_s': time.perf_counter() - submitted,
        'error': state.get('error')}
    metrics = result['metrics']
    result['final_generation_tokens_per_second'] = metrics.get('eval_count', 0) / max((metrics.get('eval_duration') or 0) / 1e9, .0001)
    if result['source'] != 'local model':
        raise RuntimeError('Actual agent fell back: ' + json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--model', default=CANDIDATE)
    parser.add_argument('--output', type=Path, default=ROOT / '.chuddy/benchmarks/model-pair')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--coordinator-window', help='Auditable name of the coordinator-approved idle window; required with --run.')
    args = parser.parse_args()
    endpoint = local_endpoint(args.endpoint)
    before = protected()
    initial = ps(endpoint)
    report = {'time': datetime.now(timezone.utc).isoformat(), 'model': args.model, 'endpoint': endpoint,
        'coordinator_window': args.coordinator_window, 'initial_ps': initial, 'initial_gpu': gpu(),
        'run_requested': args.run, 'effects': 'No GUI, real window adapters, microphone, playback or Pi outputs; temporary profile only.'}
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / 'results.json'
    def save():
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    save()
    print(json.dumps(report), flush=True)
    if not args.run:
        return 0
    if not args.coordinator_window:
        parser.error('--run requires an explicit coordinator-approved idle window.')
    if any(m['name'] != DEFAULT_MODEL for m in initial):
        raise RuntimeError('Unexpected resident model; refusing to alter shared model state.')
    available = {m['name'] for m in call(endpoint, '/api/tags')['models']}
    if args.model not in available:
        raise RuntimeError('Candidate is not already downloaded; this script never pulls.')
    voice = None
    changed = False
    samples, sampling_stop, sampling_stage = [], threading.Event(), {'name': 'preload'}
    def sample_gpu():
        while not sampling_stop.is_set():
            samples.append({'stage': sampling_stage['name'], 'time': time.monotonic(), **gpu()})
            sampling_stop.wait(.25)
    sampler = threading.Thread(target=sample_gpu, daemon=True)
    try:
        changed = True  # A timed-out unload still needs a restoration attempt.
        if initial:
            call(endpoint, '/api/generate', {'model': DEFAULT_MODEL, 'keep_alive': 0}, 30)
        sampler.start()
        report['after_original_unload'] = {'ps': ps(endpoint), 'gpu': gpu()}
        report['cold_direct'] = direct_stream(endpoint, args.model, 'Say hello in one short sentence.')
        report['candidate_full_gpu'] = assert_full_gpu(endpoint, args.model)
        report['warm_direct'] = direct_stream(endpoint, args.model, 'Say hello in one short sentence.')
        report['unpaired_agent_turns'] = []
        sampling_stage['name'] = 'unpaired'
        with tempfile.TemporaryDirectory(prefix='chuddy-model-pair-') as folder:
            stack, store, controller = agent_fixture(Path(folder), endpoint, args.model)
            try:
                for prompt in PROMPTS:
                    result = turn(controller, store, prompt)
                    result['ps'] = assert_full_gpu(endpoint, args.model)
                    report['unpaired_agent_turns'].append(result)
                    save()
                    print(json.dumps({'stage': 'unpaired', **result}), flush=True)
                report['saved_memory'] = store.memories()
                voice = IsolatedVoice(args.output / 'voice-temp')
                sampling_stage['name'] = 'voice-load'
                report['voice_warm'] = voice.request('warm')
                report['paired_residency'] = {'ps': assert_full_gpu(endpoint, args.model), 'gpu': gpu()}
                report['paired_agent_turns'] = []
                sampling_stage['name'] = 'paired'
                for prompt in ('Tell me a short joke about a cat.', 'Can you spell report?'):
                    voice_result, error = {}, []
                    def synthesize():
                        try:
                            voice_result.update(voice.request('synthesize', "Hey. I'm Chuddy. Your new supportive friend."))
                        except Exception as exc:
                            error.append(str(exc))
                    worker = threading.Thread(target=synthesize, daemon=True)
                    worker.start()
                    result = turn(controller, store, prompt)
                    worker.join(timeout=150)
                    if worker.is_alive() or error:
                        raise RuntimeError('Paired voice failed: ' + str(error))
                    result.update(voice=voice_result, ps=assert_full_gpu(endpoint, args.model), gpu=gpu())
                    report['paired_agent_turns'].append(result)
                    save()
                    print(json.dumps({'stage': 'paired', **result}), flush=True)
            finally:
                controller.close()
                store.close()
                stack.close()
        # The real observed-work script supplies strict fake effect adapters.
        observed = args.output / 'observed-agent.json'
        sampling_stage['name'] = 'observed-agent-with-voice-resident'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_observed_agent.py'), '--endpoint', endpoint,
            '--model', args.model, '--output', str(observed)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
        report['observed_agent'] = json.loads(observed.read_text()) if observed.is_file() else {'error': result.stderr[-600:]}
        report['observed_agent_exit'] = result.returncode
        turns = report['unpaired_agent_turns']
        report['summary'] = {'unpaired_first_token_median_ms': statistics.median(t['metrics']['first_token_ms'] for t in turns),
            'unpaired_total_median_ms': statistics.median(t['metrics']['total_ms'] for t in turns),
            'unpaired_total_max_ms': max(t['metrics']['total_ms'] for t in turns), 'small_sample': True}
        report['completed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if voice:
            voice.close()
        if changed:
            sampling_stage['name'] = 'restore'
            try:
                call(endpoint, '/api/generate', {'model': args.model, 'keep_alive': 0}, 30)
                if initial:
                    call(endpoint, '/api/generate', {'model': DEFAULT_MODEL, 'keep_alive': '30m', 'stream': False,
                        'options': {'num_ctx': 4096, 'num_gpu': 999}}, 120)
                report['restored_ps'] = ps(endpoint)
                report['restored_gpu'] = gpu()
            except Exception as error:
                report['restore_error'] = str(error)
        report['protected_hashes_unchanged'] = before == protected()
        sampling_stop.set()
        if sampler.is_alive():
            sampler.join(timeout=6)
        report['gpu_samples'] = samples
        for stage in ('unpaired', 'paired', 'voice-load'):
            selected = [s for s in samples if s['stage'] == stage and 'used_mib' in s]
            if selected:
                report[stage + '_gpu_observed'] = {'peak_used_mib': max(s['used_mib'] for s in selected),
                    'minimum_free_mib': min(s['free_mib'] for s in selected), 'samples': len(selected)}
        save()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
