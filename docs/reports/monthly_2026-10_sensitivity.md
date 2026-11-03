# Monthly package 2026-10: sensitivity of the month-on-month change test

Order of 2026-11-03 c. October 2026 vs September 2026, the tracked decks and archetypes of the package `monthly/2026-10/` (monthly_metrics). Export `assignments_9534f600135ceece_2026-11-01.csv` (rules 9534f600135ceece, sha256 1ea02e609f1aaba75d54adae79f75d162fc3b66b04ae1f26a8f92adc7fed611c). The package, the production code and the charts are not changed; script `scripts/monthly_sensitivity.py`.

## Methods

- **Event bootstrap** (the package): the events of each month drawn with replacement, independently, 2000 draws, seed 20261026; values read from monthly_metrics.
- **Pilot bootstrap** (this report): the pilots of each month drawn with replacement, each with all their lists of the month (every deck), independently per month, the current month first, the same draws and seed; share = lists of the deck among the drawn lists.
- Both: 95% percentile interval, two-sided p = 2 x min(P(d <= 0), P(d >= 0)) (at least 1/2000), Benjamini-Hochberg q = 0.1 separately for the tracked decks and for the archetypes.

Repeat play: October 1646 lists from 640 pilots (mean 2.57, max 23 lists per pilot, 93 pilots with >= 5 lists); September 1884 lists from 748 pilots (mean 2.52, max 19). 53 and 59 events.

## Table

Top pilot share = lists of the deck's (archetype's) most frequent pilot / its lists, in the month.

| level | deck / archetype | tracked reason | change pp | event CI | event q | event beyond | pilot CI | pilot q | pilot beyond | top pilot Oct | top pilot Sep | pilots Oct / Sep |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deck | Moonshadow | top10_month | +10.3 | +8.6 .. +12.2 | 0.001 | yes | +7.3 .. +13.9 | 0.002 | yes | 10% | 20% | 80 / 5 |
| deck | Prison | top10_month | +3.1 | +1.8 .. +4.5 | 0.001 | yes | +0.6 .. +6.0 | 0.037 | yes | 14% | 20% | 42 / 20 |
| deck | Amulet Titan | top10_month | +2.5 | +1.4 .. +3.6 | 0.002 | yes | +0.1 .. +4.9 | 0.061 | yes | 15% | 48% | 28 / 13 |
| deck | Zoo | top10_month | +2.1 | +0.8 .. +3.3 | 0.001 | yes | +0.3 .. +4.1 | 0.060 | yes | 6% | 10% | 46 / 25 |
| deck | Affinity | top10_month | +2.0 | +0.3 .. +3.6 | 0.028 | yes | −1.6 .. +5.8 | 0.402 | no | 13% | 9% | 37 / 42 |
| deck | Tron | top10_month | +0.6 | −0.9 .. +2.0 | 0.467 | no | −1.7 .. +2.8 | 0.696 | no | 11% | 11% | 34 / 49 |
| deck | Energy | top10_month | +0.5 | −0.9 .. +1.9 | 0.467 | no | −1.4 .. +2.6 | 0.691 | no | 7% | 6% | 55 / 61 |
| deck | Ruby Storm | top10_month | −0.4 | −1.8 .. +1.0 | 0.569 | no | −4.2 .. +3.8 | 0.762 | no | 34% | 19% | 31 / 35 |
| deck | Devoted Combo | top10_prev_month | −1.7 | −3.2 .. −0.3 | 0.028 | yes | −4.1 .. +0.5 | 0.204 | no | 12% | 7% | 34 / 49 |
| deck | Broodscale Combo | top10_month | −2.4 | −5.0 .. +0.1 | 0.071 | yes | −7.1 .. +2.2 | 0.415 | no | 9% | 5% | 92 / 132 |
| deck | Land Destruction | top10_prev_month | −3.7 | −4.7 .. −2.7 | 0.001 | yes | −5.5 .. −2.1 | 0.002 | yes | 14% | 12% | 7 / 41 |
| deck | Goryo's Vengeance | top10_prev_month | −3.7 | −5.0 .. −2.5 | 0.001 | yes | −5.7 .. −1.8 | 0.002 | yes | 12% | 7% | 21 / 64 |
| deck | Prowess | top10_month | −3.7 | −5.7 .. −1.9 | 0.001 | yes | −6.9 .. −0.8 | 0.028 | yes | 5% | 6% | 67 / 95 |
| deck | Blink | top10_prev_month | −3.9 | −5.4 .. −2.3 | 0.001 | yes | −6.5 .. −1.4 | 0.002 | yes | 7% | 11% | 38 / 78 |
| archetype | Aggro | archetype | +8.6 | +5.8 .. +11.4 | 0.002 | yes | +3.1 .. +14.7 | 0.028 | yes | 3% | 2% | 236 / 223 |
| archetype | Rogue & unclassified | archetype | +1.9 | +0.8 .. +3.1 | 0.002 | yes | +0.0 .. +4.5 | 0.126 | no | 29% | 13% | 35 / 22 |
| archetype | Control | archetype | −0.5 | −2.2 .. +1.3 | 0.630 | no | −3.6 .. +2.9 | 0.808 | no | 12% | 7% | 58 / 69 |
| archetype | Ramp | archetype | −1.0 | −2.7 .. +0.8 | 0.327 | no | −4.2 .. +2.2 | 0.649 | no | 10% | 8% | 53 / 78 |
| archetype | Midrange | archetype | −2.7 | −4.8 .. −0.5 | 0.047 | yes | −7.1 .. +2.0 | 0.457 | no | 5% | 5% | 129 / 168 |
| archetype | Combo | archetype | −3.0 | −6.4 .. +0.4 | 0.136 | no | −9.7 .. +3.2 | 0.490 | no | 5% | 3% | 222 / 277 |
| archetype | Graveyard | archetype | −3.4 | −5.3 .. −1.7 | 0.002 | yes | −7.2 .. +0.1 | 0.126 | no | 15% | 9% | 44 / 95 |

## Summary

Of the 14 tracked decks and 7 archetypes, 8 decks and 1 archetype are beyond normal variation in both methods: Amulet Titan (+2.5 pp), Blink (−3.9 pp), Goryo's Vengeance (−3.7 pp), Land Destruction (−3.7 pp), Moonshadow (+10.3 pp), Prison (+3.1 pp), Prowess (−3.7 pp), Zoo (+2.1 pp), Aggro (+8.6 pp). Only in the event bootstrap: 3 decks and 3 archetypes: Affinity (+2.0 pp), Broodscale Combo (−2.4 pp), Devoted Combo (−1.7 pp), Graveyard (−3.4 pp), Midrange (−2.7 pp), Rogue & unclassified (+1.9 pp). Only in the pilot bootstrap: none. The pilot-bootstrap interval is 1.84 times as wide as the event-bootstrap interval in the median (range 1.42–2.85).

