"""Small Windows DPAPI wrapper for secrets stored in app-data files."""

import base64
import binascii
import ctypes
import os
from ctypes import wintypes


PREFIX = "dpapi:"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretProtectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


def _protect_bytes(data):
    source, source_buffer = _blob(data)
    target = _DataBlob()
    _ = source_buffer
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target),
    ):
        raise SecretProtectionError(str(ctypes.WinError()))
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect_bytes(data):
    source, source_buffer = _blob(data)
    target = _DataBlob()
    _ = source_buffer
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target),
    ):
        raise SecretProtectionError(str(ctypes.WinError()))
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def protect_secret(value):
    if not value or is_protected(value) or os.name != "nt":
        return value
    protected = _protect_bytes(str(value).encode("utf-8"))
    return PREFIX + base64.b64encode(protected).decode("ascii")


def unprotect_secret(value):
    if not is_protected(value):
        return value
    if os.name != "nt":
        raise SecretProtectionError("DPAPI secrets can only be opened on Windows.")
    try:
        protected = base64.b64decode(value[len(PREFIX) :], validate=True)
        return _unprotect_bytes(protected).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise SecretProtectionError("Invalid DPAPI secret data.") from exc


def ensure_private_file(path):
    """Restrict a secret-bearing file to its owner on POSIX systems."""
    if os.name != "nt":
        os.chmod(path, 0o600)


def open_private_text_file(path, encoding="utf-8"):
    """Open a file for replacement while enforcing a POSIX owner-only mode."""
    if os.name == "nt":
        return open(path, "w", encoding=encoding)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(path, 0o600)
        return os.fdopen(descriptor, "w", encoding=encoding)
    except Exception:
        os.close(descriptor)
        raise
