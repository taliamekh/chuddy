# Chuddy software build plan

Chuddy includes a Windows desktop companion, companion browser extension,
Raspberry Pi service and simulator, and friends leaderboard. The
[concept](CHUDDY_CONCEPT.md) covers character and hardware ideas. The
[API contract](docs/API.md) describes the software interfaces.

## Architecture

Use Python 3.12, a pywebview Windows desktop shell, local HTML/CSS/JavaScript, and SQLite. The desktop application owns a loopback HTTP service. No frontend build system is required for the prototype. Use a Chrome/Edge Manifest V3 extension to report selected active work URLs. Use a separate Python Pi service and a separate lightweight friends leaderboard service.

The desktop controller is the authority for sessions, deadlines, money and scores. A local model operates an event-driven agent loop: observe approved context, recall local memory, choose from a defined set of tools, receive validated tool results, and respond in character. The controller validates every requested action against the session, cooldown and tool permissions. Model text is never executed as shell code, SQL, arbitrary window commands or hardware instructions.

Run local inference through Ollama with a quantized tool-capable model. The setup script provisions Llama 3.1 8B by default. Run inference on the existing laptop; the Pi handles physical inputs/outputs and playback. Training a new foundation model is unnecessary: Chuddy's agent, memory, tools and persona are built around a locally running pretrained model. Ollama supports [tool calling](https://docs.ollama.com/capabilities/tool-calling) and [response streaming](https://docs.ollama.com/api/streaming); the proposed [Qwen3 4B package](https://ollama.com/library/qwen3:4b) is about 2.5 GB and lists an Apache 2.0 license.

| Component | Responsibility | Default state |
| --- | --- | --- |
| Windows shell and interface | Companion, chat, games, settings, visible controls, work overlay | Open normally; interception stopped |
| Desktop controller | Session lifecycle, rule matching, cooldown, validated actions | Stopped after every application launch |
| Local AI agent | Context, memory, decisions, validated tool requests, streamed short replies | Local model; explicit readiness/loading state |
| SQLite store | Profiles, history, score events, deadlines, settings, wallet ledger | Local durable data |
| Browser extension | User-selected active URL reports to paired desktop | Reporting disabled until enabled |
| Audio pipeline | Input-device selection, live meter, local YAMNet inference, test events | Microphone off |
| Pi client and simulator | Versioned commands, events, status, simulated motion | Simulator; physical outputs disabled |
| Pi companion service | Belly input, calibrated yaw, playback, microphone events, spray requests | Disarmed after every restart |
| Friends service | Private-room event ingestion and rankings | Local development service; no public deployment |

Voice output uses a local reference-conditioned speech engine behind a cancellable worker. Chatterbox Turbo and Nano are optional engines for the Patrick-inspired voice. Its [official project](https://github.com/resemble-ai/chatterbox) provides reference-conditioned synthesis; its [Turbo model](https://huggingface.co/ResembleAI/chatterbox-turbo) lists an MIT license. A suitable voice reference/model is needed; the PNG reference supplies appearance only. Generic Windows speech remains a clearly labeled emergency fallback and does not satisfy the accurate-voice milestone. Include a local fart sound and explicit playback control.

## Free operation and target hardware

The required demonstration has no subscriptions, paid inference APIs, credit-card signup, paid voice service or required cloud hosting. Download free software/models once, then demonstrate conversation, agent tools, voice, sound scoring and games without Internet access. Run the friends service on the existing PC or Pi over the LAN. Hosting on additional rented infrastructure is outside the zero-fee baseline. Existing hardware, electricity and Internet access are not supplied by the software.

Read-only inspection found an Intel Core Ultra 7 251HX, about 31 GB usable system RAM, and an NVIDIA RTX 5070 Laptop GPU reporting 12,231 MiB VRAM through `nvidia-smi`. Ollama was not found on PATH. These are planning inputs, not a successful model or voice benchmark. Keep model contexts and memory allocations bounded so the brain and voice can coexist; prefer CPU sound classification/transcription if it avoids GPU contention.

Track licenses and attribution for each selected package, model and included sound. Do not treat a free trial or hosted free tier as the offline/free requirement.

## Verification

- Run the [automated checks](docs/VALIDATION.md) for desktop behavior, the local
  protocol, browser extension, interface, data persistence and Pi simulation.
- Check the [Windows package](docs/PACKAGING.md) with a disposable profile and
  temporary data directory. Keep recordings, keys and model files out of the package.
- Exercise real voice, capture and physical hardware only through deliberate
  device checks. Report synthesis timing separately from audible latency, and
  verify the physical wiring, clearance and travel before enabling outputs.
