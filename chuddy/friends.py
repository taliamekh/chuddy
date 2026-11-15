"""Explicit, retry-safe sharing to a service selected by the user."""
import ipaddress
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an invitation token to a redirect target.


def service_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError('Enter a friends service address.')
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use a service HTTP(S) address without credentials, query or fragment.')
    if parsed.scheme == 'http':
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1') or parsed.hostname.endswith('.local')
        try:
            local = local or ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            pass
        if not local:
            raise ValueError('Use HTTPS for a service outside your local network.')
    return value.rstrip('/')


class FriendsClient:
    def __init__(self, store):
        self.store = store
        self.last = {'status': 'not connected', 'leaderboard': [], 'synced': 0}

    def sync(self, base_url, room, token):
        base = service_url(base_url)
        if not isinstance(room, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', room):
            raise ValueError('Room names use letters, numbers, hyphens or underscores.')
        if not isinstance(token, str) or not 16 <= len(token) <= 256:
            raise ValueError('Enter the invitation token from your friends service.')
        snapshot = self.store.events_for_sync()
        events = [event for event in snapshot.pop('events') if event['kind'] == 'fart']
        inserted = 0
        response = None
        # Re-sending all immutable events makes recovery stateless. The service
        # deduplicates durable identities, including after either side restarts.
        for offset in range(0, max(1, len(events)), 100):
            data = {**snapshot, 'events': events[offset:offset + 100]}
            request = urllib.request.Request(base + '/v1/rooms/' + room + '/sync', json.dumps(data, allow_nan=False).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
            try:
                with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as result:
                    response = json.loads(result.read(512 * 1024))
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    raise ValueError('The room or invitation token was not accepted.') from error
                raise ValueError('Friends service rejected this sync. Local scores are safe.') from error
            except (OSError, ValueError) as error:
                raise ValueError('Friends service is unavailable. Local scores are safe; retry later.') from error
            if not isinstance(response, dict) or not isinstance(response.get('leaderboard'), list) or not isinstance(response.get('inserted'), int):
                raise ValueError('Friends service returned an unsupported response.')
            inserted += response['inserted']
        self.last = {'status': 'connected · private friends room', 'leaderboard': response['leaderboard'],
                     'leaderboard_periods': response.get('leaderboard_periods', {}), 'synced': inserted, 'room': room, 'profile_id': snapshot['profile']['id']}
        return dict(self.last)
