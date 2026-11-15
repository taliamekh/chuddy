# Raspberry Pi companion

The Pi belongs in the fixed half-barrel base. Only Patrick rotates in yaw. This
software does not design the wiring, CAD, motor power supply or mechanical limits.
Servo motor power comes from an external regulated supply; the Pi supplies control
signals. PCA9685 is supported as an optional adapter. The removable barrel walls
are a mechanical service feature, not part of the software protocol.

The entire service runs in simulation without GPIO packages. Physical outputs are
disarmed after **every** restart, and microphone capture is off. Creating the
service or querying status never opens GPIO, moves a servo or records audio.

## Run the simulator

From the project root, use a Python 3.12+ environment:

```powershell
$env:CHUDDY_PI_TOKEN = 'replace-with-a-long-private-pairing-token'
.\.venv\Scripts\python.exe -m pi.service --config pi/config.simulator.json
```

The default address is loopback port 8766. Configure the desktop with
`http://127.0.0.1:8766` and the same Pi token. Query status first; press the Pi belly
control deliberately to run a simulated sequence. That explicit operation plays
an included fart sound; automated tests replace playback with a stub.

On Raspberry Pi OS, create a virtual environment, install `pi/requirements.txt`,
and run `python -m pi.service`. `aplay` provides WAV playback; `espeak-ng` is an
optional **generic** Pi voice fallback. Chuddy's reference-conditioned voice runs
on the PC. Installing those OS packages and selecting actual devices is a Pi setup
step, not something this project has performed on real hardware.

For a PC/Pi link, bind `--host` to the Pi's actual LAN/USB-network address and use
that address in the desktop. Ethernet, Wi-Fi and configured USB networking use the
same protocol. A USB cable alone does not establish networking. The service
accepts an authenticated local HTTP connection; do not expose it directly to the
Internet. No firewall/network configuration is changed by the application.

## Protocol v1

Every request needs `Authorization: Bearer <Pi token>`. No browser CORS access is
enabled. Command bodies are limited to 32 KiB.

| Endpoint | Meaning |
| --- | --- |
| `GET /v1/status` | Mode, sequence, yaw, calibration, arm/lease and microphone status |
| `GET /v1/events?after=0` | Durable cursor-ordered events, at most 100 per poll |
| `POST /v1/commands` | Versioned, idempotent command |
| `POST /v1/playback` | Idempotent PC-generated reference-voice WAV, up to 1.5 MB PCM16/20 seconds |
| `GET /v1/audio?after=0` | Bounded temporary PCM capture chunks while explicitly recording |

Command example:

```json
{
  "version": 1,
  "id": "a-new-uuid-per-intended-action",
  "type": "belly.perform",
  "time": "<current ISO 8601 timestamp>",
  "payload": {}
}
```

Generate the timestamp at send time. New commands more than 15 seconds old or
over five seconds in the future are rejected. Retry an uncertain request with its
original ID and payload. IDs are durably remembered **before** outputs run, so a
reconnect/restart cannot repeat movement. Reusing an ID with different content is
an error. Response `accepted:false` contains a command error; HTTP authentication
and envelope errors use non-200 responses.

Supported commands:

- `belly.perform`: bounded yaw out, fart playback, optional calibrated spray
  press/release, yaw home. Busy requests are rejected.
- `outputs.arm`: explicit `lease_seconds` from 2 to 30 and boolean `spray`.
  Simulator stays unarmed and spray stays disabled.
- `outputs.renew`: renew an existing output lease, never arm implicitly.
- `outputs.stop`: cancel sequence/playback/capture and disarm. The sequence
  releases and attempts a bounded return while hardware remains operable.
- `audio.play`: `kind` equal to `fart`, using included fart assets.
- `audio.speak`: bounded text for the optional generic Pi speech backend.
- `audio.wav`: the dedicated playback endpoint accepts a base64 `wav` payload,
  allowing PC-generated reference-conditioned speech through `PiClient.play_wav`.
  It validates the WAV format/duration and accepts no filesystem paths from the
  caller. This route does not classify or score speech as a bodily sound.
- `mic.start` / `mic.stop`: explicit input capture; no raw audio files are saved.
- `spray.request`: records a request/status. Standalone pump holding is not
  supported; actual press/release can only occur inside the bounded sequence.

Events include unique IDs, UTC timestamps, cursor, type and payload. The PC
credits fart `audio.playback` once for a `confirmed:true` completion that matches the
kind, source and `playback_id` of an `audio.playback.started` event received on
the current connection. The event retains physical, simulated belly or explicit
test provenance. Fetched unpaired events can appear in the trace without a score.

Microphone chunks contain base64 `pcm_s16le`, mono, sample rate and cursor.
Newer services also include known WAV asset names and relative playback timing
for same-device echo subtraction. Only eight chunks are kept in memory.
Stopping capture clears the buffer. The PC forwards chunks to
`AudioPipeline.ingest_pcm` after explicit microphone enablement and runs the
classifier locally. Older Pi services still send readable PCM. Update the Pi
service to enable confirmed playback credit and echo references.

## Physical enabling and calibration

Copy `config.physical-template.json` to a local ignored configuration. Its null
fields deliberately prevent arming. Select `pca9685` or `gpio`, fill measured pulse
bounds, actual channels/pins, home/away/allowed yaw limits, speed and timeout.
Approximately 180° is the intended effect; do not use it unless the mechanism's
measured travel and wiring clearance permit it. The software permits only 0–180°
positional angles and rejects endpoints outside configured bounds.

Install only the selected adapter package on the Pi. Check actual output/audio
devices, manually establish the calibrated home position, and mark motion
calibrated only after a bench check. An open-loop positional servo cannot prove
its physical position. Send `outputs.arm` deliberately, keep renewing its lease
during the controlled session, then explicitly stop. The service disarms on lease
expiry and never resumes queued movement at startup.

Spray has separate measured calibration and a separate explicit boolean enable.
It is always disabled in simulation. Bench-test release and travel with water
before any venue-approved scent demo. No physical actuation has been validated in
this workspace. Software cannot guarantee a return after power loss or a jam.

`chuddy-pi.service` is an optional systemd template; edit paths, account and bind
address deliberately. Installing/enabling it is user-operated. Its environment
file should contain only the private Pi token, and should not be committed.
