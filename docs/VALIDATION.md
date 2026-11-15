# Chuddy validation

Run the automated checks from the project root after setting up the desktop environment.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s packaging -p 'test_*.py' -v
node --test tests/frontend.test.cjs tests/status-labels.test.cjs tests/character-geometry.cjs tests/chud-cursor.test.cjs tests/browser-tab-commands.test.cjs
.\.venv\Scripts\python.exe scripts\offline_smoke.py
```

The Python and Node suites exercise local runtime behavior with fixtures and
test doubles. The offline smoke check uses a disposable profile and local
model fixture. See [Windows packaging](PACKAGING.md) for the package check and
[artwork](ARTWORK.md) for browser rendering checks.

Real WebView2 behavior, mixed-DPI overlays, audio-device performance, voice
similarity, recognition accuracy and physical motion require device checks.
See [voice and audio](VOICE.md) and the [Pi service](../pi/README.md) for setup.
