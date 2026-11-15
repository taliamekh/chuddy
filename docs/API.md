# Chuddy prototype contract

## Foreground switch

A running session acts on one eligible foreground target. Chrome, Edge and
Brave use a verified native selected-tab close or a fresh paired extension
command. The extended version-2 report binds the tab, window, client and
current address. Native tab controls and the extension recheck exclusions
before dispatch. The browser can close its own window when its final tab closes.

A desktop work window such as Outlook gets one normal Windows close request
after revalidating its identity. An owner-handled save prompt or unconfirmed
close blocks the handoff. Gaming, Chuddy, ChatGPT, Codex, Discord, deliberate opt-outs
and transient surfaces are protected by the applicable classification and
control checks. Social media, streaming and shopping are eligible by default.
See the [interruption guide](INTERRUPTIONS.md).

`state.interruption.target_close` reports `requested` or `confirmed` when
available. A confirmed close starts `opening`. `destination_delivery` becomes
`route_ready` when the local casino route is published, `launched` when
Chrome or the installed Riot launcher is started, or `reused` when an existing
League landing client is selected. `fullscreen` reports
`pending`, `confirmed` or `failed`. Only foreground and fullscreen confirmation
permits `opened`. After a local fullscreen error, the selected casino route
remains available to the WebView. `native_window.verified_foreground` and
`verified_fullscreen` show the most recent cover verification sample, or
`null` before it is sampled. A silent `preview` uses a local rehearsal
without closing work or launching a retailer. Stop invalidates pending
handoffs and speech. An already-dispatched close belongs to the target app.

The `league` destination preflights the installed Riot launcher and League
client before requesting closure. After closure it opens the official League
product entry or uses the existing landing client. Its ticket reports
`destination_window` (`riot_client`, `league_client` or `match` when known),
`destination_foreground`, `destination_monitor_coverage` and
`destination_layout_request`. League reaches `opened` only with the verified
landing HWND in front and its client area covering the monitor. The Riot window
can remain open for manual sign-in or updates. A confirmed foreground League
window can remain open if the client does not accept monitor-filling placement.
An active match is protected and its window is left alone.
After a confirmed close, League allows a short foreground settling period
when the Windows input marker is unchanged. A later, unrelated foreground
is observed passively, including when the generic input marker changes.
That change revokes unrelated-window activation, but does not abort observation
of an already launched destination. The ordinary activation request has a
Windows application-activation fallback bound to one verified PID/window and
the still-current source. No keystrokes or delayed helper actions are used.
Riot's launcher can also receive fullscreen placement while League starts;
it is still reported as `riot_client`, never as a ready League landing client.
Captioned clients use borderless monitor placement with measured client-area
coverage; unsuccessful or cancelled frame changes are restored.
If a verified reused landing window disappears during handoff, the ticket
can trigger one normal Riot product launch. Returning to the same browser after League actually took
foreground can start a fresh ticket when cooldown permits, even if League
remained windowed.

`settings.distraction_destination` and `settings.cooldown` are the user's
stored choices. The old `settings.interception` value remains readable and
writable for compatibility. Production uses the verified close path.

These foreground and presentation rules supersede the earlier runtime notes
below.

## Character reasoning and deadline reply review

Ordinary explicit arithmetic questions use a model-input cue for Chuddy's
joined-number joke (for example 2+2 becomes twenty-two). The input cue excludes
saved amounts, counters, dates, deadlines, clock facts and literal repetition.
It does not rewrite generated model answers or perform application arithmetic.
Personality examples and spelling attempts condition the existing local model;
no new model-weight training is implied.

The pinned conversational model's buffered ordinary replies additionally pass
`deadline_reply_problem`. On explicit deadline/task topics it detects a narrow
set of unsupported temporal/extension/completion claims, while permitting
clearly imaginary character opinions. The existing single repair has no tools;
a second failed reply uses `scripted conversation fallback` with its real error
and source label. Failed candidates are withheld from speech. This lexical
check is not a universal factual verifier, calendar-conflict resolver or timezone
converter. It never edits stored deadlines, wallets, counters or clock data.
Legacy streaming and work-observation tool behavior retain their existing scope.

