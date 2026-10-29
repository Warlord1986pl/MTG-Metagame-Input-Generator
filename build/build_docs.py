# -*- coding: utf-8 -*-
"""Buduje wszystkie cztery pliki docx z odpowiadajacych im plikow md.

    python3 build_docs.py --week 37 --src . --png /mnt/user-data/uploads --out /mnt/user-data/outputs

Zasady obrazow (twarde): PNG wstawiany oryginalnym plikiem, bez PIL i bez ponownego
zapisu; szerokosc min(px_w/200, swiatlo kolumny), gorny limit px_w/96; wysokosc z
proporcji pliku. Kadrowanie przez srcRect, czyli tez bez ruszania pliku.
"""
import re, sys, os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image

import argparse
UP='/mnt/user-data/uploads/'
OUT='/mnt/user-data/outputs/'
SRC='.'
COLW=6.7  # swiatlo kolumny A4 przy marginesach 2 cm
# kadrowanie per plik: (left, top, right, bottom) jako ulamki
CROPS={}  # od W40 record_probability jest jednopanelowy, bez kadrowania

def add_image(doc, fname, caption):
    path=UP+fname
    with Image.open(path) as im:
        pw,ph=im.size
    crop=CROPS.get(fname)
    if crop:
        l,t,r,b=crop
        eff_w=pw*(1-l-r); eff_h=ph*(1-t-b)
    else:
        l=t=r=b=0.0
        eff_w,eff_h=pw,ph
    width=min(eff_w/200.0, COLW)
    hard=eff_w/96.0
    width=min(width, hard)
    height=width*eff_h/eff_w
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    run=p.add_run()
    run.add_picture(path, width=Inches(width), height=Inches(height))
    if crop:
        # srcRect na blipFill, bez ponownego zapisu pliku PNG
        inline=run._r.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline')
        blipFill=inline.find('.//{http://schemas.openxmlformats.org/drawingml/2006/picture}blipFill')
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        srcRect=OxmlElement('a:srcRect')
        for k,v in (('l',l),('t',t),('r',r),('b',b)):
            if v: srcRect.set(k, str(int(round(v*100000))))
        blip=blipFill.find(qn('a:blip'))
        blip.addnext(srcRect)
    c=doc.add_paragraph(); c.alignment=WD_ALIGN_PARAGRAPH.CENTER
    cr=c.add_run(caption.strip()); cr.italic=True; cr.font.size=Pt(9)
    cr.font.color.rgb=RGBColor(0x55,0x55,0x55)
    return width, height, (pw,ph)

BOLD=re.compile(r'\*\*(.+?)\*\*')
def add_rich(p, text):
    pos=0
    for m in BOLD.finditer(text):
        if m.start()>pos: p.add_run(text[pos:m.start()])
        r=p.add_run(m.group(1)); r.bold=True
        pos=m.end()
    if pos<len(text): p.add_run(text[pos:])

def build(md_path, docx_path):
    src=open(md_path, encoding='utf-8').read()
    doc=Document()
    st=doc.styles['Normal']; st.font.name='Calibri'; st.font.size=Pt(10.5)
    for s in doc.sections:
        s.left_margin=s.right_margin=Inches(0.79)
        s.top_margin=s.bottom_margin=Inches(0.79)
    imgs=0; caps=0; widths=[]
    for raw in src.split('\n\n'):
        block=raw.strip('\n')
        if not block.strip(): continue
        for line in block.split('\n'):
            line=line.rstrip()
            if not line.strip(): continue
            m=re.match(r'^\{\{CHART:\s*(.+?)\s*\|\s*(.+?)\s*\}\}$', line)
            if m:
                fn, cap = m.group(1), m.group(2)
                if not os.path.exists(UP+fn):
                    p=doc.add_paragraph(); p.add_run('chart n/a: '+fn).italic=True
                    continue
                w,h,native=add_image(doc, fn, cap); imgs+=1; caps+=1
                widths.append((fn,w,native[0]/96.0))
                continue
            m=re.match(r'^\{\{HEAD:\s*(.+?)\s*\|\s*([0-9A-Fa-f]{6})\s*\}\}$', line)
            if m:
                p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before=Pt(10); p.paragraph_format.space_after=Pt(2)
                p.paragraph_format.keep_with_next=True
                r=p.add_run(m.group(1)); r.bold=True; r.font.size=Pt(12)
                r.font.color.rgb=RGBColor.from_string(m.group(2).upper())
                continue
            m=re.match(r'^\{\{DATA:\s*(.+?)\s*\}\}$', line)
            if m:
                p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after=Pt(6); p.paragraph_format.keep_with_next=True
                add_rich(p, m.group(1))
                continue
            if line.startswith('- '):
                p=doc.add_paragraph(style='List Bullet')
                add_rich(p, line[2:])
                p.paragraph_format.space_after=Pt(2)
                continue
            p=doc.add_paragraph()
            add_rich(p, line)
            p.paragraph_format.space_after=Pt(4)
    doc.save(docx_path)
    return imgs, caps, widths

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--week', required=True)
    ap.add_argument('--src', default='.')
    ap.add_argument('--png', default='/mnt/user-data/uploads')
    ap.add_argument('--out', default='/mnt/user-data/outputs')
    args=ap.parse_args()
    UP=args.png.rstrip('/')+'/'; OUT=args.out.rstrip('/')+'/'; SRC=args.src.rstrip('/')+'/'
    globals()['UP']=UP; globals()['OUT']=OUT
    os.makedirs(OUT, exist_ok=True)
    w=args.week
    jobs=[(SRC+'%s_w%s.md'%(b,w), '%s_w%s.docx'%(b,w)) for b in
          ['dossier','reddit','youtube','x']]
    for md,dx in jobs:
        if not os.path.exists(md):
            print('skip (brak md):',md); continue
        i,c,w=build(md, OUT+dx)
        print(dx,'obrazy:',i,'podpisy:',c)
        for fn,ww,hard in w:
            flag='OK' if ww<=hard+1e-9 else 'PRZEKROCZONY LIMIT'
            print('   ',fn,'width %.2f in, limit px_w/96 %.2f in'%(ww,hard),flag)
