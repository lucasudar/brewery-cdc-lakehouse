#!/usr/bin/env python3
"""Render docs/diagrams/architecture.drawio to PNG (one per page) and check the layout.

The draw.io viewer runs in headless Chromium (Playwright). After each page is laid out, the check
reads the real rendered geometry (shapes and measured text boxes) and reports:

  error    shapes overlapping a shape that is not their container, or a container closer than 8 px to another shape
  error    a shape sticking out of its container
  error    a label overlapping another shape or label, or text not fitting its own shape
  error    an edge drawn through unrelated text
  warning  an edge drawn through an unrelated shape

  python3 docs/diagrams/render_png.py            # write PNGs + docs/diagrams/REPORT.md, exit 1 on errors
  python3 docs/diagrams/render_png.py --no-png   # check only

Needs: pip install playwright && playwright install chromium (CI uses the Playwright image).
The draw.io viewer is downloaded once into .cache/ (pinned version, sha256 checked).
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = HERE / "architecture.drawio"
OUTDIR = SRC.parent
PAGES = OUTDIR / "pages.tsv"
REPORT = OUTDIR / "REPORT.md"
VIEWER_VERSION = "v28.2.5"
VIEWER_URL = f"https://raw.githubusercontent.com/jgraph/drawio/{VIEWER_VERSION}/src/main/webapp/js/viewer-static.min.js"
VIEWER_SHA256 = "e77c093ad93b7f6f83a3e9f18e44ce49b8667c1f5da839b10b9ffc930c37eef1"
VIEWER = ROOT / ".cache" / f"drawio-viewer-{VIEWER_VERSION}.js"

# Runs in the page after GraphViewer has drawn the diagram. Coordinates are screen pixels at scale 1.
CHECK_JS = r"""
(graph) => {
  const model = graph.getModel(), view = graph.view, TOL = 2;
  const R = (x, y, w, h) => ({x, y, w, h});
  const inter = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
                          Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  const overlaps = (a, b) => Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > TOL &&
                             Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > TOL;
  const inside = (a, b) => a.x >= b.x - TOL && a.y >= b.y - TOL && a.x + a.w <= b.x + b.w + TOL && a.y + a.h <= b.y + b.h + TOL;
  const plain = (c) => { const d = document.createElement('div'); d.innerHTML = c.value || ''; return d.textContent.trim(); };
  const name = (c) => (plain(c) || c.id).replace(/\s+/g, ' ').slice(0, 48);
  const isAnc = (a, c) => { for (let p = model.getParent(c); p; p = model.getParent(p)) if (p === a) return true; return false; };
  const related = (a, b) => a === b || isAnc(a, b) || isAnc(b, a);
  const text = (st) => {
    if (!st.text || !st.text.node || !plain(st.cell)) return null;
    // Measure the rendered HTML label; the bounding box of mxText is an estimate for wrapped labels.
    const el = st.text.node.querySelector('div div div') || st.text.node.querySelector('div') || st.text.node;
    const r = (el.getBoundingClientRect ? el : st.text.node).getBoundingClientRect();
    const o = graph.container.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    // DOM is offset by view.translate; cell states are not, so convert back to state coordinates.
    const t = view.translate, sc = view.scale;
    return R(r.left - o.left - t.x * sc, r.top - o.top - t.y * sc, r.width, r.height);
  };
  const verts = [], edges = [];
  for (const id in model.cells) {
    const c = model.cells[id], st = view.getState(c);
    if (!st || !c.geometry) continue;
    if (model.isVertex(c)) {
      const s = st.style || {};
      const isText = s.shape === 'text' || (st.style && st.style.text !== undefined) || (c.style || '').startsWith('text;');
      const extLabel = s.verticalLabelPosition === 'bottom' || s.labelPosition === 'right';
      verts.push({c, st, box: R(st.x, st.y, st.width, st.height), lbl: text(st), isText, extLabel,
                  container: s.container == '1'});
    } else if (model.isEdge(c)) {
      edges.push({c, st, pts: (st.absolutePoints || []).filter(Boolean), lbl: text(st)});
    }
  }
  const out = [];
  const add = (level, kind, a, b, extra) => out.push({level, kind, a: name(a), b: b ? name(b) : '', ...(extra || {})});
  // shapes vs shapes
  for (let i = 0; i < verts.length; i++) {
    const a = verts[i];
    if (a.isText) continue;
    const parent = verts.find(v => v.c === model.getParent(a.c));
    if (parent && !inside(a.box, parent.box)) add('error', 'outside container', a.c, parent.c);
    for (let j = i + 1; j < verts.length; j++) {
      const b = verts[j];
      if (b.isText || related(a.c, b.c)) continue;
      if (overlaps(a.box, b.box)) add('error', 'shapes overlap', a.c, b.c);
      else if ((a.container || b.container) && overlaps(R(a.box.x - 8, a.box.y - 8, a.box.w + 16, a.box.h + 16), b.box))
        add('error', 'containers too close (< 8px)', a.c, b.c);
    }
  }
  // labels vs everything
  const obstacles = verts.filter(v => !v.isText && !v.container);
  const labels = verts.filter(v => v.lbl).map(v => ({cell: v.c, box: v.lbl, owner: v}))
    .concat(edges.filter(e => e.lbl).map(e => ({cell: e.c, box: e.lbl, edge: e})));
  for (const v of verts) {
    if (!v.lbl || v.extLabel) continue;
    const own = v.isText ? null : v.box;
    if (own && !inside(v.lbl, own)) add('error', 'text does not fit its shape', v.c, null);
  }
  for (const l of labels) {
    for (const o of obstacles) {
      if (l.owner && related(l.owner.c, o.c)) continue;
      if (l.edge && (l.edge.c.source === o.c || l.edge.c.target === o.c) && !overlaps(l.box, o.box)) continue;
      if (overlaps(l.box, o.box)) add('error', 'label overlaps shape', l.cell, o.c);
    }
    for (const v of verts.filter(v => v.container && v.lbl)) {   // container titles
      if (l.cell === v.c || (l.owner && related(l.owner.c, v.c) && !isAnc(v.c, l.owner.c))) continue;
      if (overlaps(l.box, v.lbl)) add('error', 'label overlaps container title', l.cell, v.c);
    }
  }
  for (let i = 0; i < labels.length; i++)
    for (let j = i + 1; j < labels.length; j++) {
      const a = labels[i], b = labels[j];
      if (a.owner && b.owner && related(a.owner.c, b.owner.c)) continue;
      if (a.owner && a.owner.container || b.owner && b.owner.container) continue;
      if (overlaps(a.box, b.box)) add('error', 'labels overlap', a.cell, b.cell);
    }
  // edges through unrelated shapes (warning)
  const segHits = (p, q, r) => {
    const n = 40;
    for (let k = 1; k < n; k++) {
      const x = p.x + (q.x - p.x) * k / n, y = p.y + (q.y - p.y) * k / n;
      const m = Math.min(4, r.h / 4);
      if (x > r.x + m && x < r.x + r.w - m && y > r.y + m && y < r.y + r.h - m) return true;
    }
    return false;
  };
  for (const e of edges) {
    for (const l of labels) {
      if (l.cell === e.c || (l.owner && (l.owner.st.style || {}).labelFor === e.c.id)) continue;
      const o = l.edge ? l.edge.c : l.owner.c;
      if (l.edge && (o.source === e.c.source || o.target === e.c.target)) {
        // edges leaving or entering the same shape share their first/last segment; only the middle counts
        const pts = e.pts.slice(1, -1);
        let hit = false;
        for (let k = 0; k + 1 < pts.length; k++) if (segHits(pts[k], pts[k + 1], l.box)) hit = true;
        if (!hit) continue;
      }
      if (o === e.c.source || o === e.c.target || isAnc(o, e.c.source) || isAnc(o, e.c.target)) continue;
      for (let k = 0; k + 1 < e.pts.length; k++)
        if (segHits(e.pts[k], e.pts[k + 1], l.box)) { add('error', 'edge crosses text', e.c, o,
          {edge: name(e.c.source) + ' → ' + name(e.c.target)}); break; }
    }
    for (const o of obstacles) {
      if (o.c === e.c.source || o.c === e.c.target) continue;
      if (isAnc(o.c, e.c.source) || isAnc(o.c, e.c.target)) continue;
      for (let k = 0; k + 1 < e.pts.length; k++)
        if (segHits(e.pts[k], e.pts[k + 1], o.box)) { add('warning', 'edge crosses shape', e.c, o.c,
          {edge: name(e.c.source) + ' → ' + name(e.c.target)}); break; }
    }
  }
  return out;
}
"""


def viewer() -> str:
    if not VIEWER.exists():
        VIEWER.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(VIEWER_URL, timeout=60) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != VIEWER_SHA256:
            raise SystemExit(f"draw.io viewer checksum mismatch for {VIEWER_URL}")
        VIEWER.write_bytes(data)
    return VIEWER.read_text(encoding="utf-8")


def main() -> int:
    png = "--no-png" not in sys.argv
    xml = SRC.read_text(encoding="utf-8")
    pages = [line.split("\t") for line in PAGES.read_text(encoding="utf-8").splitlines() if line]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 2800, "height": 2000}, device_scale_factor=1.5)
        page.set_content('<html><body style="margin:0;background:#fff"></body></html>')
        page.add_script_tag(content=viewer())
        for i, (fname, title) in enumerate(pages):
            page.evaluate("""(a) => {
                document.body.innerHTML = '';
                const div = document.createElement('div'); div.id = 'g'; document.body.appendChild(div);
                const doc = mxUtils.parseXml(a.xml).documentElement;
                window.gv = new GraphViewer(div, doc, {page: a.i, border: 24, nav: false, toolbar: null, resize: true});
            }""", {"xml": xml, "i": i})
            page.wait_for_function("() => window.gv && window.gv.graph && document.querySelectorAll('#g image').length >= 0")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            found = page.evaluate(f"() => ({CHECK_JS})(window.gv.graph)")
            if png:
                page.locator("#g").screenshot(path=str(OUTDIR / fname))
            results.append((fname, title, found))
        browser.close()
    if png:  # pages that no longer exist
        keep = {f for f, _ in pages}
        for old in OUTDIR.glob("*.png"):
            if old.name not in keep:
                old.unlink()

    total_err = sum(1 for _, _, f in results for x in f if x["level"] == "error")
    total_warn = sum(1 for _, _, f in results for x in f if x["level"] == "warning")
    lines = ["# Diagram layout report", "",
             "Written by `docs/diagrams/render_png.py` from the rendered draw.io pages. Errors fail CI and block publishing.", "",
             "| Page | Errors | Warnings |", "|---|---|---|"]
    for fname, title, f in results:
        e = sum(1 for x in f if x["level"] == "error")
        w = len(f) - e
        lines.append(f"| [{title}]({fname}) | {e} | {w} |")
    for fname, title, f in results:
        if not f:
            continue
        lines += ["", f"## {title}", ""]
        for x in sorted(f, key=lambda x: (x["level"] != "error", x["kind"], x["a"])):
            what = x.get("edge") or x["a"]
            lines.append(f"- **{x['level']}** {x['kind']}: `{what}`" + (f" / `{x['b']}`" if x["b"] else ""))
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for fname, title, f in results:
        e = [x for x in f if x["level"] == "error"]
        print(f"{title}: {len(e)} error(s), {len(f) - len(e)} warning(s)")
        for x in e:
            print(f"  {x['kind']}: {x['a']!r} / {x['b']!r}")
    print(f"Total: {total_err} error(s), {total_warn} warning(s). Report: {REPORT.relative_to(ROOT)}")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
