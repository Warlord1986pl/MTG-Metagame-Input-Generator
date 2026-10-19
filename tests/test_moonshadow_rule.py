import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import moonshadow_rule as mr  # noqa: E402
from challenge_mtgo_source import parse_mtgo_event  # noqa: E402


def _card(name, qty, colors=("COLOR_BLACK",)):
    return {"qty": str(qty), "card_attributes": {"card_name": name, "colors": list(colors)}}


def _deck(loginid, main, side=()):
    return {"loginid": loginid, "player": f"p{loginid}", "main_deck": list(main), "sideboard_deck": list(side)}


PAIR = [_card("Moonshadow", 4), _card("Dark Matter Manipulator", 4), _card("Blood Crypt", 4, ("COLOR_COLORLESS",))]


def test_rule_needs_both_cards_no_deaths_shadow_no_blue_and_broodscale_first():
    assert mr.moonshadow_rule_deck({"moonshadow": 1, "dark matter manipulator": 1}, False) == ("Rakdos Midrange", "Midrange")
    assert mr.moonshadow_rule_deck({"dark matter manipulator": 4}, False) is None           # DragonFodder case
    assert mr.moonshadow_rule_deck({"moonshadow": 4}, False) is None
    both = {"moonshadow": 4, "dark matter manipulator": 4}
    assert mr.moonshadow_rule_deck({**both, "death's shadow": 1}, False) is None
    assert mr.moonshadow_rule_deck(both, True) is None                                       # blue card in main
    assert mr.moonshadow_rule_deck({**both, "basking broodscale": 1}, False) is None
    assert mr.card_rule_deck({**both, "basking broodscale": 1}, False) == ("Broodscale Combo", "Combo")
    assert mr.exclusion_reason({**both, "death's shadow": 1}, True) == "deaths_shadow"


def test_blue_detection_uses_maindeck_colors_only():
    assert mr.blue_main_from_decklist(_deck("1", PAIR + [_card("Seasoned Cryomancer", 4, ("COLOR_BLUE",))]))
    assert not mr.blue_main_from_decklist(_deck("1", PAIR, side=[_card("Counterspell", 2, ("COLOR_BLUE",))]))
    # mtgo.com reports lands as COLORLESS: a blue dual alone is not detected (documented limitation)
    assert not mr.blue_main_from_decklist(_deck("1", PAIR + [_card("Watery Grave", 2, ("COLOR_COLORLESS",))]))


def test_history_pass_and_override_log(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    decks = [
        _deck("1", PAIR),                                                              # Rakdos Aggro -> rule
        _deck("2", PAIR + [_card("Death's Shadow", 4)]),                               # excluded
        _deck("3", PAIR + [_card("Seasoned Cryomancer", 4, ("COLOR_BLUE",))]),         # excluded (blue)
        _deck("4", [_card("Dark Matter Manipulator", 4)]),                             # control, untouched
    ]
    (cache / "100.json").write_text(json.dumps({"event_id": "100", "decklists": decks}))
    hist = pd.DataFrame([
        {"EventDate": "2026-10-10", "Format": "Modern", "Tier": "32", "PlayerCount": "", "EventSlug": "s",
         "EventID": "100", "Place": str(i), "Deck": d, "DeckGuess": "", "Archetype": "Aggro",
         "Pilot": f"p{i}", "LoginID": str(i)}
        for i, d in enumerate(["Rakdos Aggro", "Rakdos Death's Shadow", "Dimir Midrange", "Rakdos Aggro"], start=1)
    ])
    hist_csv = tmp_path / "challenge_history_modern.csv"
    hist.to_csv(hist_csv, index=False, encoding="utf-8-sig")
    ledger = tmp_path / "rakdos_ledger.csv"

    added = mr.apply_moonshadow_rule_to_history(hist_csv, cache, ledger, "challenge")
    assert list(added["LoginID"]) == ["1"] and added.iloc[0]["SubmittedDeck"] == "Rakdos Aggro"
    after = pd.read_csv(hist_csv, dtype=str, keep_default_na=False)
    assert list(after["Deck"]) == ["Rakdos Midrange", "Rakdos Death's Shadow", "Dimir Midrange", "Rakdos Aggro"]
    assert list(after["Archetype"])[0] == "Midrange"
    assert mr.apply_moonshadow_rule_to_history(hist_csv, cache, ledger, "challenge").empty   # idempotent

    out = mr.write_rakdos_override_log(tmp_path / "log.csv", hist_csv, None, cache, ledger,
                                       date(2026, 10, 5), date(2026, 10, 18))
    log = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert list(log[log.Section == "OVERRIDE"].SubmittedDeck) == ["Rakdos Aggro"]
    total = log[(log.Section == "SUMMARY") & (log.SubmittedDeck == "TOTAL")].iloc[0]
    assert (total.Entries, total.Trophies) == ("1", "1")
    assert sorted(log[log.Section == "EXCLUDED"].ExcludedBy) == ["blue_card", "deaths_shadow"]
    assert list(log[log.Section == "CONTROL_DMM_WITHOUT_MOONSHADOW"].Pilot) == ["p4"]
    assert mr.rule_moved_entries(ledger, date(2026, 10, 5), date(2026, 10, 18)) == 1


def test_parse_mtgo_event_sets_blue_main():
    data = {"event_id": "1", "final_rank": [{"loginid": "1", "rank": "1"}, {"loginid": "2", "rank": "2"}],
            "decklists": [_deck("1", PAIR), _deck("2", PAIR + [_card("Thought Scour", 2, ("COLOR_BLUE",))])]}
    by_login = {d.loginid: d for d in parse_mtgo_event(data)}
    assert not by_login["1"].blue_main and by_login["2"].blue_main
