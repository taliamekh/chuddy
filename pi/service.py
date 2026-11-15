"""Pi local companion HTTP service. Outputs/microphone always off at startup."""
import argparse
import base64
import collections
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import uuid
import io
import wave
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .engine import Sequence, SimulatedOutputs, SIM_CALIBRATION
from chuddy.audio_events import ECHO_DELAY
from chuddy.persona import FartShuffleBag

ROOT = Path(__file__).resolve().parents[1]


def utc():
    return datetime.now(timezone.utc).isoformat()


class Companion:
    def __init__(self, data_dir, config=None):
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self.config = config or {'backend': 'simulator', 'motion': SIM_CALIBRATION}
        self.startup_id = str(uuid.uuid4())
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path / 'pi.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,digest TEXT,response TEXT);
            CREATE TABLE IF NOT EXISTS events(cursor INTEGER PRIMARY KEY,id TEXT UNIQUE,type TEXT,time TEXT,payload TEXT);''')
        self.db.commit()
        self.armed = False
        self.disarming = False
        self.lease = 0
        self.runner = self._simulator()
        audio_dir = ROOT / 'assets' / 'audio'
        variants = sorted(audio_dir.glob('fart-youtube-*.wav'))
        self.farts = FartShuffleBag(variants or [audio_dir / 'fart.wav'])
        self.play_process = None
        self.playback_generation = 0
        self.effect_references = collections.deque(maxlen=24)
        self.button = None
        self.mic = None
        self.audio_chunks = collections.deque(maxlen=8)
        self.audio_cursor = 0
        self.running = True
        self.belly_last = -100.0
        self.emit('service.started', {'outputs': 'disarmed', 'microphone': 'off'})
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _simulator(self):
        return Sequence(event=self.emit, playback=self._belly_sound)

    def emit(self, kind, payload):
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(id,type,time,payload) VALUES(?,?,?,?)', (str(uuid.uuid4()), kind, utc(), json.dumps(payload)))

    def events(self, after):
        with self.lock:
            rows = self.db.execute('SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT 100', (after,)).fetchall()
            return {'version': 1, 'startup_id': self.startup_id, 'cursor': rows[-1]['cursor'] if rows else after,
                    'events': [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]}

    def status(self):
        with self.lock:
            cursor = self.db.execute('SELECT COALESCE(MAX(cursor),0) FROM events').fetchone()[0]
        return {'version': 1, 'startup_id': self.startup_id, 'event_cursor': cursor, **self.runner.info(), 'armed': self.armed,
                'lease_remaining': max(0, round(self.lease - time.monotonic(), 1)) if self.armed else 0,
                'backend': self.config.get('backend', 'simulator'), 'microphone': self.mic is not None,
                'calibrated': bool(self.config.get('motion', {}).get('calibrated')),
                'capabilities': ['belly', 'yaw', 'audio.play', 'audio.speak', 'audio.wav', 'mic.pcm', 'spray.request'],
                'hardware_validated': False}

    def _watchdog(self):
        while self.running:
            if self.armed and time.monotonic() >= self.lease:
                self.disarm('lease expired')
            time.sleep(0.1)

    def disarm(self, reason='explicit stop'):
        with self.lock:
            self.armed = False
            self.lease = 0
        self.runner.stop()
        self.stop_playback()
        self.stop_mic()
        if self.button:
            self.button.close()
            self.button = None
        if not self.runner.info()['busy']:
            self.runner.outputs.close()
        self.emit('outputs.disarmed', {'reason': reason})
        self.disarming = False

    def arm(self, payload):
        if self.disarming:
            raise ValueError('Outputs are still stopping.')
        if not isinstance(payload.get('spray', False), bool):
            raise ValueError('Spray enable must be an explicit boolean.')
        seconds = payload.get('lease_seconds', 10)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 2 <= seconds <= 30:
            raise ValueError('Output lease must be 2–30 seconds.')
        if self.config.get('backend', 'simulator') == 'simulator':
            return {'armed': False, 'mode': 'simulator', 'spray': 'disabled'}
        if self.runner.info()['busy']:
            raise ValueError('Wait for the current sequence to finish.')
        from .outputs import ServoOutputs
        outputs = ServoOutputs(self.config, spray=bool(payload.get('spray', False)))
        try:
            self.runner.outputs.close()
            self.runner = Sequence(outputs, self.config['motion'], self.emit, self._belly_sound,
                                   allowed=lambda: self.armed and time.monotonic() < self.lease)
            self.runner.spray_enabled = bool(payload.get('spray', False))
            self.lease, self.armed = time.monotonic() + seconds, True
            # Button setup is explicit with arming; pins have no defaults.
            if self.config.get('belly_pin') is not None:
                from gpiozero import Button
                self.button = Button(self.config['belly_pin'], pull_up=self.config.get('belly_pull_up', True), bounce_time=0.15)
                self.button.when_pressed = self.belly_press
        except Exception:
            outputs.close()
            self.armed = False
            raise
        self.emit('outputs.armed', {'lease_seconds': seconds, 'spray': self.runner.spray_enabled})
        return self.status()

    def belly_press(self):
        with self.lock:
            if time.monotonic() - self.belly_last < 0.5:
                return
            self.belly_last = time.monotonic()
        self.emit('belly.press', {})
        try:
            self.runner.start()
        except ValueError as error:
            self.emit('belly.ignored', {'reason': str(error)})

    def _belly_sound(self, kind):
        with self.lock:
            runner = self.runner
            if self.disarming or runner.cancel.is_set() or not runner.allowed():
                return
            source = 'physical playback' if runner.outputs.physical else 'simulator sound test'
            self.play_sound(kind, source, runner.cancel, runner.allowed)

    def play_sound(self, kind, source='explicit Pi sound test', cancel=None, allowed=None):
        if kind != 'fart':
            raise ValueError('Only fart sounds are available.')
        with self.lock:
            if self.disarming:
                raise ValueError('Outputs are still stopping.')
            self.stop_playback()
            generation = self.playback_generation
            path = self.farts.choose()
            with wave.open(str(path), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
            started, playback_id = time.monotonic(), str(uuid.uuid4())
            process = None
            if os.name == 'nt':
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            else:
                command = ['aplay', '-q']
                if self.config.get('output_device'):
                    command += ['-D', self.config['output_device']]
                process = self.play_process = subprocess.Popen(command + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.effect_references.append((path.name, started, duration))
            effect = {'kind': kind, 'source': source, 'playback_id': playback_id}
            self.emit('audio.playback.started', effect)
            threading.Thread(target=self._finish_effect, args=(process, started + duration, generation, cancel,
                             allowed, {**effect, 'confirmed': True}), daemon=True).start()

    def _finish_effect(self, process, ends_at, generation, cancel, allowed, payload):
        try:
            if process is None:
                time.sleep(max(0, ends_at - time.monotonic()))
            elif process.wait(timeout=15) != 0:
                return
        except subprocess.TimeoutExpired:
            with self.lock:
                if generation == self.playback_generation:
                    self.stop_playback()
            return
        with self.lock:
            if (self.running and generation == self.playback_generation and not self.disarming
                    and not (cancel and cancel.is_set()) and (not allowed or allowed())):
                self.emit('audio.playback', payload)

    def stop_playback(self):
        with self.lock:
            self.playback_generation += 1
            if self.play_process and self.play_process.poll() is None:
                self.play_process.terminate()
            if os.name == 'nt':
                import winsound
                winsound.PlaySound(None, 0)

    def speak(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 500:
            raise ValueError('Speech must be 1–500 characters.')
        if os.name == 'nt':
            raise ValueError('Pi speech uses espeak-ng; desktop voice has its own adapter.')
        self.stop_playback()
        # Argument list, never a shell. This is a labeled generic Pi fallback.
        self.play_process = subprocess.Popen(['espeak-ng', '-v', self.config.get('speech_voice', 'en'), '--', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.emit('audio.speech', {'engine': 'espeak-ng generic fallback'})

    def play_wav(self, payload):
        """Play a bounded PC-generated voice WAV; never accept an arbitrary path."""
        data = base64.b64decode(payload.get('wav', ''), validate=True)
        if not 44 <= len(data) <= 1500000:
            raise ValueError('Generated voice WAV must be at most 1.5 MB.')
        with wave.open(io.BytesIO(data), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getcomptype() != 'NONE' or audio.getsampwidth() != 2 or audio.getnchannels() not in (1, 2) or not 8000 <= audio.getframerate() <= 48000 or not .05 <= duration <= 20:
                raise ValueError('Use 0.05–20 seconds of PCM16 mono/stereo WAV, 8–48 kHz.')
        self.stop_playback()
        path = self.path / 'generated-voice.wav'
        path.write_bytes(data)
        if os.name == 'nt':
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            command = ['aplay', '-q']
            if self.config.get('output_device'):
                command += ['-D', self.config['output_device']]
            self.play_process = subprocess.Popen(command + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.emit('audio.speech', {'engine': 'PC-generated WAV', 'duration': round(duration, 3)})

    def start_mic(self):
        if self.mic is not None:
            return
        import sounddevice as sd
        rate = self.config.get('sample_rate', 16000)
        if rate not in (8000, 16000, 22050, 44100, 48000):
            raise ValueError('Unsupported microphone sample rate.')

        def capture(data, frames, timing, status):
            with self.lock:
                if self.mic is None:
                    return
                captured = time.monotonic() - frames / rate
                if timing is not None and 0 < timing.inputBufferAdcTime <= timing.currentTime:
                    captured = time.monotonic() - (timing.currentTime - timing.inputBufferAdcTime)
                effects = [{'asset': asset, 'offset': start - captured, 'duration': duration}
                           for asset, start, duration in self.effect_references
                           if captured - duration - ECHO_DELAY <= start <= captured + frames / rate + ECHO_DELAY]
                self.audio_cursor += 1
                self.audio_chunks.append({'id': str(uuid.uuid4()), 'cursor': self.audio_cursor, 'startup_id': self.startup_id, 'rate': rate, 'encoding': 'pcm_s16le', 'channels': 1,
                                          'pcm': base64.b64encode(bytes(data)).decode('ascii'), 'effects': effects})
        stream = sd.RawInputStream(samplerate=rate, channels=1, dtype='int16', device=self.config.get('input_device'),
                                  blocksize=rate, callback=capture)
        try:
            stream.start()
            self.mic = stream
            self.emit('microphone.started', {'rate': rate, 'raw_audio_saved': False})
        except Exception:
            stream.close()
            raise

    def stop_mic(self):
        with self.lock:
            mic, self.mic = self.mic, None
            self.audio_chunks.clear()
        if mic:
            # PortAudio may wait for a callback that needs self.lock. Never wait
            # while a command owns that lock; detached callbacks drop samples.
            def close_stream():
                try:
                    mic.abort()
                finally:
                    mic.close()
            threading.Thread(target=close_stream, daemon=True).start()

    def command(self, request):
        if not isinstance(request, dict) or request.get('version') != 1:
            raise ValueError('Protocol version 1 is required.')
        request_id = request.get('id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError('A bounded command id is required.')
        digest = hashlib.sha256(json.dumps({k: request.get(k) for k in ('type', 'payload')}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            row = self.db.execute('SELECT * FROM commands WHERE id=?', (request_id,)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise ValueError('Command id was reused with different content.')
                return {**json.loads(row['response']), 'replayed': True}
            try:
                stamp = datetime.fromisoformat(request['time'].replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
            except Exception as error:
                raise ValueError('Use a UTC/offset timestamp.') from error
            if not -5 <= age <= 15:
                raise ValueError('Command is stale or clock skew is too large.')
            kind, payload = request.get('type'), request.get('payload', {})
            if not isinstance(payload, dict):
                raise ValueError('Payload must be an object.')
            # Persist acceptance before any output. A restart will not replay effects.
            response = {'version': 1, 'id': request_id, 'accepted': True, 'type': kind}
            with self.db:
                self.db.execute('INSERT INTO commands VALUES(?,?,?)', (request_id, digest, json.dumps(response)))
            try:
                if kind == 'belly.perform':
                    response.update(self.runner.start())
                elif kind == 'outputs.arm':
                    response.update(self.arm(payload))
                elif kind == 'outputs.renew':
                    if not self.armed:
                        raise ValueError('Outputs are disarmed.')
                    seconds = payload.get('lease_seconds', 10)
                    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 2 <= seconds <= 30:
                        raise ValueError('Lease must be 2–30 seconds.')
                    self.lease = time.monotonic() + seconds
                elif kind == 'outputs.stop':
                    # Avoid joining a sequence while holding the event database lock.
                    self.armed = False
                    self.disarming = True
                    self.lease = 0
                    self.runner.cancel.set()
                    self.stop_playback()
                    threading.Thread(target=self.disarm, daemon=True).start()
                elif kind == 'audio.play':
                    self.play_sound(payload.get('kind'))
                elif kind == 'audio.speak':
                    self.speak(payload.get('text'))
                elif kind == 'audio.wav':
                    self.play_wav(payload)
                elif kind == 'mic.start':
                    self.start_mic()
                elif kind == 'mic.stop':
                    self.stop_mic()
                elif kind == 'spray.request':
                    # Only bounded sequence can press/release; standalone spray requests
                    # record intent but cannot hold a pump indefinitely.
                    self.emit('spray.request', {'enabled': self.runner.spray_enabled, 'standalone_output': False})
                    response['spray'] = 'disabled' if not self.runner.spray_enabled else 'available only inside belly sequence'
                else:
                    raise ValueError('Unknown command type.')
            except Exception as error:
                response.update(accepted=False, error=type(error).__name__ + ': ' + str(error))
            with self.db:
                self.db.execute('UPDATE commands SET response=? WHERE id=?', (json.dumps(response), request_id))
            self.emit('command.result', response)
            return response

    def close(self):
        self.running = False
        self.disarm('service shutdown')


def make_server(companion, token, host='127.0.0.1', port=8766):
    if not token or len(token) < 16:
        raise ValueError('Choose a Pi token with at least 16 characters.')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if not secrets.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
                self.reply(401, {'error': 'Pi token required.'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            url = urlsplit(self.path)
            try:
                after = int(parse_qs(url.query).get('after', ['0'])[0])
                if after < 0:
                    raise ValueError('Cursor must be nonnegative.')
                if url.path == '/v1/status':
                    value = companion.status()
                elif url.path == '/v1/events':
                    value = companion.events(after)
                elif url.path == '/v1/audio':
                    with companion.lock:
                        value = {'version': 1, 'startup_id': companion.startup_id, 'cursor': companion.audio_cursor, 'chunks': [c for c in companion.audio_chunks if c['cursor'] > after], 'raw_audio_saved': False}
                else:
                    return self.reply(404, {'error': 'Unknown endpoint.'})
                self.reply(200, value)
            except ValueError as error:
                self.reply(400, {'error': str(error)})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path not in ('/v1/commands', '/v1/playback'):
                return self.reply(404, {'error': 'Unknown endpoint.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                limit = 2100000 if self.path == '/v1/playback' else 32768
                if not 0 < length <= limit:
                    return self.reply(413, {'error': 'Request exceeds the endpoint size limit.'})
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('Request must be an object.')
                if self.path == '/v1/playback' and request.get('type') != 'audio.wav':
                    raise ValueError('Playback endpoint accepts only audio.wav.')
                self.reply(200, companion.command(request))
            except (ValueError, KeyError, TypeError) as error:
                self.reply(400, {'error': str(error)[:250]})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path('.chuddy/pi'))
    parser.add_argument('--host', default='127.0.0.1', help='Use a specific LAN address for PC/Pi access.')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    token = os.environ.get('CHUDDY_PI_TOKEN')
    if not token:
        parser.error('Set CHUDDY_PI_TOKEN to a private token of at least 16 characters.')
    config = json.loads(args.config.read_text()) if args.config else None
    companion = Companion(args.data_dir, config)
    server = make_server(companion, token, args.host, args.port)
    print(f'Chuddy Pi service: {args.host}:{server.server_port}; disarmed; microphone off')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        companion.close()
        server.server_close()


if __name__ == '__main__':
    main()