The generic next-assignment question is a scripted character exchange. The
first ask says “Nothing is due.” Repeats and direct timing follow-ups say
“Well, I guess I missed Assignment #14. It’s due tomorrow.” Both lines are
fixed dialogue. Explicit requests for the actual deadline or a named assignment
use saved calendar data. The exchange is scoped to the active profile and
survives the start of a follow-up voice capture. Deadlines, calendar imports,
counters, wallet and the application clock use the original data.

## Monies and passive chudding

Currency is **monies**; Clam Casino/Clam-o-matic and the shell reel symbol remain
branding. Existing wallet integers, ledger/request IDs and stored rounds are
preserved, without conversion, resets or retroactive credits. Additive SQLite
v3-compatible tables `passive_fraction` and `passive_session` store fractional
carry and the current server-owned run checkpoint.

A running opt-in session earns **1 monies/second** while foreground work is
absent. Detected work, including protected ChatGPT, Codex and Discord activity, suspends
passive credit even if an interruption fails or is cooling down. The next
non-work observation resumes credit without backpay. An open casino, capture,
scoring, local model readiness and browser polls are unnecessary.
A 0.5-second background worker settles authoritative integer elapsed milliseconds.
Windows QueryUnbiasedInterruptTime excludes sleep/hibernate. Gaps over five awake
seconds (a stalled worker/process) are discarded conservatively and reset the
boost. No browser-supplied time/rate/multiplier is accepted. Fractional monies
carry per profile; each whole integer, remainder and checkpoint commit atomically
with wallet/ledger/earnings. At wallet capacity (10^12), excess and fractional
carry are discarded. Never bank capped time for later spending.

A newly accepted fart first settles the old interval, then adds **+1×**
up to **5×** and adds **30 minutes** to the remaining boost. The remaining time
keeps growing at 5×. Expiry splits an interval at the rate change. Manual scores,
classifier detections and confirmed playback use the same event-ID deduplication
and conflict validation.
Pause/Stop/profile selection settle only the previous running interval, end the
run and reset boosts; Resume starts at 1×. Repeated Start leaves the active run
intact. Startup invalidates old run checkpoints without paying offline time.
Committed fractions survive restart; an uncommitted final heartbeat may be lost
on crash. No capture/recording is required.

`state.earnings.passive` exposes `status` (earning/work_detected/stopped/wallet_full/error),
`currency`, `base_per_second`, `rate_per_second`, `multiplier`, `multiplier_cap`,
`boost_remaining_ms`, `boost_duration_seconds`, `daily_cap:null`, and optional
`error`. `boost_duration_seconds` is the time added per accepted event.
The session bar shows the selected profile's fart count, active multiplier
and remaining time beside the session status. The sidebar and table show server-owned amounts
and rates. Legacy
`earnings.level/multiplier`, score rates, milestone bonus and `daily_*` fields
continue to mean score/game bonuses: their combined 500/day UTC cap never limits
or is consumed by passive earnings. New `earnings` rows use category `passive`;
`total_earned` includes them. Exact casino/request retry results remain unchanged
even when the current wallet has grown since the saved response.

## Shopping, fast banter and conversational microphone

The work watcher polls every 0.2s and uses a 0.25s focus settle instead of waiting
up to four seconds for dialogue. Choreography uses 0.45s approach delay and 0.35s
native/web movement, while native preparation, exact identity, cancellation and
actual close acknowledgement remain required. No timer claims to bound OS work.
Model generation and optional speech synthesis run independently of the native
switch. A late tool call for the same observation reports its existing ticket
and decision source. Tool admission alone cannot establish closure or opening.

The work observer preserves its model decision trace. When a real switch
confirms the destination, the active ticket can publish one short invite to
chat and optional speech under the same lock used by Stop.
`interruption.banter_message` and `banter_source:"scripted work outcome"`
identify that line. The casino invite is exactly “How about we gamble?”
Other destinations use a short invite for the activity. A failed switch
reports its phase and diagnostic fields. The approach, pending destination,
failures, cancellations, stale observations, late tool calls and previews
are silent. Typed requests for permission to continue a task get Chuddy's
invitation to chud. Saved facts and deadlines remain authoritative.

`settings.microphone_mode` is `push_to_talk` (default) or `always_listening`.
`audio_device:null` selects the computer's system-default input. Capture starts
through an explicit control. The on-screen + is a toggle. In Push to talk,
hold the far-right numpad + while Companion is focused and release it to finish
and submit the voice message. Losing window focus, leaving Companion or
pressing Escape cancels an unfinished keyboard hold. In Always listening,
the on-screen + turns continuous capture on and off. The continuous path
segments speech using bounded energy/silence cues, transcribes locally and
submits accepted text.
This is prototype segmentation, not speaker identification or calibrated VAD.

