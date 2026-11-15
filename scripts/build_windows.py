"""Build a Windows one-folder executable and optionally smoke-test it headlessly.

No model downloads, recording, interception, sound playback or hardware activation.
"""
import argparse
import fnmatch
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid
import venv

ROOT = Path(__file__).resolve().parents[1]
BUILD_ENV = ROOT / '.chuddy' / 'build-venv'
SUPPORT_WORKERS = ('__init__.py', 'audio_setup.py', 'asr_worker.py', 'voice_worker.py',
                   'classifier_worker.py', 'worker_process.py', 'voice_setup.py', 'voice_benchmark.py', 'leaderboard.py')
PRIVATE_DIRECTORIES = {'.git', '.chuddy', '.venv', 'venv', '__pycache__', 'runtime', 'recordings',
                       'private', 'downloads', 'auditions', 'reference-auditions', 'voice-cache', 'node_modules', '.cache', '.pytest_cache',
                       'training', 'training-data', 'voice-training', 'gpt-sovits-training', 'datasets', 'checkpoints', 'models',
                       'voice-venv', 'asr-venv', 'audio-venv', 'build-venv'}
PRIVATE_PATTERNS = ('*.pyc', '*.pyo', '*.log', '*.env', '.env', '.env.*', '*.env.*', '*.sqlite3*',
                    '*.sqlite*', '*.db', '*.db-*', '*.db.*', '*.wal', '*.shm', '*.local.*',
                    '*.pem', '*.key', '*.p12', '*.pfx', 'config.json', 'secrets.json',
                    'credentials.json', 'settings.json', 'voice.json', 'asr.json', 'audio.json',
                    '*.ckpt', '*.pth', '*.pt', '*.safetensors', '*.onnx', '*.tflite')


def private_name(name):
    lowered = name.casefold()
    return lowered in PRIVATE_DIRECTORIES or any(fnmatch.fnmatchcase(lowered, pattern) for pattern in PRIVATE_PATTERNS)


def support_ignore(directory, names):
    return [name for name in names if private_name(name)]


def assert_public_bundle(target):
    forbidden = []
    for path in target.rglob('*'):
        parts = path.relative_to(target).parts
        for index, part in enumerate(parts):
            # pythonnet's public .NET support DLLs live in its legitimate
            # package runtime directory; this is not a user's runtime capture.
            if index == 2 and parts[:3] == ('_internal', 'pythonnet', 'runtime'):
                continue
            if private_name(part):
                forbidden.append(path)
                break
    if forbidden:
        raise RuntimeError('Private/local files unexpectedly entered the bundle: ' + ', '.join(str(path.relative_to(target)) for path in forbidden))


def environment_python(directory):
    return directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare():
    BUILD_ENV.parent.mkdir(parents=True, exist_ok=True)
    # Do not alter the desktop/audio/voice environments.
    if not environment_python(BUILD_ENV).is_file():
        venv.EnvBuilder(with_pip=True).create(BUILD_ENV)
    python = environment_python(BUILD_ENV)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'packaging' / 'requirements-build.txt')], check=True)
    return python


