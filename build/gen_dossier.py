# -*- coding: utf-8 -*-
"""Preprocesor dossier (wersja od 2026-W43, dane DeckDB). Kod jest staly; tresc
   tygodnia pisze sie w dossier_w<N>.src.md w katalogu roboczym:

       python3 gen_dossier.py 43      ->  dossier_w43.md

   W zrodle:
     {{BLOCK: Talia}} / {{ABLOCK: Archetyp}}  -> {{HEAD}} z nazwa i {{DATA}} z jedna linia danych
                                               (Weekly change: rise / fall / stable) ze stats.json;
                                               rownowazniki pisze sie pod spodem jako '- ...'
     {{CARDS: Talia}}                         -> nowe karty tygodnia i zmiany list talii (obowiazkowe
                                               pod kazdym {{BLOCK}}, po rownowaznikach; Tension talii pod nim)
     {{CARDS_REST}}                           -> zmiany kart w taliach bez bloku w dossier
     {fmt:prefix.nazwa.pole}                  -> liczba ze stats.json (skladnia jak w build_deck.js)
     {{CHART: ...}}, sekcje PL, Tensions      -> przechodza bez zmian
   Nierozwiazane odwolanie albo nieznana talia przerywa przebieg. Na koncu kontrola
   mechaniczna: pierwszy wiersz po liniach danych kazdego bloku = rownowaznik czy proza.
"""
import json, math, re, sys

W = sys.argv[1] if len(sys.argv) > 1 else sys.exit('podaj numer tygodnia: python3 gen_dossier.py 43')
S = json.load(open('stats.json', encoding='utf-8'))
N = S['events']
D = {r['deck']: r for r in S['decks']}; A = {r['arch']: r for r in S['archetypes']}
MINUS = '\u2212'
half = lambda x: math.floor(x + 0.5)
FMT = {'pp': lambda x: '%s%.1f pp' % (MINUS if x < 0 else '+', abs(x)),
       'pct': lambda x: '%d%%' % half(x), 'n': lambda x: str(half(x)), 'f1': lambda x: '%.1f' % x,
       'var': lambda x: 'not tested' if x is None else ('beyond normal variation' if x else 'within normal variation'), 'raw': str}
MISSING = object()
LOW = 'low-volume deck: too little data for reliable weekly results, described as part of its archetype'


def pair(arr, k1, k2, name):
    a, b = name.split('|', 1)
    return next((r for r in arr if r[k1] == a and r[k2] == b), None)


def lookup(p, name, field):
    """Zwraca wartosc albo MISSING (brak wiersza lub pola). None = pole jest, ale puste (np. zmiana nietestowana)."""
    if p == 's': return S.get(field, MISSING)
    if p == 'h': return S['history'].get(name, {}).get(field, MISSING)
    if p == 'w': return S['all_week'].get(name, MISSING) if field == 'lists' else MISSING
    row = {'d': lambda: D.get(name), 'a': lambda: A.get(name),
           'v': lambda: pair(S['variants'], 'Deck', 'Variant', name),
           'c': lambda: pair(S['new_cards'], 'deck', 'card', name),
           'l': lambda: pair(S['list_changes'], 'deck', 'card', name)}.get(p, lambda: None)()
    return row.get(field, MISSING) if row else MISSING


TOK = re.compile(r'\{(pp|pct|n|f1|var|raw):([a-z])\.(?:([^{}]+?)\.)?([\w-]+)\}')


def resolve(t):
    def f(m):
        v = lookup(m.group(2), m.group(3), m.group(4))
        if v is MISSING or (v is None and m.group(1) != 'var'): sys.exit('nierozwiazane odwolanie: ' + m.group(0))
        return FMT[m.group(1)](v)
    return TOK.sub(f, t)


def header(head, title, arch, r):
    """Naglowek bloku: {{HEAD}} (tytul, wysrodkowany) i {{DATA}} (jedna linia danych)."""
    trend = 'not tested' if r['sig'] is None else (('rise' if r['change'] > 0 else 'fall') if r['sig'] else 'stable')
    data = ['**Share of Challenge Top 32:** %s' % FMT['pct'](r['share']),
            '**Change vs last week:** %s, %s' % (FMT['pp'](r['change']), FMT['var'](r['sig'])),
            '**Event presence:** %d/%d' % (r['ev'], N),
            '**Top 8 lists:** %d' % r['top8'],
            '**Trophies:** %d' % r['troph'],
            '**Pilots:** %s' % r.get('pilots', 'n/a')]
    return '{{HEAD: %s | 000000 }}\n\n{{DATA: **%s | %s | Weekly change: %s** | %s }}' % (head, title, arch, trend, ' | '.join(data))


def expand(m):
    kind, name = m.group(1), m.group(2).strip()
    if kind == 'BLOCK':
        if name not in D: sys.exit('nieznana talia w {{BLOCK}}: ' + name)
        return header(name, name, D[name]['arch'], D[name])
    if name not in A: sys.exit('nieznany archetyp w {{ABLOCK}}: ' + name)
    return header(name, name + ' (archetype)', name, A[name])


