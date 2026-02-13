"""
Utility functions for directory allow/deny filtering.
"""

from __future__ import annotations

import os
import platform
import fnmatch
from pathlib import Path, PurePath
from typing import Iterable, List, Optional, Tuple


def _is_case_sensitive_filesystem() -> bool:
    """Return whether path comparisons should be case-sensitive."""
    system = platform.system().lower()
    # Windows and macOS default to case-insensitive file systems
    return system not in {"windows", "darwin"}


def _normalize_path(path_value: str, case_sensitive: bool) -> str:
    """Normalize a path for reliable comparisons."""
    try:
        resolved = Path(path_value).expanduser().resolve(strict=False)
    except Exception:
        resolved = Path(path_value).expanduser().absolute()
    normalized = os.path.normpath(str(resolved))
    return normalized if case_sensitive else normalized.lower()


def _normalize_pattern(pattern_value: str, case_sensitive: bool) -> str:
    """Normalize a pattern for glob/path matching while preserving wildcards."""
    expanded = os.path.expanduser(pattern_value)
    normalized = os.path.normpath(expanded)
    return normalized if case_sensitive else normalized.lower()


def _split_parts(path_value: str) -> Tuple[str, ...]:
    """Split a path into parts for prefix matching."""
    return PurePath(path_value).parts


def _is_path_within_directory(file_path: str, directory_path: str) -> bool:
    """Return True if file_path is within directory_path (or equal)."""
    if not file_path or not directory_path:
        return False

    file_parts = _split_parts(file_path)
    dir_parts = _split_parts(directory_path)
    if len(file_parts) < len(dir_parts):
        return False
    return file_parts[: len(dir_parts)] == dir_parts


def _coerce_string_list(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    return [value for value in values if isinstance(value, str) and value.strip()]


def _contains_glob(pattern_value: str) -> bool:
    return any(char in pattern_value for char in ("*", "?", "[", "]"))


def _normalize_for_match(path_value: str) -> str:
    """Normalize path separators for consistent glob matching."""
    normalized = path_value
    if os.altsep:
        normalized = normalized.replace(os.altsep, os.sep)
    return normalized


def _match_rule(file_path: str, rule: str, case_sensitive: bool) -> bool:
    """Return True if file_path matches rule (directory or glob)."""
    if not file_path or not rule:
        return False

    normalized_file = _normalize_for_match(file_path)
    normalized_rule = _normalize_for_match(rule)

    if _contains_glob(normalized_rule) or not os.path.isabs(normalized_rule):
        # Treat relative patterns as match-anywhere
        if not os.path.isabs(normalized_rule) and not normalized_rule.startswith("**"):
            normalized_rule = os.path.join("**", normalized_rule)
        return fnmatch.fnmatchcase(normalized_file, normalized_rule)

    # Non-glob absolute rule: directory prefix match
    return _is_path_within_directory(normalized_file, normalized_rule)


def is_path_allowed(
    file_path: Optional[str],
    allow_dirs: Optional[Iterable[str]] = None,
    deny_dirs: Optional[Iterable[str]] = None,
) -> bool:
    """
    Determine if a file path is allowed based on allow/deny directory rules.

    Rules:
    - If allow_dirs is empty, default is allow.
    - If allow_dirs has entries, default is deny.
    - Rules are evaluated in order: allow_dirs, then deny_dirs.
    - The last matching rule wins.
    """
    if not file_path:
        return True

    allow_dirs_list = _coerce_string_list(allow_dirs)
    deny_dirs_list = _coerce_string_list(deny_dirs)

    case_sensitive = _is_case_sensitive_filesystem()
    normalized_file = _normalize_path(file_path, case_sensitive)

    # A path is allowed if it's not in the deny list, AND
    # (the allow list is empty OR it's in the allow list).
    is_denied = any(
        _match_rule(normalized_file, _normalize_pattern(d, case_sensitive), case_sensitive)
        for d in deny_dirs_list
    )
    if is_denied:
        return False

    is_allowed = (not allow_dirs_list) or any(
        _match_rule(normalized_file, _normalize_pattern(d, case_sensitive), case_sensitive)
        for d in allow_dirs_list
    )
    return is_allowed