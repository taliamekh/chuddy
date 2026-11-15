"""Download the free portable Ollama runtime + local model, without system installation."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from chuddy.ollama_service import DEFAULT_ENDPOINT, PortableOllama

DATA = ROOT / ".chuddy"
BASE = DEFAULT_ENDPOINT


def request(path, payload=None, timeout=5):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data, {"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=timeout)


def ensure_runtime():
    target = DATA / "tools" / "ollama"
    exe = target / "ollama.exe"
    if exe.exists():
        return exe
    if os.name != "nt":
        raise RuntimeError(
            "Install Ollama from https://ollama.com on this platform; this download is Windows-only."
        )
    req = urllib.request.Request(
        "https://api.github.com/repos/ollama/ollama/releases/latest",
        headers={"User-Agent": "Chuddy-local-setup"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        release = json.load(response)
    asset = next(
        a for a in release["assets"] if a["name"] == "ollama-windows-amd64.zip"
    )
    archive = DATA / "downloads" / asset["name"]
    archive.parent.mkdir(parents=True, exist_ok=True)
    print(
        "Portable runtime:",
        release["tag_name"],
        f"({asset['size']/1e9:.2f} GB)",
        flush=True,
    )
    if not archive.exists() or archive.stat().st_size != asset["size"]:
        temp = archive.with_suffix(".partial")
        digest = hashlib.sha256()
        with urllib.request.urlopen(
            asset["browser_download_url"], timeout=60
        ) as response, temp.open("wb") as out:
            count, last = 0, 0
            while chunk := response.read(4 * 1024 * 1024):
                out.write(chunk)
                digest.update(chunk)
                count += len(chunk)
                if count - last > 100 * 1024 * 1024:
                    print(
                        f'Downloaded {count/1e6:.0f}/{asset["size"]/1e6:.0f} MB',
                        flush=True,
                    )
                    last = count
        if temp.stat().st_size != asset["size"]:
            raise RuntimeError("Runtime download size mismatch")
        supplied = asset.get("digest")
        if (
            supplied
            and supplied.startswith("sha256:")
            and digest.hexdigest() != supplied[7:]
        ):
            raise RuntimeError("Runtime SHA256 mismatch")
        temp.replace(archive)
    # Check both downloaded and reused archive against the release digest.
    supplied = asset.get("digest")
    if supplied and supplied.startswith("sha256:"):
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != supplied[7:]:
            raise RuntimeError("Runtime SHA256 mismatch")
    target.mkdir(parents=True, exist_ok=True)
    print("Extracting portable runtime", flush=True)
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            resolved = (target / entry.filename).resolve()
            if not resolved.is_relative_to(target.resolve()):
                raise RuntimeError("Unsafe archive path")
        zipped.extractall(target)
    (target / "release.json").write_text(
        json.dumps(
            {"version": release["tag_name"], "asset": asset["name"], "digest": supplied}
        )
    )
    return exe


def ensure_server(exe, data_dir=DATA):
    portable = PortableOllama(exe, data_dir)
    for _ in range(80):
        try:
            with request("/api/version", timeout=1) as response:
                return json.load(response)
        except OSError:
            if portable.process is not None and portable.process.poll() is not None:
                raise RuntimeError("Ollama exited; inspect .chuddy/logs/ollama.log")
            portable.start_if_missing()
            time.sleep(0.25)
    raise RuntimeError("Local server startup timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.1:8b-instruct-q4_K_M")
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    exe = ensure_runtime()
    print("Local server:", ensure_server(exe), flush=True)
    if args.serve_only:
        return
    with request(
        "/api/pull", {"model": args.model, "stream": True}, timeout=180
    ) as response:
        last, progress = "", 0
        for raw in response:
            item = json.loads(raw)
            if "error" in item:
                raise RuntimeError(item["error"])
            completed = item.get("completed", 0)
            if item["status"] != last or completed - progress > 200_000_000:
                print(
                    item["status"],
                    f'{completed/1e6:.0f}/{item.get("total",0)/1e6:.0f} MB',
                    flush=True,
                )
                last, progress = item["status"], completed
    print(
        "Local model ready; no microphone, speakers or actuators were started.",
        flush=True,
    )


if __name__ == "__main__":
    main()
