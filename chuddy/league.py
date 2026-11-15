"""Launch the installed League landing client through the normal Riot product entry."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time

from . import windows


RIOT_ARGS = ("--launch-product=league_of_legends", "--launch-patchline=live")
FOCUS_SETTLE_SECONDS = .75
WINDOW_STABLE_SECONDS = .18


@dataclass(frozen=True)
class LeagueInstall:
    launcher: Path
    game: Path

    @classmethod
    def validate(cls, launcher):
        launcher = Path(launcher)
        if (not launcher.is_absolute() or launcher.drive.startswith("\\\\")
                or (launcher.parent.name.casefold(), launcher.name.casefold())
                != ("riot client", "riotclientservices.exe")):
            return None
        try:
            launcher = launcher.resolve(strict=True)
            if ((launcher.parent.name.casefold(), launcher.name.casefold())
                    != ("riot client", "riotclientservices.exe")):
                return None
            game = (launcher.parent.parent / "League of Legends").resolve(strict=True)
            if (game.parent != launcher.parent.parent or game.name.casefold() != "league of legends"
                    or not all(path.is_file() for path in
                               (launcher, game / "LeagueClient.exe", game / "LeagueClientUx.exe"))):
                return None
            return cls(launcher, game)
        except (OSError, RuntimeError, ValueError):
            return None

    def application_paths(self):
        return {os.path.normcase(str(path)): role for path, role in (
            (self.launcher, "riot_client"),
            (self.launcher.parent / "RiotClientUx.exe", "riot_client"),
            (self.launcher.parent / "RiotClientElectron" / "Riot Client.exe", "riot_client"),
            (self.game / "LeagueClient.exe", "league_startup"),
            (self.game / "LeagueClientUx.exe", "league_client"),
            (self.game / "Game" / "League of Legends.exe", "match"),
        )}


class LeagueLauncher:
    def __init__(self, *, spawn=subprocess.Popen, desktop=windows):
        self.spawn, self.desktop = spawn, desktop

    def find(self):
        if os.name != "nt":
            return None
        candidates = []
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            metadata = Path(program_data) / "Riot Games" / "RiotClientInstalls.json"
            try:
                with metadata.open(encoding="utf-8-sig") as config:
                    launcher = json.loads(config.read(65536)).get("rc_live")
                if isinstance(launcher, str):
                    candidates.append(launcher)
            except (AttributeError, OSError, ValueError):
                pass
        candidates.append(Path("C:/Riot Games/Riot Client/RiotClientServices.exe"))
        for candidate in candidates:
            install = LeagueInstall.validate(candidate)
            if install:
                return install
        return None

    def prepare(self):
        install = self.find()
        if install is None:
            raise RuntimeError("A supported local Riot and League installation could not be verified.")
        if any(role == "match" for role, _ in LeagueLaunch(install, self.desktop).windows()):
            raise RuntimeError("A League match is already open.")
        return install

    def open(self, install):
        if LeagueInstall.validate(install.launcher) != install:
            raise RuntimeError("The verified League installation is no longer available.")
        launch = LeagueLaunch(install, self.desktop, start_product=lambda: self._start_product(install))
        current = launch.windows()
        if any(role == "match" for role, _ in current):
            raise RuntimeError("A League match is already open.")
        launch.reused = any(role == "league_client" for role, _ in current)
        if not launch.reused:
            launch.dispatch_product()
        return launch

    def _start_product(self, install):
        if LeagueInstall.validate(install.launcher) != install:
            raise RuntimeError("The verified League installation is no longer available.")
        try:
            self.spawn([str(install.launcher), *RIOT_ARGS],
                       cwd=install.launcher.parent, shell=False, close_fds=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise RuntimeError("Windows could not launch the installed Riot client.") from error


class LeagueLaunch:
    def __init__(self, install, desktop=windows, *, clock=time.monotonic,
                 startup_timeout=35, layout_timeout=1.25, start_product=None):
        self.paths = install.application_paths()
        self.desktop, self.clock = desktop, clock
        self.startup_timeout, self.layout_timeout = startup_timeout, layout_timeout
        self._start_product = start_product
        self.reused = False

    def dispatch_product(self):
        if self._start_product is None:
            return
        start, self._start_product = self._start_product, None
        start()
        self.reused = False

    def windows(self):
        result = []
        for window in self.desktop.application_windows(self.paths):
            role = self.paths.get(os.path.normcase(window['executable_path']))
            if role == "league_client" and window['title'].strip().casefold() != "league of legends":
                continue  # Same executable can own splash or utility windows.
            if role and role != "league_startup":
                result.append((role, window))
        return result

    @staticmethod
    def _select(candidates, focus):
        for preferred in ("league_client", "riot_client"):
            windows_for_role = [window for role, window in candidates if role == preferred]
            selected = next((window for window in windows_for_role
                             if windows.window_key(window) == focus), None)
            if selected is not None or len(windows_for_role) == 1:
                return preferred, selected or windows_for_role[0]
        return None, None

    def confirm(self, live, stop, lock, exposed, report, *, input_at_close=None):
        """Scope activation to the verified handoff and measure the landing client."""
        now = self.clock()
        deadline, settle_until = now + self.startup_timeout, now + FOCUS_SETTLE_SECONDS
        pending_focus, focus_since, missing_since = None, None, None
        attempts, layouts = set(), {}
        while live():
            candidates = self.windows()
            roles = {windows.window_key(window): role for role, window in candidates}
            with lock:
                if not live():
                    return None
                handle = self.desktop.foreground_handle()
                owner = self.desktop.window_owner(handle) if handle else None
                focus = (handle, *owner) if owner else None
                now = self.clock()
                role_at_focus = roles.get(focus)
                if focus and role_at_focus is None:
                    # A Riot/League splash can activate between EnumWindows
                    # and this sample. Check the current owner independently.
                    role_at_focus = self.paths.get(self.desktop.application_path(handle, owner))
                if role_at_focus == "match":
                    report("match", True, None, None)
                    raise RuntimeError("A League match is already open.")
                can_activate = not handle or role_at_focus is not None or (focus is not None and focus == exposed)
                if not can_activate:
                    if input_at_close is None or self.desktop.last_input_tick() != input_at_close:
                        report(None, False, None, None)
                        raise RuntimeError("Focus moved away during the League handoff.")
                    # Close acknowledgment can precede the OS selecting the
                    # stable revealed window. Give it a bounded chance to
                    # settle without new input. A later unrelated window may
                    # be observed, but cannot authorize an activation.
                    if now <= settle_until and focus is not None:
                        if focus != pending_focus:
                            pending_focus, focus_since = focus, now
                        elif now - focus_since >= WINDOW_STABLE_SECONDS:
                            exposed, can_activate = focus, True
                else:
                    pending_focus, focus_since = None, None

                has_landing = any(role == "league_client" for role, _ in candidates)
                if (self.reused and self._start_product is not None and not has_landing
                        and role_at_focus not in ("league_client", "league_startup")):
                    if missing_since is None:
                        missing_since = now
                    elif can_activate and now - missing_since >= WINDOW_STABLE_SECONDS:
                        if "match" in roles.values():
                            report("match", False, None, None)
                            raise RuntimeError("A League match is already open.")
                        self.dispatch_product()
                        deadline = self.clock() + self.startup_timeout
                        report(None, False, None, None)
                else:
                    missing_since = None

                role, window = self._select(candidates, focus)
                if window:
                    key = windows.window_key(window)
                    focused = focus == key
                    layout = (self.desktop.application_client_area(window)
                              if role == "league_client" else None)
                    covers = layout['coverage'] if layout is not None else None
                    sizing, layout_deadline = layouts.get(key, (None, None))
                    report(role, focused, covers, sizing)
                    if focused and role == "league_client" and layout is not None:
                        if covers:
                            if self.desktop.foreground_handle() == key[0] and self.desktop.window_owner(key[0]) == key[1:]:
                                return window
                        elif layout_deadline is None:
                            sizing = self.desktop.fill_application_monitor(window)
                            layouts[key] = sizing, self.clock() + self.layout_timeout
                        elif self.clock() >= layout_deadline:
                            report(role, True, False, sizing)
                            raise RuntimeError("League is in front, but its landing window does not cover the monitor.")
                    elif not focused and can_activate and (focus, key) not in attempts:
                        self.desktop.activate_application(window, focus)
                        attempts.add((focus, key))
                if self.clock() >= deadline:
                    if role_at_focus == "riot_client":
                        report("riot_client", any(role == "riot_client" and windows.window_key(window) == focus
                                                  for role, window in candidates), None, None)
                        raise RuntimeError("Riot Client opened. League may be waiting for sign-in or an update.")
                    if role == "league_client":
                        raise RuntimeError("Windows did not confirm the League landing window in front.")
                    raise RuntimeError("Riot did not surface the League landing window.")
            stop.wait(.12)
        return None
