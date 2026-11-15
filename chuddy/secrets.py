"""Windows DPAPI protection for local service tokens."""

import ctypes
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _transform(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Use environment service tokens on non-Windows systems.")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source),
            "Chuddy service tokens",
            None,
            None,
            None,
            1,
            ctypes.byref(target),
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def protect(data):
    return _transform(data)


def unprotect(data):
    return _transform(data, True)