# --- zmiany kart przy taliach: nowe karty tygodnia + zmiany list miesiac do miesiaca ---
LC_LABEL = 'Sep to Oct'   # okres m_list_changes; zmieniasz razem z miesiacem materialu
def card_lines(deck):
    out = ['new this week: %s%s, %d pilots, %d lists' % (c['card'], ' (SB)' if c['zone'] == 'sideboard' else '', c['pilots'], c['lists'])
           for c in S['new_cards'] if c['deck'] == deck]
    out += ['%s: %s%s %s, %d%% to %d%% of lists' % (LC_LABEL, c['card'], ' (SB)' if c['zone'] == 'sideboard' else '',
            'added' if c['change'] > 0 else 'cut', c['p1'], c['p2'])
            for c in S.get('list_changes_all', []) if c['deck'] == deck]
    return out
def cards(m):
    deck = m.group(1).strip()
    if deck not in D: sys.exit('nieznana talia w {{CARDS}}: ' + deck)
    L = card_lines(deck)
    return '**Card changes:**' + (' none reported' if not L else '\n\n' + '\n\n'.join('- ' + x for x in L))


src = open('dossier_w%s.src.md' % W, encoding='utf-8').read()

# --- regula wyboru talii z blokiem (master prompt, czesc 6A) ---
def expected_blocks():
    decks = [r for r in S['decks'] if r['deck'] not in ('other decks', 'Rogue & unclassified')]
    top10 = set(S['top10']); edge = min(D[d]['lists'] for d in top10)
    exp = set(top10) | {r['deck'] for r in decks if r['lists'] == edge}                     # (1) top 10, (2) remis na granicy
    for a in {r['arch'] for r in decks}:                                                     # (3) archetyp bez talii
        if not any(D[d]['arch'] == a for d in exp):
            cand = sorted((r for r in decks if r['arch'] == a and r['share'] >= 1), key=lambda r: (-r['lists'], r['deck']))
            if cand: exp.add(cand[0]['deck'])
    exp |= {r['deck'] for r in decks if r['tracked'] and r['sig'] == 1}                     # (4) zmiana poza wahaniami
    if 'Zoo' in D and D['Zoo']['lists'] > 0: exp.add('Zoo')                                  # (5) Zoo zawsze
    return exp
blocked_list = [x.strip() for x in re.findall(r'\{\{BLOCK: ([^}]+)\}\}', src)]
exp = expected_blocks()
if set(blocked_list) != exp:
    sys.exit('regula wyboru talii: brak bloku dla %s, blok nadmiarowy dla %s'
             % (sorted(exp - set(blocked_list)) or '-', sorted(set(blocked_list) - exp) or '-'))
if 'Zoo' in exp and D['Zoo']['arch'] != 'Midrange':
    print('UWAGA: Zoo ma w danych archetyp %s, nie Midrange' % D['Zoo']['arch'])
# talia spoza tracked decks: pierwszy rownowaznik = formula LOW
for m in re.finditer(r'\{\{BLOCK: ([^}]+)\}\}\n\n- ([^\n]+)', src):
    d = m.group(1).strip()
    if not D[d]['tracked'] and m.group(2).strip() != LOW:
        sys.exit('talia spoza tracked decks bez formuly low-volume jako pierwszego rownowaznika: ' + d)
blocked = set(x.strip() for x in re.findall(r'\{\{BLOCK: ([^}]+)\}\}', src))
withcards = set(x.strip() for x in re.findall(r'\{\{CARDS: ([^}]+)\}\}', src))
if blocked - withcards: sys.exit('talie z blokiem bez {{CARDS}}: %s' % sorted(blocked - withcards))
changed = sorted({c['deck'] for c in S['new_cards']} | {c['deck'] for c in S.get('list_changes_all', [])})
rest = [d for d in changed if d not in blocked]
if rest and '{{CARDS_REST}}' not in src: sys.exit('zmiany kart w taliach spoza dossier, brak {{CARDS_REST}}: %s' % rest)
src = src.replace('{{CARDS_REST}}', '\n\n'.join('- %s, %s' % (d, x) for d in rest for x in card_lines(d)) or 'none')
src = re.sub(r'\{\{CARDS: ([^}]+)\}\}', cards, src)
firsts = [m.group(1) for m in re.finditer(r'\{\{A?BLOCK: [^}]+\}\}\n\n- ([^\n]+)', src)]
nblocks = len(re.findall(r'\{\{A?BLOCK: ', src))
out = resolve(re.sub(r'\{\{(A?BLOCK): ([^}]+)\}\}', expand, src))
open('dossier_w%s.md' % W, 'w', encoding='utf-8').write(out)

VERB = re.compile(r"\b(is|are|was|were|has|have|had|took|went|shows?|remains?|looks?|stays?|holds?|wins?|won|rose|fell|became|seems?)\b", re.I)
def is_equiv(x):
    w = x.split()
    return 4 <= len(w) <= 12 and not VERB.search(x) and not x.rstrip().endswith('.') and \
        bool(re.search(r'\d', x) or re.search(r'\b(vs|than|above|below|joint|both|all|tied|new)\b', x))
prose = [b for b in map(resolve, firsts) if b.strip() != LOW and not is_equiv(b)]
missing = nblocks - len(firsts)
print('dossier_w%s.md: %d blokow, bez rownowaznika pod blokiem: %d, proza w pierwszym wierszu: %d %s'
      % (W, nblocks, missing, len(prose), prose))
