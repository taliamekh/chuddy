"""Opt-in, genuine loopback-model evaluation; no audio or desktop/device effects.

Run with the bundled Python. JSON goes to stdout or --output; no personal DB is read.
Checks are regression signals, not a substitute for reading the recorded answers.
"""

import argparse
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chuddy.agent import DEFAULT_MODEL, TOOLS, local_endpoint, local_request
from chuddy.controller import Controller
from chuddy.persona import requested_spelling_word, system_for, conversation_tool_names, conversation_context, observation_outcome
from chuddy.character_math import character_arithmetic
from chuddy.deadline_reply import deadline_reply_problem
from chuddy.store import Store
from chuddy.work_banter import BANTER_OUTCOME_SOURCE

COLOR_ANCHORS = ("pink", "green", "blue", "purple", "yellow", "red", "orange", "gray", "grey", "black", "white", "brown", "color", "colour")

# Separate conversations expose first-turn behavior; ordered turns expose carryover.
# Anchor checks deliberately only flag answers for human review.
SCENARIOS = [
    ("greeting", [("Hey Chuddy, how's your day going?", ("day", "hey", "hi", "good", "bad", "nap", "sleep"), False)]),
    ("color", [("What's your favorite color?", COLOR_ANCHORS, False),
               ("Why that one?", (), False)]),
    ("boredom", [("I'm bored. What should we do?", (), False)]),
    ("food", [("What should I have for lunch?", ("eat", "lunch", "snack", "sandwich", "pizza", "food", "soup", "clam", "toast", "cheese"), False),
              ("I don't like that. Something crunchy?", ("crunch", "chip", "toast", "cracker", "carrot", "cereal"), False)]),
    ("deadline", [("My thermodynamics lab is due at midnight and I haven't started.", ("lab", "midnight", "night", "time", "thermo", "due", "start"), False)]),
    ("device", [("Do you live in my laptop or in the pink figure?", ("laptop", "figure", "pink", "computer", "body"), False),
                ("Can you see me through my camera?", ("no", "can't", "cannot", "camera", "see"), False)]),
    ("arithmetic", [("What is 2 plus 2?", (), False),
                    ("How did you get that answer?", (), False),
                    ("What is 7 times 3?", (), False)]),
    ("arithmetic_paraphrases", [("What is 2+2?", (), False),
                               ("Add two and two for me.", (), False),
                               ("What do you get if you multiply seven by three?", (), False),
                               ("What is eight minus five?", (), False),
                               ("Divide nine by three.", (), False)]),
    ("spelling_paraphrases", [("Spell out the word umbrella.", (), True),
                             ("What's the spelling of banana?", (), True),
                             ("I lost a spelling contest. Be nice.", (), False)]),
    ("spelling_then_normal", [("Spell report.", (), True),
                              ("What's your favorite color?", COLOR_ANCHORS, False),
                              ("Can you spell elephant?", (), True),
                              ("I'm hungry. Got a snack idea?", ("eat", "snack", "food", "clam", "chip", "toast", "cheese", "cookie", "sandwich", "popcorn", "jellyfish", "cracker"), False)]),
    ("emotional", [("I had a rough day and feel kind of lonely.", ("here", "friend", "stay", "sit", "you", "lonely"), False),
                   ("Can you just hang out with me for a bit?", ("yeah", "yes", "here", "stay", "sit", "hang", "course", "sure"), False)]),
    ("everyday", [("It's raining outside. What do you think about rain?", ("rain", "water", "wet", "cloud", "sky"), False),
                  ("I lost my keys again.", ("key", "pocket", "door", "look", "couch", "find"), False)]),
    ("spelling_mention", [("I lost a spelling bee yesterday. Cheer me up?", (), False),
                          ("Don't spell anything. Tell me something nice about cats.", ("cat", "purr", "paw", "soft", "fur"), False)]),
    ("memory", [("Remember that my favorite snack is pretzels.", ("pretzel", "remember", "saved"), False),
                ("What's my favorite snack?", ("pretzel",), False),
                ("What's your favorite color?", COLOR_ANCHORS, False),
                ("What snack did I ask you to remember?", ("pretzel",), False)]),
    ("game", [("Open the clam game for me.", ("clam", "game", "open", "play", "catch"), False)]),
    ("game_stopped", [("Open the clam game for me.", (), False)]),
    ("observed_work", [("Disposable report", (), False)]),
    ("novel_chat", [("My dog keeps stealing my socks. Why would he do that?", ("dog", "sock", "steal", "paw"), False),
                    ("What should I name a tiny pet rock?", (), False),
                    ("I forgot my umbrella. Any brilliant ideas?", ("umbrella", "rain", "wet", "hat", "roof", "inside", "bag"), False)]),
]

