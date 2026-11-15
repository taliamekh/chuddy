# Chuddy

A deliberately terrible friend: a Windows desktop companion with a real local AI
agent, a Raspberry Pi companion protocol, and extremely questionable pretend advice.
The character can misunderstand numbers and deadlines; the application keeps real
state accurate.

See the [concept](CHUDDY_CONCEPT.md), [reference image](inputs/chuddy-reference.png)
and [architecture](BUILD_PLAN.md).

## Run on this computer

Open **Start Chuddy.cmd** in this project folder, or run:

```powershell
.\start.ps1
```

The native window uses Microsoft Edge WebView2. If the native runtime is unavailable,
use `.\start.ps1 -Browser`. `-Headless` starts only the local service. The default
address is `http://127.0.0.1:8765`; runtime data lives in ignored `.chuddy/`.

Fresh launches have the work session stopped, microphone off and physical outputs
disabled. Start chudding deliberately; sessions then run without a countdown or
ordinary Pause or Return-to-work controls. Set the interruption cooldown to
**0** under Work detection for no delay between eligible interruptions.
**Ctrl+Alt+Escape** remains the emergency stop, and closing Chuddy cancels pending
work. Chuddy closes one verified selected browser tab or sends the verified
foreground desktop window a standard close request. An app's own save prompt
stays under your control. After confirming closure, Chuddy opens the selected
destination fullscreen. **Back to Chuddy** returns from Clam Casino while the
session continues. See the [interruption behavior](docs/INTERRUPTIONS.md).

**Ctrl+S** opens the hidden admin bar at the top left. `stop` pauses chudding.
`monies 1000` adds 1,000 play monies
to the current profile's casino balance. These are fictional monies.

## Install on another Windows computer

