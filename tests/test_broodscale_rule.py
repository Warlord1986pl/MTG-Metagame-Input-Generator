import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import broodscale_rule as br  # noqa: E402
from challenge_mtgo_source import MtgoEventDeck, MtgoRegistryEvent, _classify_event_decks  # noqa: E402


def _card(name, qty, sideboard=False):
    return {"qty": str(qty), "sideboard": "true" if sideboard else "false", "card_attributes": {"card_name": name}}


def _write_event(cache_dir: Path, event_id: str, decks: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    decklists = [
        {"loginid": lid, "player": f"p{lid}", "main_deck": main, "sideboard_deck": side}
        for lid, (main, side) in decks.items()
    ]
    (cache_dir / f"{event_id}.json").write_text(json.dumps({"event_id": event_id, "decklists": decklists}))


def _setup(tmp_path: Path):
    cache = tmp_path / "cache"
    _write_event(cache, "100", {
        "1": ([_card("Basking Broodscale", 4), _card("Blade of the Bloodchief", 2), _card("Forest", 10)], []),
        "2": ([_card("Eldrazi Temple", 4)], [_card("Basking Broodscale", 2)]),   # sideboard only
        "3": ([_card("Blade of the Bloodchief", 1)], []),                         # control 1
        "4": ([_card("Eldrazi Temple", 4)], []),                                  # control 2
    })
    hist = pd.DataFrame([
        {"EventDate": "2026-10-01", "Format": "Modern", "Tier": "32", "PlayerCount": "", "EventSlug": "s",
         "EventID": "100", "Place": str(i), "Deck": deck, "DeckGuess": "", "Archetype": arch,
         "Pilot": f"p{i}", "LoginID": str(i)}
        for i, (deck, arch) in enumerate(
            [("Eldrazi Ramp", "Ramp"), ("Eldrazi Ramp", "Ramp"), ("Eldrazi Ramp", "Ramp"),
             ("Broodscale Combo", "Combo")], start=1)
    ])
    hist_csv = tmp_path / "challenge_history_modern.csv"
    hist.to_csv(hist_csv, index=False, encoding="utf-8-sig")
    return cache, hist_csv, tmp_path / "ledger.csv"


def test_rule_counts_maindeck_only():
    assert br.rule_deck_for_counts({"basking broodscale": 1}) == ("Broodscale Combo", "Combo")
    assert br.rule_deck_for_counts({"blade of the bloodchief": 4}) is None
    side_only = {"main_deck": [_card("Forest", 1)], "sideboard_deck": [_card("Basking Broodscale", 3)]}
    assert br.rule_deck_for_counts(br.main_counts_from_decklist(side_only)) is None


def test_history_pass_relabels_logs_and_is_idempotent(tmp_path):
    cache, hist_csv, ledger = _setup(tmp_path)
    added = br.apply_broodscale_rule_to_history(hist_csv, cache, ledger, "challenge")
    assert list(added["LoginID"]) == ["1"]
    assert added.iloc[0]["SubmittedDeck"] == "Eldrazi Ramp"
    hist = pd.read_csv(hist_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert list(hist["Deck"]) == ["Broodscale Combo", "Eldrazi Ramp", "Eldrazi Ramp", "Broodscale Combo"]
    assert list(hist["Archetype"]) == ["Combo", "Ramp", "Ramp", "Combo"]
    assert list(hist["Pilot"]) == ["p1", "p2", "p3", "p4"]

    # second pass: nothing new, pre-rule name stays frozen in the ledger
    assert br.apply_broodscale_rule_to_history(hist_csv, cache, ledger, "challenge").empty
    assert pd.read_csv(ledger, dtype=str).iloc[0]["SubmittedDeck"] == "Eldrazi Ramp"

    out = br.write_broodscale_override_log(
        tmp_path / "log.csv", hist_csv, None, cache, ledger, date(2026, 10, 1), date(2026, 10, 1)
    )
    log = pd.read_csv(out, dtype=str, keep_default_na=False)
    ov = log[log["Section"] == "OVERRIDE"]
    assert ov[["Pilot", "SubmittedDeck", "RuleDeck", "BaskingBroodscaleMain", "BladeOfTheBloodchiefMain"]].values.tolist() == [
        ["p1", "Eldrazi Ramp", "Broodscale Combo", "4", "2"]
    ]
    summary = log[log["Section"] == "SUMMARY"].set_index("SubmittedDeck")
    assert summary.loc["Eldrazi Ramp", ["Entries", "Trophies"]].tolist() == ["1", "1"]
    assert log[log["Section"] == "CONTROL_BLADE_WITHOUT_BROODSCALE"]["Pilot"].tolist() == ["p3"]
    assert log[log["Section"] == "CONTROL_RULEDECK_WITHOUT_BROODSCALE"]["Pilot"].tolist() == ["p4"]


def test_rule_overrides_earlier_resolution_in_classifier():
    ev = MtgoRegistryEvent.__new__(MtgoRegistryEvent)
    ev.event_id, ev.date, ev.size, ev.url = "200", "2026-10-01", 32, ""
    decks = [MtgoEventDeck(loginid="9", player="x", place=1, signature={"basking broodscale": 4})]
    rows = _classify_event_decks(
        ev, decks, library=[], archetype_for=lambda d, r: "?", review_items=[],
        resolved={("200", "x"): ("Eldrazi Ramp", "Ramp")},
    )
    assert (rows[0].deck, rows[0].archetype, rows[0].pre_rule_deck) == ("Broodscale Combo", "Combo", "Eldrazi Ramp")


def test_log_splits_scope_and_transitions_reconcile(tmp_path):
    cache, hist_csv, ledger = _setup(tmp_path)
    br.apply_broodscale_rule_to_history(hist_csv, cache, ledger, "challenge")
    _write_event(cache, "300", {"7": ([_card("Basking Broodscale", 4)], [])})
    prem = pd.DataFrame([{
        "EventDate": "2026-10-01", "Format": "Modern", "Tier": "None", "PlayerCount": "", "EventSlug": "rcq",
        "EventID": "300", "Place": "25", "Deck": "Eldrazi Ramp", "DeckGuess": "", "Archetype": "Ramp",
        "Pilot": "p7", "LoginID": "7",
    }])
    prem_csv = tmp_path / "premier_history_modern.csv"
    prem.to_csv(prem_csv, index=False, encoding="utf-8-sig")
    br.apply_broodscale_rule_to_history(prem_csv, cache, ledger, "premier")
    reverse = tmp_path / "reverse.csv"
    br.append_reverse_ledger(reverse, [{
        "Kind": "challenge", "EventID": "100", "EventDate": "2026-10-01", "Tier": "32", "Pilot": "p4",
        "LoginID": "4", "Place": "4", "PreviousDeck": "Broodscale Combo", "NewDeck": "Eldrazi",
        "Method": "mtggoldfish_name", "SubmittedName": "Eldrazi", "Similarity": "", "RecordedOn": "2026-10-12",
    }])
    hist = pd.read_csv(hist_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    hist.loc[hist["Pilot"] == "p4", ["Deck", "Archetype"]] = ["Eldrazi", "Ramp"]
    hist.to_csv(hist_csv, index=False, encoding="utf-8-sig")

    out = br.write_broodscale_override_log(
        tmp_path / "log.csv", hist_csv, prem_csv, cache, ledger, date(2026, 10, 1), date(2026, 10, 1),
        reverse_ledger_csv=reverse,
    )
    log = pd.read_csv(out, dtype=str, keep_default_na=False)
    tr = log[log["Section"] == "TRANSITION"]
    got = {(r.Scope, r.SubmittedDeck, r.RuleDeck): r.Entries for r in tr.itertuples()}
    assert got == {
        ("Challenge", "Broodscale Combo", "Eldrazi"): "1",
        ("Challenge", "Eldrazi Ramp", "Broodscale Combo"): "1",
        ("Challenge", "Eldrazi Ramp", "Eldrazi Ramp"): "2",
        ("Premier", "Eldrazi Ramp", "Broodscale Combo"): "1",
    }
    totals = log[(log["Section"] == "SUMMARY") & (log["SubmittedDeck"] == "TOTAL")]
    assert dict(zip(totals["Scope"], totals["Entries"])) == {"Challenge": "1", "Premier": "1"}
    rev = log[log["Section"] == "REVERSE_REMOVED_NAME_ALIAS"]
    assert rev[["EventID", "Pilot", "RuleDeck"]].values.tolist() == [["100", "p4", "Eldrazi"]]
    assert log[log["Section"] == "CONTROL_RULEDECK_WITHOUT_BROODSCALE"].empty
