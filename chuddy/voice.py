"""Local voice adapter. Construction never records or plays audio."""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from .worker_process import terminate_worker
from .persona import FartShuffleBag

ROOT = Path(__file__).resolve().parents[1]


def speech_text(text):
    """Keep subtitles intact while making letter jokes pronounceable."""
    letters = {'A': 'ay', 'B': 'bee', 'E': 'ee', 'F': 'eff', 'O': 'oh', 'P': 'pee',
               'R': 'ar', 'T': 'tee', 'U': 'you'}
    text = re.sub(r'\b([A-Z])(?=\.\.\.)', lambda m: letters.get(m[1], m[1]), text)
    return re.sub(r'\b[A-Z](?:-[A-Z]){1,}\b', lambda m: '. '.join(letters.get(x, x) for x in m[0].split('-')) + '.', text)


class Voice:
    def __init__(self, data_dir, settings_getter):
        self.data_dir = Path(data_dir)
        self.settings_getter = settings_getter
        self.config_path = self.data_dir / 'voice.json'
        self.config = {}
        self.error = ''
        if self.config_path.exists():
            try:
                self.config = json.loads(self.config_path.read_text(encoding='utf-8'))
            except Exception as error:
                self.error = 'Voice configuration: ' + str(error)
        self.jobs = queue.Queue()
        self.playback_jobs = queue.Queue(maxsize=12)
        self.lock = threading.RLock()
        self.generation = 0
        self.process = None
        self.responses = queue.Queue()
        self.log = None
        self.status = 'off'
        self.speaking = False
        self.playing = False
        self.output_kind = None
        self.last_metrics = None
        self.last_engine = 'not loaded'
        self.play_process = None
        self.last_turn = None
        self.playback_serial = 0
        self.filler_turns = set()
        audio_dir = ROOT / 'assets' / 'audio'
        variants = sorted(audio_dir.glob('fart-youtube-*.wav'))
        self.farts = FartShuffleBag(variants or [audio_dir / 'fart.wav'])
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._playback_loop, daemon=True).start()

    def info(self):
        ref = self.config.get('reference', '')
        engine = 'included WAV sound effect' if self.playing and self.output_kind == 'effect' else self.last_engine
        return {'status': self.status, 'engine': engine, 'tts_engine': self.last_engine, 'speaking': self.speaking,
                'playing': self.playing, 'output_kind': self.output_kind,
                'reference_ready': bool(ref and Path(ref).is_file()), 'error': self.error,
                'configured': bool(self.config), 'metrics': self.last_metrics,
                'similarity': 'unvalidated · listening review required',
                'fallback': 'Windows generic voice' if not self.config and os.name == 'nt' else None}

    def say(self, text, turn_id=None):
        if not self.settings_getter().get('voice', False) or self.settings_getter().get('voice_engine') == 'off':
            return False
        self._enqueue(text, turn_id)
        return True

    def _enqueue(self, text, turn_id):
        parts = re.split(r'(\b[A-Z](?:-[A-Z]){1,}\b)', text[:1400])
        cache = Path(self.config.get('cache', self.data_dir / 'voice-cache'))
        reference = bool(self.config) and self.settings_getter().get('voice_engine', 'auto') in ('auto', 'reference')
        for part in parts:
            if not part.strip():
                continue
            if reference and re.fullmatch(r'[A-Z](?:-[A-Z])+', part) and all((cache / ('letter-' + letter + '.wav')).is_file() for letter in part.split('-')):
                for letter in part.split('-'):
                    self.jobs.put(('letter', self.generation, str(cache / ('letter-' + letter + '.wav')), turn_id))
            else:
                self.jobs.put(('say', self.generation, speech_text(part), turn_id))

    def warm(self):
        if self.settings_getter().get('voice_engine') in ('off', 'windows'):
            return
        self.jobs.put(('warm', self.generation, '', None))

    def test(self):
        if self.settings_getter().get('voice_engine') == 'off':
            return {'accepted': False, 'error': 'Choose a voice engine first.'}
        self._enqueue("Hey. I'm Chuddy. R... E... uh... B-U-T-T. Report. Nailed it.", str(uuid.uuid4()))
        reference = bool(self.config) and self.settings_getter().get('voice_engine', 'auto') != 'windows'
        return {'accepted': True, 'source': 'reference-conditioned local voice · similarity unvalidated' if reference else 'Windows generic fallback'}

    def filler(self, turn_id):
        if not self.settings_getter().get('voice', False) or self.settings_getter().get('voice_engine') in ('off', 'windows'):
            return False
        with self.lock:
            if turn_id in self.filler_turns:
                return False
            self.filler_turns.add(turn_id)
            if len(self.filler_turns) > 200:
                self.filler_turns = {turn_id}
        cache = Path(self.config.get('cache', self.data_dir / 'voice-cache')) / 'filler.wav'
        if not cache.is_file():
            return False
        # Cached audio does not wait behind model loading/inference.
        threading.Thread(target=self._play, args=(cache, self.generation, 'filler'), daemon=True).start()
        return True

    def sound(self, kind, *, allowed=None, on_start=None, on_complete=None, admission=None):
        if kind != 'fart':
            raise ValueError('Only fart sounds are available.')
        path = self.farts.choose()
        if not path.exists():
            raise ValueError('Included sound asset is missing.')
        generation = self.generation
        if allowed and not allowed():
            return False
        self.jobs.put(('sound', generation, str(path), (allowed, on_start, on_complete, admission)))
        return True

    def stop(self):
        with self.lock:
            self.generation += 1
            self.speaking = False
            self.playing = False
            self.output_kind = None
            self.status = 'off'
            if os.name == 'nt':
                import winsound
                winsound.PlaySound(None, 0)
            if self.play_process and self.play_process.poll() is None:
                self.play_process.terminate()
            # Keep a healthy inference worker warm. Its late waveform is discarded
            # by generation checks; model inference is never trusted to trigger output.

    def close(self):
        self.stop()
        with self.lock:
            if self.process and self.process.poll() is None:
                terminate_worker(self.process)
            self.process = None
            if self.log:
                self.log.close()

    def _ensure_worker(self):
        if self.process and self.process.poll() is None:
            return
        config = self.config
        required = ['python', 'source', 'models']
        if any(not config.get(k) or not Path(config[k]).exists() for k in required):
            raise ValueError('Local voice is not prepared. Run the voice setup/benchmark instructions.')
        command = [config['python'], '-u', str(ROOT / 'chuddy' / 'voice_worker.py'), '--source', config['source'],
                   '--models', config['models'], '--engine', config.get('engine', 'turbo'),
                   '--device', config.get('device', 'cuda'), '--output', str(self.data_dir / 'voice-cache')]
        if config.get('reference'):
            command += ['--reference', config['reference']]
        self.log = open(self.data_dir / 'voice-worker.log', 'a', encoding='utf-8')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, encoding='utf-8', creationflags=0x08000000 if os.name == 'nt' else 0)
        self.responses = queue.Queue()
        def reader(process, responses):
            for line in process.stdout:
                responses.put(line)
            responses.put(None)
        threading.Thread(target=reader, args=(self.process, self.responses), daemon=True).start()
        self.last_engine = 'Chatterbox ' + config.get('engine', 'turbo') + ' · ' + config.get('device', 'cuda')

    def _rpc(self, text=None):
        self._ensure_worker()
        process = self.process
        request_id = str(uuid.uuid4())
        request = {'id': request_id, 'op': 'synthesize' if text else 'warm'}
        if text:
            request['text'] = text
        process.stdin.write(json.dumps(request) + '\n')
        process.stdin.flush()
        responses = self.responses
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                line = responses.get(timeout=min(1, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if result.get('id') == request_id:
                if not result.get('ok'):
                    raise ValueError(result.get('error', 'Voice synthesis failed.'))
                return result
        if process.poll() is None:
            terminate_worker(process)
        raise ValueError('Voice worker exited or timed out. See voice-worker.log.')

    def _play(self, path, generation, output_kind='speech', callbacks=None):
        import wave
        allowed, on_start, on_complete, admission = callbacks or (None, None, None, None)
        duration = 0.
        if os.name == 'nt' or on_start:
            with wave.open(str(path), 'rb') as audio:
                duration = audio.getnframes() / audio.getframerate()
        with self.lock:
            if generation != self.generation:
                return
            self.playback_serial += 1
            serial = self.playback_serial
            self.playing = True
            self.output_kind = output_kind
            self.speaking = output_kind in ('speech', 'filler')
        completed = False
        try:
            if os.name == 'nt':
                import winsound
                # ASYNC makes Stop reliable; wait by duration without holding the lock.
                with self.lock, admission or nullcontext():
                    if generation != self.generation or serial != self.playback_serial or (allowed and not allowed()):
                        return
                    started = time.monotonic()
                    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    if on_start and duration > 0:
                        on_start(path, started, duration)
                end = started + duration
                while time.monotonic() < end and generation == self.generation and serial == self.playback_serial:
                    time.sleep(0.025)
                completed = duration > 0
            else:
                command = ['aplay', '-q']
                if self.config.get('output_device'):
                    command += ['-D', self.config['output_device']]
                with self.lock, admission or nullcontext():
                    if generation != self.generation or serial != self.playback_serial or (allowed and not allowed()):
                        return
                    started = time.monotonic()
                    process = self.play_process = subprocess.Popen(command + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if on_start and duration > 0:
                        on_start(path, started, duration)
                try:
                    completed = process.wait(timeout=60) == 0
                except subprocess.TimeoutExpired:
                    with self.lock:
                        if generation == self.generation and serial == self.playback_serial:
                            process.terminate()
                    raise
                if not completed and generation == self.generation and serial == self.playback_serial:
                    raise ValueError('Local audio output failed.')
        finally:
            with self.lock:
                if generation == self.generation and serial == self.playback_serial:
                    self.speaking = False
                    self.playing = False
                    self.output_kind = None
                    if completed and on_complete:
                        on_complete()

    def _generic(self, text, generation):
        if os.name != 'nt':
            raise ValueError('Local voice is not prepared.')
        import base64
        encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
        script = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.Rate=-2; $v.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')))"
        command = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        with self.lock:
            if generation != self.generation:
                return
            self.speaking = True
            self.playing = True
            self.output_kind = 'speech'
            self.last_engine = 'Windows generic fallback · not Patrick'
            self.play_process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', command], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.play_process.wait(timeout=90)
        if generation == self.generation:
            self.speaking = False
            self.playing = False
            self.output_kind = None

    def _loop(self):
        while True:
            kind, generation, text, turn_id = self.jobs.get()
            if generation != self.generation:
                continue
            try:
                self.status = 'warming' if kind == 'warm' else 'generating'
                if kind in ('sound', 'letter'):
                    self.playback_jobs.put((Path(text), generation, 'effect' if kind == 'sound' else 'speech', turn_id if kind == 'sound' else None))
                elif self.config and self.settings_getter().get('voice_engine', 'auto') != 'windows':
                    result = self._rpc(text if kind != 'warm' else None)
                    self.last_metrics = {k: v for k, v in result.items() if k not in ('path', 'id')}
                    if kind != 'warm' and generation == self.generation:
                        self.playback_jobs.put((Path(result['path']), generation, 'speech'))
                elif kind != 'warm' and self.settings_getter().get('voice_engine', 'auto') != 'reference':
                    self._generic(text, generation)
                else:
                    raise ValueError('Reference-conditioned voice is not prepared.')
                if generation == self.generation:
                    self.status = 'ready' if self.config else 'generic fallback ready'
                    self.error = ''
            except Exception as error:
                if generation == self.generation:
                    self.status = 'error'
                    self.speaking = False
                    self.error = str(error)[:400]

    def _playback_loop(self):
        while True:
            job = self.playback_jobs.get()
            path, generation = job[:2]
            output_kind = job[2] if len(job) > 2 else 'speech'
            if generation != self.generation:
                continue
            try:
                self.status = 'playing sound' if output_kind == 'effect' else 'speaking'
                if len(job) > 3 and job[3]:
                    self._play(path, generation, output_kind, job[3])
                else:
                    self._play(path, generation, output_kind)
                if generation == self.generation:
                    self.status = 'ready' if self.config else 'generic fallback ready'
            except Exception as error:
                if generation == self.generation:
                    self.status, self.error = 'error', str(error)[:400]
