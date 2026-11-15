"""Measure genuine local inference/tool calls without playing audio or capturing input."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chuddy.agent import local_request
from chuddy.persona import SYSTEM


def run(model, messages, tools=None):
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 4096,
            "num_predict": 150,
            "temperature": 0.35,
            "seed": 42,
        },
    }
    if tools:
        payload["tools"] = tools
    start = time.perf_counter()
    first, content, calls, final = None, "", [], {}
    with local_request("http://127.0.0.1:11434", "/api/chat", payload, 120) as response:
        for raw in response:
            item = json.loads(raw)
            if "error" in item:
                raise RuntimeError(item["error"])
            message = item.get("message", {})
            if first is None and (message.get("content") or message.get("tool_calls")):
                first = (time.perf_counter() - start) * 1000
            content += message.get("content", "")
            calls.extend(message.get("tool_calls", []))
            if item.get("done"):
                final = item
    return {
        "text": content,
        "tool_calls": calls,
        "first_response_ms": round(first, 2) if first else None,
        "total_ms": round((time.perf_counter() - start) * 1000, 2),
        "load_ms": round(final.get("load_duration", 0) / 1e6, 2),
        "tokens": final.get("eval_count"),
        "tokens_per_second": round(
            final.get("eval_count", 0) / (final.get("eval_duration", 1) / 1e9), 2
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.1:8b-instruct-q4_K_M")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".chuddy/benchmarks/ai.json"
    )
    args = parser.parse_args()
    tests = [
        ("cold introduction", "Hello. Who are you?"),
        ("arithmetic", "What is 2 + 2?"),
        ("spelling", "Spell report for me."),
        ("deadline", "My fictional lab report is due at midnight. Should I start?"),
        ("casual", "I am bored. What should we do?"),
        ("memory context", "What is my favorite snack?"),
    ]
    results = []
    prompt = (
        SYSTEM
        + "\nReply in under 35 words, in character. No stage directions or markdown. Local memory: favorite snack is popcorn."
    )
    for name, text in tests:
        result = run(
            args.model,
            [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        )
        result["case"] = name
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    tool_schema = [
        {
            "type": "function",
            "function": {
                "name": "remember_preference",
                "description": "Store a non-sensitive preference when explicitly requested.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
            },
        }
    ]
    messages = [
        {
            "role": "system",
            "content": prompt
            + " Use remember_preference when asked to remember a preference.",
        },
        {"role": "user", "content": "Remember that my favorite game is clam catch."},
    ]
    tool_result = run(args.model, messages, tool_schema)
    tool_result["case"] = "real model tool choice"
    results.append(tool_result)
    print(json.dumps(tool_result, ensure_ascii=True), flush=True)
    with local_request("http://127.0.0.1:11434", "/api/ps") as response:
        loaded = json.load(response)
    warm = [
        r["first_response_ms"]
        for r in results[1:]
        if r["first_response_ms"] is not None
    ]
    summary = {
        "model": args.model,
        "runtime": "Ollama local-only portable runtime; no paid API",
        "speaker_playback": False,
        "microphone_capture": False,
        "results": results,
        "warm_first_response_p50_ms": round(statistics.median(warm), 2),
        "warm_first_response_p95_ms": round(
            sorted(warm)[min(len(warm) - 1, int(len(warm) * 0.95))], 2
        ),
        "loaded_models": loaded,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("Saved", args.output, flush=True)


if __name__ == "__main__":
    main()
