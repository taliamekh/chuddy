"""User-operated Pi connection and scoring bridge. Never arms outputs or starts capture implicitly."""
import base64
import hashlib
import ipaddress
import threading
from collections import OrderedDict
from urllib.parse import urlparse

from .pi_client import PiClient
from .audio_events import SoundRuns, effect_path

PLAYBACK_SOURCES = {
    'physical playback': 'Chuddy Pi speaker · physical hardware unvalidated',
    'simulator sound test': 'Chuddy Pi speaker · simulated belly',
    'explicit Pi sound test': 'Chuddy Pi speaker · deliberate sound test',
}


def local_pi_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError('Enter a local Pi service address.')
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Use a local HTTP(S) Pi address without a path or credentials.')
    try:
        parsed.port
    except ValueError as error:
        raise ValueError('Invalid Pi service port.') from error
    local = parsed.hostname == 'localhost' or parsed.hostname.endswith('.local')
    try:
        address = ipaddress.ip_address(parsed.hostname)
        networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', 'fc00::/7', 'fe80::/10')
        local = local or address.is_loopback or any(address in ipaddress.ip_network(net) for net in networks if address.version == ipaddress.ip_network(net).version)
    except ValueError:
        pass
    if not local:
        raise ValueError('Pi connections must use loopback, a LAN IP or a .local hostname.')
    return value.rstrip('/')


