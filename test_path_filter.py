"""Regression tests for the allow/deny directory filter (path_filter.py) and its settings
file (config_manager.py): bracket folders, empty-list leak, CWD-relative paths.
Run with pytest or `python3 test_path_filter.py`."""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Load these two stdlib-only modules straight from disk; importing the full package would
# run app startup and touch the real settings.json.
import importlib.util


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, relpath))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


path_filter = _load("path_filter", os.path.join("simkl_mps", "utils", "path_filter.py"))
config_manager = _load("config_manager", os.path.join("simkl_mps", "config_manager.py"))
potplayer = _load("potplayer", os.path.join("simkl_mps", "players", "potplayer.py"))
is_path_allowed = path_filter.is_path_allowed

# Absolute paths valid on the host OS; lowercase to dodge case-insensitivity surprises.
_ROOT = "c:\\" if os.name == "nt" else "/"


def P(*parts):
    """Absolute, host-appropriate path from lowercase components."""
    return os.path.join(_ROOT, *parts)


# --- bracket folders ---

def test_bracket_folder_as_allow_rule_allows_its_files():
    rule = P("downloads", "[subsplease] show")
    f = P("downloads", "[subsplease] show", "ep01.mkv")
    assert is_path_allowed(f, allow_dirs=[rule]) is True


def test_bracket_folder_as_deny_rule_blocks_its_files():
    rule = P("downloads", "[subsplease]")
    f = P("downloads", "[subsplease]", "ep01.mkv")
    assert is_path_allowed(f, allow_dirs=[], deny_dirs=[rule]) is False


def test_bracket_file_allowed_under_wildcard_rule():
    # Brackets on the file side already worked; keep it that way.
    rule = P("downloads") + os.sep + "**"
    f = P("downloads", "[judas] movie (2021)", "movie.mkv")
    assert is_path_allowed(f, allow_dirs=[rule]) is True


def test_wildcard_rule_containing_brackets_matches_nested_files():
    rule = P("downloads", "[judas] movies") + os.sep + "**"
    f = P("downloads", "[judas] movies", "film", "movie.mkv")
    assert is_path_allowed(f, allow_dirs=[rule]) is True


def test_bracket_rule_does_not_spuriously_match_single_char_sibling():
    # Old bug: "[subsplease]" matched any single character at that spot.
    rule = P("downloads", "[subsplease]")
    impostor = P("downloads", "s", "ep01.mkv")
    assert is_path_allowed(impostor, allow_dirs=[rule]) is False


# --- path normalization + scoping ---

def test_relative_path_not_cwd_prefixed():
    # This used to come back as an absolute, CWD-joined path.
    norm = path_filter._normalize_path("bare.mkv", case_sensitive=True)
    assert not os.path.isabs(norm)
    assert "bare.mkv" in norm


def test_bare_filename_denied_under_nonempty_allowlist():
    # Can't tell where a bare filename lives, so a whitelist should reject it.
    assert is_path_allowed("some.movie.mkv", allow_dirs=[P("downloads")]) is False


def test_allowlist_scopes_to_folder_and_blocks_elsewhere():
    allow = [P("downloads") + os.sep + "**", P("downloads")]
    assert is_path_allowed(P("downloads", "show", "ep.mkv"), allow_dirs=allow) is True
    assert is_path_allowed(P("downloads", "ep.mkv"), allow_dirs=allow) is True
    assert is_path_allowed(P("movies", "other.mkv"), allow_dirs=allow) is False
    assert is_path_allowed(P("downloads2", "other.mkv"), allow_dirs=allow) is False


def test_empty_allowlist_allows_everything_by_design():
    # Empty allow-list = no filter configured, so everything passes.
    assert is_path_allowed(P("anywhere", "x.mkv"), allow_dirs=[]) is True
    assert is_path_allowed(P("anywhere", "x.mkv"), allow_dirs=None) is True


# --- settings persistence never drops the allow-list ---

class _TempSettings:
    """Point config_manager at a throwaway settings.json for the duration of a test."""

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="mps-cfg-")
        self._saved = (config_manager.SETTINGS_DIR, config_manager.SETTINGS_FILE,
                       config_manager._last_good_settings)
        from pathlib import Path
        config_manager.SETTINGS_DIR = Path(self._dir)
        config_manager.SETTINGS_FILE = Path(self._dir) / "settings.json"
        config_manager._last_good_settings = None
        return self

    def __exit__(self, *exc):
        (config_manager.SETTINGS_DIR, config_manager.SETTINGS_FILE,
         config_manager._last_good_settings) = self._saved
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)


def test_save_is_atomic_valid_json():
    with _TempSettings():
        config_manager.save_settings({"allow_dirs": [P("downloads")], "deny_dirs": []})
        with open(config_manager.SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)  # raises if not valid JSON
        assert data["allow_dirs"] == [P("downloads")]
        # No leftover temp file.
        assert not (config_manager.SETTINGS_FILE.parent /
                    (config_manager.SETTINGS_FILE.name + ".tmp")).exists()


def test_corrupt_file_falls_back_to_last_good_allowlist():
    with _TempSettings():
        good = {**config_manager.DEFAULT_SETTINGS, "allow_dirs": [P("downloads")]}
        config_manager.save_settings(good)
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]

        # Corrupt it, like a half-finished write would.
        with open(config_manager.SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")

        # Must not come back empty; that would kill the whitelist.
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]


