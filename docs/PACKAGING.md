# Windows executable packaging

Chuddy supports its Python launcher and a PyInstaller **one-folder** Windows package. The package contains the desktop's Python runtime, interface, local sound assets and Pi simulator. It does not contain Ollama, model weights, voice/transcription environments, recordings, scores or credentials.

The native window still requires the Microsoft WebView2 runtime. Nothing in the build changes Windows settings or silently installs that runtime. The executable is unsigned; code signing and an installer are not part of this prototype.

## Build

From the project folder, using Python 3.12 on Windows:

```powershell
python scripts/build_windows.py --prepare --verify
```

If Python is not on PATH, use the full path to your Python executable.

`--prepare` creates `.chuddy/build-venv` and installs the pinned build dependencies there. It leaves the desktop and audio/voice environments alone. Subsequent builds can omit `--prepare`; `--python` selects a different build-environment Python. `--expected-persona-sha` optionally pins the agreed persona byte digest before any build starts. Every build also checks that all runtime source snapshots stay unchanged throughout compilation/support copying. Build artifacts go to `build/windows` and `dist/Chuddy`, both ignored by Git. A build replaces the previous generated `dist/Chuddy` folder; keep personal data outside that generated build directory.

PyInstaller's spec explicitly excludes large optional ML frameworks from the desktop executable. They run in separately configured external workers. This keeps the desktop bundle practical and avoids distributing multiple GPU runtimes inside it. See [PyInstaller's spec documentation](https://pyinstaller.org/en/stable/spec-files.html) and [runtime path documentation](https://pyinstaller.org/en/stable/runtime-information.html).

## Run or move the package

Keep the **entire** `dist/Chuddy` directory together. `Chuddy.exe` needs its `_internal` folder. Move/copy that whole directory to a writable location, then launch `Chuddy.exe`.

The default durable data location is `.chuddy` **beside the executable**, outside `_internal`. The database, configuration, local models and optional workers belong there. `--data-dir` selects another location. Never store live user data under `_internal` or in a build output that will be regenerated.

Useful troubleshooting commands from the packaged folder:

```powershell
.\Chuddy.exe --browser
.\Chuddy.exe --headless --no-watch --no-ollama --port 8876 --data-dir .\demo-data
```

The console-enabled executable preserves useful startup errors. Close the native window to stop Chuddy, or use Ctrl+C for a headless run. `--headless` opens no visible window and starts with the session stopped, microphone off and physical outputs disabled. Headless mode still exposes the authenticated API: a deliberate API action can start a session or other supported feature. Add `--no-watch` for diagnostic runs that must not start the foreground-work watcher; `--no-ollama` prevents automatic startup of a portable model server.

The copied `extension` folder can be loaded unpacked in Chrome/Edge after the user chooses to do so. See its README. `pi` and `friends` contain the small service sources for separate Python environments; they are not automatically started.

## Optional local AI/audio setup

The package is explorable without downloaded models. To enable real local AI, use the copied `scripts/setup_local_ai.py` with a separate Python 3.12 executable. It downloads a free portable runtime/model into the packaged folder's external `.chuddy` directory. This step requires Internet access once and is never performed by building or launching the package.

```powershell
python scripts/setup_local_ai.py
```

An already installed local Ollama server can also be configured in Companion studio. No paid API is required. The voice and audio worker setup remains documented separately; create those environments beside the executable, or configure their absolute paths. The included source worker files do not imply that their heavyweight dependencies or models are installed. Moving a configured package may require updating the absolute paths in `voice.json`, `asr.json` or other worker configuration.

The executable includes manual events, deliberate sound tests, games and the simulator. **TensorFlow remains external:** the frozen app can launch the included `classifier_worker.py` through the Python configured in external `.chuddy/audio.json`. Run the copied `chuddy/audio_setup.py` with a separate TensorFlow-enabled Python environment to prepare the model and configuration. Copying a model alone does not configure that worker. Meter-only capture, manual events and sound tests remain available with explicit labels. External classification, voice and transcription workers are configuration-dependent integrations; the basic packaging smoke test does not establish their readiness. An absent character voice reference cannot be called an accurate voice match.

## What verification establishes

`--verify` starts the built executable with **headless mode, an unused loopback port, a temporary data directory, `--no-watch` and `--no-ollama`**. It checks the health endpoint, authenticated state, stopped session, microphone off, voice not speaking, external database creation, current UI/sprite/casino source hashes and byte-identical preserved reference. Passive fresh-state assertions additionally verify `ai_work_detection=true`, stopped/empty work-recognition diagnostics and unavailable/inactive native-window dispatch; no classification is requested. It also checks `concise-ui.css`, both Gary sibling PNGs and the casino action manifest: all 22 fetched resources must match frozen source hashes, and all seven manifest PNG declarations must match served bytes. These checks establish inclusion and byte identity, not artwork appearance. The diagnostic opens no native window and starts no capture, sound, model inference or hardware action. After its quiet economy checks it enables a session only on the disposable headless instance to test a silent local rehearsal, then stops it. No real desktop session is enabled. It checks that obsolete SVG character assets are absent.

The economy smoke check changes **only the verifier's disposable profile**. It verifies a manual burp reward and event deduplication, rejects an early clam-game claim, waits the real server's 20-second duration, then verifies one completed reward and exact replay behavior. During that wait it exercises roulette settlement, blackjack stand settlement and a poker fold through the actual authenticated API, checks authoritative reserve/return arithmetic and saved round recovery, and retries requests to confirm they do not pay twice. Random cards/wheel results are not forced; the checks compare actual returned payouts with canonical wallet balances. No live user database, game or wallet is used.

A separate silent-preview check confirms the default destination/reduced-motion settings, rejects invalid values, verifies native-overlay readiness is unavailable in headless mode, and opts into only the disposable session. It rehearses a casino interruption through the real coordinator, reaches `preview`, verifies no AI turn/dialogue/window minimization/retailer/wallet change, then checks Stop clears the rehearsal and restores stopped state. This does not exercise a real native overlay or user work window.

Support copying excludes local Pi configs, environment/credential/key files, SQLite databases and WAL/SHM files, recordings, runtime captures, private training datasets/checkpoints/weights, caches and model environments. A regression constructs a synthetic source tree to check those exclusions. The final bundle is scanned again before release metadata is written; `.gitignore` is not relied upon to protect copied files. Public example configs and pythonnet's required runtime DLL directory are retained. The casino Python modules are explicit hidden imports, while the existing complete `web` data inclusion carries casino JavaScript/CSS, `earnings.css` and raster room/table assets.

The report is written to `test-results/packaging.json`. This is a package/API smoke test, not a native WebView2 render test, microphone-device test, classifier benchmark, physical-hardware test or proof of voice similarity. Native-window checks and the full Python test suite are separate.

With an existing prepared data directory, the executable also offers a microphone-free classifier diagnostic:

```powershell
.\Chuddy.exe --check-classifier --data-dir 'C:\path\to\prepared\.chuddy'
```

This loads the external TensorFlow worker, classifies one silent buffer and exits. It does not start the HTTP server, foreground watcher, microphone, native window or speaker playback. It uses a temporary score store, so it does not add test events to the normal profile. A successful silence check verifies the packaged worker path and inference plumbing, not burp/fart recognition accuracy.