Install Python 3.12 and WebView2 if needed, then from this folder:

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe scripts\setup_local_ai.py
.\start.ps1
```

The explicit local-AI setup downloads the free portable Ollama runtime and pinned
`llama3.1:8b-instruct-q4_K_M` model. This is a large one-time download. It uses
project-local files and process-scoped configuration, with Ollama cloud features
disabled. Existing saved model choices are preserved; Companion studio can select
a different downloaded model.
Model response times depend on the machine. CPU-only inference can be slower.

No required feature uses a paid inference API, subscription, free trial, credit card
or rented server. Models and services run on existing hardware. Initial software
and model downloads need Internet access; normal local inference does not. The
included image assets do not require an image-generation service at runtime.

The optional [Windows executable build](docs/PACKAGING.md) is a complete folder,
not a standalone file to copy by itself. Large models and Python ML environments
are kept outside that folder. See the packaging document for the exact tested
capabilities and optional audio dependencies.

## What to explore

- **Companion:** local model conversation, persistent history, memory and visible
  tool trace. Character replies, cached lines and scripted fallback are labeled.
- **Work detection:** foreground activity defaults to work, with protections for
  gaming, Chuddy, ChatGPT, Codex, OS transient controls and disabled custom
  rules. Social media, streaming and shopping are eligible by default. App/title/domain/category hints identify
  exclusions; optional local AI can refine categories. Unknown games without
  recognizable metadata may still look like work. Custom process/title/URL
  overrides remain optional. Install and pair the [browser extension](extension/README.md)
  optionally for URL-specific rules. Native browser-tab and desktop-app controls
  work without it. Sessions remain opt-in, with cooldown and Resume after a pause.
  The casino is the default destination; optional LCBO/Beer Store homepages remain. Patrick's visual second
  cursor never moves your real mouse. Rehearsals are silent/local; actual work
  interception and native transparency/DPI still need deliberate device checks.
- **Deadlines:** connect private Google Calendar and Brightspace iCal links once;
  Chuddy refreshes them every 15 minutes without individual deadline entry.
  [Connection instructions](docs/DEADLINES.md) explain where to find each link.
  Dates stay authoritative and calendars are read-only. No accounts are connected
  until their links are saved in the app.
- **Bikini Bottom casino:** blackjack, no-limit Hold'em against character bots,
  roulette and slots, with Gary dealing and card/chip interactions. Running chudding
  earns 1 monies/second without recording or scoring while work is absent.
  Foreground work suspends passive earnings. Each accepted fart adds +1×
  for 60 seconds, up to 5×; Pause/Stop/restart/profile changes reset the boost.
  Passive monies have no daily cap; the old 500/day cap applies only to score/game
  bonuses. The sidebar shows the real balance/rate/multiplier. All payouts
  remain fictional and authoritative
  on the local backend. See [game rules](docs/CASINO.md).
- **Sound scoring:** profile-scoped burp/fart counts with manual, test,
  simulator and classifier provenance; Best today, Best this week and Best all time
  for local and friends rankings. Today/week use Toronto calendar boundaries, with
  weeks beginning Monday. Friends sync is explicit.
- **Companion studio:** voice/model/device settings, explicit microphone controls,
  browser pairing, friends configuration and Pi controls.

The local model has a small validated tool set: read approved context, save an
explicitly requested ordinary preference, request an included distraction in an
enabled session, and change only the fictional clock label. It cannot run shell
commands, edit files, spend money or choose arbitrary hardware outputs. The
controller checks session state, freshness and cooldown before executing actions.

## Voice and microphone

The far-right **+** beside Send toggles the computer microphone. **Push to talk**
is the default: click once to record, then again to finish and send. In Companion
studio, Audio input settings offers **Always listening**, which uses local
silence-separated transcription while switched on. Choose System default or a
specific computer input. Saving settings and reopening the app leave capture off;
Pause/Stop also stop it. The existing sound-scoring microphone remains separate.

See [local voice and audio setup](docs/VOICE.md). The primary voice candidate is
local reference-conditioned Chatterbox, isolated from the desktop environment.
It can cache an “ummm” acknowledgement and synthesize short phrases while the next
phrase is prepared. Windows system speech is clearly labeled a generic fallback.
Patrick voice similarity and end-to-end audible latency require listening/device
validation; a generated WAV or a cached filler alone does not establish either.

YAMNet performs real local sound inference when installed, with a configurable
threshold and an **uncalibrated** label. Manual/test controls remain usable without
the classifier. Chuddy's playback may count through the microphone as requested.
Retransmitting the same event ID is deduplicated, while distinct playback and
microphone detections may both score. No speaker attribution or playback suppression
is added. Raw scoring audio is not saved; push-to-talk transcription uses a short
temporary WAV which is removed afterward.

Development checks use files, synthetic buffers and fake devices. They do not
activate microphones or speakers automatically. Actual input/output devices remain
user-operated checks.

## Pi and friends

The [Pi service](pi/README.md) runs in simulation with no Pi hardware. Its fixed
base contains the electronics; only the figure turns in yaw. Physical outputs
require measured configuration, explicit arming and a renewable lease. Spray is
separately gated and never activates in simulation. Exact GPIO assignments, travel
and audio devices are configuration values. The [turret](docs/hardware/turret.md) and [cheek switch](docs/hardware/cheek-switch.md) guides describe bench setup and calibration.

The [friends service](friends/README.md) runs free on an existing PC/Pi over the LAN.
Private rooms use separate invitation tokens. Synchronization uploads only profile
names/identities and scoring events, never chat, deadlines, browsing or recordings.
It is a local/friends implementation, not a worldwide deployment.

## Verify and inspect

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
.\.venv\Scripts\python.exe scripts\benchmark_ai.py
```

Benchmarks generate files without playing speakers or recording. Results are in
ignored `.chuddy/benchmarks/` and `.chuddy/voice/`. See the [validation guide](docs/VALIDATION.md) for automated checks and device checks.
The [demo walkthrough](docs/DEMO.md) gives a short user-operated rehearsal.

Read the [API contract](docs/API.md) for integration details. SQLite profiles,
settings, scores, history, deadlines and wallet ledger persist across restarts;
session activation does not. Windows service tokens use DPAPI; environment
variables are also supported. Keep `.chuddy/`, service tokens, local recordings
and runtime model files out of Git.
