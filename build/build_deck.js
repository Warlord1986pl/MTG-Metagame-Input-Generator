/* =======================  BUILD PREZENTACJI  =======================
   Wersja od 2026-W43 (dane DeckDB). Kod jest staly; tresc tygodnia (CFG, callouty,
   notki, karty) czyta z deck_w<N>.json w katalogu roboczym:

       NODE_PATH=$(npm root -g) node build_deck.js 43

   Liczby w tresci zapisuje sie jako odwolania do stats.json:
     {fmt:prefix.nazwa.pole}  fmt: pp | pct | n | f1 | var | raw
     prefix: d (talia), a (archetyp), s (stats, bez nazwy: {n:s.lists}),
             h (historia udzialu talii, pole = tydzien ISO), v (wariant 'Talia|Wariant'),
             c (nowa karta 'Talia|Karta'), l (zmiana listy 'Talia|Karta'), w (listy talii w tygodniu, pole 'lists')
   Nierozwiazane odwolanie przerywa build (bez wartosci zastepczej).
   Typy slajdow: title, cards, snapshot, chart, top8, zoo, two.
   ==================================================================== */
const pptxgen = require('pptxgenjs');
const fs = require('fs');
const sizeOf = (p)=>{ // minimalny odczyt rozmiaru PNG
  const b=fs.readFileSync(p); return {w:b.readUInt32BE(16), h:b.readUInt32BE(20)};
};
const S = JSON.parse(fs.readFileSync('stats.json','utf8'));
const BG='111827', CARD='1F2937', BAR='3B82F6', AMB='F59E0B';
const TXT='FFFFFF', SUB='C7CDD6', MUT='9AA4B2';
const C={Aggro:'EF4444',Graveyard:'6366F1',Combo:'3B82F6',Midrange:'F59E0B',Ramp:'14B8A6',Control:'60A5FA','Rogue & unclassified':'6B7280'};
const GREEN='22C55E', RED='EF4444', GREY='9AA4B2';
const F='Calibri';
const pres=new pptxgen();
pres.layout='LAYOUT_WIDE';
pres.defineSlideMaster({title:'MAIN', background:{color:BG}});
const W=13.33, H=7.5;

function slide(){ return pres.addSlide({masterName:'MAIN'}); }
function tro(n){ return n+(n===1?' trophy':' trophies'); }

function head(s,title,sub){
  s.addShape(pres.ShapeType.rect,{x:0.42,y:0.36,w:0.09,h:0.52,fill:{color:BAR}});
  s.addText(title,{x:0.62,y:0.30,w:12.2,h:0.62,fontFace:F,fontSize:30,bold:true,color:TXT,isTextBox:true,margin:0});
  if(sub) s.addText(sub,{x:0.62,y:0.92,w:12.2,h:0.34,fontFace:F,fontSize:13,color:AMB,isTextBox:true,margin:0});
}
function card(s,x,y,w,h,color,title,lines,opt){
  opt=opt||{};
  s.addShape(pres.ShapeType.roundRect,{x:x,y:y,w:w,h:h,rectRadius:0.06,fill:{color:opt.fill||CARD},line:{color:color,width:1.5}});
  s.addText(title,{x:x+0.18,y:y+0.12,w:w-0.36,h:0.32,fontFace:F,fontSize:opt.ts||15,bold:true,color:color,isTextBox:true,margin:0});
  if(lines&&lines.length){
    const arr=lines.map((t,i)=>({text:t,options:{breakLine:i<lines.length-1}}));
    s.addText(arr,{x:x+0.18,y:y+0.46,w:w-0.36,h:h-0.58,fontFace:F,fontSize:opt.bs||11.5,color:opt.bc||SUB,isTextBox:true,margin:0,lineSpacingMultiple:1.06});
  }
}
function image(s,file,x,y,maxW,maxH){
  const d=sizeOf(file); const r=d.h/d.w;
  let w=maxW, h=w*r; if(h>maxH){h=maxH; w=h/r;}
  s.addImage({path:file,x:x+(maxW-w)/2,y:y+(maxH-h)/2,w:w,h:h});
  return {w:w,h:h};
}
function note(s,text){
  s.addText(text,{x:0.62,y:H-0.62,w:12.1,h:0.34,fontFace:F,fontSize:10.5,italic:true,color:MUT,isTextBox:true,margin:0,align:'center'});
}