`state.microphone` contains `mode`, `active`, `capturing`, `transcribing`, `status`,
`error`, `generation`, truthful `source` and `raw_audio_saved:false`. The existing
`audio` state and scoring actions remain. Authenticated `microphone` action takes
`operation:start|finish|stop`; legacy `asr_start`/`asr_finish` use the same manager.
An active capture is ready only after valid PCM arrives. The Companion shows
“Waiting for microphone audio” before readiness. `input_device`, `ptt_frames`
and `ptt_first_pcm_ms` report the opened input and first-frame timing without
including audio or speech. The PTT count and timing remain visible after
release and reset on the next PTT start. A dedicated PTT stream closes if it
opens and sends no frames within 1.5 seconds.
Finish applies only to PTT and returns local transcription `text` plus accepted
turn ID or cancellation. Always mode uses the same generation/profile admission.
Keyboard holds use `hold_start`, `hold_finish`, `hold_cancel` and
`hold_keepalive` with a `hold_id`. Start also supplies the observed microphone
generation and profile ID. The short server lease expires abandoned holds.
Pause/Stop/profile/device/mode changes invalidate queued speech and stop capture;
Resume never reopens it. Speech occurring during Chuddy's reply is dropped from
conversation capture. YAMNet independently handles acoustic fart scores on the
explicit scoring stream and the continuous conversation stream. Fart confidence
must reach the configured threshold and exceed the eructation confidence.
Overlapping positive windows form one event. Independent farts during Chuddy
playback remain eligible.
Known local playback echoes are subtracted where correlation supports a match.
Confirmed Chuddy fart playback earns one event with speaker provenance for the
selected profile.
Existing local ASR files are required; nothing downloads implicitly. Temporary
WAVs are removed after success, failure or cancellation. The local annotation
tool drafts editable speech timestamps from files. Reviewers enter acoustic
labels separately before comparing them with classifier detections. See [audio setup](VOICE.md) for the scoring workflow.

## Broad work detection and concise interface

This supersedes the historical explicit-work-rule gate below. A usable focused
foreground descriptor defaults to work without an allowlist or available model.
Chuddy and OS transient surfaces are excluded. Gaming evidence takes precedence
over enabled work rules. Deliberate disabled rules remain opt-outs. Existing
rules are optional overrides and live inside a closed
Custom overrides panel. Fresh category exclusions are checked before pending
fallbacks and again on interruption admission. A late exclusion cancels an
unexecuted approach only when its original HWND/PID/title is still foreground;
revealing an entertainment tab after successful work-tab closure does not cancel
the acknowledged casino opening. Session/focus/cooldown/tab authority is unchanged.

`state.work_detection` retains its safe shape. Broad default results use
`status:work`, `source:policy`, `reason:foreground_default`. Gaming exclusions
use `status:nonwork`, `reason:excluded_gaming`. A completed high-confidence local-model category may refine
the decision with `source:local_ai`; disabled, busy, uncertain or unavailable AI
leaves policy work eligible. Policy/fallback decisions are never labeled model
decisions. The Windows executable path is private in-memory evidence only, never
sent to inference or returned in state. Opaque unfamiliar games cannot reliably
be distinguished without category evidence.

ChatGPT, Codex and Discord desktop processes and identifiable browser tabs are protected
before custom overrides (`reason:protected_app`). The extension independently
rejects protected hosts. Native Chrome, Edge and Brave inspect the selected
tab's browser address control outside page content and recheck the host before
invoking its Close button. Address evidence stays private and ephemeral.
Missing or ambiguous address evidence denies native closure. Failed or cancelled
native preparation does not spend a cooldown. Once the performance starts, it
uses the configured cooldown of 0–3600 seconds. Zero allows the next eligible
interruption as soon as the current action and native cleanup finish.

Start chudding is green. The visible admin command button and normal-page cheat
hints are removed. Ctrl+Shift+S or Ctrl+S toggles the hidden command bar. Normal
explanatory paragraphs and reserve/settlement coaching are removed. Actual errors,
readiness, model/audio provenance, amounts, deadline dates and optional rules remain.

## Native tab X control