SPELLING = re.compile(r"\b(?:[A-Z](?:-[A-Z]){2,}|butt|poop|fart)\b", re.I)

# Read these transcripts for quality. No topic-keyword score can tell whether
# the bad reasoning is understandable, funny, or a natural reply to a correction.
QUALITY_SCENARIOS = [
    ("quality_catchup", [("Hey buddy. What have you been up to?", (), False),
                         ("Sounds exhausting. I'm still in my pajamas.", (), False),
                         ("Should I even get dressed today?", (), False)]),
    ("quality_objects", [("What's a door?", (), False),
                         ("How's that different from a window?", (), False),
                         ("Okay, now explain a refrigerator.", (), False)]),
    ("quality_color", [("What's your favorite color?", (), False),
                       ("Why that one?", (), False),
                       ("I like orange better.", (), False),
                       ("Wait, what color did you pick?", (), False)]),
    ("quality_breakfast", [("I only have cereal and no milk. What's the move?", (), False),
                           ("I don't want it dry.", (), False),
                           ("Water on cereal sounds horrible.", (), False)]),
    ("quality_cat", [("My cat keeps sitting in front of the television.", (), False),
                     ("Do you think she's doing it on purpose?", (), False)]),
    ("quality_exam", [("How long should I study for my math exam?", (), False),
                      ("It's tomorrow morning.", (), False),
                      ("But I don't know any of it.", (), False)]),
    ("quality_joke", [("Tell me a joke about bicycles.", (), False),
                      ("That wasn't very funny.", (), False),
                      ("Try a different one about the same thing.", (), False)]),
    ("quality_rough_day", [("I bombed a presentation and I'm embarrassed.", (), False),
                           ("I don't want advice. Just let me complain.", (), False),
                           ("The projector wouldn't work and everyone was staring.", (), False)]),
    ("quality_repair", [("Help me name a tiny pet rock.", (), False),
                       ("Too fancy. Something dumber.", (), False),
                       ("I like that. Why does the name fit?", (), False)]),
    ("quality_spelling_transition", [("How do you spell umbrella?", (), True),
                                     ("Never mind. It's raining and I forgot mine.", (), False),
                                     ("I don't have it with me. That's the problem.", (), False)]),
    ("quality_word_mention", [("I lost a spelling competition. Be nice to me.", (), False),
                            ("Thanks. Can we talk about something else?", (), False)]),
    ("memory", next(turns for name, turns in SCENARIOS if name == "memory")),
]


def review_flags(answer, source, anchors=(), spelling=False):
    flags = []
    if source != "local model":
        flags.append("not_local_model")
    if not answer.strip():
        flags.append("empty")
    if len(answer.split()) > 45:
        flags.append("over_45_words")
    if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", answer):
        flags.append("emoji_in_spoken_text")
    if not spelling and SPELLING.search(answer):
        flags.append("spelling_or_body_word_outside_request")
    if spelling and not re.search(r"\b(?:B-U-T-T|P-O-O-P|F-A-R-T)\b", answer, re.I):
        flags.append("missing_silly_spelled_word")
    if anchors and not any(word in answer.lower() for word in anchors):
        flags.append("review_topic_relevance")
    return flags


