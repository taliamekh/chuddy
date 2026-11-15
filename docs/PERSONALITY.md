# Chuddy's conversation and sound personality

## General-conversation source review

The user rejected the conversational quality after the earlier mechanical pass.
That verdict supersedes any implication below that 32 passing sample turns made
the character good enough. In particular, the first new 37-turn follow-up suite
had zero mechanical flags but was poor on manual review: it lost conversational
constraints, reused comforting lines, and supplied irrelevant imaginary scenes.

`scripts/evaluate_persona.py --quality` now tests complete exchanges and
corrections. `--prompt-set` compares candidate instruction JSON in an isolated
process without changing the live app or its model configuration. No answer is
replaced or retried to get a desirable line. Manual review asks whether each
reply (1) follows the actual point and constraints, (2) has understandable simple
reasoning or a natural friendly reaction, (3) sounds like brief spoken dialogue,
and (4) avoids recycling the same gag/imagery. Keywords do not assess these.

### Transcript study requested by the user

Reference material reviewed: the already downloaded captions for Nickelodeon
UK's [Patrick compilation](https://www.youtube.com/watch?v=2vqW0q7yZj8), including
the trash-collection, secret-box and toy-store exchanges; accessible indexed
dialogue from the fan transcriptions of
[Something Smells](https://spongebob.fandom.com/wiki/Something_Smells/transcript),
[I'm with Stupid](https://spongebob.fandom.com/wiki/I%27m_with_Stupid/transcript),
[Big Pink Loser](https://spongebob.fandom.com/wiki/Big_Pink_Loser/transcript),
[Tea at the Treedome](https://spongebob.fandom.com/wiki/Tea_at_the_Treedome/transcript)
and [Club SpongeBob](https://spongebob.fandom.com/wiki/Club_SpongeBob/transcript).
Fan transcripts are secondary sources, not verified production scripts. Some
direct pages were unavailable; their accessible indexed dialogue was reviewed.
Two other official-video caption retrieval attempts returned unavailable, so no
claim is made to have read their captions. No audio/video was downloaded for
this conversation study, and no source transcript is redistributed here.

The following are interpretations of those exchanges, not quotations:

| Observed pattern | Application to general conversation |
| --- | --- |
| A simple immediate goal drives an argument; the goal survives several turns. | Give Chuddy an opinion or lazy motive, rather than an unrelated scene per reply. |
| He attaches to a concrete part of the other person's words and misinterprets it. | Keep the wrong reasoning traceable to the actual topic. |
| He can feel proud, indignant, worried, excited or reassuring. | Vary reactions; a hesitant monotone is not the whole personality. |
| Ordinary replies connect the larger comic moments. | Permit a plain response or a relevant question; do not force every sentence into a joke. |
| He offers help with sincere confidence despite misunderstanding the cause. | Be an engaged friend with flawed reasoning, rather than a generic therapist or random-word generator. |
| Some episodes use insensitive mockery or stubbornness as conflict. | Preserve Chuddy's requested warmth; do not copy insults into replies to personal distress. |

Instructions derived from these patterns are being tested on new situations,
not used as a database of recognizable quotations. Several 4B alternatives were
rejected after manual review. Two free local alternatives were compared in isolation; the verdict and full
synthetic transcripts are recorded below. Live configuration and voice training
retain their normal scope.

Chuddy answers the actual question with a small, sincere, physical misunderstanding.
He is warm and slow, not a generic assistant ending with a joke. Replies aim for
5–35 words, with occasional pauses. Saved user preferences stay accurate; his
arithmetic and spelling are deliberately bad. Real application arithmetic,
deadlines, session state and permitted actions stay under controller authority.

`chuddy/persona.py` owns the instructions and a small, honestly labeled offline
fallback. `system_for(text)` adds the spelling game only for an explicit request.
Ordinary prompts contain no silly letter examples. The agent uses
this function and removed the duplicate unconditional spelling example from
`chuddy/agent.py`. The model still generates the replies and chooses permitted
tools; no response replacement, retry-to-pass or canned conversation dispatcher
was added.

The supplied report example remains: “R... E... uh... B-U-T-T. Report. Nailed it.”
Mentioning a report or losing a spelling bee does not invite that performance.
Follow-ups should stay on the subject; ordinary food/color/weather/device chatter
should not repeat stored preferences or body-word strings.

## Sound variation

`FartShuffleBag` chooses randomly without replacement, exhausting all supplied
clips before another cycle. It prevents immediate repeats across cycle boundaries.
The audio owner has integrated it into `Voice.sound('fart')`, using the existing
13 distinct `fart-youtube-*.wav` clips. The duplicate default `fart.wav` is excluded
from the bag. These are the existing CC0 archive excerpts documented in
`assets/audio/SOURCES.md`. Belly and sound
test callers share the voice sound adapter. A running old process needs the
coordinator's normal reload before using the updated code.

Tests exercised 1,300 selections, 13-clip cycles, no adjacent repeats, two-clip
boundaries, duplicate IDs, singleton fallback, empty input and concurrent access.
A separate test calls the actual Voice sound enqueue method with its constructor
and playback worker omitted: 26 queued jobs use all 13 variants and retain their
cancellation generation. This is an enqueue/selection check, not a speaker test.

## Reproducing the local-model evaluation

From the repository root, use the desktop virtual environment:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_persona_relevance tests.test_agent -v
.\.venv\Scripts\python.exe scripts/evaluate_persona.py --output "$env:TEMP\chuddy-persona-evaluation.json"
```

The genuine pinned `qwen3:4b-instruct-2507-q4_K_M` runs at loopback port 11434.
No model pull occurs. The evaluator uses fresh temporary SQLite stores, the real
LocalAgent, real Controller tools and real local inference. Voice, microphone,
simulator, Pi bridge and window operations are mocked. It never starts the HTTP
server, desktop watcher or native app. All generated dialogue, provenance,
first-token/total timing, tool outcomes and memory snapshots are recorded.

The restart test reopens the test DB/controller and excludes previous test chat
from model history by changing those test rows to system-role records. IDs remain
monotonic across the agent's persona-revision boundary. Stored preferences remain
available, and a second test profile is checked to have no copied memories.
No user's real conversation or database is opened or deleted.

The script exits nonzero for review flags, including an offline fallback. Topic
anchors are intentionally weak heuristics: pronouns and unusual yet relevant
answers can be flagged, and a topical word can hide a bad answer. Read the
transcript. The initial 32-turn run was entirely genuine local-model output, had
no spelling/body-word leakage outside explicit requests, retained memory after
restart, and successfully exercised allowed and denied game tools. Its one
automatic relevance flag was “Crumbs” as a snack suggestion; manual review found
it relevant. These observations are a finite sample, not a reliability guarantee.

## Free local voice improvement

The user explicitly reports that the current voice is not accurate. Keep that
verdict separate from successful waveform generation or timing measurements.
The existing configured [Chatterbox](https://github.com/resemble-ai/chatterbox)
is free, MIT-licensed and supports local reference-conditioned speech. The first
comparison should use several clean single-speaker references from the existing
local source, the same test lines and fixed seeds. Prefer excerpts without music,
other speakers or strong effects. Regenerate cached letters and filler after
selecting a reference, then compare timbre, cadence and spelling clarity by ear.

[RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) is an
MIT-licensed local voice-conversion alternative if clean-reference Chatterbox
still misses the timbre. It requires a suitable trained model or training data,
and another latency/VRAM benchmark. No verified Patrick RVC model has been found
or tested here. Actual voice similarity remains a listening check.

## Remaining limits

- Small local models still sometimes repeat an example or a phrase, offer vague
  suggestions, describe imagined things as seen, or confuse the figure with the
  PC. Topic relevance and character quality need broader unscripted use.
- The explicit-spelling cue is conservative English matching, not a full intent
  classifier. Quoted/indirect requests and pronouns may need more context. The
  offline fallback has no conversation memory and remains labeled as fallback.
- Generated text can violate word/format instructions. No runtime output filter
  or real microphone/speaker/listening validation was added in this work.
- Ollama and the audio worker share hardware; these warm local-model timings do
  not establish cold startup, voice latency or a reliable population p95.
