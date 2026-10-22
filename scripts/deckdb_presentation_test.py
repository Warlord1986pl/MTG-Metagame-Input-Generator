"""Presentation test of the deckdb-mode slides (orders of 2026-10-21 g and j): render every slide with the
slide renderer (src/deckdb_slides.py, 1920x1080, 13.333 x 7.5 in at 144 dpi, so 1 figure pt = 1 slide pt),
assemble them into presentation_test.pptx (one 16:9 slide per image, the image filling the slide, the glossary in
the speaker notes) and report readability per slide:

  - smallest font in slide points and the number of texts below 14 pt;
  - text outside the figure (cut off);
  - overlapping texts: every pair of visible, non-empty texts of the figure (titles, tick labels, values, cells,
    legend) whose bounding boxes intersect;
  - number of rows / bars / lines (the most tick labels on one axis, or matrix rows).

The report charts (deckdb_materials.py) are not rendered or changed here.

    python scripts/deckdb_presentation_test.py --as-of 2026-10-21 --out outputs/deckdb_working/presentation_test
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import deckdb_input as di  # noqa: E402
import deckdb_slides as ds  # noqa: E402

MIN_SLIDE_PT = 14.0
REPORT: dict = {}


def _analyse(fig: Figure, path: str) -> None:
    renderer = fig.canvas.get_renderer()
    fw, fh = fig.bbox.width, fig.bbox.height
    texts = [t for t in fig.findobj(matplotlib.text.Text) if t.get_visible() and t.get_text().strip()]
    boxes = [t.get_window_extent(renderer) for t in texts]
    sizes = [t.get_fontsize() for t in texts]
    cut = sum(1 for bb in boxes if bb.x0 < -1 or bb.y0 < -1 or bb.x1 > fw + 1 or bb.y1 > fh + 1)
    overlaps = [(texts[i].get_text(), texts[j].get_text()) for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                if boxes[i].overlaps(boxes[j])]
    # bars / lines: tick labels on the y axis; matrices (axis off): right-aligned row labels
    rows = max([len([lb for lb in ax.get_yticklabels() if lb.get_text().strip()]) if ax.axison
                else len([x for x in ax.texts if x.get_ha() == "right"]) for ax in fig.axes] + [0])
    scale = ds.SLIDE_W_IN / fig.get_figwidth()  # slide pt per figure pt (1.0 for the slide renderer)
    REPORT[Path(path).name] = {
        "min_font_slide_pt": round(min(sizes) * scale, 1) if sizes else None,
        "texts_below_14pt": sum(1 for s in sizes if s * scale < MIN_SLIDE_PT),
        "texts": len(sizes),
        "cut_texts": cut,
        "cut": [t.get_text() for t, bb in zip(texts, boxes)
                if bb.x0 < -1 or bb.y0 < -1 or bb.x1 > fw + 1 or bb.y1 > fh + 1],
        "overlaps": overlaps,
        "rows_or_bars": rows,
    }


_orig_savefig = Figure.savefig


def _savefig(self, fname, *args, **kwargs):
    self.canvas.draw()
    _analyse(self, str(fname))
    return _orig_savefig(self, fname, *args, **kwargs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trend-weeks", type=int, default=12)
    args = ap.parse_args()
    out = Path(args.out)
    for old in out.glob("*.png"):
        old.unlink()
    cfg = di.load_deckdb_config()
    export = di.load_export(cfg.export_path)
    Figure.savefig = _savefig
    try:
        slides = ds.build_deckdb_slides(export, cfg, date.fromisoformat(args.as_of), out, args.trend_weeks,
                                        log=lambda _m: None)
    finally:
        Figure.savefig = _orig_savefig
    pptx = ds.write_pptx(slides, out / "presentation_test.pptx")
    from PIL import Image

    print(f"{len(slides)} slides -> {pptx}")
    print("| slide | px | min font (slide pt) | texts < 14 pt | cut texts | overlapping text pairs | rows/bars |")
    print("|---|---|---|---|---|---|---|")
    for s in slides:
        r = REPORT.get(s.png.name, {})
        w, h = Image.open(s.png).size
        print(f"| {s.png.name} | {w}x{h} | {r.get('min_font_slide_pt')} | {r.get('texts_below_14pt')}/"
              f"{r.get('texts')} | {r.get('cut_texts')} | {len(r.get('overlaps', []))} | {r.get('rows_or_bars')} |")
    for name, r in REPORT.items():
        for a, b in r["overlaps"]:
            print(f"  overlap in {name}: {a!r} / {b!r}")
        for a in r["cut"]:
            print(f"  cut off in {name}: {a!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
