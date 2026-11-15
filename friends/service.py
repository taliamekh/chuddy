"""Run with python -m friends.service. Nothing binds publicly by default."""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from chuddy.leaderboard import period_bounds, TIMEZONE


MAX_BODY = 128 * 1024


def text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'Invalid {label}.')
    return value.strip()


def room_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('Room names use 1–64 letters, numbers, hyphens or underscores.')
    return value


def identity(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError('Invalid device/profile identity.') from error


class FriendsStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rooms(name TEXT PRIMARY KEY, token_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members(room TEXT, installation_id TEXT, profile_id TEXT, name TEXT,
                    PRIMARY KEY(room,installation_id,profile_id));
                CREATE TABLE IF NOT EXISTS events(room TEXT, installation_id TEXT, profile_id TEXT,
                    id TEXT, kind TEXT, source TEXT, confidence REAL, time TEXT,
                    PRIMARY KEY(room,installation_id,profile_id,id));
            ''')

    def close(self):
        with self.lock:
            self.db.close()

    def create_room(self, name):
        name = room_name(name)
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            try:
                self.db.execute('INSERT INTO rooms VALUES(?,?)', (name, hashlib.sha256(token.encode()).hexdigest()))
            except sqlite3.IntegrityError as error:
                raise ValueError('That room already exists. Its invitation token was not changed.') from error
        return token

    def authorized(self, room, token):
        if not isinstance(token, str) or len(token) > 256:
            return False
        with self.lock:
            row = self.db.execute('SELECT token_hash FROM rooms WHERE name=?', (room,)).fetchone()
            # Compare even for unknown rooms; never reveal whether a private room exists.
            actual = hashlib.sha256(token.encode()).hexdigest()
            return hmac.compare_digest(row[0] if row else '0' * 64, actual) and row is not None

    def sync(self, room, payload):
        room = room_name(room)
        if not isinstance(payload, dict) or not isinstance(payload.get('profile'), dict):
            raise ValueError('Sync requires a profile and event list.')
        installation = identity(payload.get('installation_id'))
        profile = identity(payload['profile'].get('id'))
        name = text(payload['profile'].get('name'), 'pseudonym', 50)
        events = payload.get('events')
        if not isinstance(events, list) or len(events) > 100:
            raise ValueError('Sync supports at most 100 events per request.')
        prepared = []
        for event in events:
            if not isinstance(event, dict) or event.get('kind') not in ('burp', 'fart'):
                raise ValueError('Unsupported score event.')
            event_id = text(event.get('id'), 'event ID', 160)
            source = text(event.get('source'), 'event source', 160)
            confidence = event.get('confidence')
            if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
                raise ValueError('Invalid score confidence.')
            stamp = text(event.get('time'), 'event time', 80)
            try:
                parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except ValueError as error:
                raise ValueError('Invalid score timestamp.') from error
            if parsed.tzinfo is None:
                raise ValueError('Score timestamps need a time zone.')
            prepared.append((room, installation, profile, event_id, event['kind'], source, confidence, parsed.astimezone(timezone.utc).isoformat()))
        inserted = 0
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM rooms WHERE name=?', (room,)).fetchone():
                raise ValueError('Room unavailable.')
            self.db.execute('INSERT INTO members VALUES(?,?,?,?) ON CONFLICT(room,installation_id,profile_id) DO UPDATE SET name=excluded.name', (room, installation, profile, name))
            for record in prepared:
                previous = self.db.execute('SELECT kind,source,confidence,time FROM events WHERE room=? AND installation_id=? AND profile_id=? AND id=?', record[:4]).fetchone()
                if previous and tuple(previous) != record[4:]:
                    raise ValueError('An immutable event ID was reused with different data.')
                if record[4] != 'fart':
                    continue  # Old clients can replay history; only new fart records are admitted.
                inserted += self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)', record).rowcount
            at = datetime.now(timezone.utc)
            return {'inserted': inserted, 'leaderboard': self.leaderboard(room, at=at),
                    'leaderboard_periods': {**{period: self.leaderboard(room, period, at) for period in ('today', 'week', 'all_time')}, 'timezone': TIMEZONE},
                    'scope': 'private friends room', 'self_reported': True}

    def leaderboard(self, room, period='all_time', at=None):
        start, end = period_bounds(period, at)
        with self.lock:
            return [dict(row) for row in self.db.execute('''SELECT m.installation_id || ':' || m.profile_id id,
                m.name,COALESCE(SUM(e.kind='burp'),0) burp,COALESCE(SUM(e.kind='fart'),0) fart,
                COUNT(e.id) total FROM members m LEFT JOIN events e ON m.room=e.room
                AND m.installation_id=e.installation_id AND m.profile_id=e.profile_id
                AND (? IS NULL OR julianday(e.time)>=julianday(?)) AND julianday(e.time)<=julianday(?)
                WHERE m.room=? GROUP BY m.room,m.installation_id,m.profile_id
                ORDER BY fart DESC,m.name COLLATE NOCASE,id''', (start, start, end, room))]


def make_server(host, port, store):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ChuddyFriends/1'

        def log_message(self, format, *args):
            pass  # Tokens and room names never appear in access logs.

        def send_json(self, status, payload):
            data = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/health':
                return self.send_json(200, {'ok': True, 'service': 'Chuddy private friends leaderboard', 'version': 1})
            self.send_json(404, {'error': 'Not found.'})

        def do_POST(self):
            self.connection.settimeout(10)
            match = re.fullmatch(r'/v1/rooms/([A-Za-z0-9_-]{1,64})/sync', urlparse(self.path).path)
            if not match:
                return self.send_json(404, {'error': 'Not found.'})
            auth = self.headers.get('Authorization', '')
            if not auth.startswith('Bearer ') or not store.authorized(match[1], auth[7:]):
                return self.send_json(401, {'error': 'Room or invitation token not accepted.'})
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                return self.send_json(415, {'error': 'Use application/json.'})
            try:
                length = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self.send_json(413, {'error': 'Body must be 1–131072 bytes.'})
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('Incomplete request body.')
                payload = json.loads(body)
                result = store.sync(match[1], payload)
            except (ValueError, UnicodeDecodeError, TypeError) as error:
                return self.send_json(400, {'error': str(error)[:200]})
            except sqlite3.Error:
                return self.send_json(503, {'error': 'Leaderboard storage unavailable; retry later.'})
            self.send_json(200, result)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description='Free private friends leaderboard; no external hosting required.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--db', default=os.environ.get('CHUDDY_FRIENDS_DB', 'data/friends.sqlite3'))
    parser.add_argument('--create-room', metavar='NAME', help='Create a room locally, print its invitation token, then exit.')
    args = parser.parse_args()
    store = FriendsStore(args.db)
    if args.create_room:
        try:
            token = store.create_room(args.create_room)
            print(json.dumps({'room': args.create_room, 'invitation_token': token}))
        finally:
            store.close()
        return
    server = make_server(args.host, args.port, store)
    print(f'Chuddy friends service: http://{args.host}:{server.server_port} (private rooms; not a worldwide deployment)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