def test_backup_recovers_allowlist_after_restart():
    # Fresh process (no in-memory cache) + corrupt settings.json must still recover
    # the allow-list from the .bak file, not fall back to "allow everything".
    with _TempSettings():
        config_manager.save_settings({**config_manager.DEFAULT_SETTINGS,
                                      "allow_dirs": [P("downloads")]})
        config_manager._last_good_settings = None  # simulate a restart
        with open(config_manager.SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write("{ half-written")
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]


def test_save_retries_transient_replace_failure():
    # A one-off os.replace failure (Windows sharing violation) should be retried, not
    # swallowed, so the save still lands.
    with _TempSettings():
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("simulated WinError 5")
            return real_replace(src, dst)

        config_manager.os.replace = flaky_replace
        try:
            ok = config_manager.save_settings({**config_manager.DEFAULT_SETTINGS,
                                               "allow_dirs": [P("downloads")]})
        finally:
            config_manager.os.replace = real_replace
        assert ok is True
        assert calls["n"] >= 2  # first attempt failed, retry succeeded
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]


def test_load_persists_enrichment_to_disk():
    # A settings file missing default keys gets enriched, and load_settings must write that
    # back. The save has to run AFTER the read handle is closed -- doing it while the file
    # was still open failed on Windows (rename-over-open, WinError 5), so the enrichment
    # never reached disk. This asserts it does.
    with _TempSettings():
        with open(config_manager.SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"watch_completion_threshold": 80}, fh)  # missing allow_dirs/deny_dirs/...
        settings = config_manager.load_settings()
        assert "allow_dirs" in settings and "deny_dirs" in settings
        with open(config_manager.SETTINGS_FILE, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert "allow_dirs" in on_disk  # enrichment actually persisted


def test_non_dict_settings_json_falls_back():
    # settings.json holding valid-but-non-dict JSON must not crash the load.
    with _TempSettings():
        config_manager.save_settings({**config_manager.DEFAULT_SETTINGS, "allow_dirs": [P("downloads")]})
        with open(config_manager.SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]


def test_non_dict_backup_ignored():
    with _TempSettings():
        with open(config_manager._backup_path(), "w", encoding="utf-8") as f:
            f.write('"not a dict"')
        assert config_manager._load_backup() is None


def test_defaults_not_mutated_by_returned_settings():
    with _TempSettings():
        config_manager._last_good_settings = None  # no cache, no backup -> returns defaults
        settings = config_manager._fallback_settings()
        settings["allow_dirs"].append("x")
        assert config_manager.DEFAULT_SETTINGS["allow_dirs"] == []


def test_save_survives_fsync_failure():
    with _TempSettings():
        real_fsync = os.fsync

        def boom(fd):
            raise OSError("fsync unsupported")

        config_manager.os.fsync = boom
        try:
            ok = config_manager.save_settings({**config_manager.DEFAULT_SETTINGS, "allow_dirs": [P("downloads")]})
        finally:
            config_manager.os.fsync = real_fsync
        assert ok is True
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]


def test_save_sanitizes_dir_lists():
    # junk/missing dir lists get cleaned before write + cache, so a later fallback is safe.
    with _TempSettings():
        config_manager.save_settings({"allow_dirs": ["  ", 123, P("downloads")]})
        assert config_manager.get_setting("allow_dirs") == [P("downloads")]
        assert config_manager.get_setting("deny_dirs") == []


# --- PotPlayer full-path resolution (forward slashes so os.path.basename splits on any OS) ---

class _FakeOpenFile:
    def __init__(self, path):
        self.path = path


class _FakeProc:
    def __init__(self, paths):
        self._paths = paths

    def open_files(self):
        return [_FakeOpenFile(p) for p in self._paths]

    def cmdline(self):
        return []


def _potplayer_with_open_files(paths):
    integ = potplayer.PotPlayerIntegration()
    integ._get_process_from_hwnd = lambda hwnd: _FakeProc(paths)
    return integ


def test_potplayer_resolves_group_tagged_file():
    # Raw title (with the [Group] tag the real file has) resolves; the cleaned name doesn't.
    integ = _potplayer_with_open_files(["D:/downloads/[Erai-raws] Baki-dou - 15.mkv"])
    assert integ._resolve_full_path("[Erai-raws] Baki-dou - 15.mkv", 1) == "D:/downloads/[Erai-raws] Baki-dou - 15.mkv"
    assert integ._resolve_full_path("Baki-dou - 15.mkv", 1) is None


def test_potplayer_falls_back_to_cleaned_name():
    # File on disk has no appendage; the cleaned name matches, the raw title doesn't.
    integ = _potplayer_with_open_files(["D:/movies/Show.mkv"])
    assert integ._resolve_full_path("Show (With subtitles).mkv", 1) is None
    assert integ._resolve_full_path("Show.mkv", 1) == "D:/movies/Show.mkv"


def test_potplayer_hidden_extension_prefers_video_not_subtitle():
    # Title hid the extension -> stem match, but must pick the video, not a same-named .srt.
    integ = _potplayer_with_open_files(["D:/dl/Show.srt", "D:/dl/Show.mkv"])
    assert integ._resolve_full_path("Show", 1) == "D:/dl/Show.mkv"


def _run_standalone():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001 - test runner surfaces any failure
            failures += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_standalone() else 0)