def copy_support(target):
    """Ship small setup sources; never copy application data or model environments."""
    assert_public_bundle(target)
    for folder in ('extension', 'pi', 'friends', 'docs'):
        source = ROOT / folder
        if source.exists():
            shutil.copytree(source, target / folder, dirs_exist_ok=True,
                            ignore=support_ignore)
    for filename in ('CHUDDY_CONCEPT.md', 'BUILD_PLAN.md'):
        source = ROOT / filename
        if source.is_file():
            shutil.copy2(source, target / filename)
    (target / 'scripts').mkdir(exist_ok=True)
    for filename in ('setup_local_ai.py', 'benchmark_ai.py'):
        source = ROOT / 'scripts' / filename
        if source.is_file():
            shutil.copy2(source, target / 'scripts' / filename)
    (target / 'chuddy').mkdir(exist_ok=True)
    for filename in SUPPORT_WORKERS:
        shutil.copy2(ROOT / 'chuddy' / filename, target / 'chuddy' / filename)
    if (ROOT / 'README.md').is_file():
        shutil.copy2(ROOT / 'README.md', target / 'SOURCE-README.md')
    for source in (ROOT / 'requirements.txt', ROOT / 'requirements-audio.txt'):
        shutil.copy2(source, target / source.name)
    (target / 'START-HERE.txt').write_text(
        'CHUDDY WINDOWS PROTOTYPE\n\n'
        'Run Chuddy.exe from this writable folder. Keep _internal beside it.\n'
        'Application data is external in .chuddy beside Chuddy.exe.\n'
        'The first launch is stopped; microphones and physical outputs are off.\n'
        'Windows WebView2 is required for the native window.\n'
        'Troubleshooting: Chuddy.exe --browser or Chuddy.exe --headless --no-ollama\n\n'
        'Free local AI models and optional voice/classifier environments are not\n'
        'bundled. They need explicit setup. See docs/PACKAGING.md and docs/DEMO.md.\n'
        'No worldwide service is deployed. No real banking or purchases exist.\n', encoding='utf-8')
    assert_public_bundle(target)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_snapshot():
    paths = [ROOT / 'run.py', ROOT / 'inputs' / 'chuddy-reference.png', ROOT / 'packaging' / 'Chuddy.spec']
    for folder in ('chuddy', 'web', 'pi', 'friends', 'extension', 'assets/audio', 'assets/icons'):
        paths.extend(path for path in (ROOT / folder).rglob('*') if path.is_file()
                     and not any(private_name(part) for part in path.relative_to(ROOT).parts))
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in sorted(set(paths)) if path.is_file()}


def verify_economy(read_state, post_action, reject_action):
    """Quiet integration smoke against only the verifier's disposable profile.

    Uses no voice, microphone, AI, session/interception or device actions. The
    clam timer is measured by the real server; it is not patched or backdated.
    """
    initial = read_state()
    profile_id = initial['profile']['id']
    assert isinstance(initial.get('earnings'), dict), 'Frozen state is missing earnings.'
    assert isinstance(initial.get('casino'), dict), 'Frozen state is missing casino.'
    assert initial['earnings']['total_earned'] == 0, 'Disposable profile must start without earnings.'

    # Start the real timer first; casino actions run while that timer elapses.
    ticket_payload = {'action': 'clam_game_start', 'profile_id': profile_id, 'request_id': str(uuid.uuid4())}
    began = time.monotonic()
    ticket = post_action(ticket_payload)
    assert post_action(ticket_payload) == ticket, 'Clam start retry changed the ticket.'
    completion_payload = {'action': 'clam_game_finish', 'profile_id': profile_id,
                          'round_id': ticket['round_id'], 'caught': 3, 'request_id': str(uuid.uuid4())}
    before_early = read_state()['balance']
    reject_action(completion_payload)
    assert read_state()['balance'] == before_early, 'Early completion changed the wallet.'

    score_payload = {'action': 'score', 'kind': 'fart', 'request_id': str(uuid.uuid4())}
    score = post_action(score_payload)
    assert score['counted'] is True
    after_score = read_state()
    manual_reward = after_score['balance'] - before_early
    assert manual_reward == initial['earnings']['rates']['fart'], 'Manual event reward differs from server policy.'
    assert post_action(score_payload)['counted'] is False, 'Duplicate score was counted again.'
    assert read_state()['balance'] == after_score['balance'], 'Duplicate score paid twice.'

    tables = []
    for game, bet in (('roulette', 2), ('blackjack', 2), ('poker', 20)):
        before = read_state()['balance']
        payload = {'action': 'casino_start', 'profile_id': profile_id, 'game': game,
                   'bet': bet, 'request_id': str(uuid.uuid4())}
        if game == 'roulette':
            payload['selection'] = {'kind': 'red'}
        started = post_action(payload)
        assert started['round']['game'] == game
        assert post_action(payload) == started, 'Casino start replay changed its result.'
        assert read_state()['balance'] == started['balance'], 'Casino start replay changed the wallet.'
        result = started
        reserve = result['round']['view']['reserve']
        moves = 0
        while not result['round']['complete']:
            assert moves < 5, 'Bounded casino smoke did not settle.'
            view = result['round']['view']
            assert not set(view).intersection({'deck', 'shoe', 'burned', 'acted_at', 'pending'}), 'Private deck data leaked.'
            move = 'stand' if game == 'blackjack' else 'fold'
            request = {'action': 'casino_act', 'profile_id': profile_id, 'round_id': result['round']['id'],
                       'revision': result['round']['revision'], 'move': move, 'request_id': str(uuid.uuid4())}
            result = post_action(request)
            canonical = read_state()['balance']
            assert post_action(request) == result, 'Casino action retry changed its stored result.'
            assert read_state()['balance'] == canonical, 'Casino action retry paid twice.'
            moves += 1
        payout = result['round']['payout']
        assert isinstance(payout, int) and payout >= 0
        assert result['balance'] == before - reserve + payout, 'Casino wallet reserve/return arithmetic differs.'
        assert read_state()['balance'] == result['balance']
        fetched = post_action({'action': 'casino_state'})
        assert fetched['round'] == result['round'], 'Saved round cannot be recovered through casino_state.'
        # A stale start replay is deliberately cached; it cannot debit/refund
        # again or replace the current wallet with its old balance.
        assert post_action(payload) == started
        assert read_state()['balance'] == result['balance'], 'Settled start replay changed canonical balance.'
        tables.append({'game': game, 'reserve': reserve, 'payout': payout, 'wallet_before': before,
                       'wallet_after': result['balance'], 'moves': moves, 'exact_retries': True})

    minimum = ticket['minimum_seconds']
    assert isinstance(minimum, (int, float)) and 0 < minimum <= 30, 'Unexpected server game duration.'
    remaining = minimum + .25 - (time.monotonic() - began)
    if remaining > 0:
        time.sleep(remaining)
    before_completion = read_state()['balance']
    completed = post_action(completion_payload)
    assert completed['status'] == 'completed'
    assert completed['reward'] == initial['earnings']['rates']['game']
    assert completed['balance'] == before_completion + completed['reward']
    assert post_action(completion_payload) == completed, 'Clam completion retry changed its stored payout.'
    final = read_state()
    assert final['balance'] == completed['balance'], 'Clam completion retry paid twice.'
    assert final['earnings']['score_earned'] == manual_reward
    assert final['earnings']['game_earned'] == completed['reward']
    reject_action({**completion_payload, 'request_id': str(uuid.uuid4())})
    assert read_state()['balance'] == final['balance'], 'Completed ticket paid again under a new request ID.'
    assert final['session'] == 'stopped' and not final['audio']['recording'] and not final['voice']['speaking']
    return {'status': 'passed', 'manual_reward': manual_reward, 'manual_duplicate_paid': False,
            'game_reward': completed['reward'], 'game_duration_seconds': round(time.monotonic() - began, 3),
            'early_game_claim_rejected': True, 'completion_retries_paid_once': True,
            'casino': tables, 'profile_disposable': True, 'session_started': False,
            'microphone_used': False, 'playback_used': False, 'model_requested': False}


