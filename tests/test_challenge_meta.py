import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_meta as cm  # noqa: E402
import statistics_engine as se  # noqa: E402


def _history(tmp_path: Path, decks) -> Path:
    rows = [
        {"EventDate": "2026-10-01", "Format": "Modern", "Tier": "32", "PlayerCount": "", "EventSlug": "s",
         "EventID": "1", "Place": str(i + 1), "Deck": deck, "DeckGuess": "", "Archetype": arch,
         "Pilot": f"p{i}", "LoginID": str(i)}
        for i, (deck, arch) in enumerate(decks)
    ]
    path = tmp_path / "challenge_history_modern.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _videre(run_dir: Path) -> None:
    run_dir.mkdir()
    pd.DataFrame([
        {"Deck": "Broodscale Combo", "Source Deck Names": "Broodscale Combo", "Meta": 8.0, "Winrate": 0.53, "Winrate Game Count": 600, "Archetype": "Combo"},
        {"Deck": "Eldrazi Ramp", "Source Deck Names": "Eldrazi Ramp", "Meta": 7.0, "Winrate": 0.55, "Winrate Game Count": 500, "Archetype": "Ramp"},
        {"Deck": "Affinity", "Source Deck Names": "Affinity", "Meta": 5.0, "Winrate": 0.57, "Winrate Game Count": 400, "Archetype": "Aggro"},
    ]).to_csv(run_dir / "metagame_input.csv", index=False)


def _ledger(tmp_path: Path, submitted: str) -> Path:
    path = tmp_path / "ledger.csv"
    pd.DataFrame([{
        "Kind": "challenge", "EventID": "1", "EventDate": "2026-10-01", "Tier": "32", "Pilot": "p0",
        "LoginID": "0", "Place": "1", "SubmittedDeck": submitted, "RuleDeck": "Broodscale Combo",
        "BaskingBroodscaleMain": "4", "BladeOfTheBloodchiefMain": "2", "RecordedOn": "2026-10-12",
    }]).to_csv(path, index=False, encoding="utf-8-sig")
    return path


DECKS = [("Broodscale Combo", "Combo")] * 2 + [("Affinity", "Aggro"), ("4C Cosmogoyf", "Combo")]


@pytest.mark.parametrize("submitted, mixed", [("Eldrazi Ramp", True), ("Broodscale Combo", False)])
def test_meta_is_top32_share_and_winrate_flags(tmp_path, submitted, mixed):
    run_dir = tmp_path / "run"
    _videre(run_dir)
    xlsx = cm.build_challenge_meta_input(
        run_dir, _history(tmp_path, DECKS), _ledger(tmp_path, submitted),
        date(2026, 10, 1), date(2026, 10, 1), "Modern", 0.5,
    )
    out = pd.read_excel(xlsx).set_index("Deck")
    assert out["Meta"].to_dict() == {"Broodscale Combo": 50.0, "Affinity": 25.0, "4C Cosmogoyf": 25.0}
    assert set(out["MetaSource"]) == {cm.META_SOURCE_CHALLENGE}
    assert out.loc["Broodscale Combo", "Meta_Videre"] == 8.0 and np.isnan(out.loc["4C Cosmogoyf", "Meta_Videre"])
    assert out.loc["Affinity", "Winrate"] == 0.57
    assert out.loc["4C Cosmogoyf", "WR_Flag"] == cm.WR_FLAG_NO_VIDERE
    if mixed:
        assert np.isnan(out.loc["Broodscale Combo", "Winrate"]) and out.loc["Broodscale Combo", "WR_Flag"] == cm.WR_FLAG_MIXED
    else:
        assert out.loc["Broodscale Combo", "Winrate"] == 0.53
    assert (run_dir / cm.CHALLENGE_META_CSV).exists()


def test_unresolved_rows_fail_closed(tmp_path):
    with pytest.raises(cm.ChallengeMetaError):
        cm.challenge_top32_share(
            _history(tmp_path, [("NEEDS_MANUAL_REVIEW", "NEEDS_MANUAL_REVIEW"), ("Affinity", "Aggro")]),
            date(2026, 10, 1), date(2026, 10, 1), "Modern",
        )


def test_nan_winrate_never_becomes_a_label_or_a_fake_50(tmp_path):
    df = pd.DataFrame({
        "Deck": ["A", "B", "C", "D", "E"], "Deck Display Name": ["A", "B", "C", "D", "E"],
        "Meta": [20.0, 10.0, 5.0, 3.0, 1.0], "Winrate": [np.nan, 0.55, 0.50, 0.45, 0.60],
        "Winrate Game Count": [np.nan, 300, 200, 100, 60], "Archetype": ["Combo", "Combo", "Aggro", "Aggro", "Ramp"],
        "WR_Flag": [cm.WR_FLAG_MIXED, "", "", "", ""],
    })
    m = se._calculate_metrics(df, 1000, 5).set_index("Deck")
    # no winrate -> Importance from Meta alone (weight rescaled to 1), ranked normally
    assert m.loc["A", "Importance"] == pytest.approx(1.0) and m.loc["A", "Importance_Flag"] == "meta_only"
    assert (m.loc["A", "Quartile"], m.loc["A", "Prep Priority"]) == ("Q4", "Very High Prep Priority")
    assert m.loc["A", "Performance Label"] == ""
    assert (m.loc[["B", "C", "D", "E"], "Importance_Flag"] == "").all()
    assert m.loc["A", "Encounter Copies"] == 200
    assert m.loc[["B", "C", "D", "E"], "Quartile"].ne("").all()

    arch = se._aggregate_by_archetype(df).set_index("Archetype")
    assert arch.loc["Combo", "Winrate"] == pytest.approx(0.55)  # A's NaN excluded, not counted as 0
    assert arch.loc["Combo", "WR_Flag"].endswith("A")

    xlsx = se._build_record_probability_excel(df, 5, "t", tmp_path)
    rec = pd.read_excel(xlsx).set_index("Deck")
    assert pd.isna(rec.loc["A", "Deck Winrate"]) and pd.isna(rec.loc["A", "5-0"])
    assert rec.loc["A", "WR_Flag"] == cm.WR_FLAG_MIXED
