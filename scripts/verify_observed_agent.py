"""Opt-in real local-model work/tool check with every effect adapter replaced.

No watcher, native shell, microphone, playback, retailer or device is activated.
Uses a temporary profile/database; writes only an ignored JSON evidence file.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chuddy.agent import DEFAULT_MODEL, local_endpoint
from chuddy.controller import Controller
from chuddy.store import Store
from tests.helpers import FakeAudio, FakeVoice, FakeSimulator, FakeChromeLauncher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--output', type=Path, default=ROOT / '.chuddy/benchmarks/observed-agent.json')
    args = parser.parse_args()
    endpoint = local_endpoint(args.endpoint)
    window = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Disposable report - Word'}
    class NoDesktopWindows:
        def foreground(self):
            return dict(window)

        def identity(self, handle):
            return window['pid']

        def minimize_work(self, target):
            raise AssertionError('No desktop minimization is permitted in this check.')

        def restore(self, handle):
            raise AssertionError('No desktop restoration is permitted in this check.')
    with tempfile.TemporaryDirectory(prefix='chuddy-observation-check-') as folder:
        data = Path(folder)
        store = Store(data / 'state.sqlite3')
        store.update_settings({'ai_endpoint': endpoint, 'ai_model': args.model,
                               'voice': False, 'distraction_destination': 'casino'})
        controller = None
        try:
            with patch('chuddy.voice.Voice', FakeVoice), \
                 patch('chuddy.controller.AudioPipeline', FakeAudio), \
                 patch('chuddy.hardware.Simulator', FakeSimulator), \
                 patch('chuddy.pi_bridge.PiBridge', MagicMock()), \
                 patch('chuddy.interception.PreservingWindows', NoDesktopWindows), \
                 patch('chuddy.interception.ChromeLauncher', FakeChromeLauncher), \
                 patch('chuddy.controller.windows.foreground', return_value=window), \
                 patch('chuddy.controller.windows.minimize', side_effect=AssertionError('No real minimization')), \
                 patch('chuddy.controller.windows.restore', side_effect=AssertionError('No real restoration')):
                controller = Controller(store, data)
                controller.distractions.delay = .02
                controller.set_session('start')
                controller.observe_work('Disposable report', window)
                controller.agent.worker.join(timeout=35)
                if controller.agent.worker.is_alive():
                    controller.agent.cancel()
                    raise TimeoutError('The local observation turn did not finish.')
                controller.distractions.wait(timeout=2)
                agent = controller.agent.info()
                interruption = controller.interruption
                accepted = any(row.get('tool') == 'request_distraction' and row.get('result', {}).get('ok')
                               for row in agent['trace'])
                passed = bool(agent['source'] == 'local model' and accepted and interruption
                              and interruption['phase'] == 'opened' and interruption['destination'] == 'casino')
                report = {'passed': passed, 'model': args.model, 'endpoint': endpoint,
                          'source': agent['source'], 'metrics': agent['metrics'], 'trace': agent['trace'],
                          'interruption': interruption, 'answer': store.messages()[-1]['text'],
                          'effects': 'All window, voice, audio, simulator, Pi and retailer adapters are fakes. No watcher/native shell.',
                          'active_app_or_configuration_changed': False}
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                print(json.dumps(report, ensure_ascii=True), flush=True)
                return 0 if passed else 1
        finally:
            if controller:
                controller.close()
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
