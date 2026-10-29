# -*- coding: utf-8 -*-
"""Walidacja i agregacja paczki DeckDB (od 2026-W43).

    python3 analysis.py --week 43 --src /mnt/user-data/uploads --out .

Wejscie: data.xlsx (arkusze opisane w 'contents') i notes.md (Status, Glossary).
Wyjscie: krotki raport walidacji na stdout i stats.json dla build_deck.js i gen_dossier.py.
Testy = czesc 2 master promptu (1, 3-8). Test 2 (zbior ID eventow na mtgo.com) zostaje
reczny: skrypt wypisuje ID do porownania. Brak fallbackow: test FAIL konczy sie kodem 1.
Usuniete wobec wersji sprzed W43: tiery, ALL_*, BestPilots, mediany, etykiety,
winrate, encounter, delta, MERGES (klasyfikacja nalezy do DeckDB).
"""
import argparse, json, os, re, sys
import pandas as pd

SHEETS = ['decks', 'archetypes', 'top8', 'new_cards', 'pilots',
          'share_history_decks', 'share_history_archetypes']
RU = 'Rogue & unclassified'


def parse_notes(path):
    t = open(path, encoding='utf8').read()
    g = lambda pat: re.search(pat, t).group(1)
    st = dict(
        week=g(r'ISO (\d{4}-W\d{2})'),
        start=g(r'ISO \d{4}-W\d{2}, (\d{4}-\d{2}-\d{2})'),
        end=g(r'\.\. (\d{4}-\d{2}-\d{2}) \(as of'),
        events=int(g(r'Events: (\d+)')),
        lists=int(g(r'Top 32 lists: (\d+)')),
        coverage=float(g(r'Participant coverage: ([\d.]+)%')),
        players=int(g(r'/ (\d+) players')),
        rules=g(r'DeckDB rules version: (\w+)'),
        sha=g(r'sha256 (\w{64})'),
        triage=g(r'Triage \(DeckDB K\d+\): (\w+)'),
        monthly='Monthly material' in t,
    )
    ev = re.findall(r'^\| (\d{8}) \| (\d{4}-\d{2}-\d{2}) \| (\d+) \| (\d+) \|$', t, re.M)
    st['event_rows'] = [dict(id=int(a), date=b, players=int(c), lists=int(d)) for a, b, c, d in ev]
    m = re.search(r'Classification changes since the previous week.*?(\d+) common lists, (\d+) changed', t, re.S)
    st['class_changes'] = dict(common=int(m.group(1)), changed=int(m.group(2))) if m else None
    st['class_change_lines'] = re.findall(r'^- (.+ → .+: \d+)$', t, re.M)
    m = re.search(r'fixed colour on the deck trend.*?\): (.+?) \(', t)
    st['shared_colours'] = m.group(1) if m else ''
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--week', required=True)
    ap.add_argument('--src', default='/mnt/user-data/uploads')
    ap.add_argument('--out', default='.')
    a = ap.parse_args()
    X = pd.read_excel(os.path.join(a.src, 'data.xlsx'), sheet_name=None)
    S = parse_notes(os.path.join(a.src, 'notes.md'))
    R = []; ok = True

    def chk(cond, msg):
        nonlocal ok
        R.append(('OK  ' if cond else 'FAIL') + ' ' + msg); ok = ok and bool(cond)

    # 1. Status, wersje
    chk(S['week'].endswith('W%s' % a.week), 'tydzien %s, okno %s .. %s' % (S['week'], S['start'], S['end']))
    vers = {(tuple(X[s].rules_version.unique()), tuple(X[s].export_sha256.unique()),
             tuple(X[s].data_version.unique())) for s in X if 'rules_version' in X[s]}
    one = len(vers) == 1 and all(len(v) == 1 for v in list(vers)[0])
    chk(one and list(vers)[0][0][0] == S['rules'] and list(vers)[0][1][0] == S['sha'],
        'jedna wersja regul %s i jeden sha256 we wszystkich arkuszach i w Status' % S['rules'])
    for s in SHEETS:
        chk(s in X, 'arkusz %s obecny' % s)
    R.append('INFO triage: %s' % S['triage'])
    # 2. eventy (zewnetrznie recznie)
    E = S['event_rows']
    chk(len(E) == S['events'] and len({e['id'] for e in E}) == len(E),
        '%d eventow w tabeli Status, ID unikalne' % len(E))
    R.append('RECZNIE porownaj na mtgo.com/decklists: ' + ', '.join('%d (%s)' % (e['id'], e['date']) for e in E))
    chk(all(S['start'] <= e['date'] <= S['end'] for e in E), 'daty eventow w oknie')
    # 3. listy
    exp = sum(min(32, e['players']) for e in E)
    short = [e['id'] for e in E if e['lists'] != min(32, e['players'])]
    chk(exp == S['lists'] == sum(e['lists'] for e in E) and not short,
        'listy %d = suma min(32, gracze) %d%s' % (S['lists'], exp, '' if not short else ', rozjazd: %s' % short))
    chk(sum(e['players'] for e in E) == S['players'] and abs(100 * S['lists'] / S['players'] - S['coverage']) < 0.05,
        'pokrycie %.1f%% = %d / %d' % (S['coverage'], S['lists'], S['players']))
    # 4. Top 8 i trofea
    T = X['top8']
    g = T.groupby('EventID').agg(n=('Place', 'size'), w=('Winner', 'sum'))
    bad = g[(g.n != 8) | (g.w != 1)].index.tolist()
    chk(set(g.index) == {e['id'] for e in E}, 'Top 8: te same ID co w Status')
    chk(len(T) == 8 * S['events'] and T.Winner.sum() == S['events'] and not bad,
        'listy Top 8 %d = 8 x %d, trofea %d = eventy%s' % (len(T), S['events'], T.Winner.sum(), '' if not bad else ', rozjazd: %s' % bad))
    # 5. udzialy
    D, A = X['decks'], X['archetypes']
    chk(D.Lists.sum() == S['lists'] and abs(D.Share_Top32_pct.sum() - 100) < 0.05,
        'talie + other + R&U: %d list, %.2f%%' % (D.Lists.sum(), D.Share_Top32_pct.sum()))
    chk(A.Lists.sum() == S['lists'] and abs(A.Share_Top32_pct.sum() - 100) < 0.05,
        'archetypy + R&U: %d list, %.2f%%' % (A.Lists.sum(), A.Share_Top32_pct.sum()))
    # 6. monotonicznosc po wszystkich taliach tygodnia
    H = X['share_history_decks']; HW = H[H.ISO_week == S['week']].set_index('Deck')
    chk(HW.Lists.sum() == S['lists'], 'historia: tydzien %s ma %d list' % (S['week'], HW.Lists.sum()))
    t8 = T.groupby('Deck').size(); tr = T[T.Winner == 1].groupby('Deck').size()
    viol = [d for d in HW.index if not (tr.get(d, 0) <= t8.get(d, 0) <= HW.Lists[d])]
    viol += D[D.Events_with_list > D.Week_events].Deck.tolist()
    chk(not viol, 'trofea <= Top 8 <= Top 32, obecnosc <= eventy (%d talii)%s' % (len(HW), '' if not viol else ': %s' % viol))
    # 7. poprzedni tydzien z tej samej paczki
    weeks = sorted(H.ISO_week.unique()); prev = weeks[-2]
    HP = H[H.ISO_week == prev].set_index('Deck')
    real = D[~D.Deck.isin(['other decks'])]
    diff = [r.Deck for r in real.itertuples() if HP.Lists.get(r.Deck, 0) != r.Prev_week_lists]
    chk(not diff, 'poprzedni tydzien (%s) w arkuszu decks = historia w tej samej paczce' % prev)
    # 8. Rogue & unclassified
    ru = D[D.Deck == RU].iloc[0]
    flag = ru.Share_Top32_pct > 5 or ru.Change_pp > 2
    R.append(('UWAGA' if flag else 'OK  ') + ' %s %.1f%%, zmiana %+.1f pp' % (RU, ru.Share_Top32_pct, ru.Change_pp))
    # nazwy
    names = set(HW.index) | set(T.Deck)
    cc = [n for n in names if re.fullmatch(r'[WUBRGC]{1,5}', str(n))]
    chk(not cc, 'brak nazw talii bedacych kodem kolorow%s' % ('' if not cc else ': %s' % cc))
    pl = [v for v in T.Variant.dropna().unique() if re.search(r'\b(inne|pozostale|pozostałe)\b', v)]
    if pl: R.append('UWAGA polskie etykiety wariantow: %s (zadanie DeckDB)' % pl)
    # remis na granicy top 10
    top = D[~D.Deck.isin(['other decks', RU])]
    edge = HW.drop(RU, errors='ignore').Lists
    tied = edge[edge == top.Lists.min()].index.tolist()
    if len(tied) > (top.Lists == top.Lists.min()).sum():
        R.append('UWAGA remis na granicy top 10 (%d list): %s; w top 10: %s'
                 % (top.Lists.min(), ', '.join(tied), ', '.join(top[top.Lists == top.Lists.min()].Deck)))
    if 'variants_week' in X:
        V = X['variants_week']; vs = V.groupby('Deck')[['Lists_Top32', 'Lists_Top8', 'Trophies']].sum()
        badv = [d for d in vs.index if (vs.Lists_Top32[d], vs.Lists_Top8[d], vs.Trophies[d]) != (int(HW.Lists.get(d, -1)), int(t8.get(d, 0)), int(tr.get(d, 0)))]
        chk(not badv, 'variants_week: sumy wariantow = talia (Top 32, Top 8, trofea) dla %d talii%s' % (len(vs), '' if not badv else ': %s' % badv))
    sig = D[D.Change_CI_excludes_0 == 1].Deck.tolist() + ['archetyp ' + x for x in A[A.Change_CI_excludes_0 == 1].Archetype]
    R.append('INFO zmiany poza wahaniami: %s' % (', '.join(sig) or 'brak'))

    print('\n'.join(R))
    if not ok:
        print('WALIDACJA NIE PRZESZLA, stats.json nie zapisany'); sys.exit(1)

    P = X['pilots'].set_index('Deck'); NC = X['new_cards']
    rows = []
    for r in D.itertuples():
        rows.append(dict(deck=r.Deck, arch=r.Archetype if isinstance(r.Archetype, str) else '',
                         lists=int(r.Lists), share=round(r.Share_Top32_pct, 2), ev=int(r.Events_with_list),
                         pilots=int(r.Pilots), change=round(r.Change_pp, 2), sig=int(r.Change_CI_excludes_0),
                         ci=[round(r.Change_CI_low_pp, 1), round(r.Change_CI_high_pp, 1)],
                         prev_lists=int(r.Prev_week_lists), n_decks=int(r.Decks_in_row),
                         top8=int(t8.get(r.Deck, 0)), troph=int(tr.get(r.Deck, 0)),
                         top8_events=int(T[T.Deck == r.Deck].EventID.nunique()),
                         top_pilot_share=round(P.Top_pilot_share_pct.get(r.Deck, float('nan')), 1) if r.Deck in P.index else None,
                         concentrated=int(P.Concentrated.get(r.Deck, 0)) if r.Deck in P.index else 0))
    arch = [dict(arch=r.Archetype, lists=int(r.Lists), pilots=int(r.Pilots), share=round(r.Share_Top32_pct, 2), ev=int(r.Events_with_list),
                 change=round(r.Change_pp, 2), sig=int(r.Change_CI_excludes_0),
                 top8=int((T.Archetype == r.Archetype).sum()), troph=int(((T.Archetype == r.Archetype) & (T.Winner == 1)).sum()),
                 top_deck=(top[top.Archetype == r.Archetype].sort_values('Lists', ascending=False).Deck.head(1).tolist() or [''])[0])
            for r in A.itertuples()]
    winners = [dict(event=int(r.EventID), date=str(r.Date)[:10], deck=r.Deck,
                    variant=r.Variant if isinstance(r.Variant, str) else '', arch=r.Archetype)
               for r in T[T.Winner == 1].sort_values(['Date', 'EventID']).itertuples()]
    top8 = [dict(deck=d, top8=int(n), troph=int(tr.get(d, 0)), lists=int(HW.Lists.get(d, 0)))
            for d, n in t8.sort_values(ascending=False).items()]
    newc = [dict(deck=r.Deck, card=r.Card, zone=r.Zone, pilots=int(r.Pilots_with_card), lists=int(r.Lists_with_card))
            for r in NC[NC.Reported == 1].sort_values('Pilots_with_card', ascending=False).itertuples()]
    hist = {d: H[H.Deck == d].set_index('ISO_week').Share_Top32_pct.round(1).to_dict() for d in HW.index}
    variants = X['variants_week'].drop(columns=['rules_version', 'export_sha256', 'data_version']).to_dict('records') if 'variants_week' in X else []
    lc = []; lc_all = []
    if 'm_list_changes' in X:
        M = X['m_list_changes']; M = M[(M.Reported_change == 1) & (M.Mana_only_land == 0)]
        M = M.reindex(M.Within_variants_pp.abs().sort_values(ascending=False).index)
        lc_all = [dict(deck=r.Deck, card=r.Card, zone=r.Zone, p1=round(r.Presence_1_pct), p2=round(r.Presence_2_pct),
                       change=round(r.Within_variants_pp, 1)) for r in M.itertuples()]
        lc = [dict(deck=r.Deck, card=r.Card, zone=r.Zone, p1=round(r.Presence_1_pct), p2=round(r.Presence_2_pct),
                   change=round(r.Within_variants_pp, 1), ci=[round(r.Within_CI_low_pp, 1), round(r.Within_CI_high_pp, 1)],
                   pilots_deck=int(r.Deck_pilots_2), pilots_card=int(r.Pilots_with_card_2)) for r in M.head(12).itertuples()]
    S.update(week_num=a.week, variants=variants, list_changes=lc, list_changes_all=lc_all, prev_week=prev, decks=rows, archetypes=arch, winners=winners, top8=top8,
             new_cards=newc, history=hist, all_week=HW.Lists.astype(int).to_dict(),
             top8_variants=T.groupby(['Deck', 'Variant']).size().reset_index().values.tolist(),
             validation=R)
    json.dump(S, open(os.path.join(a.out, 'stats.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    print('zapisane: stats.json w %s' % a.out)


if __name__ == '__main__':
    main()