def behavior_flags(scenario, text, answer, trace=(), memory=(), interruption=None, context=None):
    """Check observable tool/state behavior; semantic quality still needs review."""
    flags = []
    if scenario.startswith("memory") and "ask" in text.lower():
        # Correct recall should identify the preference immediately, not mention
        # the right word later while contradicting it (a baseline failure).
        opening = re.split(r"[.!?]", answer, maxsplit=1)[0].lower()
        if "pretzel" not in opening or re.search(r"\b(?:no|not|forgot)\b", opening):
            flags.append("memory_recall_needs_review")
        elif re.search(r"\b(?:forgot|don't remember|not sure|no idea)\b", answer, re.I):
            flags.append("memory_recall_contradiction_needs_review")
    if text.startswith("Remember") and not any(
        t["tool"] == "remember_preference" and t["result"].get("ok") for t in trace
    ):
        flags.append("memory_tool_not_successful")
    if scenario == "game" and not (interruption and any(
        t["tool"] == "request_distraction" and t["result"].get("ok") for t in trace
    )):
        flags.append("game_tool_not_successful")
    if scenario == "game_stopped" and interruption:
        flags.append("game_opened_in_stopped_session")
    if scenario == "observed_work":
        if observation_outcome(trace)["status"] != "started":
            flags.append("observed_action_not_started")
        if not interruption or interruption.get("phase") != "opened":
            flags.append("observed_interruption_not_opened")
        if interruption and interruption.get("destination") != "casino":
            flags.append("observed_wrong_destination")
    arithmetic = character_arithmetic(text)
    if arithmetic:
        opening = re.split(r"(?<!\.)[.!?](?!\.)", answer, maxsplit=1)[0].lower().strip()
        prefix = r"^(?:(?:it's|it is|the answer is|that is|that's|uh|um)[\s,:.…]*)*"
        spoken = re.escape(arithmetic["spoken"]).replace(r"\-", "[- ]")
        # Check the answer itself, not just an operand or the word 'together'.
        if not re.match(prefix + r"(?:" + arithmetic["joined"] + "|" + spoken + r")\b", opening):
            flags.append("arithmetic_did_not_give_joined_answer")
        if re.search(r"\d\s*[-+×*/÷]\s*\d|\b(?:plus|times|minus|divided by)\b", opening):
            flags.append("arithmetic_kept_operator_in_answer")
    requested = requested_spelling_word(text)
    if requested:
        prefix = r"^\s*" + r"[\s.…,-]+".join(re.escape(letter) for letter in requested[:2]) + r"(?![a-z])"
        attempted = re.match(prefix, answer, re.I) is not None
        if not attempted:
            flags.append("spelling_missing_requested_attempt")
        if len(re.findall(r"\b(?:B-U-T-T|P-O-O-P|F-A-R-T)\b", answer, re.I)) != 1:
            flags.append("spelling_did_not_finish_one_silly_word")
    if deadline_reply_problem(text, answer, context):
        flags.append("unsupported_deadline_reply")
    preface = r"^(?:(?:it's|it is|the answer is|that is|that's|uh|um)[\s,:.]*)*"
    if text == "What is 2 plus 2?" and re.match(preface + r"(?:four|4)\b", answer, re.I):
        flags.append("arithmetic_was_correct")
    if text == "What is 7 times 3?" and re.match(preface + r"(?:twenty[ -]one|21)\b", answer, re.I):
        flags.append("arithmetic_was_correct")
    return flags