/* ---------- odwolania do stats.json ---------- */
const MINUS='\u2212';
const D={}; S.decks.forEach(r=>D[r.deck]=r);
const A={}; S.archetypes.forEach(r=>A[r.arch]=r);
const half=(x)=>Math.floor(x+0.5);
const FMT={
  pp:(x)=>(x<0?MINUS:'+')+Math.abs(x).toFixed(1)+' pp',
  pct:(x)=>half(x)+'%', n:(x)=>String(half(x)), f1:(x)=>Number(x).toFixed(1),
  var:(x)=>x?'beyond normal variation':'within normal variation', raw:(x)=>String(x)};
function pair(arr,k1,k2,name){ const [a,b]=name.split('|'); return arr.find(r=>r[k1]===a&&r[k2]===b); }
function lookup(p,name,field){
  let row;
  if(p==='s') return S[field];
  if(p==='d') row=D[name]; else if(p==='a') row=A[name];
  else if(p==='h') return (S.history[name]||{})[field];
  else if(p==='v') row=pair(S.variants,'Deck','Variant',name);
  else if(p==='c') row=pair(S.new_cards,'deck','card',name);
  else if(p==='l') row=pair(S.list_changes,'deck','card',name);
  else if(p==='w') return field==='lists'?S.all_week[name]:undefined;
  return row?row[field]:undefined;
}
const TOK=/\{(pp|pct|n|f1|var|raw):([a-z])\.(?:([^{}]+?)\.)?([\w-]+)\}/g;
function R(t){
  if(Array.isArray(t)) return t.map(R);
  if(typeof t!=='string') return t;
  return t.replace(TOK,(m,f,p,name,field)=>{ const v=lookup(p,name,field);
    if(v===undefined||v===null) throw new Error('nierozwiazane odwolanie: '+m); return FMT[f](v); });
}
const NAMED={BAR:BAR,AMB:AMB,GREEN:GREEN,RED:RED,GREY:GREY,TXT:TXT,SUB:SUB,MUT:MUT};
const col=(c)=>NAMED[c]||C[c]||c;

const WEEK=process.argv[2]; if(!WEEK) throw new Error('podaj numer tygodnia: node build_deck.js 43');
const T=JSON.parse(fs.readFileSync('deck_w'+WEEK+'.json','utf8'));
const CFG=T.cfg; const N=S.events;
const notes=fs.readFileSync('notes.md','utf8');
function spk(file){ const m=notes.match(new RegExp('### '+file.replace('.','\\.')+'\\n\\n([^#]+)')); return m?m[1].trim():''; }
const IMG={x:4.35,y:1.40,w:8.55,h:5.45};
function cardC(s,x,y,w,h,c,def){ card(s,x,y,w,h,col(c.col),R(c.t),R(c.l),{bs:c.bs||def.bs,ts:c.ts||def.ts}); }
function bigBoxes(s,big,y,h,ly,lh){
  big.forEach((b,i)=>{ const x=0.62+i*3.12; const c=col(b[2]);
   s.addShape(pres.ShapeType.roundRect,{x:x,y:y,w:2.92,h:h,rectRadius:0.06,fill:{color:CARD},line:{color:c,width:1.5}});
   s.addText(R(b[0]),{x:x+0.16,y:y+0.10,w:2.6,h:h===1.25?0.62:0.66,fontFace:F,fontSize:34,bold:true,color:c,align:'center',isTextBox:true,margin:0});
   s.addText(R(b[1]),{x:x+0.16,y:ly,w:2.6,h:lh,fontFace:F,fontSize:12,color:SUB,align:'center',isTextBox:true,margin:0}); });
}