A Chrome extension is no longer required for browser tab closure. Unpaired
Chrome, Edge and Brave work observations use a local Windows UIAutomation
adapter. It prepares the selected browser-chrome TabItem and its own descendant
Close button on a bounded MTA worker before the approach animation. Web Document
subtrees and the browser-window Close button are excluded. The original tab
RuntimeId is checked again at invocation. Closure is acknowledged when
the original tab disappears and a sibling remains visible, or when the
last-tab window exits. Paired-extension closure requires version-2 report
metadata. The native path remains available when older reports lack it.

`state.browser_tab_control` reports passive native availability/readiness/status
and `mode:native` or paired `mode:extension`; opaque UIA identities and screen
points are never public. A `preparing` interruption phase does not move Patrick.
The animation uses the prepared tab X location; invalid/stale coordinates never
substitute the window X. Pause/Stop/settings/profile/exit cancel queued native
work and prevent late casino actions. An already-dispatched native Invoke cannot
be revoked. No new service or package is needed beyond the existing Windows
pythonnet/.NET runtime. Older extension-required notes below are historical.

## Interruption presentation and session controls

Normal approaches move the character without displaying a speech bubble. The
chat invite follows the confirmed destination handoff. A close acknowledgment
may make the local route available earlier. The shell routes to the casino once
per `state.casino_opening_id`. The ID identifies
the local route for the `casino` or legacy `game` destination. Real routes publish
after the target has closed, and rehearsal routes publish on `preview`. The ID
survives retirement so delayed polls can still route and clears on Pause or
Stop. The shell remembers the routed ID in per-tab session storage across
reloads. **Back to Chuddy** returns to Companion while the session continues,
and a later opening with a new ID routes again. `casino_leave` takes that
`opening_id` and cancels or releases its matching native cover.
`state.casino_interruption_id` tracks a real casino opening with foreground and
fullscreen confirmation. `state.casino_interruption_active` indicates whether
that ID exists. The ID survives retirement and clears when a new real route
is published, or on Pause or Stop.
Manual visits and rehearsals offer the same Back control.

The profile button, casino score shortcut and direct `#leaderboard` route
reach scores and rankings.
The header offers Start or Resume as appropriate. The hidden command bar's
`stop` still pauses the session. Emergency Ctrl+Alt+Escape and app close still
cancel queued tab actions, generation/audio and native effects.

The desktop watcher may detect a work title before the extension reports. A
later paired report now strengthens that same HWND/PID/title observation before
admission. Missing, stale or mismatched tab control is rejected before Patrick
starts approaching. `state.browser_tab_control` supplies only safe readiness
status/reason, never URLs, tab IDs or client identities.

## Calendar imports and desktop integration

This update supersedes the older warning/countdown and clam-game interface
behavior described below. Production warnings have no grace countdown. Opt-in
sessions are untimed (`session_remaining:null` while running). Ordinary Pause,
Stop, Return-to-work and Escape-dismiss controls are removed; emergency
Ctrl+Alt+Escape, app exit and internal cancellation remain. Finished interruptions
retire internally after cooldown on a new work observation.

Default and saved legacy `game` destinations migrate to `casino`. The clam-catch
and separate wallet screens are removed; historical APIs/data remain compatible.
A browser interruption requires the updated paired extension with exact active,
focused tab identity and browser-instance ID. `/browser/report` replies offer a
`close_tab` command; scoped `command_claim` and `command_result` requests validate
execution and acknowledge completion before the casino opens. A final-tab close
first adds an inactive blank tab to preserve the browser window. Unpaired browser
control returns an error and never falls back to minimizing the entire browser.

`state.leaderboard_periods` contains `today`, `week`, `all_time` rankings and
`timezone:America/Toronto`. Week begins Monday at local midnight; DST and score
UTC offsets are respected. Friends sync returns the same period object. Local
and friends rankings order by `fart`. Rows retain historical combined fields.

`state.deadline_sync` lists Google/Brightspace connection status, last sync,
errors and counts, never feed URLs. A one-time private iCal connection enables
15-minute read-only automatic refresh. Imported rows carry source ownership;
UID updates/removals do not alter manual deadlines. All-day rows carry original
`calendar_date` and `calendar_timezone`, not a fabricated exact due time.

Additional authenticated desktop actions:

