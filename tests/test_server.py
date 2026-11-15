import http.client
import json
import threading

from chuddy.server import DesktopServer
from tests.helpers import ControllerCase


EXTENSION = 'chrome-extension://' + 'a' * 32


class ServerTests(ControllerCase):
    def setUp(self):
        super().setUp()
        (self.path / 'web').mkdir()
        (self.path / 'web' / 'index.html').write_text('<html><head></head><body>Chuddy</body></html>', encoding='utf-8')
        (self.path / 'web' / 'app.js').write_text('console.log("local")', encoding='utf-8')
        (self.path / 'private.txt').write_text('must not leak')
        self.server = DesktopServer(self.controller, self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        super().tearDown()

    def request(self, method, path, payload=None, headers=None, raw=None):
        headers = dict(headers or {})
        if payload is not None:
            raw = json.dumps(payload).encode()
            headers.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_authenticated_state_and_no_unpaired_access(self):
        self.assertEqual(self.request('GET', '/api/state')[0], 401)
        status, headers, body = self.request('GET', '/api/state', headers={'X-Chuddy-Token': self.server.token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['session'], 'stopped')
        self.assertNotIn(self.controller.browser_token, body.decode())
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_earned_clams_fund_casino_with_exact_http_retry(self):
        headers = {'X-Chuddy-Token': self.server.token}
        status, _, body = self.request('POST', '/api/action',
            {'action': 'score', 'kind': 'fart', 'request_id': 'earn'}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['earnings']['balance'], 105)
        payload = {'action': 'casino_start', 'game': 'roulette', 'bet': 5,
                   'selection': {'kind': 'straight', 'value': 0},
                   'profile_id': self.store.profile()['id'], 'request_id': 'table'}
        status, _, body = self.request('POST', '/api/action', payload, headers)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result['round']['complete'])
        self.assertEqual(result['balance'], 100 + result['round']['payout'])
        status, _, replay = self.request('POST', '/api/action', payload, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(replay), result)
        status, _, body = self.request('GET', '/api/state', headers=headers)
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(state['balance'], result['balance'])
        self.assertEqual(state['casino']['round']['id'], result['round']['id'])
        self.assertEqual(state['earnings']['total_earned'], 5)

    def test_host_and_origin_boundaries(self):
        self.assertEqual(self.request('GET', '/', headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Origin': 'https://attacker.example'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'X-Chuddy-Token': self.server.token, 'Origin': EXTENSION})[0], 403)

    def test_extension_token_has_browser_only_scope(self):
        headers = {'X-Chuddy-Token': self.controller.browser_token, 'Origin': EXTENSION}
        self.assertEqual(self.request('GET', '/browser/rules', headers=headers)[0], 200)
        status, response_headers, _ = self.request('POST', '/browser/report', {'active': False, 'focused': False}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers.get('Access-Control-Allow-Origin'), EXTENSION)
        self.assertEqual(self.request('POST', '/api/action', {'action': 'deposit', 'amount': 1, 'request_id': 'x'}, headers)[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'X-Chuddy-Token': self.controller.browser_token})[0], 401)

    def test_preflight_only_exposes_browser_routes(self):
        headers = {'Origin': EXTENSION, 'Access-Control-Request-Headers': 'X-Chuddy-Token, Content-Type'}
        self.assertEqual(self.request('OPTIONS', '/browser/report', headers=headers)[0], 204)
        self.assertEqual(self.request('OPTIONS', '/api/action', headers=headers)[0], 403)
        self.assertEqual(self.request('OPTIONS', '/browser/report', headers={'Origin': 'https://example.com'})[0], 403)

    def test_bootstrap_is_local_and_traversal_cannot_read_private_files(self):
        status, _, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, body.decode())
        for path in ('/web/../private.txt', '/web/%2e%2e/private.txt', '/web/%2e%2e%5cprivate.txt'):
            with self.subTest(path=path):
                status, _, body = self.request('GET', path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'must not leak', body)

    def test_malformed_inputs_and_oversized_bodies_fail_cleanly(self):
        headers = {'X-Chuddy-Token': self.server.token, 'Content-Type': 'application/json'}
        for body in (b'[]', b'{bad}', b'{"action":"deposit","amount":NaN}', b'{"action":"unknown"}'):
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/action', raw=body, headers=headers)[0], 400)
        # The server must reject the declared size before the client sends a body.
        # Queuing the rejected body can reset the Windows connection during close.
        self.assertEqual(self.request('POST', '/api/action',
            headers={**headers, 'Content-Length': '131073'})[0], 413)
        self.assertEqual(self.request('POST', '/api/action', raw=b'{}', headers={**headers, 'Content-Type': 'text/plain'})[0], 415)

    def test_non_ascii_auth_token_returns_unauthorized_without_disconnect(self):
        self.assertEqual(self.request('POST', '/api/action', {'action': 'session', 'operation': 'stop'}, {'X-Chuddy-Token': 'é'})[0], 401)

    def test_deposit_request_retry_is_idempotent_over_http(self):
        headers = {'X-Chuddy-Token': self.server.token}
        payload = {'action': 'deposit', 'amount': 10, 'request_id': 'http-retry'}
        first = self.request('POST', '/api/action', payload, headers)
        second = self.request('POST', '/api/action', payload, headers)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[2], second[2])
        self.assertEqual(self.store.balance(), 110)