def verify_silent_preview(read_state, post_action, reject_action):
    """One opt-in rehearsal on the disposable headless service, never a desktop."""
    initial = read_state()
    assert initial['session'] == 'stopped'
    assert initial['desktop_overlay']['status'] == 'unavailable', 'Headless verification must not construct a native overlay.'
    assert initial['desktop_overlay']['hotkey'] is False
    assert initial['desktop_interception']['pending_restorations'] == 0
    assert initial['desktop_interception']['covered'] is False
    assert initial['settings']['distraction_destination'] == 'casino'
    assert initial['settings']['reduced_motion'] is False
    assert post_action({'action': 'test_trigger'})['triggered'] is False, 'Stopped session accepted an interruption.'
    reject_action({'action': 'settings', 'values': {'distraction_destination': 'https://invalid.example/'}})
    reject_action({'action': 'settings', 'values': {'reduced_motion': 'false'}})
    assert read_state()['settings']['distraction_destination'] == 'casino'
    message_ids = [row['id'] for row in initial['messages']]
    turn_id = initial['agent'].get('turn_id')
    balance = initial['balance']
    try:
        post_action({'action': 'settings', 'values': {'distraction_destination': 'casino', 'reduced_motion': True}})
        post_action({'action': 'session', 'operation': 'start'})
        assert read_state()['desktop_overlay']['status'] == 'unavailable'
        assert post_action({'action': 'test_trigger'})['triggered'] is True
        deadline = time.monotonic() + 5
        while True:
            current = read_state()
            preview = current.get('interruption')
            if preview and preview.get('phase') == 'preview':
                break
            assert time.monotonic() < deadline, 'Silent rehearsal did not reach its bounded preview state.'
            time.sleep(.05)
        assert preview['test'] is True and preview['destination'] == 'casino'
        assert current['desktop_interception']['pending_restorations'] == 0
        assert current['desktop_overlay']['status'] == 'unavailable' and not current['desktop_overlay']['hotkey']
        assert [row['id'] for row in current['messages']] == message_ids, 'Silent rehearsal generated dialogue.'
        assert current['agent'].get('turn_id') == turn_id, 'Silent rehearsal began an AI turn.'
        assert current['balance'] == balance, 'Opening the rehearsal changed the wallet.'
        assert not current['audio']['recording'] and not current['voice']['speaking']
    finally:
        post_action({'action': 'session', 'operation': 'stop'})
        post_action({'action': 'settings', 'values': {'distraction_destination': 'casino', 'reduced_motion': False}})
    stopped = read_state()
    assert stopped['session'] == 'stopped' and stopped['interruption'] is None
    assert stopped['desktop_interception']['covered'] is False
    assert stopped['desktop_interception']['pending_restorations'] == 0
    return {'status': 'passed', 'session_opt_in': 'disposable headless instance only', 'destination': 'casino',
            'phase': 'preview', 'default_settings_validated': True, 'invalid_settings_rejected': True,
            'stop_cleared_preview': True, 'native_overlay_created': False, 'windows_minimized': False,
            'retailer_opened': False, 'dialogue_created': False, 'ai_turn_started': False,
            'microphone_used': False, 'playback_used': False}


