#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kontrola jakosci po buildzie. Drukuje krotki raport, jedna linia na test.

    python3 qa.py --week 37 --events 27 --dir /mnt/user-data/outputs

Sprawdza:
  PPTX: validate.py, brak em-dashow, brak etykiet Trusted/Sample w tekstach wlasnych,
        suma trofeow na slajdzie Top 8 of the week = liczba eventow, brak nazw talii
        z listy FORBIDDEN (np. nazw wycofanych po reklasyfikacji)
  DOCX: kazdy plik istnieje, liczba obrazow = liczba znacznikow CHART w zrodle md,
        kazdy obraz ma podpis, zadna szerokosc powyzej px_w/96, brak pustych akapitow
        po naglowkach
  X:    liczba znakow kazdego posta ponizej 280
  Od W43 (dane DeckDB): brak slow z WORDS_BANNED w prezentacji i wszystkich md,
        brak em-dashow w md, suma trofeow w sekcji Trophy distribution dossier = eventy,
        zdania ze zmiana talii/archetypu w granicach wahan opisana slowami trendu
        (stats.json, sig=0) wypisywane jako UWAGA do recznej oceny,
        brak nierozwiazanych odwolan {fmt:...} i {{BLOCK: ...}} w prezentacji i w md
Zwraca kod 1, jesli ktorykolwiek test nie przeszedl.
"""
import argparse, glob, os, re, subprocess, sys, zipfile

VALIDATE = '/mnt/skills/public/pptx/scripts/office/validate.py'
FORBIDDEN = []          # np. ['WBG'] po reklasyfikacji          # np. ['WBG'] po reklasyfikacji, sprawdzane tylko w PPTX


def pptx_text(path):
    try:
        out = subprocess.run(['markitdown', path], capture_output=True, text=True).stdout
        if out.strip():
            return out
    except FileNotFoundError:
        pass
    from pptx import Presentation
    return '\n'.join(sh.text_frame.text for s in Presentation(path).slides
                     for sh in s.shapes if sh.has_text_frame)


def check_pptx(path, events, res):
    if not os.path.exists(path):
        res.append(('FAIL', 'brak pliku %s' % os.path.basename(path))); return
    r = subprocess.run([sys.executable, VALIDATE, path], capture_output=True, text=True)
    res.append(('OK' if 'PASSED' in r.stdout else 'FAIL', 'validate.py'))
    t = pptx_text(path)
    res.append(('FAIL' if '\u2014' in t else 'OK', 'brak em-dashow w prezentacji'))
    bad = re.findall(r'\b(Trusted|Sample)\b', t)
    res.append(('UWAGA' if bad else 'OK', 'etykiety Trusted/Sample: %d wystapien' % len(bad)))
    for f in FORBIDDEN:
        n = t.count(f)
        res.append(('UWAGA' if n else 'OK', 'nazwa wycofana "%s": %d wystapien' % (f, n)))
    seg = t.split('Top 8 of the week')[-1].split('Pilots')[0] if 'Top 8 of the week' in t else ''
    s = sum(int(x) for x in re.findall(r'(\d+) troph', seg))
    res.append(('OK' if s == events else 'FAIL',
                'suma trofeow na slajdzie: %d przy %d eventach' % (s, events)))


def check_docx(path, md_path, res):
    name = os.path.basename(path)
    if not os.path.exists(path):
        res.append(('FAIL', 'brak pliku %s' % name)); return
    z = zipfile.ZipFile(path)
    media = [n for n in z.namelist() if n.startswith('word/media/')]
    doc = z.read('word/document.xml').decode('utf8', 'ignore')
    if md_path and os.path.exists(md_path):
        want = len(re.findall(r'\{\{CHART:', open(md_path, encoding='utf8').read()))
        res.append(('OK' if len(media) == want else 'FAIL',
                    '%s: obrazy %d, znaczniki CHART %d' % (name, len(media), want)))
    caps = doc.count('<w:i/>')
    res.append(('OK' if caps >= len(media) else 'UWAGA',
                '%s: kursywne podpisy %d przy %d obrazach' % (name, caps, len(media))))
    widths = [int(x) for x in re.findall(r'<wp:extent cx="(\d+)"', doc)]
    over = [w for w in widths if w / 914400 > 12]
    res.append(('OK' if not over else 'FAIL', '%s: szerokosci obrazow w normie' % name))
    empty = len(re.findall(r'<w:p [^>]*/>', doc))
    res.append(('OK' if empty < 5 else 'UWAGA', '%s: puste akapity %d' % (name, empty)))


def check_x(md_path, res):
    if not os.path.exists(md_path):
        res.append(('UWAGA', 'brak %s' % os.path.basename(md_path))); return
    t = open(md_path, encoding='utf8').read()
    posts = re.split(r'\*\*Post \d+\*\*', t)[1:]
    bad = []
    for i, p in enumerate(posts, 1):
        body = re.split(r'\(\d+ characters', p)[0].strip()
        if len(body) >= 280:
            bad.append('%d (%d znakow)' % (i, len(body)))
    res.append(('OK' if not bad else 'FAIL',
                'posty na X ponizej 280 znakow, %d postow%s'
                % (len(posts), '' if not bad else ', za dlugie: ' + ', '.join(bad))))


WORDS_BANNED = ['winrate', 'encounter']
TREND = re.compile(r'\b(rising|rise|rose|falling|fell|surg\w*|collaps\w*|grow\w*|grew|declin\w*|dropp?\w*|up \d|down \d|gain\w*|lost)\b', re.I)
SAFE = re.compile(r'(within|inside) (normal|weekly)|within variation|not support|beyond', re.I)


def check_md_rules(src, week, events, res):
    import json
    st = json.load(open(os.path.join(src, 'stats.json')))
    flat = [r['deck'] for r in st['decks'] if not r['sig']] + [r['arch'] for r in st['archetypes'] if not r['sig']]
    for base in ['dossier', 'reddit', 'youtube', 'x']:
        p = os.path.join(src, '%s_w%s.md' % (base, week))
        if not os.path.exists(p): continue
        t = open(p, encoding='utf8').read()
        res.append(('FAIL' if '\u2014' in t else 'OK', '%s_w%s.md: brak em-dashow' % (base, week)))
        for w in WORDS_BANNED:
            n = len(re.findall(w, t, re.I))
            res.append(('FAIL' if n else 'OK', '%s_w%s.md: slowo "%s" %d razy' % (base, week, w, n)))
        if base == 'dossier':
            t = t.split('**Tensions')[0] + t.split('**What changed in the data')[-1]
        for sent in re.split(r'(?<=[.?!])\s+|\n', t):
            if TREND.search(sent) and not SAFE.search(sent) and any(d in sent for d in flat if d):
                res.append(('UWAGA', '%s: slowo trendu przy zmianie w granicach wahan: %s' % (base, sent.strip()[:120])))
    d = open(os.path.join(src, 'dossier_w%s.md' % week), encoding='utf8').read()
    seg = d.split('**Trophy distribution')[-1].split('{{CHART')[0]
    s = sum(int(n) * len(names.split(',')) for n, names in re.findall(r'^- (\d+): ([^\n]+)$', seg, re.M) if not names.startswith(('Aggro', 'archetypes')))
    res.append(('OK' if s == events else 'FAIL', 'dossier: suma trofeow %d przy %d eventach' % (s, events)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--week', required=True)
    ap.add_argument('--events', type=int, required=True)
    ap.add_argument('--dir', default='/mnt/user-data/outputs')
    ap.add_argument('--src', default='.', help='katalog roboczy z plikami md')
    a = ap.parse_args()
    w = a.week
    res = []
    check_pptx(os.path.join(a.dir, 'modern_metagame_w%s.pptx' % w), a.events, res)
    for base in ['dossier', 'reddit', 'youtube', 'x']:
        check_docx(os.path.join(a.dir, '%s_w%s.docx' % (base, w)),
                   os.path.join(a.src, '%s_w%s.md' % (base, w)) if base == 'dossier' else None, res)
    check_x(os.path.join(a.src, 'x_w%s.md' % w), res)
    t = pptx_text(os.path.join(a.dir, 'modern_metagame_w%s.pptx' % w))
    for word in WORDS_BANNED:
        n = len(re.findall(word, t, re.I))
        res.append(('FAIL' if n else 'OK', 'prezentacja: slowo "%s" %d razy' % (word, n)))
    check_md_rules(a.src, w, a.events, res)
    left = re.findall(r'\{(?:pp|pct|n|f1|var|raw):[^}]*\}|\{\{A?BLOCK:[^}]*\}\}', t)
    for base in ['dossier', 'reddit', 'youtube', 'x']:
        p = os.path.join(a.src, '%s_w%s.md' % (base, w))
        if os.path.exists(p): left += re.findall(r'\{(?:pp|pct|n|f1|var|raw):[^}]*\}|\{\{A?BLOCK:[^}]*\}\}', open(p, encoding='utf8').read())
    res.append(('FAIL' if left else 'OK', 'nierozwiazane odwolania w gotowych plikach: %d %s' % (len(left), left[:3])))
    for st, msg in res:
        print('%-5s %s' % (st, msg))
    return 1 if any(st == 'FAIL' for st, _ in res) else 0


if __name__ == '__main__':
    sys.exit(main())
