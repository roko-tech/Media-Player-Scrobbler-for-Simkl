import json

from simkl_mps import anime_mapping


def test_existing_anime_map_is_available_on_first_lookup(tmp_path, monkeypatch):
    cache = tmp_path / "anime-list-full.json"
    cache.write_text(
        json.dumps(
            [
                {
                    "type": "TV",
                    "tvdb_id": 291630,
                    "simkl_id": 431548,
                    "season": {"tvdb": 1},
                },
                {
                    "type": "TV",
                    "tvdb_id": 291630,
                    "simkl_id": 529392,
                    "season": {"tvdb": 2},
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(anime_mapping, "_cache_path", lambda: cache)
    monkeypatch.setattr(anime_mapping, "_indices", None)
    monkeypatch.setattr(anime_mapping, "_loading", False)

    assert anime_mapping.resolve_split_season(431548, 2) == 529392


def test_invalid_download_keeps_last_known_good_map(tmp_path, monkeypatch):
    cache = tmp_path / "anime-list-full.json"
    original = '[{"simkl_id": 431548, "tvdb_id": 291630, "season": {"tvdb": 1}}]'
    cache.write_text(original, encoding="utf-8")

    class Response:
        content = b"not-json"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(anime_mapping.requests, "get", lambda *args, **kwargs: Response())

    assert anime_mapping._download(cache) is False
    assert cache.read_text(encoding="utf-8") == original
