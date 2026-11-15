"""Run the local integration suites against a committed snapshot."""

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run_suite(name, command, snapshot, env, log):
    print(f"Running {name}...", flush=True)
    print(f"\n{name}\n{shlex.join(command)}", file=log, flush=True)
    result = subprocess.run(
        command, cwd=snapshot, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    outcome = f"{name}: {'passed' if result.returncode == 0 else 'failed'}"
    print(outcome, flush=True)
    print(outcome, file=log, flush=True)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", nargs="?", default="HEAD")
    args = parser.parse_args()

    git = shutil.which("git")
    node = shutil.which("node")
    if not git or not node:
        parser.exit(1, "Git and Node must be available on PATH.\n")

    resolved = subprocess.run(
        [git, "rev-parse", "--verify", "--end-of-options", f"{args.revision}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if resolved.returncode:
        parser.exit(1, "The requested revision does not resolve to a local commit.\n")
    commit = resolved.stdout.strip()

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("CHUDDY_REVIEW_DIR", None)
    env["CHUDDY_TEST_PYTHON"] = sys.executable
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    bundled_modules = (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if (bundled_modules / "playwright").is_dir():
        module_paths = [p for p in (env.get("NODE_PATH"), str(bundled_modules)) if p]
        env["NODE_PATH"] = os.pathsep.join(module_paths)

    with tempfile.TemporaryDirectory(prefix="chuddy-integration-") as temp:
        archive = Path(temp) / "source.tar"
        snapshot = Path(temp) / "source"
        subprocess.run([git, "archive", "-o", str(archive), commit], cwd=ROOT, check=True)
        with tarfile.open(archive) as source:
            source.extractall(snapshot, filter="data")

        (snapshot / "test-results").mkdir(exist_ok=True)
        node_tests = sorted(str(path.relative_to(snapshot)) for path in (snapshot / "tests").glob("*.cjs"))
        if not node_tests:
            parser.exit(1, "The requested revision contains no Node test suites.\n")

        reports = ROOT / "test-results"
        reports.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=reports,
            prefix=f"integration-{commit[:7]}-", suffix=".log", delete=False,
        ) as log:
            print(f"Checking {commit}\nLog: {log.name}", flush=True)
            print(f"Commit: {commit}", file=log, flush=True)
            python_ok = run_suite(
                "Python", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                snapshot, env, log,
            )
            node_ok = run_suite("Node and headless Edge", [node, "--test", *node_tests], snapshot, env, log)
            return 0 if python_ok and node_ok else 1


if __name__ == "__main__":
    sys.exit(main())
