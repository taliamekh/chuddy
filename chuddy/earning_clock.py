"""Elapsed awake time. Currency must never accrue during Windows suspend."""
import ctypes
import sys
import time


def awake_monotonic():
    if sys.platform == 'win32':
        value = ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(value)):
            raise OSError('Awake-time clock unavailable')
        return value.value / 10_000_000
    # CLOCK_MONOTONIC excludes suspend on Linux; Windows uses its explicit API.
    return time.monotonic()
