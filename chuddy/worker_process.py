"""Cleanup only subprocess trees created by Chuddy's local inference workers."""
import os
import subprocess


def terminate_worker(process):
    if process is None or process.poll() is not None:
        return
    try:
        if process.stdin:
            process.stdin.close()
    except (OSError, ValueError):
        pass
    # Windows venv launchers may wrap a second Python process. Terminating only
    # the launcher can orphan a loaded GPU model, so close the owned tree.
    if os.name == 'nt' and isinstance(process.pid, int) and process.pid > 0:
        try:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, creationflags=0x08000000)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    process.terminate()
