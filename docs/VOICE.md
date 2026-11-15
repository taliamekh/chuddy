# Local voice, microphone and speech recognition

All inference runs locally after model downloads. No paid speech API, account or
API key is required. Voice, microphone and physical outputs are not activated by
setup commands, constructors, model preparation or automated tests.

## Character voice

The selected candidate is [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox),
an MIT-licensed reference-conditioned voice model. Turbo and Nano are evaluated
through an isolated process/environment so PyTorch dependencies cannot break the
desktop's TensorFlow sound classifier. The upstream source is pinned to
`5de7a54aa4e5e2baadb0182dde554908b48b85c2` for this prototype.

Windows/GPU installation is a measured compatibility task: the upstream dependency
file pins old PyTorch on Python 3.12, while this laptop uses a Blackwell GPU. The
setup helper instead selects PyTorch/torchaudio 2.9.1 CUDA 12.8. It does not install
drivers, alter security settings or enable WSL. If that GPU path fails, benchmark
Nano on CPU rather than claiming the GPU works.

From an existing Python environment at the project root:

```powershell
.\.venv\Scripts\python.exe -m chuddy.voice_setup --install-voice
.\.venv\Scripts\python.exe -m chuddy.voice_setup --download-voice turbo
.\.venv\Scripts\python.exe -m chuddy.voice_benchmark --engine turbo --device cuda --reference .chuddy/voice/reference.wav --configure
.\.venv\Scripts\python.exe -m chuddy.voice_benchmark --engine turbo --device cuda --reference .chuddy/voice/reference.wav --letters --configure
```

