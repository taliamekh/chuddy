"""Calendar fixtures and mock transports only; no real accounts or browsing."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from chuddy.deadline_sync import (Cancelled, DeadlineSync, FeedError, fetch_calendar,
                                  parse_calendar, validate_feed_url)
from chuddy.store import Store


NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
GOOGLE = "https://calendar.google.com/calendar/ical/test/private-fixture/basic.ics"
BRIGHTSPACE = "https://brightspace.example.edu/d2l/le/calendar/feed/user/feed.ics?token=fixture-secret"


def calendar(*items, zone="America/Toronto"):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Fixture//EN\r\nX-WR-TIMEZONE:" + zone + "\r\n" + "\r\n".join(items) + "\r\nEND:VCALENDAR\r\n").encode()


def event(uid="report", title="Report due", start="20261101T230000Z", extra=""):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{start}\r\n{extra}\r\nEND:VEVENT"


class CalendarParsingTests(unittest.TestCase):
    def test_zulu_and_todo_due_preserve_authoritative_time(self):
        task = "BEGIN:VTODO\r\nUID:task\r\nSUMMARY:Submit essay\r\nDUE:20261102T040000Z\r\nEND:VTODO"
        rows = parse_calendar(calendar(event(), task), utcnow=NOW)
        self.assertEqual([r["due"] for r in rows], ["2026-11-01T23:00:00+00:00", "2026-11-02T04:00:00+00:00"])

    def test_floating_time_uses_declared_calendar_zone(self):
        row = parse_calendar(calendar(event(start="20261103T170000")), utcnow=NOW)[0]
        self.assertEqual(row["due"], "2026-11-03T22:00:00+00:00")

    def test_all_day_is_explicit_date_anchor(self):
        text = event().replace("DTSTART:20261101T230000Z", "DTSTART;VALUE=DATE:20261103")
        row = parse_calendar(calendar(text), utcnow=NOW)[0]
        self.assertTrue(row["date_only"])
        self.assertEqual(row["title"], "Report due [all day]")
        self.assertEqual(row["due"], "2026-11-03T05:00:00+00:00")
        self.assertEqual(row["calendar_date"], "2026-11-03")
        self.assertEqual(row["calendar_timezone"], "America/Toronto")

    def test_all_day_original_date_survives_different_calendar_timezone(self):
        text = event().replace("DTSTART:20261101T230000Z", "DTSTART;VALUE=DATE:20261103")
        row = parse_calendar(calendar(text, zone="Asia/Tokyo"), utcnow=NOW)[0]
        self.assertEqual(row["due"], "2026-11-02T15:00:00+00:00")
        self.assertEqual(row["calendar_date"], "2026-11-03")
        self.assertEqual(row["calendar_timezone"], "Asia/Tokyo")

    def test_weekly_recurrence_respects_dst_and_stable_occurrence_keys(self):
        text = event().replace("DTSTART:20261101T230000Z", "DTSTART;TZID=America/Toronto:20261030T170000")
        text = text.replace("\r\nEND:VEVENT", "\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT")
        rows = parse_calendar(calendar(text), utcnow=NOW)
        self.assertEqual([r["due"] for r in rows], ["2026-10-30T21:00:00+00:00", "2026-11-06T22:00:00+00:00", "2026-11-13T22:00:00+00:00"])
        self.assertEqual(len({row["key"] for row in rows}), 3)
        renamed = parse_calendar(calendar(text.replace("Report due", "New title")), utcnow=NOW)
        self.assertEqual([r["key"] for r in rows], [r["key"] for r in renamed])

    def test_recurrence_exdate_and_moved_override(self):
        base = event(extra="RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261102T230000Z")
        override = event(start="20261103T200000Z", extra="RECURRENCE-ID:20261103T230000Z")
        rows = parse_calendar(calendar(base, override), utcnow=NOW)
        self.assertEqual([r["due"] for r in rows], ["2026-11-01T23:00:00+00:00", "2026-11-03T20:00:00+00:00"])

    def test_cancellation_undated_tasks_and_outside_window_do_not_become_deadlines(self):
        rows = parse_calendar(calendar(event(extra="STATUS:CANCELLED"), event(uid="old", start="20200101T000000Z"), "BEGIN:VTODO\r\nUID:undated\r\nSUMMARY:Maybe\r\nEND:VTODO"), utcnow=NOW)
        self.assertEqual(rows, [])

    def test_cancelled_latest_revision_removes_prior_uid(self):
        rows = parse_calendar(calendar(event(extra="SEQUENCE:0"), event(extra="SEQUENCE:1\r\nSTATUS:CANCELLED")), utcnow=NOW)
        self.assertEqual(rows, [])

    def test_malformed_html_missing_uid_and_high_frequency_are_rejected(self):
        for body in (b"<html>Login</html>", calendar(event().replace("UID:report\r\n", "")), calendar(event(extra="RRULE:FREQ=SECONDLY;COUNT=1000000"))):
            with self.subTest(body=body[:30]), self.assertRaises(FeedError):
                parse_calendar(body, utcnow=NOW)

    def test_cancel_during_expansion_and_long_feed(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            parse_calendar(calendar(event()), utcnow=NOW, cancel=stop)
        with self.assertRaises(FeedError):
            parse_calendar(b"BEGIN:VCALENDAR" + b" " * (2 * 1024 * 1024), utcnow=NOW)


class CalendarURLTests(unittest.TestCase):
    def mock_connection(self, *, status=200, location=None, content=None):
        response = Mock()
        response.status = status
        response.getheader.side_effect = lambda key: location if key == "Location" else None
        response.read.side_effect = [calendar(event()) if content is None else content, b""]
        connection = Mock()
        connection.sock = None  # HTTP/1.0 or Connection: close is valid.
        connection.getresponse.return_value = response
        return connection

    def test_only_provider_subscription_urls(self):
        self.assertEqual(validate_feed_url("google", GOOGLE), GOOGLE)
        self.assertEqual(validate_feed_url("brightspace", BRIGHTSPACE), BRIGHTSPACE)
        for provider, url in (("google", BRIGHTSPACE), ("brightspace", "https://example.edu/login"), ("google", GOOGLE.replace("https:", "http:")), ("google", GOOGLE.replace("calendar.google.com", "calendar.google.com.evil.test")), ("brightspace", BRIGHTSPACE.replace("brightspace.example.edu", "127.0.0.1")), ("brightspace", BRIGHTSPACE.replace("https://", "https://user:password@")), ("brightspace", BRIGHTSPACE + "#secret")):
            with self.subTest(url=url), self.assertRaises(FeedError):
                validate_feed_url(provider, url)

    def test_private_dns_is_not_fetched(self):
        address = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch("chuddy.deadline_sync.socket.getaddrinfo", return_value=address), patch("chuddy.deadline_sync._PinnedHTTPS") as connection:
            with self.assertRaises(FeedError):
                fetch_calendar("brightspace", BRIGHTSPACE, threading.Event())
            connection.assert_not_called()

    def test_transport_exception_never_leaks_subscription_secrets(self):
        address = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with patch("chuddy.deadline_sync.socket.getaddrinfo", return_value=address), patch("chuddy.deadline_sync._PinnedHTTPS", side_effect=RuntimeError(BRIGHTSPACE)):
            with self.assertRaises(FeedError) as caught:
                fetch_calendar("brightspace", BRIGHTSPACE, threading.Event())
            self.assertNotIn("fixture-secret", str(caught.exception))

    def test_successful_https_feed_with_closed_connection_and_no_cookies(self):
        address = [(2, 1, 6, "", ("8.8.8.8", 443))]
        connection = self.mock_connection()
        with patch("chuddy.deadline_sync.socket.getaddrinfo", return_value=address), patch("chuddy.deadline_sync._PinnedHTTPS", return_value=connection):
            self.assertEqual(fetch_calendar("google", GOOGLE, threading.Event()), calendar(event()))
        headers = connection.request.call_args.kwargs["headers"]
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)
        connection.close.assert_called_once()

    def test_redirect_is_revalidated_and_remote_error_body_not_read(self):
        address = [(2, 1, 6, "", ("8.8.8.8", 443))]
        for status, location in ((302, "https://127.0.0.1/d2l/le/calendar/feed/user/feed.ics"), (403, None)):
            connection = self.mock_connection(status=status, location=location)
            with self.subTest(status=status), patch("chuddy.deadline_sync.socket.getaddrinfo", return_value=address), patch("chuddy.deadline_sync._PinnedHTTPS", return_value=connection), self.assertRaises(FeedError):
                fetch_calendar("brightspace", BRIGHTSPACE, threading.Event())
            connection.getresponse.return_value.read.assert_not_called()
            connection.close.assert_called_once()

    def test_transport_stops_before_dns_when_cancelled(self):
        stop = threading.Event()
        stop.set()
        with patch("chuddy.deadline_sync.socket.getaddrinfo") as dns, self.assertRaises(Cancelled):
            fetch_calendar("google", GOOGLE, stop)
        dns.assert_not_called()


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.sqlite3")
        self.profile = self.store.profile()["id"]
        self.protect = patch("chuddy.secrets.protect", side_effect=lambda value: b"encrypted:" + value).start()
        self.unprotect = patch("chuddy.secrets.unprotect", side_effect=lambda value: value.removeprefix(b"encrypted:")).start()
        self.body = calendar(event())
        self.sync = DeadlineSync(self.store, Path(self.temp.name), fetcher=lambda provider, url, stop: self.body, utcnow=lambda: NOW)

    def tearDown(self):
        self.sync.close()
        self.store.close()
        self.temp.cleanup()
        patch.stopall()

    def source(self, provider="google"):
        with self.store.lock:
            return dict(self.store.db.execute("SELECT * FROM deadline_sources WHERE profile_id=? AND provider=?", (self.profile, provider)).fetchone())

    def connect_and_sync(self, provider="google"):
        self.sync.connect(provider, GOOGLE if provider == "google" else BRIGHTSPACE, self.profile)
        self.sync._sync(self.source(provider))

    def test_constructor_is_passive_and_status_is_secret_free(self):
        self.assertIsNone(self.sync._thread)
        self.assertTrue(all(not row["connected"] for row in self.sync.info()["sources"]))
        self.sync.connect("google", GOOGLE, self.profile)
        public = json.dumps(self.sync.info())
        self.assertNotIn("private-fixture", public)
        self.assertNotIn("https://", public)
        self.assertTrue(self.source()["secret"].startswith(b"encrypted:"))
        self.assertEqual(self.store.deadlines(), [])

    def test_idempotent_refresh_updates_uid_without_duplicate(self):
        self.connect_and_sync()
        first = self.store.deadlines()[0]
        self.sync._sync(self.source())
        self.assertEqual(len(self.store.deadlines()), 1)
        self.body = calendar(event(title="Revised report", start="20261104T230000Z"))
        self.sync._sync(self.source())
        revised = self.store.deadlines()[0]
        self.assertEqual(revised["id"], first["id"])
        self.assertEqual(revised["due"], "2026-11-04T23:00:00+00:00")
        self.assertEqual(revised["title"], "Revised report")
        self.assertIsNone(revised["calendar_date"])
        self.assertIsNone(revised["calendar_timezone"])

    def test_all_day_metadata_stored_and_cleared_when_event_gets_exact_time(self):
        self.body = calendar(event().replace("DTSTART:20261101T230000Z", "DTSTART;VALUE=DATE:20261103"), zone="Asia/Tokyo")
        self.connect_and_sync()
        row = self.store.deadlines()[0]
        self.assertEqual(row["calendar_date"], "2026-11-03")
        self.assertEqual(row["calendar_timezone"], "Asia/Tokyo")
        self.body = calendar(event())
        self.sync._sync(self.source())
        row = self.store.deadlines()[0]
        self.assertIsNone(row["calendar_date"])
        self.assertIsNone(row["calendar_timezone"])

    def test_removed_events_delete_only_owned_rows(self):
        self.store.add_deadline("Manual", "2026-11-02T23:00:00Z")
        self.connect_and_sync()
        self.body = calendar()
        self.sync._sync(self.source())
        self.assertEqual([row["title"] for row in self.store.deadlines()], ["Manual"])

    def test_failed_fetch_or_parse_preserves_prior_rows_and_last_success(self):
        self.connect_and_sync()
        before, last = self.store.deadlines(), self.sync.info()["sources"][0]["last_sync"]
        self.body = b"<html>Login required</html>"
        self.sync._sync(self.source())
        self.assertEqual(self.store.deadlines(), before)
        status = self.sync.info()["sources"][0]
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["last_sync"], last)

    def test_disconnecting_one_provider_preserves_other_provider_and_manual(self):
        self.store.add_deadline("Manual", "2026-11-02T23:00:00Z")
        self.connect_and_sync("google")
        self.connect_and_sync("brightspace")
        self.sync.disconnect("google", self.profile)
        rows = self.store.deadlines()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["calendar_source"] for row in rows}, {None, "brightspace"})

    def test_manually_deleted_id_reused_by_manual_row_is_not_owned(self):
        self.connect_and_sync()
        self.store.delete_deadline(self.store.deadlines()[0]["id"])
        manual = self.store.add_deadline("Manual recycled id", "2026-11-02T23:00:00Z")
        self.sync._sync(self.source())
        self.assertEqual(len(self.store.deadlines()), 2)
        self.assertTrue(any(row["id"] == manual["id"] and row["title"] == "Manual recycled id" for row in self.store.deadlines()))
        self.sync.disconnect("google", self.profile)
        self.assertEqual([row["title"] for row in self.store.deadlines()], ["Manual recycled id"])

    def test_manually_deleted_id_reused_by_other_source_does_not_conflict(self):
        self.connect_and_sync()
        self.store.delete_deadline(self.store.deadlines()[0]["id"])
        self.connect_and_sync("brightspace")
        self.sync._sync(self.source())
        self.assertEqual(len(self.store.deadlines()), 2)
        self.assertEqual({row["calendar_source"] for row in self.store.deadlines()}, {"google", "brightspace"})

    def test_profile_switch_cannot_connect_for_stale_ui_profile(self):
        other = self.store.add_profile("Other")
        self.store.select_profile(other["id"])
        for operation in (lambda: self.sync.connect("google", GOOGLE, self.profile), lambda: self.sync.refresh(self.profile), lambda: self.sync.disconnect("google", self.profile)):
            with self.assertRaises(FeedError):
                operation()
        self.assertFalse(any(row["connected"] for row in self.sync.info()["sources"]))

    def test_background_refresh_writes_original_profile_after_switch(self):
        self.sync.connect("google", GOOGLE, self.profile)
        source = self.source()
        other = self.store.add_profile("Other")
        self.store.select_profile(other["id"])
        self.sync._sync(source)
        self.assertEqual(self.store.deadlines(), [])
        self.assertEqual(len(self.store.deadlines(self.profile)), 1)

    def test_late_reconnect_or_disconnect_response_cannot_write(self):
        self.sync.connect("google", GOOGLE, self.profile)
        stale = self.source()
        transport = Mock(return_value=self.body)
        self.sync.fetcher = transport
        self.sync.connect("google", GOOGLE.replace("private-fixture", "private-new"), self.profile)
        self.sync._sync(stale)
        self.assertEqual(self.store.deadlines(), [])
        self.sync.disconnect("google", self.profile)
        self.sync._sync(stale)
        self.assertEqual(self.store.deadlines(), [])
        self.sync.connect("google", GOOGLE, self.profile)
        self.sync._sync(stale)
        self.assertEqual(self.store.deadlines(), [])
        transport.assert_not_called()

    def test_disconnect_cancels_inflight_transport(self):
        def fetch(provider, url, cancel):
            self.sync.disconnect(provider, self.profile)
            self.assertTrue(cancel.is_set())
            return self.body
        self.sync.fetcher = fetch
        self.connect_and_sync()
        self.assertEqual(self.store.deadlines(), [])
        self.assertFalse(self.sync.info()["sources"][0]["connected"])

    def test_close_cancels_worker_before_store_is_closed(self):
        entered = threading.Event()
        def fetch(provider, url, cancel):
            entered.set()
            deadline = time.monotonic() + 3
            while not cancel.is_set() and time.monotonic() < deadline:
                time.sleep(.01)
            return self.body
        self.sync.fetcher = fetch
        self.sync.connect("google", GOOGLE, self.profile)
        self.sync.start()
        self.assertTrue(entered.wait(2))
        self.sync.close()
        self.assertFalse(self.sync._thread.is_alive())
        self.assertEqual(self.store.deadlines(), [])

    def test_cancelled_fetch_cannot_import_late_rows(self):
        def fetch(provider, url, stop):
            self.sync.close()
            return self.body
        self.sync.fetcher = fetch
        self.connect_and_sync()
        self.assertEqual(self.store.deadlines(), [])

    def test_restart_keeps_mapping_and_resumes_without_duplicating(self):
        self.connect_and_sync()
        before = self.store.deadlines()
        self.sync.close()
        self.sync = DeadlineSync(self.store, Path(self.temp.name), fetcher=lambda *args: self.body, utcnow=lambda: NOW)
        self.sync._sync(self.source())
        self.assertEqual(self.store.deadlines(), before)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_worker_refreshes_connected_sources_with_mock_transport(self):
        self.sync.connect("google", GOOGLE, self.profile)
        self.sync.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.store.deadlines():
            time.sleep(.01)
        self.assertEqual(len(self.store.deadlines()), 1)
        self.sync.close()
        self.assertFalse(self.sync._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