def cursor(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise ValueError('Invalid Pi event cursor.')
    return value


class PiBridge:
    POLL_SECONDS = 0.4

    def __init__(self, store, audio):
        self.store, self.audio = store, audio
        self.lock = threading.RLock()
        self.client = None
        self.cancel = threading.Event()
        self.cancel.set()
        self.mic_cancel = threading.Event()
        self.mic_cancel.set()
        self.worker = None
        self.profile_id = None
        self.url = ''
        self.cursor_key = ''
        self.audio_cursor = 0
        self.playback_baseline = 0
        self.playback_started = OrderedDict()
        self.audio_runs = SoundRuns()
        self.score_allowed = lambda: True
        self.startup_id = ''
        self.microphone_enabled = False
        self.state = {'status': 'disconnected', 'connected': False, 'microphone': False, 'microphone_error': '', 'error': '', 'remote': None, 'profile_id': None, 'recent': [], 'scored': 0}

    def info(self):
        with self.lock:
            return {**self.state, 'recent': [dict(item) for item in self.state['recent']], 'remote': dict(self.state['remote']) if isinstance(self.state['remote'], dict) else None}

    def _async_stop(self, client):
        def stop_remote():
            try:
                client.stop()
            except Exception:
                pass  # Local cancellation never depends on a reachable Pi.
        threading.Thread(target=stop_remote, daemon=True).start()

    def disconnect(self):
        with self.lock:
            self.cancel.set()
            self.mic_cancel.set()
            client, self.client = self.client, None
            self.microphone_enabled = False
            self.state.update(status='disconnected', connected=False, microphone=False, error='')
        if client:
            self._async_stop(client)
        return self.info()

    def stop(self):
        return self.disconnect()

    def connect(self, url, token):
        url = local_pi_url(url)
        if not isinstance(token, str) or not 16 <= len(token) <= 2000 or any(char in token for char in ('\r', '\n')):
            raise ValueError('Enter the Pi service token (at least 16 characters).')
        self.disconnect()
        client = PiClient(url, token)
        cancel = threading.Event()
        profile_id = self.store.profile()['id']
        key = 'pi_cursor_' + hashlib.sha256((url + '|' + profile_id).encode()).hexdigest()
        with self.lock:
            self.client, self.cancel = client, cancel
            self.url, self.profile_id, self.cursor_key = url, profile_id, key
            self.state.update(status='connecting', connected=False, error='', profile_id=profile_id, recent=[], scored=0, remote=None)
        try:
            remote = client.status()
            if not isinstance(remote, dict) or remote.get('version') != 1:
                raise ValueError('Pi service protocol version 1 is required.')
            saved = self.store.settings().get(key)
            previous = saved.get('cursor') if isinstance(saved, dict) else saved
            tail = remote.get('event_cursor')
            if previous is None:
                if tail is None:
                    # Compatibility with an earlier service: drain history without
                    # scoring before accepting new events for this selected profile.
                    for _ in range(1000):
                        if cancel.is_set():
                            return self.info()
                        events = client.events()
                        if not events:
                            break
                    else:
                        raise ValueError('Pi history is too large; update the Pi service to expose event_cursor.')
                else:
                    client.cursor = cursor(tail)
            else:
                client.cursor = cursor(previous)
                if tail is not None and cursor(tail) < client.cursor:
                    client.cursor = cursor(tail)  # Pi database was replaced/reset.
            with self.lock:
                if cancel.is_set() or self.client is not client:
                    return self.info()
                self.startup_id = str(remote.get('startup_id', ''))
                self.audio_cursor = 0
                self.playback_baseline = cursor(tail) if tail is not None else client.cursor
                self.playback_started.clear()
                self._persist_cursor(client.cursor, remote.get('startup_id', ''))
                self.state.update(status='connected · outputs controlled on Pi', connected=True, remote=remote, microphone=False)
                self.worker = threading.Thread(target=self._poll, args=(client, cancel, profile_id, key, url), daemon=True)
                self.worker.start()
        except Exception as error:
            with self.lock:
                if self.client is client and not cancel.is_set():
                    self.state.update(status='connection failed', connected=False, error=str(error)[:200])
                    cancel.set()
            raise
        return self.info()

    def _persist_cursor(self, value, startup_id):
        self.store.setting(self.cursor_key, {'cursor': cursor(value), 'startup_id': str(startup_id)[:100]})

    @staticmethod
    def _score_id(url, profile_id, event_id, prefix):
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
            raise ValueError('Pi events require a stable bounded ID.')
        return prefix + hashlib.sha256((url + '|' + profile_id + '|' + event_id).encode()).hexdigest()

    def _event(self, event, url, profile_id, cancel):
        if not isinstance(event, dict) or not isinstance(event.get('type'), str) or not isinstance(event.get('payload'), dict):
            raise ValueError('Invalid Pi event envelope.')
        kind, payload = event['type'], event['payload']
        with self.lock:
            if cancel.is_set():
                return
            # UI history contains event types and harmless score kinds, never PCM.
            self.state['recent'].append({'type': kind[:80], 'kind': 'fart' if payload.get('kind') == 'fart' else None})
            self.state['recent'] = self.state['recent'][-10:]
            if (kind not in ('audio.playback.started', 'audio.playback') or payload.get('kind') != 'fart'
                    or not self.score_allowed()
                    or cursor(event.get('cursor')) <= self.playback_baseline):
                return
            source = payload.get('source')
            if not isinstance(source, str) or source not in PLAYBACK_SOURCES:
                return
            playback_id = payload.get('playback_id')
            if not isinstance(playback_id, str) or not 1 <= len(playback_id) <= 200:
                return
            details = (payload['kind'], source)
            if kind == 'audio.playback.started':
                self.playback_started.setdefault(playback_id, details)
                if len(self.playback_started) > 64:
                    self.playback_started.popitem(last=False)
                return
            if payload.get('confirmed') is not True or self.playback_started.get(playback_id) != details:
                return
            score_id = self._score_id(url, profile_id, playback_id, 'pi-playback:')
            counted = self.store.event(score_id, payload['kind'], PLAYBACK_SOURCES[source],
                                       profile_id=profile_id, active_only=True)
            self.state['scored'] += int(counted)

    def _poll(self, client, cancel, profile_id, key, url):
        while not cancel.is_set():
            previous_cursor, events_complete = client.cursor, False
            try:
                events = client.events()
                if not isinstance(events, list) or len(events) > 100:
                    raise ValueError('Invalid Pi event batch.')
                for event in events:
                    self._event(event, url, profile_id, cancel)
                with self.lock:
                    if cancel.is_set() or self.client is not client:
                        return
                    self._persist_cursor(client.cursor, self.startup_id)
                    events_complete = True
                    enabled, mic_cancel, after = self.microphone_enabled, self.mic_cancel, self.audio_cursor
                if enabled and not mic_cancel.is_set():
                    result = client.audio_chunks(after=after)
                    self._audio(result, client, cancel, mic_cancel, profile_id, url)
                with self.lock:
                    if not cancel.is_set() and self.client is client:
                        self.state.update(status='connected · outputs controlled on Pi', connected=True, error='')
            except Exception as error:
                if not events_complete:
                    client.cursor = previous_cursor
                with self.lock:
                    if not cancel.is_set() and self.client is client:
                        self.state.update(status='Pi unreachable · retrying', connected=False, error=str(error)[:200])
            cancel.wait(self.POLL_SECONDS)

    def _audio(self, result, client, cancel, mic_cancel, profile_id, url):
        if not isinstance(result, dict) or not isinstance(result.get('chunks'), list) or len(result['chunks']) > 8:
            raise ValueError('Invalid Pi audio response.')
        startup_id = result.get('startup_id')
        with self.lock:
            if cancel.is_set() or mic_cancel.is_set() or self.client is not client:
                return
            if startup_id and self.startup_id and startup_id != self.startup_id:
                # A Pi reboot stops its mic. Never silently restart recording.
                self.mic_cancel.set()
                self.microphone_enabled = False
                self.state.update(microphone=False, microphone_error='Pi restarted; enable its microphone again explicitly.')
                return
        for item in result['chunks']:
            if cancel.is_set() or mic_cancel.is_set():
                return
            if not isinstance(item, dict) or item.get('encoding') != 'pcm_s16le' or item.get('channels') != 1:
                raise ValueError('Pi audio must be mono signed 16-bit little-endian PCM.')
            rate = item.get('rate')
            if isinstance(rate, bool) or not isinstance(rate, int) or not 8000 <= rate <= 96000:
                raise ValueError('Invalid Pi audio sample rate.')
            encoded = item.get('pcm')
            if not isinstance(encoded, str) or len(encoded) > 2_560_000:
                raise ValueError('Pi audio chunk is too large.')
            pcm = base64.b64decode(encoded, validate=True)
            item_cursor = cursor(item.get('cursor'))
            with self.lock:
                if cancel.is_set() or mic_cancel.is_set() or item_cursor <= self.audio_cursor:
                    continue
                runs = self.audio_runs
            event_id = self._score_id(url, profile_id, item.get('id'), 'pi-microphone:')
            references = []
            effects = item.get('effects', [])
            if not isinstance(effects, list) or len(effects) > 24:
                raise ValueError('Invalid Pi effect references.')
            for effect in effects:
                if not isinstance(effect, dict):
                    raise ValueError('Invalid Pi effect reference.')
                path = effect_path(effect.get('asset'))
                offset, duration = effect.get('offset'), effect.get('duration')
                if (path is None or type(offset) not in (int, float) or type(duration) not in (int, float)
                        or not -11 <= offset <= 11 or not 0 < duration <= 10):
                    raise ValueError('Invalid Pi effect reference.')
                references.append((path, item_cursor + offset, item_cursor + offset + duration))
            self.audio.ingest_pcm(pcm, rate, event_id=event_id, profile_id=profile_id, cancel_event=mic_cancel,
                                  runs=runs, start=item_cursor, references=references, admission_lock=self.lock)
            with self.lock:
                if cancel.is_set() or mic_cancel.is_set():
                    return
                self.audio_cursor = item_cursor
        with self.lock:
            if not cancel.is_set() and not mic_cancel.is_set():
                self.audio_cursor = max(self.audio_cursor, cursor(result.get('cursor', self.audio_cursor)))

    def microphone(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('Choose whether to enable the Pi microphone.')
        with self.lock:
            client, cancel = self.client, self.cancel
            if client is None or cancel.is_set():
                raise ValueError('Connect to the Pi before controlling its microphone.')
            self.mic_cancel.set()
            self.microphone_enabled = False
            self.state['microphone'] = False
            mic_cancel = threading.Event()
            if enabled:
                self.mic_cancel = mic_cancel
        if not enabled:
            def stop_mic():
                try:
                    client.microphone(False)
                except Exception:
                    pass
            threading.Thread(target=stop_mic, daemon=True).start()
            return self.info()
        try:
            baseline = client.audio_chunks(after=0)
            if not isinstance(baseline, dict):
                raise ValueError('Invalid Pi microphone status.')
            baseline_cursor = cursor(baseline.get('cursor', 0))
            result = client.microphone(True)
            if not isinstance(result, dict) or result.get('accepted') is False:
                raise ValueError('Pi did not accept microphone capture.')
        except Exception:
            mic_cancel.set()
            self._async_stop(client)
            raise
        with self.lock:
            if cancel.is_set() or mic_cancel.is_set() or self.client is not client:
                self._async_stop(client)
                return self.info()
            self.audio_cursor = baseline_cursor
            self.audio_runs = SoundRuns()
            self.startup_id = str(baseline.get('startup_id') or self.startup_id)
            self.microphone_enabled = True
            self.state['microphone'] = True
            self.state['microphone_error'] = ''
        return self.info()
