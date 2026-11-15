"""Authoritative state and effects. Character arithmetic never touches this logic."""

import fnmatch
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse
from . import windows
from .agent import LocalAgent, local_endpoint
from .audio import AudioPipeline
from .conversation import security_related
from .destinations import DESTINATIONS
from .work_recognition import BROWSERS, evaluate
from .work_banter import BANTER_OUTCOME_SOURCE

WORK_SETTLE_SECONDS = 0.25
WORK_POLL_SECONDS = 0.2
KEYBOARD_MIC_LEASE_SECONDS = 4


def match_rule(rules, kind, value):
    return next(
        (
            r
            for r in rules
            if r.get("enabled")
            and r["kind"] == kind
            and fnmatch.fnmatchcase(value.lower(), r["pattern"].lower())
        ),
        None,
    )


def integer(value, low, high):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not low <= value <= high
        or not math.isfinite(value)
        or int(value) != value
    ):
        raise ValueError(f"Enter a whole number from {low} to {high}.")
    return int(value)


def service_url(value):
    if not value:
        return value
    p = urlparse(value)
    if (
        p.scheme not in ("http", "https")
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
    ):
        raise ValueError("Use an HTTP(S) service URL without embedded credentials.")
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        try:
            private = ipaddress.ip_address(p.hostname).is_private
        except ValueError:
            private = p.hostname.endswith(".local")
        if not private:
            raise ValueError("Remote services need HTTPS; HTTP is allowed on your LAN.")
    return value.rstrip("/")


