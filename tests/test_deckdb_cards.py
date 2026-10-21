import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import deckdb_cards as dc  # noqa: E402
import deckdb_input as di  # noqa: E402
from test_deckdb_input import _row, _write  # noqa: E402


def _cards_csv(tmp_path: Path, rows) -> Path:
    p = tmp_path / "cards.csv"
    pd.DataFrame(rows, columns=list(dc.CARD_COLUMNS)).to_csv(p, index=False, encoding="utf-8")
    return p


def _card(eid, dtid, zone, oid, name, qty, basic=0, nm="2"):
    return {"event_id": str(eid), "decktournamentid": str(dtid), "zone": zone, "oracle_id": oid,
            "canonical_name": name, "qty": qty, "is_basic": basic, "name_match_version": nm,
            "scryfall_dump_id": "1", "data_version": "dv"}


def _two_months(tmp_path):
    """Deck Zoo: 40 lists x 20 pilots in September and in October. Card X in 10% of September lists
    and 60% of October lists (all variant V); card Y in every list; card Gone only in September; card
    New only in October; Plains (basic) everywhere."""
    rows, cards = [], []
    n = 0
    for month, day, x_share in (("09", "2026-09-10", 0.1), ("10", "2026-10-10", 0.6)):
        for i in range(40):
            n += 1
            rows.append(_row(1000 + n // 8, n, day, "assigned", "Zoo", "Aggro", f"p{i % 20}", (i % 32) + 1,
                             variant="V Zoo"))
            cards.append(_card(1000 + n // 8, n, "main", "y", "Y", 4))
            cards.append(_card(1000 + n // 8, n, "main", "plains", "Plains", 2, basic=1))
            if i < 40 * x_share:
                cards.append(_card(1000 + n // 8, n, "main", "x", "X", 2))
            if month == "09" and i < 5:
                cards.append(_card(1000 + n // 8, n, "sideboard", "gone", "Gone", 1))
            if month == "10" and i < 3:
                cards.append(_card(1000 + n // 8, n, "sideboard", "new", "New", 1))
    exp = di.load_export(_write(tmp_path, rows))
    return exp, dc.load_cards(_cards_csv(tmp_path, cards), exp)


def test_load_cards_checks_keys_versions_and_drops_basics(tmp_path):
    exp, cards = _two_months(tmp_path)
    assert cards.basic_rows == 80 and not (cards.rows["oracle_id"] == "plains").any()
    bad = cards.rows.assign(name_match_version="1", is_basic="0")
    with pytest.raises(di.DeckdbInputError, match="mapowania nazw"):
        dc.load_cards(_cards_csv(tmp_path, bad.to_dict("records")), exp)
    missing = cards.rows[cards.rows["decktournamentid"] != "1"].assign(is_basic="0")
    with pytest.raises(di.DeckdbInputError, match="nie zgadzają się"):
        dc.load_cards(_cards_csv(tmp_path, missing.to_dict("records")), exp)


def test_change_test_family_and_first_appearance_path(tmp_path):
    exp, cards = _two_months(tmp_path)
    t = dc.change_tables(exp, cards, (date(2026, 9, 10), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 10)))
    tested = t["tested"].set_index("Karta")
    assert set(tested.index) == {"X", "Y"}  # Gone / New (0% in one month) are not tested
    x = tested.loc["X"]
    assert x["Zmiana_pp"] == pytest.approx(50.0) and x["Raportowana"] == 1
    assert x["Przedzial_dol_pp"] < 50 < x["Przedzial_gora_pp"]
    assert x["Wewnatrz_wariantow_pp"] + x["Proporcje_wariantow_pp"] == pytest.approx(50.0)
    assert x["Wewnatrz_wariantow_pp"] == pytest.approx(50.0)  # one variant: no mix change
    assert tested.loc["Y", "Zmiana_pp"] == 0 and tested.loc["Y", "Raportowana"] == 0
    paths = dict(zip(t["outside"]["Karta"], t["outside"]["Sciezka"]))
    assert paths["New"].startswith("pierwsze pojawienie") and paths["Gone"].startswith("zniknięcie")
    p2 = t["presence_2"].set_index("Karta")
    assert p2.loc["X", "Obecnosc_pct"] == pytest.approx(60.0) and p2.loc["X", "Sr_kopii"] == 2
    first = dc.first_appearances(exp, cards, date(2026, 10, 1), date(2026, 10, 10))
    assert set(first["Karta"]) == {"New"} and first.iloc[0]["Listy_z_karta_30d"] == 3


def test_bh_and_eligibility_thresholds(tmp_path, monkeypatch):
    reject, q = dc._bh(pd.Series([0.001, 0.02, 0.03, 0.5]).to_numpy(), 0.10)
    assert list(reject) == [True, True, True, False] and q[0] == pytest.approx(0.004)
    exp, cards = _two_months(tmp_path)
    monkeypatch.setattr(dc, "MIN_PILOTS", 21)  # 20 pilots per month -> the deck does not qualify
    t = dc.change_tables(exp, cards, (date(2026, 9, 10), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 10)))
    assert t["tested"].empty and t["eligible"]["Testowana"].tolist() == [0]


def test_pilot_list_changes(tmp_path):
    rows = [
        _row(1, 1, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1),
        _row(2, 2, "2026-10-03", "assigned", "Zoo", "Aggro", "a", 2),
        _row(3, 3, "2026-10-05", "assigned", "Burn", "Aggro", "a", 3),
    ]
    exp = di.load_export(_write(tmp_path, rows))
    cards = dc.load_cards(_cards_csv(tmp_path, [
        _card(1, 1, "main", "x", "X", 4), _card(1, 1, "sideboard", "s", "S", 2),
        _card(2, 2, "main", "x", "X", 3), _card(2, 2, "sideboard", "x", "X", 1), _card(2, 2, "main", "z", "Z", 1),
        _card(3, 3, "main", "b", "B", 4),
    ]), exp)
    t = dc.pilot_list_changes(exp, cards, date(2026, 10, 1), date(2026, 10, 5)).set_index("Talia")
    zoo = t.loc["Zoo"]
    assert zoo["Pary_list"] == 1 and zoo["Zmiany_talii"] == 1
    assert zoo["Main_mediana"] == 2 and zoo["Side_mediana"] == 3  # main: X -1, Z +1; side: S -2, X +1
    assert zoo["Przeniesienia_main_side_srednio"] == 1 and zoo["Odstep_dni_mediana"] == 2