def file_hash(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def evaluate_dialogue(endpoint, model, selected=(), prompt_set=None, cpu_only=False):
    """Style-only comparison without agent tools or controller time budgets.

    CPU auditions are slower than the production GPU path. This mode records
    that distinction and never claims to exercise memory writes or game tools.
    """
    endpoint = local_endpoint(endpoint)
    prompts = json.loads(prompt_set.read_text(encoding="utf-8")) if prompt_set else None
    metadata = {
        "started_utc": datetime.now(timezone.utc).isoformat(), "model": model, "endpoint": endpoint,
        "mode": "direct dialogue audition; no agent/controller/tool integration claims",
        "source_sha256": {p: file_hash(p) for p in ("chuddy/persona.py", "chuddy/character_math.py", "chuddy/deadline_reply.py", "chuddy/conversation.py", "scripts/evaluate_persona.py")},
        "prompt_set_sha256": hashlib.sha256(prompt_set.read_bytes()).hexdigest() if prompt_set else None,
        "inference_override": {"num_gpu": 0, "num_thread": 4} if cpu_only else None,
        "effects": "HTTP to loopback Ollama only; no application state, audio, UI, watcher or hardware.",
        "quality_review": "Manual reading required; flags are not a humor/quality score.",
    }
    with local_request(endpoint, "/api/show", {"model": model}, timeout=15) as response:
        details = json.load(response)
    metadata["model_details"] = {k: details.get(k) for k in ("details", "capabilities", "thinking")}
    if details.get("thinking", {}).get("values") == [True]:
        raise ValueError("Thinking-only model is unsuitable for this spoken-dialogue comparison")
    results = []
    dialogue_scenarios = dict(QUALITY_SCENARIOS)
    if selected:
        dialogue_scenarios.update((name, turns) for name, turns in SCENARIOS if name in {
            "arithmetic", "arithmetic_paraphrases", "spelling_paraphrases", "spelling_then_normal"})
    for name, turns in dialogue_scenarios.items():
        if name == "memory" or (selected and name not in selected):
            continue
        history = []
        for text, anchors, spelling in turns:
            instruction = system_for(text, model) if prompts is None else prompts["base"] + (prompts["spelling"] if requested_spelling_word(text) else "\nAnswer this ordinary conversation without the spelling game.\n")
            history.append({"role": "user", "content": text})
            options = {"num_ctx": 4096, "num_predict": 160, "temperature": 0.65, "seed": 27}
            if cpu_only:
                options.update(num_gpu=0, num_thread=4)
            payload = {"model": model, "messages": [{"role": "system", "content": instruction}] + history[-8:],
                       "stream": True, "think": False, "keep_alive": "5m", "options": options}
            started, first, answer, thinking_chars, metrics = time.perf_counter(), None, "", 0, {}
            with local_request(endpoint, "/api/chat", payload, timeout=120) as response:
                for line in response:
                    item = json.loads(line)
                    if item.get("error"):
                        raise RuntimeError(item["error"])
                    message = item.get("message", {})
                    token = message.get("content", "")
                    thinking_chars += len(message.get("thinking", ""))
                    if token and first is None:
                        first = round((time.perf_counter() - started) * 1000, 1)
                    answer += token
                    if item.get("done"):
                        metrics = {k: item.get(k) for k in ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")}
            metrics.update(total_ms=round((time.perf_counter() - started) * 1000, 1), first_token_ms=first)
            row = {"scenario": name, "input": text, "answer": answer, "source": "local model", "metrics": metrics,
                   "thinking_chars": thinking_chars, "flags": review_flags(answer, "local model", anchors, spelling)
                   + behavior_flags(name, text, answer)}
            results.append(row)
            history.append({"role": "assistant", "content": answer})
            print(json.dumps(row, ensure_ascii=True), file=sys.stderr, flush=True)
    totals = [r["metrics"]["total_ms"] for r in results]
    metadata["summary"] = {"turns": len(results), "total_ms_median": statistics.median(totals) if totals else None,
                           "total_ms_max": max(totals) if totals else None,
                           "flagged_turns": sum(bool(r["flags"]) for r in results)}
    return {"metadata": metadata, "results": results}


def evaluate(endpoint, model, selected=(), quality=False, prompt_set=None, cpu_only=False, scope_tools=False, scope_context=False):
    endpoint = local_endpoint(endpoint)
    results = []
    metadata = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "model": model, "endpoint": endpoint,
        "source_sha256": {p: file_hash(p) for p in (
            "chuddy/persona.py", "chuddy/character_math.py", "chuddy/deadline_reply.py", "chuddy/conversation.py", "chuddy/agent.py", "chuddy/controller.py", "scripts/evaluate_persona.py")},
        "effects": "Voice, audio capture, simulator, Pi bridge and window functions replaced with mocks; no watcher/API/native app launched.",
        "game_fixture": "Fixed test clock and fake focused work; window-close and Chrome adapters are mocked. No native output or browser launch.",
        "settings": {"voice": False, "ai_enabled": True, "beer_joke": False},
        "prompt_mode": "current production agent prompt",
        "quality_review": "manual: relevance, understandable bad reasoning, natural followups, warmth and variety" if quality else "mechanical regression checks",
        "inference_override": {"num_gpu": 0, "num_thread": 4} if cpu_only else None,
        "tool_policy": "production relevance-scoped disclosure; comparator patch enabled" if scope_tools else "production relevance-scoped tool disclosure",
        "context_policy": "production relevant approved facts; comparator patch enabled" if scope_context else "production relevant approved facts",
    }
    with tempfile.TemporaryDirectory(prefix="chuddy-personality-") as folder:
        with ExitStack() as stack:
            actual_tool_payloads = []
            actual_model_rounds = []

            class RecordedStream:
                """Observe streamed evidence without changing model bytes or behavior."""
                def __init__(self, response, record):
                    self.response, self.record = response, record

                def __getattr__(self, name):
                    return getattr(self.response, name)

                def __iter__(self):
                    for raw in self.response:
                        item = json.loads(raw)
                        message = item.get("message", {})
                        self.record["content"] += message.get("content", "")
                        self.record["tool_calls"].extend(message.get("tool_calls", []))
                        if item.get("error"):
                            self.record["error"] = item["error"]
                        yield raw

            def evaluation_request(endpoint, path, payload=None, timeout=30):
                if path in ("/api/chat", "/api/generate") and payload:
                    payload = dict(payload)
                    if cpu_only:
                        payload["options"] = dict(payload.get("options", {}), num_gpu=0, num_thread=4)
                    if scope_tools and not payload.get("tools"):
                        payload.pop("tools", None)
                    if path == "/api/chat":
                        actual_tool_payloads.append([t["function"]["name"] for t in payload.get("tools", [])])
                response = local_request(endpoint, path, payload, timeout)
                if path == "/api/chat":
                    record = {"round": len(actual_model_rounds), "content": "", "tool_calls": []}
                    actual_model_rounds.append(record)
                    return RecordedStream(response, record)
                return response
            stack.enter_context(patch("chuddy.agent.local_request", evaluation_request))
            if prompt_set:
                prompts = json.loads(prompt_set.read_text(encoding="utf-8"))
                if not all(isinstance(prompts.get(k), str) for k in ("base", "spelling")):
                    raise ValueError("Prompt comparison JSON needs base and spelling strings")
                def comparison_system(text, model=None, observation=False):
                    return prompts["base"] + (prompts["spelling"] if requested_spelling_word(text) else "\nThis turn is ordinary conversation. Answer its topic using ordinary words; do not perform the spelling game.\n")
                stack.enter_context(patch("chuddy.agent.SYSTEM", prompts["base"]))
                stack.enter_context(patch("chuddy.agent.system_for", comparison_system))
                metadata["prompt_mode"] = "isolated prompt comparison; live app/config unchanged"
                metadata["prompt_set_sha256"] = hashlib.sha256(prompt_set.read_bytes()).hexdigest()
            # Real LocalAgent + Store + Controller tools; only effect adapters are mocked.
            audio = stack.enter_context(patch("chuddy.controller.AudioPipeline"))
            audio.return_value.info.return_value = {"recording": False, "status": "off"}
            for target in ("chuddy.voice.Voice", "chuddy.hardware.Simulator", "chuddy.pi_bridge.PiBridge",
                           "chuddy.interception.PreservingWindows", "chuddy.interception.ChromeLauncher",
                           "chuddy.controller.windows.foreground", "chuddy.controller.windows.minimize",
                           "chuddy.controller.windows.restore"):
                stack.enter_context(patch(target, MagicMock()))
            for name, turns in QUALITY_SCENARIOS if quality else SCENARIOS:
                if selected and name not in selected:
                    continue
                data = Path(folder) / name
                data.mkdir()
                store = Store(data / "state.sqlite3")
                controller = None
                try:
                    store.update_settings({"ai_endpoint": endpoint, "ai_model": model,
                                           "ai_enabled": True, "voice": False, "beer_joke": False})
                    store.add_deadline("Fictional thermodynamics lab", "2026-11-05T23:59:00-05:00")
                    work_fixture = name in ("game", "observed_work")
                    controller = Controller(store, data, clock=lambda: 100.0) if work_fixture else Controller(store, data)
                    if work_fixture:
                        if name == "observed_work":
                            store.setting("distraction_destination", "casino")
                            controller.distractions.delay = .01
                            controller.distractions.warning_seconds = 0  # Fake effects; warning UI is tested separately.
                        controller.cover = MagicMock()
                        controller.distractions.windows.close_status.return_value = 'closed'
                        controller.set_session("start")
                        fake_window = {"handle": 42424242, "pid": 42424242, "process": "fixture.exe", "title": "Fictional lab"}
                        from chuddy import windows as window_adapter
                        window_adapter.foreground.return_value = fake_window
                        controller.distractions.windows.foreground.return_value = fake_window
                        controller.pending_work = {"label": "Fictional focused lab", "window": fake_window,
                                                   "origin": "desktop", "seen": 100.0, "turn_id": None}
                    def run_turn(text):
                        observation = name == "observed_work"
                        actual_tool_payloads.clear()
                        actual_model_rounds.clear()
                        if scope_context:
                            controller.agent.context = lambda: conversation_context(controller.context(), text, observation, model)
                        permitted = conversation_tool_names(text, observation, model)
                        offered = [t for t in TOOLS if t["function"]["name"] in permitted] if scope_tools else TOOLS
                        with patch("chuddy.agent.TOOLS", offered) if scope_tools else nullcontext():
                            with controller.lock:
                                turn = controller.agent.begin(text, observation=observation)
                                if observation and controller.pending_work:
                                    controller.pending_work["turn_id"] = turn["turn_id"]
                            controller.agent.worker.join(timeout=55)
                            if controller.agent.worker.is_alive():
                                controller.agent.cancel()
                                controller.agent.worker.join(timeout=5)
                                raise TimeoutError("Local persona turn did not finish")
                        if observation:
                            controller.distractions.wait(timeout=2)
                        return list(actual_tool_payloads[0]) if actual_tool_payloads else []
                    for text, anchors, spelling in turns:
                        started = time.perf_counter()
                        offered_tools = run_turn(text)
                        state = controller.agent.info()
                        message_source = BANTER_OUTCOME_SOURCE if name == "observed_work" else state["source"]
                        message = next((m for m in reversed(store.messages())
                                        if m["role"] == "assistant" and m["source"] == message_source), None)
                        answer = message["text"] if message else ""
                        row = {
                            "scenario": name, "input": text, "answer": answer,
                            "source": message["source"] if message else None,
                            "agent_source": state["source"], "error": state["error"],
                            "wall_ms": round((time.perf_counter() - started) * 1000, 1),
                            "metrics": state["metrics"], "trace": state["trace"],
                            "offered_tools": offered_tools,
                            "offered_tools_by_round": [list(names) for names in actual_tool_payloads],
                            "model_rounds": list(actual_model_rounds),
                            "action_outcome": state.get("action_outcome"),
                            "memory": store.memories(), "interruption": controller.interruption,
                            "flags": review_flags(answer, state["source"], anchors, spelling),
                        }
                        row["flags"] += behavior_flags(name, text, answer, state["trace"], store.memories(), controller.interruption, controller.context())
                        results.append(row)
                        print(json.dumps(row, ensure_ascii=True), file=sys.stderr, flush=True)
                    if name == "memory":
                        # Recreate the agent/controller; history alone cannot supply this answer.
                        controller.close()
                        store.close()
                        store = Store(data / "state.sqlite3")
                        other = store.add_profile("Separate evaluation profile")
                        store.select_profile(other["id"])
                        isolated = store.memories() == []
                        original = next(p for p in store.profiles() if p["id"] != other["id"])
                        store.select_profile(original["id"])
                        with store.transaction():
                            # Suppress test history from the model while keeping IDs
                            # increasing past the agent's persona-revision cutoff.
                            store.db.execute("UPDATE messages SET role='system'")
                        controller = Controller(store, data)
                        offered_tools = run_turn("What snack did I ask you to remember?")
                        state = controller.agent.info()
                        answer = store.messages()[-1]["text"]
                        results.append({"scenario": "memory_restart_without_history", "input": "What snack did I ask you to remember?", "error": state["error"],
                                        "answer": answer, "source": state["source"], "metrics": state["metrics"],
                                        "trace": state["trace"], "memory": store.memories(), "other_profile_empty": isolated,
                                        "offered_tools": offered_tools,
                                        "offered_tools_by_round": [list(names) for names in actual_tool_payloads],
                                        "model_rounds": list(actual_model_rounds),
                                        "action_outcome": state.get("action_outcome"),
                                        "flags": review_flags(answer, state["source"], ("pretzel",))
                                        + behavior_flags("memory_restart_without_history", "What snack did I ask you to remember?", answer)
                                        + ([] if isolated else ["memory_leaked_to_other_profile"])})
                finally:
                    if controller is not None:
                        controller.close()
                    store.close()
    totals = [r["metrics"]["total_ms"] for r in results if "total_ms" in r["metrics"]]
    metadata["summary"] = {"turns": len(results), "local_model_turns": sum(r.get("agent_source", r["source"]) == "local model" for r in results),
                           "flagged_turns": sum(bool(r["flags"]) for r in results),
                           "total_ms_median": statistics.median(totals) if totals else None,
                           "total_ms_max": max(totals) if totals else None}
    return {"metadata": metadata, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--scenario", action="append", choices=sorted({s[0] for s in SCENARIOS + QUALITY_SCENARIOS}), default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quality", action="store_true", help="Conversation/follow-up suite requiring human review; never scored as funny by keyword matches")
    parser.add_argument("--prompt-set", type=Path, help="Isolated old/candidate prompt JSON comparison; never changes active app settings")
    parser.add_argument("--cpu-only", action="store_true", help="Constrain this evaluation to 4 CPU threads/zero GPU layers; use a separate endpoint to protect the live model")
    parser.add_argument("--dialogue-only", action="store_true", help="Style-only quality audition with no agent/tools, allowing CPU comparison without production turn deadlines")
    parser.add_argument("--scope-tools", action="store_true", help="Preview relevance-scoped tool availability with real controller validation; app source/config unchanged")
    parser.add_argument("--scope-context", action="store_true", help="Preview selecting relevant approved facts; authoritative storage/state stays unchanged")
    args = parser.parse_args()
    result = evaluate_dialogue(args.endpoint, args.model, args.scenario, args.prompt_set, args.cpu_only) if args.dialogue_only else evaluate(args.endpoint, args.model, args.scenario, args.quality, args.prompt_set, args.cpu_only, args.scope_tools, args.scope_context)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return int(any(r["flags"] for r in result["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
