"""Offline tests for challenge_mtgo_source.fetch_mtgo_event_json's durable-cache rules.

Regression for 12854120 (C96, 2026-09-16): mtgo.com served decklists before final_rank, that
intermediate blob was cached, and the event stayed ERROR on every later run.

Run directly: python tests/test_event_json_cache.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_mtgo_source  # noqa: E402
from challenge_mtgo_source import fetch_mtgo_event_json  # noqa: E402

DECKLISTS = [{"loginid": "1", "player": "a"}]
FINAL_RANK = [{"loginid": "1", "rank": "1"}]


def _page(blob: dict) -> str:
    return f"<script>window.MTGO.decklists.data = {json.dumps(blob)};</script>"


def _fetch_with(blob: dict, cache_dir: Path) -> tuple:
    calls = []

    def stub(url: str, timeout: int = 45, attempts: int = 5, log=None) -> str:
        calls.append(url)
        return _page(blob)

    original = challenge_mtgo_source._fetch_text_retry
    challenge_mtgo_source._fetch_text_retry = stub
    try:
        data = fetch_mtgo_event_json("123", "https://example/123", cache_dir)
    finally:
        challenge_mtgo_source._fetch_text_retry = original
    return data, calls


def test_blob_without_final_rank_is_not_cached() -> None:
    cache_dir = Path(tempfile.mkdtemp())
    data, calls = _fetch_with({"decklists": DECKLISTS}, cache_dir)
    assert data["decklists"] == DECKLISTS and len(calls) == 1
    assert not (cache_dir / "123.json").exists()


def test_complete_blob_is_cached_and_reused() -> None:
    cache_dir = Path(tempfile.mkdtemp())
    complete = {"decklists": DECKLISTS, "final_rank": FINAL_RANK}
    _fetch_with(complete, cache_dir)
    assert (cache_dir / "123.json").exists()
    data, calls = _fetch_with({"decklists": []}, cache_dir)
    assert calls == [] and data == complete


def test_stale_cached_blob_without_final_rank_is_refetched() -> None:
    cache_dir = Path(tempfile.mkdtemp())
    (cache_dir / "123.json").write_text(json.dumps({"decklists": DECKLISTS}), encoding="utf-8")
    complete = {"decklists": DECKLISTS, "final_rank": FINAL_RANK}
    data, calls = _fetch_with(complete, cache_dir)
    assert len(calls) == 1 and data == complete
    assert json.loads((cache_dir / "123.json").read_text(encoding="utf-8")) == complete


if __name__ == "__main__":
    test_blob_without_final_rank_is_not_cached()
    test_complete_blob_is_cached_and_reused()
    test_stale_cached_blob_without_final_rank_is_refetched()
    print("All event JSON cache tests passed.")
