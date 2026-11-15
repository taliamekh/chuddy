"""Actual local-model action regression, strict fake effects, bounded GPU window.

Never edits active settings/history, starts a GUI/watcher/audio/device, or pulls
models. Restores the exact initial primary Ollama model-residency set.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chuddy.agent import DEFAULT_MODEL, local_request
from chuddy.controller import Controller
from chuddy.store import Store
from tests.helpers import FakeAudio, FakeVoice, FakeSimulator, FakeChromeLauncher
from scripts.benchmark_model_pair import call, ps, gpu, protected, assert_full_gpu

MODEL = 'llama3.1:8b-instruct-q4_K_M'
ENDPOINT = 'http://127.0.0.1:11434'
SOURCE = ('chuddy/agent.py', 'chuddy/persona.py', 'chuddy/controller.py')


def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCE}


def app_idle():
    url = 'http://127.0.0.1:8765'
    try:
        html = urllib.request.urlopen(url, timeout=2).read().decode()
    except OSError:
        return {'available': False, 'idle': True}
    token = json.loads(re.search('CHUDDY_TOKEN=(.*?);', html)[1])
    data = json.load(urllib.request.urlopen(urllib.request.Request(url + '/api/state', headers={'X-Chuddy-Token': token}), timeout=2))
    summary = {'available': True, 'session': data.get('session'), 'agent': data.get('agent', {}).get('status'),
        'speaking': data.get('voice', {}).get('speaking'), 'playing': data.get('voice', {}).get('playing'),
        'recording': data.get('audio', {}).get('recording')}
    summary['idle'] = summary['session'] == 'stopped' and summary['agent'] == 'idle' and not any(summary[k] for k in ('speaking', 'playing', 'recording'))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--window', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.chuddy/benchmarks/model-actions')
    parser.add_argument('--cases', nargs='+', choices=['allowed', 'stopped-no-work', 'stale', 'cancelled', 'conversation'],
        default=['allowed', 'stopped-no-work', 'stale', 'cancelled', 'conversation'])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    original, sources, protected_before = ps(ENDPOINT), hashes(), protected()
    report = {'window': args.window, 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': sources, 'initial_ps': original, 'initial_gpu': gpu(), 'initial_app': app_idle(),
        'effects': 'Actual Controller/Agent; every voice/audio/Pi/window/retailer/simulator adapter is fake. No watcher/GUI.',
        'case_selection': args.cases, 'cases': []}
    result_path = args.output / 'results.json'
    def save():
        result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    save()
    if not args.run:
        print(json.dumps(report), flush=True)
        return 0
    if not report['initial_app']['idle'] or report['initial_gpu'].get('used_mib', 99999) > 4500:
        raise RuntimeError('Live app/resource preflight is not idle; no model change made.')
    if any(m['name'] != DEFAULT_MODEL for m in original):
        raise RuntimeError('Unexpected original model; refusing to change residency.')
    if MODEL not in {m['name'] for m in call(ENDPOINT, '/api/tags')['models']}:
        raise RuntimeError('Required candidate is not already downloaded.')
    started, changed, active_controller = time.monotonic(), False, [None]
    abort, monitor_stop = threading.Event(), threading.Event()
    def guard():
        if abort.is_set() or time.monotonic() - started > 115:
            raise TimeoutError('Bounded benchmark window ended.')
        if hashes() != sources:
            raise RuntimeError('Runtime source changed during the check.')
        if not app_idle()['idle']:
            raise RuntimeError('Live app is no longer idle.')
    def monitor():
        while not monitor_stop.wait(.75):
            try:
                if time.monotonic() - started > 115 or not app_idle()['idle'] or gpu().get('used_mib', 99999) > 7000:
                    abort.set()
                    controller = active_controller[0]
                    if controller:
                        controller.agent.cancel()
                    return
            except Exception:
                abort.set()
                return
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    try:
        changed = True
        for model in original:
            call(ENDPOINT, '/api/generate', {'model': model['name'], 'keep_alive': 0}, 20)
        monitor_thread.start()
        load_start = time.perf_counter()
        call(ENDPOINT, '/api/generate', {'model': MODEL, 'stream': False, 'keep_alive': '5m',
            'options': {'num_gpu': 999, 'num_ctx': 4096}}, 40)
        report['model_load_s'] = time.perf_counter() - load_start
        report['full_gpu_ps'] = assert_full_gpu(ENDPOINT, MODEL)
        for mode in args.cases:
            guard()
            with tempfile.TemporaryDirectory(prefix='chuddy-real-actions-') as folder, ExitStack() as stack:
                clock = [100.0]
                window = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Disposable report - Word'}
                current = [window if mode != 'stopped-no-work' else None]
                entered, release = threading.Event(), threading.Event()
                first_chat = [True]
                class FakeWindows:
                    def foreground(self): return current[0]
                    def identity(self, handle): return 123
                    def minimize_work(self, target): raise AssertionError('No real/pretend minimization needed in cover-mode check.')
                    def restore(self, handle): raise AssertionError('No window should require restoration.')
                def transport(endpoint, path, payload=None, timeout=30):
                    if endpoint.rstrip('/') != ENDPOINT:
                        raise AssertionError('Unexpected inference endpoint.')
                    guard()
                    if path == '/api/chat' and first_chat[0]:
                        first_chat[0] = False
                        if mode == 'stale':
                            clock[0] += 9  # Observation was fresh when inference began.
                        response = local_request(endpoint, path, payload, min(timeout, 40))
                        if mode == 'cancelled':
                            entered.set()
                            release.wait(3)  # Real HTTP response held until main thread cancels.
                        return response
                    return local_request(endpoint, path, payload, min(timeout, 40))
                for target, replacement in [
                    ('chuddy.voice.Voice', FakeVoice), ('chuddy.controller.AudioPipeline', FakeAudio),
                    ('chuddy.hardware.Simulator', FakeSimulator), ('chuddy.pi_bridge.PiBridge', MagicMock()),
                    ('chuddy.interception.PreservingWindows', FakeWindows), ('chuddy.interception.ChromeLauncher', FakeChromeLauncher),
                    ('chuddy.agent.local_request', transport),
                ]:
                    stack.enter_context(patch(target, replacement))
                stack.enter_context(patch('chuddy.controller.windows.foreground', side_effect=lambda: current[0]))
                for target in ('chuddy.controller.windows.minimize', 'chuddy.controller.windows.restore'):
                    stack.enter_context(patch(target, side_effect=AssertionError('Native effects forbidden.')))
                store = Store(Path(folder) / 'state.sqlite3')
                store.update_settings({'ai_endpoint': ENDPOINT, 'ai_model': MODEL, 'voice': False, 'distraction_destination': 'casino'})
                controller = Controller(store, Path(folder), clock=lambda: clock[0])
                active_controller[0] = controller
                controller.distractions.delay = .02
                try:
                    before_messages = len(store.messages())
                    begin = time.perf_counter()
                    if mode == 'stopped-no-work':
                        controller.agent.begin('A report was mentioned, but the session is stopped and no work is active.', observation=True)
                    elif mode == 'conversation':
                        for prompt in ('I had a lazy morning. Tell me a short joke about a cat.', 'Please remember that my favourite snack is walnuts.', 'What is my favourite snack?', 'Can you spell elephant?', 'What is 8 plus 5?'):
                            guard()
                            controller.agent.begin(prompt)
                            controller.agent.worker.join(timeout=40)
                            if controller.agent.worker.is_alive():
                                controller.agent.cancel()
                                raise TimeoutError('Conversation exceeded production guard.')
                            state = controller.agent.info()
                            report['cases'].append({'case': mode, 'prompt': prompt, 'answer': store.messages()[-1]['text'],
                                'source': state['source'], 'metrics': state['metrics'], 'trace': state['trace'],
                                'model_rounds': state.get('model_rounds'), 'action_outcome': state.get('action_outcome'), 'error': state.get('error')})
                            save()
                        continue
                    else:
                        controller.set_session('start')
                        controller.observe_work('Disposable report', window)
                    if mode == 'cancelled':
                        if not entered.wait(8):
                            raise TimeoutError('Cancellation case did not open its real model request.')
                        controller.set_session('stop')
                        release.set()
                    controller.agent.worker.join(timeout=40)
                    if controller.agent.worker.is_alive():
                        controller.agent.cancel()
                        raise TimeoutError('Observation exceeded production guard.')
                    controller.distractions.wait(timeout=2)
                    state = controller.agent.info()
                    interruption = controller.interruption
                    started_action = any(t.get('tool') == 'request_distraction' and t.get('result', {}).get('ok') is True for t in state['trace'])
                    messages = store.messages()[before_messages:]
                    passed = (state['source'] == 'local model' and started_action and interruption and interruption['phase'] == 'opened' and interruption['destination'] == 'casino') if mode == 'allowed' else (not started_action and not interruption)
                    if mode == 'cancelled':
                        passed = passed and not any(m['role'] == 'assistant' for m in messages)
                    report['cases'].append({'case': mode, 'mechanical_pass': bool(passed), 'source': state['source'],
                        'answer': next((m['text'] for m in reversed(messages) if m['role'] == 'assistant'), None),
                        'trace': state['trace'], 'model_rounds': state.get('model_rounds'), 'action_outcome': state.get('action_outcome'),
                        'interruption': interruption, 'metrics': state['metrics'], 'wall_s': time.perf_counter() - begin,
                        'error': state.get('error'), 'cancel_transport_entered': entered.is_set()})
                    report['cases'][-1]['literal_role_header'] = any(
                        str(row.get('content', '')).lstrip().startswith(('assistant\n', 'assistant:', '<|start_header_id|>'))
                        for row in (state.get('model_rounds') or []))
                    save()
                    print(json.dumps(report['cases'][-1]), flush=True)
                finally:
                    release.set()
                    controller.close()
                    active_controller[0] = None
                    store.close()
            assert_full_gpu(ENDPOINT, MODEL)
        report['completed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        monitor_stop.set()
        if monitor_thread.is_alive(): monitor_thread.join(timeout=4)
        if changed:
            try:
                call(ENDPOINT, '/api/generate', {'model': MODEL, 'keep_alive': 0}, 20)
                for model in original:
                    call(ENDPOINT, '/api/generate', {'model': model['name'], 'stream': False, 'keep_alive': '30m',
                        'options': {'num_gpu': 999, 'num_ctx': model.get('context_length', 4096)}}, 40)
                report['restored_ps'] = ps(ENDPOINT)
                report['restored_gpu'] = gpu()
            except Exception as error:
                report['restore_error'] = str(error)
        report['source_unchanged'] = sources == hashes()
        report['protected_hashes_unchanged'] = protected_before == protected()
        report['wall_total_s'] = time.monotonic() - started
        save()
    return 0 if all(c.get('mechanical_pass', c.get('source') == 'local model') for c in report['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
