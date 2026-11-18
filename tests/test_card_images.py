"""Card images of the week's new cards (weekly_runner/card_images.py): Scryfall lookup, double-faced cards, cache,
PNG header check, no fallback. Offline with a fake HTTP client; the W46 control hits Scryfall (marker live)."""
import struct
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from weekly_runner import card_images as ci  # noqa: E402


def _png(w=745, h=1040) -> bytes:
    return ci.PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00rest"


class _Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content

    def json(self):
        return self._data


class _Fake:
    """Scryfall stand-in: cards {name: json}, images {url: bytes}; *fail* = urls answering 500 n times."""

    def __init__(self, cards, images, fail=None):
        self.cards, self.images, self.fail, self.calls = cards, images, dict(fail or {}), []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, (params or {}).get("exact")))
        assert headers == ci.HEADERS
        key = url if params is None else params["exact"]
        if self.fail.get(key, 0) > 0:
            self.fail[key] -= 1
            return _Resp(500)
        if params is not None:
            return _Resp(200, self.cards[key]) if key in self.cards else _Resp(404)
        return _Resp(200, content=self.images[url])


def _card(name, sid, faces=False, size=(745, 1040)):
    url = f"https://img/{sid}.png"
    data = {"id": sid, "name": name, "set": "tst", "artist": "A"}
    if faces:
        data["card_faces"] = [{"image_uris": {"png": url}, "artist": "A"}, {"image_uris": {"png": "x"}}]
    else:
        data["image_uris"] = {"png": url}
    return data, url, _png(*size)


SID1, SID2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _setup(fail=None):
    d1, u1, b1 = _card("Genesis Device", SID1, size=(744, 1040))
    d2, u2, b2 = _card("Fire // Ice", SID2, faces=True)
    return _Fake({"Genesis Device": d1, "Fire // Ice": d2}, {u1: b1, u2: b2}, fail)


def test_images_saved_byte_for_byte_double_faced_and_cache(tmp_path):
    fake, sleeps = _setup(), []
    r = ci.fetch_card_images(["Fire // Ice", "Genesis Device"], tmp_path / "c1", tmp_path / "cache", get=fake,
                             sleep=sleeps.append, clock=lambda: 0.0)
    assert sorted(p.name for p in (tmp_path / "c1").iterdir()) == ["Fire __ Ice.png", "Genesis Device.png"]
    assert (tmp_path / "c1" / "Fire __ Ice.png").read_bytes() == fake.images[f"https://img/{SID2}.png"]
    assert (r["downloaded"], r["cached"]) == (2, 0) and len(fake.calls) == 4
    # >= 100 ms between requests: 500 ms before a card lookup, 100 ms before an image
    assert sorted(set(round(s, 3) for s in sleeps)) == [ci.MIN_INTERVAL, ci.API_INTERVAL] and len(sleeps) == 3
    fake.calls.clear()
    r = ci.fetch_card_images(["Fire // Ice", "Genesis Device"], tmp_path / "c2", tmp_path / "cache", get=fake,
                             sleep=lambda s: None)
    assert (r["downloaded"], r["cached"]) == (0, 2)
    assert all(url == ci.API for url, _n in fake.calls)  # only the card lookups, no image download


def test_missing_card_http_error_and_bad_size_stop(tmp_path):
    with pytest.raises(ci.CardImageError, match="'Nope'.*brak w Scryfallu"):
        ci.fetch_card_images(["Nope"], tmp_path / "a", tmp_path / "cache", get=_setup(), sleep=lambda s: None)
    # one 500 then 200: passes (2 attempts); two 500s: stop with the card name
    ci.fetch_card_images(["Genesis Device"], tmp_path / "b", tmp_path / "cache", get=_setup({"Genesis Device": 1}),
                         sleep=lambda s: None)
    with pytest.raises(ci.CardImageError, match="'Genesis Device'.*2 próbach.*HTTP 500"):
        ci.fetch_card_images(["Genesis Device"], tmp_path / "c", tmp_path / "cache",
                             get=_setup({"Genesis Device": 2}), sleep=lambda s: None)
    d, u, b = _card("Big", "00000000-0000-0000-0000-000000000003", size=(488, 680))
    with pytest.raises(ci.CardImageError, match="'Big'.*488x680"):
        ci.fetch_card_images(["Big"], tmp_path / "d", tmp_path / "cache", get=_Fake({"Big": d}, {u: b}),
                             sleep=lambda s: None)
    assert not (tmp_path / "cache" / "00000000-0000-0000-0000-000000000003.png").exists()  # never cached
    with pytest.raises(ci.CardImageError, match="liczba|plików"):
        (tmp_path / "e").mkdir()
        (tmp_path / "e" / "Extra.png").write_bytes(_png())
        ci.check_cards_dir(tmp_path / "e", [])


def test_card_names_and_notes_line(tmp_path):
    x = tmp_path / "data.xlsx"
    with pd.ExcelWriter(x) as xw:
        pd.DataFrame({"Deck": ["A", "B", "C"], "Card": ["Zap", "Zap", "Bolt"]}).to_excel(xw, sheet_name="new_cards", index=False)
    assert ci.card_names(x) == ["Bolt", "Zap"]
    notes = tmp_path / "notes.md"
    notes.write_text("# P\n\n## Files\n\n- `a.png` - a.\n\nSheets of `data.xlsx` (after `contents`): `x`.\n\n"
                     "## Speaker notes\n", encoding="utf-8")
    ci.add_notes_line(notes)
    ci.add_notes_line(notes)  # once only
    text = notes.read_text(encoding="utf-8")
    assert text.count(ci.NOTES_LINE) == 1
    assert "- `a.png` - a.\n" + ci.NOTES_LINE + "\n\nSheets of" in text


@pytest.mark.live
def test_w46_genesis_device(tmp_path):
    """W46 control: cards/ = the 42 distinct cards of new_cards, Genesis Device.png 744x1040, set trk, Cliff Nielsen."""
    xlsx = REPO / "weekly" / "2026-W46" / "data.xlsx"
    if not xlsx.exists():
        pytest.skip("weekly/2026-W46/data.xlsx not on this machine")
    names = ci.card_names(xlsx)
    r = ci.fetch_card_images(names, tmp_path / "cards")
    assert len(list((tmp_path / "cards").iterdir())) == len(names) == 42
    g = next(c for c in r["cards"] if c["card"] == "Genesis Device")
    assert (g["file"], g["width"], g["height"], g["set"], g["artist"]) == ("Genesis Device.png", 744, 1040, "trk",
                                                                          "Cliff Nielsen")
    assert ci.png_size(tmp_path / "cards" / "Genesis Device.png") == (744, 1040)
