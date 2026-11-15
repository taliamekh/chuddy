"""Disposable stores and fake clocks/devices only."""
import concurrent.futures
import threading
from unittest.mock import patch

from chuddy.store import MAX_BALANCE, Store
from tests.helpers import ControllerCase


class PassiveMoniesTests(ControllerCase):
    def advance(self, seconds):
        self.now += seconds
        self.controller.earnings_tick()

    def passive_paid(self):
        return self.store.db.execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE category='passive'").fetchone()[0]

    def start(self):
        self.controller.set_session('start')

    def test_running_without_scores_or_recording_earns_and_polls_do_not_mint(self):
        self.start()
        for _ in range(3):
            self.advance(1)
        self.assertEqual(self.store.balance(), 103)
        for _ in range(15):
            self.controller.state()
            self.controller.earnings_tick()
        self.assertEqual(self.store.balance(), 103)
        self.assertEqual(self.store.scores()['total'], 0)
        self.assertFalse(self.controller.audio.recording)

    def test_foreground_work_and_protected_apps_suspend_payment_and_keep_the_boost_clock(self):
        self.start()
        self.advance(1)
        foreground = self.patches[-1].target.foreground
        foreground.return_value = {'handle':42, 'pid':123, 'process':'WINWORD.EXE', 'title':'Report'}
        self.controller.poll_work()
        self.assertEqual(self.store.passive_info()['status'], 'work_detected')
        self.store.event('boost-at-work', 'fart', 'manual')
        self.advance(3)
        foreground.return_value = {**foreground.return_value, 'process':'Codex.exe'}
        self.controller.poll_work()
        self.assertEqual(self.controller.work_detection['reason'], 'protected_app')
        self.advance(2)
        self.assertEqual(self.passive_paid(), 1)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1795000)
        foreground.return_value = None
        self.controller.poll_work()
        self.advance(1)
        self.assertEqual(self.passive_paid(), 3)

    def test_worker_saves_without_any_state_requests(self):
        self.start()
        self.now += 2
        # Observe commit notification, not HTTP polls. Real wait is bounded.
        saved = threading.Event()
        original = self.store.passive_tick
        def tick():
            original()
            saved.set()
        with patch.object(self.store, 'passive_tick', side_effect=tick):
            self.assertTrue(saved.wait(2))
        self.assertEqual(self.store.balance(), 102)

    def test_farts_settle_previous_rate_and_extend_the_boost_once(self):
        self.start()
        self.now += 2
        self.store.event('one', 'fart', 'manual')
        self.assertEqual(self.passive_paid(), 2)
        self.assertEqual(self.store.passive_info()['multiplier'], 2)
        self.store.event('one', 'fart', 'manual')
        self.assertEqual(self.store.passive_info()['multiplier'], 2)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1800000)
        self.advance(2)
        self.assertEqual(self.passive_paid(), 6)
        self.store.event('two', 'fart', 'deliberate sound test')
        self.assertEqual(self.store.passive_info()['multiplier'], 3)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 3598000)
        self.store.event('three', 'fart', 'manual')
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 5398000)

    def test_controller_rejects_burp_before_output_or_boost(self):
        self.start()
        with patch.object(self.controller.audio, 'play_effect') as playback:
            for action in ('score', 'sound_test'):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Only fart'):
                    self.controller.action({'action': action, 'kind': 'burp', 'request_id': 'old-action'})
            playback.assert_not_called()
        self.assertEqual(self.store.scores()['total'], 0)
        self.assertEqual(self.store.balance(), 100)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 0)
        self.assertTrue(self.controller.action({'action': 'score', 'kind': 'fart', 'request_id': 'old-action'})['counted'])
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1800000)
        self.assertEqual(self.controller.context()['scores'], {'fart': 1})

    def test_boost_cap_expiry_and_accepted_playback_sources(self):
        self.start()
        for index in range(8):
            self.store.event(str(index), 'fart', 'simulator sound test')
        self.assertEqual(self.store.passive_info()['multiplier'], 5)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 14400000)
        for _ in range(12):
            self.advance(5)
        self.assertEqual(self.passive_paid(), 300)
        self.assertEqual(self.store.passive_info()['multiplier'], 5)
        self.advance(2)
        self.assertEqual(self.passive_paid(), 310)

    def test_expiry_inside_tick_splits_at_exact_boundary(self):
        self.start()
        self.store.event('boost', 'fart', 'manual')
        for _ in range(359):
            self.now += 5
            self.store.passive_tick()
        self.advance(4)
        self.advance(3)
        self.assertEqual(self.passive_paid(), 3602)
        self.assertEqual(self.store.passive_info()['multiplier'], 1)
        self.store.event('next', 'fart', 'manual')
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1800000)

    def test_pause_stop_and_repeat_start_do_not_duplicate_or_reset(self):
        self.start()
        self.store.event('one', 'fart', 'manual')
        self.now += 1
        self.start()
        self.assertEqual(self.store.passive_info()['multiplier'], 2)
        self.controller.set_session('pause')
        self.assertEqual(self.passive_paid(), 2)
        self.advance(100)
        self.store.event('paused-fart', 'fart', 'manual')
        self.controller.set_session('resume')
        self.assertEqual(self.store.passive_info()['multiplier'], 1)
        self.advance(1)
        self.controller.set_session('stop')
        self.advance(100)
        self.assertEqual(self.passive_paid(), 3)

    def test_fraction_carry_survives_pause_and_restart_without_offline_money(self):
        self.start()
        self.advance(.4)
        self.controller.set_session('pause')
        self.store.bind_passive_runtime(lambda: self.now)
        self.advance(1000)
        self.controller.set_session('resume')
        self.advance(.6)
        self.assertEqual(self.passive_paid(), 1)

    def test_restart_reopen_keeps_wallet_history_and_discards_old_timer(self):
        self.start()
        self.advance(2)
        self.store.event('one', 'fart', 'manual')
        self.controller.close()
        before = self.store.balance()
        reopened = Store(self.store.path)
        try:
            reopened.bind_passive_runtime(lambda: self.now+10000)
            reopened.passive_tick()
            self.assertEqual(reopened.balance(), before)
            self.assertEqual(reopened.scores()['total'], 1)
            self.assertEqual(reopened.passive_info()['status'], 'stopped')
        finally:
            reopened.close()

    def test_profile_switch_pays_old_profile_only_and_stops(self):
        first = self.store.profile()['id']
        other = self.store.add_profile('Other')
        self.start()
        self.now += 3
        self.controller.action({'action':'profile_select', 'id':other['id']})
        self.advance(3)
        self.assertEqual(self.store.balance(first), 103)
        self.assertEqual(self.store.balance(), 100)
        self.assertEqual(self.controller.session, 'stopped')

    def test_long_sleep_or_stall_is_not_credited_or_banked(self):
        self.start()
        self.store.event('one', 'fart', 'manual')
        self.advance(60*60)
        self.assertEqual(self.passive_paid(), 0)
        self.assertEqual(self.store.passive_info()['multiplier'], 1)
        self.advance(1)
        self.assertEqual(self.passive_paid(), 1)

    def test_fraction_is_cadence_independent_and_concurrent_ticks_are_once(self):
        self.start()
        for _ in range(10):
            self.advance(.125)
        self.assertEqual(self.passive_paid(), 1)
        self.now += 1
        with concurrent.futures.ThreadPoolExecutor(5) as pool:
            list(pool.map(lambda _: self.controller.earnings_tick(), range(10)))
        self.assertEqual(self.passive_paid(), 2)

    def test_bonus_cap_does_not_disable_passive_and_passive_does_not_consume_bonus_cap(self):
        self.start()
        for _ in range(101):
            self.advance(5)
        self.assertEqual(self.store.earnings()['daily_remaining'], 500)
        for i in range(200):
            self.store.event(str(i), 'fart', 'manual')
        self.assertEqual(self.store.earnings()['daily_remaining'], 0)
        self.advance(1)
        self.assertEqual(self.passive_paid(), 510)
        self.assertEqual(self.store.passive_info()['rate_per_second'], 5)

    def test_full_wallet_and_fraction_do_not_bank_money(self):
        self.store.db.execute('UPDATE profile_wallet SET balance=?', (MAX_BALANCE,))
        self.start()
        self.advance(1.5)
        self.assertEqual(self.store.passive_info()['status'], 'wallet_full')
        self.store.transact(-10, 'test spend')
        self.advance(.5)
        self.assertEqual(self.store.balance(), MAX_BALANCE-10)
        self.advance(.5)
        self.assertEqual(self.store.balance(), MAX_BALANCE-9)

    def test_transaction_failure_rolls_back_checkpoint_then_retry_pays_once(self):
        self.start()
        self.now += 2
        with patch.object(self.store, '_transact', side_effect=RuntimeError('disk')):
            self.controller.earnings_tick()
        self.assertEqual(self.controller.state()['earnings']['passive']['status'], 'error')
        self.assertEqual(self.passive_paid(), 0)
        self.controller.earnings_tick()
        self.controller.earnings_tick()
        self.assertEqual(self.passive_paid(), 2)
        self.assertIsNone(self.controller.earnings_error)

    def test_expiring_session_does_not_pay_after_deadline(self):
        self.start()
        self.controller.expires = self.now+2
        self.advance(4)
        self.assertEqual(self.passive_paid(), 2)
        self.assertEqual(self.controller.session, 'stopped')

    def test_client_elapsed_is_ignored_and_casino_retry_keeps_original_result(self):
        self.controller.action({'action':'session','operation':'start','elapsed':999999,'multiplier':99})
        request = {'action':'casino_start','game':'blackjack','bet':10,
                   'profile_id':self.store.profile()['id'],'request_id':'reserve'}
        result = self.controller.action(request)
        self.advance(3)
        self.assertEqual(self.controller.action(request), result)
        self.assertEqual(self.store.balance(), result['balance']+3)
        self.assertEqual(self.passive_paid(), 3)

    def test_failed_start_remains_visible_and_stop_failure_invalidates_late_ticks(self):
        with patch.object(self.store, 'passive_start', side_effect=RuntimeError('disk')):
            self.start()
        self.controller.earnings_tick()
        self.assertEqual(self.controller.state()['earnings']['passive']['status'], 'error')
        self.controller.set_session('pause')
        self.controller.set_session('resume')
        self.now += 1
        with patch.object(self.store, '_transact', side_effect=RuntimeError('disk')):
            self.controller.set_session('stop')
        self.assertEqual(self.controller.session, 'stopped')
        self.now += 1
        self.store.passive_tick()
        self.assertEqual(self.passive_paid(), 0)
        self.start()
        self.advance(1)
        self.assertEqual(self.passive_paid(), 1)

    def test_stop_cuts_earning_before_slow_cleanup_and_late_fart(self):
        self.start()
        self.now += 1
        def slow_cleanup():
            self.assertEqual(self.controller.session, 'stopped')
            self.assertFalse(self.store.passive_enabled)
            self.now += 30
            self.store.event('late-playback', 'fart', 'simulator sound test')
        with patch.object(self.controller.simulator, 'stop', side_effect=slow_cleanup):
            self.controller.set_session('stop')
        self.assertEqual(self.passive_paid(), 1)
        self.assertEqual(self.store.passive_info()['status'], 'stopped')

    def test_windows_clock_uses_unbiased_awake_time_and_fails_closed(self):
        from types import SimpleNamespace
        from chuddy.earning_clock import awake_monotonic
        def query(pointer):
            pointer._obj.value = 123_000_000
            return True
        api = SimpleNamespace(kernel32=SimpleNamespace(QueryUnbiasedInterruptTime=query))
        with patch('chuddy.earning_clock.sys.platform', 'win32'), patch('chuddy.earning_clock.ctypes.windll', api, create=True):
            self.assertEqual(awake_monotonic(), 12.3)
            api.kernel32.QueryUnbiasedInterruptTime = lambda p: False
            with self.assertRaises(OSError):
                awake_monotonic()