class Controller:
    def __init__(self, store, data_dir, clock=time.monotonic):
        from .voice import Voice
        from .hardware import Simulator
        from .pi_bridge import PiBridge
        from .casino import CasinoService
        from .interception import InterceptionCoordinator, PreservingWindows
        from .deadline_sync import DeadlineSync

        self.store, self.data_dir, self.clock = store, data_dir, clock
        self.store.cancel_active_games()
        self.casino = CasinoService(store)
        self.deadline_sync = DeadlineSync(store, data_dir)
        self.lock = threading.RLock()
        self.session, self.expires = "stopped", 0
        self.pending_work, self.browser, self.browser_identity = None, None, None
        self._interruption_work = None
        self._work_turn_attempt = None
        self._browser_revision = 0
        self.browser_url, self.browser_window = None, None
        self.browser_target = None
        from .browser_tabs import BrowserTabCommands
        from .native_browser_tabs import NativeBrowserTabs
        self.browser_tabs = BrowserTabCommands(self.lock)
        self.native_browser_tabs = NativeBrowserTabs()
        self.character_clock = "snack thirty"
        self.cover, self.uncover = None, lambda: None
        self.minimized, self.alive = set(), True
        self.watcher_enabled = False
        self.watcher_running = False
        self.work_detection = {"status": "stopped", "is_work": False, "label": None,
                               "source": None, "reason": "Start a session to detect work."}
        from .work_recognition import WorkRecognizer
        self.work_recognizer = WorkRecognizer(store.settings, clock=clock)
        self.voice = Voice(data_dir, store.settings)
        self.audio = AudioPipeline(store, data_dir / "models" / "yamnet")
        self.audio.score_allowed = lambda: self.alive and (self.session != "running" or self.clock() < self.expires)
        self.pi_bridge = PiBridge(store, self.audio)
        self.pi_bridge.score_allowed = self.audio.score_allowed
        self.simulator = Simulator(store, self.voice, self.audio)
        self.agent = LocalAgent(store, self.context, self.tool, self.voice, managed_work_dialogue=True)
        from .microphone import ConversationMicrophone
        self.microphone = ConversationMicrophone(
            self.audio, store.settings, store.profile, self._microphone_transcript,
            suppress=lambda: self.voice.info().get("speaking", False),
        )
        self._held_microphone = None
        self._ended_microphone_holds = deque(maxlen=64)
        self.browser_token = store.settings().get(
            "browser_token"
        ) or secrets.token_urlsafe(32)
        store.setting("browser_token", self.browser_token)
        self.service_tokens = {
            "pi_token": os.environ.get("CHUDDY_PI_TOKEN", ""),
            "friends_token": os.environ.get("CHUDDY_FRIENDS_TOKEN", ""),
        }
        self._read_secrets()
        self.desktop_overlay = None
        self.distractions = InterceptionCoordinator(
            allowed=lambda: self.alive and self.session == "running" and self.clock() < self.expires,
            cooldown=lambda: self.store.settings()["cooldown"],
            windows=PreservingWindows(), clock=self.clock, lock=self.lock,
            cover=lambda: self.cover() if self.cover else None, uncover=lambda: self.uncover(),
            close_desktop=True, local_ready=lambda: self.cover is not None,
            speak=lambda text: self.say(text, BANTER_OUTCOME_SOURCE),
            terminal_dialogue=True,
            on_dialogue_error=lambda error: self.store.log("work dialogue", error),
            recent_banter=lambda: [row["text"] for row in self.store.messages() if row["role"] == "assistant"],
            warning_seconds=0, close_tab=self._close_browser_tab, prepare_tab=self._prepare_browser_tab,
        )
        self.deadline_sync.start()
        self.earnings_error = None
        from .earning_clock import awake_monotonic
        earning_clock = awake_monotonic if clock is time.monotonic else clock
        # Production uses awake time; fixtures retain their injected clock. Cap
        # any final interval at session expiry without mixing clock epochs.
        self.store.bind_passive_runtime(lambda: earning_clock() -
            (max(0, self.clock()-self.expires) if self.session == 'running' else 0))
        self._earnings_stop = threading.Event()
        self._earnings_worker = threading.Thread(target=self._earnings_loop, name='chuddy-earnings', daemon=True)
        self._earnings_worker.start()

    def _earnings_loop(self):
        # Settle without waiting for HTTP polling, microphone or local AI.
        while not self._earnings_stop.wait(0.5):
            self.earnings_tick()

    def earnings_tick(self):
        with self.lock:
            try:
                self.check_expiry()
                if self.alive and self.session == 'running':
                    self.store.passive_tick()
                    if self.store.passive_enabled:
                        self.earnings_error = None
            except Exception:
                self.earnings_error = 'Monies could not be saved. Retrying.'

    def _publish_work_detection(self, result):
        self.work_detection = {key: result.get(key) for key in
            ("status", "is_work", "label", "source", "reason", "activity")}
        try:
            self.store.set_passive_work_detected(bool(result.get("is_work") or result.get("reason") == "protected_app"))
        except Exception:
            self.earnings_error = 'Monies could not be saved. Retrying.'

    @property
    def interruption(self):
        return self.distractions.info()

    def _read_secrets(self):
        path = self.data_dir / "service-secrets.bin"
        if path.exists():
            try:
                from .secrets import unprotect

                self.service_tokens.update(
                    json.loads(unprotect(path.read_bytes()).decode("utf-8"))
                )
            except Exception:
                self.store.log("secrets", "Could not open saved tokens for this user.")

    def _save_secrets(self):
        from .secrets import protect

        path = self.data_dir / "service-secrets.bin"
        temp = path.with_suffix(".tmp")
        temp.write_bytes(protect(json.dumps(self.service_tokens).encode("utf-8")))
        temp.replace(path)

    def context(self):
        return {
            "session": self.session,
            "deadlines": self.store.deadlines()[:6],
            "scores": {"fart": self.store.scores()["fart"]},
            "mock_balance": self.store.balance(),
            "economy": self.store.passive_info(),
            "memory": self.store.memories()[:12],
            "work": self.pending_work["label"] if self.pending_work else None,
            "beer_joke_enabled": self.store.settings()["beer_joke"],
            "character_clock": self.character_clock,
            "distraction_destination": self.store.settings()["distraction_destination"],
            "recent_work_lines": [row["text"] for row in self.store.messages()
                                  if row["role"] == "assistant"][-8:],
        }

    def _microphone_transcript(self, text, generation, profile_id):
        """Final admission for captured speech; late/profile-switched audio is inert."""
        with self.lock:
            held = self._held_microphone
            if held and held["generation"] == generation and self.clock() >= held["deadline"]:
                return {"cancelled": True, "text": ""}
            if (not self.alive or not self.microphone.current(generation, profile_id)
                    or self.store.profile()["id"] != profile_id):
                return {"cancelled": True, "text": ""}
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                return {"text": ""}
            return self.agent.begin(text[:2000])

    def _finish_microphone(self, generation=None):
        result = (self.microphone.finish() if generation is None else
                  self.microphone.finish(expected_generation=generation))
        if result.get("cancelled"):
            return result
        accepted = self._microphone_transcript(result.get("text", ""), result.get("generation"), result.get("profile_id"))
        return {**result, **accepted}

    def _release_microphone_hold(self, held):
        self._held_microphone = None
        held["done"].set()

    def _watch_microphone_hold(self, held):
        while not held["done"].is_set():
            with self.lock:
                if self._held_microphone is not held:
                    return
                if not self.alive or held["generation"] != self.microphone.generation:
                    self._release_microphone_hold(held)
                    return
                remaining = held["deadline"] - self.clock()
                if remaining <= 0:
                    self._release_microphone_hold(held)
                    self.microphone.stop(preserve_scoring=True)
                    return
            held["done"].wait(remaining)

    def _keyboard_microphone(self, operation, payload):
        token = payload.get("hold_id")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", token):
            raise ValueError("Invalid microphone hold ID.")
        cancelled = {"cancelled": True}
        with self.lock:
            held = self._held_microphone
            generation = self.microphone.generation
            owns = held is not None and held["id"] == token and held["generation"] == generation
            if operation == "hold_start":
                expected = payload.get("generation")
                if (not self.alive or token in self._ended_microphone_holds or owns
                        or type(expected) is not int or expected != generation
                        or payload.get("profile_id") != self.store.profile()["id"]
                        or self.store.settings()["microphone_mode"] != "push_to_talk"):
                    return cancelled
                info = self.microphone.info()
                if info["active"] or info["transcribing"]:
                    return cancelled
                self.agent.cancel(keep_assignment_exchange=True)
                result = self.microphone.start()
                held = self._held_microphone = {"id": token, "generation": result["generation"],
                                               "deadline": self.clock() + KEYBOARD_MIC_LEASE_SECONDS,
                                               "released": False, "done": threading.Event()}
                threading.Thread(target=self._watch_microphone_hold, args=(held,), daemon=True).start()
                return result
            if operation in ("hold_finish", "hold_cancel"):
                # A release/cancel may reach the service before its Start.
                self._ended_microphone_holds.append(token)
            if not owns:
                return cancelled
            if operation == "hold_cancel" or self.clock() >= held["deadline"]:
                self._release_microphone_hold(held)
                self.microphone.stop(preserve_scoring=True)
                return cancelled
            if operation == "hold_keepalive":
                held["deadline"] = self.clock() + KEYBOARD_MIC_LEASE_SECONDS
                return {"accepted": True}
            if held["released"]:
                return cancelled
            held["released"] = True
        try:
            return self._finish_microphone(generation)
        finally:
            with self.lock:
                if self._held_microphone is held:
                    self._release_microphone_hold(held)

    def tool(self, name, args, cancel, turn_id, user_text, observation):
        if not isinstance(args, dict) or len(json.dumps(args)) > 2000:
            raise ValueError("Invalid tool arguments.")
        with self.lock:
            if cancel.is_set() or self.agent.info()["turn_id"] != turn_id:
                return {"ok": False, "error": "turn cancelled"}
            if name == "read_context":
                return {"ok": True, "context": self.context()}
            if name == "remember_preference":
                request = user_text.lower().strip()
                denied = re.search(
                    r"\b(?:do not|don't|never)\s+(?:remember|save|store)\b", request
                )
                permission = re.search(
                    r"^(?:(?:hey|chuddy|okay|please)[,.!\s]*)*(?:remember|keep in mind|don't forget)\s+|\b(?:i want you to|i would like you to|can you|could you|please)\s+remember\s+",
                    request,
                )
                if observation or denied or not permission:
                    return {
                        "ok": False,
                        "error": "User must explicitly request memory.",
                    }
                key, value = args.get("key"), args.get("value")
                if (
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or not 1 <= len(key) <= 120
                    or not 1 <= len(value) <= 300
                ):
                    raise ValueError("Memory needs a short key and value.")
                if security_related(" ".join((user_text, key, value))) or any(
                    w in (key + " " + value + " " + user_text).lower()
                    for w in (
                        "password",
                        "security answer",
                        "bank account",
                        "credit card",
                        "social insurance",
                        "ssn",
                        "api key",
                    )
                ):
                    return {
                        "ok": False,
                        "error": "Only ordinary non-sensitive preferences may be stored.",
                    }
                return {"ok": True, "memory": self.store.remember(key, value)}
            if name == "set_character_clock":
                label = args.get("label", "")
                if not isinstance(label, str) or not 1 <= len(label) <= 60:
                    raise ValueError("Use a short fictional clock label.")
                self.character_clock = label
                return {"ok": True, "fictional_clock_only": label}
            if name == "request_distraction":
                configured = self.store.settings()["distraction_destination"]
                destination = args.get("destination", configured)
                if (
                    ("game" in args and args["game"] != "clam-catch")
                    or destination != configured
                    or not isinstance(args.get("reason", ""), str)
                    or len(args.get("reason", "")) > 200
                ):
                    raise ValueError("Invalid included-game request.")
                # A fast policy action may already be underway before dialogue
                # reaches its tool call. Report that same turn's actual action;
                # do not start another performance or pretend the model chose it.
                attempt = self._work_turn_attempt
                if (observation and attempt and self.session == "running"
                        and attempt['turn_id'] == turn_id
                        and attempt['receipt']['destination'] == destination):
                    receipt = attempt['receipt']
                    phase = receipt['phase']
                    if phase in ('error', 'cancelled'):
                        return {"ok": False, "phase": phase, "target_close": receipt.get('target_close'),
                                "error": receipt.get("error_detail") or "The distraction was cancelled."}
                    return {"ok": True, "destination": destination,
                            "game": "clam-catch" if destination == "game" else destination,
                            "already_started": True, "phase": phase, "target_close": receipt.get('target_close'),
                            "decision_source": attempt["decision_source"],
                            "reason": ("Distraction opened" if phase == "opened"
                                       else "This observation's distraction is in progress")}
                work = self.pending_work
                if (not work or self.clock() - work["seen"] > 8
                        or (observation and work.get("turn_id") != turn_id)):
                    return {"ok": False, "error": "Work observation expired."}
                if work and not self._work_still_active(work):
                    self.pending_work = None
                    return {"ok": False, "error": "The user left that work window."}
                accepted = self.interrupt(
                    work["label"] if work else "A little break",
                    work.get("window") if work else None,
                    source="local agent decision",
                    destination=destination, cancel=cancel,
                )
                return {
                    "ok": accepted,
                    "game": ("clam-catch" if destination == "game" else destination) if accepted else None,
                    "destination": destination if accepted else None,
                    "reason": (
                        "Distraction performance started"
                        if accepted
                        else "Session/cooldown does not allow interruption"
                    ),
                }
            raise ValueError("Tool is not permitted.")

    def say(self, text, source="offline script"):
        self.store.message("assistant", text, source)
        if self.store.settings()["voice"]:
            self.voice.say(text)

    def set_session(self, action):
        with self.lock:
            if action in ("start", "resume"):
                if action == "resume" and self.session != "paused":
                    raise ValueError("Start a session first.")
                self.session, self.expires = "running", math.inf
                try:
                    self.store.passive_start()
                    self.earnings_error = None
                except Exception:
                    self.earnings_error = 'Monies could not start. Pause and resume to retry.'
                self.work_detection = {"status": "watching", "is_work": False, "label": None,
                                       "source": None, "reason": "Waiting for a foreground work app."}
                if self.desktop_overlay:
                    self.desktop_overlay.roam()
            elif action in ("pause", "stop"):
                try:
                    stopped_at = int(self.store.earning_clock()*1000)
                except Exception:
                    stopped_at = None
                self.audio.cancel_scores()
                self.pi_bridge.stop()
                # Invalidate before waiting on DB/device cleanup. The final
                # settlement uses admission time, never time spent stopping.
                was_earning = self.store.passive_enabled
                self.store.passive_enabled = False
                self.session = (
                    "paused"
                    if action == "pause" and self.session != "stopped"
                    else "stopped"
                )
                self.distractions.stop(end_session=True)
                self.browser_tabs.cancel()
                self.native_browser_tabs.cancel()
                self.pending_work = None
                self._interruption_work = None
                self._work_turn_attempt = None
                self.work_recognizer.cancel()
                self.work_detection = {"status": self.session, "is_work": False, "label": None,
                                       "source": None, "reason": "Work detection is paused." if self.session == "paused" else "Start a session to detect work."}
                self.store.cancel_active_games()
                self.agent.cancel()
                self.microphone.stop()
                self.simulator.stop()
                self.restore_work()
                self._pi_stop()
                try:
                    if was_earning:
                        self.store.passive_stop(at_ms=stopped_at)
                except Exception:
                    self.earnings_error = 'The last monies update could not be saved.'
            else:
                raise ValueError("Unknown session action.")
            self.store.log("session", self.session)
            return {"session": self.session}

    def restore_work(self):
        legacy_windows = bool(self.minimized)
        for handle in self.minimized:
            windows.restore(handle)
        self.minimized.clear()
        # The coordinator owns its cover and asynchronous cleanup Future. A
        # second unconditional uncover invalidates that acknowledged request.
        if legacy_windows:
            self.uncover()

    def dismiss(self):
        with self.lock:
            self.distractions.stop()
            self.browser_tabs.cancel()
            self.native_browser_tabs.cancel()
            self.pending_work = None
            self._interruption_work = None
            self.agent.cancel()
            self.restore_work()
            if self.session == "running" and self.desktop_overlay:
                self.desktop_overlay.roam()
        return {"dismissed": True}

    def check_expiry(self):
        if self.session == "running" and self.clock() >= self.expires:
            self.set_session("stop")

    def _focused_browser_target(self, window=None):
        """Return only a fresh paired report bound to this exact work window.

        A title-rule observation can arrive before the extension heartbeat. Its
        work record has no tab identity yet; a later report may strengthen that
        same observation, but must never substitute a different document/window.
        """
        if (not self.browser or not self.browser_target or not self.browser_window):
            return None, 'No fresh paired work-tab report. Reload, pair and enable the updated browser extension.'
        if (not 0 <= self.clock() - self.browser['seen'] <= 8
                or not self.distractions.fresh_browser_report(self.browser.get('sent_at', 0))):
            return None, 'The paired work-tab report expired. Keep the selected work tab focused.'
        target = self.browser_target
        if (not isinstance(target.get('tab_id'), int) or not target.get('client_id')
                or not isinstance(target.get('window_id'), int) or not target.get('address_id')
                or target.get('close_version') != 2):
            return None, 'Reload the updated browser extension, pair it and enable work-tab control.'
        if not windows.same_foreground(window, self.browser_window):
            return None, 'The focused document changed. Waiting for its matching browser report.'
        return dict(target), 'The paired browser extension is ready for this work tab.'

    def browser_tab_control(self):
        with self.lock:
            current = windows.foreground()
            target, reason = self._focused_browser_target(current)
            if target is not None and (not current or str(current.get('process', '')).lower() not in BROWSERS):
                target, reason = None, 'Keep the selected work tab focused for browser control.'
            if target is not None:
                return {'ready': True, 'status': 'ready', 'mode': 'extension', 'reason': reason}
            native = self.native_browser_tabs.info()
            return {'ready': bool(native.get('ready')), 'available': bool(native.get('available')),
                    'status': native.get('status', 'unavailable'), 'mode': 'native',
                    'reason': native.get('reason') or 'Chrome tabs are closed directly. No extension is required.'}

    def _prepare_browser_tab(self, target, valid, live):
        rules = self.store.settings()['rules']
        return self.native_browser_tabs.prepare_async(target['window'], valid, live,
            policy=lambda window, url: evaluate(window, rules, url=url)['is_work'])

    def _close_browser_tab(self, target, valid, live):
        if target and target.get('kind') == 'native':
            return self.native_browser_tabs.request(target, valid, live)
        return self.browser_tabs.request(target, valid, live)

    def interrupt(self, label, window=None, test=False, source="offline rule", destination=None, cancel=None):
        with self.lock:
            self.check_expiry()
            if (
                self.session != "running"
                or self.distractions.cooldown_remaining() > 0
            ):
                return False
            work = dict(self.pending_work) if self.pending_work else None
            if not test and not work:
                return False
            work_window = (work or {}).get('window') or window
            if not test and work_window:
                browser_fresh = self._fresh_browser_window(work_window)
                decision = self.work_recognizer.observe(work_window, self.store.settings()['rules'],
                    url=self.browser_url if browser_fresh else None, allow_ai=False)
                if not decision.get('is_work'):
                    self._publish_work_detection(decision)
                    self.pending_work = None
                    return False
            tab_target = None
            if not test and work_window and str(work_window.get('process', '')).lower() in BROWSERS:
                tab_target, reason = self._focused_browser_target(work_window)
                if work.get('origin') == 'browser' and work.get('browser_identity') != self.browser_identity:
                    self.work_detection.update(status='browser_unavailable', is_work=True,
                        label=work.get('label'), reason='The focused work tab changed. Waiting for its matching observation.')
                    return False
                if tab_target is not None:
                    work['origin'] = 'browser'
                    work['browser_identity'] = self.browser_identity
                else:
                    native = self.native_browser_tabs.info()
                    if not native.get('available'):
                        self.work_detection.update(status='browser_unavailable', is_work=True,
                            label=work.get('label'), reason=native.get('reason') or 'Native browser tab controls are unavailable.')
                        return False
                    tab_target = {'kind': 'native', 'window': dict(work_window)}
                    work['browser_revision'] = self._browser_revision
            result = self.distractions.request(
                label, destination=destination or self.store.settings()["distraction_destination"],
                window=work_window,
                seen=(work or {}).get("seen"),
                active=(lambda work=work: self._continued_work_active(work)) if work else None,
                mode=self.store.settings()["interception"], test=test, cancel=cancel,
                tab_target=tab_target,
            )
            if not result["ok"]:
                return False
            self._interruption_work = dict(work, decision_source=source) if work else None
            if not test and work.get('turn_id'):
                self._work_turn_attempt = {'turn_id': work['turn_id'],
                    'decision_source': source, 'receipt': self.distractions.current}
            self.pending_work = None
            self.store.log(
                "interruption", ("test: " if test else "") + label + " · " + source
            )
            return True

    def _work_still_active(self, work):
        if self.clock() - work["seen"] > 8:
            return False
        return self._continued_work_active(work)

    def _fresh_browser_window(self, window):
        return bool(self.browser and 0 <= self.clock() - self.browser['seen'] <= 8
                    and self.distractions.fresh_browser_report(self.browser.get('sent_at', 0))
                    and windows.same_foreground(window, self.browser_window))

    def _continued_work_active(self, work):
        """Keep the original identity and dispatch route throughout the approach."""
        if work.get('browser_revision', self._browser_revision) != self._browser_revision:
            return False
        window = work.get("window")
        if work.get("origin") == "browser" and (
            work.get("browser_identity") != self.browser_identity
            or (window is not None and not self._fresh_browser_window(window))
        ):
            return False
        if window:
            current = windows.foreground()
            return (windows.same_foreground(current, window) and not current.get('is_transient')
                    and not self.distractions.is_destination_window(current))
        return True

    def _clear_browser_work(self):
        if self.pending_work and self.pending_work.get("origin") == "browser":
            turn = self.pending_work.get("turn_id")
            self.pending_work = None
            if turn == self.agent.info()["turn_id"]:
                self.agent.cancel()

    def observe_work(self, label, window=None, origin="desktop"):
        with self.lock:
            self.check_expiry()
            if not self.distractions.permits_foreground(window):
                return False
            self.distractions.retire_completed()
            pending = self.pending_work
            if pending and pending["origin"] == origin and pending["label"] == label and self._work_still_active(pending):
                # Called only with a new actual watcher/extension observation.
                pending["seen"] = self.clock()
                return False
            if (
                self.session != "running"
                or self.pending_work
                or self.interruption
                or self.distractions.cooldown_remaining() > 0
            ):
                return False
            self.pending_work = {
                "label": label,
                "window": dict(window) if window else None,
                "seen": self.clock(),
                "created": self.clock(),
                "origin": origin,
                "browser_identity": self.browser_identity if origin == "browser" else None,
            }
            if self.agent.info()["status"] not in ("thinking", "warming"):
                result = self.agent.begin(
                    f"The user is working in {label}. Request the configured distraction promptly.",
                    observation=True,
                )
                self.pending_work["turn_id"] = result["turn_id"]
            else:
                self.pending_work["turn_id"] = None
            return True

    def poll_work(self):
        """One foreground observation; no history or inactive-window scan."""
        with self.lock:
            self.check_expiry()
            if self.session != "running" or not self.alive:
                return
            work = self.pending_work
            if work and not self._work_still_active(work):
                self.pending_work = None
                if work.get("turn_id") == self.agent.info()["turn_id"]:
                    self.agent.cancel()
                work = None
            window = windows.foreground()
            self.distractions.observe_foreground(window)
            if window and window["pid"] != os.getpid():
                rules = self.store.settings()["rules"]
                browser_fresh = self._fresh_browser_window(window)
                metadata = (dict(window, is_chuddy_destination=True)
                            if self.distractions.is_destination_window(window) else window)
                result = self.work_recognizer.observe(metadata, rules,
                    url=self.browser_url if browser_fresh else None,
                    idle_seconds=windows.input_idle_seconds(),
                    allow_ai=self.agent.info()["status"] not in ("thinking", "warming"))
                self._publish_work_detection(result)
                if result.get("is_work"):
                    self.observe_work(result["label"],window, origin="browser" if browser_fresh else "desktop")
                else:
                    pending = self.pending_work
                    if pending and (not pending.get("window") or all(
                            pending["window"].get(key) == window.get(key) for key in ("handle", "pid", "title"))):
                        self.pending_work = None
                        if pending.get("turn_id") == self.agent.info().get("turn_id"):
                            self.agent.cancel()
                    # A new negative classification also invalidates an already
                    # queued approach before its native Close operation.
                    source = self._interruption_work
                    same_source = bool(source and source.get('window') and all(
                        source['window'].get(key) == window.get(key) for key in ('handle', 'pid', 'title')))
                    if same_source and self.interruption and self.interruption.get("phase") in ("preparing", "warning", "approaching"):
                        self.distractions.stop()
                        self.browser_tabs.cancel()
                        self.native_browser_tabs.cancel()
                        self._interruption_work = None
            else:
                pending, self.pending_work = self.pending_work, None
                if pending and pending.get("turn_id") == self.agent.info().get("turn_id"):
                    self.agent.cancel()
                self._publish_work_detection({"status": "watching", "is_work": False, "label": None,
                    "source": None, "reason": "Chuddy is in the foreground."})
            work = self.pending_work
            if (work and self.clock()-work.get("created",work["seen"]) >= WORK_SETTLE_SECONDS
                    and self._work_still_active(work)):
                # Dialogue is concurrent; native preflight/ack remain authoritative.
                # Fresh category exclusions above always precede this fallback.
                self.interrupt(work["label"],work.get("window"),source="work detection fallback")

    def watch(self):
        self.watcher_running = True
        try:
            while self.alive:
                try:
                    self.poll_work()
                except Exception as error:
                    self.work_detection = {"status": "error", "is_work": False, "label": None,
                                           "source": None, "reason": type(error).__name__}
                    self.store.log("watcher error",type(error).__name__)
                time.sleep(WORK_POLL_SECONDS)
        finally:
            self.watcher_running = False

    def browser_event(self, payload):
        with self.lock:
            self.check_expiry()
            client_id = payload.get('client_id')
            if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 100):
                raise ValueError('Invalid browser instance identity.')
            if 'command_claim' in payload:
                return self.browser_tabs.claim(payload['command_claim'], client_id)
            if 'command_result' in payload:
                return self.browser_tabs.complete(payload['command_result'], client_id, payload.get('closed'))
            if payload.get("active") is not True or payload.get("focused") is not True:
                if client_id and self.browser_target and client_id != self.browser_target['client_id']:
                    return {"matched": False, "interrupted": False}
                if self.browser_identity is not None:
                    self._browser_revision += 1
                self.browser = None
                self.browser_identity = None
                self.browser_url, self.browser_window = None, None
                self.browser_target = None
                self._clear_browser_work()
                return {"matched": False, "interrupted": False}
            sent = payload.get("sent_at")
            if (
                isinstance(sent, bool)
                or not isinstance(sent, (int, float))
                or abs(time.time() * 1000 - sent) > 8000
                or not math.isfinite(sent)
            ):
                raise ValueError("Stale browser report.")
            if not self.distractions.fresh_browser_report(sent):
                return {"matched": False, "interrupted": False}
            window = windows.foreground()
            if self.distractions.is_destination_window(window) or (os.name == "nt" and (
                not window or window.get('is_transient') or window["process"].lower() not in BROWSERS
            )):
                return {"matched": False, "interrupted": False}
            url = payload.get("url", "")
            if not isinstance(url, str) or len(url) > 4096:
                raise ValueError("Invalid URL report.")
            p = urlparse(url)
            if (
                p.scheme not in ("http", "https")
                or not p.hostname
                or p.username
                or p.password
            ):
                raise ValueError("Only ordinary web URLs are supported.")
            sanitized = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
            tab_id = payload.get("tab_id")
            if tab_id is not None and (isinstance(tab_id, bool) or not isinstance(tab_id, int) or not 0 <= tab_id <= 2**53 - 1):
                raise ValueError("Invalid active tab identity.")
            window_id = payload.get('window_id')
            if window_id is not None and (type(window_id) is not int or not 0 <= window_id <= 2**53 - 1):
                raise ValueError('Invalid browser window identity.')
            close_version = payload.get('close_version')
            address_id = payload.get('address_id')
            if address_id is not None and (not isinstance(address_id, str) or not re.fullmatch(r'[0-9a-f]{64}', address_id)):
                raise ValueError('Invalid browser address identity.')
            identity = hashlib.sha256(json.dumps(
                (sanitized, address_id, tab_id, window_id, client_id, windows.window_key(window)),
                separators=(',', ':')).encode('utf-8')).hexdigest()
            if self.browser_identity != identity:
                self._browser_revision += 1
                self._clear_browser_work()
                # A newly paired observation supersedes a title-only candidate.
                # It must enter under its own turn, not inherit the old one.
                pending = self.pending_work
                if pending and windows.same_foreground(window, pending.get('window')):
                    self.pending_work = None
                    if pending.get('turn_id') == self.agent.info().get('turn_id'):
                        self.agent.cancel()
            self.browser_identity = identity
            self.browser = {"host": p.hostname, "seen": self.clock(), "sent_at": sent}
            self.browser_url, self.browser_window = sanitized, dict(window) if window else None
            self.browser_target = {'tab_id': tab_id, 'window_id': window_id, 'url': sanitized,
                                   'address_id': address_id, 'client_id': client_id, 'close_version': close_version}
            rules = self.store.settings()["rules"]
            recognition = self.work_recognizer.observe(window or {}, rules, url=sanitized,
                idle_seconds=windows.input_idle_seconds(), allow_ai=self.session == "running"
                    and self.agent.info()["status"] not in ("thinking", "warming"))
            matched = bool(recognition.get("is_work"))
            if self.session == "running":
                self._publish_work_detection(recognition)
            if not matched:
                self._clear_browser_work()
            return {
                "matched": matched,
                "interrupted": bool(
                    matched and self.observe_work(recognition["label"], window, origin="browser")
                ),
                "command": self.browser_tabs.offer(client_id),
            }

    def settings(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Provide settings as an object.")
        allowed = {
            "name",
            "cooldown",
            "voice",
            "beer_joke",
            "interception",
            "distraction_destination",
            "reduced_motion",
            "ai_enabled",
            "ai_work_detection",
            "ai_endpoint",
            "ai_model",
            "voice_engine",
            "friends_url",
            "friends_room",
            "pi_url",
            "audio_threshold",
            "audio_device",
            "microphone_mode",
            "rules",
        }
        clean, secret_changes = {}, {}
        for key, value in payload.items():
            if key in self.service_tokens:
                if not isinstance(value, str) or len(value) > 2000:
                    raise ValueError("Invalid service token.")
                if value:
                    secret_changes[key] = value
                continue
            if key not in allowed:
                raise ValueError("Unknown setting: " + key)
            if key in ("voice", "beer_joke", "ai_enabled", "ai_work_detection", "reduced_motion") and not isinstance(
                value, bool
            ):
                raise ValueError("Expected an on/off value.")
            if key == "cooldown":
                value = integer(value, 0, 3600)
            if key == "audio_threshold" and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.01 <= value <= 1
                or not math.isfinite(value)
            ):
                raise ValueError("Audio threshold must be 0.01 to 1.")
            if key == "audio_device" and value is not None:
                value = integer(value, 0, 1000)
            if key == "microphone_mode" and value not in ("push_to_talk", "always_listening"):
                raise ValueError("Choose Push to talk or Always listening.")
            if key == "interception" and value not in ("cover", "minimize"):
                raise ValueError("Choose cover or minimize.")
            if key == "distraction_destination" and value not in DESTINATIONS:
                raise ValueError("Choose a supported interruption destination.")
            if key == "rules":
                if not isinstance(value, list) or len(value) > 100:
                    raise ValueError("Provide at most 100 rules.")
                for r in value:
                    if (
                        not isinstance(r, dict)
                        or r.get("kind") not in ("url", "process", "window")
                        or not isinstance(r.get("enabled"), bool)
                        or not all(
                            isinstance(r.get(k), str) and 0 < len(r[k]) <= 250
                            for k in ("pattern", "label")
                        )
                    ):
                        raise ValueError("Rules need kind, pattern, label and enabled.")
            if key in (
                "name",
                "ai_endpoint",
                "ai_model",
                "voice_engine",
                "friends_url",
                "friends_room",
                "pi_url",
            ):
                if (
                    not isinstance(value, str)
                    or len(value) > 500
                    or (
                        key in ("name", "friends_room", "ai_model")
                        and not value.strip()
                    )
                ):
                    raise ValueError("Invalid text setting.")
                if key == "ai_endpoint":
                    value = local_endpoint(value)
                if key in ("pi_url", "friends_url"):
                    value = service_url(value)
                if key == "ai_model" and (
                    ":cloud" in value.lower() or "-cloud" in value.lower()
                ):
                    raise ValueError(
                        "Cloud models are excluded; select a downloaded model."
                    )
            clean[key] = value
        with self.lock:
            current = self.store.settings()
            if any(key in clean and clean[key] != current.get(key)
                   for key in ("audio_device", "microphone_mode")):
                self.microphone.stop()
            if any(key in clean and clean[key] != current[key] for key in ("ai_enabled", "ai_model", "ai_endpoint", "ai_work_detection")):
                self.agent.cancel()
                self.work_recognizer.cancel()
            if any(key in clean and clean[key] != current[key] for key in ("distraction_destination", "interception", "rules", "ai_enabled", "ai_model", "ai_endpoint", "ai_work_detection")):
                self.distractions.stop()
                self.browser_tabs.cancel()
                self.native_browser_tabs.cancel()
                self.pending_work = None
                self._interruption_work = None
                self.agent.cancel()
                self.work_recognizer.cancel()
            self.store.update_settings(clean)
            if secret_changes:
                self.service_tokens.update(secret_changes)
                self._save_secrets()
            if "pi_url" in clean or "pi_token" in secret_changes:
                self.pi_bridge.stop()
            if clean.get("voice") is False:
                self.voice.stop()
            if "reduced_motion" in clean and self.desktop_overlay:
                self.desktop_overlay.reduced_motion = clean["reduced_motion"]
            if self.desktop_overlay and self.session == "running" and any(key in clean for key in ("distraction_destination", "interception", "rules", "ai_enabled", "ai_model", "ai_endpoint", "ai_work_detection")):
                self.desktop_overlay.roam()
        return {"saved": True}

    def _pi_stop(self):
        if not self.store.settings().get("pi_url"):
            return

        def remote_stop():
            try:
                self.pi_command("stop")
            except Exception:
                pass

        threading.Thread(target=remote_stop, daemon=True).start()

    def pi_command(self, command=None):
        from .pi_client import PiClient

        url = self.store.settings()["pi_url"]
        if not url:
            raise ValueError("Configure a local Pi address first.")
        client = PiClient(url, self.service_tokens["pi_token"])
        if command is None:
            return client.status()
        if command == "belly":
            return client.belly()
        if command == "stop":
            return client.stop()
        return client.command(command)

    def action(self, p):
        if not isinstance(p, dict):
            raise ValueError("Expected a JSON object.")
        action = p.get("action")
        if action == "chat":
            return self.agent.begin(p.get("text"))
        if action == "cancel_turn":
            self.agent.cancel()
            return {"cancelled": True}
        if action == "session":
            return self.set_session(p.get("operation"))
        if action == "dismiss":
            return self.dismiss()
        if action == "casino_leave":
            return {"left": self.distractions.leave_casino(p.get("opening_id"))}
        if action == "test_trigger":
            triggered = self.interrupt("Sample report", test=True, source="manual test")
            return {"triggered": triggered}
        if action == "settings":
            return self.settings(p.get("values"))
        if action in ("deadline_connect", "deadline_disconnect", "deadline_refresh"):
            profile_id = p.get("profile_id")
            if action == "deadline_connect":
                return self.deadline_sync.connect(p.get("provider"), p.get("url"), profile_id)
            if action == "deadline_disconnect":
                return self.deadline_sync.disconnect(p.get("provider"), profile_id)
            return self.deadline_sync.refresh(profile_id)
        if action == "admin_cheat":
            with self.store.lock:
                if p.get("profile_id") != self.store.profile()["id"]:
                    raise ValueError("The selected profile changed. Reopen the cheat bar and try again.")
                return self.store.admin_money(p.get("command"), p.get("request_id"), p.get("profile_id"))
        if action == "deadline_add":
            return {"deadline": self.store.add_deadline(p.get("title"), p.get("due"))}
        if action == "deadline_import":
            return {"imported": self.store.import_deadlines(p.get("items"))}
        if action == "deadline_delete":
            return {"deleted": self.store.delete_deadline(p.get("id"))}
        if action == "deposit":
            return self.store.deposit(p.get("amount"), p.get("request_id"))
        if action == "spin":
            if "profile_id" in p:
                from .casino import spin_for_profile

                return spin_for_profile(self.store, p)
            return self.store.spin(p.get("bet"), p.get("request_id"))
        if action == "casino_start":
            return self.casino.start(p)
        if action == "casino_act":
            return self.casino.act(p)
        if action == "casino_state":
            return self.casino.state()
        if action in ("clam_game_start", "clam_game_finish", "clam_game_cancel"):
            with self.lock, self.store.lock:
                if p.get("profile_id") != self.store.profile()["id"]:
                    raise ValueError("The profile changed. Return to the original profile.")
                if action == "clam_game_start":
                    return self.store.start_game(p.get("request_id"))
                if action == "clam_game_cancel":
                    return self.store.cancel_game(p.get("round_id"))
                return self.store.reward_game(
                    p.get("round_id"), p.get("request_id"), p.get("caught")
                )
        if action == "shop":
            return self.store.purchase(p.get("item"), p.get("request_id"))
        if action == "score":
            with self.store.lock:
                counted = self.store.event(
                    p.get("request_id") or str(uuid.uuid4()), p.get("kind"), "manual"
                )
                return {"counted": counted, "earnings": self.store.earnings()}
        if action == "sound_test":
            kind = p.get("kind")
            if kind != "fart":
                raise ValueError("Only fart sounds are available.")
            with self.lock:
                accepted = self.audio.play_effect(self.voice, kind, "Chuddy speaker · deliberate sound test")
            return {"accepted": accepted}
        if action == "belly":
            with self.lock:
                return self.simulator.belly()
        if action == "mic":
            with self.lock:
                if p.get("operation") == "start":
                    self.microphone.stop()
                    self.audio.start()
                elif p.get("operation") == "stop":
                    self.microphone.stop()
                else:
                    raise ValueError("Choose start or stop.")
                return self.audio.info()
        if action == "microphone" or action in ("asr_start", "asr_finish"):
            operation = {"asr_start": "start", "asr_finish": "finish"}.get(action, p.get("operation"))
            if operation in ("hold_start", "hold_finish", "hold_cancel", "hold_keepalive"):
                return self._keyboard_microphone(operation, p)
            if operation == "stop":
                with self.lock:
                    return self.microphone.stop()
            if operation == "finish":
                with self.lock:
                    held = self._held_microphone
                    if held and held["generation"] == self.microphone.generation:
                        return {"cancelled": True, "text": ""}
                return self._finish_microphone()
            if operation != "start":
                raise ValueError("Choose start, finish or stop.")
            with self.lock:
                self.agent.cancel(keep_assignment_exchange=True)
                return self.microphone.start()
        if action == "voice_test":
            return self.voice.test()
        if action == "model_warm":
            self.voice.warm()
            return self.agent.warm()
        if action == "profile_add":
            return self.store.add_profile(p.get("name"))
        if action == "profile_select":
            with self.lock:
                self.set_session("stop")
                with self.store.lock:
                    profile = self.store.select_profile(p.get("id"))
                    self.store.cancel_active_games()
                    return profile
        if action == "memory_delete":
            return {"deleted": self.store.forget(p.get("id"))}
        if action == "friends_sync":
            from .friends import FriendsClient

            s = self.store.settings()
            return FriendsClient(self.store).sync(
                s["friends_url"],
                s["friends_room"],
                self.service_tokens["friends_token"],
            )
        if action == "pi_status":
            return self.pi_command()
        if action == "pi_connect":
            return self.pi_bridge.connect(
                self.store.settings()["pi_url"], self.service_tokens["pi_token"]
            )
        if action == "pi_disconnect":
            return self.pi_bridge.disconnect()
        if action == "pi_mic":
            operation = p.get("operation")
            if operation not in ("start", "stop"):
                raise ValueError("Choose start or stop.")
            return self.pi_bridge.microphone(operation == "start")
        if action == "pi_belly":
            return self.pi_command("belly")
        if action == "pairing":
            return {
                "token": self.browser_token,
                "url": getattr(self, "ui_url", "http://127.0.0.1:8765"),
            }
        raise ValueError("Unknown action.")

    def state(self):
        self.check_expiry()
        # Snapshot profile data together, including other SQLite connections.
        # Release the transaction before worker info() calls:
        # workers may take their own lock before reading Store settings.
        with self.store.transaction():
            s = self.store.settings()
            s.pop("browser_token", None)
            profile_state = {
                "settings": s,
                "messages": self.store.messages(),
                "scores": self.store.scores(),
                "balance": self.store.balance(),
                "earnings": self.store.earnings(),
                "casino": self.casino.state(),
                "catalog": self.store.catalog(),
                "deadlines": self.store.deadlines(),
                "logs": self.store.logs(),
                "profiles": self.store.profiles(),
                "profile": self.store.profile(),
                "memory": self.store.memories(),
                "leaderboard": self.store.leaderboard(),
                "leaderboard_periods": self.store.leaderboard_periods(),
                "deadline_sync": self.deadline_sync.info(),
            }
            if self.earnings_error:
                profile_state['earnings']['passive'].update(status='error', rate_per_second=0, error=self.earnings_error)
        with self.lock:
            session, expires = self.session, self.expires
            interruption = self.interruption
            casino_interruption_id = self.distractions.casino_interruption_id
            casino_opening_id = self.distractions.casino_opening_id
            browser = ({"host": self.browser["host"], "seen": self.browser["seen"]}
                       if self.browser and self.clock() - self.browser["seen"] < 15 else None)
        return {
            **profile_state,
            "session": session,
            "session_remaining": (
                None if math.isinf(expires) else max(0, int(expires - self.clock()))
                if session == "running"
                else 0
            ),
            "cooldown_remaining": math.ceil(self.distractions.cooldown_remaining()),
            "interruption": interruption,
            "casino_interruption_active": casino_interruption_id is not None,
            "casino_interruption_id": casino_interruption_id,
            "casino_opening_id": casino_opening_id,
            "desktop_interception": self.distractions.diagnostics(),
            "desktop_overlay": self.desktop_overlay.info() if self.desktop_overlay else {"status": "unavailable", "hotkey": False},
            "monitoring": {"desktop_enabled": self.watcher_enabled, "desktop_running": self.watcher_running},
            "work_detection": dict(self.work_detection),
            "browser_tab_control": self.browser_tab_control(),
            "native_window": self.native_window.info() if getattr(self, 'native_window', None) else {"status": "unavailable"},
            "hardware": self.simulator.info(),
            "pi_bridge": self.pi_bridge.info(),
            "audio": self.audio.info(),
            "microphone": self.microphone.info(),
            "voice": self.voice.info(),
            "agent": self.agent.info(),
            "browser": browser,
            "real_time": datetime.now(timezone.utc).isoformat(),
            "character_clock": self.character_clock,
            "has_pi_token": bool(self.service_tokens["pi_token"]),
            "has_friends_token": bool(self.service_tokens["friends_token"]),
        }

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self.set_session("stop")
        self._earnings_stop.set()
        self._earnings_worker.join(timeout=2)
        self.deadline_sync.close()
        self.distractions.close()
        self.native_browser_tabs.close()
        self.audio.close()
        self.voice.close()
