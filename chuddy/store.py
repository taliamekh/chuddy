"""Durable local state. Character jokes never participate in numeric logic."""
import copy
import json
import math
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from .leaderboard import period_bounds, TIMEZONE


def now():
    return datetime.now(timezone.utc).isoformat()


def whole(value, minimum=0, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value) or int(value) != value:
        raise ValueError(f'Use a whole number from {minimum} to {maximum}.')
    return int(value)


def short_text(value, label, maximum=160):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} must contain 1–{maximum} characters.')
    return value.strip()


DEFAULTS = {
    'name': 'You', 'cooldown': 90, 'voice': False, 'voice_engine': 'auto', 'beer_joke': False,
    'interception': 'cover', 'distraction_destination': 'casino', 'reduced_motion': False,
    'ai_enabled': True, 'ai_work_detection': True, 'ai_endpoint': 'http://127.0.0.1:11434', 'ai_model': 'llama3.1:8b-instruct-q4_K_M',
    'friends_url': '', 'friends_room': 'chuddy-club', 'pi_url': '',
    'audio_threshold': 0.35, 'audio_device': None, 'microphone_mode': 'push_to_talk',
    'rules': [
        {'kind': 'process', 'pattern': 'WINWORD.EXE', 'label': 'Microsoft Word', 'enabled': True},
        {'kind': 'window', 'pattern': '* - Word', 'label': 'Word window', 'enabled': True},
        {'kind': 'window', 'pattern': '* - Google Docs - Google Chrome', 'label': 'Google Docs in Chrome', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://docs.google.com/document/*', 'label': 'Google Docs', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://*.brightspace.com/*', 'label': 'Brightspace', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://brightspace.carleton.ca/*', 'label': 'Carleton Brightspace', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://mail.google.com/*', 'label': 'Gmail', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://outlook.office.com/*', 'label': 'Outlook', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://outlook.live.com/*', 'label': 'Outlook personal', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://*.myworkdayjobs.com/*', 'label': 'Job applications', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://www.linkedin.com/jobs/*', 'label': 'LinkedIn jobs', 'enabled': True},
        {'kind': 'url', 'pattern': 'https://job-boards.greenhouse.io/*', 'label': 'Greenhouse applications', 'enabled': True},
    ]
}

CATALOG = [
    {'id': 'pretend-beer', 'name': 'Pretend beer', 'price': 8, 'description': 'Fictional bubbles. Nothing shipped, nothing drunk.'},
    {'id': 'mystery-snack', 'name': 'Mystery snack', 'price': 5, 'description': 'A pretend snack of uncertain snackness.'},
    {'id': 'tiny-hat', 'name': 'Tiny hat', 'price': 12, 'description': 'An imaginary hat for a very small thought.'},
]

# Play-money policy, kept authoritative and independent of character dialogue.
MAX_BALANCE = 1_000_000_000_000
EARNING_DAILY_CAP = 500
SCORE_RATES = {'fart': 5}
LEVEL_INTERVAL, MAX_LEVEL = 10, 3
MILESTONE_BONUS = 20
GAME_REWARD, GAME_DAILY_LIMIT = 10, 3
GAME_MINIMUM_SECONDS, GAME_EXPIRY_SECONDS = 20, 120
PASSIVE_PER_SECOND = 1
SOUND_MULTIPLIER_CAP, SOUND_BOOST_MS = 5, 1_800_000
SQLITE_MAX_INTEGER = 2**63 - 1
PASSIVE_MAX_GAP_MS = 5_000


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.earning_clock = time.monotonic
        self.earning_runtime = None
        self.passive_enabled = False
        self.passive_work_detected = False
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=15000')
        self._migrate()
        if not self.messages():
            self.message('assistant', "Hey. I'm Chuddy. Your new supportive friend. I can help with… uh… everything probably.", 'offline script')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    def _migrate(self):
        with self.transaction():
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version > 3:
                raise ValueError('This database was created by a newer Chuddy. Keep it intact and use that version.')
            for statement in [
                'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT, text TEXT, source TEXT, time TEXT)',
                'CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT, source TEXT, confidence REAL, time TEXT)',
                'CREATE TABLE IF NOT EXISTS deadlines(id INTEGER PRIMARY KEY, title TEXT, due TEXT)',
                'CREATE TABLE IF NOT EXISTS wallet(id INTEGER PRIMARY KEY CHECK(id=1), balance INTEGER CHECK(balance>=0))',
                'CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, delta INTEGER, reason TEXT, time TEXT)',
                'CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, kind TEXT, detail TEXT, time TEXT)',
                'CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, created TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS profile_wallet(profile_id TEXT PRIMARY KEY, balance INTEGER NOT NULL CHECK(balance>=0 AND balance<=1000000000000))',
                'CREATE TABLE IF NOT EXISTS requests(profile_id TEXT, request_id TEXT, operation TEXT, payload TEXT, result TEXT, PRIMARY KEY(profile_id,request_id))',
                'CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, profile_id TEXT, key TEXT, value TEXT, created TEXT, updated TEXT, UNIQUE(profile_id,key))',
                '''CREATE TABLE IF NOT EXISTS earnings(profile_id TEXT, reward_key TEXT, category TEXT,
                    kind TEXT, source TEXT, amount INTEGER NOT NULL CHECK(amount>=0), nominal INTEGER,
                    multiplier INTEGER, bonus INTEGER, time TEXT, detail TEXT,
                    PRIMARY KEY(profile_id,reward_key))''',
                '''CREATE TABLE IF NOT EXISTS clam_rounds(id TEXT PRIMARY KEY, profile_id TEXT,
                    started_at TEXT, expires_at TEXT, status TEXT, completed_at TEXT, result TEXT)''',
                '''CREATE TABLE IF NOT EXISTS passive_fraction(profile_id TEXT PRIMARY KEY,
                    units INTEGER NOT NULL CHECK(units>=0 AND units<1000))''',
                '''CREATE TABLE IF NOT EXISTS passive_session(id INTEGER PRIMARY KEY CHECK(id=1),
                    runtime TEXT, profile_id TEXT, checkpoint INTEGER, multiplier INTEGER,
                    boost_until INTEGER, sequence INTEGER)''',
            ]:
                self.db.execute(statement)
            if not self.db.execute('SELECT 1 FROM profiles LIMIT 1').fetchone():
                old_name = self.db.execute("SELECT value FROM settings WHERE key='name'").fetchone()
                name = json.loads(old_name[0]) if old_name else 'You'
                if not isinstance(name, str) or not name.strip():
                    name = 'You'
                initial_id = str(uuid.uuid4())
                self.db.execute('INSERT INTO profiles VALUES(?,?,?)', (initial_id, name[:50], now()))
                old_wallet = self.db.execute('SELECT balance FROM wallet WHERE id=1').fetchone()
                self.db.execute('INSERT INTO profile_wallet VALUES(?,?)', (initial_id, old_wallet[0] if old_wallet else 100))
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('selected_profile_id', json.dumps(initial_id)))
            else:
                initial_id = self.db.execute('SELECT id FROM profiles ORDER BY created,id LIMIT 1').fetchone()[0]
            for table in ('messages', 'events', 'deadlines', 'ledger'):
                columns = {row['name'] for row in self.db.execute(f'PRAGMA table_info({table})')}
                if 'profile_id' not in columns:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN profile_id TEXT')
                self.db.execute(f'UPDATE {table} SET profile_id=? WHERE profile_id IS NULL', (initial_id,))
                self.db.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_profile ON {table}(profile_id)')
            if not self.db.execute("SELECT 1 FROM settings WHERE key='installation_id'").fetchone():
                self.db.execute('INSERT INTO settings VALUES(?,?)', ('installation_id', json.dumps(str(uuid.uuid4()))))
            self.db.execute('CREATE INDEX IF NOT EXISTS idx_earnings_profile_time ON earnings(profile_id,time)')
            self.db.execute('CREATE INDEX IF NOT EXISTS idx_clam_rounds_profile_status ON clam_rounds(profile_id,status)')
            # Retire the removed clam-catch destination, preserving other choices.
            self.db.execute("UPDATE settings SET value=? WHERE key='distraction_destination' AND value=?", (json.dumps('casino'), json.dumps('game')))
            # Existing score events deliberately receive no retroactive rewards.
            self.db.execute('PRAGMA user_version=3')

    def _profile_id(self, profile_id=None):
        if profile_id is None:
            row = self.db.execute("SELECT value FROM settings WHERE key='selected_profile_id'").fetchone()
            profile_id = json.loads(row[0]) if row else None
        if not self.db.execute('SELECT 1 FROM profiles WHERE id=?', (profile_id,)).fetchone():
            raise ValueError('Unknown local profile.')
        return profile_id

    def close(self):
        with self.lock:
            self.db.close()

    def settings(self):
        with self.lock:
            values = {r['key']: json.loads(r['value']) for r in self.db.execute('SELECT * FROM settings')}
            result = {**copy.deepcopy(DEFAULTS), **values}
            result['name'] = self.profile()['name']
            return result

    def setting(self, key, value):
        self.update_settings({key: value})

    def update_settings(self, values):
        if not isinstance(values, dict):
            raise ValueError('Settings must be an object.')
        encoded = [(short_text(k, 'Setting key', 80), json.dumps(v, allow_nan=False)) for k, v in values.items()]
        if 'name' in values:
            name = short_text(values['name'], 'Pseudonym', 50)
        with self.transaction():
            if 'name' in values:
                try:
                    self.db.execute('UPDATE profiles SET name=? WHERE id=?', (name, self._profile_id()))
                except sqlite3.IntegrityError as error:
                    raise ValueError('A profile already uses that pseudonym.') from error
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)', encoded)
        return self.settings()

    def profiles(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM profiles ORDER BY created,id')]

    def profile(self):
        with self.lock:
            return dict(self.db.execute('SELECT * FROM profiles WHERE id=?', (self._profile_id(),)).fetchone())

    def add_profile(self, name):
        name = short_text(name, 'Pseudonym', 50)
        result = {'id': str(uuid.uuid4()), 'name': name, 'created': now()}
        with self.transaction():
            try:
                self.db.execute('INSERT INTO profiles VALUES(:id,:name,:created)', result)
            except sqlite3.IntegrityError as error:
                raise ValueError('A profile already uses that pseudonym.') from error
            self.db.execute('INSERT INTO profile_wallet VALUES(?,100)', (result['id'],))
        return result

    def select_profile(self, profile_id):
        with self.transaction():
            profile_id = self._profile_id(profile_id)
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('selected_profile_id', json.dumps(profile_id)))
        return self.profile()

    def message(self, role, text, source, profile_id=None):
        if role not in ('user', 'assistant', 'system'):
            raise ValueError('Unknown message role.')
        text = short_text(text, 'Message', 10000)
        source = short_text(source, 'Message source', 160)
        with self.transaction():
            cursor = self.db.execute('INSERT INTO messages(role,text,source,time,profile_id) VALUES(?,?,?,?,?)', (role, text, source, now(), self._profile_id(profile_id)))
            return cursor.lastrowid

    def messages(self, profile_id=None):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM (SELECT * FROM messages WHERE profile_id=? ORDER BY id DESC LIMIT 80) ORDER BY id', (self._profile_id(profile_id),))]

    def event(self, event_id, kind, source, confidence=None, profile_id=None, *, active_only=False):
        event_id = short_text(event_id, 'Event ID', 160)
        source = short_text(source, 'Event source', 160)
        if kind != 'fart':
            raise ValueError('Only farts can be scored.')
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
            raise ValueError('Confidence must be between zero and one.')
        with self.transaction():
            profile_id = self._profile_id(profile_id)
            if active_only and profile_id != self._profile_id():
                return False
            previous = self.db.execute('SELECT kind,source,confidence,profile_id FROM events WHERE id=?', (event_id,)).fetchone()
            if previous and tuple(previous) != (kind, source, confidence, profile_id):
                raise ValueError('That event ID was already used for different score data.')
            timestamp = now()
            inserted = bool(self.db.execute('INSERT OR IGNORE INTO events(id,kind,source,confidence,time,profile_id) VALUES(?,?,?,?,?,?)', (event_id, kind, source, confidence, timestamp, profile_id)).rowcount)
            if inserted:
                # Settle old-rate time before applying a new, deduplicated boost.
                self._passive_tick()
                if self.passive_enabled:
                    self.db.execute('''UPDATE passive_session SET multiplier=MIN(?,multiplier+1),
                        boost_until=MIN(?,MAX(checkpoint,boost_until))+? WHERE runtime=? AND profile_id=?''',
                        (SOUND_MULTIPLIER_CAP, SQLITE_MAX_INTEGER - SOUND_BOOST_MS,
                         SOUND_BOOST_MS, self.earning_runtime, profile_id))
                count = self.db.execute("SELECT COUNT(*) FROM earnings WHERE profile_id=? AND category='score'", (profile_id,)).fetchone()[0]
                multiplier = min(MAX_LEVEL, 1 + count // LEVEL_INTERVAL)
                bonus = MILESTONE_BONUS if (count + 1) % LEVEL_INTERVAL == 0 else 0
                self._earn(profile_id, 'score:' + event_id, 'score', kind, source,
                           SCORE_RATES[kind] * multiplier + bonus, multiplier, bonus, timestamp,
                           {'event_id': event_id, 'base': SCORE_RATES[kind], 'sequence': count + 1})
            return inserted

    def scores(self, profile_id=None):
        with self.lock:
            profile_id = self._profile_id(profile_id)
            counts = {'burp': 0, 'fart': 0}
            counts.update({r['kind']: r['n'] for r in self.db.execute('SELECT kind,COUNT(*) n FROM events WHERE profile_id=? GROUP BY kind', (profile_id,))})
            recent = 'SELECT * FROM events WHERE profile_id=? {} ORDER BY time DESC,id DESC LIMIT 12'
            return {**counts, 'total': sum(counts.values()),
                    'recent': [dict(r) for r in self.db.execute(recent.format(''), (profile_id,))],
                    'recent_farts': [dict(r) for r in self.db.execute(recent.format("AND kind='fart'"), (profile_id,))]}

    def leaderboard(self, period='all_time', at=None):
        start, end = period_bounds(period, at)
        with self.lock:
            return [dict(r) for r in self.db.execute('''SELECT p.id,p.name,COALESCE(SUM(e.kind='burp'),0) burp,
                COALESCE(SUM(e.kind='fart'),0) fart,COUNT(e.id) total FROM profiles p
                LEFT JOIN events e ON p.id=e.profile_id
                AND (? IS NULL OR julianday(e.time)>=julianday(?))
                AND julianday(e.time)<=julianday(?)
                GROUP BY p.id ORDER BY fart DESC,p.name COLLATE NOCASE,p.id''', (start, start, end))]

    def leaderboard_periods(self, at=None):
        at = at or datetime.now(timezone.utc)
        with self.lock:
            return {**{period: self.leaderboard(period, at) for period in ('today', 'week', 'all_time')}, 'timezone': TIMEZONE}

    def admin_money(self, command, request_id, profile_id):
        import re
        if not isinstance(command, str) or not re.fullmatch(r'monies [1-9][0-9]{0,12}', command):
            raise ValueError('Use monies followed by a positive whole amount.')
        amount = whole(int(command.split()[1]), 1, MAX_BALANCE)
        # Explicit identity is part of the retry key; never credit a new profile.
        return self._wallet_request('admin_money', {'amount': amount}, request_id,
                                    lambda pid: {'balance': self._transact(pid, amount, 'admin monies cheat'), 'amount': amount}, profile_id)

    def events_for_sync(self):
        with self.lock:
            # Only pseudonym, durable identity and score provenance leave the device.
            profile = self.profile()
            events = [dict(r) for r in self.db.execute('SELECT id,kind,source,confidence,time FROM events WHERE profile_id=? ORDER BY time,id', (profile['id'],))]
            return {'installation_id': self.settings()['installation_id'], 'profile': {'id': profile['id'], 'name': profile['name']}, 'events': events}

    def balance(self, profile_id=None):
        with self.lock:
            return self.db.execute('SELECT balance FROM profile_wallet WHERE profile_id=?', (self._profile_id(profile_id),)).fetchone()[0]

    def _transact(self, profile_id, delta, reason):
        balance = self.balance(profile_id)
        if not 0 <= balance + delta <= MAX_BALANCE:
            raise ValueError('Not enough pretend monies, or the pretend wallet is full.')
        self.db.execute('UPDATE profile_wallet SET balance=balance+? WHERE profile_id=?', (delta, profile_id))
        self.db.execute('INSERT INTO ledger(delta,reason,time,profile_id) VALUES(?,?,?,?)', (delta, reason, now(), profile_id))
        return balance + delta

    def transact(self, delta, reason):
        if isinstance(delta, bool) or not isinstance(delta, int) or abs(delta) > 1_000_000_000:
            raise ValueError('Wallet changes require a bounded whole number.')
        reason = short_text(reason, 'Ledger reason', 160)
        with self.transaction():
            return self._transact(self._profile_id(), delta, reason)

    def _wallet_request(self, operation, payload, request_id, effect, expected_profile=None):
        request_id = short_text(request_id, 'Request ID', 160)
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self.transaction():
            profile_id = self._profile_id()
            if expected_profile is not None and profile_id != expected_profile:
                raise ValueError('The local profile changed. Return to the original profile to retry.')
            previous = self.db.execute('SELECT * FROM requests WHERE profile_id=? AND request_id=?', (profile_id, request_id)).fetchone()
            if previous:
                if previous['operation'] != operation or previous['payload'] != encoded:
                    raise ValueError('That request ID was already used for a different operation.')
                return json.loads(previous['result'])
            result = {**effect(profile_id), 'request_id': request_id}
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', (profile_id, request_id, operation, encoded, json.dumps(result)))
            return result

    def _earn(self, profile_id, reward_key, category, kind, source, nominal, multiplier, bonus, timestamp, detail):
        """Call only inside the same transaction as the score/completion insert."""
        daily = self.db.execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE profile_id=? AND category!='passive' AND substr(time,1,10)=?", (profile_id, timestamp[:10])).fetchone()[0]
        room = MAX_BALANCE - self.balance(profile_id)
        amount = min(nominal, max(0, EARNING_DAILY_CAP - daily), room)
        balance = self._transact(profile_id, amount, ('chudding reward: ' + category + '/' + kind + ' · ' + source)[:160])
        self.db.execute('INSERT INTO earnings VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (profile_id, reward_key, category, kind, source, amount, nominal, multiplier, bonus, timestamp, json.dumps(detail, allow_nan=False)))
        return {'amount': amount, 'nominal': nominal, 'multiplier': multiplier, 'bonus': bonus,
                'balance': balance, 'capped': amount < nominal,
                'daily_cap': EARNING_DAILY_CAP, 'daily_remaining': max(0, EARNING_DAILY_CAP - daily - amount)}

    def earnings(self, profile_id=None):
        with self.lock:
            profile_id = self._profile_id(profile_id)
            totals = self.db.execute('''SELECT COALESCE(SUM(amount),0) total,
                COALESCE(SUM(CASE WHEN category='score' THEN amount ELSE 0 END),0) score,
                COALESCE(SUM(CASE WHEN category='game' THEN amount ELSE 0 END),0) game,
                COALESCE(SUM(category='score'),0) events FROM earnings WHERE profile_id=?''', (profile_id,)).fetchone()
            day = now()[:10]
            daily = self.db.execute('''SELECT COALESCE(SUM(CASE WHEN category!='passive' THEN amount ELSE 0 END),0) earned,
                COALESCE(SUM(category='game'),0) games FROM earnings WHERE profile_id=? AND substr(time,1,10)=?''', (profile_id, day)).fetchone()
            count = totals['events']
            level = min(MAX_LEVEL, 1 + count // LEVEL_INTERVAL)
            recent = []
            for row in self.db.execute('SELECT * FROM earnings WHERE profile_id=? ORDER BY time DESC,rowid DESC LIMIT 12', (profile_id,)):
                item = dict(row)
                item['detail'] = json.loads(item['detail'])
                recent.append(item)
            return {'profile_id': profile_id, 'balance': self.balance(profile_id), 'total_earned': totals['total'],
                    'score_earned': totals['score'], 'game_earned': totals['game'], 'rewarded_events': count,
                    'level': level, 'multiplier': level, 'level_cap': MAX_LEVEL,
                    'next_level_at': level * LEVEL_INTERVAL if level < MAX_LEVEL else None,
                    'daily_earned': daily['earned'], 'daily_cap': EARNING_DAILY_CAP,
                    'daily_remaining': max(0, EARNING_DAILY_CAP - daily['earned']), 'day': day, 'day_timezone': 'UTC',
                    'game_rounds_today': daily['games'], 'game_rounds_remaining': max(0, GAME_DAILY_LIMIT - daily['games']),
                    'rates': {**SCORE_RATES, 'game': GAME_REWARD, 'milestone_bonus': MILESTONE_BONUS,
                              'milestone_every': LEVEL_INTERVAL, 'game_daily_limit': GAME_DAILY_LIMIT,
                              'game_minimum_seconds': GAME_MINIMUM_SECONDS, 'game_expiry_seconds': GAME_EXPIRY_SECONDS},
                    'passive': self.passive_info(), 'recent': recent}

    def bind_passive_runtime(self, clock):
        """Startup never resumes an old monotonic checkpoint or mints offline money."""
        with self.transaction():
            self.earning_clock, self.earning_runtime = clock, str(uuid.uuid4())
            self.passive_enabled = False
            self.passive_work_detected = False
            self.db.execute('DELETE FROM passive_session')

    def passive_start(self):
        with self.transaction():
            row = self.db.execute('SELECT runtime FROM passive_session WHERE id=1').fetchone()
            if row and row['runtime'] == self.earning_runtime and self.passive_enabled:
                return  # repeated Start is not a boost reset or a second timer
            self.earning_runtime = str(uuid.uuid4())  # unique ledger namespace for each run
            self.passive_work_detected = False
            self.db.execute('INSERT OR REPLACE INTO passive_session VALUES(1,?,?,?,?,?,?)',
                            (self.earning_runtime, self._profile_id(), int(self.earning_clock()*1000), 1, 0, 0))
        self.passive_enabled = True

    def passive_stop(self, at_ms=None):
        try:
            with self.transaction():
                self._passive_tick(end_ms=at_ms, final=True)
                self.db.execute('DELETE FROM passive_session WHERE runtime=?', (self.earning_runtime,))
        finally:
            self.passive_enabled = False

    def passive_tick(self):
        with self.transaction():
            self._passive_tick()

    def set_passive_work_detected(self, detected):
        with self.lock:
            if not self.passive_enabled or self.passive_work_detected == detected:
                return
            try:
                with self.transaction():
                    self._passive_tick()
            finally:
                # Fail closed on a write error. A failed resume can retry on the
                # next observation without paying for the intervening work time.
                if detected:
                    self.passive_work_detected = True
            self.passive_work_detected = detected

    def _passive_tick(self, end_ms=None, final=False):
        """Store lock + transaction serialize worker, score callbacks and lifecycle.

        Integer millis and fractional carry make polling cadence irrelevant. A
        missed heartbeat >5s is discarded (sleep/stall); never catch up offline.
        Checkpoint, fraction, ledger and wallet commit or roll back together.
        """
        if not self.passive_enabled and not final:
            return
        row = self.db.execute('SELECT * FROM passive_session WHERE id=1 AND runtime=?',
                              (self.earning_runtime,)).fetchone()
        if not row:
            return
        end = int(self.earning_clock()*1000) if end_ms is None else end_ms
        start = row['checkpoint']
        if end <= start:
            return
        if row['profile_id'] != self._profile_id():
            self.db.execute('DELETE FROM passive_session WHERE id=1')
            self.passive_enabled = False
            return
        if end-start > PASSIVE_MAX_GAP_MS:
            self.db.execute('UPDATE passive_session SET checkpoint=?,multiplier=1,boost_until=0 WHERE id=1', (end,))
            return
        multiplier = row['multiplier'] if end < row['boost_until'] else 1
        if self.passive_work_detected:
            self.db.execute('UPDATE passive_session SET checkpoint=?,multiplier=? WHERE id=1', (end, multiplier))
            return
        boosted = max(0, min(end, row['boost_until'])-start)
        units = (end-start + boosted*(row['multiplier']-1))*PASSIVE_PER_SECOND
        pid = row['profile_id']
        fraction = self.db.execute('SELECT units FROM passive_fraction WHERE profile_id=?', (pid,)).fetchone()
        amount, remainder = divmod(units+(fraction[0] if fraction else 0), 1000)
        room = MAX_BALANCE-self.balance(pid)
        credited = min(amount, room)
        if amount >= room:
            remainder = 0  # full-wallet time is not banked for later spending
        self.db.execute('INSERT OR REPLACE INTO passive_fraction VALUES(?,?)', (pid, remainder))
        sequence = row['sequence']+1
        if credited:
            self._transact(pid, credited, 'chudding: passive monies')
            self.db.execute('INSERT INTO earnings VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (pid, f"passive:{self.earning_runtime}:{sequence}", 'passive', 'chudding',
                 'running session', credited, amount, row['multiplier'], 0, now(),
                 json.dumps({'elapsed_ms': end-start, 'boosted_ms': boosted})))
        self.db.execute('UPDATE passive_session SET checkpoint=?,multiplier=?,sequence=? WHERE id=1', (end, multiplier, sequence))

    def passive_info(self):
        with self.lock:
            pid = self._profile_id()
            row = self.db.execute('SELECT * FROM passive_session WHERE id=1 AND runtime=? AND profile_id=?',
                                  (self.earning_runtime, pid)).fetchone()
            if not self.passive_enabled:
                row = None
            remaining = max(0, row['boost_until']-int(self.earning_clock()*1000)) if row else 0
            multiplier = row['multiplier'] if remaining else 1
            full = self.balance(pid) >= MAX_BALANCE
            working = bool(row and self.passive_work_detected)
            return {'status': 'wallet_full' if full else 'work_detected' if working else 'earning' if row else 'stopped',
                    'base_per_second': PASSIVE_PER_SECOND,
                    'rate_per_second': PASSIVE_PER_SECOND*multiplier if row and not (full or working) else 0,
                    'multiplier': multiplier, 'multiplier_cap': SOUND_MULTIPLIER_CAP,
                    'boost_remaining_ms': remaining, 'boost_duration_seconds': SOUND_BOOST_MS//1000,
                    'daily_cap': None, 'currency': 'monies'}

    def start_game(self, request_id, expected_profile=None):
        """Issue a profile-bound ticket. Completing/stopping is a separate action."""
        def effect(profile_id):
            timestamp = now()
            self.db.execute("UPDATE clam_rounds SET status='expired' WHERE profile_id=? AND status='active' AND expires_at<?", (profile_id, timestamp))
            if self.db.execute("SELECT 1 FROM clam_rounds WHERE profile_id=? AND status='active'", (profile_id,)).fetchone():
                raise ValueError('Finish or cancel the active clam game before starting another.')
            round_id = str(uuid.uuid4())
            expires_at = (datetime.fromisoformat(timestamp) + timedelta(seconds=GAME_EXPIRY_SECONDS)).isoformat()
            self.db.execute('INSERT INTO clam_rounds VALUES(?,?,?,?,?,?,?)', (round_id, profile_id, timestamp, expires_at, 'active', None, None))
            return {'round_id': round_id, 'profile_id': profile_id, 'started_at': timestamp, 'expires_at': expires_at,
                    'minimum_seconds': GAME_MINIMUM_SECONDS, 'minimum_catches': 3, 'maximum_catches': 2000,
                    'reward': GAME_REWARD, 'status': 'active'}
        result = self._wallet_request('clam_game_start', {}, request_id, effect, expected_profile)
        with self.lock:
            status = self.db.execute('SELECT status FROM clam_rounds WHERE id=?', (result['round_id'],)).fetchone()[0]
            return {**result, 'status': status}

    def reward_game(self, round_id, request_id, caught, expected_profile=None):
        round_id = short_text(round_id, 'Round ID', 160)
        caught = whole(caught, 3, 2000)
        def effect(profile_id):
            row = self.db.execute('SELECT * FROM clam_rounds WHERE id=? AND profile_id=?', (round_id, profile_id)).fetchone()
            if row is None:
                raise ValueError('This clam-game ticket belongs to another profile or does not exist.')
            if row['status'] != 'active':
                raise ValueError('This clam game is already completed, cancelled or expired. Retry a completed request using its original ID.')
            timestamp = now()
            elapsed = (datetime.fromisoformat(timestamp) - datetime.fromisoformat(row['started_at'])).total_seconds()
            if elapsed < GAME_MINIMUM_SECONDS:
                raise ValueError('Complete the full 20-second clam game before claiming its reward.')
            if elapsed > GAME_EXPIRY_SECONDS:
                raise ValueError('This clam-game ticket has expired. Start a new game.')
            games = self.db.execute("SELECT COUNT(*) FROM earnings WHERE profile_id=? AND category='game' AND substr(time,1,10)=?", (profile_id, timestamp[:10])).fetchone()[0]
            nominal = GAME_REWARD if games < GAME_DAILY_LIMIT else 0
            reward = self._earn(profile_id, 'game:' + round_id, 'game', 'clam-catch',
                                'completed local game · accepted self-reported catches', nominal, 1, 0, timestamp,
                                {'round_id': round_id, 'caught': caught, 'elapsed_seconds': elapsed})
            result = {**reward, 'reward': reward['amount'], 'round_id': round_id, 'profile_id': profile_id, 'caught': caught,
                      'status': 'completed', 'game_daily_limit': GAME_DAILY_LIMIT,
                      'game_rounds_remaining': max(0, GAME_DAILY_LIMIT - games - 1),
                      'limit_reason': 'daily game limit' if games >= GAME_DAILY_LIMIT else 'daily earnings or wallet cap' if reward['capped'] else None}
            self.db.execute("UPDATE clam_rounds SET status='completed',completed_at=?,result=? WHERE id=?", (timestamp, json.dumps(result), round_id))
            return result
        return self._wallet_request('clam_game_reward', {'round_id': round_id, 'caught': caught}, request_id, effect, expected_profile)

    def cancel_game(self, round_id, profile_id=None):
        round_id = short_text(round_id, 'Round ID', 160)
        with self.transaction():
            profile_id = self._profile_id(profile_id)
            row = self.db.execute('SELECT status FROM clam_rounds WHERE id=? AND profile_id=?', (round_id, profile_id)).fetchone()
            if row is None:
                raise ValueError('This clam-game ticket belongs to another profile or does not exist.')
            self.db.execute("UPDATE clam_rounds SET status='cancelled' WHERE id=? AND profile_id=? AND status='active'", (round_id, profile_id))
            status = 'cancelled' if row['status'] == 'active' else row['status']
            return {'round_id': round_id, 'cancelled': status == 'cancelled', 'status': status}

    def cancel_active_games(self, profile_id=None):
        with self.transaction():
            profile_id = self._profile_id(profile_id)
            changed = self.db.execute("UPDATE clam_rounds SET status='cancelled' WHERE profile_id=? AND status='active'", (profile_id,)).rowcount
            return {'profile_id': profile_id, 'cancelled': changed}

    def deposit(self, amount, request_id):
        amount = whole(amount, 1)
        return self._wallet_request('deposit', {'amount': amount}, request_id, lambda pid: {'balance': self._transact(pid, amount, 'pretend deposit'), 'amount': amount})

    def spin(self, bet, request_id, expected_profile=None):
        bet = whole(bet, 1, 10000)

        def effect(profile_id):
            if self.balance(profile_id) < bet:
                raise ValueError('Not enough pretend monies for that bet.')
            symbols = [secrets.choice(['clam', 'hat', 'butt', 'snack']) for _ in range(3)]
            unique = len(set(symbols))
            payout = bet * (5 if unique == 1 else 2 if unique == 2 else 0)
            return {'symbols': symbols, 'bet': bet, 'payout': payout, 'balance': self._transact(profile_id, payout - bet, 'simulated slot spin')}

        return self._wallet_request('spin', {'bet': bet}, request_id, effect, expected_profile)

    @staticmethod
    def catalog():
        return copy.deepcopy(CATALOG)

    def purchase(self, item, request_id):
        selected = next((entry for entry in CATALOG if entry['id'] == item), None)
        if not selected:
            raise ValueError('Choose an item from the fictional shop.')
        return self._wallet_request('purchase', {'item': item}, request_id, lambda pid: {'item': selected['id'], 'price': selected['price'], 'balance': self._transact(pid, -selected['price'], 'fictional purchase: ' + item)})

    def deadlines(self, profile_id=None):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM deadlines WHERE profile_id=? ORDER BY due,id', (self._profile_id(profile_id),))]

    @staticmethod
    def _deadline(title, due):
        title = short_text(title, 'Deadline title', 160)
        if not isinstance(due, str) or len(due) > 80:
            raise ValueError('Use an ISO8601 deadline with a time-zone offset.')
        try:
            parsed = datetime.fromisoformat(due.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('Use an ISO8601 deadline with a time-zone offset.') from error
        if parsed.tzinfo is None:
            raise ValueError('Deadline must include its time zone.')
        return title, parsed.astimezone(timezone.utc).isoformat()

    def add_deadline(self, title, due):
        return self.import_deadlines([{'title': title, 'due': due}])[0]

    def import_deadlines(self, items):
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError('Import between 1 and 100 deadlines.')
        prepared = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Every deadline needs title and due fields.')
            prepared.append(self._deadline(item.get('title'), item.get('due')))
        result = []
        with self.transaction():
            profile_id = self._profile_id()
            for title, due in prepared:
                cursor = self.db.execute('INSERT INTO deadlines(title,due,profile_id) VALUES(?,?,?)', (title, due, profile_id))
                result.append({'id': cursor.lastrowid, 'title': title, 'due': due, 'profile_id': profile_id})
        return result

    def delete_deadline(self, deadline_id):
        deadline_id = whole(deadline_id, 1, 2**53 - 1)
        with self.transaction():
            return bool(self.db.execute('DELETE FROM deadlines WHERE id=? AND profile_id=?', (deadline_id, self._profile_id())).rowcount)

    def memories(self, profile_id=None):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM memory WHERE profile_id=? ORDER BY updated DESC,id DESC', (self._profile_id(profile_id),))]

    def remember(self, key, value, profile_id=None):
        key = short_text(key, 'Memory key', 120)
        value = short_text(value, 'Memory', 300)
        timestamp = now()
        with self.transaction():
            profile_id = self._profile_id(profile_id)
            if not self.db.execute('SELECT 1 FROM memory WHERE profile_id=? AND key=?', (profile_id, key)).fetchone() and self.db.execute('SELECT COUNT(*) FROM memory WHERE profile_id=?', (profile_id,)).fetchone()[0] >= 100:
                raise ValueError('Local memory is full. Forget an entry before adding another.')
            self.db.execute('INSERT INTO memory(profile_id,key,value,created,updated) VALUES(?,?,?,?,?) ON CONFLICT(profile_id,key) DO UPDATE SET value=excluded.value,updated=excluded.updated', (profile_id, key, value, timestamp, timestamp))
            return dict(self.db.execute('SELECT * FROM memory WHERE profile_id=? AND key=?', (profile_id, key)).fetchone())

    def forget(self, memory_id):
        memory_id = whole(memory_id, 1, 2**53 - 1)
        with self.transaction():
            return bool(self.db.execute('DELETE FROM memory WHERE id=? AND profile_id=?', (memory_id, self._profile_id())).rowcount)

    def log(self, kind, detail):
        with self.transaction():
            self.db.execute('INSERT INTO audit(kind,detail,time) VALUES(?,?,?)', (str(kind)[:80], str(detail)[:1000], now()))

    def logs(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 20')]
