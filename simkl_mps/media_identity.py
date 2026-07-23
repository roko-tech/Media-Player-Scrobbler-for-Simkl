"""Canonical identities for local media files and title-only fallbacks."""

import os


def normalize_media_path(path):
    """Return a stable absolute, case-normalized path without touching the file."""
    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    return os.path.normcase(os.path.abspath(os.path.normpath(expanded)))


def normalize_media_cache_key(key):
    """Normalize cache-key prefixes while preserving POSIX path case."""
    text = str(key).strip()
    lowered = text.casefold()
    if lowered.startswith("path:"):
        return f"path:{os.path.normcase(text[5:])}"
    if lowered.startswith("title:"):
        return f"title:{text[6:].casefold()}"
    return lowered


def cache_key_for_media(path=None, title=None):
    if path:
        path_text = str(path).strip()
        if path_text.casefold().startswith(("path:", "title:")):
            return normalize_media_cache_key(path_text)
        return f"path:{normalize_media_path(path_text)}"
    if title:
        title_text = str(title).strip()
        if title_text.casefold().startswith(("path:", "title:")):
            return normalize_media_cache_key(title_text)
        return f"title:{title_text.casefold()}"
    return None


def same_media_path(first, second):
    if not first or not second:
        return False
    return normalize_media_path(first) == normalize_media_path(second)