The reference needs more than five seconds of clean speech. A provisional local
reference was extracted from approximately 09:12.4–09:21.1 of the official
[Nickelodeon UK Patrick compilation](https://www.youtube.com/watch?v=2vqW0q7yZj8).
The public source, temporary download, reference and generated auditions stay in
ignored `.chuddy/voice`; they are not redistributed in the repository. Selection
was based on the official clip and its captions, not a completed listening review.
The PNG reference contains no voice. Patrick similarity remains **unvalidated**
until the user hears the generated samples. Pitch shifting or a generic system
voice is not reported as an accurate match.

`voice_benchmark` writes WAV auditions and JSON timing data without playing
speakers or using a microphone. `--configure` writes local `.chuddy/voice.json`
and a same-voice cached filler. Restart the desktop after preparing/changing that
configuration. To compare Nano, download `nano`, then benchmark with
`--engine nano --device cpu`. Do not configure an unreviewed candidate as an
established exact match; the interface preserves the unvalidated status.

The current adapter supports `auto`, `reference`, `windows` and `off`. Windows
speech is labeled a generic emergency fallback, not Patrick. User voice enablement
is separate from explicit sound tests. The app does not speak on a fresh install
until the user enables voice or explicitly requests a voice test.

The worker keeps reference conditioning/models warm, synthesizes short complete
phrases, and prepares the next phrase while the playback queue speaks. This is
application-level phrase playback, not a claim of native sample-streaming support
from upstream. A cached filler can play without waiting for synthesis. Stop clears
queued output and discards late results while preserving a healthy warm worker.
Application shutdown closes the worker. Display spelling remains `B-U-T-T`; the
speech input uses individual letter pronunciations. Audition those pronunciations.
The letter setup command caches individually synthesized A/B/E/F/O/P/R/T/U WAVs in
the same reference-conditioned voice. When available, the adapter plays those
cached letter units for B-U-T-T/P-O-O-P/F-A-R-T instead of asking the model to
render the entire hyphenated sequence as one word. These local caches are not
included in distributed source assets.

Timing reports distinguish cold startup, warm synthesis, audio duration and real-time
factor. They do not equate a filler with the first substantive answer. Overall
conversation latency also includes local language-model generation and any device
playback/network buffering. Benchmark both models together before promising speed.

### Measured audition results on this laptop

Turbo ran on the RTX 5070 Laptop GPU with PyTorch 2.9.1+cu128; a CUDA matrix
operation and all five reference-conditioned auditions succeeded. Peak allocated
VRAM was approximately 3,083 MiB. The first-ever process took 52.34 seconds to
return its first WAV, including approximately 45.25 seconds for model/reference
preparation (initial library/JIT work). A second process returned its first WAV in
14.50 seconds, with model/reference preparation at 7.55 seconds. Prewarming matters.

| Warm Turbo audition | Synthesis | Audio duration |
| --- | --- | --- |
| Greeting | 0.97 s | 3.18 s |
| Hesitating spelling | 1.92 s | 5.58 s |
| Wrong arithmetic | 1.20 s | 3.58 s |
| Deadline excuse | 1.63 s | 4.82 s |

Across these four warm samples, median synthesis was 1.42 seconds; the maximum was
1.92 seconds. The benchmark labels that maximum as a small-sample p95 estimate;
it is not a statistically robust latency percentile or end-to-end conversation
measurement. Warm real-time factors were approximately 0.31–0.34. Files were
generated without playback, so audible latency, letter clarity and Patrick
similarity remain listening/device checks. Stable local auditions are
`.chuddy/voice/audition-greeting.wav` and `audition-spelling.wav`.

The working isolated environment also pins `setuptools==80.9.0` because Perth's
watermarker imports `pkg_resources`, removed from newer setuptools. Chatterbox is
installed editable with `--no-deps` after the explicit dependency choices, because
its package initializer requires distribution metadata. The Perth watermark is
retained; it was not disabled to make synthesis work.

Nano also completed the same reference-conditioned audition using ordinary
PyTorch CPU inference, with no GPU memory allocated. Its four warm
synthesis times were 2.48, 5.02, 2.54 and 4.07 seconds; median 3.30 seconds and
maximum 5.02 seconds, with real-time factors approximately 0.70–0.84. It is a
working CPU alternative, but Turbo GPU was faster here and remains configured.
Those are synthesis benchmarks, not a listening similarity comparison.

## Sound classification

Use the desktop audio dependencies, then explicitly prepare Google's official
[YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-audio.txt
.\.venv\Scripts\python.exe -m chuddy.audio_setup
```

Setup downloads the official TF Hub SavedModel archive, extracts data safely,
restores the model on CPU and runs a silent-waveform smoke test. It never opens the
microphone. The classifier uses the official 521-class map, with indices 53 for
burping and 55 for fart. Inputs are mono/resampled to 16 kHz. It is an **uncalibrated
demo detector**, not verified bodily-event identification.

Microphone capture requires a deliberate UI start, selected input and OS access.
The state shows a live meter, recording status and model loaded/missing/error
status separately. Missing classification keeps the meter and manual/test paths
usable. No raw scoring audio files are saved. Audio queues are bounded; Stop
closes capture and rejects late classification events. Changing profiles must stop
capture before starting under the new profile.

Manual events, included sound tests, simulator playback and YAMNet predictions
retain different source labels. Chuddy's own playback **can** score through the
microphone; no playback suppression, attribution or anti-cheating is added. The
included procedural effects are comedy assets and are not calibrated positive
examples: actual YAMNet inference on those assets did not pass the default 0.35
threshold in the initial check. The deliberate sound-test score remains labeled
test, not classifier success.

The added CC0 YouTube-origin archive effects were checked separately: 9 of 13
trimmed variants exceeded the default 0.35 threshold, including the default
`fart.wav` at approximately 0.914. This establishes file-inference functionality
on these effects, not real microphone accuracy or general classifier calibration.
Sources and exact editing steps are recorded in `assets/audio/SOURCES.md`.

## Push-to-talk

```powershell
.\.venv\Scripts\python.exe -m chuddy.voice_setup --install-asr
```

This prepares `faster-whisper==1.2.1`, the free local tiny.en model and CPU/int8
inference. PyAV is pinned to 15.1.0 because 19.0.2 removed an argument used by this
faster-whisper release. Audio and ASR environments remain separate.

Start push-to-talk deliberately, speak, then finish to transcribe locally. Capture
is capped at 20 seconds. If push-to-talk opened the microphone, finishing closes it;
an already active scoring microphone remains active. A short temporary WAV is
deleted after transcription. Stop cancels pending transcription and prevents the
late text from starting a conversation. This has not been tested with the user's
real microphone during development.

An initial offline transcription of the 8.7-second public reference completed in
approximately 0.92 seconds on CPU; it contained transcription errors, so this is a
pipeline/latency smoke test rather than an accuracy claim.

## Validation and hardware limits

Run `python -m unittest tests.test_audio_hardware -v` for source labels, original
profile attribution, inference cancellation, resampling, missing-model honesty,
bounded reversible yaw, no simulated spray, authentication, duplicate/stale
commands, output lease expiry and disabled startup checks. Tests stub all playback
and physical outputs. Actual microphones, speakers, Pi GPIO/servos, pump release
and mechanical clearance still require deliberate device checks. See
[Pi setup](../pi/README.md) for its protocol and configuration.