def require_same_snapshot(before, after):
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    if changed:
        raise RuntimeError('Runtime source changed during build; wait for a new freeze before rebuilding: ' + ', '.join(changed))


def verify(target):
    executable = target / 'Chuddy.exe'
    if not executable.is_file():
        raise RuntimeError('Executable not found: ' + str(executable))
    manifest = json.loads((target / 'build-manifest.json').read_text(encoding='utf-8'))
    current_snapshot = hashlib.sha256(json.dumps(source_snapshot(), sort_keys=True).encode('utf-8')).hexdigest()
    if manifest.get('source_snapshot_sha256') != current_snapshot:
        raise RuntimeError('Source changed after executable build; rebuild before verifying the current release.')
    worker_hashes = {}
    for filename in ('voice_worker.py', 'asr_worker.py', 'classifier_worker.py', 'audio_setup.py', 'worker_process.py'):
        source_hash = sha256(ROOT / 'chuddy' / filename)
        for folder in (target / '_internal' / 'chuddy', target / 'chuddy'):
            if sha256(folder / filename) != source_hash:
                raise RuntimeError('External worker source differs from current source: ' + str(folder / filename))
        worker_hashes[filename] = source_hash
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='chuddy-package-check-') as temporary:
        data = Path(temporary) / 'data'
        log_path = Path(temporary) / 'launch.log'
        with log_path.open('wb') as log:
            process = subprocess.Popen([str(executable), '--headless', '--no-watch', '--no-ollama', '--port', str(port), '--data-dir', str(data)],
                                       cwd=str(target), stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=0x08000000 if os.name == 'nt' else 0)
            try:
                base = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 25
                last_error = ''
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('Packaged app exited early:\n' + log_path.read_text(errors='replace'))
                    try:
                        with urllib.request.urlopen(base + '/health', timeout=1) as response:
                            if json.load(response).get('status') == 'ok':
                                break
                    except OSError as error:
                        last_error = str(error)
                        time.sleep(.2)
                else:
                    raise RuntimeError('Packaged API did not become ready: ' + last_error + '\n' + log_path.read_text(errors='replace'))
                with urllib.request.urlopen(base + '/', timeout=3) as response:
                    html = response.read().decode('utf-8')
                match = re.search(r'window\.CHUDDY_TOKEN=("[^"\n]+")', html)
                if not match:
                    raise RuntimeError('Packaged HTML did not contain the local bootstrap token.')
                token = json.loads(match[1])
                request = urllib.request.Request(base + '/api/state', headers={'X-Chuddy-Token': token})
                with urllib.request.urlopen(request, timeout=3) as response:
                    state = json.load(response)
                def read_state():
                    request = urllib.request.Request(base + '/api/state', headers={'X-Chuddy-Token': token})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.load(response)

                def post_action(payload):
                    request = urllib.request.Request(base + '/api/action', json.dumps(payload).encode('utf-8'),
                        {'X-Chuddy-Token': token, 'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return json.load(response)

                def reject_action(payload):
                    try:
                        post_action(payload)
                    except urllib.error.HTTPError as error:
                        assert error.code == 400, 'Expected a validated client rejection.'
                        assert json.load(error).get('error'), 'Rejection is missing its error message.'
                    else:
                        raise AssertionError('Invalid wallet/game action was unexpectedly accepted.')
                assert state['session'] == 'stopped', 'Fresh packaged session must be stopped.'
                assert not state['audio']['recording'], 'Microphone must be off.'
                assert not state.get('voice', {}).get('speaking'), 'Voice must not play at launch.'
                assert state['settings']['ai_work_detection'] is True, 'Fresh work-recognition preference must be enabled.'
                work_detection = state['work_detection']
                assert work_detection['status'] == 'stopped' and work_detection['is_work'] is False, 'Stopped headless launch must not classify work.'
                assert work_detection.get('label') is None and work_detection.get('source') is None, 'Fresh work diagnostics must not contain an observation.'
                native_window = state['native_window']
                assert native_window['status'] == 'unavailable', 'Headless launch must not construct native window dispatch.'
                assert native_window.get('pending') is None and native_window.get('applied') is None and native_window.get('error') is None, 'Headless native-window diagnostics must be inactive.'
                assert data.joinpath('chuddy.sqlite3').is_file(), 'Selected external data directory must contain the database.'
                economy = verify_economy(read_state, post_action, reject_action)
                fresh = read_state()
                assert set(fresh['leaderboard_periods']) == {'today', 'week', 'all_time', 'timezone'}
                assert fresh['leaderboard_periods']['timezone'] == 'America/Toronto'
                assert all(not source['connected'] for source in fresh['deadline_sync']['sources'])
                cheat = {'action': 'admin_cheat', 'command': 'monies 123456',
                         'request_id': str(uuid.uuid4()), 'profile_id': fresh['profile']['id']}
                credited = post_action(cheat)
                assert credited['balance'] == fresh['balance'] + 123456
                assert post_action(cheat) == credited
                assert read_state()['balance'] == credited['balance']
                reject_action({**cheat, 'request_id': str(uuid.uuid4()), 'command': 'monies -1'})
                assert 'id="page-arcade"' not in html and 'id="session-stop"' not in html
                assert 'id="admin-console"' in html and 'id="interruption-overlay"' not in html
                icon = target / '_internal' / 'assets' / 'icons' / 'chuddy-desktop.ico'
                assert sha256(icon) == sha256(ROOT / 'assets' / 'icons' / 'chuddy-desktop.ico')
                preview = verify_silent_preview(read_state, post_action, reject_action)
                checked = {}
                hashes = {}
                action_manifest = None
                routes = ['/web/style.css', '/web/app.js', '/web/ui-utils.js', '/web/character-rig.js',
                          '/web/earnings.css', '/web/concise-ui.css', '/web/casino-room.css', '/web/casino-room.js',
                          '/web/chud-cursor.css', '/web/chud-cursor.js',
                          '/web/assets/chuddy/gestures.png', '/web/assets/casino/action-manifest.json', '/reference.png']
                routes.extend('/' + path.relative_to(ROOT).as_posix() for path in sorted((ROOT / 'web' / 'assets' / 'casino').glob('*.png')))
                for route in routes:
                    with urllib.request.urlopen(base + route, timeout=3) as response:
                        content = response.read()
                    assert len(content) > 100, 'Missing packaged resource: ' + route
                    checked[route] = len(content)
                    source = ROOT / ('inputs/chuddy-reference.png' if route == '/reference.png' else route.lstrip('/'))
                    hashes[route] = hashlib.sha256(content).hexdigest()
                    assert hashes[route] == sha256(source), 'Packaged bytes differ from current source: ' + route
                    if route == '/web/assets/casino/action-manifest.json':
                        action_manifest = json.loads(content)
                assert isinstance(action_manifest, list) and action_manifest, 'Casino action manifest must contain assets.'
                action_files = set()
                for entry in action_manifest:
                    filename = entry.get('file') if isinstance(entry, dict) else None
                    assert isinstance(filename, str) and Path(filename).name == filename and filename.endswith('.png'), 'Invalid casino asset name.'
                    assert filename not in action_files, 'Duplicate casino action asset: ' + filename
                    action_files.add(filename)
                    route = '/web/assets/casino/' + filename
                    assert route in hashes and hashes[route] == entry.get('sha256'), 'Casino action manifest hash differs from served PNG: ' + filename
                required_gary = {'action-gary-dealer-clean.png', 'action-gary-suit.png'}
                assert required_gary <= action_files, 'Casino action manifest is missing Gary sibling assets.'
                assert '/web/chuddy.svg' not in html, 'Obsolete SVG character reference remains.'
                assert not (target / '_internal' / 'web' / 'chuddy.svg').exists(), 'Obsolete SVG character asset remains.'
                assert_public_bundle(target)
                return {'status': 'passed', 'executable': str(executable), 'executable_sha256': sha256(executable), 'headless': True,
                        'fresh_session': state['session'], 'microphone_recording': state['audio']['recording'],
                        'voice_speaking': state.get('voice', {}).get('speaking'), 'resources': checked, 'resource_sha256': hashes,
                        'fresh_startup_diagnostics': {'passive_only': True, 'ai_work_detection': state['settings']['ai_work_detection'],
                            'work_detection': work_detection, 'native_window': native_window},
                        'external_data_directory': True, 'visible_window_opened': False,
                        'native_webview_verified': False, 'audio_devices_verified': False,
                        'models_bundled': False, 'watcher_disabled': True, 'portable_model_start_disabled': True,
                        'worker_source_sha256': worker_hashes, 'source_snapshot_sha256': current_snapshot,
                        'earnings_and_casino': economy,
                        'requested_updates': {'period_rankings': True, 'calendar_module_loaded': True,
                            'accounts_connected': False, 'admin_exact_retry': True, 'character_icon_packaged': True,
                            'retired_ui_absent': True},
                        'silent_preview': preview,
                        'casino_action_manifest': {'status': 'passed', 'entries': len(action_files), 'gary_assets': sorted(required_gary)},
                        'private_file_filter_passed': True, 'old_svg_character_absent': True}
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='Install build-only dependencies into .chuddy/build-venv.')
    parser.add_argument('--python', type=Path, help='Python executable containing PyInstaller; default isolated build environment.')
    parser.add_argument('--verify', action='store_true', help='After building, start only an isolated headless API and inspect safe state/resources.')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--expected-persona-sha', help='Refuse a build if the declared frozen persona bytes differ.')
    parser.add_argument('--dist', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Build the Windows executable on Windows.')
    output = args.dist.resolve()
    target = output / 'Chuddy'
    python = args.python or (prepare() if args.prepare else environment_python(BUILD_ENV))
    if not args.verify_only:
        if args.expected_persona_sha and sha256(ROOT / 'chuddy' / 'persona.py') != args.expected_persona_sha.lower():
            raise SystemExit('Persona SHA-256 differs from the declared freeze; no build started.')
        if not python.is_file():
            raise SystemExit('Build Python missing. Run again with --prepare or supply --python.')
        before_build = source_snapshot()
        subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', str(output),
                        '--workpath', str(ROOT / 'build' / 'windows'), str(ROOT / 'packaging' / 'Chuddy.spec')], cwd=str(ROOT), check=True)
        require_same_snapshot(before_build, source_snapshot())
        copy_support(target)
        files = [path for path in target.rglob('*') if path.is_file()]
        assert_public_bundle(target)
        snapshot = source_snapshot()
        require_same_snapshot(before_build, snapshot)
        manifest = {'name': 'Chuddy', 'format': 'PyInstaller one-folder', 'python': sys.version,
                    'file_count': len(files), 'bytes': sum(path.stat().st_size for path in files),
                    'executable_sha256': sha256(target / 'Chuddy.exe'),
                    'source_snapshot_sha256': hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode('utf-8')).hexdigest(),
                    'source_sha256': snapshot,
                    'includes_models': False, 'includes_private_data': False}
        (target / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in manifest.items() if key != 'source_sha256'}, indent=2), flush=True)
    if args.verify or args.verify_only:
        result = verify(target)
        report_path = ROOT / 'test-results' / 'packaging.json'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)
    print('Bundle folder: ' + str(target), flush=True)


if __name__ == '__main__':
    main()