/* ---------- szablony slajdow ---------- */
const TPL={
 title(t){
  const s=slide();
  s.addShape(pres.ShapeType.rect,{x:1.0,y:0.28,w:11.33,h:0.06,fill:{color:'E11D48'}});
  s.addText('Modern Metagame Breakdown',{x:1.0,y:0.38,w:11.33,h:0.8,fontFace:F,fontSize:40,bold:true,color:TXT,align:'center',isTextBox:true,margin:0});
  s.addShape(pres.ShapeType.rect,{x:1.0,y:1.18,w:11.33,h:0.06,fill:{color:'E11D48'}});
  s.addShape(pres.ShapeType.rect,{x:5.85,y:1.55,w:1.62,h:0.42,fill:{color:'E11D48'}});
  s.addText('2026 WEEK '+CFG.week,{x:5.85,y:1.58,w:1.62,h:0.36,fontFace:F,fontSize:12,bold:true,color:TXT,align:'center',isTextBox:true,margin:0});
  s.addShape(pres.ShapeType.rect,{x:3.6,y:2.35,w:6.13,h:2.35,fill:{color:'111827'},line:{color:'374151',width:1,dashType:'dash'}});
  s.addText('[ thumbnail placeholder ]',{x:3.6,y:3.3,w:6.13,h:0.5,fontFace:F,fontSize:14,color:'4B5563',align:'center',isTextBox:true,margin:0});
  s.addShape(pres.ShapeType.rect,{x:3.75,y:4.85,w:5.83,h:1.9,fill:{color:'052E16'},line:{color:'22C55E',width:2}});
  s.addText([{text:CFG.headline[0],options:{breakLine:true}},{text:CFG.headline[1]}],
    {x:3.85,y:5.05,w:5.63,h:1.5,fontFace:F,fontSize:26,bold:true,color:TXT,align:'center',isTextBox:true,margin:0,lineSpacingMultiple:1.25});
 },
 cards(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  t.cards.forEach((c,i)=>{ const x=0.62+(i%3)*4.12, y=1.45+Math.floor(i/3)*2.55; cardC(s,x,y,3.92,2.35,c,{bs:13,ts:16}); });
  if(t.note) note(s,R(t.note));
 },
 snapshot(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  bigBoxes(s,t.big,1.40,1.25,2.14,0.4);
  t.arch.forEach((c,i)=>cardC(s,0.62+i*3.12,2.85,2.92,2.0,c,{bs:12.5,ts:17}));
  cardC(s,9.98,2.85,2.92,4.0,t.story,{bs:12.5,ts:17});
  cardC(s,0.62,5.05,9.16,1.80,t.trophies,{bs:13,ts:16});
 },
 chart(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  let y=1.40; t.cards.forEach(c=>{ const h=c.h||1.62; card(s,0.62,y,3.48,h,col(c.col),R(c.t),R(c.l),{bs:12.5,ts:15}); y+=h+0.16; });
  image(s,t.png,IMG.x,IMG.y,IMG.w,IMG.h);
  if(t.foot) note(s,R(t.foot));
  s.addNotes(spk(t.png));
 },
 top8(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  s.addText('EVENT WINNERS',{x:0.62,y:1.40,w:3.5,h:0.3,fontFace:F,fontSize:13,bold:true,color:AMB,isTextBox:true,margin:0});
  S.winners.forEach((r,i)=>{ const y=1.75+i*0.40;
    s.addShape(pres.ShapeType.rect,{x:0.62,y:y,w:3.48,h:0.36,fill:{color:i%2?'16202F':CARD}});
    s.addText(r.date.slice(8)+' Oct',{x:0.70,y:y+0.04,w:0.75,h:0.28,fontFace:F,fontSize:11,color:MUT,isTextBox:true,margin:0});
    s.addText(r.deck+(r.variant&&r.variant!==r.deck?' ('+r.variant.replace(/^.*?: /,'')+')':''),{x:1.45,y:y+0.04,w:2.62,h:0.28,fontFace:F,fontSize:11.5,color:C[r.arch]||TXT,bold:true,isTextBox:true,margin:0,fit:'shrink'}); });
  image(s,t.png,IMG.x,IMG.y,IMG.w,IMG.h);
  if(t.note) note(s,R(t.note));
  s.addNotes(spk(t.png));
 },
 zoo(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  bigBoxes(s,t.big,1.40,1.45,2.18,0.55);
  const X=[[0.62,4.0],[4.82,4.0],[9.02,3.88]];
  t.cards.forEach((c,i)=>cardC(s,X[i][0],3.10,X[i][1],2.4,c,{bs:13.5,ts:17}));
  if(t.note) note(s,R(t.note));
 },
 two(t){
  const s=slide(); head(s,R(t.title),R(t.sub));
  cardC(s,0.62,1.40,6.0,5.35,t.cards[0],{bs:16,ts:19});
  cardC(s,6.85,1.40,6.05,5.35,t.cards[1],{bs:16,ts:19});
 },
};
T.slides.forEach(t=>{ if(t.if && !CFG[t.if]) return; if(!TPL[t.type]) throw new Error('nieznany typ slajdu: '+t.type); TPL[t.type](t); });
pres.writeFile({fileName:CFG.outfile}).then(()=>console.log('ok'));
