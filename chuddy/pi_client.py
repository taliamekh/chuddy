"""Explicit authenticated PC→Pi commands, with caller-controlled event polling."""
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
import ipaddress
import socket
import base64
from pathlib import Path
from datetime import datetime, timezone


class PiClient:
    def __init__(self, url, token, timeout=4):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('Pi URL must be a local HTTP(S) base address without credentials or a path.')
        try:
            addresses = [ipaddress.ip_address(parsed.hostname)]
        except ValueError:
            if parsed.hostname != 'localhost' and not parsed.hostname.endswith('.local'):
                raise ValueError('Use a LAN IP, localhost or a .local hostname.')
            addresses = [ipaddress.ip_address(entry[4][0]) for entry in socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)]
        if not addresses or any(not (a.is_private or a.is_loopback or a.is_link_local) or a.is_multicast or a.is_unspecified for a in addresses):
            raise ValueError('Pi address must resolve only to local network addresses.')
        self.url = url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.cursor = 0
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, path, payload=None):
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        request = urllib.request.Request(self.url + path, data, {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                reason = json.load(error).get('error', str(error))
            except Exception:
                reason = str(error)
            raise ValueError('Pi: ' + reason) from error

    def status(self):
        return self._request('/v1/status')

    def command(self, kind, payload=None, request_id=None):
        return self._request('/v1/commands', {'version': 1, 'id': request_id or str(uuid.uuid4()), 'type': kind,
                            'time': datetime.now(timezone.utc).isoformat(), 'payload': payload or {}})

    def belly(self, request_id=None):
        return self.command('belly.perform', request_id=request_id)

    def stop(self):
        return self.command('outputs.stop')

    def events(self):
        result = self._request('/v1/events?after=' + str(self.cursor))
        self.cursor = result['cursor']
        return result['events']

    def arm(self, lease_seconds=10, spray=False):
        return self.command('outputs.arm', {'lease_seconds': lease_seconds, 'spray': spray})

    def renew(self, lease_seconds=10):
        return self.command('outputs.renew', {'lease_seconds': lease_seconds})

    def microphone(self, enabled):
        return self.command('mic.start' if enabled else 'mic.stop')

    def audio_chunks(self, after=0):
        return self._request('/v1/audio?after=' + str(int(after)))

    def play_wav(self, path, request_id=None):
        path = Path(path)
        if path.stat().st_size > 1500000:
            raise ValueError('Pi voice WAV must be at most 1.5 MB.')
        return self._request('/v1/playback', {'version': 1, 'id': request_id or str(uuid.uuid4()), 'type': 'audio.wav',
            'time': datetime.now(timezone.utc).isoformat(), 'payload': {'wav': base64.b64encode(path.read_bytes()).decode('ascii')}})
