"""Private, read-only calendar subscriptions for authoritative local deadlines.

No account credentials are scraped. Connect a provider's private iCalendar feed
once; only its own imported rows are reconciled on later refreshes.
"""

from collections import defaultdict
from datetime import date, datetime, time as daytime, timedelta, timezone
import hashlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
import uuid
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROVIDERS = ("google", "brightspace")
MAX_BYTES, MAX_COMPONENTS, MAX_ITEMS = 2 * 1024 * 1024, 2500, 5000
REFRESH_SECONDS = 15 * 60


class FeedError(ValueError):
    """A deliberately credential-free error suitable for public state."""


class Cancelled(Exception):
    pass


class _Cancellation:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


def validate_feed_url(provider, url):
    if provider not in PROVIDERS:
        raise FeedError("Choose Google Calendar or Brightspace.")
    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        raise FeedError("Paste the private calendar subscription link.")
    url = url.strip()
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname and
                 parsed.port in (None, 443) and not parsed.username and
                 not parsed.password and not parsed.fragment and
                 not any(ord(c) < 33 for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise FeedError("Use an HTTPS calendar feed without a username or password.")
    hostname = parsed.hostname.lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise FeedError("Use the provider's calendar hostname.")
    if provider == "google" and not (
        hostname == "calendar.google.com" and
        parsed.path.startswith("/calendar/ical/") and parsed.path.endswith(".ics")
    ):
        raise FeedError("Use Google Calendar's iCal subscription address.")
    if provider == "brightspace" and not parsed.path.lower().startswith("/d2l/le/calendar/feed/"):
        raise FeedError("Use the subscription address from Brightspace Calendar.")
    return url


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def fetch_calendar(provider, url, cancel):
    """Bounded HTTPS fetch, public DNS pinned per hop, without proxy/cookies."""
    end = time.monotonic() + 20
    for _ in range(4):
        if cancel.is_set():
            raise Cancelled()
        parsed = urlsplit(validate_feed_url(provider, url))
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise FeedError("Calendar refresh timed out. Saved deadlines are unchanged.")
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(
                parsed.hostname, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise FeedError("The calendar address must resolve to a public provider.")
            connection = _PinnedHTTPS(parsed.hostname, sorted(addresses)[0], min(6, remaining))
            try:
                target = parsed.path + ("?" + parsed.query if parsed.query else "")
                connection.request("GET", target, headers={"Accept": "text/calendar", "User-Agent": "Chuddy-Calendar/1"})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise FeedError("The calendar provider sent an invalid redirect.")
                    url = validate_feed_url(provider, urljoin(url, location))
                    continue
                if response.status != 200:
                    raise FeedError("Calendar access failed. Check or renew the private subscription link.")
                length = response.getheader("Content-Length")
                if length and (not length.isdecimal() or int(length) > MAX_BYTES):
                    raise FeedError("Calendar feed is larger than the supported 2 MB.")
                content = bytearray()
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise FeedError("Calendar refresh timed out. Saved deadlines are unchanged.")
                    if connection.sock is not None:
                        connection.sock.settimeout(min(6, remaining))
                    chunk = response.read(min(65536, MAX_BYTES + 1 - len(content)))
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise FeedError("Calendar feed is larger than the supported 2 MB.")
                    if not chunk:
                        return bytes(content)
            finally:
                connection.close()
        except (Cancelled, FeedError):
            raise
        except Exception:
            # Provider URLs frequently contain bearer secrets: never propagate
            # exception text, redirect targets, certificate text or HTTP bodies.
            raise FeedError("Could not refresh the calendar. Saved deadlines are unchanged.") from None
    raise FeedError("The calendar provider sent too many redirects.")


def _aware(value, local_zone):
    if isinstance(value, datetime):
        return value.replace(tzinfo=local_zone) if value.tzinfo is None else value
    if isinstance(value, date):
        return datetime.combine(value, daytime(), local_zone)
    raise FeedError("A calendar item has an invalid due date.")


def parse_calendar(content, *, timezone_name="America/Toronto", utcnow=None, cancel=None):
    """Expand daily-or-slower recurrence within -7/+366 days, preserving IDs.

    All-day dates use local midnight as a date anchor and are labelled; that
    anchor is not a supplied exact due time. VEVENT start and VTODO due are used.
    Unsupported or malformed content fails the whole refresh without deleting.
    """
    try:
        from icalendar import Calendar
        import recurring_ical_events
    except ImportError:
        raise FeedError("Calendar support is not installed. Install the app's calendar dependencies.") from None
    try:
        local_zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise FeedError("Choose a valid IANA calendar time zone.") from None
    if not isinstance(content, bytes) or len(content) > MAX_BYTES or b"BEGIN:VCALENDAR" not in content[:100]:
        raise FeedError("The subscription did not return a supported iCalendar feed.")
    current = utcnow or datetime.now(timezone.utc)
    lower, upper = current - timedelta(days=7), current + timedelta(days=366)
    budget = time.monotonic() + 8
    result = {}
    try:
        calendar = Calendar.from_ical(content)
        if calendar.name != "VCALENDAR":
            raise FeedError("The subscription did not return an iCalendar feed.")
        calendar_zone = str(calendar.get("X-WR-TIMEZONE", timezone_name))
        local_zone = ZoneInfo(calendar_zone)
        groups, zones = defaultdict(list), []
        components = calendar.walk()
        if len(components) > MAX_COMPONENTS:
            raise FeedError("Calendar feed has too many items for a single subscription.")
        for component in components:
            if component.name == "VTIMEZONE":
                zones.append(component)
            if component.name not in ("VEVENT", "VTODO"):
                continue
            uid = str(component.get("UID", ""))
            if not uid or len(uid) > 2048:
                raise FeedError("A calendar item is missing its stable identifier.")
            if component.errors:
                raise FeedError("A calendar item could not be read. Saved deadlines are unchanged.")
            if "RRULE" in component:
                rules = component["RRULE"]
                if str(rules.get("FREQ", [""])[0]).upper() not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
                    raise FeedError("Calendar recurrence more frequent than daily is unsupported.")
                if any(len(rules.get(key, [])) > 1 for key in ("BYHOUR", "BYMINUTE", "BYSECOND")):
                    raise FeedError("This calendar has an unsupported high-volume recurrence rule.")
                if "DTSTART" not in component:
                    raise FeedError("A recurring calendar item needs a start date.")
            groups[(component.name, uid)].append(component)

        def accept(component, recurring):
            if str(component.get("STATUS", "")).upper() in ("CANCELLED", "COMPLETED"):
                return
            field = "DUE" if component.name == "VTODO" and "DUE" in component else "DTSTART"
            if field not in component:
                return  # Undated tasks carry no deadline.
            value = component.decoded(field)
            due = _aware(value, local_zone).astimezone(timezone.utc)
            if not lower <= due <= upper:
                return
            date_only = isinstance(value, date) and not isinstance(value, datetime)
            title = " ".join(str(component.get("SUMMARY", "Calendar event")).split())[:145] or "Calendar event"
            if date_only:
                title += " [all day]"
            uid = str(component["UID"])
            recurrence = component.get("RECURRENCE-ID")
            identity = ""
            if recurring:
                identity = _aware(recurrence.dt if recurrence else component.decoded("DTSTART"), local_zone).astimezone(timezone.utc).isoformat()
            key = hashlib.sha256((component.name + "\0" + uid + "\0" + identity).encode()).hexdigest()
            result[key] = {"key": key, "title": title, "due": due.isoformat(), "date_only": date_only,
                           "calendar_date": value.isoformat() if date_only else None,
                           "calendar_timezone": calendar_zone if date_only else None}
            if len(result) > MAX_ITEMS:
                raise FeedError("Calendar has more than 5000 upcoming items.")

        for (kind, _), items in groups.items():
            if cancel and cancel.is_set():
                raise Cancelled()
            if time.monotonic() > budget:
                raise FeedError("Calendar recurrence took too long. Saved deadlines are unchanged.")
            recurring = any(any(key in component for key in ("RRULE", "RDATE", "RECURRENCE-ID")) for component in items)
            if recurring:
                subset = Calendar()
                for key in ("VERSION", "PRODID", "X-WR-TIMEZONE"):
                    if key in calendar:
                        subset[key] = calendar[key]
                for component in zones + items:
                    subset.add_component(component)
                expanded = recurring_ical_events.of(subset, components=[kind], skip_bad_series=False).between(lower, upper)
                if len(expanded) > MAX_ITEMS:
                    raise FeedError("Calendar recurrence has too many upcoming items.")
                for component in expanded:
                    accept(component, True)
            else:
                # A source can send multiple revisions of one UID. Prefer its
                # greatest SEQUENCE then DTSTAMP, including cancellation.
                component = max(items, key=lambda item: (int(item.get("SEQUENCE", 0)), str(item.get("DTSTAMP", ""))))
                accept(component, False)
        return sorted(result.values(), key=lambda item: (item["due"], item["key"]))
    except (FeedError, Cancelled):
        raise
    except Exception:
        raise FeedError("Calendar content could not be read. Saved deadlines are unchanged.") from None


class DeadlineSync:
    """Per-profile subscription worker; no network until start/connect/refresh."""

    def __init__(self, store, data_dir, *, fetcher=None, utcnow=None, interval=REFRESH_SECONDS):
        self.store, self.data_dir = store, data_dir
        self.fetcher = fetcher or fetch_calendar
        self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self.interval = max(60, int(interval))
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread, self._guard = None, threading.RLock()
        self._requested = set()
        self._active = {}
        with store.transaction():
            columns = {row["name"] for row in store.db.execute("PRAGMA table_info(deadlines)")}
            for column in ("calendar_source", "calendar_key", "calendar_date", "calendar_timezone"):
                if column not in columns:
                    store.db.execute(f"ALTER TABLE deadlines ADD COLUMN {column} TEXT")
            store.db.execute("""CREATE TABLE IF NOT EXISTS deadline_sources(
                profile_id TEXT, provider TEXT, secret BLOB NOT NULL, timezone TEXT NOT NULL,
                revision TEXT NOT NULL, last_sync TEXT, status TEXT NOT NULL, error TEXT,
                PRIMARY KEY(profile_id,provider))""")
            store.db.execute("""CREATE TABLE IF NOT EXISTS deadline_source_items(
                profile_id TEXT, provider TEXT, item_key TEXT, deadline_id INTEGER NOT NULL,
                date_only INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(profile_id,provider,item_key))""")
            store.db.execute("UPDATE deadline_sources SET status='connected' WHERE status='syncing'")

    def _selected(self, profile_id):
        if not isinstance(profile_id, str) or self.store._profile_id(profile_id) != self.store._profile_id():
            raise FeedError("The selected profile changed. Try again for the current profile.")
        return profile_id

    def info(self, profile_id=None):
        with self.store.lock:
            profile_id = self.store._profile_id(profile_id)
            saved = {row["provider"]: dict(row) for row in self.store.db.execute(
                "SELECT provider,last_sync,status,error FROM deadline_sources WHERE profile_id=?", (profile_id,))}
            sources = []
            for provider in PROVIDERS:
                row = saved.get(provider)
                count = self.store.db.execute("SELECT COUNT(*) FROM deadline_source_items WHERE profile_id=? AND provider=?", (profile_id, provider)).fetchone()[0]
                sources.append({"provider": provider, "connected": row is not None,
                                "status": row["status"] if row else "disconnected",
                                "last_sync": row["last_sync"] if row else None,
                                "error": row["error"] if row else None, "count": count})
            return {"sources": sources, "refresh_seconds": self.interval}

    def connect(self, provider, url, profile_id, timezone_name="America/Toronto"):
        from .secrets import protect
        url = validate_feed_url(provider, url)
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise FeedError("Choose a valid IANA calendar time zone.") from None
        try:
            protected = protect(url.encode("utf-8"))
        except Exception:
            raise FeedError("Could not protect the calendar link for this Windows user.") from None
        with self.store.transaction():
            self._selected(profile_id)
            self.store.db.execute("""INSERT INTO deadline_sources VALUES(?,?,?,?,?,NULL,'connected',NULL)
                ON CONFLICT(profile_id,provider) DO UPDATE SET secret=excluded.secret,
                timezone=excluded.timezone,revision=excluded.revision,status='connected',error=NULL""",
                (profile_id, provider, protected, timezone_name, uuid.uuid4().hex))
        self._cancel_source(profile_id, provider)
        self.refresh(profile_id)
        return self.info(profile_id)

    def disconnect(self, provider, profile_id):
        if provider not in PROVIDERS:
            raise FeedError("Choose Google Calendar or Brightspace.")
        with self.store.transaction():
            self._selected(profile_id)
            self.store.db.execute("DELETE FROM deadlines WHERE profile_id=? AND calendar_source=?", (profile_id, provider))
            self.store.db.execute("DELETE FROM deadline_source_items WHERE profile_id=? AND provider=?", (profile_id, provider))
            self.store.db.execute("DELETE FROM deadline_sources WHERE profile_id=? AND provider=?", (profile_id, provider))
        self._cancel_source(profile_id, provider)
        return self.info(profile_id)

    def _cancel_source(self, profile_id, provider):
        with self._guard:
            for identity, cancel in self._active.items():
                if identity[:2] == (profile_id, provider):
                    cancel.set()

    def refresh(self, profile_id):
        with self.store.lock:
            self._selected(profile_id)
        with self._guard:
            self._requested.add(profile_id)
        self._wake.set()
        return self.info(profile_id)

    def start(self):
        with self._guard:
            if self._thread and self._thread.is_alive():
                return
            if self._stop.is_set():
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="chuddy-calendar-sync")
            self._thread.start()

    def _run(self):
        next_all = 0
        while not self._stop.is_set():
            self._wake.clear()
            refresh_all = time.monotonic() >= next_all
            with self._guard:
                requested, self._requested = self._requested, set()
            with self.store.lock:
                if self._stop.is_set():
                    return
                sources = [dict(row) for row in self.store.db.execute("SELECT * FROM deadline_sources")]
            for source in sources:
                if self._stop.is_set():
                    return
                if refresh_all or source["profile_id"] in requested:
                    self._sync(source)
            if refresh_all:
                next_all = time.monotonic() + self.interval
            self._wake.wait(max(.1, min(self.interval, next_all - time.monotonic())))

    def _sync(self, source):
        identity = (source["profile_id"], source["provider"], source["revision"])
        event = threading.Event()
        with self._guard:
            self._active[identity] = event
        try:
            self._sync_source(source, _Cancellation(self._stop, event))
        finally:
            with self._guard:
                if self._active.get(identity) is event:
                    del self._active[identity]

    def _sync_source(self, source, cancel):
        from .secrets import unprotect
        identity = (source["profile_id"], source["provider"], source["revision"])
        if cancel.is_set():
            return
        with self.store.transaction():
            if cancel.is_set():
                return
            updated = self.store.db.execute("UPDATE deadline_sources SET status='syncing',error=NULL WHERE profile_id=? AND provider=? AND revision=?", identity)
            if not updated.rowcount:
                return
        try:
            try:
                url = unprotect(source["secret"]).decode("utf-8")
            except Exception:
                raise FeedError("Could not open the saved calendar link. Reconnect this calendar.") from None
            content = self.fetcher(source["provider"], url, cancel)
            items = parse_calendar(content, timezone_name=source["timezone"], utcnow=self.utcnow(), cancel=cancel)
            if cancel.is_set():
                return
            with self.store.transaction():
                # Old requests cannot write after reconnect/disconnect/profile
                # changes, or after close invalidates this worker.
                if cancel.is_set() or not self.store.db.execute("SELECT 1 FROM deadline_sources WHERE profile_id=? AND provider=? AND revision=?", identity).fetchone():
                    return
                old = {row["item_key"]: row["deadline_id"] for row in self.store.db.execute("SELECT item_key,deadline_id FROM deadline_source_items WHERE profile_id=? AND provider=?", identity[:2])}
                retained = set()
                for item in items:
                    key, deadline_id = item["key"], old.get(item["key"])
                    retained.add(key)
                    if deadline_id and self.store.db.execute("SELECT 1 FROM deadlines WHERE id=? AND profile_id=? AND calendar_source=? AND calendar_key=?", (deadline_id, source["profile_id"], source["provider"], key)).fetchone():
                        self.store.db.execute("UPDATE deadlines SET title=?,due=?,calendar_date=?,calendar_timezone=? WHERE id=? AND profile_id=?", (item["title"], item["due"], item["calendar_date"], item["calendar_timezone"], deadline_id, source["profile_id"]))
                    else:
                        cursor = self.store.db.execute("INSERT INTO deadlines(title,due,profile_id,calendar_source,calendar_key,calendar_date,calendar_timezone) VALUES(?,?,?,?,?,?,?)", (item["title"], item["due"], source["profile_id"], source["provider"], key, item["calendar_date"], item["calendar_timezone"]))
                        deadline_id = cursor.lastrowid
                    self.store.db.execute("INSERT INTO deadline_source_items VALUES(?,?,?,?,?) ON CONFLICT(profile_id,provider,item_key) DO UPDATE SET deadline_id=excluded.deadline_id,date_only=excluded.date_only", (source["profile_id"], source["provider"], key, deadline_id, int(item["date_only"])))
                for key, deadline_id in old.items():
                    if key not in retained:
                        self.store.db.execute("DELETE FROM deadlines WHERE id=? AND profile_id=? AND calendar_source=? AND calendar_key=?", (deadline_id, source["profile_id"], source["provider"], key))
                        self.store.db.execute("DELETE FROM deadline_source_items WHERE profile_id=? AND provider=? AND item_key=?", (source["profile_id"], source["provider"], key))
                self.store.db.execute("UPDATE deadline_sources SET status='ready',error=NULL,last_sync=? WHERE profile_id=? AND provider=? AND revision=?", (self.utcnow().astimezone(timezone.utc).isoformat(),) + identity)
        except Cancelled:
            pass
        except Exception as error:
            if cancel.is_set():
                return
            message = str(error) if isinstance(error, FeedError) else "Calendar refresh failed. Saved deadlines are unchanged."
            with self.store.transaction():
                self.store.db.execute("UPDATE deadline_sources SET status='error',error=? WHERE profile_id=? AND provider=? AND revision=?", (message,) + identity)

    def close(self):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=7)
