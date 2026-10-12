from datetime import date
from pathlib import Path
import re
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from article_skeleton import (  # noqa: E402
    CoverageReport,
    coverage_report,
    fmt_pp,
    iso_weeks_clipped,
    newcombe_diff_ci,
    rank_by_share,
    deck_month_stats,
    render_monthly_skeleton,
    wilson_ci,
)


def test_wilson_matches_reference():
    # Newcombe (1998) Table I: 81/263 -> 0.2553..0.3662
    lo, hi = wilson_ci(81, 263)
    assert lo == pytest.approx(0.2553, abs=1e-4)
    assert hi == pytest.approx(0.3662, abs=1e-4)
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_newcombe_matches_reference():
    # Newcombe (1998) Table II, method 10: 56/70 - 48/80 -> 0.0524..0.3339
    d, lo, hi = newcombe_diff_ci(56, 70, 48, 80)
    assert d == pytest.approx(0.2)
    assert lo == pytest.approx(0.0524, abs=1e-4)
    assert hi == pytest.approx(0.3339, abs=1e-4)


def test_fmt_pp_signed_and_no_negative_zero():
    assert fmt_pp(0.027) == "+2,7 pp"
    assert fmt_pp(-0.0001) == "+0,0 pp"


def test_iso_weeks_clipped_to_month():
    weeks = iso_weeks_clipped(date(2026, 9, 1), date(2026, 9, 30))
    assert weeks[0] == (date(2026, 9, 1), date(2026, 9, 6))
    assert weeks[-1] == (date(2026, 9, 28), date(2026, 9, 30))
    assert sum((b - a).days + 1 for a, b in weeks) == 30


def _rows(month: str, decks_per_event: list[list[str]], start_id: int) -> list[dict]:
    out = []
    for i, decks in enumerate(decks_per_event):
        day = date.fromisoformat(f"{month}-{(i % 28) + 1:02d}")
        for place, deck in enumerate(decks, 1):
            out.append({
                "Month": month, "EventDate": day.isoformat(), "Date": day, "EventID": str(start_id + i),
                "Place": str(place), "PlaceN": place, "Deck": deck, "Tier": "32", "PlayerCount": "40",
                "Pilot": f"p{place}", "Identity": f"id:{place}",
            })
    return out


def _fixture():
    decks = [f"D{i:02d}" for i in range(12)]
    cur = _rows("2026-09", [decks + decks[:4]] * 3, 100)
    prev = _rows("2026-08", [list(reversed(decks)) + decks[:4]] * 3, 200)
    return pd.DataFrame(cur + prev)


def test_ranking_is_by_share_with_documented_tiebreak():
    df = _fixture()
    stats = deck_month_stats(df[df["Month"] == "2026-09"])
    order = rank_by_share(stats)
    assert order[:4] == ["D00", "D01", "D02", "D03"]  # 2 entries each per event
    # remaining decks tie on entries; Top 8 count, then name decides
    assert order[4:8] == ["D04", "D05", "D06", "D07"]


def test_render_structure_has_no_prose_outside_blockquotes():
    df = _fixture()
    cov = CoverageReport("2026-09", 3, 3, events_per_day={})
    pcov = CoverageReport("2026-08", 3, 3, events_per_day={})
    md = render_monthly_skeleton(df, "2026-09", "2026-08", cov, pcov, [], [], validated_on=date(2026, 10, 9))
    lines = [l for l in md.splitlines() if l.strip()]
    for l in lines:
        assert l.startswith("#") or l.startswith(">"), l
    assert len(re.findall(r"^### \d+\. ", md, flags=re.M)) == 10
    assert "### Wypadły z pierwszej dziesiątki" in md
    assert "### Metodologia" in md
    assert md.index("Wypadły") < md.index("Metodologia")


def test_coverage_flags_missing_and_ignores_empty_days():
    df = _fixture()
    rep = coverage_report(df, "2026-09", {"100", "101", "102", "999"})
    assert not rep.ok and rep.missing_from_data == ["999"]
    assert len(rep.empty_days) == 27  # empty days reported, not a failure on their own
    assert coverage_report(df, "2026-09", {"100", "101", "102"}).ok
