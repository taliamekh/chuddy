"""Ephemeral paired-extension commands. No browser or native input is used here."""
from concurrent.futures import Future
import time
import uuid


class BrowserTabCommands:
    def __init__(self, lock, *, wall_clock=time.time):
        self.lock, self.wall_clock = lock, wall_clock
        self.pending = None
        self.acknowledged = None

    def request(self, target, valid, live):
        with self.lock:
            self.cancel()
            if (not target or isinstance(target.get('tab_id'), bool)
                    or not isinstance(target.get('tab_id'), int) or not target.get('client_id')):
                raise RuntimeError('Pair and enable the updated browser extension to close only the work tab.')
            future = Future()
            self.pending = {'command': {'id': str(uuid.uuid4()), 'action': 'close_tab',
                'tab_id': target['tab_id'], 'url': target['url'], 'window_id': target.get('window_id'),
                'address_id': target.get('address_id'),
                'client_id': target['client_id'], 'expires_at': (self.wall_clock() + 4) * 1000},
                'valid': valid, 'live': live, 'future': future, 'claimed': False}
            return future

    def cancel(self):
        with self.lock:
            pending, self.pending = self.pending, None
            self.acknowledged = None
            if pending and not pending['future'].done():
                pending['future'].set_result(False)

    def offer(self, client_id):
        with self.lock:
            pending = self.pending
            if not pending:
                return None
            deadline = pending.get('confirm_by', pending['command']['expires_at'])
            if not pending['live']() or self.wall_clock()*1000 >= deadline:
                self.cancel()
                return None
            if client_id != pending['command']['client_id'] or pending['claimed']:
                return None
            if not pending['valid']():
                self.cancel()
                return None
            return dict(pending['command'])

    def claim(self, command_id, client_id):
        with self.lock:
            command = self.offer(client_id)
            if not command or command_id != command['id']:
                return {'command': None}
            self.pending['claimed'] = True
            self.pending['confirm_by'] = (self.wall_clock() + 4.25) * 1000
            # Permission is short lived; the extension rechecks focus and URL
            # again immediately before the tabs.remove operation.
            command['expires_at'] = (self.wall_clock() + .75) * 1000
            return {'command': {**command, 'execute': True}}

    def complete(self, command_id, client_id, closed):
        with self.lock:
            pending = self.pending
            if (not pending or command_id != pending['command']['id']
                    or client_id != pending['command']['client_id']):
                return {'accepted': self.acknowledged == (command_id, client_id, closed)}
            if not isinstance(closed, bool) or (closed and not pending['claimed']):
                raise ValueError('Invalid browser command acknowledgement.')
            self.pending = None
            deadline = pending.get('confirm_by', pending['command']['expires_at'])
            accepted = bool(pending['live']() and self.wall_clock()*1000 < deadline)
            if accepted:
                self.acknowledged = (command_id, client_id, closed)
            if not pending['future'].done():
                if closed and accepted:
                    pending['future'].set_result(True)
                elif accepted:
                    pending['future'].set_exception(RuntimeError('The browser did not confirm that the selected tab could be closed.'))
                else:
                    pending['future'].set_result(False)
            return {'accepted': accepted}