| action | fields and effect |
| --- | --- |
| `admin_cheat` | `command:"monies 1000"`, `request_id`, `profile_id`; positive integer play-clam credit, exact retry and profile validation. |
| `deadline_connect` | `provider`: `google`/`brightspace`, private HTTPS `url`, current `profile_id`; protect link with Windows DPAPI and schedule refresh. |
| `deadline_refresh` | Current `profile_id`; schedule background refresh. |
| `deadline_disconnect` | `provider`, current `profile_id`; remove only that source and its imported rows, invalidate in-flight results. |

See [calendar setup](DEADLINES.md) for account connection and fixture boundaries.

The production agent now uses model-aware `system_for(text,model)` and `persona_revision(model)`, plus relevance-scoped tool/context disclosure. Ordinary chat receives no unrelated app tools or deadlines; explicit memory requests expose the memory tool, and work observations retain the user's configured destination and work label. This narrows what the model is offered, not the controller's authority: every requested effect still passes normal validation. The default is now pinned Llama 3.1 8B after actual local action/denial/stale/cancellation and conversation checks; this is a small-sample prototype choice, not universal characterization acceptance.

Desktop interruption integration adds `settings.distraction_destination` (`game`, `casino`, `lcbo`, `beer_store`; default `game`) and persisted `reduced_motion` (boolean). The configured destination is authoritative; a model cannot choose a different retailer. Legacy `request_distraction` tool arguments using `game:"clam-catch"` request the configured destination. Non-test requests require a fresh focused work observation and the current model turn's cancellation event is propagated.

`state.interruption` is the coordinator's live record: `{id,label,destination,game,phase,test,error,error_detail?}`. Phases are `warning`, `approaching`, `clicking`, `opened`, `preview` or `error`. Normal work starts with an eight-second stationary native warning; `warning_message` and monotonic `warning_remaining` expose it. Leaving the original window/document or Stop cancels it. Before approach, native visibility diagnostics must confirm the warning rendered; otherwise an explicit error prevents action. The frontend only navigates to game/casino after `opened`/`preview`; retailer success leaves Chrome foreground. Rehearsals are silent and never launch a retailer or minimize real work. `state.desktop_interception` exposes restoration/cover failures, and `state.desktop_overlay` reports native readiness/hotkey availability. Stop/pause/dismiss/profile changes invalidate late effects before voice/device cleanup; changing destination/style/rules also cancels the old pending choreography without resetting cooldown.

The native overlay is bound only in the Windows native shell, remains inert at startup, and shows a stationary buddy only on deliberate session start/resume. It never injects real mouse clicks. The browser UI uses a raster cursor fallback. Idle and warning never travel; only interruption approach moves, taking2.6 seconds before the coordinator's3.1-second action delay. Real Chrome navigation uses fixed allowlisted homepages and no purchasing or age-gate automation. See [interruption behavior](INTERRUPTIONS.md) for configuration and checks.

`state.monitoring` exposes `{desktop_enabled,desktop_running}`. Normal launch starts the foreground watcher; `--no-watch` is a diagnostic override and the UI warns when disabled. Fresh defaults include the explicit title rule `* - Google Docs - Google Chrome`; existing saved custom rules are preserved. Title fallback cannot distinguish documents with identical titles; paired extension tab identity provides stronger matching. It does not scan inactive windows or save page content.

`state.work_detection` reports safe classification status, label, source, reason and generic recent-input age. Enabled explicit rules are immediate. `settings.ai_work_detection` (default true) enables a nonblocking selected-local-model metadata classifier for supported ambiguous work apps; disabled rules and companion exclusions remain authoritative. The model receives fixed work cues, never document contents, typed text, raw titles, identifiers or raw URLs. Input age uses GetLastInputInfo only and cannot distinguish typing from mouse input. Rule-recognized work gets a warning even if dialogue stalls: after at most four seconds a labelled application fallback requests it. This does not count as a local-model tool decision. Recognition results never bypass focus/session/cooldown checks.

Extension reports may include an ephemeral integer `tab_id`. Sanitized URL plus tab identity produces an in-memory observation digest, so changing documents or tabs on the same host invalidates a delayed action. Query/fragment values and raw URLs are not persisted or exposed through state. Legacy reports without `tab_id` remain compatible but cannot distinguish two tabs at the exact same URL.

