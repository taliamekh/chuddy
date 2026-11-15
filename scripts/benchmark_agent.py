"""Exercise the actual local agent/controller with no playback, capture or watcher."""

import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chuddy.store import Store
from chuddy.controller import Controller


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="chuddy-agent-benchmark-") as folder:
        data = Path(folder)
        store = Store(data / "state.sqlite3")
        controller = Controller(store, data)
        store.add_deadline("Fictional lab report", "2026-11-05T23:59:00-05:00")
        for text in [
            "Hello. Who are you?",
            "What is 2 plus 2?",
            "Spell report.",
            "Remember that my favorite snack is popcorn.",
            "What is my favorite snack?",
            "My report is due at midnight. Should I start?",
            "Open the clam game for me.",
        ]:
            if text.startswith("Open"):
                # There is no desktop watcher or native cover callback in this test.
                controller.set_session("start")
            turn = controller.agent.begin(text)["turn_id"]
            deadline = time.monotonic() + 90
            while (
                controller.agent.info()["status"] == "thinking"
                and time.monotonic() < deadline
            ):
                time.sleep(0.025)
            state = controller.agent.info()
            if state["status"] == "thinking":
                controller.agent.cancel()
                raise TimeoutError("Local agent benchmark timed out")
            answer = store.messages()[-1]
            row = {
                "input": text,
                "answer": answer["text"],
                "source": answer["source"],
                "trace": state["trace"],
                "metrics": state["metrics"],
                "memory": store.memories(),
                "interruption": controller.interruption,
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
        controller.close()
        store.close()
    target = ROOT / ".chuddy/benchmarks/agent.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "results": results,
                "playback": False,
                "microphone": False,
                "desktop_watcher": False,
                "physical_outputs": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Saved", target, flush=True)


if __name__ == "__main__":
    main()
