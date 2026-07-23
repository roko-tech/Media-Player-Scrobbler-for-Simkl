"""Canonical identities for local media files and title-only fallbacks."""

import os


def normalize_media_path(path):
    """Return a stable absolute, case-normalized path without touching the file."""
    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    return os.path.normcase(os.path.abspath(os.path.normpath(expanded))).casefold()


def cache_key_for_media(path=None, title=None):
    if path:
        path_text = str(path).strip()
        if path_text.casefold().startswith(("path:", "title:")):
            return path_text.casefold()
        return f"path:{normalize_media_path(path_text)}"
    if title:
        title_text = str(title).strip()
        if title_text.casefold().startswith(("path:", "title:")):
            return title_text.casefold()
        return f"title:{title_text.casefold()}"
    return None


def same_media_path(first, second):
    if not first or not second:
        return False
    return normalize_media_path(first) == normalize_media_path(second)