State includes `earnings` (saved profile rewards, level/multiplier, rate policy,
UTC daily allowance and recent provenance) and `casino` (public durable table
round plus balance/profile). New fart score events credit the same wallet
atomically with a base of 5 monies. Multipliers 1×/2×/3× advance every ten reward
records, with a 20-monies bonus every tenth event. The combined score/game
bonus limit is 500 monies per UTC day, excluding passive earnings. Legacy burp
events and their issued rewards remain in history. Event IDs are idempotent.

The 20-second clam game pays up to 10 monies for at least three accepted self-reported catches, first three eligible rounds per UTC day, subject to the shared earning cap. Stop/pause/profile switch/startup invalidate active game tickets. Casino wins and demo deposits are separate from earned rewards. `docs/CASINO.md` documents the chosen table variants and stake reservation; all amounts remain fictional monies.

The desktop binds loopback on port 8765 by default. The Pi companion uses 8766; the separate friends service uses 8770, so both can run on the same PC. Local model inference uses loopback 11434. All `/api/` routes require `X-Chuddy-Token`. The HTML injects the desktop token in `window.CHUDDY_TOKEN`. Browser extension requests use a distinct paired token and can only call `/browser/report` and `/browser/rules`. JSON errors have `error`. Requests are bounded to 128 KiB.

`GET /api/state` returns `settings`, `messages`, `scores`, `balance`, `deadlines`, `session`, `session_remaining`, `cooldown_remaining`, `interruption`, `hardware`, `audio`, `voice`, `agent`, `logs`, `browser`, `real_time`, `profiles`, `profile`, `memory`, `leaderboard`. Missing optional integrations supply status objects. `messages` entries have id/role/text/source/time. `scores` retains historical `burp`, `fart`, `total` and `recent` reads. `scores.recent_farts` returns up to 12 recent fart events with provenance for the Leaderboard. `hardware` has mode/phase/yaw/spray/sequence. `agent` has status, model, source, draft, error, turn_id, trace, metrics. `voice` has status, engine, speaking, reference_ready. UI must escape user/model text.

While the source app is open with Local AI enabled, the local-model monitor
checks residency and renews the selected model's finite lease. It retries
service and model failures independently of the Chudding session. Under
automatic monitoring, `agent.ready`, `agent.model_status`, and
`agent.model_error` describe Ollama readiness. `agent.status`,
`agent.error` and `agent.source` describe the individual turn.

Use `POST /api/action` with a JSON object containing `action` and fields:

| action | fields and effect |
| --- | --- |
| `chat` | `text`; starts background turn, returns `turn_id`. Poll state for messages/draft/status. |
| `cancel_turn` | Stops current generation/queued voice. |
| `session` | `operation`: start, pause, resume, stop. |
| `dismiss` | Clears interruption and restores work. |
| `test_trigger` | Deliberate test work event; requires running session and cooldown. |
| `settings` | `values`: validated partial settings object. |
| `deadline_add` | `title`, `due`: ISO8601 with offset. |
| `deadline_import` | `items`: [{title,due}]; atomic validation/import. |
| `deadline_delete` | `id`. |
| `deposit` | `amount`, `request_id`: integer play monies only. |
| `spin` | `bet`, `request_id`: returns symbols/payout/balance; mock only. |
| `casino_start` | `game`: blackjack/poker/roulette, `bet`, `request_id`, `profile_id`; roulette also `selection`. Server reserves chips and creates a durable round. |
| `casino_act` | `round_id`, expected `revision`, `move`, optional `amount`, `request_id`, `profile_id`; validates turn/rules and settles once. |
| `casino_state` | Returns the selected profile's latest public round and wallet; private deck and unrevealed hands stay on server. |
| `casino_leave` | `opening_id` from `state.casino_opening_id`; releases the matching native casino cover. |
| `clam_game_start` | `request_id`, `profile_id`; issues a server-timed 20-second game ticket. |
| `clam_game_finish` | `round_id`, `request_id`, `caught`, `profile_id`; at least 3 accepted self-reported catches after 20 seconds, before 120 seconds. Fixed policy reward, never a client amount. |
| `clam_game_cancel` | `round_id`, `profile_id`; cancels an unfinished game. Stop/pause/profile switch also invalidate active tickets. |
| `shop` | `item` (fictional item ID), `request_id`: mock purchase only. |
| `score` | `kind:"fart"`, `request_id`; provenance manual. |
| `sound_test` | `kind:"fart"`. Returns `accepted` for queue admission. The labeled speaker event appears in state after confirmed playback. |
| `belly` | Runs simulator performance. |
| `mic` | `operation`: start/stop; deliberate only. |
| `voice_test` | Explicit spoken test, no microphone. |
| `model_warm` | Loads local model, no voice playback. |
| `profile_add` | `name`: local pseudonym. |
| `profile_select` | `id`. |
| `memory_delete` | `id`. |
| `friends_sync` | Sync to explicitly configured service. |
| `pi_status` | Query configured Pi. |
| `pi_belly` | Explicit command to configured Pi service, disarmed by default. |
| `pairing` | Returns browser pairing token only when user opens pairing view. |

`GET /api/devices` returns input devices. Static `GET /reference.png` returns preserved reference. Static assets are under `/web/`. Use local assets only. Speech defaults off on fresh install; user voice enable persists. Session and microphone always start stopped/off.

Settings keys include existing defaults, plus `ai_enabled` (true default when local model ready), `ai_endpoint` (http://127.0.0.1:11434), `ai_model` (llama3.1:8b-instruct-q4_K_M), `voice_engine` (auto), `friends_url`, `friends_room`, `pi_url`. Never put credential values into state. UI can show secret presence; pairing is a deliberate action. No generic paid cloud integration is required. The unpinned `qwen3:4b` alias was rejected during local evaluation; use the explicit instruction model.

Browser reports: `{url, active:true, focused:true, sent_at:<epoch milliseconds>, request_id}`. Only active/focused fresh reports matching desktop rules may interrupt. Clear reports with `{active:false,focused:false}` on focus loss. Strip query/fragment before matching or storing, never persist raw URLs. Extension categories/user patterns and enabled state are explicit.

Store backwards-compatible public methods: `settings()`, `setting(k,v)`, `message(role,text,source)`, `messages()`, `event(id,kind,source,confidence=None)`, `scores()`, `balance()`, `transact(delta,reason)`, `deadlines()`, `add_deadline(title,due)`, `log(kind,detail)`, `logs()`. Add atomic `update_settings(values)`, `import_deadlines(items)`, `delete_deadline(id)`, `deposit(amount,request_id)`, `spin(bet,request_id)`, `purchase(item,request_id)`, `profiles()`, `profile()`, `add_profile(name)`, `select_profile(id)`, `memories()`, `remember(key,value)`, `forget(id)`, `leaderboard()`, `events_for_sync()`. Return JSON-serializable dicts/lists. Let root know exact signatures once implemented.

Audio contract retains `AudioPipeline(store,model_path)`: `.info()`, `.devices()`, `.start()`, `.stop()`. Add `.classify(samples,sample_rate)` if practical for fixtures/Pi audio. Voice: `Voice(data_dir,settings_getter)` with `.say(text,turn_id=None)`, `.filler(turn_id)`, `.stop()`, `.info()`, `.test()`; sound playback `.sound(kind)` if useful. No automatic microphone or output playback in constructors.

Hardware: provide `Simulator(store,voice=None)` with `.belly()`, `.stop()`, `.info()`, never GPIO in desktop process. Pi client/service protocol documented by hardware owner. Keep components independent of controller imports to avoid cycles.

## Candidate observed-work protocol

For the optional pinned Llama model only, an observed-work turn receives the
actual configured destination/work and a single request_distraction tool. The
agent permits one attempt, validates its offered arguments, rejects unoffered or
duplicate calls, and gives the model its real result before a final response.
It can use at most the existing three generation rounds; there is no automatic
controller action or replacement answer. Ordinary chat and the default Qwen
observation path retain their behavior.

The optional agent fields model_rounds and action_outcome retain bounded raw
rounds and actual action outcome for diagnosis. They clear on cancel/new turn.
A denied/missing action is an explicit action error while the generated reply
retains local-model provenance and readiness. Interim promises are not shown or
spoken; a final candidate observation without an accepted action is not voiced.
Controller validation, cancellation, focus/session/cooldown and configured
destination remain authoritative. An accepted request means the performance is
starting, not that a destination has opened.

Fixture checks establish orchestration. Subsequent genuine model checks passed
allowed/stopped/stale/cancellation cases and ordinary memory/traits. A transport
follow-up uses a transient controller-owned user turn because the pinned Llama
template ignores later system messages. Raw role headers disappeared in the
three-case changed-transport check without stripping or rewriting replies. Llama
is now selected; saved choices in other existing installations are preserved.
